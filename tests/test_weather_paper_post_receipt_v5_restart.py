from __future__ import annotations

from polymarket_scanner.weather_only_paper_post_receipt import (
    PAPER_EXECUTION_PROTOCOL_V5,
    PostReceiptWeatherPaperStore,
)

NOW = 1_800_000_000.0


def _payload(name: str) -> dict:
    return {
        "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
        "decision_id": f"decision-{name}",
        "decision_expires_at": NOW + 30.0,
        "event_title": name,
        "financial_authority": False,
        "automatic_order_placement": False,
    }


def _signal(store: PostReceiptWeatherPaperStore, name: str, receipt: bool) -> int:
    sid = store.save_signal(
        fingerprint=f"fp-{name}", lane="weather_forecast_raw_gap", evidence_class="TEST",
        event_id=f"event-{name}", market_id=f"market-{name}", side="YES",
        token_id=f"token-{name}", model_probability=0.8, entry_cost=0.91,
        raw_gap=0.1, theoretical_payout=1.0, created_at=NOW - 1.0,
        payload=_payload(name),
    )
    assert sid is not None
    store.set_signal_status(sid, "PENDING_DELIVERY")
    if receipt:
        store.mark_telegram_sent(sid, 1000 + sid, sent_at=NOW)
    return sid


def test_restart_after_receipt_never_reconstructs_v5_fill(tmp_path):
    store = PostReceiptWeatherPaperStore(tmp_path / "paper.sqlite")
    sid = _signal(store, "restart", True)
    recovered = store.reconcile_v5_after_restart()
    assert recovered["actionability_unproven_after_restart"] == 1
    assert recovered["reconstructed_fills"] == 0
    with store._conn() as db:
        assert db.execute(
            "SELECT status FROM weather_paper_signals WHERE id=?", (sid,)
        ).fetchone()[0] == "ACTIONABILITY_UNPROVEN"
        assert db.execute(
            "SELECT COUNT(*) FROM weather_paper_positions WHERE signal_id=?", (sid,)
        ).fetchone()[0] == 0
    assert store.reconcile_v5_after_restart()["actionability_unproven_after_restart"] == 0


def test_restart_during_unreceipted_delivery_is_uncertain(tmp_path):
    store = PostReceiptWeatherPaperStore(tmp_path / "paper.sqlite")
    sid = _signal(store, "uncertain", False)
    recovered = store.reconcile_v5_after_restart()
    assert recovered["delivery_uncertain_recovered"] == 1
    assert recovered["reconstructed_fills"] == 0
    with store._conn() as db:
        assert db.execute(
            "SELECT status FROM weather_paper_signals WHERE id=?", (sid,)
        ).fetchone()[0] == "DELIVERY_UNCERTAIN"
