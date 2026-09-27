import asyncio
import os
import sqlite3
import time

import pytest

from polymarket_scanner.production.telegram import Telegram
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from polymarket_scanner.v11.operator_command_adapter import TelegramCommandIdentity, TelegramOperatorCommandAdapter
from polymarket_scanner.v11.operator_command_poller import OperatorCommandPollerError, TelegramOperatorCommandPoller
from polymarket_scanner.v11.operator_safety_router import OperatorSafetyPolicy, OperatorSafetyRouter

WORKER_KEY = "v11-operator-command-poller"


class Bot:
    principal = Telegram.principal

    def __init__(self, updates=(), *, bot_id="123", chat_id="42", operators=("42",)):
        self.delivery_identity = {"bot_id": bot_id, "chat_id": chat_id}
        self.operators = set(operators)
        self._updates = list(updates)
        self.calls = []

    async def updates(self, offset: int):
        self.calls.append(offset)
        return [u for u in self._updates if u["update_id"] >= offset]


def message_update(text, *, uid=1, actor=42, chat=42, date=None, **overrides):
    row = {"message_id": 1000 + uid, "date": int(time.time()) if date is None else date,
           "text": text, "chat": {"id": chat, "type": "private"}, "from": {"id": actor, "is_bot": False}}
    row.update(overrides)
    return {"update_id": uid, "message": row}


@pytest.fixture
def rig(tmp_path):
    tmp_path.chmod(0o700)
    store = EvidenceStore(tmp_path / "evidence.sqlite", "V11_PAPER")
    policy = OperatorSafetyPolicy(account_id="account", operators=(42,),
        allowed_scopes=("ACCOUNT", "CITY", "STATION", "EVENT"),
        allowed_actions=("CANCEL_AND_HALT", "NO_NEW_ORDERS", "QUARANTINE_STATION"))
    router = OperatorSafetyRouter(store, policy)
    identity = TelegramCommandIdentity(bot_id=123, chat_id=42, operators=(42,))

    def build(bot):
        adapter = TelegramOperatorCommandAdapter(bot, identity, router)
        return TelegramOperatorCommandPoller(store, WORKER_KEY, adapter)
    return store, build


def test_fresh_poller_starts_at_offset_zero_and_advances_past_applied_updates(rig):
    store, build = rig
    bot = Bot([message_update("/CANCEL_AND_HALT ACCOUNT account stop", uid=1)])
    poller = build(bot)
    assert poller.offset() == 0
    outcomes = asyncio.run(poller.step())
    assert bot.calls == [0]
    assert len(outcomes) == 1 and outcomes[0]["outcome"]["actor"] == 42
    assert poller.offset() == 2
    # One durable first-claim record plus one applied safety reduction.
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 2


def test_second_step_resumes_from_the_durable_offset_not_zero(rig):
    store, build = rig
    bot = Bot([message_update("/CANCEL_AND_HALT ACCOUNT account first", uid=1)])
    poller = build(bot)
    asyncio.run(poller.step())
    bot._updates.append(message_update("/NO_NEW_ORDERS ACCOUNT account second", uid=2))
    outcomes = asyncio.run(poller.step())
    assert bot.calls == [0, 2]
    assert len(outcomes) == 1 and outcomes[0]["update_id"] == 2
    assert poller.offset() == 3


def test_no_updates_leaves_the_offset_and_head_unchanged(rig):
    store, build = rig
    poller = build(Bot([]))
    outcomes = asyncio.run(poller.step())
    assert outcomes == []
    assert poller.offset() == 0
    assert poller._head() is None


def test_unauthenticated_update_is_reported_but_does_not_stall_a_later_one(rig):
    store, build = rig
    bot = Bot([
        message_update("/CANCEL_AND_HALT ACCOUNT account not an operator", uid=1, actor=999),
        message_update("/CANCEL_AND_HALT ACCOUNT account real operator", uid=2),
    ])
    poller = build(bot)
    outcomes = asyncio.run(poller.step())
    assert "error" in outcomes[0] and outcomes[0]["error"] == "UPDATE_NOT_FROM_AUTHENTICATED_OPERATOR"
    assert outcomes[1]["outcome"]["actor"] == 42
    assert poller.offset() == 3
    # One durable first-claim record plus one applied safety reduction.
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 2


def test_replaying_the_same_batch_twice_is_idempotent(rig):
    store, build = rig
    bot = Bot([message_update("/CANCEL_AND_HALT ACCOUNT account stop", uid=1)])
    poller = build(bot)
    first = asyncio.run(poller.step())
    second = asyncio.run(build(Bot(bot._updates)).step())
    assert second == []
    # One durable first-claim record plus one applied safety reduction.
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 2


def test_concurrent_step_on_the_same_worker_key_is_refused(rig):
    import fcntl
    store, build = rig
    poller = build(Bot([]))
    lock_path = store.path.with_name(store.path.name + "." + WORKER_KEY + ".lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(OperatorCommandPollerError, match="POLLER_ALREADY_RUNNING"):
            asyncio.run(poller.step())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_concurrent_step_on_the_same_bot_from_a_different_worker_key_is_refused(rig):
    import fcntl
    store, build = rig
    identity = TelegramCommandIdentity(bot_id=123, chat_id=42, operators=(42,))
    policy = OperatorSafetyPolicy(account_id="account", operators=(42,),
        allowed_scopes=("ACCOUNT",), allowed_actions=("CANCEL_AND_HALT",))
    adapter = TelegramOperatorCommandAdapter(Bot([]), identity, OperatorSafetyRouter(store, policy))
    poller = TelegramOperatorCommandPoller(store, "a-different-worker-key", adapter)
    lock_path = store.path.with_name("operator-bot-123.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(OperatorCommandPollerError, match="OPERATOR_COMMANDS_BOT_ALREADY_POLLING"):
            asyncio.run(poller.step())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_concurrent_step_on_the_same_bot_from_a_different_store_in_the_same_directory_is_refused(rig, tmp_path):
    import fcntl
    store, build = rig
    other_store = EvidenceStore(tmp_path / "other.sqlite", "V11_PAPER")
    identity = TelegramCommandIdentity(bot_id=123, chat_id=42, operators=(42,))
    policy = OperatorSafetyPolicy(account_id="account", operators=(42,),
        allowed_scopes=("ACCOUNT",), allowed_actions=("CANCEL_AND_HALT",))
    adapter = TelegramOperatorCommandAdapter(Bot([]), identity, OperatorSafetyRouter(other_store, policy))
    poller = TelegramOperatorCommandPoller(other_store, WORKER_KEY, adapter)
    lock_path = store.path.with_name("operator-bot-123.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(OperatorCommandPollerError, match="OPERATOR_COMMANDS_BOT_ALREADY_POLLING"):
            asyncio.run(poller.step())
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_concurrent_step_on_a_different_bot_is_not_affected(rig):
    import fcntl
    store, build = rig
    poller = build(Bot([]))
    lock_path = store.path.with_name("operator-bot-999.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert asyncio.run(poller.step()) == []
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@pytest.mark.parametrize("changed", ["worker", "store", "policy", "chat"])
def test_idle_bot_owner_cannot_be_replaced_by_a_consumer_with_an_independent_cursor_or_policy(rig, tmp_path, changed):
    from dataclasses import replace

    class AcknowledgingBot(Bot):
        async def updates(self, offset):
            # Telegram confirms and forgets older updates on the next request.
            self._updates[:] = [u for u in self._updates if u["update_id"] >= offset]
            return await super().updates(offset)

    store, build = rig
    pending = []
    bot = AcknowledgingBot()
    bot._updates = pending
    owner = build(bot)
    assert asyncio.run(owner.step()) == []
    other_store = EvidenceStore(tmp_path / "other.sqlite", "V11_PAPER") if changed == "store" else store
    worker_key = "other-worker" if changed == "worker" else WORKER_KEY
    identity, policy = owner.adapter.identity, owner.adapter.router.policy
    if changed == "policy":
        policy = replace(policy, allowed_actions=("NO_NEW_ORDERS",))
    if changed == "chat":
        identity = replace(identity, chat_id=43, operators=(43,))
        policy = replace(policy, operators=(43,))
    competitor = AcknowledgingBot(chat_id=str(identity.chat_id), operators=tuple(map(str, identity.operators)))
    competitor._updates = pending
    other = TelegramOperatorCommandPoller(other_store, worker_key,
        TelegramOperatorCommandAdapter(competitor, identity, OperatorSafetyRouter(other_store, policy)))
    pending.append(message_update("/CANCEL_AND_HALT ACCOUNT account emergency"))
    for _ in range(2):
        try:
            asyncio.run(other.step())
        except OperatorCommandPollerError as exc:
            assert str(exc) == "OPERATOR_COMMANDS_BOT_OWNER_MISMATCH"
    # A fresh poller for the original owner must still receive this safety command.
    asyncio.run(build(bot).step())
    # One durable first-claim record plus the one applied emergency command;
    # no competitor ever durably claims or writes anything.
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 2
    assert competitor.calls == []
    assert bot.calls == [0, 0]


@pytest.mark.parametrize("obstruction", ["directory", "symlink"])
def test_failed_bot_lock_open_does_not_leak_worker_descriptors(rig, tmp_path, obstruction):
    store, build = rig
    lock_path = store.path.with_name("operator-bot-123.lock")
    if obstruction == "directory":
        lock_path.mkdir()
    else:
        lock_path.symlink_to(tmp_path / "absent")
    before = len(os.listdir("/proc/self/fd"))
    for _ in range(3):
        with pytest.raises(OSError):
            asyncio.run(build(Bot()).step())
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("different_store", [False, True])
def test_inflight_owner_excludes_a_real_competing_poll_and_recovers_after_cancellation(rig, tmp_path, different_store):
    store, build = rig

    async def run():
        entered = asyncio.Event()

        class WaitingBot(Bot):
            async def updates(self, offset):
                entered.set()
                await asyncio.Event().wait()

        owner = build(WaitingBot())
        task = asyncio.create_task(owner.step())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            other_store = EvidenceStore(tmp_path / "other.sqlite", "V11_PAPER") if different_store else store
            bot = Bot()
            other = TelegramOperatorCommandPoller(other_store, "competing-worker",
                TelegramOperatorCommandAdapter(bot, owner.adapter.identity,
                    OperatorSafetyRouter(other_store, owner.adapter.router.policy)))
            with pytest.raises(OperatorCommandPollerError, match="BOT_ALREADY_POLLING"):
                await other.step()
            assert bot.calls == []
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert owner.offset() == 0
        recovered = build(Bot([message_update("/CANCEL_AND_HALT ACCOUNT account recovered")]))
        assert (await recovered.step())[0]["outcome"]["actor"] == 42
        assert recovered.offset() == 2

    before = len(os.listdir("/proc/self/fd"))
    asyncio.run(run())
    assert len(os.listdir("/proc/self/fd")) == before


@pytest.mark.parametrize("failure", ["short_write", "file_sync", "directory_sync"])
def test_owner_binding_must_be_durable_before_network_and_failed_claims_preserve_state(rig, monkeypatch, failure):
    import stat

    store, build = rig
    bot = Bot([message_update("/CANCEL_AND_HALT ACCOUNT account durable")])
    poller = build(bot)
    real_write, real_fsync = os.write, os.fsync

    def write(fd, data):
        return real_write(fd, data[:5] if failure == "short_write" else data)

    def fsync(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if (failure == "directory_sync" and directory) or (failure == "file_sync" and not directory):
            raise OSError("synthetic sync failure")
        return real_fsync(fd)

    before = len(os.listdir("/proc/self/fd"))
    with monkeypatch.context() as patch:
        patch.setattr(os, "write", write)
        patch.setattr(os, "fsync", fsync)
        with pytest.raises((OperatorCommandPollerError, OSError)):
            asyncio.run(poller.step())
    assert bot.calls == [] and poller.offset() == 0
    # The durable first-claim commits before the local lock file write/sync
    # that these failures target, so it survives even though the local
    # binding does not.
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1
    assert len(os.listdir("/proc/self/fd")) == before
    if failure == "short_write":
        with pytest.raises(OperatorCommandPollerError, match="BOT_OWNER_MISMATCH"):
            asyncio.run(build(bot).step())
        assert store.path.with_name("operator-bot-123.lock").read_bytes() == b"alpha"
    else:
        assert asyncio.run(build(bot).step())[0]["outcome"]["actor"] == 42


def test_corrupt_owner_binding_is_not_overwritten_or_used_for_polling(rig):
    store, build = rig
    bot = Bot()
    asyncio.run(build(bot).step())
    lock_path = store.path.with_name("operator-bot-123.lock")
    lock_path.write_bytes(b"partial-or-unknown-owner")
    with pytest.raises(OperatorCommandPollerError, match="BOT_OWNER_MISMATCH"):
        asyncio.run(build(bot).step())
    assert bot.calls == [0]
    assert lock_path.read_bytes() == b"partial-or-unknown-owner"


@pytest.mark.parametrize("unsafe", ["fifo", "hardlink", "permissions"])
def test_unsafe_bot_lock_is_refused_without_polling_or_mutating_it(rig, tmp_path, unsafe):
    store, build = rig
    lock_path = store.path.with_name("operator-bot-123.lock")
    if unsafe == "fifo":
        os.mkfifo(lock_path, 0o600)
    else:
        lock_path.write_bytes(b"preserve")
        lock_path.chmod(0o600 if unsafe == "hardlink" else 0o644)
        if unsafe == "hardlink":
            os.link(lock_path, tmp_path / "other-link")
    bot = Bot()
    before = len(os.listdir("/proc/self/fd"))
    with pytest.raises(OperatorCommandPollerError, match="BOT_LOCK_UNSAFE"):
        asyncio.run(build(bot).step())
    assert bot.calls == []
    assert len(os.listdir("/proc/self/fd")) == before
    if unsafe != "fifo":
        assert lock_path.read_bytes() == b"preserve"


def test_distinct_bots_in_one_directory_have_independent_owners_and_cursors(rig, tmp_path):
    from dataclasses import replace

    store, build = rig
    first = build(Bot([message_update("/CANCEL_AND_HALT ACCOUNT account first")]))
    asyncio.run(first.step())
    other_store = EvidenceStore(tmp_path / "other.sqlite", "V11_PAPER")
    bot = Bot([message_update("/CANCEL_AND_HALT ACCOUNT account second", uid=5)], bot_id="999")
    other = TelegramOperatorCommandPoller(other_store, WORKER_KEY,
        TelegramOperatorCommandAdapter(bot, replace(first.adapter.identity, bot_id=999),
            OperatorSafetyRouter(other_store, first.adapter.router.policy)))
    assert asyncio.run(other.step())[0]["outcome"]["actor"] == 42
    assert first.offset() == 2 and other.offset() == 6
    # Each store holds its own bot's first-claim record plus its own applied
    # safety reduction.
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 2
    assert len(other_store.records(kind="OPERATOR_EVENT", limit=10)) == 2


def test_a_fresh_empty_local_lock_file_cannot_reclaim_a_durably_claimed_bot(rig):
    """The durable claim, not the local lock file, is authoritative.

    A different directory or host sharing this same evidence store starts
    with its own empty local lock file; that must not let it believe the bot
    is unclaimed. Simulate that by resetting the local file to empty after a
    real claim and attempting to bind a different, independent identity.
    """
    from dataclasses import replace

    store, build = rig
    owner = build(Bot([]))
    assert asyncio.run(owner.step()) == []
    owner._bot_lock_path().write_bytes(b"")
    independent_identity = replace(owner.adapter.identity, chat_id=99, operators=(99,))
    independent_policy = replace(owner.adapter.router.policy, operators=(99,))
    intruder_bot = Bot(chat_id="99", operators=("99",))
    intruder = TelegramOperatorCommandPoller(store, WORKER_KEY,
        TelegramOperatorCommandAdapter(intruder_bot, independent_identity,
                                        OperatorSafetyRouter(store, independent_policy)))
    with pytest.raises(OperatorCommandPollerError, match="BOT_OWNER_MISMATCH"):
        asyncio.run(intruder.step())
    assert intruder_bot.calls == []
    # The true owner is unaffected and its cursor still advances normally.
    owner._bot_lock_path().write_bytes(owner._binding())
    assert asyncio.run(owner.step()) == []
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1


def test_replacing_the_database_at_an_owned_path_requires_review(rig):
    store, build = rig
    owner = build(Bot())
    asyncio.run(owner.step())
    original_path = store.path.with_name("preserved.sqlite")
    store.path.rename(original_path)
    fresh = EvidenceStore(store.path, "V11_PAPER")
    bot = Bot()
    replacement = TelegramOperatorCommandPoller(fresh, WORKER_KEY,
        TelegramOperatorCommandAdapter(bot, owner.adapter.identity,
            OperatorSafetyRouter(fresh, owner.adapter.router.policy)))
    with pytest.raises(OperatorCommandPollerError, match="BOT_OWNER_MISMATCH"):
        asyncio.run(replacement.step())
    assert bot.calls == [] and original_path.is_file()


def test_handoff_transfers_binding_without_disturbing_the_cursor(rig):
    store, build = rig
    from dataclasses import replace

    first = build(Bot([message_update("/CANCEL_AND_HALT ACCOUNT account first")]))
    asyncio.run(first.step())
    assert first.offset() == 2
    new_identity = replace(first.adapter.identity, chat_id=43, operators=(43,))
    new_policy = replace(first.adapter.router.policy, operators=(43,))
    successor_bot = Bot([message_update("/CANCEL_AND_HALT ACCOUNT account second", uid=5, actor=43, chat=43)],
                         chat_id="43", operators=("43",))
    successor = TelegramOperatorCommandPoller(store, WORKER_KEY,
        TelegramOperatorCommandAdapter(successor_bot, new_identity, OperatorSafetyRouter(store, new_policy)))
    with pytest.raises(OperatorCommandPollerError, match="BOT_OWNER_MISMATCH"):
        asyncio.run(successor.step())
    result = reviewed_handoff(successor, first, "operator rotation drill")
    assert result == {"outcome": "OPERATOR_COMMANDS_BOT_OWNER_HANDOFF", "reason": "operator rotation drill"}
    # The cursor (offset 2, keyed by the unchanged worker_key/store) is preserved.
    assert successor.offset() == 2
    outcomes = asyncio.run(successor.step())
    assert outcomes[0]["outcome"]["actor"] == 43
    assert successor.offset() == 6
    handoffs = [r for r in store.records(kind="OPERATOR_EVENT", limit=10)
                if r["body"]["details"].get("outcome") == "OPERATOR_COMMANDS_BOT_OWNER_HANDOFF"]
    assert len(handoffs) == 1
    assert handoffs[0]["body"]["details"]["reason"] == "operator rotation drill"
    # The old binding still refuses to poll once superseded.
    with pytest.raises(OperatorCommandPollerError, match="BOT_OWNER_MISMATCH"):
        asyncio.run(first.step())


def test_handoff_refuses_a_bot_that_was_never_durably_claimed(rig):
    store, build = rig
    owner = build(Bot([]))
    successor = rotated_poller(owner)
    with pytest.raises(OperatorCommandPollerError, match="OPERATOR_COMMANDS_HANDOFF_NOT_CLAIMED"):
        reviewed_handoff(successor, owner)
    assert not store.records(kind="OPERATOR_EVENT", limit=10)


def test_handoff_refuses_a_no_op_transfer(rig):
    store, build = rig
    poller = build(Bot([]))
    asyncio.run(poller.step())
    with pytest.raises(OperatorCommandPollerError, match="OPERATOR_COMMANDS_HANDOFF_NOT_CHANGED"):
        reviewed_handoff(poller, poller, "nothing actually changed")


@pytest.mark.parametrize("reason", ["", "   ", "x" * 201, "line one\nline two", "one\rtwo", "one\x00two", "one\u2028two"])
def test_handoff_rejects_an_invalid_reason(rig, reason):
    store, build = rig
    from dataclasses import replace

    owner = build(Bot([]))
    asyncio.run(owner.step())
    identity = replace(owner.adapter.identity, chat_id=43, operators=(43,))
    policy = replace(owner.adapter.router.policy, operators=(43,))
    successor = TelegramOperatorCommandPoller(store, WORKER_KEY,
        TelegramOperatorCommandAdapter(Bot(chat_id="43", operators=("43",)), identity,
                                        OperatorSafetyRouter(store, policy)))
    with pytest.raises(OperatorCommandPollerError, match="OPERATOR_COMMANDS_HANDOFF_REASON_INVALID"):
        reviewed_handoff(successor, owner, reason)


def test_handoff_is_refused_while_a_poll_holds_the_bot_lock(rig, tmp_path):
    import fcntl

    store, build = rig
    poller = build(Bot([]))
    asyncio.run(poller.step())
    lock_path = store.path.with_name("operator-bot-123.lock")
    fd = os.open(lock_path, os.O_CREAT | os.O_WRONLY, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(OperatorCommandPollerError, match="OPERATOR_COMMANDS_BOT_ALREADY_POLLING"):
            reviewed_handoff(poller, poller, "cannot run concurrently")
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def test_worker_key_must_be_a_valid_identity(rig):
    store, build = rig
    bot = Bot([])
    identity = TelegramCommandIdentity(bot_id=123, chat_id=42, operators=(42,))
    policy = OperatorSafetyPolicy(account_id="account", operators=(42,),
        allowed_scopes=("ACCOUNT",), allowed_actions=("CANCEL_AND_HALT",))
    adapter = TelegramOperatorCommandAdapter(bot, identity, OperatorSafetyRouter(store, policy))
    with pytest.raises(EvidenceError, match="INVALID_IDENTITY"):
        TelegramOperatorCommandPoller(store, "", adapter)


@pytest.mark.parametrize("text, error", [
    ("/DISABLE_INVENTORY_OPERATIONS ACCOUNT account denied", "ACTION_NOT_PREAUTHORIZED"),
    ("/CANCEL_AND_HALT ACCOUNT other-account stop", "ACCOUNT_SCOPE_MISMATCH"),
    ("/QUARANTINE_STATION ACCOUNT account wrong scope", "SAFETY_SCOPE_MISMATCH"),
    ("/CANCEL_AND_HALT EVENT " + "x" * 161 + " stop", "INVALID_IDENTITY"),
    ("/CANCEL_AND_HALT ACCOUNT account " + "x" * 161, "INVALID_IDENTITY"),
    ("/CANCEL_AND_HALT ACCOUNT account multi\nline reason", "INVALID_IDENTITY"),
])
def test_rejected_command_does_not_block_later_emergency_command(rig, text, error):
    store, build = rig
    bot = Bot([message_update(text, uid=1),
               message_update("/CANCEL_AND_HALT ACCOUNT account emergency", uid=2)])
    poller = build(bot)
    outcomes = asyncio.run(poller.step())
    assert outcomes[0] == {"update_id": 1, "error": error}
    assert outcomes[1]["outcome"]["result"]["body"]["details"]["flags"]["no_new_orders"]
    assert poller.offset() == 3
    # One durable first-claim record plus the one applied emergency command.
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 2
    assert asyncio.run(build(bot).step()) == []
    assert bot.calls == [0, 3]


def test_conflicting_redelivery_preserves_original_and_processes_later_command(rig):
    store, build = rig
    first = message_update("/NO_NEW_ORDERS ACCOUNT account original", uid=1)
    poller = build(Bot([]))
    original = poller.adapter.handle(first)["result"]
    # Simulate a committed command whose batch cursor was not committed.
    bot = Bot([message_update("/CANCEL_AND_HALT ACCOUNT account changed", uid=1),
               message_update("/CANCEL_AND_HALT ACCOUNT account emergency", uid=2)])
    poller = build(bot)
    outcomes = asyncio.run(poller.step())
    assert outcomes[0] == {"update_id": 1, "error": "REPLAY_REQUEST_CONFLICT"}
    assert outcomes[1]["outcome"]["result"]["body"]["details"]["cancellation_status"] == "REQUESTED_NOT_CONFIRMED"
    assert store.get(original["id"]) == original
    # The direct adapter.handle() call (original), the second poller's
    # first-claim record, and its one applied emergency command.
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 3
    assert poller.offset() == 3


@pytest.mark.parametrize("kind, error", [
    ("OPERATOR_EVENT", EvidenceError("ARCHIVE_DISK_HEADROOM")),
    ("OPERATOR_EVENT", EvidenceError("AUDIT_STATE_CHANGED")),
    ("OPERATOR_EVENT", EvidenceError("RECORD_INTEGRITY_FAILED")),
    ("OPERATOR_EVENT", sqlite3.OperationalError("database is locked")),
    ("RUNTIME_STATUS", EvidenceError("AUDIT_STATE_CHANGED")),
])
def test_failed_write_keeps_cursor_retryable_and_prior_reductions_intact(rig, monkeypatch, kind, error):
    store, build = rig
    bot = Bot([message_update("/NO_NEW_ORDERS ACCOUNT account first", uid=1),
               message_update("/CANCEL_AND_HALT ACCOUNT account emergency", uid=2)])
    # Durably claim the bot first so the injected OPERATOR_EVENT failure below
    # targets a command's own reduction, not the unrelated first-claim record.
    assert asyncio.run(build(Bot()).step()) == []
    claimed = len(store.records(kind="OPERATOR_EVENT", limit=10))
    poller = build(bot)
    audit = store.audit

    def fail_write(record_id, **kwargs):
        if kwargs["kind"] == kind and record_id != "telegram:42:1001":
            raise error
        return audit(record_id, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "audit", fail_write)
        with pytest.raises(type(error), match=str(error)):
            asyncio.run(poller.step())
    assert poller.offset() == 0
    original = store.get("telegram:42:1001")
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == claimed + (2 if kind == "RUNTIME_STATUS" else 1)
    recovered = build(bot)
    outcomes = asyncio.run(recovered.step())
    assert bot.calls == [0, 0]
    assert outcomes[0]["outcome"]["result"] == original
    assert outcomes[1]["outcome"]["result"]["body"]["details"]["cancellation_status"] == "REQUESTED_NOT_CONFIRMED"
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == claimed + 2
    assert recovered.offset() == 3


def test_router_freshness_rejection_does_not_block_later_command(rig):
    store, build = rig
    now = int(time.time())
    bot = Bot([message_update("/NO_NEW_ORDERS ACCOUNT account future", uid=1, date=now),
               message_update("/CANCEL_AND_HALT ACCOUNT account emergency", uid=2, date=now - 2)])
    poller = build(bot)
    poller.adapter.router.clock = lambda: now - 1
    outcomes = asyncio.run(poller.step())
    assert outcomes[0] == {"update_id": 1, "error": "COMMAND_EXPIRED_OR_BACKDATED"}
    assert outcomes[1]["outcome"]["actor"] == 42
    assert poller.offset() == 3
    # One durable first-claim record plus one applied safety reduction.
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 2


def test_real_telegram_client_uses_bounded_mocked_transport_and_resumes(rig):
    import json
    import httpx

    store, build = rig
    config = store.path.with_name("telegram-test.json")
    config.write_text(json.dumps({"token": "123:synthetic_test_only", "chat_id": 42,
                                  "operator_user_ids": [42]}))
    config.chmod(0o600)
    updates = [message_update("/CANCEL_AND_HALT ACCOUNT wrong-account typo", uid=1),
               message_update("/CANCEL_AND_HALT ACCOUNT account emergency", uid=2)]
    calls = []

    def respond(request):
        assert request.method == "POST" and request.url.path.endswith("/getUpdates")
        payload = json.loads(request.content)
        calls.append(payload)
        return httpx.Response(200, json={"ok": True, "result": [
            update for update in updates if update["update_id"] >= payload["offset"]]})

    async def run():
        bot = Telegram(config, transport=httpx.MockTransport(respond))
        try:
            outcomes = await build(bot).step()
            assert outcomes[0] == {"update_id": 1, "error": "ACCOUNT_SCOPE_MISMATCH"}
            assert outcomes[1]["outcome"]["actor"] == 42
            assert await build(bot).step() == []
        finally:
            await bot.close()

    asyncio.run(run())
    assert calls == [{"offset": offset, "timeout": 0, "limit": 25,
                      "allowed_updates": ["message", "callback_query"]} for offset in (0, 3)]
    # One durable first-claim record plus one applied safety reduction.
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 2


# Independent batch-9 review: transfer boundaries and durable recovery.
def rotated_poller(owner, *, store=None, worker_key=None):
    from dataclasses import replace

    store = store or owner.store
    identity = replace(owner.adapter.identity, chat_id=43, operators=(43,))
    policy = replace(owner.adapter.router.policy, operators=(43,))
    return TelegramOperatorCommandPoller(store, worker_key or owner.worker_key,
        TelegramOperatorCommandAdapter(Bot(chat_id="43", operators=("43",)), identity,
                                        OperatorSafetyRouter(store, policy)))


def reviewed_handoff(successor, predecessor, reason="reviewed rotation"):
    return successor.handoff_bot_owner(previous_identity=predecessor.adapter.identity,
                                       previous_policy=predecessor.adapter.router.policy, reason=reason)


@pytest.mark.parametrize("changed", ["worker", "store", "namespace", "database"])
def test_review_handoff_cannot_adopt_an_independent_cursor(rig, tmp_path, changed):
    store, build = rig
    owner = build(Bot([message_update("/NO_NEW_ORDERS ACCOUNT account original")]))
    asyncio.run(owner.step())
    original_binding = owner._bot_lock_path().read_bytes()
    original_worker = owner.worker_key
    if changed in {"store", "namespace"}:
        target = EvidenceStore(tmp_path / "other.sqlite",
                               "ABLATION:review" if changed == "namespace" else "V11_PAPER")
    elif changed == "database":
        store.path.rename(tmp_path / "preserved.sqlite")
        target = EvidenceStore(store.path, "V11_PAPER")
    else:
        target = store
    successor = rotated_poller(owner, store=target,
                              worker_key="other-worker" if changed == "worker" else original_worker)
    assert successor.offset() == 0 and (changed == "database" or owner.offset() == 2)
    with pytest.raises(OperatorCommandPollerError):
        reviewed_handoff(successor, owner)
    assert owner._bot_lock_path().read_bytes() == original_binding
    assert successor.adapter.telegram.calls == []


def test_review_failed_handoff_cannot_empty_the_owner_lock(rig, monkeypatch):
    store, build = rig
    owner = build(Bot())
    asyncio.run(owner.step())
    lock_path = owner._bot_lock_path()
    before = lock_path.read_bytes()
    successor = rotated_poller(owner)

    def fail_write(*args):
        raise OSError("synthetic owner write failure")

    with monkeypatch.context() as patch:
        patch.setattr(os, "write", fail_write)
        try:
            reviewed_handoff(successor, owner)
        except OSError:
            pass
    # A handoff must never turn an owned bot into an unclaimed empty file.
    assert lock_path.read_bytes() == before


def test_review_repeated_rotation_has_distinct_durable_history(rig):
    store, build = rig
    owner = build(Bot())
    asyncio.run(owner.step())
    successor = rotated_poller(owner)
    reviewed_handoff(successor, owner, "first rotation")
    reviewed_handoff(owner, successor, "reviewed rollback")
    reviewed_handoff(successor, owner, "second rotation")
    handoffs = [r for r in store.records(kind="OPERATOR_EVENT", limit=10)
                if r["body"]["details"].get("outcome") == "OPERATOR_COMMANDS_BOT_OWNER_HANDOFF"]
    assert len(handoffs) == 3
    assert [r["body"]["details"]["reason"] for r in handoffs] == [
        "first rotation", "reviewed rollback", "second rotation"]


@pytest.mark.parametrize("saved", [b"", b"partial", b"\xff" * 4097],
                         ids=["empty", "partial", "oversized-non-ascii"])
def test_review_handoff_cannot_overwrite_unknown_owner_state(rig, saved):
    store, build = rig
    owner = build(Bot())
    asyncio.run(owner.step())
    path = owner._bot_lock_path()
    path.write_bytes(saved)
    successor = rotated_poller(owner)
    with pytest.raises(OperatorCommandPollerError):
        reviewed_handoff(successor, owner)
    assert path.read_bytes() == saved
    # Only the original first-claim record; the refused handoff writes nothing.
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1


def test_review_handoff_retry_after_committed_audit_is_idempotent(rig, monkeypatch):
    store, build = rig
    owner = build(Bot())
    asyncio.run(owner.step())
    successor = rotated_poller(owner)
    audit = store.audit

    def interrupted(*args, **kwargs):
        audit(*args, **kwargs)
        raise OSError("synthetic interruption after audit commit")

    with monkeypatch.context() as patch:
        patch.setattr(store, "audit", interrupted)
        with pytest.raises(OSError, match="after audit commit"):
            reviewed_handoff(successor, owner)
    reviewed_handoff(successor, owner)
    assert asyncio.run(successor.step()) == []
    with pytest.raises(OperatorCommandPollerError, match="BOT_OWNER_MISMATCH"):
        asyncio.run(owner.step())
    # The original first-claim record plus the one committed handoff.
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 2


@pytest.mark.parametrize("failure", ["audit", "file_sync", "directory_sync"])
def test_handoff_failure_before_commit_preserves_owner_and_cursor(rig, monkeypatch, failure):
    import stat

    store, build = rig
    owner = build(Bot([message_update("/NO_NEW_ORDERS ACCOUNT account existing")]))
    asyncio.run(owner.step())
    successor = rotated_poller(owner)
    anchor = owner._bot_lock_path().read_bytes()
    original_head = owner._head()
    original_events = store.records(kind="OPERATOR_EVENT", limit=10)
    real_fsync = os.fsync

    def fail_audit(*args, **kwargs):
        raise EvidenceError("ARCHIVE_DISK_HEADROOM")

    def fsync(fd):
        directory = stat.S_ISDIR(os.fstat(fd).st_mode)
        if (failure == "directory_sync" and directory) or (failure == "file_sync" and not directory):
            raise OSError("synthetic sync failure")
        return real_fsync(fd)

    before = len(os.listdir("/proc/self/fd"))
    with monkeypatch.context() as patch:
        if failure == "audit":
            patch.setattr(store, "audit", fail_audit)
        else:
            patch.setattr(os, "fsync", fsync)
        with pytest.raises((EvidenceError, OSError)):
            reviewed_handoff(successor, owner)
    assert len(os.listdir("/proc/self/fd")) == before
    assert owner._bot_lock_path().read_bytes() == anchor
    assert owner._head() == original_head
    assert store.records(kind="OPERATOR_EVENT", limit=10) == original_events
    with pytest.raises(OperatorCommandPollerError, match="BOT_OWNER_MISMATCH"):
        asyncio.run(successor.step())
    assert asyncio.run(owner.step()) == []
    reviewed_handoff(successor, owner)
    assert asyncio.run(successor.step()) == []


@pytest.mark.parametrize("after_commit", [False, True])
def test_handoff_recovers_after_actual_process_exit(rig, after_commit):
    store, build = rig
    owner = build(Bot([message_update("/NO_NEW_ORDERS ACCOUNT account existing")]))
    asyncio.run(owner.step())
    successor = rotated_poller(owner)
    before = owner._head()
    pid = os.fork()
    if pid == 0:
        audit = store.audit

        def crash(*args, **kwargs):
            if after_commit:
                audit(*args, **kwargs)
            os._exit(73)

        store.audit = crash
        reviewed_handoff(successor, owner)
        os._exit(74)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 73
    reopened = EvidenceStore(store.path, store.namespace)
    recovered = rotated_poller(owner, store=reopened)
    if after_commit:
        with pytest.raises(OperatorCommandPollerError, match="BOT_OWNER_MISMATCH"):
            asyncio.run(owner.step())
        assert asyncio.run(recovered.step()) == []
    else:
        with pytest.raises(OperatorCommandPollerError, match="BOT_OWNER_MISMATCH"):
            asyncio.run(recovered.step())
        assert asyncio.run(owner.step()) == []
    reviewed_handoff(recovered, owner)
    assert recovered._head() == before
    assert asyncio.run(recovered.step()) == []
    handoffs = [r for r in reopened.records(kind="OPERATOR_EVENT", limit=10)
                if r["body"]["details"].get("outcome") == "OPERATOR_COMMANDS_BOT_OWNER_HANDOFF"]
    assert len(handoffs) == 1
    details = handoffs[0]["body"]["details"]
    assert details["cursor_seq"] == before["seq"] and details["offset"] == 2
    assert details["previous_binding"] == owner._binding().decode()
    assert details["new_binding"] == recovered._binding().decode()
    # The handoff's evidence now also cites the durable first-claim record it
    # supersedes, in addition to the preserved cursor.
    claim = next(r for r in reopened.records(kind="OPERATOR_EVENT", limit=10)
                 if r["body"]["details"].get("outcome") == "OPERATOR_COMMANDS_BOT_OWNER_CLAIM")
    assert handoffs[0]["body"]["evidence"] == [
        {"id": claim["id"], "sha256": claim["sha256"]},
        {"id": before["id"], "sha256": before["sha256"]},
    ]


@pytest.mark.parametrize("changed", ["identity", "policy", "bot", "account"])
def test_handoff_requires_the_exact_previous_configuration_and_same_account(rig, changed):
    from dataclasses import replace

    store, build = rig
    owner = build(Bot())
    asyncio.run(owner.step())
    successor = rotated_poller(owner)
    prior_identity, prior_policy = owner.adapter.identity, owner.adapter.router.policy
    if changed == "identity":
        prior_identity = replace(prior_identity, chat_id=99)
    elif changed == "policy":
        prior_policy = replace(prior_policy, allowed_actions=("NO_NEW_ORDERS",))
    elif changed == "bot":
        prior_identity = replace(prior_identity, bot_id=999)
    else:
        prior_policy = replace(prior_policy, account_id="different-account")
    with pytest.raises(OperatorCommandPollerError):
        successor.handoff_bot_owner(previous_identity=prior_identity,
                                    previous_policy=prior_policy, reason="incorrect review")
    # Only the original first-claim record; the refused handoff writes nothing.
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1
    assert asyncio.run(owner.step()) == []


def test_handoff_cursor_cas_refuses_an_intervening_cursor_write(rig, monkeypatch):
    from polymarket_scanner.v11.operator_command_poller import VERSION

    store, build = rig
    owner = build(Bot())
    asyncio.run(owner.step())
    successor = rotated_poller(owner)
    audit = store.audit

    def racing_audit(*args, **kwargs):
        audit("synthetic-intervening-cursor", event_id=WORKER_KEY, kind="RUNTIME_STATUS",
              details=dict(version=VERSION, offset=2, financial_authority=False, messages_sent=False))
        return audit(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "audit", racing_audit)
        with pytest.raises(EvidenceError, match="AUDIT_GUARDED_STATE_CHANGED"):
            reviewed_handoff(successor, owner)
    # Only the original first-claim record; the aborted handoff writes nothing.
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1
    assert asyncio.run(owner.step()) == []
    with pytest.raises(OperatorCommandPollerError, match="BOT_OWNER_MISMATCH"):
        asyncio.run(successor.step())


def test_handoff_holds_bot_lock_until_the_ownership_transaction_commits(rig, monkeypatch):
    store, build = rig
    owner = build(Bot())
    asyncio.run(owner.step())
    successor = rotated_poller(owner)
    audit = store.audit

    def competing_audit(*args, **kwargs):
        with pytest.raises(OperatorCommandPollerError, match="BOT_ALREADY_POLLING"):
            reviewed_handoff(successor, owner)
        with pytest.raises(OperatorCommandPollerError, match="BOT_ALREADY_POLLING"):
            asyncio.run(owner.step())
        return audit(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(store, "audit", competing_audit)
        reviewed_handoff(successor, owner)
    assert asyncio.run(successor.step()) == []


def test_handoff_anchor_cannot_be_cleared_to_revive_an_old_owner(rig):
    store, build = rig
    owner = build(Bot())
    asyncio.run(owner.step())
    successor = rotated_poller(owner)
    reviewed_handoff(successor, owner)
    owner._bot_lock_path().write_bytes(b"")
    for poller in (owner, successor):
        with pytest.raises(OperatorCommandPollerError, match="BOT_OWNER_MISMATCH"):
            asyncio.run(poller.step())
    assert owner._bot_lock_path().read_bytes() == b""


def test_candidate_component_handoff_resumes_original_offset_and_pending_command(rig):
    from polymarket_scanner.v11.operator_command_runtime import CandidateOperatorCommands

    store, build = rig
    owner = build(Bot([message_update("/NO_NEW_ORDERS ACCOUNT account original")]))
    asyncio.run(owner.step())
    rotated = rotated_poller(owner)
    pending = message_update("/CANCEL_AND_HALT ACCOUNT account emergency", uid=2, actor=43, chat=43)
    bot = Bot([pending], chat_id="43", operators=("43",))
    component = CandidateOperatorCommands(store, "account", WORKER_KEY, telegram=bot,
        identity=rotated.adapter.identity, policy=rotated.adapter.router.policy)
    component.handoff_bot_owner(previous_identity=owner.adapter.identity,
                                previous_policy=owner.adapter.router.policy, reason="reviewed component rotation")
    outcome = asyncio.run(component.step())
    assert bot.calls == [2]
    assert component.poller.offset() == 3
    result = outcome["outcomes"][0]["outcome"]["result"]["body"]["details"]
    assert result["cancellation_status"] == "REQUESTED_NOT_CONFIRMED"
    assert result["flags"]["no_new_orders"] and result["cancellation_request_id"] == "telegram:43:1002"
