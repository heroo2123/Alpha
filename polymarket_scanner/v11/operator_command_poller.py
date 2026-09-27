"""Bounded production entry point that polls real Telegram updates for
``TelegramOperatorCommandAdapter``.

Nothing before this module ever called ``Telegram.updates``/``.handle()`` for
the operator safety-router path: the adapter and router were only reachable
from tests. This poller is the missing caller. It fetches at most one bounded
batch of already-delivered updates per ``step()``, authenticates and routes
each one through the unchanged adapter/router, and durably advances its own
polling cursor as a ``RUNTIME_STATUS`` evidence record so a restart resumes
after the last update it actually attempted rather than replaying or skipping.

One offset is shared by exactly one worker key; a file lock refuses a second
concurrent ``step()`` for that key so two processes never race Telegram's
stateful ``getUpdates`` offset. An update that fails authentication, grammar,
or router authorization does not raise out of ``step()`` and does not stall
later updates in the same batch; its outcome is reported and its offset still
advances, matching the existing cooperative ``Controller.commands`` behavior.
This performs no message delivery, credential provisioning, account mutation,
or independent operational acceptance; those remain separate and unclaimed.
"""
from __future__ import annotations

import fcntl
import os

from .evidence import EvidenceError, EvidenceStore, digest, identity
from .operator_command_adapter import OperatorCommandError, TelegramOperatorCommandAdapter

VERSION = "alpha_v11_operator_command_poller_v1"


class OperatorCommandPollerError(EvidenceError):
    pass


class TelegramOperatorCommandPoller:
    """Advances one durable per-worker Telegram offset through ``adapter.handle``."""

    def __init__(self, store: EvidenceStore, worker_key: str,
                 adapter: TelegramOperatorCommandAdapter):
        self.store, self.worker_key, self.adapter = store, identity(worker_key), adapter

    def _head(self):
        row = self.store.latest(kind="RUNTIME_STATUS", event_id=self.worker_key)
        if row and row["body"]["details"].get("version") != VERSION:
            raise OperatorCommandPollerError("POLLER_STATE_VERSION_MISMATCH")
        return row

    def offset(self) -> int:
        head = self._head()
        return head["body"]["details"]["offset"] if head else 0

    async def step(self) -> list[dict]:
        """One bounded poll; never sleeps, retries, or blocks on Telegram."""
        fd = os.open(self.store.path.with_name(self.store.path.name + "." + self.worker_key + ".lock"),
                     os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise OperatorCommandPollerError("POLLER_ALREADY_RUNNING") from None
            return await self._step()
        finally:
            os.close(fd)

    async def _step(self) -> list[dict]:
        head = self._head()
        offset = head["body"]["details"]["offset"] if head else 0
        updates = await self.adapter.telegram.updates(offset)
        outcomes = []
        applied_through = offset
        for update in updates:
            uid = update.get("update_id")
            if type(uid) is not int or uid < offset:
                continue
            try:
                outcomes.append(dict(update_id=uid, outcome=self.adapter.handle(update)))
            except OperatorCommandError as exc:
                # Unauthenticated/malformed/conflicting updates must not stall
                # later commands in this poll or be retried forever unchanged.
                outcomes.append(dict(update_id=uid, error=str(exc)))
            applied_through = max(applied_through, uid + 1)
        if applied_through != offset:
            key = "operator-poll:" + digest([self.worker_key, offset, applied_through])
            self.store.audit(key, event_id=self.worker_key, kind="RUNTIME_STATUS",
                details=dict(version=VERSION, offset=applied_through,
                             financial_authority=False, messages_sent=False),
                expected_previous_seq=head["seq"] if head else 0)
        return outcomes
