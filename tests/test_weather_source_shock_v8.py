from __future__ import annotations

from datetime import date
from types import SimpleNamespace

from polymarket_scanner.weather_only_contracts import DAILY_HIGH, DAILY_LOW
from polymarket_scanner.weather_only_live_paper_all_signals_v8 import (
    AllPaperWeatherLiveV8Service,
    _excluded,
    _new_extreme_transition,
)

AS_OF = 1_789_500_000.0


def _capture(values, family, published):
    return {
        "as_of": AS_OF,
        "observed_state": {"extreme_value": published},
        "official_observations": [
            {"observed_at": AS_OF - 300.0 + index * 60.0, "value": value}
            for index, value in enumerate(values)
        ],
    }


def test_high_source_shock_requires_latest_observation_to_make_new_high():
    assert _new_extreme_transition(_capture([90, 92, 94], DAILY_HIGH, 94), DAILY_HIGH)[:2] == (92.0, 94.0)
    assert _new_extreme_transition(_capture([90, 94, 93], DAILY_HIGH, 94), DAILY_HIGH) is None


def test_low_source_shock_requires_latest_observation_to_make_new_low():
    assert _new_extreme_transition(_capture([55, 53, 51], DAILY_LOW, 51), DAILY_LOW)[:2] == (53.0, 51.0)
    assert _new_extreme_transition(_capture([55, 51, 52], DAILY_LOW, 51), DAILY_LOW) is None


def test_exclusion_direction_is_correct_for_high_and_low():
    low_bucket = SimpleNamespace(lower=None, upper=92)
    high_bucket = SimpleNamespace(lower=53, upper=None)
    assert _excluded(low_bucket, 94.0, DAILY_HIGH) is True
    assert _excluded(low_bucket, 92.0, DAILY_HIGH) is False
    assert _excluded(high_bucket, 51.0, DAILY_LOW) is True
    assert _excluded(high_bucket, 53.0, DAILY_LOW) is False


def test_candidate_builder_returns_only_newly_excluded_buckets(monkeypatch):
    target = date.fromtimestamp(AS_OF)
    buckets = (
        SimpleNamespace(trade_open=True, no_token="no-90", market_id="m90", condition_id="c90", lower=None, upper=90),
        SimpleNamespace(trade_open=True, no_token="no-93", market_id="m93", condition_id="c93", lower=91, upper=93),
        SimpleNamespace(trade_open=True, no_token="no-95", market_id="m95", condition_id="c95", lower=94, upper=95),
    )
    compiled = SimpleNamespace(
        family=DAILY_HIGH, event_id="event-1", station_hint="KSEA",
        target_date=target, unit="F", buckets=buckets,
    )
    monkeypatch.setattr(
        "polymarket_scanner.weather_only_live_paper_all_signals_v8.compile_strict_temperature_event",
        lambda event: compiled,
    )
    capture = _capture([92, 92, 94], DAILY_HIGH, 94)
    capture.update(
        {
            "event_id":"event-1", "station":"KSEA", "target_date":target.isoformat(),
            "block_reasons":["WRH_TO_MODEL_POPULATION_ALIGNMENT_UNPROVEN"],
            "station_metadata":{"timezone":"UTC"}, "capture_sha256":"a"*64,
        }
    )
    service = object.__new__(AllPaperWeatherLiveV8Service)
    rows = service._source_shock_capture_candidates(capture, {"id":"event-1","title":"test"})
    assert [row["market_id"] for row in rows] == ["m93"]
    assert rows[0]["side"] == "NO"
    assert rows[0]["previous_official_extreme"] == 92.0
    assert rows[0]["new_official_extreme"] == 94.0
