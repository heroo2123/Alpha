"""Independent frozen-candidate adversarial tests; no external service calls.

Target: 51d66c9cc42ad9efb23ccd41a6a1ed3972dbe873.
Uses only disposable SQLite journals and mocked exchange/HTTP/RPC transports.
"""
import asyncio
from copy import deepcopy
from decimal import Decimal, ROUND_CEILING, ROUND_HALF_UP
import json
import sqlite3
import time

import pytest

from polymarket_scanner.production.chain import ExchangeError
from polymarket_scanner.production.exchange import PublicMarketReader
from polymarket_scanner.production.fees import EXCHANGE_PUBLISHED_SCHEDULE, fee_requirement
from polymarket_scanner.production.ledger import micros
from test_production_exchange import Chain, Wire, context, TOKEN, CONDITION, NOW
from test_production_fee_policy import use_schedule
from test_production_lifecycle import harness


@pytest.mark.parametrize("exponent", range(5))
def test_fragment_rounding_at_all_buy_prices_stays_within_schedule_envelope(exponent):
    quantum = Decimal(".00001")
    source = context()
    source[1][1]["fd"] = {"r": ".05", "e": str(exponent), "to": True}
    chain = Chain()
    chain.fee = 0
    snapshot = PublicMarketReader(fee_policy=EXCHANGE_PUBLISHED_SCHEDULE,
        transport=Wire(source), chain=chain, clock=lambda: NOW).market_snapshot(TOKEN, CONDITION)
    for limit in map(Decimal, (".01", ".1", ".4", ".5", ".8", ".99")):
        allowance = fee_requirement(snapshot, limit, False)
        for index in range(1, 100):
            price = Decimal(index) / 100
            if price > limit:
                continue
            rate = Decimal(".05") * (price * (1-price)) ** exponent
            for shares in map(Decimal, (".000001", ".000049", ".000099", ".000401", ".001", ".01", "1", "7.37")):
                raw = shares * rate
                # Check both documented subminimum-zero plus upward adjacent
                # rounding, and ordinary nearest rounding at the half quantum.
                upper = Decimal(0) if raw < quantum else raw.quantize(quantum, rounding=ROUND_CEILING)
                nearest = raw.quantize(quantum, rounding=ROUND_HALF_UP)
                assert upper <= shares * allowance
                assert nearest <= shares * allowance


def test_slow_public_fee_read_cannot_relabel_old_evidence_fresh():
    clock = [float(NOW)]
    chain = Chain()
    chain.fee = 0
    original = chain.call_uint

    def delayed(*args, **kwargs):
        clock[0] += 16
        return original(*args, **kwargs)

    chain.call_uint = delayed
    with pytest.raises(ExchangeError, match="STALE"):
        PublicMarketReader(fee_policy=EXCHANGE_PUBLISHED_SCHEDULE,
            transport=Wire(context()), chain=chain, clock=lambda: clock[0]).market_snapshot(TOKEN, CONDITION)


def test_full_first_basket_fill_fee_breach_cancels_other_intent_and_stops_next_leg(harness, tmp_path):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    use_schedule(harness, tmp_path)
    assert asyncio.run(engine.execute(signal))
    original_order = exchange.posts[0]
    basket = deepcopy(signal)
    basket.update(id="independent-basket", key="independent-basket", strategy="STRUCTURAL")
    basket["legs"] = [dict(signal["legs"][0], token="124", condition="condition2"),
                      dict(signal["legs"][0], token="125", condition="condition3")]
    weather.value = basket
    assert store.save(basket)
    assert store.begin_send(basket["id"])
    store.receipt(basket["id"], 2)
    exchange.on_submit = lambda: exchange.fill(fee_breach=True)
    assert asyncio.run(engine.execute(basket))
    assert exchange.posts == [original_order, "o124"]
    assert exchange.cancels == [original_order]
    assert ledger.order("o124")["status"] == "FILLED"
    assert ledger.order("o125") is None
    assert ledger.order(original_order)["status"] == "CANCEL_REQUESTED"
    assert ledger.summary()["reserved_micros"] == ledger.order(original_order)["reserved"]
    assert ledger.summary()["confirmed_fill_count"] == 1
    assert ledger.state("fault") == "ACTUAL_FEE_LIMIT_BREACH"
    assert not engine.authority()


def test_fee_evidence_audit_failure_prevents_financial_post(harness, tmp_path):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    use_schedule(harness, tmp_path)
    with ledger.connect() as db:
        db.execute("CREATE TRIGGER reject_fee_audit BEFORE INSERT ON execution_audit WHEN NEW.kind='SUBMISSION_ARMED' BEGIN SELECT RAISE(ABORT,'independent fee audit fault'); END")
    with pytest.raises(sqlite3.Error):
        asyncio.run(engine.execute(signal))
    assert exchange.posts == []
    assert ledger.orders(outstanding=False) == []
    assert ledger.summary()["reserved_micros"] == 0
    assert engine.io_fault and not engine.authority()


def test_breach_cancel_failure_survives_restart_and_blocks_recovery_until_terminal(harness, tmp_path, monkeypatch):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    use_schedule(harness, tmp_path)
    clock = [time.time()]
    monkeypatch.setattr(time, "time", lambda: clock[0])
    assert asyncio.run(engine.execute(signal))
    order_id = exchange.posts[0]
    attempted = []

    def uncertain_cancel(oid):
        attempted.append(oid)
        raise ExchangeError("MOCK_CANCEL_NETWORK_FAILURE")

    exchange.cancel_order = uncertain_cancel
    exchange.fill(Decimal(".5"), fee_breach=True)
    asyncio.run(engine.reconcile())
    assert attempted == [order_id]
    assert ledger.order(order_id)["status"] == "CANCEL_REQUESTED"
    reserved = ledger.order(order_id)["reserved"]
    assert reserved > 0
    ledger.recover_after_restart()
    asyncio.run(engine.reconcile(ignore_sticky_fault=True))
    assert not engine.reconciled
    clock[0] += 16
    asyncio.run(engine.manage_existing())
    assert attempted == [order_id, order_id]
    assert ledger.order(order_id)["reserved"] == reserved
    exchange.remote[order_id]["status"] = "CANCELLED"
    asyncio.run(engine.reconcile(ignore_sticky_fault=True))
    assert engine.reconciled
    assert ledger.summary()["confirmed_fill_count"] == 1
    assert ledger.summary()["reserved_micros"] == 0
    assert ledger.state("fault") == "ACTUAL_FEE_LIMIT_BREACH"
    assert not engine.authority()
    assert exchange.posts == [order_id]


def test_partial_fill_release_preserves_full_remaining_fee_allowance(harness, tmp_path):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    cfg = use_schedule(harness, tmp_path)
    assert asyncio.run(engine.execute(signal))
    oid = exchange.posts[0]
    initial = ledger.order(oid)["reserved"]
    exchange.fill(Decimal(".5"))
    asyncio.run(engine.reconcile())
    order = ledger.order(oid)
    remaining = Decimal(order["quantity"]-order["matched"]) / 1_000_000
    assert order["reserved"] == micros(remaining * (Decimal(order["limit_price"])+cfg.risk.max_fee_per_share))
    totals = ledger.summary()
    assert totals["actual_cost_micros"] + totals["actual_fees_micros"] + totals["reserved_micros"] <= initial
    assert ledger.state("fault") is None
    with ledger.connect() as db:
        first = json.loads(db.execute("SELECT data FROM execution_audit WHERE kind='SUBMISSION_ARMED'").fetchone()[0])["fee_evidence"]
    exchange.fee_details["r"] = ".99"
    with ledger.connect() as db:
        retained = json.loads(db.execute("SELECT data FROM execution_audit WHERE kind='SUBMISSION_ARMED'").fetchone()[0])["fee_evidence"]
    assert retained == first and retained["fd"]["r"] == ".05"


def test_real_adapter_unknown_post_then_confirmed_breach_queues_and_attempts_cancel(tmp_path, monkeypatch):
    from dataclasses import replace
    from polymarket_scanner.production.chain import decode_fills
    from polymarket_scanner.production.engine import ExecutionEngine
    from polymarket_scanner.production.ledger import ExecutionLedger
    from polymarket_scanner.production.signals import SignalStore, SignalReader
    from test_production_lifecycle import config, candidate, Weather
    from test_production_transport_integration import ContractChain, ContractWire
    from test_production_exchange import (client, WALLET, CLOB, raw_order,
        fill_log, receipt, STANDARD_EXCHANGE)

    class PartialChain(ContractChain):
        fee = 0

        def token_balance(self, wallet, token):
            return int(self.order["takerAmount"]) // 2 if self.order else 0

        def fills(self, transaction, *, order_id, wallet, token, exchange):
            q = int(self.order["takerAmount"]) // 2
            log = fill_log(quantity=q, cost=int(self.order["makerAmount"]) // 2,
                           fee=int(Decimal(q) * Decimal(".051")))
            log["topics"][1] = order_id
            return decode_fills(receipt([log]), order_id=order_id, wallet=wallet,
                                token=token, exchange=exchange)

    class PartialWire(ContractWire):
        def request(self, method, url, **kwargs):
            if method == "DELETE":
                self.calls.append((method, url, kwargs))
                assert url == CLOB + "/order"
                assert json.loads(kwargs["body"])["orderID"] == self.order_id
                return 200, {"canceled": [self.order_id], "not_canceled": {}}
            status, value = super().request(method, url, **kwargs)
            if url.startswith(CLOB + "/data/order/") and self.chain.order:
                value = raw_order(id=self.order_id, status="LIVE",
                    original_size=str(Decimal(self.chain.order["takerAmount"])/1_000_000),
                    size_matched=str(Decimal(int(self.chain.order["takerAmount"])//2)/1_000_000))
            return status, value

    monkeypatch.setattr(time, "time", lambda: NOW)
    cfg = replace(config(tmp_path, fee_policy=EXCHANGE_PUBLISHED_SCHEDULE), wallet=WALLET, signer=WALLET)
    cfg = replace(cfg, risk=replace(cfg.risk, per_order=Decimal("6")))
    cfg.activation_file.write_text(json.dumps({"action": "ACTIVATE_LIVE_EXECUTION",
        "wallet": WALLET, "config_sha256": cfg.config_sha256}))
    signal = candidate()
    signal["legs"][0].update(token=TOKEN, condition=CONDITION)
    store = SignalStore(cfg.signal_db)
    try:
        assert store.save(signal) and store.begin_send(signal["id"])
        store.receipt(signal["id"], 1)
        ledger = ExecutionLedger(cfg.execution_db, WALLET)
        chain = PartialChain()
        wire = PartialWire(chain, False)
        adapter = client(wire, chain, fee_policy=EXCHANGE_PUBLISHED_SCHEDULE)
        engine = ExecutionEngine(cfg, ledger, SignalReader(cfg.signal_db), adapter, Weather(signal))
        asyncio.run(engine.reconcile())
        assert asyncio.run(engine.execute(signal))
        assert ledger.orders()[0]["status"] == "UNKNOWN"
        ledger.recover_after_restart()
        asyncio.run(engine.reconcile())
        order = ledger.order(wire.order_id)
        assert order["status"] == "CANCEL_REQUESTED" and order["reserved"] > 0
        assert ledger.state("fault") == "ACTUAL_FEE_LIMIT_BREACH"
        assert ledger.summary()["confirmed_fill_count"] == 1
        assert len([call for call in wire.calls if call[0] == "POST"]) == 1
        assert len([call for call in wire.calls if call[0] == "DELETE"]) == 1
        assert not engine.authority()
        assert not asyncio.run(engine.execute(signal))
    finally:
        store.close()
