from __future__ import annotations

from polymarket_scanner.weather_only_live_paper_human import (
    HumanReadableWeatherLivePaperService,
    _market_question,
    _member_vote,
)


def test_member_vote_describes_31_of_31_without_claiming_calibrated_certainty():
    assert _member_vote(1.0) == (31, 31)
    assert _member_vote(0.0) == (0, 31)
    assert _member_vote(30 / 31) == (30, 31)


def test_market_question_matches_exact_child_market():
    event = {
        "markets": [
            {"id": "a", "question": "Will it be 31C?"},
            {"id": "b", "question": "Will it be 32C?"},
        ]
    }
    assert _market_question(event, "b") == "Will it be 32C?"
    assert _market_question(event, "missing") is None


def test_no_forecast_message_explains_action_votes_cost_and_uncertainty_without_instance():
    candidate = {
        "event_id": "997479",
        "market_id": "4430373",
        "side": "NO",
        "bucket_label": "32–33C",
        "station": "TEST",
        "target_date": "2026-09-12",
        "unit": "C",
        "raw_probability": 1.0,
        "ask": 0.001,
        "fee": 0.00005,
        "entry_cost": 0.00105,
        "raw_gap": 0.99895,
        "ask_size": 50.0,
    }
    event = {
        "id": "997479",
        "title": "Highest temperature in Test City on September 12?",
        "markets": [{"id": "4430373", "question": "Will the highest temperature be 32-33C?"}],
    }
    text = HumanReadableWeatherLivePaperService._forecast_message(object(), candidate, event)
    assert "PAPER SIDE: NO" in text
    assert "BUY NO" in text
    assert "NOT in 32–33C" in text
    assert "31/31" in text
    assert "0.105¢" in text
    assert "pays $1 if correct" in text
    assert "does <b>not</b> mean a 100% real-world chance" in text
    assert "No order was placed" in text


def test_yes_forecast_message_explains_inside_bucket():
    candidate = {
        "event_id": "e",
        "market_id": "m",
        "side": "YES",
        "bucket_label": "80–81F",
        "station": "KAAA",
        "target_date": "2026-09-13",
        "unit": "F",
        "raw_probability": 30 / 31,
        "ask": 0.50,
        "fee": 0.0,
        "entry_cost": 0.50,
        "raw_gap": 30 / 31 - 0.50,
        "ask_size": 10.0,
    }
    text = HumanReadableWeatherLivePaperService._forecast_message(object(), candidate, None)
    assert "PAPER SIDE: YES" in text
    assert "IS in 80–81F" in text
    assert "30/31" in text
