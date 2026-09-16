from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_paper_positions import WeatherPaperPositionError
from polymarket_scanner.weather_only_paper_post_receipt import (
    PAPER_EXECUTION_PROTOCOL_V5,
    PAPER_POSITION_VERSION_V5,
    PostReceiptWeatherPaperStore,
)

NOW = 1_800_000_000.0


def payload(name: str) -> dict:
    return {
        "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
        "decision_id": f"decision-{name}",
        "decision_expires_at": NOW + 30.0,
        "event_title": name,
        "financial_authority": False,
        "automatic_order_placement": False,
    }


def signal(store: PostReceiptWeatherPaperStore, name: str, sent_at: float = NOW) -> int:
    sid = store.save_signal(
        fingerprint=f"fp-{name}", lane="weather_same_day_friend_lock",
        evidence_class="TEST", event_id=f"event-{name}", market_id=f"market-{name}",
        side="YES", token_id=f"token-{name}", model_probability=0.99,
        entry_cost=0.91, raw_gap=0.08, theoretical_payout=1.0,
        created_at=NOW - 1.0, payload=payload(name),
    )
    assert sid is not None
    store.set_signal_status(sid, "PENDING_DELIVERY")
    store.mark_telegram_sent(sid, 100 + sid, sent_at=sent_at)
    return sid


def execution(name: str, started: float = NOW + 1.0, finished: float = NOW + 2.0) -> dict:
    return {
        "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
        "decision_id": f"decision-{name}", "decision_expires_at": NOW + 30.0,
        "post_receipt_recheck_started_at": started,
        "post_receipt_recheck_finished_at": finished,
        "entry_cost_per_unit": 0.91, "visible_units": 20.0,
        "minimum_order_size": 1.0, "theoretical_payout_per_unit": 1.0,
        "legs": [{
            "market_id": f"market-{name}", "condition_id": f"condition-{name}",
            "token_id": f"token-{name}", "side": "YES", "ask": 0.90,
            "fee": 0.01, "quote_observed_at": finished - 0.1,
            "minimum_order_size": 1.0, "book_hash": f"hash-{name}",
        }],
    }


def test_directional_post_receipt_admission_is_idempotent(tmp_path):
    store = PostReceiptWeatherPaperStore(tmp_path / "paper.sqlite")
    sid = signal(store, "one")
    position = store.admit_post_receipt_position(sid, 5.0, execution("one"))
    assert position["position_version"] == PAPER_POSITION_VERSION_V5
    assert position["execution_protocol"] == PAPER_EXECUTION_PROTOCOL_V5
    assert position["position_kind"] == "DIRECTIONAL"
    assert position["status"] == "OPEN"
    assert position["filled_units"] == pytest.approx(5.0 / 0.91)
    assert position["legs"][0]["condition_id"] == "condition-one"
    again = store.admit_post_receipt_position(sid, 5.0, execution("one"))
    assert again["id"] == position["id"]


def test_structural_admission_persists_all_settlement_identities(tmp_path):
    store = PostReceiptWeatherPaperStore(tmp_path / "paper.sqlite")
    sid = signal(store, "basket")
    value = execution("basket")
    value["entry_cost_per_unit"] = 0.80
    value["visible_units"] = 3.0
    value["legs"] = [
        {"market_id":"m1","condition_id":"c1","token_id":"yes-1","side":"YES",
         "ask":0.39,"fee":0.01,"quote_observed_at":NOW+1.9,"minimum_order_size":1.0,"book_hash":"h1"},
        {"market_id":"m2","condition_id":"c2","token_id":"yes-2","side":"YES",
         "ask":0.39,"fee":0.01,"quote_observed_at":NOW+1.8,"minimum_order_size":2.0,"book_hash":"h2"},
    ]
    position = store.admit_post_receipt_position(sid, 5.0, value)
    assert position["position_kind"] == "STRUCTURAL"
    assert position["side"] == "BASKET"
    assert position["filled_units"] == pytest.approx(3.0)
    assert position["legs"] == [
        {"market_id":"m1","condition_id":"c1","token_id":"yes-1","side":"YES"},
        {"market_id":"m2","condition_id":"c2","token_id":"yes-2","side":"YES"},
    ]


def test_recheck_cannot_start_before_telegram_receipt(tmp_path):
    store = PostReceiptWeatherPaperStore(tmp_path / "paper.sqlite")
    sid = signal(store, "causal", sent_at=NOW + 3.0)
    with pytest.raises(WeatherPaperPositionError) as exc:
        store.admit_post_receipt_position(
            sid, 5.0, execution("causal", started=NOW + 2.0, finished=NOW + 4.0)
        )
    assert exc.value.code == "V5_POST_RECEIPT_RECHECK_NOT_CAUSAL"


def test_expiry_boundary_is_closed(tmp_path):
    store = PostReceiptWeatherPaperStore(tmp_path / "paper.sqlite")
    sid = signal(store, "expiry")
    with pytest.raises(WeatherPaperPositionError) as exc:
        store.admit_post_receipt_position(
            sid, 5.0, execution("expiry", started=NOW + 29.0, finished=NOW + 30.0)
        )
    assert exc.value.code == "V5_DECISION_EXPIRED"
