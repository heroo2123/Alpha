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
from polymarket_scanner.weather_only_contracts import (
    DAILY_LOW,
    SOURCE_NWS_WRH,
    CompiledWeatherEvent,
    WeatherBucket,
)
from polymarket_scanner.weather_only_forecast import EnsembleMappingPolicy
from polymarket_scanner.weather_only_gefs_hourly import (
    build_verified_gefs_path_from_hourly,
    parse_open_meteo_gefs_hourly_target_day,
)
from polymarket_scanner.weather_only_near_term import verify_near_term_segment_coverage
from polymarket_scanner.weather_only_rules import TemperatureRuleAuthority
from polymarket_scanner.weather_only_same_day_contract import build_same_day_contract_semantics
from polymarket_scanner.weather_only_same_day_envelope import (
    SameDayEnvelopeError,
    build_same_day_evidence_envelope,
    canonical_evidence_sha256,
    verify_same_day_evidence_envelope,
)
from polymarket_scanner.weather_only_three_layer import build_three_layer_research_decision
from polymarket_scanner.weather_only_unresolved_coverage import build_unresolved_coverage_plan


TARGET = date(2026, 9, 12)
POPULATION = "WRH_HOURLY_DATA"


def _ts(hour: int, minute: int = 0) -> float:
    base = datetime(2026, 9, 12, tzinfo=timezone.utc)
    return (base + timedelta(hours=hour, minutes=minute)).timestamp()


def _compiled() -> CompiledWeatherEvent:
    return CompiledWeatherEvent(
        compiler_version="fixture",
        event_id="event-low",
        event_slug="event-low",
        title="Lowest temperature fixture",
        family=DAILY_LOW,
        target_date=TARGET,
        unit="F",
        source_family=SOURCE_NWS_WRH,
        source_urls=("https://weather.gov/wrh/timeseries?site=KAAA",),
        station_hint="KAAA",
        buckets=(
            WeatherBucket("m-le6", "c-le6", "6 F or lower", "le6", None, 6.0, "F", "y-le6", "n-le6", True),
            WeatherBucket("m7", "c7", "7 F", "7", 7.0, 7.0, "F", "y7", "n7", True),
            WeatherBucket("m8", "c8", "8 F", "8", 8.0, 8.0, "F", "y8", "n8", True),
            WeatherBucket("m-ge9", "c-ge9", "9 F or higher", "ge9", 9.0, None, "F", "y-ge9", "n-ge9", True),
        ),
        partition_shape_complete=True,
        exactly_one_outcome_proven=True,
        shadow_supported=True,
        financial_authority=False,
        rejection_reasons=(),
    )


def _semantics(compiled: CompiledWeatherEvent):
    authority = TemperatureRuleAuthority(
        version="rules-v-test",
        profile="NWS_WRH_DAILY_EXTREME_CURRENT_TEMPLATE_V1",
        family=DAILY_LOW,
        source_family=SOURCE_NWS_WRH,
        statistic="DAILY_LOWEST_TEMP",
        observation_population=POPULATION,
        precision="WHOLE_DEGREE_F",
        fallback_policy="WEATHER_UNDERGROUND_IF_WRH_UNAVAILABLE_BY_NEXT_DAY_2359_ET",
        finality_policy="FIRST_FOLLOWING_DATE_DATAPOINT_OR_NEXT_DAY_2359_ET",
        correction_policy="ACCEPT_REVISIONS_UNTIL_FIRST_FOLLOWING_DATE_DATAPOINT",
        no_data_outcome="LOWEST_BRACKET",
        rule_semantics_proven=True,
        exactly_one_outcome_proven=True,
        settlement_value_adapter_ready=False,
        financial_authority=False,
        rejection_reasons=(),
    )
    return build_same_day_contract_semantics(compiled, authority)


def _official_rows(as_of: float) -> tuple[OfficialObservation, ...]:
    values = (10.0, 9.0, 8.0, 7.0, 8.0)
    return tuple(
        OfficialObservation(
            station="KAAA",
            population_id=POPULATION,
            unit="F",
            observed_at=_ts(hour, 10 if hour == 4 else 30),
            received_at=as_of - 5.0,
            value=value,
            source_revision=f"wrh-visible-version-{hour}",
        )
        for hour, value in enumerate(values)
    )


def _gefs_payload() -> dict:
    times = [f"2026-09-12T{hour:02d}:00" for hour in range(24)]
    hourly = {"time": times}
    units = {"time": "iso8601"}
    keys = ("temperature_2m",) + tuple(
        f"temperature_2m_member{index:02d}" for index in range(1, 31)
    )
    for index, key in enumerate(keys):
        # control + members01..10 retain a possible unresolved low of 6.
        value = 6.0 if index < 11 else 10.0
        hourly[key] = [value for _ in range(24)]
        units[key] = "°F"
    return {
        "latitude": 40.0,
        "longitude": -73.0,
        "timezone": "UTC",
        "hourly": hourly,
        "hourly_units": units,
    }


def _bundle_inputs():
    as_of = _ts(4, 20)
    compiled = _compiled()
    semantics = _semantics(compiled)
    rows = _official_rows(as_of)
    observed = build_observed_extreme(
        rows,
        station="KAAA",
        population_id=POPULATION,
        unit="F",
        family=DAILY_LOW,
        target_start=_ts(0),
        as_of=as_of,
    )
    coverage = build_unresolved_coverage_plan(
        station="KAAA",
        population_id=POPULATION,
        timezone="UTC",
        target_date=TARGET,
        as_of=as_of,
        accepted_observation_times=[row.observed_at for row in rows],
        population_alignment_certified=True,
    )
    near_raw = {
        "provider": "fixture-near-term",
        "station": "KAAA",
        "issued_at": as_of - 60.0,
        "received_at": as_of - 30.0,
        "segment_start": coverage.near_term_segment.start,
        "segment_end": coverage.near_term_segment.end,
        "predicted_low_f": 8.0,
    }
    near = verify_near_term_segment_coverage(
        NearTermEvidence(
            station="KAAA",
            unit="F",
            issued_at=as_of - 60.0,
            received_at=as_of - 30.0,
            predicted_extreme=8.0,
            horizon_end=coverage.near_term_segment.end,
            source_role=PWS_ROLE,
            source_id="fixture-near-term",
        ),
        family=DAILY_LOW,
        as_of=as_of,
        segment=coverage.near_term_segment,
        source_evidence_sha256=canonical_evidence_sha256(near_raw),
        coverage_method="FULL_SEGMENT_EXTREME_FORECAST_FIXTURE",
        full_segment_extreme_certified=True,
    )
    hourly = parse_open_meteo_gefs_hourly_target_day(
        _gefs_payload(),
        station="KAAA",
        target_date=TARGET,
        unit="F",
        timezone="UTC",
        requested_latitude=40.0,
        requested_longitude=-73.0,
        received_at=as_of - 10.0,
    )
    path = build_verified_gefs_path_from_hourly(
        hourly,
        family=DAILY_LOW,
        as_of=as_of,
        target_end=coverage.target_end,
        unresolved_segments=coverage.ensemble_segments,
    )
    mapping = EnsembleMappingPolicy(policy_id="fixture-map", include_control=True)
    decision = build_three_layer_research_decision(
        compiled,
        observed,
        coverage,
        near,
        path,
        mapping,
        as_of=as_of,
    )
    return compiled, semantics, rows, coverage, near_raw, near, hourly, path, mapping, decision, as_of


def _make_envelope(inputs, *, near_raw_override=None):
    compiled, semantics, rows, coverage, near_raw, near, hourly, path, mapping, decision, as_of = inputs
    return build_same_day_evidence_envelope(
        compiled=compiled,
        contract_semantics=semantics,
        mapping_policy=mapping,
        official_observations=rows,
        coverage=coverage,
        near_term_raw_evidence=near_raw if near_raw_override is None else near_raw_override,
        near_term=near,
        hourly_gefs=hourly,
        remaining_path=path,
        decision=decision,
        release_sha="a" * 64,
        config_sha256="b" * 64,
        execution_protocol_id="SAME_DAY_RESEARCH_ONLY_V1",
        created_at=as_of,
    )


def test_envelope_archives_rule_semantics_and_preimages_then_replays_without_network():
    inputs = _bundle_inputs()
    envelope = _make_envelope(inputs)
    verify_same_day_evidence_envelope(envelope)
    decision = inputs[9]
    assert envelope.offline_replay_verified is True
    assert envelope.contract_semantics["observation_population"] == POPULATION
    assert envelope.contract_semantics["correction_policy"] == "ACCEPT_REVISIONS_UNTIL_FIRST_FOLLOWING_DATE_DATAPOINT"
    assert len(envelope.official_observations) == 5
    assert len(envelope.hourly_gefs_raw["member_series"]) == 31
    assert envelope.final_decision["evidence_sha256"] == decision.evidence_sha256
    assert envelope.calibrated_probability is False
    assert envelope.same_day_delivery_enabled is False
    assert envelope.financial_authority is False


def test_near_term_hash_without_matching_raw_preimage_is_rejected():
    inputs = _bundle_inputs()
    near_raw = dict(inputs[4])
    near_raw["predicted_low_f"] = 2.0
    with pytest.raises(SameDayEnvelopeError, match="SAME_DAY_ENVELOPE_NEAR_TERM_RAW_DIGEST_MISMATCH"):
        _make_envelope(inputs, near_raw_override=near_raw)


def test_raw_hourly_preimage_change_cannot_replay_original_member_path():
    inputs = list(_bundle_inputs())
    hourly = inputs[6]
    altered_series = list(hourly.member_series)
    changed_values = list(altered_series[0].values)
    changed_values[6] = 5.0
    altered_series[0] = replace(altered_series[0], values=tuple(changed_values))
    inputs[6] = replace(hourly, member_series=tuple(altered_series))
    with pytest.raises(SameDayEnvelopeError, match="SAME_DAY_ENVELOPE_INPUT_INVALID:GEFS_HOURLY_CONTENT_RUN_DIGEST_MISMATCH"):
        _make_envelope(tuple(inputs))


def test_rule_population_tampering_is_rejected_before_replay():
    inputs = list(_bundle_inputs())
    semantics = inputs[1]
    inputs[1] = replace(semantics, observation_population="WRH_ALL_TIMES")
    with pytest.raises(SameDayEnvelopeError, match="SAME_DAY_ENVELOPE_INPUT_INVALID:SAME_DAY_CONTRACT_SEMANTICS_DIGEST_MISMATCH"):
        _make_envelope(tuple(inputs))


def test_envelope_digest_tampering_is_rejected():
    envelope = _make_envelope(_bundle_inputs())
    corrupted = replace(envelope, config_sha256="c" * 64)
    with pytest.raises(SameDayEnvelopeError, match="SAME_DAY_ENVELOPE_DIGEST_MISMATCH"):
        verify_same_day_evidence_envelope(corrupted)
