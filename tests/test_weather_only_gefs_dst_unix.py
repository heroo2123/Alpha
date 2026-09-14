from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import httpx

from polymarket_scanner.weather_only_three_layer_guarded import GuardedOpenMeteoGEFSHourlyClient


def _fallback_epochs() -> list[int]:
    zone = ZoneInfo("America/New_York")
    start = datetime(2026, 11, 1, 0, 0, tzinfo=zone).timestamp()
    end = datetime(2026, 11, 2, 0, 0, tzinfo=zone).timestamp()
    assert end - start == 25 * 3600
    return [int(start + index * 3600) for index in range(25)]


def _payload() -> dict:
    times = _fallback_epochs()
    hourly = {"time": times}
    units = {"time": "unixtime"}
    keys = ("temperature_2m",) + tuple(
        f"temperature_2m_member{index:02d}" for index in range(1, 31)
    )
    for offset, key in enumerate(keys):
        hourly[key] = [60.0 + offset * 0.1 + hour * 0.01 for hour in range(len(times))]
        units[key] = "°F"
    return {
        "latitude": 40.78,
        "longitude": -73.87,
        "timezone": "America/New_York",
        "hourly": hourly,
        "hourly_units": units,
    }


def test_guarded_gefs_uses_unix_time_and_preserves_both_fall_back_hours():
    seen: list[httpx.Request] = []

    async def handler(request: httpx.Request):
        seen.append(request)
        return httpx.Response(200, request=request, content=json.dumps(_payload()).encode())

    async def scenario():
        client = GuardedOpenMeteoGEFSHourlyClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            result = await client.target_day(
                station="KLGA",
                latitude=40.7769,
                longitude=-73.8740,
                target_date=date(2026, 11, 1),
                unit="F",
                timezone="America/New_York",
            )
        finally:
            await client.close()
        return result

    result = asyncio.run(scenario())
    assert len(seen) == 1
    query = httpx.QueryParams(seen[0].url.query)
    assert query.get("timeformat") == "unixtime"
    assert len(result.valid_times) == 25
    assert all(
        after - before == 3600
        for before, after in zip(result.valid_times, result.valid_times[1:])
    )
    local_hours = [
        datetime.fromtimestamp(value, tz=timezone.utc).astimezone(ZoneInfo("America/New_York"))
        for value in result.valid_times
    ]
    repeated_one_am = [value for value in local_hours if value.hour == 1]
    assert len(repeated_one_am) == 2
    assert {value.fold for value in repeated_one_am} == {0, 1}
