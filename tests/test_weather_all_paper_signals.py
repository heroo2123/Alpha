from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from polymarket_scanner.weather_only_contracts import DAILY_HIGH, DAILY_LOW
from polymarket_scanner.weather_only_live_paper_all_signals import (
    AllSignalsCrashSafeWeatherPaperStore,
    SAME_DAY_FRIEND_LANE,
    _friend_capture_gate,
)


def _epoch(hour: int = 15, minute: int = 30) -> float:
    return datetime(2026, 9, 15, hour, minute, tzinfo=ZoneInfo("America/Chicago")).timestamp()


def _compiled(family: str = DAILY_HIGH):
    return SimpleNamespace(family=family, unit="F")


def _bucket(value: float = 90.0):
    return SimpleNamespace(lower=value, upper=value)


def _capture(
    *,
    family: str = DAILY_HIGH,
    support_hits: int = 30,
    nws_extreme: float | None = None,
    extra_reason: str | None = None,
):
    as_of = _epoch()
    if family == DAILY_HIGH:
        values = [90.0, 89.0, 89.0, 88.0]
        observed_extreme = 90.0
        member_good = 90.0
        member_bad = 91.0
        default_nws = 90.0
    else:
        values = [50.0, 51.0, 51.0, 52.0]
        observed_extreme = 50.0
        member_good = 50.0
        member_bad = 49.0
        default_nws = 50.0

    observations = [
        {
            "observed_at": as_of - (len(values) - index) * 3600.0,
            "value": value,
        }
        for index, value in enumerate(values, 1)
    ]
    members = [
        {"member_label": f"member{index:02d}", "extreme_value": member_good}
        for index in range(support_hits)
    ]
    members.extend(
        {"member_label": f"bad{index:02d}", "extreme_value": member_bad}
        for index in range(31 - support_hits)
    )
    reasons = ["WRH_TO_MODEL_POPULATION_ALIGNMENT_UNPROVEN"]
    if extra_reason:
        reasons.append(extra_reason)
    return {
        "capture_sha256": "a" * 64,
        "as_of": as_of,
        "block_reasons": reasons,
        "station_metadata": {"timezone": "America/Chicago"},
        "observed_state": {"extreme_value": observed_extreme},
        "official_observations": observations,
        "near_term_path": {
            "sampled_extreme": default_nws if nws_extreme is None else nws_extreme,
        },
        "remaining_hours_path": {
            "ensemble": {"members": members},
        },
    }


def test_friend_high_gate_accepts_three_layer_supported_late_lock():
    result = _friend_capture_gate(_capture(), _compiled(), _bucket())
    assert result is not None
    assert result["gefs_hits"] == 30
    assert result["gefs_total"] == 31
    assert result["trend_steps"] == 3
    assert result["observed_extreme"] == 90.0
    assert result["current"] == 88.0


def test_friend_low_gate_is_symmetric():
    result = _friend_capture_gate(
        _capture(family=DAILY_LOW),
        _compiled(DAILY_LOW),
        _bucket(50.0),
    )
    assert result is not None
    assert result["gefs_hits"] == 30
    assert result["trend_steps"] == 3
    assert result["observed_drop"] == 2.0


def test_friend_gate_rejects_only_29_of_31_gefs_support():
    assert _friend_capture_gate(
        _capture(support_hits=29), _compiled(), _bucket()
    ) is None


def test_friend_gate_rejects_nws_path_outside_bucket():
    assert _friend_capture_gate(
        _capture(nws_extreme=91.0), _compiled(), _bucket()
    ) is None


def test_friend_gate_rejects_any_additional_capture_block_reason():
    assert _friend_capture_gate(
        _capture(extra_reason="ELAPSED_OFFICIAL_OBSERVATION_GAPS_PRESENT"),
        _compiled(),
        _bucket(),
    ) is None


def test_promoted_same_day_lane_reuses_directional_paper_position_accounting():
    store = object.__new__(AllSignalsCrashSafeWeatherPaperStore)
    signal = {
        "telegram_message_id": 123,
        "telegram_sent_at": 1_789_000_000.0,
        "entry_cost": 0.91,
        "lane": SAME_DAY_FRIEND_LANE,
        "evidence_class": "HEURISTIC_UNCALIBRATED_WRH_NWS_GEFS_V1",
        "event_id": "evt",
        "market_id": "mkt",
        "token_id": "tok",
        "side": "YES",
        "payload_json": {
            "event_title": "Highest temperature test",
            "ask_size": 100.0,
            "quote_observed_at": 1_789_000_000.0,
        },
    }
    spec = store._position_spec(signal, 10.0)
    assert spec is not None
    assert spec["lane"] == SAME_DAY_FRIEND_LANE
    assert spec["position_kind"] == "DIRECTIONAL"
    assert spec["market_id"] == "mkt"
    assert spec["token_id"] == "tok"
    assert spec["side"] == "YES"
    assert spec["status"] == "OPEN"


def test_unrelated_unknown_directional_lane_remains_unadmitted():
    store = object.__new__(AllSignalsCrashSafeWeatherPaperStore)
    signal = {
        "telegram_message_id": 123,
        "telegram_sent_at": 1_789_000_000.0,
        "entry_cost": 0.91,
        "lane": "weather_unreviewed_directional",
        "evidence_class": "TEST",
        "event_id": "evt",
        "market_id": "mkt",
        "token_id": "tok",
        "side": "YES",
        "payload_json": {"ask_size": 100.0},
    }
    assert store._position_spec(signal, 10.0) is None
