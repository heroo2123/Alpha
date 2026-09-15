from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_paper_post_receipt import (
    PAPER_EXECUTION_PROTOCOL_V5,
    PostReceiptWeatherPaperStore,
)

NOW = 1_800_000_000.0


def _signal(store: PostReceiptWeatherPaperStore, name: str) -> int:
    sid = store.save_signal(
        fingerprint=f"fp-terminal-{name}",
        lane="weather_same_day_friend_lock",
        evidence_class="TEST_V5_TERMINAL",
        event_id=f"event-{name}",
        market_id=f"market-{name}",
        side="YES",
        token_id=f"token-{name}",
        model_probability=0.9,
        entry_cost=0.92,
        raw_gap=0.03,
        theoretical_payout=1.0,
        created_at=NOW - 2.0,
        payload={
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
            "decision_id": f"decision-{name}",
            "decision_expires_at": NOW + 30.0,
            "event_title": f"Event {name}",
            "financial_authority": False,
            "automatic_order_placement": False,
        },
    )
    assert sid is not None
    store.set_signal_status(sid, "PENDING_DELIVERY")
    store.mark_telegram_sent(sid, 5000 + sid, sent_at=NOW)
    return sid


def _status(store: PostReceiptWeatherPaperStore, sid: int) -> str:
    with store._conn() as db:
        return str(
            db.execute(
                "SELECT status FROM weather_paper_signals WHERE id=?", (sid,)
            ).fetchone()[0]
        )


def test_not_actionable_status_and_reason_are_committed_together_and_survive_restart(tmp_path):
    store = PostReceiptWeatherPaperStore(tmp_path / "paper.sqlite")
    sid = _signal(store, "specific")
    store.mark_post_receipt_not_actionable(
        sid,
        decision_id="decision-specific",
        event_id="event-specific",
        market_id="market-specific",
        side="YES",
        reason="POST_RECEIPT_BOOK_MOVED",
        recorded_at=NOW + 1.0,
    )

    assert _status(store, sid) == "POST_RECEIPT_NOT_ACTIONABLE"
    with store._conn() as db:
        row = db.execute(
            "SELECT outcome,reason FROM weather_paper_decisions "
            "WHERE decision_id='decision-specific' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert tuple(row) == (
            "POST_RECEIPT_NOT_ACTIONABLE",
            "POST_RECEIPT_BOOK_MOVED",
        )
        assert db.execute(
            "SELECT COUNT(*) FROM weather_paper_positions WHERE signal_id=?", (sid,)
        ).fetchone()[0] == 0

    recovered = store.reconcile_v5_after_restart()
    assert recovered["actionability_unproven_after_restart"] == 0
    assert recovered["reconstructed_fills"] == 0
    assert _status(store, sid) == "POST_RECEIPT_NOT_ACTIONABLE"


def test_terminal_audit_rolls_back_status_if_decision_insert_fails(tmp_path):
    store = PostReceiptWeatherPaperStore(tmp_path / "paper.sqlite")
    sid = _signal(store, "rollback")
    assert _status(store, sid) == "ACKNOWLEDGED"

    with store._conn() as db:
        db.execute(
            """
            CREATE TRIGGER fail_terminal_decision
            BEFORE INSERT ON weather_paper_decisions
            WHEN NEW.outcome='POST_RECEIPT_NOT_ACTIONABLE'
            BEGIN
                SELECT RAISE(ABORT, 'forced terminal audit failure');
            END;
            """
        )

    with pytest.raises(Exception):
        store.mark_post_receipt_not_actionable(
            sid,
            decision_id="decision-rollback",
            event_id="event-rollback",
            market_id="market-rollback",
            side="YES",
            reason="FORCED_FAILURE",
            recorded_at=NOW + 1.0,
        )

    # The status update happened earlier in the attempted transaction, so observing
    # ACKNOWLEDGED here proves SQLite rolled the whole transaction back.
    assert _status(store, sid) == "ACKNOWLEDGED"
    with store._conn() as db:
        assert db.execute(
            "SELECT COUNT(*) FROM weather_paper_decisions "
            "WHERE decision_id='decision-rollback'"
        ).fetchone()[0] == 0


def test_accounting_error_terminal_state_is_not_reclassified_on_restart(tmp_path):
    store = PostReceiptWeatherPaperStore(tmp_path / "paper.sqlite")
    sid = _signal(store, "accounting")
    store.set_signal_status(sid, "PAPER_ACCOUNTING_ERROR")

    recovered = store.reconcile_v5_after_restart()
    assert recovered["actionability_unproven_after_restart"] == 0
    assert recovered["reconstructed_fills"] == 0
    assert _status(store, sid) == "PAPER_ACCOUNTING_ERROR"
