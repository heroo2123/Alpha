from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_conditioned_extremes import (
    OfficialObservation,
    TimeSegment,
    build_observed_extreme,
)
from polymarket_scanner.weather_only_conditioned_paths import (
    ConditionedPathError,
    GEFS_EXPECTED_MEMBER_COUNT,
    MemberPathPoint,
    PathCoveragePolicy,
    build_gefs_remaining_hours_path,
    build_verified_remaining_hours_path,
    condition_extremes_from_verified_path,
)
from polymarket_scanner.weather_only_contracts import DAILY_HIGH


HOUR = 3600.0
AS_OF = 5 * HOUR
TARGET_END = 10 * HOUR


def _observed():
    rows = (
        OfficialObservation(
            station="KLGA",
            population_id="WRH_HOURLY_DATA",
            unit="F",
            observed_at=1 * HOUR,
            received_at=1.1 * HOUR,
            value=72.0,
            source_revision="r1",
        ),
        OfficialObservation(
            station="KLGA",
            population_id="WRH_HOURLY_DATA",
            unit="F",
            observed_at=4 * HOUR,
            received_at=4.1 * HOUR,
            value=80.0,
            source_revision="r2",
        ),
    )
    return build_observed_extreme(
        rows,
        station="KLGA",
        population_id="WRH_HOURLY_DATA",
        unit="F",
        family=DAILY_HIGH,
        target_start=0.0,
        as_of=AS_OF,
    )


def _policy(count: int = 3):
    return PathCoveragePolicy(
        policy_id="TEST_HOURLY_GRID_V1",
        grid_step_seconds=int(HOUR),
        grid_anchor=0.0,
        expected_member_count=count,
    )


def _points():
    return (
        MemberPathPoint("m0", 6 * HOUR, 75.0),
        MemberPathPoint("m0", 7 * HOUR, 77.0),
        MemberPathPoint("m0", 8 * HOUR, 79.0),
        MemberPathPoint("m1", 6 * HOUR, 81.0),
        MemberPathPoint("m1", 7 * HOUR, 82.0),
        MemberPathPoint("m1", 8 * HOUR, 81.0),
        MemberPathPoint("m2", 6 * HOUR, 83.0),
        MemberPathPoint("m2", 7 * HOUR, 85.0),
        MemberPathPoint("m2", 8 * HOUR, 84.0),
    )


def _verified(points=None, *, segments=None, issued_at=4 * HOUR):
    return build_verified_remaining_hours_path(
        station="KLGA",
        unit="F",
        family=DAILY_HIGH,
        as_of=AS_OF,
        issued_at=issued_at,
        received_at=4.5 * HOUR,
        target_end=TARGET_END,
        unresolved_segments=segments or (TimeSegment(6 * HOUR, 9 * HOUR, "future"),),
        points=points or _points(),
        provider="fixture-ensemble",
        provider_run_id="run-001",
        policy=_policy(),
    )


def test_full_member_paths_are_reduced_only_after_exact_unresolved_grid_coverage():
    verified = _verified()
    assert verified.coverage_complete is True
    assert verified.expected_valid_times == (6 * HOUR, 7 * HOUR, 8 * HOUR)
    assert verified.member_labels == ("m0", "m1", "m2")
    assert verified.point_count == 9
    assert len(verified.path_evidence_sha256) == 64
    assert f"path_sha256={verified.path_evidence_sha256}" in verified.ensemble.provider_run_id
    assert tuple(member.extreme_value for member in verified.ensemble.members) == (79.0, 82.0, 85.0)
    assert verified.ensemble.calibrated_probability is False
    assert verified.ensemble.settlement_authority is False
    assert verified.ensemble.financial_authority is False

    result = condition_extremes_from_verified_path(_observed(), verified, as_of=AS_OF)
    # max(observed=80, each unresolved member extreme)
    assert result.distribution.final_member_extremes == (80.0, 82.0, 85.0)
    assert result.remaining_path_evidence_sha256 == verified.path_evidence_sha256
    assert result.calibrated_probability is False
    assert result.settlement_authority is False
    assert result.financial_authority is False


def test_one_missing_member_hour_fails_closed_instead_of_renormalizing_member_frequency():
    points = tuple(row for row in _points() if not (row.member_label == "m2" and row.valid_at == 7 * HOUR))
    with pytest.raises(ConditionedPathError) as raised:
        _verified(points)
    assert raised.value.code == "CONDITIONED_PATH_MEMBER_GRID_INCOMPLETE"


def test_elapsed_model_value_outside_u_t_cannot_survive_into_member_extreme():
    points = _points() + (MemberPathPoint("m0", 5 * HOUR, 99.0),)
    with pytest.raises(ConditionedPathError) as raised:
        _verified(points)
    assert raised.value.code == "CONDITIONED_PATH_POINT_OUTSIDE_UNRESOLVED_GRID"


def test_duplicate_member_valid_time_is_rejected():
    points = _points() + (MemberPathPoint("m0", 6 * HOUR, 76.0),)
    with pytest.raises(ConditionedPathError) as raised:
        _verified(points)
    assert raised.value.code == "CONDITIONED_PATH_DUPLICATE_MEMBER_TIME"


def test_member_count_is_frozen_and_missing_member_cannot_be_silently_accepted():
    points = tuple(row for row in _points() if row.member_label != "m2")
    with pytest.raises(ConditionedPathError) as raised:
        _verified(points)
    assert raised.value.code == "CONDITIONED_PATH_MEMBER_COUNT_MISMATCH"


def test_past_unresolved_segment_requires_a_run_that_predates_that_segment():
    segment = (TimeSegment(3 * HOUR, 4 * HOUR, "official observation delayed"),)
    points = (
        MemberPathPoint("m0", 3 * HOUR, 74.0),
        MemberPathPoint("m1", 3 * HOUR, 75.0),
        MemberPathPoint("m2", 3 * HOUR, 76.0),
    )
    with pytest.raises(ConditionedPathError) as raised:
        _verified(points, segments=segment, issued_at=3.5 * HOUR)
    assert raised.value.code == "CONDITIONED_PATH_RUN_POSTDATES_UNRESOLVED_SEGMENT"


def test_past_unresolved_segment_can_use_a_prospective_run_that_already_existed():
    segment = (TimeSegment(3 * HOUR, 4 * HOUR, "official observation delayed"),)
    points = (
        MemberPathPoint("m0", 3 * HOUR, 74.0),
        MemberPathPoint("m1", 3 * HOUR, 75.0),
        MemberPathPoint("m2", 3 * HOUR, 76.0),
    )
    verified = _verified(points, segments=segment, issued_at=2 * HOUR)
    assert verified.expected_valid_times == (3 * HOUR,)
    assert tuple(member.extreme_value for member in verified.ensemble.members) == (74.0, 75.0, 76.0)


def test_non_grid_aligned_unresolved_segment_is_rejected_not_rounded():
    with pytest.raises(ConditionedPathError) as raised:
        _verified(segments=(TimeSegment(6 * HOUR + 60, 9 * HOUR, "misaligned"),))
    assert raised.value.code == "CONDITIONED_PATH_SEGMENT_NOT_GRID_ALIGNED"


def test_future_received_model_run_is_lookahead_and_rejected():
    with pytest.raises(ConditionedPathError) as raised:
        build_verified_remaining_hours_path(
            station="KLGA",
            unit="F",
            family=DAILY_HIGH,
            as_of=AS_OF,
            issued_at=4 * HOUR,
            received_at=AS_OF + 1,
            target_end=TARGET_END,
            unresolved_segments=(TimeSegment(6 * HOUR, 9 * HOUR, "future"),),
            points=_points(),
            provider="fixture-ensemble",
            provider_run_id="future-run",
            policy=_policy(),
        )
    assert raised.value.code == "CONDITIONED_PATH_TIME_ORDER_INVALID"


def test_path_digest_changes_when_one_member_value_changes():
    first = _verified()
    changed = list(_points())
    changed[-1] = MemberPathPoint("m2", 8 * HOUR, 84.5)
    second = _verified(tuple(changed))
    assert first.path_evidence_sha256 != second.path_evidence_sha256
    assert first.ensemble.evidence_sha256 != second.ensemble.evidence_sha256


def test_gefs_wrapper_requires_all_31_members():
    assert GEFS_EXPECTED_MEMBER_COUNT == 31
    points = tuple(MemberPathPoint(f"m{i:02d}", 6 * HOUR, 70.0 + i) for i in range(31))
    verified = build_gefs_remaining_hours_path(
        station="KLGA",
        unit="F",
        family=DAILY_HIGH,
        as_of=AS_OF,
        issued_at=4 * HOUR,
        received_at=4.5 * HOUR,
        target_end=8 * HOUR,
        unresolved_segments=(TimeSegment(6 * HOUR, 7 * HOUR, "future"),),
        points=points,
        provider_run_id="gefs-run-001",
        grid_step_seconds=int(HOUR),
        grid_anchor=0.0,
    )
    assert len(verified.member_labels) == 31

    with pytest.raises(ConditionedPathError) as raised:
        build_gefs_remaining_hours_path(
            station="KLGA",
            unit="F",
            family=DAILY_HIGH,
            as_of=AS_OF,
            issued_at=4 * HOUR,
            received_at=4.5 * HOUR,
            target_end=8 * HOUR,
            unresolved_segments=(TimeSegment(6 * HOUR, 7 * HOUR, "future"),),
            points=points[:-1],
            provider_run_id="gefs-run-001",
            grid_step_seconds=int(HOUR),
            grid_anchor=0.0,
        )
    assert raised.value.code == "CONDITIONED_PATH_MEMBER_COUNT_MISMATCH"
