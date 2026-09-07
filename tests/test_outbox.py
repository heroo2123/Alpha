import time

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


def test_suppressed_row_requires_claim_and_is_terminal(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    outbox = TelegramOutbox(db)
    signal_id = store.save_signal(_signal("legacy-actionable", "ACTIONABLE"))
    assert signal_id is not None
    assert outbox.enqueue_signal(signal_id, 0) is True

    due = outbox.next_due()
    assert due is not None
    assert outbox.claim(due["id"]) is True
    # A second worker cannot claim the same row.
    assert outbox.claim(due["id"]) is False

    outbox.mark_suppressed(due["id"], "P0 containment")
    assert outbox.pending_count() == 0
    assert outbox.next_due() is None
    row = outbox.status(due["id"])
    assert row is not None
    assert row["status"] == "SUPPRESSED"
    assert row["sent_at"] is None
    assert "P0 containment" in row["last_error"]


def test_confirmed_no_delivery_can_requeue_but_uncertain_is_terminal(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    outbox = TelegramOutbox(db)

    retry_id = store.save_signal(_signal("retry", "ACTIONABLE"))
    uncertain_id = store.save_signal(_signal("uncertain", "ACTIONABLE"))
    assert retry_id is not None and uncertain_id is not None
    assert outbox.enqueue_signal(retry_id, 0)
    assert outbox.enqueue_signal(uncertain_id, 0)

    first = outbox.next_due()
    assert first is not None and first["signal_id"] == retry_id
    assert outbox.claim(first["id"])
    outbox.mark_failed(first["id"], "explicit 429")
    retry_row = outbox.status(first["id"])
    assert retry_row is not None
    assert retry_row["status"] == "PENDING"
    assert retry_row["attempts"] == 1

    # Force the retry into the future so the second row is the next due one.
    second = outbox.next_due()
    assert second is not None and second["signal_id"] == uncertain_id
    assert outbox.claim(second["id"])
    outbox.mark_uncertain(second["id"], "network outcome ambiguous")
    uncertain_row = outbox.status(second["id"])
    assert uncertain_row is not None
    assert uncertain_row["status"] == "UNCERTAIN"
    assert uncertain_row["sent_at"] is None


def test_recovery_only_quarantines_stale_claims(tmp_path):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    outbox = TelegramOutbox(db)
    signal_id = store.save_signal(_signal("stale", "ACTIONABLE"))
    assert signal_id is not None
    assert outbox.enqueue_signal(signal_id, 0)
    due = outbox.next_due()
    assert due is not None
    assert outbox.claim(due["id"])

    # A live/fresh claim must not be stolen by periodic recovery.
    assert outbox.recover_abandoned_claims(60.0) == 0
    row = outbox.status(due["id"])
    assert row is not None and row["status"] == "SENDING"

    with outbox._conn() as c:
        c.execute(
            "UPDATE telegram_outbox SET claimed_at=? WHERE id=?",
            (time.time() - 120.0, due["id"]),
        )
    assert outbox.recover_abandoned_claims(60.0) == 1
    row = outbox.status(due["id"])
    assert row is not None
    assert row["status"] == "UNCERTAIN"
    assert outbox.next_due() is None
