import sqlite3
import time
from datetime import datetime, timezone

from polymarket_scanner.atomic_delivery import persist_trade_now_intent
from polymarket_scanner.models import Signal
from polymarket_scanner.outbox import TelegramOutbox
from polymarket_scanner.store import Store


def _signal(key: str) -> Signal:
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
            "fingerprint_bucket_seconds": 900,
        },
        created_at=datetime.now(timezone.utc),
    )


def _claimed(tmp_path, key="chaos"):
    db = str(tmp_path / "signals.db")
    store = Store(db)
    outbox = TelegramOutbox(db)
    signal_id = persist_trade_now_intent(store, _signal(key), priority=0)
    assert signal_id is not None
    due = outbox.next_due()
    assert due is not None
    assert outbox.claim(int(due["id"])) is True
    return db, store, outbox, due


def test_atomic_claim_prevents_second_worker_from_sending_same_intent(tmp_path):
    _db, _store, outbox, due = _claimed(tmp_path, "double-worker")
    assert outbox.claim(int(due["id"])) is False
    assert outbox.next_due() is None
    row = outbox.status(int(due["id"]))
    assert row["status"] == "SENDING"
    assert row["claimed_at"] is not None


def test_recent_inflight_claim_is_not_stolen_or_recovered(tmp_path):
    _db, _store, outbox, due = _claimed(tmp_path, "recent")
    assert outbox.recover_abandoned_claims(60.0) == 0
    row = outbox.status(int(due["id"]))
    assert row["status"] == "SENDING"
    assert outbox.next_due() is None


def test_crash_after_possible_telegram_acceptance_becomes_uncertain_never_pending(tmp_path):
    """Model: claim -> request may cross network -> process dies before local receipt write."""
    db, _store, outbox, due = _claimed(tmp_path, "post-crossing-crash")
    outbox_id = int(due["id"])

    # Simulate enough time passing for a restarted worker to classify the abandoned
    # in-flight claim. There is intentionally no mark_sent/mark_failed call here.
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE telegram_outbox SET claimed_at=? WHERE id=?",
            (time.time() - 120.0, outbox_id),
        )

    assert outbox.recover_abandoned_claims(60.0) == 1
    row = outbox.status(outbox_id)
    assert row["status"] == "UNCERTAIN"
    assert row["sent_at"] is None
    assert row["claimed_at"] is None
    assert "not retried" in str(row["last_error"])
    assert outbox.next_due() is None
    assert outbox.pending_count() == 0

    # Repeated recovery is idempotent and cannot turn UNCERTAIN back into PENDING.
    assert outbox.recover_abandoned_claims(1.0) == 0
    assert outbox.status(outbox_id)["status"] == "UNCERTAIN"


def test_confirmed_no_delivery_is_the_only_failure_path_that_requeues(tmp_path):
    db, _store, outbox, due = _claimed(tmp_path, "confirmed-no-delivery")
    outbox_id = int(due["id"])
    outbox.mark_failed(outbox_id, "explicit Telegram 429; no message accepted")
    row = outbox.status(outbox_id)
    assert row["status"] == "PENDING"
    assert row["attempts"] == 1
    assert row["claimed_at"] is None
    assert row["next_attempt_at"] > time.time()

    # The retry is delayed; the delivery loop must later rebuild a fresh certificate.
    assert outbox.next_due() is None
    with sqlite3.connect(db) as connection:
        connection.execute(
            "UPDATE telegram_outbox SET next_attempt_at=0 WHERE id=?",
            (outbox_id,),
        )
    assert outbox.next_due()["id"] == outbox_id
    assert outbox.claim(outbox_id) is True


def test_terminal_delivery_states_cannot_be_rewritten_by_late_worker_actions(tmp_path):
    _db, _store, outbox, due = _claimed(tmp_path, "terminal")
    outbox_id = int(due["id"])
    outbox.mark_uncertain(outbox_id, "ambiguous network result")
    assert outbox.status(outbox_id)["status"] == "UNCERTAIN"

    # A delayed callback from an old worker must not overwrite the terminal state.
    outbox.mark_sent(outbox_id)
    outbox.mark_failed(outbox_id, "late retryable result")
    outbox.mark_suppressed(outbox_id, "late suppression")
    row = outbox.status(outbox_id)
    assert row["status"] == "UNCERTAIN"
    assert row["sent_at"] is None
    assert outbox.next_due() is None


def test_sent_receipt_is_terminal_and_not_recoverable(tmp_path):
    _db, _store, outbox, due = _claimed(tmp_path, "sent")
    outbox_id = int(due["id"])
    outbox.mark_sent(outbox_id)
    row = outbox.status(outbox_id)
    assert row["status"] == "SENT"
    assert row["sent_at"] is not None
    assert row["claimed_at"] is None
    assert outbox.recover_abandoned_claims(1.0) == 0
    assert outbox.next_due() is None
