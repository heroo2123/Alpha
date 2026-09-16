from __future__ import annotations

import asyncio
from copy import deepcopy
from decimal import Decimal
import json
from pathlib import Path
import sqlite3
import time

import pytest

from polymarket_scanner.production.config import ConfigurationError, ProductionConfig
from polymarket_scanner.production.engine import ExecutionEngine, ExecutionError
from polymarket_scanner.production.io import lease
from polymarket_scanner.production.ledger import ExecutionLedger, LedgerError, micros
from polymarket_scanner.production.signals import SignalStore, SignalReader
from polymarket_scanner.production.service import SignalService

WALLET = "0x" + "12" * 20


def config(tmp_path, mode="LIVE_EXECUTION", **overrides):
    raw = {"mode": mode, "signal_db": str(tmp_path / "signals" / "signals.db"), "status_path": str(tmp_path / "signals" / "signals.json"),
      "execution_status_path": str(tmp_path / "execution" / "execution.json"), "execution_db": str(tmp_path / "execution" / "execution.db"),
      "credentials_file": str(tmp_path / "credentials.json"), "activation_file": str(tmp_path / "activation.json"), "stop_file": str(tmp_path / "stop"),
      "wallet": WALLET, "signer": WALLET, "fee_policy": "ONCHAIN_BOUND", "strategies": ["DIRECTIONAL", "MAKER", "STRUCTURAL"], "allow_uncalibrated": True,
      "partial_basket_policy": "SEQUENTIAL_FAK_FULL_RESERVATION_STOP_ON_KNOWN_FAILURE", "rpc_url": "https://polygon.example.invalid",
      "min_model_gap": "0.05", "min_structural_edge": "0.02", "risk": {"capital": "100", "per_order": "5", "per_station_day": "20", "max_loss": "100", "daily_loss": "100", "max_price": "0.95", "max_slippage": "0.02", "max_fee_per_share": "0.05", "legging_loss": "10", "max_open_orders": 10, "max_positions": 20, "max_maker_rest_seconds": 240}}
    raw.update(overrides)
    return ProductionConfig.parse(raw)


def candidate(strategy="DIRECTIONAL"):
    now = time.time()
    return {"id": "sig-1", "key": "episode-1", "strategy": strategy, "title": "Temperature test", "event_id": "event1", "event_url": "https://polymarket.com/event/test", "station_day": "KSEA/2026-09-17", "semantic_hash": "semantic1", "evidence_hash": "evidence1", "evidence": {}, "created": now, "expires": now + 120,
            "uncalibrated": True, "model_frequency": "0.8", "theoretical_payout": "1",
            "legs": [{"token": "123", "condition": "condition1", "market_id": "m1", "side": "YES", "price": "0.4", "fee": "0.004", "available": "100", "min_size": "1", "tick": "0.01", "neg_risk": False, "outcome_index": 0}]}


class Weather:
    def __init__(self, value):
        self.value = value
        self.calls = 0
        self.error = None

    async def revalidate(self, signal):
        self.calls += 1
        if self.error:
            raise self.error
        return deepcopy(self.value)


class Exchange:
    def __init__(self):
        self.posts = []
        self.cancels = []
        self.remote = {}
        self.fills = {}
        self.balances = {}
        self.balance = 100_000_000
        self.wallet = WALLET
        self.outcome = "ACKNOWLEDGED"
        self.ask = "0.4"
        self.ask_size = "100"
        self.fee_bps = "100"
        self.fee_policy = "ONCHAIN_BOUND"
        self.fee_details = {"r": ".05", "e": "1", "to": True}
        self.on_submit = None
        self.resolution = None

    def account_snapshot(self, **kwargs):
        return {"wallet": self.wallet, "signer": WALLET, "openings_allowed": True, "balance": self.balance, "allowances": {"exchange1": 100_000_000}, "open_orders": [x for x in self.remote.values() if x["status"] == "LIVE"], "positions": [{"token": t, "quantity": q} for t, q in self.balances.items()]}

    def account_trades(self, **kwargs):
        return []

    def market_snapshot(self, token, condition):
        from polymarket_scanner.production.fees import make_fee_evidence
        observed = time.time()
        evidence = make_fee_evidence(self.fee_policy, token=token, condition=condition,
            exchange="exchange1", observed_at=observed, fd=self.fee_details,
            max_fee_bps=int(self.fee_bps), max_fee_block={"number": 1, "hash": "0x" + "11" * 32},
            maker_base_fee_bps=0, taker_base_fee_bps=0)
        return {"tick_size": "0.01", "min_order_size": "1", "received_at": observed,
                "token": token, "condition": condition, "fee_policy": self.fee_policy, "fee_evidence": evidence,
                "neg_risk": False, "exchange": "exchange1", "max_fee_bps": int(self.fee_bps),
                "book": {"ask": self.ask, "ask_size": self.ask_size, "bid": "0.39", "bid_size": "100"}}

    def prepare_buy(self, **kwargs):
        self.prepared = kwargs
        evidence = self.market_snapshot(kwargs["token"], kwargs["condition"])["fee_evidence"]
        return {"order_id": "o" + kwargs["token"], "wire_hash": "wire-hash", "fee_evidence": evidence,
                "payload": {"order": {"expiration": str(kwargs["expiration"]), "signature": "isolated-fixture-not-live"}}}

    def submit(self, prepared):
        self.posts.append(prepared["order_id"])
        kw = self.prepared
        if self.outcome != "REJECTED":
            self.remote[prepared["order_id"]] = {"id": prepared["order_id"], "token": kw["token"], "quantity": micros(kw["quantity"]), "matched": 0, "status": "LIVE", "condition": kw["condition"], "price": str(kw["price"]), "side": "BUY", "expires": kw["expiration"]}
        if self.on_submit:
            self.on_submit()
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def get_order(self, oid):
        return self.remote.get(oid)

    def confirmed_fills(self, oid, token, neg_risk, *, trades=None, recorded_fills=None):
        return self.fills.get(oid, [])

    def token_balance(self, token):
        return self.balances.get(token, 0)

    def cancel_order(self, oid):
        self.cancels.append(oid)
        return {"canceled": [oid]}

    def settlement(self, condition, index, **kwargs):
        return self.resolution

    def fill(self, fraction=Decimal("1"), *, cancelled=False, fee_breach=False):
        oid = self.posts[-1]
        remote = self.remote[oid]
        quantity = int(Decimal(remote["quantity"]) * fraction)
        self.fills[oid] = [{"id": "f1", "order_id": oid, "token": remote["token"], "quantity": quantity, "cost": int(Decimal(quantity) * Decimal("0.4")), "fee": int(Decimal(quantity) * Decimal("0.1" if fee_breach else "0.004")), "transaction_hash": "tx1", "block_hash": "block1", "log_index": 0}]
        remote.update(matched=quantity, status="CANCELLED" if cancelled else "MATCHED" if fraction == 1 else "LIVE")
        self.balances[remote["token"]] = quantity


@pytest.fixture
def harness(tmp_path):
    cfg = config(tmp_path)
    cfg.activation_file.write_text(json.dumps({"action": "ACTIVATE_LIVE_EXECUTION", "wallet": WALLET, "config_sha256": cfg.config_sha256}))
    signal = candidate()
    store = SignalStore(cfg.signal_db)
    store.bind_telegram({"bot_id": "123", "chat_id": "42"})
    store.save(signal)
    store.begin_send(signal["id"])
    store.receipt(signal["id"], 1)
    ledger = ExecutionLedger(cfg.execution_db, WALLET)
    exchange = Exchange()
    weather = Weather(signal)
    engine = ExecutionEngine(cfg, ledger, SignalReader(cfg.signal_db), exchange, weather)
    asyncio.run(engine.reconcile())
    return cfg, signal, store, ledger, exchange, weather, engine


def test_manual_signal_never_opens_actual_position(harness, tmp_path):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    manual = config(tmp_path / "manual", "LIVE_SIGNALS")
    assert not manual.activation_requested()
    assert store.summary()["actual_trades_inferred"] == 0
    assert ledger.summary()["confirmed_fill_count"] == 0
    with pytest.raises(ConfigurationError, match="EXECUTION_MODE_REQUIRED"):
        ExecutionEngine(manual, ledger, SignalReader(cfg.signal_db), exchange, weather)


@pytest.mark.parametrize("outcome", ["UNKNOWN", TimeoutError(), ConnectionError()])
def test_accepted_response_lost_never_resubmits(harness, outcome):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    exchange.outcome = outcome
    assert asyncio.run(engine.execute(signal))
    assert ledger.orders()[0]["status"] == "UNKNOWN"
    ledger.recover_after_restart()
    asyncio.run(engine.reconcile())
    asyncio.run(engine.execute(signal))
    assert len(exchange.posts) == 1
    assert ledger.summary()["confirmed_fill_count"] == 0


def test_timeout_before_acceptance_keeps_unknown_reserved(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    exchange.outcome = TimeoutError()
    exchange.on_submit = exchange.remote.clear
    asyncio.run(engine.execute(signal))
    asyncio.run(engine.reconcile())
    assert not engine.authority()
    assert ledger.orders()[0]["status"] == "UNKNOWN"
    assert ledger.summary()["reserved_micros"] > 0
    assert len(exchange.posts) == 1


def test_explicit_rejection_releases_without_fill(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    exchange.outcome = "REJECTED"
    asyncio.run(engine.execute(signal))
    assert ledger.summary()["reserved_micros"] == 0
    assert ledger.summary()["confirmed_fill_count"] == 0
    assert ledger.orders(outstanding=False)[0]["status"] == "REJECTED"


def test_partial_cancel_race_preserves_committed_fill(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))
    store.terminal(signal["id"], "INVALIDATED", "weather revision")
    asyncio.run(engine.manage_existing())
    assert ledger.orders()[0]["status"] == "CANCEL_REQUESTED"
    assert ledger.summary()["reserved_micros"] > 0
    exchange.fill(Decimal("0.5"), cancelled=True)
    asyncio.run(engine.reconcile())
    assert ledger.summary()["confirmed_fill_count"] == 1
    assert ledger.summary()["reserved_micros"] == 0
    assert ledger.orders(outstanding=False)[0]["status"] == "CANCELLED"
    assert ledger.positions()[0]["quantity"] > 0


def test_signal_expires_inflight_does_not_erase_fill(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    exchange.on_submit = lambda: store.terminal(signal["id"], "EXPIRED", "during POST")
    asyncio.run(engine.execute(signal))
    exchange.fill()
    asyncio.run(engine.reconcile())
    assert ledger.summary()["confirmed_fill_count"] == 1
    assert store.recent()[0]["status"] == "EXPIRED"


def test_actual_fee_breach_recorded_before_fault_and_settlement(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))
    exchange.fill(fee_breach=True)
    exchange.resolution = {"payout": "1", "proof": {"finalized_block": "b1"}}
    asyncio.run(engine.reconcile())
    result = ledger.summary()
    assert result["confirmed_fill_count"] == 1
    assert result["actual_fees_micros"] > 0
    assert result["settled_position_count"] == 1
    assert result["verified_redemption_proceeds_by_asset_micros"] == {}
    assert ledger.state("fault") == "ACTUAL_FEE_LIMIT_BREACH"
    assert not engine.authority()


@pytest.mark.parametrize("change", ["balance", "price", "fee", "liquidity", "weather", "semantics", "account", "kill"])
def test_changes_block_submission(harness, change):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    if change == "balance": exchange.balance = 0
    if change == "price": exchange.ask = "0.5"
    if change == "fee": exchange.fee_bps = "5000"
    if change == "liquidity": exchange.ask_size = "0.5"
    if change == "weather": weather.error = RuntimeError("STALE_WEATHER")
    if change == "semantics": weather.value = dict(signal, semantic_hash="changed")
    if change == "account": exchange.wallet = "0x" + "13" * 20
    if change == "kill": cfg.stop_file.touch()
    try:
        asyncio.run(engine.execute(signal))
    except (ExecutionError, LedgerError, RuntimeError):
        pass
    assert not exchange.posts


@pytest.mark.parametrize("boundary", ["CAPITAL_RESERVED", "SUBMISSION_ARMED", "SUBMISSION_ACKNOWLEDGED", "CONFIRMED_FILL", "ORDER_TERMINAL"])
def test_sqlite_failure_boundaries_are_atomic(harness, boundary):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    with ledger.connect() as db:
        db.execute("CREATE TRIGGER fail_boundary BEFORE INSERT ON execution_audit WHEN NEW.kind='" + boundary + "' BEGIN SELECT RAISE(ABORT,'injected write failure'); END")
    if boundary in {"CONFIRMED_FILL", "ORDER_TERMINAL"}:
        asyncio.run(engine.execute(signal))
        # A full fill is already terminal; exercise a real partial/cancel
        # transition rather than expecting a duplicate no-op audit event.
        exchange.fill(fraction=Decimal("0.5") if boundary == "ORDER_TERMINAL" else Decimal("1"), cancelled=boundary == "ORDER_TERMINAL")
        with pytest.raises(sqlite3.Error):
            asyncio.run(engine.reconcile())
        if boundary == "CONFIRMED_FILL":
            assert ledger.summary()["confirmed_fill_count"] == 0
        else:
            assert ledger.summary()["confirmed_fill_count"] == 1
    else:
        with pytest.raises(sqlite3.Error):
            asyncio.run(engine.execute(signal))
        assert len(exchange.posts) == (1 if boundary == "SUBMISSION_ACKNOWLEDGED" else 0)
        if exchange.posts:
            ledger.recover_after_restart()
            assert ledger.orders()[0]["status"] == "UNKNOWN"


def test_duplicate_signal_and_worker_lease(harness, tmp_path):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))
    asyncio.run(engine.execute(signal))
    assert len(exchange.posts) == 1
    path = tmp_path / "worker.lock"
    with lease(path):
        with pytest.raises(ConfigurationError, match="WORKER_ALREADY_RUNNING"):
            with lease(path):
                pytest.fail("second writer acquired")


def test_terminal_state_audit_and_sync_commit_together(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    with store.connect() as db:
        db.execute("CREATE TRIGGER no_sync BEFORE INSERT ON live_sync BEGIN SELECT RAISE(ABORT,'outbox write failed'); END")
    with pytest.raises(sqlite3.Error):
        store.terminal(signal["id"], "INVALIDATED", "revision")
    assert store.recent()[0]["status"] == "ACTIVE"
    with store.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM live_audit WHERE kind='SIGNAL_TERMINAL'").fetchone()[0] == 0


def test_late_telegram_receipt_enters_terminal_sync(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    new = dict(candidate(), id="late", key="late-key")
    store.save(new)
    store.begin_send("late")
    store.terminal("late", "INVALIDATED", "weather revised while send pending")
    store.receipt("late", 201)
    assert store.pending_sync("late")[0]["message_id"] == 201
    assert ledger.summary()["confirmed_fill_count"] == 0


def test_restart_does_not_resend_unknown_telegram(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    new = dict(candidate(), id="lost", key="lost-key")
    store.save(new)
    assert store.begin_send("lost")
    store.recover()
    assert not store.begin_send("lost")
    assert store.summary()["unknown_deliveries"] == 1


def test_stop_command_is_idempotent_and_does_not_claim_cancellation(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))
    assert store.command(1, "operator", "/stop")
    assert not store.command(1, "operator", "/stop")
    assert not engine.authority()
    asyncio.run(engine.manage_existing())
    assert not exchange.cancels
    assert ledger.orders()[0]["status"] == "ACKNOWLEDGED"
