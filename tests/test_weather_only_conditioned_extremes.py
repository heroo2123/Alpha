from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_conditioned_extremes import (
    CONDITIONED_EXTREME_VERSION,
    OFFICIAL_NOWCAST_ROLE,
    PWS_ROLE,
    ConditionedExtremeError,
    NearTermEvidence,
    OfficialObservation,
    RemainingMemberExtreme,
    TimeSegment,
    build_observed_extreme,
    build_remaining_hours_ensemble,
    condition_extremes,
)
from polymarket_scanner.weather_only_contracts import DAILY_HIGH, DAILY_LOW


START = 1_000.0
AS_OF = 2_000.0
END = 3_000.0


def _obs(value: float, *, station: str = "EDDM", unit: str = "C", population: str = "WRH_ALL_TIMES"):
    return OfficialObservation(
        station=station,
        population_id=population,
        unit=unit,
        observed_at=1_500.0,
        received_at=1_510.0,
        value=value,
        source_revision="rev-1",
    )


def _observed(family: str, value: float = 7.0):
    return build_observed_extreme(
        [_obs(value)],
        station="EDDM",
        population_id="WRH_ALL_TIMES",
        unit="C",
        family=family,
        target_start=START,
        as_of=AS_OF,
    )


def _remaining(family: str, values: tuple[float, ...]):
    return build_remaining_hours_ensemble(
        station="EDDM",
        unit="C",
        family=family,
        issued_at=1_800.0,
        received_at=1_900.0,
        target_end=END,
        unresolved_segments=(TimeSegment(AS_OF, END, "future"),),
        members=tuple(RemainingMemberExtreme(f"m{i}", value) for i, value in enumerate(values)),
        coverage_complete=True,
        provider="fixture",
        provider_run_id="run-1",
    )


def test_observed_low_is_a_hard_upper_bound_on_every_final_member_low():
    result = condition_extremes(_observed(DAILY_LOW, 7.0), _remaining(DAILY_LOW, (11.0, 9.0, 8.0)), as_of=AS_OF)
    assert result.version == CONDITIONED_EXTREME_VERSION
    assert result.observed_extreme == 7.0
    assert result.final_member_extremes == (7.0, 7.0, 7.0)
    assert all(value <= 7.0 for value in result.final_member_extremes)


def test_observed_low_does_not_make_exact_low_certain_when_remaining_day_can_go_lower():
    result = condition_extremes(_observed(DAILY_LOW, 7.0), _remaining(DAILY_LOW, (8.0, 7.0, 6.0)), as_of=AS_OF)
    assert result.final_member_extremes == (7.0, 7.0, 6.0)


def test_observed_high_is_a_hard_lower_bound_on_every_final_member_high():
    result = condition_extremes(_observed(DAILY_HIGH, 31.0), _remaining(DAILY_HIGH, (28.0, 30.0, 33.0)), as_of=AS_OF)
    assert result.final_member_extremes == (31.0, 31.0, 33.0)
    assert all(value >= 31.0 for value in result.final_member_extremes)


def test_official_observation_identity_and_as_of_fail_closed():
    with pytest.raises(ConditionedExtremeError, match="CONDITIONED_OBSERVATION_STATION_MISMATCH"):
        build_observed_extreme(
            [_obs(7.0, station="KDFW")],
            station="EDDM",
            population_id="WRH_ALL_TIMES",
            unit="C",
            family=DAILY_LOW,
            target_start=START,
            as_of=AS_OF,
        )

    future = OfficialObservation(
        station="EDDM",
        population_id="WRH_ALL_TIMES",
        unit="C",
        observed_at=1_900.0,
        received_at=2_100.0,
        value=6.0,
        source_revision="future",
    )
    with pytest.raises(ConditionedExtremeError, match="CONDITIONED_OBSERVATION_LOOKAHEAD"):
        build_observed_extreme(
            [future],
            station="EDDM",
            population_id="WRH_ALL_TIMES",
            unit="C",
            family=DAILY_LOW,
            target_start=START,
            as_of=AS_OF,
        )


def test_missing_unresolved_coverage_is_not_silently_treated_as_known():
    with pytest.raises(ConditionedExtremeError, match="CONDITIONED_UNRESOLVED_COVERAGE_INCOMPLETE"):
        build_remaining_hours_ensemble(
            station="EDDM",
            unit="C",
            family=DAILY_LOW,
            issued_at=1_800.0,
            received_at=1_900.0,
            target_end=END,
            unresolved_segments=(TimeSegment(AS_OF, END, "future"),),
            members=(RemainingMemberExtreme("control", 6.0),),
            coverage_complete=False,
            provider="fixture",
            provider_run_id="run-1",
        )


def test_near_term_pws_and_official_nowcast_are_diagnostics_not_settlement_authority():
    pws = NearTermEvidence(
        station="EDDM",
        unit="C",
        issued_at=1_850.0,
        received_at=1_900.0,
        predicted_extreme=4.0,
        horizon_end=2_300.0,
        source_role=PWS_ROLE,
        source_id="PWS-1",
    )
    official_nowcast = NearTermEvidence(
        station="EDDM",
        unit="C",
        issued_at=1_850.0,
        received_at=1_900.0,
        predicted_extreme=5.0,
        horizon_end=2_300.0,
        source_role=OFFICIAL_NOWCAST_ROLE,
        source_id="NOWCAST-1",
    )
    result = condition_extremes(
        _observed(DAILY_LOW, 7.0),
        _remaining(DAILY_LOW, (8.0, 9.0, 10.0)),
        as_of=AS_OF,
        near_term=((pws, 2.0), (official_nowcast, 2.0)),
    )
    # Layer 2 can veto/diagnose later, but does not silently overwrite layer 1/3.
    assert result.final_member_extremes == (7.0, 7.0, 7.0)
    assert all(row["settlement_authority"] is False for row in result.near_term_diagnostics)
    assert all(row["financial_authority"] is False for row in result.near_term_diagnostics)
    assert all(row["material_conflict"] is True for row in result.near_term_diagnostics)
