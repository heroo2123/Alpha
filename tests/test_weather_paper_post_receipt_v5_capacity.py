from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_paper_post_receipt import (
    PAPER_EXECUTION_PROTOCOL_V5,
    PostReceiptWeatherPaperStore,
)

NOW = 1_800_000_000.0


def _make_signal(store: PostReceiptWeatherPaperStore, name: str) -> int:
    payload = {
        "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
        "decision_id": f"decision-{name}",
        "decision_expires_at": NOW + 30.0,
        "event_title": name,
        "financial_authority": False,
        "automatic_order_placement": False,
    }
    sid = store.save_signal(
        fingerprint=f"fp-{name}", lane="weather_forecast_raw_gap", evidence_class="TEST",
        event_id=f"event-{name}", market_id="market-shared", side="YES",
        token_id="token-shared", model_probability=0.8, entry_cost=0.91,
        raw_gap=0.1, theoretical_payout=1.0, created_at=NOW - 1.0, payload=payload,
    )
    assert sid is not None
    store.set_signal_status(sid, "PENDING_DELIVERY")
    store.mark_telegram_sent(sid, 1000 + sid, sent_at=NOW)
    return sid


def _execution(name: str) -> dict:
    return {
        "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
        "decision_id": f"decision-{name}", "decision_expires_at": NOW + 30.0,
        "post_receipt_recheck_started_at": NOW + 1.0,
        "post_receipt_recheck_finished_at": NOW + 2.0,
        "entry_cost_per_unit": 0.91, "visible_units": 6.0,
        "minimum_order_size": 0.1, "theoretical_payout_per_unit": 1.0,
        "legs": [{
            "market_id":"market-shared","condition_id":"condition-shared",
            "token_id":"token-shared","side":"YES","ask":0.90,"fee":0.01,
            "quote_observed_at":NOW+1.9,"minimum_order_size":0.1,"book_hash":"book-shared",
        }],
    }


def test_shared_snapshot_capacity_is_bounded_across_two_paper_signals(tmp_path):
    store = PostReceiptWeatherPaperStore(tmp_path / "paper.sqlite")
    first = _make_signal(store, "a")
    second = _make_signal(store, "b")
    p1 = store.admit_post_receipt_position(first, 5.0, _execution("a"))
    p2 = store.admit_post_receipt_position(second, 5.0, _execution("b"))
    assert p1["filled_units"] == pytest.approx(5.0 / 0.91)
    assert p2["filled_units"] == pytest.approx(6.0 - p1["filled_units"])
    assert p1["filled_units"] + p2["filled_units"] == pytest.approx(6.0)
