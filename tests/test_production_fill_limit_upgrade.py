"""Upgrade populated journals without losing fills or bypassing aggregate limits."""
import asyncio
import sqlite3
import time

import pytest

from polymarket_scanner.production.ledger import ExecutionLedger
from test_production_lifecycle import harness


def legacy_fragments(harness, *, cost=4, fee=1):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    assert asyncio.run(engine.execute(signal))
    order = ledger.orders()[0]
    rows = [{"id": f"legacy:{i}", "order_id": order["id"], "token": order["token"],
             "quantity": 10, "cost": cost, "fee": fee, "transaction_hash": f"legacy-tx-{i}",
             "block_hash": "legacy-block", "log_index": i} for i in range(2)]
    # Populated v0 schema state, written before aggregate-limit auditing existed.
    # Each fragment met v0's individual tolerance; no actual endpoint is called.
    with ledger.transaction() as db:
        db.execute("DELETE FROM execution_state WHERE key='fill_limit_audit_version'")
        for row in rows:
            db.execute("INSERT INTO execution_fills VALUES(?,?,?,?,?,?,?,?,?,?)", tuple(row.values()) + (time.time(),))
        db.execute("UPDATE execution_orders SET matched=20,status='PARTIAL' WHERE id=?", (order["id"],))
    exchange.fills[order["id"]] = rows
    exchange.remote[order["id"]]["matched"] = 20
    exchange.balances[order["token"]] = 20
    return order


@pytest.mark.parametrize("cost,fee,code", [(4, 1, "ACTUAL_FEE_LIMIT_BREACH"),
                                         (5, 0, "ACTUAL_PRICE_LIMIT_BREACH")])
def test_old_aggregate_breach_cannot_regain_authority_through_duplicate_fills(harness, cost, fee, code):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    order = legacy_fragments(harness, cost=cost, fee=fee)
    ledger.recover_after_restart()
    asyncio.run(engine.reconcile())
    assert not engine.authority()
    assert ledger.state("fault") == code
    assert ledger.summary()["confirmed_fill_count"] == 2
    assert ledger.order(order["id"])["status"] == "CANCEL_REQUESTED"
    assert exchange.cancels == [order["id"]]
    assert ledger.state("fill_limit_audit_version") == "1"


def test_upgrade_audit_is_atomic_and_retries_after_database_failure(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    order = legacy_fragments(harness)
    with ledger.connect() as db:
        db.execute("CREATE TRIGGER fail_upgrade BEFORE INSERT ON execution_audit WHEN NEW.kind='FILL_LIMIT_UPGRADE_COMPLETE' BEGIN SELECT RAISE(ABORT,'injected'); END")
    with pytest.raises(sqlite3.Error):
        asyncio.run(engine.reconcile())
    assert not engine.authority()
    assert ledger.state("fault") is None
    assert ledger.state("fill_limit_audit_version") is None
    assert ledger.order(order["id"])["status"] == "PARTIAL"
    assert exchange.cancels == []
    with ledger.connect() as db:
        db.execute("DROP TRIGGER fail_upgrade")
    asyncio.run(engine.reconcile())
    assert ledger.state("fault") == "ACTUAL_FEE_LIMIT_BREACH"
    assert exchange.cancels == [order["id"]]


def test_acknowledged_upgrade_breach_does_not_refault_on_restart(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    order = legacy_fragments(harness)
    asyncio.run(engine.reconcile())
    assert not engine.authority()
    exchange.remote[order["id"]]["status"] = "CANCELLED"
    asyncio.run(engine.reconcile(ignore_sticky_fault=True))
    assert engine.reconciled
    ledger.clear_fault("ACTUAL_FEE_LIMIT_BREACH")
    engine.ledger = ledger = ExecutionLedger(cfg.execution_db, cfg.wallet)
    ledger.recover_after_restart()
    asyncio.run(engine.reconcile())
    assert engine.authority()
    assert ledger.summary()["confirmed_fill_count"] == 2
    with ledger.connect() as db:
        assert db.execute("SELECT COUNT(*) FROM execution_audit WHERE kind='FILL_LIMIT_UPGRADE_COMPLETE'").fetchone()[0] == 2
        # One empty fresh journal audit plus the simulated prior-version upgrade.
        assert db.execute("SELECT COUNT(*) FROM execution_audit WHERE kind='FILL_LIMIT_UPGRADE_BREACH'").fetchone()[0] == 1
