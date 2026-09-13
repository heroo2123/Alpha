from __future__ import annotations

from datetime import datetime, timedelta, timezone

from polymarket_scanner.weather_only_forecast import EnsembleMappingPolicy
from polymarket_scanner.weather_only_gefs_hourly import parse_open_meteo_gefs_hourly_target_day
from polymarket_scanner.weather_only_nws_near_term import build_nws_raw_snapshot
from polymarket_scanner.weather_only_paper_facade import CorrectiveWeatherPaperStore
from polymarket_scanner.weather_only_rules import compile_temperature_rule_authority
from polymarket_scanner.weather_only_same_day_capture import (
    SAME_DAY_CAPTURE_BLOCKED,
    assemble_same_day_capture,
    verify_same_day_capture_record,
)
from polymarket_scanner.weather_only_same_day_capture_store import SameDayCaptureStore
from polymarket_scanner.weather_only_same_day_contract import build_same_day_contract_semantics
from polymarket_scanner.weather_only_station_metadata import parse_nws_station_metadata
from test_weather_only_conditioned_wrh import AS_OF, TARGET, _compiled, _snapshot
from test_weather_only_rules import _nws_event


LAT = 40.7769
LON = -73.8740
ZONE = "America/New_York"


def _station_metadata():
    return parse_nws_station_metadata(
        {
            "id": "https://api.weather.gov/stations/KLGA",
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [LON, LAT]},
            "properties": {
                "stationIdentifier": "KLGA",
                "timeZone": ZONE,
            },
        },
        requested_station="KLGA",
        received_at=AS_OF - 30.0,
    )


def _nws_snapshot():
    segment_hour_start = datetime(2026, 9, 11, 18, 0, tzinfo=timezone.utc)
    points = {
        "type": "Feature",
        "properties": {
            "forecastGridData": "https://api.weather.gov/gridpoints/OKX/33,37"
        },
    }
    grid = {
        "type": "Feature",
        "properties": {
            "updateTime": "2026-09-11T17:30:00+00:00",
            "temperature": {
                "uom": "wmoUnit:degC",
                "values": [
                    {
                        "validTime": segment_hour_start.isoformat() + "/PT1H",
                        "value": 28.0,
                    }
                ],
            },
        },
    }
    return build_nws_raw_snapshot(
        points,
        grid,
        station="KLGA",
        latitude=LAT,
        longitude=LON,
        points_received_at=AS_OF - 20.0,
        grid_received_at=AS_OF - 10.0,
    )


def _gefs():
    hourly = {"time": [f"2026-09-11T{hour:02d}:00" for hour in range(24)]}
    units = {"time": "iso8601"}
    keys = ("temperature_2m",) + tuple(
        f"temperature_2m_member{index:02d}" for index in range(1, 31)
    )
    for index, key in enumerate(keys):
        hourly[key] = [72.0 + index * 0.05 + hour * 0.1 for hour in range(24)]
        units[key] = "°F"
    return parse_open_meteo_gefs_hourly_target_day(
        {
            "latitude": LAT,
            "longitude": LON,
            "timezone": ZONE,
            "hourly": hourly,
            "hourly_units": units,
        },
        station="KLGA",
        target_date=TARGET,
        unit="F",
        timezone=ZONE,
        requested_latitude=LAT,
        requested_longitude=LON,
        received_at=AS_OF - 5.0,
    )


def _capture():
    compiled = _compiled(high=True)
    authority = compile_temperature_rule_authority(_nws_event(high=True, hourly=True), compiled)
    semantics = build_same_day_contract_semantics(compiled, authority)
    return assemble_same_day_capture(
        compiled=compiled,
        contract_semantics=semantics,
        station_metadata=_station_metadata(),
        wrh_snapshot=_snapshot(),
        near_term_raw_snapshot=_nws_snapshot(),
        hourly_gefs=_gefs(),
        as_of=AS_OF,
        mapping_policy=EnsembleMappingPolicy(
            policy_id="same-day-capture-test-v1", include_control=True
        ),
        # Deliberately leave the production scientific gate closed.
        population_alignment_certified=False,
    )


def test_silent_capture_collects_all_three_layer_preimages_but_blocks_uncertified_population():
    capture = _capture()
    verify_same_day_capture_record(capture)
    assert capture.status == SAME_DAY_CAPTURE_BLOCKED
    assert "WRH_TO_MODEL_POPULATION_ALIGNMENT_UNPROVEN" in capture.block_reasons
    assert capture.observed_state["extreme_value"] == 82.0
    assert capture.near_term_path is not None
    assert capture.remaining_hours_path is not None
    assert len(capture.hourly_gefs["member_series"]) == 31
    assert capture.final_decision is None
    assert capture.calibrated_probability is False
    assert capture.same_day_delivery_enabled is False
    assert capture.financial_authority is False


def test_blocked_capture_persists_without_creating_validated_paper_pnl(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    capture_store = SameDayCaptureStore(db)
    capture = _capture()
    row_id = capture_store.save(capture)
    assert row_id is not None
    assert capture_store.save(capture) is None
    summary = capture_store.summary()
    assert summary["total"] == 1
    assert summary["blocked"] == 1
    assert summary["ready_uncalibrated"] == 0
    assert summary["included_in_validated_pnl"] is False

    paper = CorrectiveWeatherPaperStore(db)
    stats = paper.stats()
    assert int(stats.get("open") or 0) == 0
    assert int(stats.get("resolved") or 0) == 0
    assert float(stats.get("pnl") or 0.0) == 0.0
