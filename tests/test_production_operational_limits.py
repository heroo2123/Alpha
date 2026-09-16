"""Production boundary tests with isolated SQLite and fake financial transports."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import time

import pytest

from polymarket_scanner.production.config import ConfigurationError, ProductionConfig, canonical, digest
from test_production_lifecycle import harness


@pytest.mark.parametrize("mode", [[], {}, 1, False])
def test_malformed_mode_has_precise_nonsecret_error(mode):
    with pytest.raises(ConfigurationError, match="INVALID_MODE"):
        ProductionConfig.parse({"mode": mode})


def test_operator_configuration_change_revokes_running_authority(harness, tmp_path):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    raw = {"fixture": "reviewed operator settings"}
    source = tmp_path / "runtime-config.json"
    source.write_text(json.dumps(raw))
    current = replace(cfg, source_path=source, config_sha256=digest(raw))
    current.activation_file.write_text(json.dumps({"action": "ACTIVATE_LIVE_EXECUTION", "wallet": current.wallet, "config_sha256": current.config_sha256}))
    engine.config = current
    assert engine.authority()
    source.write_text(json.dumps({"fixture": "operator changed a limit"}))
    assert not engine.authority()
    asyncio.run(engine.execute(signal))
    assert exchange.posts == []


def test_stop_after_durable_intent_before_post_aborts_without_unknown_order(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    original = ledger.begin_submission

    def arm_then_stop(*args):
        original(*args)
        cfg.stop_file.touch()

    ledger.begin_submission = arm_then_stop
    asyncio.run(engine.execute(signal))
    assert not exchange.posts
    assert ledger.summary()["reserved_micros"] == 0
    assert ledger.orders(outstanding=False)[0]["status"] == "REJECTED"
    with ledger.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM execution_audit WHERE kind='LOCAL_ABORT_NO_FINANCIAL_POST'").fetchone()[0] == 1


def test_twelve_thousand_historical_orders_do_not_create_lifetime_rpc_scan(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    now = time.time()
    plan = {"signal": signal, "legs": signal["legs"], "strategy": signal["strategy"]}
    payload = canonical({"order": {"expiration": "0"}})
    with ledger.transaction() as db:
        db.executemany("INSERT INTO execution_intents VALUES(?,?,?,?,?,?,?,?,?,?,?)", (
            (f"intent-{i}", f"signal-{i}", cfg.wallet, "DIRECTIONAL", "KSEA/2026-09-17", canonical(plan), digest(plan), now, now, "SUBMISSION_CLOSED", 0)
            for i in range(12000)))
        db.executemany("INSERT INTO execution_orders VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (
            (f"order-{i:05}", f"intent-{i}", 0, "123", "condition1", 1000000, "0.4", "0.004", "REJECTED", 0, "fixture-wire", now, now, 0, 0)
            for i in range(12000)))
        db.executemany("INSERT INTO execution_submissions VALUES(?,?)", ((f"order-{i:05}", payload) for i in range(12000)))
    queried = []
    original = exchange.get_order

    def observed(order_id):
        queried.append(order_id)
        return original(order_id)

    exchange.get_order = observed
    asyncio.run(engine.reconcile())
    assert len(queried) == 5
    assert len(set(queried)) == 5
    assert engine.reconciled
    assert ledger.summary()["confirmed_fill_count"] == 0


def test_late_unmanaged_trade_is_detected_by_historical_window(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    exchange.account_trades = lambda **kwargs: [{"owned_order_ids": ["foreign-old-order"]}]
    with pytest.raises(RuntimeError, match="UNMANAGED_HISTORICAL_ACCOUNT_TRADE"):
        asyncio.run(engine.reconcile())
    assert not engine.authority()
    assert not exchange.posts


def test_close_only_permission_still_reconciles_actual_fills(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))
    exchange.fill()
    snapshot = exchange.account_snapshot
    exchange.account_snapshot = lambda **kwargs: dict(snapshot(**kwargs), openings_allowed=False, opening_restrictions=["ACCOUNT_CLOSE_ONLY"])
    asyncio.run(engine.reconcile())
    assert engine.reconciled
    assert not engine.authority()
    assert ledger.summary()["confirmed_fill_count"] == 1


def test_signal_reader_failure_still_requests_cancel_and_keeps_actual_ledger(harness):
    import sqlite3
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))

    def unavailable(*args, **kwargs):
        raise sqlite3.OperationalError("isolated reader unavailable")

    engine.reader.state = unavailable
    result = asyncio.run(engine.tick())
    assert not result["financial_authority"]
    assert exchange.cancels == exchange.posts
    assert ledger.summary()["reserved_micros"] > 0


def test_signal_wal_survives_between_transactions_for_readonly_worker(tmp_path):
    import os
    import sqlite3
    from polymarket_scanner.production.signals import SignalStore
    store = SignalStore(tmp_path / "signals.db")
    try:
        store.set_state("probe", "first")
        for suffix in ("-wal", "-shm"):
            assert Path(str(store.path) + suffix).stat().st_mode & 0o777 == 0o640
        # No write permission is requested or needed by this connection.
        with sqlite3.connect(store.path.as_uri() + "?mode=ro", uri=True) as reader:
            assert reader.execute("SELECT value FROM live_state WHERE key='probe'").fetchone()[0] == "first"
            store.set_state("probe", "next")
            assert reader.execute("SELECT value FROM live_state WHERE key='probe'").fetchone()[0] == "next"
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                reader.execute("UPDATE live_state SET value='tampered'")
    finally:
        store.close()
