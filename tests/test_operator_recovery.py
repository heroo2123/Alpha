"""Grant rotation, restart and settings propagation use only isolated fixtures."""
import asyncio
from dataclasses import replace
from decimal import Decimal
import json
import time

import pytest

from polymarket_scanner.production.config import digest, canonical
from polymarket_scanner.production.control import ControlError, ControlStore, initial_settings
from polymarket_scanner.production.executor_control import rotate_authorization
from polymarket_scanner.production.engine import ExecutionEngine
from polymarket_scanner.production.collection import Scanner
from polymarket_scanner.production.controller import Controller
from polymarket_scanner.production.io import atomic_json
from polymarket_scanner.production.signals import SignalStore
from test_operator_control_protocol import operator_config, button, click
from test_operator_executor import op, request, activate
from test_operator_panel import Bot
from test_operator_collection import collection


def new_grant(op):
    old=op[0]
    policy=replace(old.operator_control,db=old.signal_db.parent/"new-control.db",authorization_version="fixture-grant-2")
    cfg=replace(old,operator_control=policy,config_sha256="f"*64)
    cfg.activation_file.write_text(json.dumps({"action":"ACTIVATE_LIVE_EXECUTION","wallet":cfg.wallet,"config_sha256":cfg.config_sha256}))
    return cfg,ControlStore(cfg)


def test_local_grant_rotation_preserves_fills_fault_and_consumed_receipts(op):
    activate(op); asyncio.run(op[-1].tick()); op[4].fill(Decimal("1")); asyncio.run(op[-1].reconcile())
    ledger=op[3]; before=ledger.summary(); ledger.fault("FIXTURE_STICKY_FAULT")
    cfg,controls=new_grant(op)
    try:
        rotate_authorization(cfg,ledger,digest(json.loads(ledger.state("control_identity"))))
        engine=ExecutionEngine(cfg,ledger,op[-1].reader,op[4],op[5])
        after=ledger.summary()
        assert before["positions"]==after["positions"] and before["confirmed_fill_count"]==after["confirmed_fill_count"]==1
        assert ledger.state("fault")=="FIXTURE_STICKY_FAULT"
        assert engine.control.settings["paused"] and engine.control.settings["experience"]=="SIGNALS"
        assert not engine.authority()
        with ledger.connect() as db: assert db.execute("SELECT COUNT(*) FROM execution_controls").fetchone()[0]==2
    finally: controls.close()


@pytest.mark.parametrize("block",["outstanding","identity","activation","nonempty"])
def test_grant_rotation_rejects_unsafe_preconditions_without_journal_change(op,block):
    activate(op)
    if block=="outstanding": asyncio.run(op[-1].tick())
    ledger=op[3]; before=ledger.state("control_identity")
    cfg,controls=new_grant(op)
    try:
        expected=digest(json.loads(before))
        if block=="identity": expected="0"*64
        elif block=="activation": cfg.activation_file.unlink()
        elif block=="nonempty": click(controls,button(controls))
        with pytest.raises(ControlError): rotate_authorization(cfg,ledger,expected)
        assert ledger.state("control_identity")==before
    finally: controls.close()


def test_manual_controller_restart_consumes_durable_setting_once(tmp_path):
    cfg=operator_config(tmp_path,mode="LIVE_SIGNALS")
    controls=ControlStore(cfg); signals=SignalStore(cfg.signal_db)
    try:
        action=button(controls,"SET",{"key":"min_model_gap","value":".3"})
        click(controls,action)
        controls.close(); controls=ControlStore(cfg)
        controller=Controller(cfg,signals,controls,Bot())
        controller.panel.process_signals_requests(); controller.panel.process_signals_requests()
        assert controller.panel.settings()["min_model_gap"]=="0.3"
        assert controller.panel.settings()["revision"]==1
        with controls.connect() as db: assert db.execute("SELECT COUNT(*) FROM operator_request_results").fetchone()[0]==1
    finally: controls.close(); signals.close()


def test_expired_manual_request_is_audited_rejected_after_restart(tmp_path,monkeypatch):
    cfg=operator_config(tmp_path,mode="LIVE_SIGNALS"); controls=ControlStore(cfg); signals=SignalStore(cfg.signal_db)
    try:
        click(controls,button(controls,"SET",{"key":"min_model_gap","value":".3"}))
        future=time.time()+121; monkeypatch.setattr(time,"time",lambda:future)
        controller=Controller(cfg,signals,controls,Bot()); controller.panel.process_signals_requests()
        assert controller.panel.settings()["min_model_gap"]==str(cfg.min_model_gap)
        assert controls.state("signals_control_result")=="CONTROL_REQUEST_EXPIRED"
    finally: controls.close(); signals.close()


def test_reduced_threshold_filters_already_queued_signal(collection,op):
    scanner,controller,bot=collection
    status=op[-1].status(); status["control"]["min_model_gap"]=".5"
    atomic_json(op[0].execution_status_path,status)
    scanner.publish("SIGNAL",dict(op[1],id="new",key="new"),0)
    asyncio.run(controller.collect())
    assert not bot.messages


def test_scanner_evaluates_current_operator_strategy_thresholds(collection,op):
    scanner,controller,bot=collection; called=[]
    settings=initial_settings(op[0]); settings.update(strategies=["MAKER"],min_model_gap=".35",min_structural_edge=".15")
    op[2].set_state("collection_settings",canonical(settings))
    class Weather:
        async def revalidate(self,s): return s
        async def discover(self): return {"status":{},"events":[{"id":"1"}]}
        async def evaluate(self,event,strategies,gap,edge): called.append((strategies,gap,edge)); return []
        async def outcome(self,s): return None
    asyncio.run(Scanner(op[0],scanner,Weather()).cycle())
    assert called==[({"MAKER"},Decimal(".35"),Decimal(".15"))]


def test_stale_executor_status_keeps_last_displayed_limits_but_no_authority(collection,op):
    scanner,controller,bot=collection
    status=op[-1].status(); status["control"]["min_model_gap"]=".35"
    atomic_json(op[0].execution_status_path,status)
    assert controller.panel.settings()["min_model_gap"]==".35"
    status["updated_at"]=time.time()-31; atomic_json(op[0].execution_status_path,status)
    assert controller.panel.settings()["min_model_gap"]==".35"
    assert not controller.service.execution_status()["financial_authority"]
