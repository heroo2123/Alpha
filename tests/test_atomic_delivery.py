import sqlite3

import pytest

from polymarket_scanner.atomic_delivery import persist_trade_now_intent
from polymarket_scanner.models import Signal
from polymarket_scanner.outbox import TelegramOutbox
from polymarket_scanner.store import Store


def _ready_signal(key: str = "one") -> Signal:
    return Signal(
        detector="test_promoted_detector",
        confidence="ACTIONABLE",
        event_id=f"event-{key}",
        market_id=f"market-{key}",
        title="ready",
        detail="ready",
        url="https://example.com",
        edge=0.05,
        entry_cost=0.95,
        theoretical_payout=1.0,
        token_ids=[f"token-{key}"],
        metadata={
            "trade_ready": True,
            "trade_ready_version": "test",
            "fingerprint_key": key,
        },
    )


def test_trade_signal_and_outbox_intent_commit_together(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    outbox = TelegramOutbox(db)

    signal_id = persist_trade_now_intent(store, _ready_signal(), priority=0)
    assert signal_id is not None
    row = store.get_signal(signal_id)
    assert row is not None
    due = outbox.next_due()
    assert due is not None
    assert due["signal_id"] == signal_id
    assert due["priority"] == 0
    assert due["status"] == "PENDING"


def test_outbox_insert_failure_rolls_back_signal_fingerprint(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    TelegramOutbox(db)
    with sqlite3.connect(db) as connection:
        connection.execute("DROP TABLE telegram_outbox")

    signal = _ready_signal("rollback")
    with pytest.raises(sqlite3.OperationalError):
        persist_trade_now_intent(store, signal, priority=0)

    with store._conn() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM signals WHERE fingerprint=?",
            (signal.fingerprint(),),
        ).fetchone()[0]
    assert count == 0


def test_duplicate_trade_intent_does_not_create_orphan_rows(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    outbox = TelegramOutbox(db)
    signal = _ready_signal("duplicate")

    first = persist_trade_now_intent(store, signal, priority=0)
    second = persist_trade_now_intent(store, signal, priority=0)
    assert first is not None
    assert second is None

    with store._conn() as connection:
        signals = connection.execute(
            "SELECT COUNT(*) FROM signals WHERE fingerprint=?", (signal.fingerprint(),)
        ).fetchone()[0]
        intents = connection.execute(
            "SELECT COUNT(*) FROM telegram_outbox WHERE signal_id=?", (first,)
        ).fetchone()[0]
    assert signals == 1
    assert intents == 1


def test_non_trade_ready_signal_cannot_create_financial_intent(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    TelegramOutbox(db)
    signal = _ready_signal("not-ready")
    signal.metadata["trade_ready"] = False
    with pytest.raises(ValueError, match="trade-ready"):
        persist_trade_now_intent(store, signal, priority=0)
