from polymarket_scanner.config import settings
from polymarket_scanner.models import Signal
from polymarket_scanner.outbox import TelegramOutbox
from polymarket_scanner.store import Store


def _signal(event_id: str, confidence: str = "WATCH") -> Signal:
    return Signal(
        detector="test_detector",
        confidence=confidence,
        event_id=event_id,
        market_id=event_id,
        title=f"signal {event_id}",
        detail="test",
        url="https://example.com",
        edge=0.1,
        entry_cost=0.5,
        theoretical_payout=1.0,
        token_ids=[event_id],
    )


def test_outbox_persists_prioritizes_actionable_and_bounds_watch(tmp_path, monkeypatch):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    outbox = TelegramOutbox(db)
    monkeypatch.setattr(settings, "telegram_watch_backlog_limit", 2)

    watch_ids = [store.save_signal(_signal(f"watch-{i}")) for i in range(3)]
    actionable_id = store.save_signal(_signal("actionable", "ACTIONABLE"))
    assert all(x is not None for x in watch_ids)
    assert actionable_id is not None

    assert outbox.enqueue_signal(watch_ids[0], 10) is True
    assert outbox.enqueue_signal(watch_ids[1], 10) is True
    assert outbox.enqueue_signal(watch_ids[2], 10) is False

    # ACTIONABLE must never be rejected by the WATCH backlog limit.
    assert outbox.enqueue_signal(actionable_id, 0) is True

    # A fresh object sees the same queue, proving persistence across processes/restarts.
    reopened = TelegramOutbox(db)
    assert reopened.pending_count() == 3
    due = reopened.next_due()
    assert due is not None
    assert due["signal_id"] == actionable_id
    assert due["priority"] == 0
