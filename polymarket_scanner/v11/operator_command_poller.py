"""Bounded production entry point that polls real Telegram updates for
``TelegramOperatorCommandAdapter``.

Nothing before this module ever called ``Telegram.updates``/``.handle()`` for
the operator safety-router path: the adapter and router were only reachable
from tests. This poller is the missing caller. It fetches at most one bounded
batch of already-delivered updates per ``step()``, authenticates and routes
each one through the unchanged adapter/router, and durably advances its own
polling cursor as a ``RUNTIME_STATUS`` evidence record after the batch. A
restart resumes at that committed cursor. A failed batch is redelivered;
the adapter/router preserve any already-committed reductions idempotently.

A per-worker lock and a bot-scoped lock serialize concurrent ``step()`` calls.
The bot lock also retains a durable digest of the store path/file identity,
namespace, worker key and adapter identity/policy before any network request,
including an idle first poll. A different consumer is refused even between
polls or after restart: Telegram confirms older updates on a higher offset,
so merely serializing requests from independent cursors can lose commands.
The original consumer resumes its existing evidence-store cursor unchanged.
A mismatched or incomplete binding requires review; it is never overwritten.

Both locks live beside the store. This is cooperative same-directory binding,
not protected configuration custody: other directories/hosts, older code and
controllers that ignore these locks are not excluded. Existing empty lock files
can be claimed on first use with the reviewed configuration; stop old consumers
before upgrading. Binding changes and database replacement require the reviewed
``handoff_bot_owner`` transfer below, not lock deletion: it keeps this poller's
own worker_key/store cursor untouched -- only the bot-scoped network binding
moves -- and durably records the exact prior/new bindings and the caller's
stated reason so the change is never silent. Deleting or truncating the lock
file directly still bypasses that review and remains unsupported.

An update that fails authentication, grammar, or router authorization is reported
without stalling later commands; storage/integrity failures retain the cursor for
retry. Real delivery, credential provisioning and independent operational
acceptance remain unclaimed.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import asdict
import fcntl
import os
import stat

from .evidence import EvidenceError, EvidenceStore, digest, identity
from .operator_command_adapter import TelegramOperatorCommandAdapter
from .operator_safety_router import OperatorSafetyError

VERSION = "alpha_v11_operator_command_poller_v1"
# These reducer/input rejections cannot succeed on an unchanged retry.
_COMMAND_REJECTIONS = frozenset({
    "INVALID_IDENTITY", "SAFETY_ACTION_INVALID", "SAFETY_SCOPE_MISMATCH",
    "REPLAY_REQUEST_CONFLICT",
})


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
        with ExitStack() as cleanup:
            worker_fd = os.open(self.store.path.with_name(self.store.path.name + "." + self.worker_key + ".lock"),
                                os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
            cleanup.callback(os.close, worker_fd)
            try:
                fcntl.flock(worker_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise OperatorCommandPollerError("POLLER_ALREADY_RUNNING") from None
            bot_fd = self._open_locked_bot_lock(cleanup)
            self._bind_bot_owner(bot_fd)
            return await self._step()

    def _binding(self) -> bytes:
        store_info = self.store.path.stat()
        return ("alpha_v11_operator_bot_owner_v1:" + digest(dict(
            store=str(self.store.path), store_file=[store_info.st_dev, store_info.st_ino],
            namespace=self.store.namespace, worker_key=self.worker_key,
            identity=asdict(self.adapter.identity), policy=asdict(self.adapter.router.policy))) + "\n").encode()

    def _bot_lock_path(self):
        return self.store.path.with_name("operator-bot-" + str(self.adapter.identity.bot_id) + ".lock")

    def _open_locked_bot_lock(self, cleanup: ExitStack) -> int:
        fd = os.open(self._bot_lock_path(), os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        cleanup.callback(os.close, fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise OperatorCommandPollerError("OPERATOR_COMMANDS_BOT_ALREADY_POLLING") from None
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid != os.geteuid() or info.st_mode & 0o077):
            raise OperatorCommandPollerError("OPERATOR_COMMANDS_BOT_LOCK_UNSAFE")
        return fd

    def _fsync_lock_and_parent(self, fd: int) -> None:
        os.fsync(fd)
        parent_fd = os.open(self.store.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)

    def _bind_bot_owner(self, fd: int) -> None:
        """Pin a local consumer before polling; never overwrite or transfer it."""
        binding = self._binding()
        saved = os.read(fd, len(binding) + 1)
        if saved and saved != binding:
            raise OperatorCommandPollerError("OPERATOR_COMMANDS_BOT_OWNER_MISMATCH")
        if not saved and os.write(fd, binding) != len(binding):
            raise OperatorCommandPollerError("OPERATOR_COMMANDS_BOT_OWNER_WRITE_INCOMPLETE")
        # Also sync an existing match: a previous attempt may have written the
        # complete binding but failed to make it durable. No network before both.
        self._fsync_lock_and_parent(fd)

    def handoff_bot_owner(self, *, reason: str) -> dict:
        """Durably replace the bot lock's owner binding after explicit review.

        This keeps ``self.worker_key``/``self.store`` as-is, so the poller's own
        durable cursor is untouched by the transfer; only the bot-scoped network
        binding moves to this poller's identity/policy. Unlike deleting the lock
        file, the exact prior binding, the new one and the caller's stated reason
        are recorded as a durable ``OPERATOR_EVENT`` before the lock is rewritten,
        so history is never silently lost, and a no-op handoff (nothing to change)
        is refused rather than manufacturing a redundant record.
        """
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 200 or "\n" in reason:
            raise OperatorCommandPollerError("OPERATOR_COMMANDS_HANDOFF_REASON_INVALID")
        with ExitStack() as cleanup:
            fd = self._open_locked_bot_lock(cleanup)
            previous = os.read(fd, 4096)
            binding = self._binding()
            if previous == binding:
                raise OperatorCommandPollerError("OPERATOR_COMMANDS_HANDOFF_NOT_CHANGED")
            previous_text, new_text = previous.decode("utf-8", "replace"), binding.decode()
            record_id = "operator-bot-owner-handoff:" + digest([self.worker_key, previous_text, new_text])
            self.store.audit(record_id, event_id=self.worker_key, kind="OPERATOR_EVENT",
                details=dict(version=VERSION, outcome="OPERATOR_COMMANDS_BOT_OWNER_HANDOFF",
                             reason=reason, previous_binding=previous_text,
                             new_binding=new_text, financial_authority=False, messages_sent=False))
            os.lseek(fd, 0, os.SEEK_SET)
            os.ftruncate(fd, 0)
            if os.write(fd, binding) != len(binding):
                raise OperatorCommandPollerError("OPERATOR_COMMANDS_BOT_OWNER_WRITE_INCOMPLETE")
            self._fsync_lock_and_parent(fd)
            return dict(outcome="OPERATOR_COMMANDS_BOT_OWNER_HANDOFF", reason=reason)

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
            except EvidenceError as exc:
                if not isinstance(exc, OperatorSafetyError) and str(exc) not in _COMMAND_REJECTIONS:
                    # Storage, integrity and CAS failures must remain retryable;
                    # acknowledging them could lose an unapplied safety command.
                    raise
                outcomes.append(dict(update_id=uid, error=str(exc)))
            applied_through = max(applied_through, uid + 1)
        if applied_through != offset:
            key = "operator-poll:" + digest([self.worker_key, offset, applied_through])
            self.store.audit(key, event_id=self.worker_key, kind="RUNTIME_STATUS",
                details=dict(version=VERSION, offset=applied_through,
                             financial_authority=False, messages_sent=False),
                expected_previous_seq=head["seq"] if head else 0)
        return outcomes
