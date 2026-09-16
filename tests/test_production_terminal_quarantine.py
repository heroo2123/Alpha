"""Terminal contradictions preserve externally reported exposure and custody."""
import asyncio
import sqlite3

import pytest

from test_production_lifecycle import harness


@pytest.mark.parametrize("terminal", ["CANCELLED", "REJECTED"])
def test_revived_order_stays_reserved_and_cancellable(harness, terminal):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    if terminal == "REJECTED":
        exchange.outcome = "REJECTED"
    asyncio.run(engine.execute(signal))
    oid = exchange.posts[0]
    if terminal == "CANCELLED":
        exchange.remote[oid]["status"] = "CANCELLED"
        asyncio.run(engine.reconcile())
    else:
        order = ledger.order(oid)
        exchange.remote[oid] = {"id": oid, "token": order["token"], "quantity": order["quantity"],
            "matched": 0, "status": "LIVE", "condition": order["condition_id"],
            "price": order["limit_price"], "side": "BUY", "expires": 0}
    assert ledger.order(oid)["status"] == terminal
    assert ledger.summary()["reserved_micros"] == 0
    exchange.remote[oid]["status"] = "LIVE"
    with pytest.raises(RuntimeError, match="REMOTE_TERMINAL_ORDER_REVIVED"):
        asyncio.run(engine.reconcile())
    assert ledger.order(oid)["status"] == "CANCEL_REQUESTED"
    assert ledger.summary()["reserved_micros"] > 0
    assert ledger.state("fault") == "REMOTE_TERMINAL_ORDER_REVIVED"
    assert not engine.authority()
    assert engine.reader.is_active(signal["id"])
    asyncio.run(engine.manage_existing())
    assert exchange.cancels == [oid]
    assert ledger.summary()["reserved_micros"] > 0


def test_quarantine_database_failure_never_leaves_half_reservation(harness):
    cfg, signal, store, ledger, exchange, weather, engine = harness
    asyncio.run(engine.execute(signal))
    oid = exchange.posts[0]
    exchange.remote[oid]["status"] = "CANCELLED"
    asyncio.run(engine.reconcile())
    with ledger.connect() as db:
        db.execute("CREATE TRIGGER fail_quarantine BEFORE INSERT ON execution_audit WHEN NEW.kind='TERMINAL_ORDER_QUARANTINED' BEGIN SELECT RAISE(ABORT,'quarantine write failed'); END")
    exchange.remote[oid]["status"] = "LIVE"
    with pytest.raises(sqlite3.Error):
        asyncio.run(engine.reconcile())
    assert not engine.authority()
    assert ledger.summary()["reserved_micros"] == 0
    assert ledger.order(oid)["status"] == "CANCELLED"
    with ledger.connect() as db:
        db.execute("DROP TRIGGER fail_quarantine")
    with pytest.raises(RuntimeError, match="REMOTE_TERMINAL_ORDER_REVIVED"):
        asyncio.run(engine.reconcile())
    assert ledger.summary()["reserved_micros"] > 0
