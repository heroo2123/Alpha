"""Acceptance regressions derived from the separately preserved b7 review.

These tests are implementation-author adaptations, not independent signoff.
All transports, accounts and keys are isolated test fixtures.
"""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
import time
from concurrent.futures import ThreadPoolExecutor
import threading
import sqlite3

import pytest

from polymarket_scanner.production.chain import ExchangeError, STANDARD_EXCHANGE, ChainReader, calldata
from polymarket_scanner.production.engine import ExecutionEngine, ExecutionError
from polymarket_scanner.production.control import ControlStore
from polymarket_scanner.production.config import digest
from polymarket_scanner.production.exchange import order_hash
from polymarket_scanner.production.ledger import ExecutionLedger
from polymarket_scanner.production.signals import SignalStore, SignalReader
from test_production_deposit_session import (
    session_client, SessionChain, DEPOSIT, SESSION, OWNER,
)
from test_production_deposit_session_engine import make_engine
from test_production_exchange import NOW, TOKEN, CONDITION, Wire, context
from test_production_lifecycle import config, candidate, Weather
from test_operator_control_protocol import operator_config
from test_operator_executor import activate, request


class AckWire:
    """Offline POST recorder; never creates a network client."""
    def __init__(self):
        self.posts = []
        self.at_post = None

    def request(self, method, url, **kwargs):
        assert method == "POST" and url.endswith("/order")
        payload = json.loads(kwargs["body"])
        oid = order_hash(payload["order"], STANDARD_EXCHANGE, signature_type=3)
        self.posts.append(oid)
        if self.at_post:
            self.at_post()
        return 200, {"success": True, "orderID": oid, "status": "matched", "errorMsg": ""}


def real_adapter_engine(tmp_path, monkeypatch, *, with_control=False, control_expiry=None):
    tick = [float(NOW)]
    monkeypatch.setattr(time, "time", lambda: tick[0])
    cfg = config(tmp_path, wallet=DEPOSIT, signer=SESSION, deposit_owner=OWNER,
        wallet_type="DEPOSIT_WALLET", signature_type=3, session_scopes=["CLOB"],
        session_valid_until=NOW+10000, session_exclusive_until=NOW+9000)
    if with_control:
        policy = operator_config(tmp_path).operator_control
        if control_expiry is not None:
            policy = replace(policy, expires=control_expiry)
        cfg = replace(cfg, operator_control=policy)
    cfg.activation_file.write_text(json.dumps({"action": "ACTIVATE_LIVE_EXECUTION",
        "wallet": DEPOSIT, "config_sha256": cfg.config_sha256}))
    cfg.activation_file.chmod(0o600)
    signal = candidate()
    signal["legs"][0].update(token=TOKEN, condition=CONDITION)
    store = SignalStore(cfg.signal_db)
    store.bind_telegram({"bot_id": "123", "chat_id": "42"})
    store.save(signal); store.begin_send(signal["id"]); store.receipt(signal["id"], 1)
    ledger = ExecutionLedger(cfg.execution_db, DEPOSIT)
    wire, chain = AckWire(), SessionChain()
    exchange = session_client(wire, chain=chain, clock=lambda: tick[0])
    market_source = session_client(Wire(context()), clock=lambda: tick[0])
    snapshot = market_source.market_snapshot(TOKEN, CONDITION)
    snapshot["min_order_size"] = snapshot["minimum_buy_notional"] = "1"
    exchange.market_snapshot = lambda token, condition: deepcopy(snapshot)
    account = dict(wallet=DEPOSIT, signer=SESSION, wallet_type="DEPOSIT_WALLET",
        signature_type=3, deposit_owner=OWNER, order_visibility="SESSION_SIGNER_ONLY",
        openings_allowed=True, opening_restrictions=[], balance=100_000_000,
        allowances={STANDARD_EXCHANGE: 100_000_000}, open_orders=[], trades=[],
        positions=[], wallet_activity=[], wallet_activity_session_trades=[],
        wallet_activity_after=1, wallet_activity_before=NOW)
    exchange.account_snapshot = lambda **kwargs: deepcopy(account)
    exchange.account_trades = lambda **kwargs: []
    exchange.confirmed_fills = lambda *a, **kw: []
    exchange.get_order = lambda *a, **kw: None
    controls = ControlStore(cfg) if with_control else None
    engine = ExecutionEngine(cfg, ledger, SignalReader(cfg.signal_db), exchange, Weather(signal))
    engine.review_controls = controls
    asyncio.run(engine.reconcile())
    assert engine.base_authority()
    return tick, cfg, signal, store, ledger, wire, chain, exchange, engine


def test_original_final_account_failure_now_closed_and_survives_restart(tmp_path):
    cfg, signal, store, ledger, exchange, engine = make_engine(tmp_path)
    cfg.activation_file.chmod(0o600)
    asyncio.run(engine.reconcile())
    before = exchange.account_snapshot
    exchange.account_snapshot = lambda **kw: dict(before(**kw), wallet_activity=[{
        "transaction_hash": "0x"+"12"*32, "condition": CONDITION, "token": TOKEN,
        "side": "SELL", "quantity": 1000000, "timestamp": int(time.time())}], wallet_activity_session_trades=[])
    with pytest.raises(ExecutionError, match="EXTERNAL_WALLET_TRADE_ACTIVITY"):
        asyncio.run(engine.execute(signal))
    assert exchange.posts == [] and ledger.orders(outstanding=False) == []
    assert ledger.state("fault") == "EXTERNAL_WALLET_TRADE_ACTIVITY"
    assert not asyncio.run(engine.execute(signal))
    restarted = ExecutionEngine(cfg, ledger, SignalReader(cfg.signal_db), exchange, Weather(signal))
    assert not restarted.authority()
    assert ledger.summary()["confirmed_fill_count"] == 0


@pytest.mark.parametrize("excess", ["quantity", "count"])
def test_same_transaction_excess_on_final_snapshot_is_sticky(tmp_path, excess):
    cfg, signal, store, ledger, exchange, engine = make_engine(tmp_path)
    cfg.activation_file.chmod(0o600)
    asyncio.run(engine.reconcile())
    original = exchange.account_snapshot
    identity = dict(transaction_hash="0x"+"14"*32, condition=CONDITION, token=TOKEN, side="BUY")
    activity = [dict(identity, quantity=2000000, timestamp=int(time.time()))] if excess == "quantity" else [dict(identity, quantity=500000, timestamp=int(time.time()))]*2
    exchange.account_snapshot = lambda **kw: dict(original(**kw), wallet_activity=activity,
        wallet_activity_session_trades=[dict(identity, wallet_quantity=1000000, status="CONFIRMED", matched_at=int(time.time()))])
    with pytest.raises(ExecutionError, match="EXTERNAL_WALLET_TRADE_ACTIVITY"):
        asyncio.run(engine.execute(signal))
    assert exchange.posts == [] and ledger.state("fault") == "EXTERNAL_WALLET_TRADE_ACTIVITY"


def test_real_prepare_and_submit_positive_control(tmp_path, monkeypatch):
    tick, cfg, signal, store, ledger, wire, chain, exchange, engine = real_adapter_engine(tmp_path, monkeypatch)
    assert asyncio.run(engine.execute(signal))
    assert len(wire.posts) == 1 and ledger.orders()[0]["status"] == "ACKNOWLEDGED"
    assert not asyncio.run(engine.execute(signal)) and len(wire.posts) == 1


def test_revocation_after_reconcile_before_prepare_releases_unsubmitted_reservation(tmp_path, monkeypatch):
    tick, cfg, signal, store, ledger, wire, chain, exchange, engine = real_adapter_engine(tmp_path, monkeypatch)
    chain.session_authorized_until = 0
    with pytest.raises(ExchangeError, match="SESSION_AUTHORIZATION_REVOKED_OR_MISSING"):
        asyncio.run(engine.execute(signal))
    assert wire.posts == [] and ledger.orders(outstanding=False) == []
    assert ledger.summary()["reserved_micros"] == 0


def test_revocation_after_actual_signing_is_caught_by_second_chain_read(tmp_path, monkeypatch):
    tick, cfg, signal, store, ledger, wire, chain, exchange, engine = real_adapter_engine(tmp_path, monkeypatch)
    original = exchange.prepare_buy
    def revoke_after_prepare(**kw):
        result = original(**kw)
        chain.session_authorized_until = 0
        return result
    exchange.prepare_buy = revoke_after_prepare
    with pytest.raises(ExecutionError, match="SESSION_AUTHORIZATION_REVOKED_OR_MISSING"):
        asyncio.run(engine.execute(signal))
    assert wire.posts == [] and ledger.orders(outstanding=False) == []
    assert ledger.summary()["reserved_micros"] == 0
    assert not engine.authority()


@pytest.mark.parametrize("where", ["rpc", "signing", "journal"])
def test_boundary_known_local_no_post_misclassified_unknown(tmp_path, monkeypatch, where):
    tick, cfg, signal, store, ledger, wire, chain, exchange, engine = real_adapter_engine(tmp_path, monkeypatch)
    if where == "rpc":
        original = exchange.session_opening_restriction
        def delayed_read():
            value = original(); tick[0] += 6; return value
        exchange.session_opening_restriction = delayed_read
    elif where == "signing":
        original = exchange._sign_order
        def delayed_sign(*args):
            value = original(*args); tick[0] += 6; return value
        exchange._sign_order = delayed_sign
    else:
        original = ledger.begin_submission
        def delayed_lock(*args):
            tick[0] += 6
            return original(*args)
        ledger.begin_submission = delayed_lock
    assert asyncio.run(engine.execute(signal))
    assert wire.posts == []
    # A known local rejection consumes the attempt and releases its reservation.
    assert ledger.orders(outstanding=False)[0]["status"] == "REJECTED"
    assert ledger.summary()["reserved_micros"] == 0
    assert engine.last_error == "PREPARED_ORDER_EXPIRED"
    assert not asyncio.run(engine.execute(signal))


@pytest.mark.parametrize("local_change", ["stop", "activation", "signal"])
def test_boundary_local_authority_can_change_while_submit_is_queued(tmp_path, monkeypatch, local_change):
    tick, cfg, signal, store, ledger, wire, chain, exchange, engine = real_adapter_engine(tmp_path, monkeypatch)
    original = engine.call
    at_post = {}
    wire.at_post = lambda: at_post.update(authority=engine.authority(), active=engine.reader.is_active(signal["id"]))
    async def queued_call(method, *args, **kw):
        if getattr(method, "__name__", None) == "submit":
            # Deterministic thread dispatch delay between the final engine
            # checks and the actual submit worker's transport invocation.
            tick[0] += 1
            if local_change == "stop": cfg.stop_file.touch()
            elif local_change == "activation": cfg.activation_file.unlink()
            else: store.terminal(signal["id"], "INVALIDATED", "review scheduler boundary")
        return await original(method, *args, **kw)
    engine.call = queued_call
    assert asyncio.run(engine.execute(signal))
    assert wire.posts == [] and at_post == {}
    assert ledger.summary()["reserved_micros"] == 0


def test_stop_during_final_chain_work_is_caught_before_arming(tmp_path, monkeypatch):
    tick, cfg, signal, store, ledger, wire, chain, exchange, engine = real_adapter_engine(tmp_path, monkeypatch)
    original = exchange.session_opening_restriction
    def delayed_stop():
        result = original(); cfg.stop_file.touch(); return result
    exchange.session_opening_restriction = delayed_stop
    assert asyncio.run(engine.execute(signal))
    assert wire.posts == [] and ledger.orders(outstanding=False) == []
    assert ledger.summary()["reserved_micros"] == 0


def test_boundary_real_thread_queue_permits_stop_before_post(tmp_path, monkeypatch):
    tick, cfg, signal, store, ledger, wire, chain, exchange, engine = real_adapter_engine(tmp_path, monkeypatch)
    submit_requested = threading.Event()
    at_post = {}
    wire.at_post = lambda: at_post.update(stopped=cfg.stop_file.exists(), authority=engine.authority())
    pool = ThreadPoolExecutor(max_workers=1)
    def occupies_worker_until_submission_is_queued():
        assert submit_requested.wait(timeout=2)
        cfg.stop_file.touch()
    original_check = exchange.session_opening_restriction
    def chain_check_then_queue_other_work():
        result = original_check()
        if not submit_requested.is_set():
            pool.submit(occupies_worker_until_submission_is_queued)
        return result
    exchange.session_opening_restriction = chain_check_then_queue_other_work
    original_call = engine.call
    async def notify_queued(method, *args, **kw):
        if getattr(method, "__name__", None) == "submit":
            submit_requested.set()
        return await original_call(method, *args, **kw)
    engine.call = notify_queued
    async def run():
        asyncio.get_running_loop().set_default_executor(pool)
        return await engine.execute(signal)
    assert asyncio.run(run())
    assert wire.posts == [] and at_post == {}
    assert ledger.summary()["reserved_micros"] == 0


@pytest.mark.parametrize("change", ["confirmation", "operator_authorization", "pause", "cancel"])
def test_boundary_actual_operator_authority_not_rechecked_inside_submit_worker(tmp_path, monkeypatch, change):
    values = real_adapter_engine(tmp_path, monkeypatch, with_control=True,
        control_expiry=NOW+0.5 if change == "operator_authorization" else None)
    tick, cfg, signal, store, ledger, wire, chain, exchange, engine = values
    op = (cfg, signal, engine.review_controls, ledger, exchange, engine.weather, engine)
    activate(op, "CONFIRM" if change == "confirmation" else "AUTOMATIC")
    if change == "confirmation":
        controls = engine.review_controls
        revision = engine.control.settings["revision"]
        action = controls.action(42, "CONFIRM_TRADE", {"signal_id": signal["id"],
            "signal_hash": digest(signal)}, revision, expires=NOW+0.5)
        controls.bind_message([action], 7)
        controls.click(action, actor=42, message_id=7, update_id=3, revision=revision)
        engine.control.process()
    at_post = {}
    wire.at_post = lambda: at_post.update(authority=engine.authority(), reason=engine.control.reason(),
        confirmation_deadline=engine._attempt_deadline, now=tick[0])
    original = engine.call
    async def queued(method, *args, **kw):
        if getattr(method, "__name__", None) == "submit":
            tick[0] += 1
            if change in ("pause", "cancel"):
                request(op, change.upper(), process=False)
        return await original(method, *args, **kw)
    engine.call = queued
    assert asyncio.run(engine.execute(signal))
    assert wire.posts == [] and at_post == {}
    assert ledger.summary()["reserved_micros"] == 0


def test_multileg_revocation_stops_second_and_preserves_first_reservation(tmp_path):
    cfg, signal, store, ledger, exchange, engine = make_engine(tmp_path)
    cfg.activation_file.chmod(0o600)
    signal["strategy"] = "STRUCTURAL"
    signal["legs"].append(dict(signal["legs"][0], token="456", condition="condition2"))
    asyncio.run(engine.reconcile())
    check_count = [0]
    def revoke_second():
        check_count[0] += 1
        return "SESSION_AUTHORIZATION_REVOKED_OR_MISSING" if check_count[0] == 2 else None
    exchange.session_opening_restriction = revoke_second
    with pytest.raises(ExecutionError, match="SESSION_AUTHORIZATION_REVOKED_OR_MISSING"):
        asyncio.run(engine.execute(signal))
    assert exchange.posts == ["o123"]
    assert len(ledger.orders()) == 1
    assert ledger.summary()["reserved_micros"] == ledger.orders()[0]["reserved"] > 0
    assert ledger.summary()["confirmed_fill_count"] == 0
    assert not engine.authority()


def test_known_no_post_under_actual_sqlite_writer_contention(tmp_path, monkeypatch):
    tick, cfg, signal, store, ledger, wire, chain, exchange, engine = real_adapter_engine(tmp_path, monkeypatch)
    acquired, entering_submission = threading.Event(), threading.Event()
    holder_errors = []
    def hold_writer():
        try:
            db = sqlite3.connect(ledger.path, timeout=5, isolation_level=None)
            db.execute("BEGIN IMMEDIATE")
            acquired.set()
            assert entering_submission.wait(timeout=2)
            # Let the production BEGIN IMMEDIATE reach the competing lock.
            threading.Event().wait(0.05)
            tick[0] += 6
            db.execute("COMMIT")
            db.close()
        except Exception as exc:
            holder_errors.append(type(exc).__name__)
    holder = threading.Thread(target=hold_writer)
    original_check = exchange.session_opening_restriction
    def chain_check_then_hold_journal():
        result = original_check()
        if holder.ident is None:
            holder.start()
            assert acquired.wait(timeout=2)
        return result
    exchange.session_opening_restriction = chain_check_then_hold_journal
    original_begin = ledger.begin_submission
    def notify_begin(*args, **kw):
        entering_submission.set()
        return original_begin(*args, **kw)
    ledger.begin_submission = notify_begin
    assert asyncio.run(engine.execute(signal))
    holder.join(timeout=2)
    assert not holder.is_alive() and not holder_errors
    assert wire.posts == [] and ledger.orders(outstanding=False)[0]["status"] == "REJECTED"
    assert ledger.summary()["reserved_micros"] == 0
