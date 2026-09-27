import time

import pytest

from polymarket_scanner.production.telegram import Telegram
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from polymarket_scanner.v11.event_risk import EventContext, SafetyReductions
from polymarket_scanner.v11.operator_command_adapter import (
    OperatorCommandError, TelegramCommandIdentity, TelegramOperatorCommandAdapter,
)
from polymarket_scanner.v11.operator_safety_router import OperatorSafetyPolicy, OperatorSafetyRouter


class Bot:
    principal = Telegram.principal

    def __init__(self, *, bot_id="123", chat_id="42", operators=("42",)):
        self.delivery_identity = {"bot_id": bot_id, "chat_id": chat_id}
        self.operators = set(operators)


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
    adapter = TelegramOperatorCommandAdapter(Bot(), identity, router)
    return store, router, adapter


def no_reduction_recorded(store):
    return not store.records(kind="OPERATOR_EVENT", limit=1)


def test_adapter_construction_rejects_telegram_bot_chat_identity_mismatch(rig):
    _, router, _ = rig
    identity = TelegramCommandIdentity(bot_id=999, chat_id=42, operators=(42,))
    with pytest.raises(OperatorCommandError, match="ADAPTER_TELEGRAM_IDENTITY_MISMATCH"):
        TelegramOperatorCommandAdapter(Bot(), identity, router)


def test_adapter_construction_rejects_operator_allowlist_mismatch(rig):
    _, router, _ = rig
    identity = TelegramCommandIdentity(bot_id=123, chat_id=42, operators=(7,))
    with pytest.raises(OperatorCommandError, match="ADAPTER_TELEGRAM_OPERATOR_MISMATCH"):
        TelegramOperatorCommandAdapter(Bot(), identity, router)


def test_authenticated_command_applies_reduction(rig):
    store, router, adapter = rig
    update = message_update("/CANCEL_AND_HALT ACCOUNT account operator requested stop")
    outcome = adapter.handle(update)
    assert outcome["actor"] == 42
    assert outcome["result"]["body"]["details"]["flags"]["no_new_orders"]
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1


def test_replaying_the_identical_update_is_idempotent(rig):
    store, router, adapter = rig
    update = message_update("/CANCEL_AND_HALT ACCOUNT account operator requested stop")
    first = adapter.handle(update)
    second = adapter.handle(update)
    assert first == second
    assert len(store.records(kind="OPERATOR_EVENT", limit=10)) == 1


def test_same_message_id_with_different_text_conflicts(rig):
    store, router, adapter = rig
    adapter.handle(message_update("/CANCEL_AND_HALT ACCOUNT account first reason", uid=1))
    conflicting = message_update("/NO_NEW_ORDERS ACCOUNT account second reason", uid=1)
    with pytest.raises(Exception, match="REPLAY_REQUEST_CONFLICT"):
        adapter.handle(conflicting)


def test_unauthenticated_actor_is_rejected_and_creates_no_reduction(rig):
    store, router, adapter = rig
    update = message_update("/CANCEL_AND_HALT ACCOUNT account not an operator", actor=999)
    with pytest.raises(OperatorCommandError, match="UPDATE_NOT_FROM_AUTHENTICATED_OPERATOR"):
        adapter.handle(update)
    assert no_reduction_recorded(store)


def test_non_private_chat_is_rejected(rig):
    store, router, adapter = rig
    update = message_update("/CANCEL_AND_HALT ACCOUNT account wrong chat type")
    update["message"]["chat"]["type"] = "group"
    with pytest.raises(OperatorCommandError, match="UPDATE_NOT_FROM_AUTHENTICATED_OPERATOR"):
        adapter.handle(update)
    assert no_reduction_recorded(store)


def test_forwarded_message_is_rejected(rig):
    store, router, adapter = rig
    update = message_update("/CANCEL_AND_HALT ACCOUNT account forwarded", forward_from={"id": 1})
    with pytest.raises(OperatorCommandError, match="UPDATE_NOT_FROM_AUTHENTICATED_OPERATOR"):
        adapter.handle(update)
    assert no_reduction_recorded(store)


def test_stale_message_date_is_rejected(rig):
    store, router, adapter = rig
    update = message_update("/CANCEL_AND_HALT ACCOUNT account too old", date=int(time.time()) - 300)
    with pytest.raises(OperatorCommandError, match="UPDATE_NOT_FROM_AUTHENTICATED_OPERATOR"):
        adapter.handle(update)
    assert no_reduction_recorded(store)


def test_callback_query_updates_are_not_supported(rig):
    store, router, adapter = rig
    update = {"update_id": 1, "callback_query": {"id": "q1", "from": {"id": 42, "is_bot": False},
              "data": "x", "message": {"message_id": 1, "date": int(time.time()), "chat": {"id": 42}, "from": {"id": 123, "is_bot": True}}}}
    with pytest.raises(OperatorCommandError, match="COMMAND_CALLBACK_NOT_SUPPORTED"):
        adapter.handle(update)
    assert no_reduction_recorded(store)


@pytest.mark.parametrize("text", [
    "not a command at all",
    "/CANCEL_AND_HALT PORTFOLIO account bad scope",
    "/CANCEL_AND_HALT ACCOUNT",
    "/lowercase ACCOUNT account bad action case",
])
def test_malformed_command_grammar_is_rejected(rig, text):
    store, router, adapter = rig
    with pytest.raises(OperatorCommandError, match="COMMAND_GRAMMAR_INVALID"):
        adapter.handle(message_update(text))
    assert no_reduction_recorded(store)


def test_station_scope_command_reaches_the_underlying_safety_view(rig):
    store, router, adapter = rig
    update = message_update("/QUARANTINE_STATION STATION KJFK station quarantine")
    adapter.handle(update)
    context = EventContext("account", "city", "KJFK", "event")
    flags = SafetyReductions(store).view(context)["flags"]
    assert flags["quarantined"]


def test_action_not_preauthorized_by_the_underlying_policy_is_still_rejected(rig):
    store, router, adapter = rig
    update = message_update("/DISABLE_INVENTORY_OPERATIONS ACCOUNT account not preauthorized for this policy")
    with pytest.raises(Exception, match="ACTION_NOT_PREAUTHORIZED"):
        adapter.handle(update)
    assert no_reduction_recorded(store)
