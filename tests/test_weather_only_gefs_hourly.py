from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from polymarket_scanner.weather_only_conditioned_extremes import TimeSegment
from polymarket_scanner.weather_only_contracts import DAILY_HIGH
from polymarket_scanner.weather_only_gefs_hourly import (
    GEFS_HOURLY_CELL_SELECTION,
    GEFS_HOURLY_PROVIDER_MODEL,
    GEFS_HOURLY_STEP_SECONDS,
    GEFS_HOURLY_TEMPORAL_RESOLUTION,
    GEFS_HOURLY_TIMEFORMAT,
    GEFSHourlyError,
    build_verified_gefs_path_from_hourly,
    parse_open_meteo_gefs_hourly_target_day,
    verify_gefs_hourly_evidence,
)


TARGET = date(2026, 9, 11)
DAY_START = datetime(2026, 9, 11, 0, 0, tzinfo=timezone.utc).timestamp()
RECEIVED = datetime(2026, 9, 11, 10, 5, tzinfo=timezone.utc).timestamp()
AS_OF = datetime(2026, 9, 11, 10, 6, tzinfo=timezone.utc).timestamp()
DAY_END = datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc).timestamp()


def _keys():
    return ("temperature_2m",) + tuple(
        f"temperature_2m_member{i:02d}" for i in range(1, 31)
    )


def _payload():
    times = [int(DAY_START + hour * GEFS_HOURLY_STEP_SECONDS) for hour in range(24)]
    hourly = {"time": times}
    units = {"time": GEFS_HOURLY_TIMEFORMAT}
    for member, key in enumerate(_keys()):
        hourly[key] = [60.0 + member * 0.1 + hour for hour in range(24)]
        units[key] = "°F"
    return {
        "latitude": 40.78,
        "longitude": -73.87,
        "timezone": "UTC",
        "hourly": hourly,
        "hourly_units": units,
    }


def _parse(payload=None, *, received_at=RECEIVED, **policy):
    return parse_open_meteo_gefs_hourly_target_day(
        payload or _payload(),
        station="KLGA",
        target_date=TARGET,
        unit="F",
        timezone="UTC",
        requested_latitude=40.7769,
        requested_longitude=-73.8740,
        received_at=received_at,
        **policy,
    )


def test_hourly_parser_preserves_all_31_member_paths_and_pinned_query_policy():
    distribution = _parse()
    assert len(distribution.member_labels) == 31
    assert len(distribution.member_series) == 31
    assert len(distribution.valid_times) == 24
    assert distribution.valid_times[0] == DAY_START
    assert distribution.valid_times[-1] + GEFS_HOURLY_STEP_SECONDS == DAY_END
    assert distribution.member_labels[0] == "control"
    assert distribution.member_labels[-1] == "member30"
    assert len(distribution.member_series[0].values) == 24
    assert distribution.provider_model == GEFS_HOURLY_PROVIDER_MODEL
    assert distribution.query_cell_selection == GEFS_HOURLY_CELL_SELECTION
    assert distribution.query_temporal_resolution == GEFS_HOURLY_TEMPORAL_RESOLUTION
    assert distribution.query_timeformat == GEFS_HOURLY_TIMEFORMAT
    assert len(distribution.content_run_id) == 64
    assert len(distribution.evidence_sha256) == 64
    assert distribution.calibrated_probability is False
    assert distribution.settlement_authority is False
    assert distribution.financial_authority is False
    assert verify_gefs_hourly_evidence(distribution) == distribution


def test_parser_rejects_unreviewed_model_or_query_policy():
    with pytest.raises(GEFSHourlyError, match="GEFS_HOURLY_PROVIDER_MODEL_MISMATCH"):
        _parse(provider_model="ncep_gefs_seamless")
    with pytest.raises(GEFSHourlyError, match="GEFS_HOURLY_CELL_SELECTION_MISMATCH"):
        _parse(query_cell_selection="land")
    with pytest.raises(GEFSHourlyError, match="GEFS_HOURLY_TEMPORAL_RESOLUTION_MISMATCH"):
        _parse(query_temporal_resolution="native")
    with pytest.raises(GEFSHourlyError, match="GEFS_HOURLY_TIMEFORMAT_MISMATCH"):
        _parse(query_timeformat="iso8601")


def test_content_identity_collapses_identical_retrievals_but_receipt_evidence_remains_distinct():
    first = _parse(received_at=RECEIVED)
    second = _parse(received_at=RECEIVED + 30)
    assert first.content_run_id == second.content_run_id
    assert first.evidence_sha256 != second.evidence_sha256


def test_one_member_value_change_creates_new_content_identity():
    first = _parse()
    payload = _payload()
    payload["hourly"]["temperature_2m_member30"][23] += 0.25
    second = _parse(payload)
    assert first.content_run_id != second.content_run_id
    assert first.evidence_sha256 != second.evidence_sha256


def test_policy_tampering_cannot_pass_evidence_verification():
    distribution = _parse()
    tampered = replace(distribution, query_temporal_resolution="native")
    with pytest.raises(GEFSHourlyError, match="GEFS_HOURLY_ADAPTER_IDENTITY_MISMATCH"):
        verify_gefs_hourly_evidence(tampered)


def test_future_u_t_projection_discards_elapsed_model_hours_before_conditioning():
    distribution = _parse()
    unresolved = (
        TimeSegment(
            datetime(2026, 9, 11, 11, 0, tzinfo=timezone.utc).timestamp(),
            datetime(2026, 9, 12, 0, 0, tzinfo=timezone.utc).timestamp(),
            "future target-day model grid",
        ),
    )
    verified = build_verified_gefs_path_from_hourly(
        distribution,
        family=DAILY_HIGH,
        as_of=AS_OF,
        target_end=DAY_END,
        unresolved_segments=unresolved,
    )
    assert verified.member_labels[0] == "control"
    assert len(verified.member_labels) == 31
    assert verified.expected_valid_times[0] == unresolved[0].start
    assert verified.expected_valid_times[-1] == datetime(
        2026, 9, 11, 23, 0, tzinfo=timezone.utc
    ).timestamp()
    # The control path rises hourly. If elapsed 00-10 model hours leaked into U(t),
    # the path lineage/count would differ; exact coverage contains only 11-23.
    assert verified.point_count == 31 * 13
    assert verified.ensemble.calibrated_probability is False
    assert verified.ensemble.financial_authority is False


def test_elapsed_unresolved_gap_cannot_borrow_a_model_retrieved_after_the_gap_started():
    distribution = _parse()
    unresolved = (
        TimeSegment(
            datetime(2026, 9, 11, 8, 0, tzinfo=timezone.utc).timestamp(),
            datetime(2026, 9, 11, 9, 0, tzinfo=timezone.utc).timestamp(),
            "official row publication delayed",
        ),
    )
    with pytest.raises(GEFSHourlyError) as raised:
        build_verified_gefs_path_from_hourly(
            distribution,
            family=DAILY_HIGH,
            as_of=AS_OF,
            target_end=DAY_END,
            unresolved_segments=unresolved,
        )
    assert (
        raised.value.code
        == "GEFS_HOURLY_PATH_INVALID:CONDITIONED_PATH_RUN_POSTDATES_UNRESOLVED_SEGMENT"
    )


def test_unit_member_and_grid_schema_drift_fail_closed():
    payload = _payload()
    payload["hourly_units"]["temperature_2m_member07"] = "°C"
    with pytest.raises(GEFSHourlyError) as unit:
        _parse(payload)
    assert unit.value.code == "GEFS_HOURLY_TEMPERATURE_UNIT_MISMATCH"

    payload = _payload()
    payload["hourly"].pop("temperature_2m_member07")
    with pytest.raises(GEFSHourlyError) as member:
        _parse(payload)
    assert member.value.code == "GEFS_HOURLY_MEMBER_SCHEMA_DRIFT"

    payload = _payload()
    payload["hourly"]["time"][10] += 1800
    with pytest.raises(GEFSHourlyError) as grid:
        _parse(payload)
    assert grid.value.code == "GEFS_HOURLY_GRID_NOT_EXACT_HOURLY"


def test_target_day_must_be_complete_not_truncated_to_remaining_hours_by_provider():
    payload = _payload()
    for values in payload["hourly"].values():
        if isinstance(values, list):
            del values[:2]
    with pytest.raises(GEFSHourlyError) as raised:
        _parse(payload)
    assert raised.value.code == "GEFS_HOURLY_TARGET_START_MISSING"


def test_tampered_dataclass_cannot_pass_digest_verification():
    distribution = _parse()
    tampered = replace(distribution, content_run_id="f" * 64)
    with pytest.raises(GEFSHourlyError) as raised:
        verify_gefs_hourly_evidence(tampered)
    assert raised.value.code == "GEFS_HOURLY_CONTENT_RUN_DIGEST_MISMATCH"


def test_lookup_u_t_grid_must_be_covered_by_returned_target_day():
    distribution = _parse()
    unresolved = (
        TimeSegment(DAY_END, DAY_END + GEFS_HOURLY_STEP_SECONDS, "outside target"),
    )
    with pytest.raises(GEFSHourlyError) as raised:
        build_verified_gefs_path_from_hourly(
            distribution,
            family=DAILY_HIGH,
            as_of=AS_OF,
            target_end=DAY_END + GEFS_HOURLY_STEP_SECONDS,
            unresolved_segments=unresolved,
        )
    assert raised.value.code == "GEFS_HOURLY_UNRESOLVED_GRID_NOT_COVERED"



def _dst_payload(times, timezone_name):
    hourly = {"time": list(times)}
    units = {"time": GEFS_HOURLY_TIMEFORMAT}
    for member, key in enumerate(_keys()):
        hourly[key] = [60.0 + member * 0.1 + index * 0.01 for index in range(len(times))]
        units[key] = "°F"
    return {
        "latitude": 40.78,
        "longitude": -73.87,
        "timezone": timezone_name,
        "hourly": hourly,
        "hourly_units": units,
    }


def _local_day_epochs(target: date, timezone_name: str) -> list[int]:
    zone = ZoneInfo(timezone_name)
    start = datetime(target.year, target.month, target.day, tzinfo=zone).timestamp()
    following = date.fromordinal(target.toordinal() + 1)
    end = datetime(following.year, following.month, following.day, tzinfo=zone).timestamp()
    return list(range(int(start), int(end), GEFS_HOURLY_STEP_SECONDS))


def test_spring_forward_23_hour_local_day_is_accepted_with_unambiguous_unix_instants():
    target = date(2026, 3, 8)
    times = _local_day_epochs(target, "America/New_York")
    assert len(times) == 23
    result = parse_open_meteo_gefs_hourly_target_day(
        _dst_payload(times, "America/New_York"),
        station="KLGA", target_date=target, unit="F", timezone="America/New_York",
        requested_latitude=40.7769, requested_longitude=-73.8740, received_at=RECEIVED,
    )
    assert len(result.valid_times) == 23
    assert all(
        after - before == GEFS_HOURLY_STEP_SECONDS
        for before, after in zip(result.valid_times, result.valid_times[1:])
    )


def test_fall_back_25_hour_day_accepts_both_repeated_wall_hours_as_distinct_epochs():
    target = date(2026, 11, 1)
    times = _local_day_epochs(target, "America/New_York")
    assert len(times) == 25
    result = parse_open_meteo_gefs_hourly_target_day(
        _dst_payload(times, "America/New_York"),
        station="KLGA", target_date=target, unit="F", timezone="America/New_York",
        requested_latitude=40.7769, requested_longitude=-73.8740, received_at=RECEIVED,
    )
    assert len(result.valid_times) == 25
    local_labels = [
        datetime.fromtimestamp(value, tz=timezone.utc)
        .astimezone(ZoneInfo("America/New_York"))
        .strftime("%Y-%m-%d %H:%M %z")
        for value in result.valid_times
    ]
    assert any("01:00 -0400" in value for value in local_labels)
    assert any("01:00 -0500" in value for value in local_labels)

    duplicate = list(times)
    duplicate[2] = duplicate[1]
    with pytest.raises(GEFSHourlyError, match="GEFS_HOURLY_TIME_INSTANT_DUPLICATE"):
        parse_open_meteo_gefs_hourly_target_day(
            _dst_payload(duplicate, "America/New_York"),
            station="KLGA", target_date=target, unit="F", timezone="America/New_York",
            requested_latitude=40.7769, requested_longitude=-73.8740, received_at=RECEIVED,
        )

# END_DST_TESTS
