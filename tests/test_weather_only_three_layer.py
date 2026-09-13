from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from polymarket_scanner.weather_only_conditioned_extremes import (
    NearTermEvidence,
    OfficialObservation,
    PWS_ROLE,
    build_observed_extreme,
)
from polymarket_scanner.weather_only_conditioned_paths import (
    MemberPathPoint,
    build_gefs_remaining_hours_path,
)
from polymarket_scanner.weather_only_contracts import (
    DAILY_LOW,
    SOURCE_NWS_WRH,
    CompiledWeatherEvent,
    WeatherBucket,
)
from polymarket_scanner.weather_only_forecast import EnsembleMappingPolicy
from polymarket_scanner.weather_only_near_term import (
    NearTermCoverageError,
    verify_near_term_segment_coverage,
)
from polymarket_scanner.weather_only_three_layer import (
    ThreeLayerError,
    build_three_layer_research_decision,
    verify_three_layer_decision_integrity,
)
from polymarket_scanner.weather_only_unresolved_coverage import build_unresolved_coverage_plan


def _ts(hour: int, minute: int = 0) -> float:
    base = datetime(2026, 9, 12, tzinfo=timezone.utc)
    return (base + timedelta(hours=hour, minutes=minute)).timestamp()


def _compiled() -> CompiledWeatherEvent:
    buckets = (
        WeatherBucket("m-le6", "c-le6", "6 F or lower", "le6", None, 6.0, "F", "y-le6", "n-le6", True),
        WeatherBucket("m7", "c7", "7 F", "7", 7.0, 7.0, "F", "y7", "n7", True),
        WeatherBucket("m8", "c8", "8 F", "8", 8.0, 8.0, "F", "y8", "n8", True),
        WeatherBucket("m-ge9", "c-ge9", "9 F or higher", "ge9", 9.0, None, "F", "y-ge9", "n-ge9", True),
    )
    return CompiledWeatherEvent(
        compiler_version="fixture",
        event_id="event-low",
        event_slug="event-low",
        title="Lowest temperature fixture",
        family=DAILY_LOW,
        target_date=date(2026, 9, 12),
        unit="F",
        source_family=SOURCE_NWS_WRH,
        source_urls=("https://weather.gov/wrh/timeseries",),
        station_hint="KAAA",
        buckets=buckets,
        partition_shape_complete=True,
        exactly_one_outcome_proven=True,
        shadow_supported=True,
        financial_authority=False,
        rejection_reasons=(),
    )


def _observed(as_of: float, population: str = "WRH_TEST"):
    values = (10.0, 9.0, 8.0, 7.0, 8.0)
    rows = tuple(
        OfficialObservation(
            station="KAAA",
            population_id=population,
            unit="F",
            observed_at=_ts(hour, 10 if hour == 4 else 30),
            received_at=as_of - 5.0,
            value=value,
            source_revision=f"revision-{hour}",
        )
        for hour, value in enumerate(values)
    )
    return build_observed_extreme(
        rows,
        station="KAAA",
        population_id=population,
        unit="F",
        family=DAILY_LOW,
        target_start=_ts(0),
        as_of=as_of,
    )


def _coverage(as_of: float, *, alignment: bool = True, missing_hour: int | None = None):
    times = [
        _ts(hour, 10 if hour == 4 else 30)
        for hour in range(5)
        if hour != missing_hour
    ]
    return build_unresolved_coverage_plan(
        station="KAAA",
        population_id="WRH_TEST",
        timezone="UTC",
        target_date=date(2026, 9, 12),
        as_of=as_of,
        accepted_observation_times=times,
        population_alignment_certified=alignment,
    )


def _near_term(coverage, as_of: float):
    raw = NearTermEvidence(
        station="KAAA",
        unit="F",
        issued_at=as_of - 60.0,
        received_at=as_of - 30.0,
        predicted_extreme=8.0,
        horizon_end=coverage.near_term_segment.end,
        source_role=PWS_ROLE,
        source_id="pws-fixture-reviewed-adapter",
    )
    return verify_near_term_segment_coverage(
        raw,
        family=DAILY_LOW,
        as_of=as_of,
        segment=coverage.near_term_segment,
        source_evidence_sha256="a" * 64,
        coverage_method="FULL_SEGMENT_EXTREME_FORECAST_FIXTURE",
        full_segment_extreme_certified=True,
    )


def _ensemble_path(coverage, as_of: float):
    assert coverage.ensemble_segments
    valid_times = list(range(int(_ts(5)), int(_ts(24)), 3600))
    points = []
    for index in range(31):
        label = "control" if index == 0 else f"member{index:02d}"
        # First 11 members retain a possible unresolved low of 6; the other 20 stay
        # above the already-observed 7. Every member has the complete future path.
        value = 6.0 if index < 11 else 10.0
        points.extend(MemberPathPoint(label, float(valid), value) for valid in valid_times)
    return build_gefs_remaining_hours_path(
        station="KAAA",
        unit="F",
        family=DAILY_LOW,
        as_of=as_of,
        issued_at=as_of - 120.0,
        received_at=as_of - 10.0,
        target_end=_ts(24),
        unresolved_segments=coverage.ensemble_segments,
        points=tuple(points),
        provider_run_id="fixture-gefs-run",
        grid_step_seconds=3600,
        grid_anchor=_ts(0),
    )


def test_three_layers_condition_each_member_on_observed_reality_and_map_exact_buckets():
    as_of = _ts(4, 20)
    observed = _observed(as_of)
    coverage = _coverage(as_of)
    near = _near_term(coverage, as_of)
    path = _ensemble_path(coverage, as_of)
    decision = build_three_layer_research_decision(
        _compiled(),
        observed,
        coverage,
        near,
        path,
        EnsembleMappingPolicy(policy_id="fixture-map", include_control=True),
        as_of=as_of,
    )
    verify_three_layer_decision_integrity(decision)
    assert decision.member_count == 31
    assert decision.distribution.observed_extreme == 7.0
    assert set(decision.distribution.final_member_extremes) == {6.0, 7.0}
    assert max(decision.distribution.final_member_extremes) <= 7.0
    rows = {row.market_id: row for row in decision.bucket_frequencies}
    assert rows["m-le6"].member_hits == 11
    assert rows["m7"].member_hits == 20
    assert rows["m8"].member_hits == 0
    assert rows["m-ge9"].member_hits == 0
    assert abs(decision.probability_sum - 1.0) < 1e-12
    assert decision.calibrated_probability is False
    assert decision.same_day_delivery_enabled is False
    assert decision.financial_authority is False


def test_missing_elapsed_observation_hole_blocks_three_layer_assembly():
    as_of = _ts(4, 20)
    observed = _observed(as_of)
    coverage = _coverage(as_of, missing_hour=2)
    near = _near_term(coverage, as_of)
    path = _ensemble_path(coverage, as_of)
    with pytest.raises(ThreeLayerError, match="THREE_LAYER_ELAPSED_OBSERVATION_GAP"):
        build_three_layer_research_decision(
            _compiled(), observed, coverage, near, path,
            EnsembleMappingPolicy(policy_id="fixture-map", include_control=True),
            as_of=as_of,
        )


def test_unproven_population_to_grid_mapping_blocks_assembly():
    as_of = _ts(4, 20)
    observed = _observed(as_of)
    coverage = _coverage(as_of, alignment=False)
    near = _near_term(coverage, as_of)
    path = _ensemble_path(coverage, as_of)
    with pytest.raises(ThreeLayerError, match="THREE_LAYER_POPULATION_ALIGNMENT_UNPROVEN"):
        build_three_layer_research_decision(
            _compiled(), observed, coverage, near, path,
            EnsembleMappingPolicy(policy_id="fixture-map", include_control=True),
            as_of=as_of,
        )


def test_current_pws_reading_cannot_be_mislabeled_full_segment_forecast():
    as_of = _ts(4, 20)
    coverage = _coverage(as_of)
    raw = NearTermEvidence(
        station="KAAA",
        unit="F",
        issued_at=as_of - 30,
        received_at=as_of - 10,
        predicted_extreme=8.0,
        horizon_end=coverage.near_term_segment.end,
        source_role=PWS_ROLE,
        source_id="current-reading-only",
    )
    with pytest.raises(NearTermCoverageError, match="NEAR_TERM_FULL_SEGMENT_COVERAGE_UNPROVEN"):
        verify_near_term_segment_coverage(
            raw,
            family=DAILY_LOW,
            as_of=as_of,
            segment=coverage.near_term_segment,
            source_evidence_sha256="b" * 64,
            coverage_method="CURRENT_READING_ONLY",
            full_segment_extreme_certified=False,
        )


def test_near_term_digest_tampering_is_rejected_before_assembly():
    as_of = _ts(4, 20)
    observed = _observed(as_of)
    coverage = _coverage(as_of)
    near = _near_term(coverage, as_of)
    tampered = replace(near, predicted_extreme=2.0)
    path = _ensemble_path(coverage, as_of)
    with pytest.raises(ThreeLayerError, match="THREE_LAYER_NEAR_TERM_INVALID:NEAR_TERM_EVIDENCE_DIGEST_MISMATCH"):
        build_three_layer_research_decision(
            _compiled(), observed, coverage, tampered, path,
            EnsembleMappingPolicy(policy_id="fixture-map", include_control=True),
            as_of=as_of,
        )
