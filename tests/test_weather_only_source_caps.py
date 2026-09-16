from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import httpx
import pytest

from polymarket_scanner.weather_only_sources import NWSObservationProxyClient, WeatherSourceError


def _feature(minute: int) -> dict:
    timestamp = f"2026-09-11T13:{minute:02d}:00Z"
    return {
        "id": f"https://api.weather.gov/stations/KLGA/observations/{timestamp}",
        "type": "Feature",
        "properties": {
            "station": "https://api.weather.gov/stations/KLGA",
            "timestamp": timestamp,
            "rawMessage": "KLGA TEST",
            "temperature": {"unitCode": "wmoUnit:degC", "value": 25.0},
        },
    }


def test_nws_collection_hitting_requested_limit_is_not_treated_as_complete():
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "type": "FeatureCollection",
            "features": [_feature(1), _feature(2)],
        })

    async def run():
        client = NWSObservationProxyClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.observations(
                station="KLGA",
                start=datetime(2026, 9, 11, 13, 0, tzinfo=timezone.utc),
                end=datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc),
                limit=2,
            )
        finally:
            await client.close()

    with pytest.raises(WeatherSourceError) as raised:
        asyncio.run(run())
    assert raised.value.code == "NWS_OBSERVATION_RESPONSE_LIMIT_REACHED"


def test_nws_limit_must_be_real_integer_not_boolean_or_fractional():
    async def run(limit):
        client = NWSObservationProxyClient()
        try:
            return await client.observations(
                station="KLGA",
                start=datetime(2026, 9, 11, 13, 0, tzinfo=timezone.utc),
                end=datetime(2026, 9, 11, 14, 0, tzinfo=timezone.utc),
                limit=limit,
            )
        finally:
            await client.close()

    for invalid in (True, 2.5):
        with pytest.raises(WeatherSourceError) as raised:
            asyncio.run(run(invalid))
        assert raised.value.code == "NWS_OBSERVATION_LIMIT_INVALID"
