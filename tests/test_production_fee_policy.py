"""Explicit fee authority and durable response to actual venue fee breaches."""
import asyncio
from copy import deepcopy
from dataclasses import asdict
from decimal import Decimal
import json
import sqlite3

import pytest

from test_production_lifecycle import config, harness
from polymarket_scanner.production.config import ConfigurationError
from polymarket_scanner.production.engine import ExecutionError
from polymarket_scanner.production.chain import ExchangeError
from polymarket_scanner.production.ledger import micros


def test_live_execution_requires_explicit_fee_policy_but_manual_signals_do_not(tmp_path):
    with pytest.raises(ConfigurationError, match="MISSING_SETTING:fee_policy"):
        config(tmp_path, fee_policy=None)
    assert config(tmp_path, mode="LIVE_SIGNALS", fee_policy=None).fee_policy is None


@pytest.mark.parametrize("policy", [False, [], "PUBLISHED", "AUTO"])
def test_invalid_fee_policy_has_precise_configuration_error(tmp_path, policy):
    with pytest.raises(ConfigurationError, match="INVALID_FEE_POLICY"):
        config(tmp_path, fee_policy=policy)


def test_fee_policy_change_requires_new_config_bound_activation(harness, tmp_path):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    changed = config(tmp_path, fee_policy="EXCHANGE_PUBLISHED_SCHEDULE")
    assert cfg.activation_requested()
    assert cfg.config_sha256 != changed.config_sha256
    assert not changed.activation_requested()


def test_actual_fee_breach_cancels_remaining_order_without_erasing_fill(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    assert asyncio.run(engine.execute(signal))
    exchange.fill(Decimal("0.5"), fee_breach=True)
    asyncio.run(engine.tick())
    order = ledger.orders()[0]
    assert ledger.state("fault") == "ACTUAL_FEE_LIMIT_BREACH"
    assert ledger.summary()["confirmed_fill_count"] == 1
    assert order["matched"] > 0
    assert order["reserved"] > 0
    assert order["status"] == "CANCEL_REQUESTED"
    assert exchange.cancels == [order["id"]]
    assert not engine.authority()


def test_fee_breach_queues_every_managed_remainder_across_restart(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    assert asyncio.run(engine.execute(signal))
    other = deepcopy(signal)
    other.update(id="sig-2", key="episode-2")
    other["legs"][0]["token"] = "124"
    weather.value = other
    store.save(other)
    store.begin_send(other["id"])
    store.receipt(other["id"], 2)
    assert asyncio.run(engine.execute(other))
    exchange.fill(Decimal("0.5"), fee_breach=True)
    fill = exchange.fills[exchange.posts[-1]][0]
    ledger.record_fill(fill)  # Crash immediately after committing the actual fill.
    assert all(order["status"] == "CANCEL_REQUESTED" for order in ledger.orders())
    ledger.recover_after_restart()
    asyncio.run(engine.manage_existing())
    assert set(exchange.cancels) == set(exchange.posts)
    assert ledger.summary()["confirmed_fill_count"] == 1
    assert all(order["reserved"] > 0 for order in ledger.orders())


def test_fee_breach_and_cancellation_queue_are_one_atomic_write(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    assert asyncio.run(engine.execute(signal))
    exchange.fill(Decimal("0.5"), fee_breach=True)
    with ledger.connect() as db:
        db.execute("CREATE TRIGGER fail_cancel_queue BEFORE INSERT ON execution_audit WHEN NEW.kind='ACTUAL_LIMIT_BREACH_CANCEL_QUEUED' BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.Error):
        asyncio.run(engine.reconcile())
    assert ledger.summary()["confirmed_fill_count"] == 0
    assert ledger.state("fault") is None
    assert ledger.orders()[0]["status"] == "ACKNOWLEDGED"
    assert exchange.cancels == []
    with ledger.connect() as db:
        db.execute("DROP TRIGGER fail_cancel_queue")
    asyncio.run(engine.reconcile())
    assert ledger.summary()["confirmed_fill_count"] == 1
    assert ledger.orders()[0]["status"] == "CANCEL_REQUESTED"
    assert exchange.cancels == exchange.posts


def use_schedule(harness, tmp_path, *, cap=".05"):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    risk = {key: str(value) if isinstance(value, Decimal) else value for key, value in asdict(cfg.risk).items()}
    risk["max_fee_per_share"] = cap
    changed = config(tmp_path, fee_policy="EXCHANGE_PUBLISHED_SCHEDULE", risk=risk)
    changed.activation_file.write_text(json.dumps({"action": "ACTIVATE_LIVE_EXECUTION", "wallet": changed.wallet, "config_sha256": changed.config_sha256}))
    engine.config = changed
    exchange.fee_policy = changed.fee_policy
    exchange.fee_bps = "0"  # Real current protocol setting: unbounded, not free.
    return changed


@pytest.mark.parametrize("maker", [False, True])
def test_explicit_schedule_reserves_full_operator_allowance_and_binds_proof(harness, tmp_path, maker):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    cfg = use_schedule(harness, tmp_path)
    if maker:
        signal = deepcopy(signal)
        signal.update(id="maker-fees", key="maker-fees", strategy="MAKER")
        exchange.ask = "0.41"
        weather.value = signal
        store.save(signal)
        store.begin_send(signal["id"])
        store.receipt(signal["id"], 2)
    assert engine.authority()
    assert asyncio.run(engine.execute(signal))
    order = ledger.orders()[0]
    plan = ledger.plan(order["intent_id"])
    leg = plan["legs"][0]
    assert Decimal(leg["fee_cap"]) == cfg.risk.max_fee_per_share
    assert Decimal(leg["observed_fee_requirement"]) == Decimal("0" if maker else ".024")
    assert order["reserved"] == micros(Decimal(leg["quantity"]) * (Decimal(leg["price"]) + cfg.risk.max_fee_per_share))
    assert leg["fee_evidence"]["max_fee_bps"] == 0
    assert leg["fee_evidence"]["fd"] == exchange.fee_details
    with ledger.connect() as db:
        audit = json.loads(db.execute("SELECT data FROM execution_audit WHERE kind='SUBMISSION_ARMED'").fetchone()[0])
    assert audit["fee_evidence"]["policy"] == cfg.fee_policy
    assert audit["fee_evidence"]["observed_at"] >= leg["fee_evidence"]["observed_at"]
    assert ledger.summary()["confirmed_fill_count"] == 0
    assert engine.status()["fee_limit_scope"] == "LOCAL_SUBMISSION_CHECK_AND_RESERVATION_NOT_SIGNED_EXCHANGE_CAP"


def test_schedule_checks_peak_fee_at_better_buy_fill_price(harness, tmp_path):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    use_schedule(harness, tmp_path, cap=".04")
    exchange.fee_details["r"] = ".1"
    exchange.ask = ".8"
    leg = dict(signal["legs"][0], price=".8")
    # At .8 the envelope is .032, but a better .5 fill has .05 envelope.
    with pytest.raises(ExecutionError, match="EXECUTABLE_FEE_LIMIT"):
        asyncio.run(engine._checked_leg(leg, leg, maker=False))
    assert exchange.posts == []


def test_fee_schedule_change_after_reservation_prevents_post(harness, tmp_path):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    use_schedule(harness, tmp_path)
    original = weather.revalidate

    async def refresh(value):
        result = await original(value)
        if weather.calls >= 2:
            exchange.fee_details["r"] = ".8"
        return result

    weather.revalidate = refresh
    with pytest.raises(ExecutionError, match="EXECUTABLE_FEE_LIMIT"):
        asyncio.run(engine.execute(signal))
    assert exchange.posts == []
    assert ledger.summary()["reserved_micros"] == 0


def test_fee_evidence_missing_or_policy_mismatch_never_defaults_to_free(harness, tmp_path):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    use_schedule(harness, tmp_path)
    exchange.fee_policy = "ONCHAIN_BOUND"
    with pytest.raises(ExchangeError, match="FEE_POLICY_MISMATCH"):
        asyncio.run(engine.execute(signal))
    exchange.fee_policy = "EXCHANGE_PUBLISHED_SCHEDULE"
    del exchange.fee_details["e"]
    with pytest.raises(ExchangeError, match="FEE_EVIDENCE_MISSING"):
        asyncio.run(engine.execute(signal))
    assert exchange.posts == []
