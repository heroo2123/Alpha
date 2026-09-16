from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from polymarket_scanner.weather_only_contracts import (
    DAILY_LOW,
    SOURCE_NWS_WRH,
    CompiledWeatherEvent,
    WeatherBucket,
)
from polymarket_scanner.weather_only_rules import TemperatureRuleAuthority
from polymarket_scanner.weather_only_same_day_contract import (
    SameDayContractError,
    build_same_day_contract_semantics,
    verify_same_day_contract_semantics,
)


def _compiled(unit: str = "F") -> CompiledWeatherEvent:
    return CompiledWeatherEvent(
        compiler_version="fixture",
        event_id="event-low",
        event_slug="event-low",
        title="Lowest temperature fixture",
        family=DAILY_LOW,
        target_date=date(2026, 9, 12),
        unit=unit,
        source_family=SOURCE_NWS_WRH,
        source_urls=("https://weather.gov/wrh/timeseries?site=KDAL",),
        station_hint="KDAL",
        buckets=(
            WeatherBucket("m0", "c0", "70 or lower", "le70", None, 70.0, unit, "y0", "n0", True),
            WeatherBucket("m1", "c1", "71 or higher", "ge71", 71.0, None, unit, "y1", "n1", True),
        ),
        partition_shape_complete=True,
        exactly_one_outcome_proven=True,
        shadow_supported=True,
        financial_authority=False,
        rejection_reasons=(),
    )


def _authority(population: str = "WRH_HOURLY_DATA", unit: str = "F") -> TemperatureRuleAuthority:
    return TemperatureRuleAuthority(
        version="rules-v-test",
        profile="NWS_WRH_DAILY_EXTREME_CURRENT_TEMPLATE_V1",
        family=DAILY_LOW,
        source_family=SOURCE_NWS_WRH,
        statistic="DAILY_LOWEST_TEMP",
        observation_population=population,
        precision=f"WHOLE_DEGREE_{unit}",
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


def test_current_layer1_capability_is_narrowly_hourly_fahrenheit_only():
    semantics = build_same_day_contract_semantics(_compiled(), _authority())
    verify_same_day_contract_semantics(semantics, _compiled())
    assert semantics.observation_population == "WRH_HOURLY_DATA"
    assert semantics.layer1_adapter_capable is True
    assert semantics.layer1_adapter_block_reason is None
    assert semantics.same_day_delivery_authority is False
    assert semantics.settlement_authority is False
    assert semantics.financial_authority is False


def test_munich_style_all_times_population_is_not_silently_coerced_to_hourly():
    semantics = build_same_day_contract_semantics(
        _compiled(), _authority(population="WRH_ALL_TIMES")
    )
    assert semantics.layer1_adapter_capable is False
    assert semantics.layer1_adapter_block_reason == "LAYER1_OBSERVATION_POPULATION_UNSUPPORTED:WRH_ALL_TIMES"
    assert semantics.observation_population == "WRH_ALL_TIMES"


def test_celsius_contract_is_blocked_by_current_fahrenheit_layer1_adapter():
    semantics = build_same_day_contract_semantics(
        _compiled(unit="C"), _authority(population="WRH_HOURLY_DATA", unit="C")
    )
    assert semantics.layer1_adapter_capable is False
    assert semantics.layer1_adapter_block_reason == "LAYER1_UNIT_ADAPTER_UNSUPPORTED:C"


def test_tampered_population_cannot_pass_semantic_digest_verification():
    compiled = _compiled()
    semantics = build_same_day_contract_semantics(compiled, _authority())
    tampered = replace(semantics, observation_population="WRH_ALL_TIMES")
    with pytest.raises(SameDayContractError, match="SAME_DAY_CONTRACT_SEMANTICS_DIGEST_MISMATCH"):
        verify_same_day_contract_semantics(tampered, compiled)


def test_unproven_rule_authority_is_rejected_before_semantic_sidecar_exists():
    bad = replace(
        _authority(),
        rule_semantics_proven=False,
        rejection_reasons=("NWS_OBSERVATION_POPULATION_UNPROVEN",),
    )
    with pytest.raises(SameDayContractError, match="SAME_DAY_CONTRACT_RULE_AUTHORITY_UNPROVEN"):
        build_same_day_contract_semantics(_compiled(), bad)
