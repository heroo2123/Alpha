from __future__ import annotations

from datetime import datetime, timezone

import pytest

from polymarket_scanner.weather_only_conditioned_extremes import DAILY_HIGH, TimeSegment
from polymarket_scanner.weather_only_near_term_path import verify_near_term_path_integrity
from polymarket_scanner.weather_only_nws_near_term import (
    NWSNearTermError,
    NWS_NEAR_TERM_HYPOTHESIS,
    NWS_NEAR_TERM_STEP_SECONDS,
    parse_nws_near_term_grid_path,
)


def _ts(hour: int, minute: int = 0) -> float:
    return datetime(2026, 9, 13, hour, minute, tzinfo=timezone.utc).timestamp()


def _points(url: str = "https://api.weather.gov/gridpoints/OKX/33,37") -> dict:
    return {
        "type": "Feature",
        "properties": {"forecastGridData": url},
    }


def _grid(*, uom: str = "wmoUnit:degC", update: str = "2026-09-13T09:00:00+00:00") -> dict:
    return {
        "type": "Feature",
        "properties": {
            "updateTime": update,
            "temperature": {
                "uom": uom,
                "values": [
                    {"validTime": "2026-09-13T10:00:00+00:00/PT1H", "value": 20.0},
                    {"validTime": "2026-09-13T11:00:00+00:00/PT1H", "value": 21.0},
                ],
            },
        },
    }


def _parse(points=None, grid=None):
    return parse_nws_near_term_grid_path(
        points or _points(),
        grid or _grid(),
        station="KLGA",
        latitude=40.7769,
        longitude=-73.8740,
        unit="F",
        family=DAILY_HIGH,
        segment=TimeSegment(_ts(10, 5), _ts(11), "near-term fractional ensemble cell"),
        received_at=_ts(10, 4),
    )


def test_nws_grid_interval_path_covers_entire_fractional_layer2_segment_without_authority_upgrade():
    path = _parse()
    verify_near_term_path_integrity(path)
    assert path.station == "KLGA"
    assert path.source_role == "OFFICIAL_NOWCAST_RESEARCH"
    assert path.sampling_step_seconds == NWS_NEAR_TERM_STEP_SECONDS
    assert path.sampling_hypothesis_id == NWS_NEAR_TERM_HYPOTHESIS
    assert path.points[0].valid_at == _ts(10, 5)
    assert path.points[-1].valid_at == _ts(11)
    # Endpoint is the left-hand limit of the half-open Layer-2 segment, so it retains
    # the 10:00-11:00 NWS grid value rather than borrowing the Layer-3 11:00 instant.
    assert all(point.value == pytest.approx(68.0) for point in path.points)
    assert path.sampled_extreme == pytest.approx(68.0)
    assert path.continuous_physical_extreme_certified is False
    assert path.calibrated_probability is False
    assert path.settlement_authority is False
    assert path.same_day_delivery_authority is False
    assert path.financial_authority is False


def test_grid_update_time_after_receipt_is_rejected_as_impossible_provenance():
    with pytest.raises(NWSNearTermError, match="NWS_NEAR_TERM_UPDATE_AFTER_RECEIPT"):
        _parse(grid=_grid(update="2026-09-13T10:10:00+00:00"))


def test_uncovered_near_term_sample_fails_closed_instead_of_interpolating_across_gap():
    grid = _grid()
    grid["properties"]["temperature"]["values"] = [
        {"validTime": "2026-09-13T10:00:00+00:00/PT15M", "value": 20.0},
        {"validTime": "2026-09-13T10:30:00+00:00/PT30M", "value": 20.5},
    ]
    with pytest.raises(NWSNearTermError, match="NWS_NEAR_TERM_SAMPLE_UNCOVERED"):
        _parse(grid=grid)


def test_redirected_or_non_nws_grid_identity_is_rejected():
    with pytest.raises(NWSNearTermError, match="NWS_NEAR_TERM_GRID_URL_INVALID"):
        _parse(points=_points("https://example.com/gridpoints/OKX/33,37"))


def test_temperature_unit_schema_drift_is_rejected():
    with pytest.raises(NWSNearTermError, match="NWS_NEAR_TERM_SOURCE_UNIT_UNSUPPORTED"):
        _parse(grid=_grid(uom="wmoUnit:K"))


def test_overlapping_grid_intervals_are_rejected_not_last_write_wins():
    grid = _grid()
    grid["properties"]["temperature"]["values"] = [
        {"validTime": "2026-09-13T10:00:00+00:00/PT1H", "value": 20.0},
        {"validTime": "2026-09-13T10:30:00+00:00/PT1H", "value": 21.0},
    ]
    with pytest.raises(NWSNearTermError, match="NWS_NEAR_TERM_INTERVAL_OVERLAP"):
        _parse(grid=grid)
