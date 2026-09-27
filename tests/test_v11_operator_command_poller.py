import asyncio
import os
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


def test_worker_key_must_be_a_valid_identity(rig):
    store, build = rig
    bot = Bot([])
    identity = TelegramCommandIdentity(bot_id=123, chat_id=42, operators=(42,))
    policy = OperatorSafetyPolicy(account_id="account", operators=(42,),
        allowed_scopes=("ACCOUNT",), allowed_actions=("CANCEL_AND_HALT",))
    adapter = TelegramOperatorCommandAdapter(bot, identity, OperatorSafetyRouter(store, policy))
    with pytest.raises(EvidenceError, match="INVALID_IDENTITY"):
        TelegramOperatorCommandPoller(store, "", adapter)
