import asyncio
import json
import sqlite3
import time

import pytest

from polymarket_scanner.production.collection import ScanStore, Scanner, drain_scanner
from polymarket_scanner.production.control import ControlError
from polymarket_scanner.production.controller import Controller
from polymarket_scanner.production.io import atomic_json
from test_operator_executor import op
from test_operator_panel import Bot
from polymarket_scanner.production.signals import SignalStore


@pytest.fixture
def collection(op):
    scanner=ScanStore(op[0]); signals=SignalStore(op[0].signal_db); bot=Bot()
    controller=Controller(op[0],signals,op[2],bot)
    yield scanner,controller,bot
    scanner.close(); signals.close()


def test_public_handoff_preserves_signal_identity_and_deduplicates(collection,op):
    scanner,controller,bot=collection
    signal=dict(op[1],id="new-signal",key="new-key")
    scanner.publish("SIGNAL",signal,0)
    asyncio.run(controller.collect())
    assert len(bot.messages)==1
    scanner.publish("SIGNAL",signal,0)
    asyncio.run(controller.collect())
    assert len(bot.messages)==1
    assert controller.store.candidate(signal["id"])==signal


def test_terminal_handoff_immediately_invalidates_delivery_without_affecting_account(collection,op):
    scanner,controller,bot=collection
    called=[]
    async def invalidate(row): called.append(row); return "EDITED"
    bot.invalidate=invalidate
    scanner.publish("TERMINAL",{"id":op[1]["id"],"status":"INVALIDATED","reason":"SOURCE_REVISION"},0)
    asyncio.run(controller.collect())
    assert called and controller.store.recent()[0]["status"]=="INVALIDATED"
    assert op[3].summary()["confirmed_fill_count"]==0


def test_cross_db_cursor_failure_replays_without_duplicate_send(collection,op,monkeypatch):
    scanner,controller,bot=collection
    signal=dict(op[1],id="new-signal",key="new-key")
    scanner.publish("SIGNAL",signal,0)
    original=op[2].set_state
    def fail(*args): raise sqlite3.OperationalError("injected")
    monkeypatch.setattr(op[2],"set_state",fail)
    with pytest.raises(sqlite3.Error): asyncio.run(controller.collect())
    assert len(bot.messages)==1
    monkeypatch.setattr(op[2],"set_state",original)
    asyncio.run(controller.collect())
    assert len(bot.messages)==1 and op[2].state("scanner_cursor")=="1"


def test_scanner_cannot_change_controller_configuration(collection,op):
    scanner,controller,bot=collection
    with scanner.connect() as db:
        db.execute("UPDATE scan_state SET value='{}' WHERE key='identity'")
    with pytest.raises(ControlError,match="IDENTITY"): asyncio.run(controller.collect())


def test_handoff_capacity_and_cursor_regression_fail_closed(collection,op):
    scanner,controller,bot=collection
    with scanner.connect() as db:
        db.executemany("INSERT INTO scan_events(id,kind,data,created) VALUES(?,'SIGNAL','{}',0)",((str(i),) for i in range(1000)))
    with pytest.raises(ControlError,match="BACKLOG_FULL"): scanner.publish("SIGNAL",op[1],0)
    scanner.publish("SIGNAL",op[1],1000)
    with pytest.raises(ControlError,match="CURSOR_REGRESSED"): asyncio.run(controller.collect())


def test_signal_settings_filter_does_not_invent_a_trade(collection,op):
    scanner,controller,bot=collection
    status=op[-1].status(); status["control"]["strategies"]=["MAKER"]
    atomic_json(op[0].execution_status_path,status)
    scanner.publish("SIGNAL",dict(op[1],id="new",key="new"),0)
    asyncio.run(controller.collect())
    assert bot.messages==[] and not op[4].posts


def test_scanner_public_inputs_run_without_telegram_or_signer(collection,op):
    scanner,controller,bot=collection
    class Weather:
        async def revalidate(self,signal): return signal
        async def discover(self): return {"status":{"supported":1,"unsupported":2},"events":[{"id":"e"}]}
        async def evaluate(self,*args): return [dict(op[1],id="new",key="new")]
        async def outcome(self,signal): return None
    worker=Scanner(op[0],scanner,Weather())
    asyncio.run(worker.run(once=True))
    status=json.loads(op[0].operator_control.scanner_status.read_text())
    assert status["last_cycle"]>0 and status["discovery"]["unsupported"]==2
    assert not bot.messages and not op[4].posts
    asyncio.run(controller.collect()); assert len(bot.messages)==1
