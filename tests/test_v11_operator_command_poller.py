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
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1


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
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1


def test_replaying_the_same_batch_twice_is_idempotent(rig):
    store, build = rig
    bot = Bot([message_update("/CANCEL_AND_HALT ACCOUNT account stop", uid=1)])
    poller = build(bot)
    first = asyncio.run(poller.step())
    second = asyncio.run(build(Bot(bot._updates)).step())
    assert second == []
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1


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
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1
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
    assert not store.records(kind="OPERATOR_EVENT", limit=10)
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
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1
    assert len(other_store.records(kind="OPERATOR_EVENT", limit=10)) == 1


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
    result = successor.handoff_bot_owner(reason="operator rotation drill")
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


def test_handoff_refuses_a_no_op_transfer(rig):
    store, build = rig
    poller = build(Bot([]))
    asyncio.run(poller.step())
    with pytest.raises(OperatorCommandPollerError, match="OPERATOR_COMMANDS_HANDOFF_NOT_CHANGED"):
        poller.handoff_bot_owner(reason="nothing actually changed")


@pytest.mark.parametrize("reason", ["", "   ", "x" * 201, "line one\nline two"])
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
        successor.handoff_bot_owner(reason=reason)


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
            poller.handoff_bot_owner(reason="cannot run concurrently")
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
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1
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
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 2
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
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == (2 if kind == "RUNTIME_STATUS" else 1)
    recovered = build(bot)
    outcomes = asyncio.run(recovered.step())
    assert bot.calls == [0, 0]
    assert outcomes[0]["outcome"]["result"] == original
    assert outcomes[1]["outcome"]["result"]["body"]["details"]["cancellation_status"] == "REQUESTED_NOT_CONFIRMED"
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 2
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
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1


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
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1
