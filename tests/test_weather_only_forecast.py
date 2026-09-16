from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date

import httpx
import pytest

from polymarket_scanner.weather_only_contracts import DAILY_HIGH, DAILY_LOW, compile_weather_event
from polymarket_scanner.weather_only_forecast import (
    FORECAST_ADAPTER_VERSION,
    GEFS_TOTAL_MEMBERS,
    EnsembleMappingPolicy,
    OpenMeteoGEFSEnsembleClient,
    WeatherForecastError,
    map_ensemble_to_contract_buckets,
    parse_open_meteo_gefs_daily_extreme,
)


def _market(mid: str, question: str) -> dict:
    return {
        "id": mid,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
        "enableOrderBook": True,
        "question": question,
        "slug": f"market-{mid}",
        "conditionId": f"condition-{mid}",
        "outcomes": '["Yes","No"]',
        "clobTokenIds": f'["{mid}-yes","{mid}-no"]',
    }


def _event() -> dict:
    return {
        "id": "event-1",
        "slug": "nyc-high",
        "title": "Highest temperature in NYC on September 11?",
        "description": "Observation date 11 Sep '26, in whole degrees Fahrenheit.",
        "resolutionSource": "https://www.weather.gov/wrh/timeseries?site=KLGA",
        "markets": [
            _market("low", "Will the highest temperature be 69°F or lower?"),
            _market("mid", "Will the highest temperature be 70-71°F?"),
            _market("high", "Will the highest temperature be 72°F or higher?"),
        ],
    }


def _payload(*, family=DAILY_HIGH, values=None, unit="°F", timezone="America/New_York") -> dict:
    variable = "temperature_2m_max" if family == DAILY_HIGH else "temperature_2m_min"
    values = list(values or [69.4] + [69.6] * 10 + [71.4] * 10 + [71.6] * 10)
    assert len(values) == GEFS_TOTAL_MEMBERS
    daily = {"time": ["2026-09-11"]}
    units = {"time": "iso8601"}
    keys = [variable] + [f"{variable}_member{i:02d}" for i in range(1, 31)]
    for key, value in zip(keys, values):
        daily[key] = [value]
        units[key] = unit
    return {
        "latitude": 40.78,
        "longitude": -73.88,
        "elevation": 10.0,
        "generationtime_ms": 1.0,
        "utc_offset_seconds": -14400,
        "timezone": timezone,
        "timezone_abbreviation": "EDT",
        "daily": daily,
        "daily_units": units,
    }


def _distribution(payload=None, *, family=DAILY_HIGH, unit="F"):
    return parse_open_meteo_gefs_daily_extreme(
        payload or _payload(family=family, unit="°F" if unit == "F" else "°C"),
        station="KLGA",
        target_date=date(2026, 9, 11),
        family=family,
        unit=unit,
        timezone="America/New_York",
        requested_latitude=40.7769,
        requested_longitude=-73.8740,
        received_at=100.0,
    )


def test_gefs_parser_freezes_current_31_member_schema_and_authority_boundary():
    row = _distribution()
    assert row.adapter == FORECAST_ADAPTER_VERSION
    assert row.provider_model == "ncep_gefs_seamless"
    assert row.station == "KLGA"
    assert row.target_date == date(2026, 9, 11)
    assert row.family == DAILY_HIGH
    assert row.unit == "F"
    assert len(row.member_labels) == 31
    assert row.member_labels[0] == "control"
    assert row.member_labels[-1] == "member30"
    assert len(row.member_values) == 31
    assert len(row.evidence_sha256) == 64
    assert row.source_role == "FORECAST_RESEARCH_ONLY"
    assert row.settlement_authority is False
    assert row.calibration_label_authority is False
    assert row.calibrated_probability is False
    assert row.financial_authority is False


def test_forecast_evidence_digest_is_deterministic_for_same_model_snapshot():
    first = _distribution()
    second = _distribution()
    assert first.evidence_sha256 == second.evidence_sha256
    changed = _distribution(_payload(values=[69.5] + [69.6] * 10 + [71.4] * 10 + [71.6] * 10))
    assert changed.evidence_sha256 != first.evidence_sha256


@pytest.mark.parametrize(
    ("mutator", "code"),
    [
        (lambda p: p["daily"].pop("temperature_2m_max_member30"), "FORECAST_MEMBER_SCHEMA_DRIFT"),
        (lambda p: p["daily"].__setitem__("temperature_2m_max_member31", [70]), "FORECAST_MEMBER_SCHEMA_DRIFT"),
        (lambda p: p["daily_units"].__setitem__("temperature_2m_max_member01", "°C"), "FORECAST_TEMPERATURE_UNIT_MISMATCH"),
        (lambda p: p.__setitem__("timezone", "UTC"), "FORECAST_TIMEZONE_MISMATCH"),
        (lambda p: p["daily"]["temperature_2m_max_member01"].__setitem__(0, float("nan")), "FORECAST_MEMBER_VALUE_INVALID"),
    ],
)
def test_gefs_parser_fails_closed_on_schema_unit_timezone_or_value_drift(mutator, code):
    payload = _payload()
    mutator(payload)
    with pytest.raises(WeatherForecastError) as raised:
        _distribution(payload)
    assert raised.value.code == code


def test_gefs_parser_rejects_missing_or_duplicate_target_day():
    missing = _payload()
    missing["daily"]["time"] = ["2026-09-12"]
    with pytest.raises(WeatherForecastError) as raised:
        _distribution(missing)
    assert raised.value.code == "FORECAST_TARGET_DATE_NOT_UNIQUE"

    duplicate = _payload()
    duplicate["daily"]["time"] = ["2026-09-11", "2026-09-11"]
    for key, series in duplicate["daily"].items():
        if key != "time":
            series.append(series[0])
    with pytest.raises(WeatherForecastError) as raised:
        _distribution(duplicate)
    assert raised.value.code == "FORECAST_DAILY_TIME_DUPLICATE"


def test_mapping_policy_is_explicit_and_rejects_unknown_transform():
    with pytest.raises(ValueError):
        EnsembleMappingPolicy("x", True, "PYTHON_ROUND")
    with pytest.raises(ValueError):
        EnsembleMappingPolicy("x", 1)


def test_ensemble_maps_to_exactly_one_contract_bucket_but_stays_uncalibrated():
    compiled = replace(compile_weather_event(_event()), exactly_one_outcome_proven=True)
    forecast = map_ensemble_to_contract_buckets(
        compiled,
        _distribution(),
        EnsembleMappingPolicy("nearest-whole-v1-all31", include_control=True),
    )
    assert forecast.member_count == 31
    assert forecast.included_control is True
    assert forecast.probability_sum == pytest.approx(1.0)
    rows = {row.market_id: row for row in forecast.bucket_frequencies}
    assert rows["low"].member_hits == 1
    assert rows["mid"].member_hits == 20
    assert rows["high"].member_hits == 10
    assert rows["low"].raw_member_frequency == pytest.approx(1 / 31)
    assert rows["mid"].raw_member_frequency == pytest.approx(20 / 31)
    assert rows["high"].raw_member_frequency == pytest.approx(10 / 31)
    assert all(row.calibrated is False for row in forecast.bucket_frequencies)
    assert all(row.financial_authority is False for row in forecast.bucket_frequencies)
    assert forecast.calibrated is False
    assert forecast.settlement_authority is False
    assert forecast.financial_authority is False


def test_control_member_inclusion_is_part_of_named_mapping_policy():
    compiled = replace(compile_weather_event(_event()), exactly_one_outcome_proven=True)
    forecast = map_ensemble_to_contract_buckets(
        compiled,
        _distribution(),
        EnsembleMappingPolicy("nearest-whole-v1-perturbed30", include_control=False),
    )
    rows = {row.market_id: row for row in forecast.bucket_frequencies}
    assert forecast.member_count == 30
    assert forecast.included_control is False
    assert rows["low"].member_hits == 0
    assert rows["mid"].member_hits == 20
    assert rows["high"].member_hits == 10


def test_half_degree_quantization_is_half_away_from_zero_not_bankers_rounding():
    values = [69.5] + [69.4] * 30
    compiled = replace(compile_weather_event(_event()), exactly_one_outcome_proven=True)
    forecast = map_ensemble_to_contract_buckets(
        compiled,
        _distribution(_payload(values=values)),
        EnsembleMappingPolicy("nearest-whole-v1", include_control=True),
    )
    rows = {row.market_id: row for row in forecast.bucket_frequencies}
    assert rows["mid"].member_hits == 1
    assert rows["low"].member_hits == 30


def test_mapper_rejects_unproven_partition_and_identity_mismatches():
    compiled = compile_weather_event(_event())
    distribution = _distribution()
    policy = EnsembleMappingPolicy("nearest-whole-v1", include_control=True)
    with pytest.raises(WeatherForecastError) as raised:
        map_ensemble_to_contract_buckets(compiled, distribution, policy)
    assert raised.value.code == "FORECAST_CONTRACT_PARTITION_UNPROVEN"

    certified = replace(compiled, exactly_one_outcome_proven=True)
    wrong_station = replace(distribution, station="KJFK")
    with pytest.raises(WeatherForecastError) as raised:
        map_ensemble_to_contract_buckets(certified, wrong_station, policy)
    assert raised.value.code == "FORECAST_CONTRACT_STATION_MISMATCH"

    wrong_date = replace(distribution, target_date=date(2026, 9, 12))
    with pytest.raises(WeatherForecastError) as raised:
        map_ensemble_to_contract_buckets(certified, wrong_date, policy)
    assert raised.value.code == "FORECAST_CONTRACT_DATE_MISMATCH"


def test_low_temperature_family_uses_minimum_member_schema():
    event = _event()
    event["title"] = "Lowest temperature in NYC on September 11?"
    event["markets"] = [
        _market("low", "Will the lowest temperature be 49°F or lower?"),
        _market("mid", "Will the lowest temperature be 50-51°F?"),
        _market("high", "Will the lowest temperature be 52°F or higher?"),
    ]
    compiled = compile_weather_event(event)
    assert compiled.family == DAILY_LOW
    distribution = _distribution(
        _payload(family=DAILY_LOW, values=[49.0] * 31),
        family=DAILY_LOW,
    )
    assert distribution.family == DAILY_LOW
    assert len(distribution.member_values) == 31


def test_gefs_client_requests_one_model_one_day_one_extreme_and_nearest_cell():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=_payload())

    async def run():
        client = OpenMeteoGEFSEnsembleClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.daily_extreme(
                station="KLGA",
                latitude=40.7769,
                longitude=-73.8740,
                target_date=date(2026, 9, 11),
                family=DAILY_HIGH,
                unit="F",
                timezone="America/New_York",
            )
        finally:
            await client.close()

    row = asyncio.run(run())
    assert row.financial_authority is False
    request = seen[0]
    assert request.url.path == "/v1/ensemble"
    assert request.url.params.get("models") == "ncep_gefs_seamless"
    assert request.url.params.get("daily") == "temperature_2m_max"
    assert request.url.params.get("temperature_unit") == "fahrenheit"
    assert request.url.params.get("timezone") == "America/New_York"
    assert request.url.params.get("start_date") == "2026-09-11"
    assert request.url.params.get("end_date") == "2026-09-11"
    assert request.url.params.get("cell_selection") == "nearest"
