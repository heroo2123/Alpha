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
The current consumer resumes its existing evidence-store cursor unchanged.
A mismatched or incomplete binding requires review; it is never overwritten.

Both locks live beside the store, but ownership itself is anchored in the
shared evidence store, not in either lock file. The very first bind for a bot
records a CAS-guarded durable ``OPERATOR_EVENT`` claim (``expected_previous_seq
=0``) before the local lock file is trusted, so a second consumer in a
different directory or host that merely happens to have its own empty local
lock file cannot silently believe the bot is unclaimed: it reads the same
durable claim through the shared store and is refused. Existing empty lock
files from before this claim existed can still be claimed on first use with
the reviewed configuration, which durably records the claim retroactively;
stop old consumers before upgrading. Identity/policy rotation uses
handoff_bot_owner with the exact prior configuration on the SAME store/file,
namespace, worker, bot and account, cooperatively performed by whichever local
process currently holds that configuration; it does not by itself grant a
brand-new host authorization it never had. A CAS-guarded durable audit advances
ownership atomically while the lock retains its original anchor. Database
replacement or cursor migration requires a separate reviewed recovery
procedure; this API cannot perform it. Deleting or truncating the lock file
bypasses review and remains unsupported. Older consumers must stay stopped:
they do not understand the handoff/claim journal.

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
import re
import stat

from .evidence import EvidenceError, EvidenceStore, digest, identity
from .operator_command_adapter import TelegramCommandIdentity, TelegramOperatorCommandAdapter
from .operator_safety_router import OperatorSafetyError, OperatorSafetyPolicy

VERSION = "alpha_v11_operator_command_poller_v1"
HANDOFF_VERSION = "alpha_v11_operator_bot_handoff_v1"
CLAIM_VERSION = "alpha_v11_operator_bot_claim_v1"
_BINDING_PATTERN = re.compile(rb"alpha_v11_operator_bot_owner_v1:[0-9a-f]{64}\n")
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

    def _cursor_scope(self) -> dict:
        store_info = self.store.path.stat()
        return dict(store=str(self.store.path), store_file=[store_info.st_dev, store_info.st_ino],
                    namespace=self.store.namespace, worker_key=self.worker_key)

    def _binding(self, *, previous_identity=None, previous_policy=None) -> bytes:
        return ("alpha_v11_operator_bot_owner_v1:" + digest(dict(
            **self._cursor_scope(),
            identity=asdict(previous_identity if previous_identity is not None else self.adapter.identity),
            policy=asdict(previous_policy if previous_policy is not None else self.adapter.router.policy))) + "\n").encode()

    def _owner_event(self) -> str:
        return "operator-bot-owner:" + str(self.adapter.identity.bot_id)

    def _owner_state(self, fd: int):
        """Read the local lock anchor and the durable claim/handoff head, if any.

        The durable store, not either local lock file, is authoritative: a
        durable claim or handoff head always wins. ``current`` is ``None`` only
        when nobody has ever durably claimed this bot, in which case the local
        lock file is inert (the store, not this file, decides "unclaimed").
        """
        saved = os.read(fd, len(self._binding()) + 1)
        head = self.store.latest(kind="OPERATOR_EVENT", event_id=self._owner_event())
        if head is None:
            return saved, None, None
        details = head["body"]["details"]
        version = details.get("version")
        if version == CLAIM_VERSION:
            if (details.get("cursor_sha256") != digest(self._cursor_scope())
                    or not isinstance(details.get("binding"), str)
                    or not _BINDING_PATTERN.fullmatch(details["binding"].encode())):
                raise OperatorCommandPollerError("OPERATOR_COMMANDS_BOT_OWNER_MISMATCH")
            return saved, details["binding"].encode(), head
        if (version != HANDOFF_VERSION
                or details.get("anchor_binding") != saved.decode("ascii", "replace")
                or details.get("cursor_sha256") != digest(self._cursor_scope())
                or not _BINDING_PATTERN.fullmatch(saved)
                or not isinstance(details.get("new_binding"), str)
                or not _BINDING_PATTERN.fullmatch(details["new_binding"].encode())):
            raise OperatorCommandPollerError("OPERATOR_COMMANDS_BOT_OWNER_MISMATCH")
        return saved, details["new_binding"].encode(), head

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
        saved, current, head = self._owner_state(fd)
        if current is not None and current != binding:
            raise OperatorCommandPollerError("OPERATOR_COMMANDS_BOT_OWNER_MISMATCH")
        # Before any handoff, the local file IS the binding, so it must
        # independently be empty or exactly correct, whether or not a durable
        # claim already backs it: a mismatched or incomplete local binding
        # still requires review and is never silently repaired. After a
        # handoff the local file deliberately keeps its original anchor
        # instead, so this check no longer applies to it.
        if ((head is None or head["body"]["details"].get("version") == CLAIM_VERSION)
                and saved and saved != binding):
            raise OperatorCommandPollerError("OPERATOR_COMMANDS_BOT_OWNER_MISMATCH")
        if current is None:
            # Nobody has ever durably claimed this bot. Record a CAS-guarded
            # claim before trusting the local file, so a different directory
            # or host sharing this same store cannot silently believe it is
            # unclaimed too.
            record_id = "operator-bot-owner-claim:" + digest(
                [self._owner_event(), self._cursor_scope(), binding.decode()])
            try:
                self.store.audit(record_id, event_id=self._owner_event(), kind="OPERATOR_EVENT",
                    details=dict(version=CLAIM_VERSION, outcome="OPERATOR_COMMANDS_BOT_OWNER_CLAIM",
                                 binding=binding.decode(), cursor_sha256=digest(self._cursor_scope()),
                                 financial_authority=False, messages_sent=False),
                    expected_previous_seq=0)
            except EvidenceError as exc:
                if str(exc) != "AUDIT_STATE_CHANGED":
                    raise
                # A different consumer durably claimed this bot first.
                raise OperatorCommandPollerError("OPERATOR_COMMANDS_BOT_OWNER_MISMATCH") from None
        if not saved and os.write(fd, binding) != len(binding):
            raise OperatorCommandPollerError("OPERATOR_COMMANDS_BOT_OWNER_WRITE_INCOMPLETE")
        # Also sync an existing match: a previous attempt may have written the
        # complete binding but failed to make it durable. No network before both.
        self._fsync_lock_and_parent(fd)

    def handoff_bot_owner(self, *, previous_identity: TelegramCommandIdentity,
                          previous_policy: OperatorSafetyPolicy, reason: str) -> dict:
        """Rotate a reviewed identity/policy on the original store and cursor.

        The caller supplies the exact previous configuration for comparison under
        the bot lock; this is a consistency check, not independent authorization.
        Store/file, namespace, worker, bot and account migration are refused.
        One durable CAS-guarded audit IS the ownership change. The original lock
        anchor is never rewritten, so failures cannot expose an empty/unowned bot.
        Retrying the latest committed transition returns its original result.
        Pending messages still pass the successor's authentication/freshness policy.
        """
        if (not isinstance(reason, str) or not reason.strip() or len(reason) > 200
                or len(reason.splitlines()) != 1 or any(ord(c) < 32 or ord(c) == 127 for c in reason)):
            raise OperatorCommandPollerError("OPERATOR_COMMANDS_HANDOFF_REASON_INVALID")
        if (not isinstance(previous_identity, TelegramCommandIdentity)
                or not isinstance(previous_policy, OperatorSafetyPolicy)
                or previous_identity.bot_id != self.adapter.identity.bot_id
                or previous_policy.account_id != self.adapter.router.policy.account_id):
            raise OperatorCommandPollerError("OPERATOR_COMMANDS_HANDOFF_SCOPE_MISMATCH")
        expected = self._binding(previous_identity=previous_identity, previous_policy=previous_policy)
        binding = self._binding()
        with ExitStack() as cleanup:
            fd = self._open_locked_bot_lock(cleanup)
            anchor, current, head = self._owner_state(fd)
            if current is None:
                raise OperatorCommandPollerError("OPERATOR_COMMANDS_HANDOFF_NOT_CLAIMED")
            if head["body"]["details"].get("version") == CLAIM_VERSION and anchor != current:
                # Before any handoff the local lock file must exactly mirror the
                # durable claim; an unknown/partial local file is never trusted
                # as the anchor for a transfer, even though the durable claim
                # itself already identifies the true current owner.
                raise OperatorCommandPollerError("OPERATOR_COMMANDS_BOT_OWNER_MISMATCH")
            result = dict(outcome="OPERATOR_COMMANDS_BOT_OWNER_HANDOFF", reason=reason)
            if expected == binding:
                raise OperatorCommandPollerError("OPERATOR_COMMANDS_HANDOFF_NOT_CHANGED")
            if head and current == binding:
                details = head["body"]["details"]
                if details.get("previous_binding") == expected.decode() and details.get("reason") == reason:
                    self._fsync_lock_and_parent(fd)
                    return result
            if current != expected:
                raise OperatorCommandPollerError("OPERATOR_COMMANDS_BOT_OWNER_MISMATCH")
            self._fsync_lock_and_parent(fd)
            cursor = self._head()
            cursor_seq = cursor["seq"] if cursor else 0
            previous_seq = head["seq"] if head else 0
            record_id = "operator-bot-owner-handoff:" + digest(
                [self._owner_event(), previous_seq, expected.decode(), binding.decode(), reason])
            self.store.audit(record_id, event_id=self._owner_event(), kind="OPERATOR_EVENT",
                details=dict(version=HANDOFF_VERSION, **result,
                             anchor_binding=anchor.decode(), previous_binding=expected.decode(),
                             new_binding=binding.decode(), cursor_sha256=digest(self._cursor_scope()),
                             cursor_seq=cursor_seq, offset=cursor["body"]["details"]["offset"] if cursor else 0,
                             previous_handoff_seq=previous_seq,
                             financial_authority=False, messages_sent=False),
                evidence_ids=tuple(row["id"] for row in (head, cursor) if row),
                expected_previous_seq=previous_seq,
                expected_heads=(("RUNTIME_STATUS", self.worker_key, cursor_seq),))
            return result

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
