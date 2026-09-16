import asyncio
from dataclasses import replace
from decimal import Decimal
import json
import sqlite3
import time

import pytest

from polymarket_scanner.production.config import digest
from polymarket_scanner.production.control import ControlStore
from polymarket_scanner.production.engine import ExecutionEngine
from polymarket_scanner.production.ledger import ExecutionLedger
from polymarket_scanner.production.signals import SignalStore, SignalReader
from test_operator_control_protocol import operator_config
from test_production_lifecycle import candidate, Exchange, Weather, WALLET


@pytest.fixture
def op(tmp_path):
    cfg = operator_config(tmp_path)
    cfg.activation_file.write_text(json.dumps({"action":"ACTIVATE_LIVE_EXECUTION","wallet":WALLET,"config_sha256":cfg.config_sha256}))
    signal = candidate()
    store = SignalStore(cfg.signal_db)
    store.bind_telegram({"bot_id":"123","chat_id":"42"})
    store.save(signal); store.begin_send(signal["id"]); store.receipt(signal["id"],1)
    controls = ControlStore(cfg)
    ledger = ExecutionLedger(cfg.execution_db, WALLET)
    exchange, weather = Exchange(), Weather(signal)
    engine = ExecutionEngine(cfg,ledger,SignalReader(cfg.signal_db),exchange,weather)
    asyncio.run(engine.reconcile())
    yield cfg,signal,controls,ledger,exchange,weather,engine
    controls.close(); store.close()


def request(op, operation, data=None, *, process=True):
    cfg,signal,store,ledger,exchange,weather,engine=op
    revision=engine.control.settings["revision"]
    action=store.action(42,operation,data or {},revision)
    store.bind_message([action],7)
    with store.connect() as db:
        update=(db.execute("SELECT MAX(id) FROM operator_updates").fetchone()[0] or 0)+1
    store.click(action,actor=42,message_id=7,update_id=update,revision=revision)
    if process:
        engine.control.process()
    return action


def activate(op, experience="AUTOMATIC"):
    request(op,"SET",{"key":"experience","value":experience})
    request(op,"RESUME")
    assert op[-1].authority()


def test_initial_grant_does_not_itself_activate_automatic_trading(op):
    assert not op[-1].authority()
    asyncio.run(op[-1].tick())
    assert op[4].posts==[]


def test_automatic_uses_existing_lifecycle_and_pause_is_immediate(op):
    activate(op)
    request(op,"PAUSE",process=False)
    assert not op[-1].authority()  # before the worker consumes the request
    asyncio.run(op[-1].tick())
    assert op[4].posts==[]
    request(op,"RESUME")
    asyncio.run(op[-1].tick())
    assert len(op[4].posts)==1


def test_confirm_one_attempt_revalidates_and_cannot_duplicate(op):
    activate(op,"CONFIRM")
    engine,exchange=op[-1],op[4]
    asyncio.run(engine.tick()); assert exchange.posts==[]
    data={"signal_id":op[1]["id"],"signal_hash":digest(op[1])}
    request(op,"CONFIRM_TRADE",data)
    request(op,"CONFIRM_TRADE",data)
    exchange.ask="0.42"  # fresh quote within explicit bound
    op[5].value["legs"][0]["price"]="0.42"
    asyncio.run(engine.tick()); asyncio.run(engine.tick())
    assert len(exchange.posts)==1 and exchange.prepared["price"]==Decimal("0.42")
    assert op[5].calls>=2


def test_unknown_submission_stays_one_order_after_restart(op):
    activate(op,"CONFIRM")
    request(op,"CONFIRM_TRADE",{"signal_id":op[1]["id"],"signal_hash":digest(op[1])})
    op[4].outcome=TimeoutError("not logged")
    asyncio.run(op[-1].tick())
    restarted=ExecutionEngine(op[0],op[3],op[-1].reader,op[4],op[5])
    asyncio.run(restarted.tick())
    assert len(op[4].posts)==1


@pytest.mark.parametrize("key,value",[("per_order","4"),("strategy:DIRECTIONAL",False),("experience","SIGNALS")])
def test_settings_revision_requests_cancel_but_preserves_partial_fill(op,key,value):
    activate(op); asyncio.run(op[-1].tick())
    op[4].fill(Decimal(".5")); asyncio.run(op[-1].reconcile())
    request(op,"SET",{"key":key,"value":value})
    assert op[3].orders()[0]["status"]=="CANCEL_REQUESTED"
    asyncio.run(op[-1].safety_tick())
    assert op[4].cancels
    assert op[3].summary()["confirmed_fill_count"]==1
    assert op[3].summary()["positions"]


def test_pause_during_weather_validation_prevents_signing_and_post(op):
    activate(op)
    async def changed(signal):
        request(op,"PAUSE",process=False)
        return op[1]
    op[5].revalidate=changed
    asyncio.run(op[-1].execute(op[1]))
    assert not op[4].posts and not hasattr(op[4],"prepared")


def test_settings_change_during_signing_aborts_durable_submission(op):
    activate(op)
    prepare=op[4].prepare_buy
    def changed(**kwargs):
        value=prepare(**kwargs)
        request(op,"SET",{"key":"max_price","value":".9"})
        return value
    op[4].prepare_buy=changed
    asyncio.run(op[-1].execute(op[1]))
    assert not op[4].posts and op[3].summary()["reserved_micros"]==0


@pytest.mark.parametrize("change",["activation","fault","expired","config"])
def test_resume_cannot_bypass_external_authority(op,change):
    request(op,"SET",{"key":"experience","value":"AUTOMATIC"})
    if change=="activation": op[0].activation_file.unlink()
    elif change=="fault": op[3].fault("FIXTURE_FAULT")
    elif change=="expired": op[-1].control.policy=replace(op[0].operator_control,expires=time.time()-1)
    else: op[0].activation_file.write_text('{"config_sha256":"wrong"}')
    request(op,"RESUME")
    assert not op[-1].authority() and op[-1].control.settings["paused"]


def test_control_transaction_failure_preserves_previous_state_and_revokes_authority(op):
    activate(op)
    request(op,"PAUSE",process=False)
    with op[3].connect() as db:
        db.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON execution_controls BEGIN SELECT RAISE(ABORT,'injected'); END")
    before=op[-1].control.settings.copy()
    with pytest.raises(sqlite3.Error): op[-1].control.process()
    assert op[-1].control.settings==before and not op[-1].authority()


def test_rejected_confirmation_is_consumed_without_automatic_retry(op):
    activate(op,"CONFIRM")
    request(op,"CONFIRM_TRADE",{"signal_id":op[1]["id"],"signal_hash":digest(op[1])})
    op[4].ask=".9"
    asyncio.run(op[-1].tick())
    op[4].ask=".4"
    asyncio.run(op[-1].tick())
    assert op[4].posts==[]


def test_deleted_control_history_cannot_reopen_trading(op):
    activate(op)
    with op[2].connect() as db: db.execute("DELETE FROM operator_requests")
    assert not op[-1].authority()
