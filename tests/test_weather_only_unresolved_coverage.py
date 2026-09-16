from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from polymarket_scanner.weather_only_unresolved_coverage import (
    UnresolvedCoverageError,
    build_unresolved_coverage_plan,
    local_day_bounds,
)


def _epoch_local(day: date, hour: int, minute: int, zone: str, *, fold: int = 0) -> float:
    return datetime(
        day.year, day.month, day.day, hour, minute,
        tzinfo=ZoneInfo(zone), fold=fold,
    ).timestamp()


def test_missing_elapsed_hour_remains_in_u_t_even_after_later_observation():
    day = date(2026, 9, 12)
    zone = "Europe/Berlin"
    # Visible official rows exist in 00, 01 and 03 local cells.  The 02 cell is
    # missing even though a later row exists; U(t) must retain that earlier hole.
    observations = [
        _epoch_local(day, 0, 51, zone),
        _epoch_local(day, 1, 51, zone),
        _epoch_local(day, 3, 51, zone),
    ]
    as_of = _epoch_local(day, 4, 20, zone)
    plan = build_unresolved_coverage_plan(
        station="EDDM",
        population_id="WRH_HOURLY_TEST",
        timezone=zone,
        target_date=day,
        as_of=as_of,
        accepted_observation_times=observations,
        population_alignment_certified=True,
    )
    assert plan.elapsed_gap_segments
    gaps = [(segment.start, segment.end, segment.reason) for segment in plan.elapsed_gap_segments]
    two = _epoch_local(day, 2, 0, zone)
    three = _epoch_local(day, 3, 0, zone)
    assert any(start <= two + 1e-6 and end >= three - 1e-6 for start, end, _ in gaps)
    assert plan.observation_grid_ready is False
    assert plan.near_term_segment is not None
    assert plan.near_term_segment.start == as_of
    assert plan.same_day_delivery_authority is False


def test_complete_visible_elapsed_grid_partitions_current_remainder_and_future_layer3():
    day = date(2026, 9, 12)
    zone = "America/New_York"
    as_of = _epoch_local(day, 4, 20, zone)
    observations = [_epoch_local(day, hour, 51, zone) for hour in range(4)]
    # Current 04:00-05:00 cell already has a visible row before the decision.
    observations.append(_epoch_local(day, 4, 10, zone))
    plan = build_unresolved_coverage_plan(
        station="KLGA",
        population_id="WRH_HOURLY_TEST",
        timezone=zone,
        target_date=day,
        as_of=as_of,
        accepted_observation_times=observations,
        population_alignment_certified=True,
    )
    assert plan.elapsed_gap_segments == ()
    assert plan.observation_grid_ready is True
    assert plan.near_term_segment is not None
    assert plan.near_term_segment.start == as_of
    assert plan.near_term_segment.end == _epoch_local(day, 5, 0, zone)
    assert plan.ensemble_segments
    assert plan.ensemble_segments[0].start == _epoch_local(day, 5, 0, zone)
    assert plan.ensemble_segments[-1].end == local_day_bounds(day, zone)[1]


def test_exact_grid_boundary_reserves_first_future_hour_for_layer2_not_layer3():
    day = date(2026, 9, 12)
    zone = "Asia/Tokyo"
    as_of = _epoch_local(day, 6, 0, zone)
    observations = [_epoch_local(day, hour, 30, zone) for hour in range(6)]
    plan = build_unresolved_coverage_plan(
        station="RJTT",
        population_id="TEST",
        timezone=zone,
        target_date=day,
        as_of=as_of,
        accepted_observation_times=observations,
        population_alignment_certified=True,
    )
    assert plan.elapsed_gap_segments == ()
    assert plan.near_term_segment is not None
    assert plan.near_term_segment.start == as_of
    assert plan.near_term_segment.end == _epoch_local(day, 7, 0, zone)
    assert plan.ensemble_segments[0].start == _epoch_local(day, 7, 0, zone)


def test_population_alignment_remains_explicit_scientific_gate():
    day = date(2026, 9, 12)
    zone = "UTC"
    as_of = datetime(2026, 9, 12, 3, 20, tzinfo=timezone.utc).timestamp()
    observations = [
        datetime(2026, 9, 12, hour, 51, tzinfo=timezone.utc).timestamp()
        for hour in range(3)
    ]
    observations.append(datetime(2026, 9, 12, 3, 10, tzinfo=timezone.utc).timestamp())
    plan = build_unresolved_coverage_plan(
        station="KAAA",
        population_id="UNVALIDATED_POPULATION",
        timezone=zone,
        target_date=day,
        as_of=as_of,
        accepted_observation_times=observations,
    )
    assert plan.elapsed_gap_segments == ()
    assert plan.population_alignment_certified is False
    assert plan.observation_grid_ready is False
    assert plan.same_day_delivery_authority is False
    assert plan.financial_authority is False


def test_dst_local_day_uses_23_or_25_distinct_utc_cells():
    spring = build_unresolved_coverage_plan(
        station="EDDM",
        population_id="TEST",
        timezone="Europe/Berlin",
        target_date=date(2026, 3, 29),
        as_of=_epoch_local(date(2026, 3, 29), 12, 0, "Europe/Berlin"),
        accepted_observation_times=[
            _epoch_local(date(2026, 3, 29), 0, 30, "Europe/Berlin")
        ],
    )
    assert spring.grid_cell_count == 23

    # The fall-back day contains two distinct 02:30 wall-clock observations. Epoch
    # identity, not the repeated label, keeps them separate.
    fall_day = date(2026, 10, 25)
    first_0230 = _epoch_local(fall_day, 2, 30, "Europe/Berlin", fold=0)
    second_0230 = _epoch_local(fall_day, 2, 30, "Europe/Berlin", fold=1)
    assert first_0230 != second_0230
    fall = build_unresolved_coverage_plan(
        station="EDDM",
        population_id="TEST",
        timezone="Europe/Berlin",
        target_date=fall_day,
        as_of=_epoch_local(fall_day, 12, 0, "Europe/Berlin"),
        accepted_observation_times=[first_0230, second_0230],
    )
    assert fall.grid_cell_count == 25
    assert fall.accepted_observation_count == 2


def test_future_or_duplicate_observation_fails_closed():
    day = date(2026, 9, 12)
    cutoff = datetime(2026, 9, 12, 4, 0, tzinfo=timezone.utc).timestamp()
    later = datetime(2026, 9, 12, 5, 0, tzinfo=timezone.utc).timestamp()
    with pytest.raises(UnresolvedCoverageError, match="UNRESOLVED_OBSERVATION_LOOKAHEAD"):
        build_unresolved_coverage_plan(
            station="KAAA",
            population_id="TEST",
            timezone="UTC",
            target_date=day,
            as_of=cutoff,
            accepted_observation_times=[later],
        )

    visible = datetime(2026, 9, 12, 1, 30, tzinfo=timezone.utc).timestamp()
    with pytest.raises(UnresolvedCoverageError, match="UNRESOLVED_OBSERVATION_DUPLICATE_INSTANT"):
        build_unresolved_coverage_plan(
            station="KAAA",
            population_id="TEST",
            timezone="UTC",
            target_date=day,
            as_of=cutoff,
            accepted_observation_times=[visible, visible],
        )


def test_evidence_digest_is_stable_and_changes_with_missing_hour():
    day = date(2026, 9, 12)
    cutoff = datetime(2026, 9, 12, 4, 20, tzinfo=timezone.utc).timestamp()
    rows = [
        datetime(2026, 9, 12, hour, 30, tzinfo=timezone.utc).timestamp()
        for hour in range(4)
    ]
    rows.append(datetime(2026, 9, 12, 4, 10, tzinfo=timezone.utc).timestamp())
    one = build_unresolved_coverage_plan(
        station="KAAA",
        population_id="TEST",
        timezone="UTC",
        target_date=day,
        as_of=cutoff,
        accepted_observation_times=rows,
        population_alignment_certified=True,
    )
    two = build_unresolved_coverage_plan(
        station="KAAA",
        population_id="TEST",
        timezone="UTC",
        target_date=day,
        as_of=cutoff,
        accepted_observation_times=rows,
        population_alignment_certified=True,
    )
    missing = build_unresolved_coverage_plan(
        station="KAAA",
        population_id="TEST",
        timezone="UTC",
        target_date=day,
        as_of=cutoff,
        accepted_observation_times=[rows[0], rows[1], rows[3], rows[4]],
        population_alignment_certified=True,
    )
    assert one.evidence_sha256 == two.evidence_sha256
    assert one.evidence_sha256 != missing.evidence_sha256
