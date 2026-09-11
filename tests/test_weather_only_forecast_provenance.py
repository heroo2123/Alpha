from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import date, datetime

import pytest

from polymarket_scanner.weather_only_contracts import DAILY_HIGH, compile_weather_event
from polymarket_scanner.weather_only_forecast import (
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


def _payload(*, latitude=40.78, longitude=-73.88) -> dict:
    variable = "temperature_2m_max"
    values = [69.4] + [69.6] * 10 + [71.4] * 10 + [71.6] * 10
    daily = {"time": ["2026-09-11"]}
    units = {"time": "iso8601"}
    keys = [variable] + [f"{variable}_member{i:02d}" for i in range(1, 31)]
    for key, value in zip(keys, values):
        daily[key] = [value]
        units[key] = "°F"
    return {
        "latitude": latitude,
        "longitude": longitude,
        "timezone": "America/New_York",
        "daily": daily,
        "daily_units": units,
    }


def _distribution(payload=None, *, received_at=100.0):
    return parse_open_meteo_gefs_daily_extreme(
        payload or _payload(),
        station="KLGA",
        target_date=date(2026, 9, 11),
        family=DAILY_HIGH,
        unit="F",
        timezone="America/New_York",
        requested_latitude=40.7769,
        requested_longitude=-73.8740,
        received_at=received_at,
    )


def test_target_date_must_be_calendar_date_not_datetime_subclass():
    with pytest.raises(WeatherForecastError) as raised:
        parse_open_meteo_gefs_daily_extreme(
            _payload(),
            station="KLGA",
            target_date=datetime(2026, 9, 11),
            family=DAILY_HIGH,
            unit="F",
            timezone="America/New_York",
            requested_latitude=40.7769,
            requested_longitude=-73.8740,
            received_at=100.0,
        )
    assert raised.value.code == "FORECAST_TARGET_DATE_INVALID"


@pytest.mark.parametrize("received_at", [float("nan"), float("inf"), -1.0, True])
def test_receipt_time_must_be_finite_nonnegative_number(received_at):
    with pytest.raises(WeatherForecastError) as raised:
        _distribution(received_at=received_at)
    assert raised.value.code == "FORECAST_RECEIPT_TIME_INVALID"


def test_forecast_evidence_digest_binds_resolved_grid_coordinates():
    first = _distribution(_payload(latitude=40.78, longitude=-73.88))
    moved = _distribution(_payload(latitude=40.79, longitude=-73.87))
    assert first.member_values == moved.member_values
    assert first.evidence_sha256 != moved.evidence_sha256


def test_mapper_rejects_tampered_member_values_with_stale_digest():
    compiled = replace(compile_weather_event(_event()), exactly_one_outcome_proven=True)
    distribution = _distribution()
    tampered = replace(
        distribution,
        member_values=(99.0,) + distribution.member_values[1:],
    )
    with pytest.raises(WeatherForecastError) as raised:
        map_ensemble_to_contract_buckets(
            compiled,
            tampered,
            EnsembleMappingPolicy("fixture-policy", include_control=True),
        )
    assert raised.value.code == "FORECAST_EVIDENCE_DIGEST_MISMATCH"


def test_mapper_rejects_tampered_member_identity_even_with_correct_count():
    compiled = replace(compile_weather_event(_event()), exactly_one_outcome_proven=True)
    distribution = _distribution()
    labels = list(distribution.member_labels)
    labels[1], labels[2] = labels[2], labels[1]
    tampered = replace(distribution, member_labels=tuple(labels))
    with pytest.raises(WeatherForecastError) as raised:
        map_ensemble_to_contract_buckets(
            compiled,
            tampered,
            EnsembleMappingPolicy("fixture-policy", include_control=True),
        )
    assert raised.value.code == "FORECAST_MEMBER_IDENTITY_MISMATCH"


def test_client_rejects_empty_timezone_and_datetime_before_network_io():
    async def run_empty_timezone():
        client = OpenMeteoGEFSEnsembleClient()
        try:
            return await client.daily_extreme(
                station="KLGA",
                latitude=40.7769,
                longitude=-73.8740,
                target_date=date(2026, 9, 11),
                family=DAILY_HIGH,
                unit="F",
                timezone="",
            )
        finally:
            await client.close()

    with pytest.raises(WeatherForecastError) as raised:
        asyncio.run(run_empty_timezone())
    assert raised.value.code == "FORECAST_TIMEZONE_INVALID"

    async def run_datetime():
        client = OpenMeteoGEFSEnsembleClient()
        try:
            return await client.daily_extreme(
                station="KLGA",
                latitude=40.7769,
                longitude=-73.8740,
                target_date=datetime(2026, 9, 11),
                family=DAILY_HIGH,
                unit="F",
                timezone="America/New_York",
            )
        finally:
            await client.close()

    with pytest.raises(WeatherForecastError) as raised:
        asyncio.run(run_datetime())
    assert raised.value.code == "FORECAST_TARGET_DATE_INVALID"
