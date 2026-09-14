from __future__ import annotations

import asyncio
import json
import time

import httpx

from polymarket_scanner.weather_only_pws import (
    PWS_STATUS_AUTH_ERROR,
    PWS_STATUS_MALFORMED_RESPONSE,
    PWS_STATUS_TIMEOUT,
    build_pws_diagnostic,
)
from polymarket_scanner.weather_only_synoptic_pws import SynopticCWOPPWSClient


LAT = 40.7769
LON = -73.8740


def _good_payload(temp: float = 83.0) -> dict:
    now = int(time.time())
    return {
        "UNITS": {"air_temp": "Fahrenheit"},
        "QC_SUMMARY": {"QC_CHECKS_APPLIED": ["sl_range_check"]},
        "STATION": [
            {
                "STID": "CWTEST",
                "MNET_ID": "65",
                "STATUS": "ACTIVE",
                "RESTRICTED": False,
                "QC_FLAGGED": False,
                "LATITUDE": str(LAT + 0.005),
                "LONGITUDE": str(LON),
                "OBSERVATIONS": {
                    "air_temp_value_1": {"date_time": str(now), "value": temp}
                },
            }
        ],
        "SUMMARY": {
            "RESPONSE_CODE": 1,
            "RESPONSE_MESSAGE": "OK",
            "NUMBER_OF_OBJECTS": 1,
        },
    }


def test_http_auth_failures_are_not_misclassified_as_no_data():
    for code in (401, 403):
        async def handler(request, code=code):
            return httpx.Response(code, request=request, json={"error": "auth"})

        async def scenario():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                result = await SynopticCWOPPWSClient(token="bad", http=http).fetch_snapshot(
                    latitude=LAT, longitude=LON, unit="F"
                )
                assert result.status == PWS_STATUS_AUTH_ERROR
                assert result.observations == ()

        asyncio.run(scenario())


def test_missing_summary_fails_closed_as_malformed():
    async def handler(request):
        body = _good_payload()
        body.pop("SUMMARY")
        return httpx.Response(200, request=request, json=body)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SynopticCWOPPWSClient(token="t", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_MALFORMED_RESPONSE
            assert result.observations == ()

    asyncio.run(scenario())


class SlowStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        await asyncio.sleep(0.2)
        yield json.dumps(_good_payload()).encode("utf-8")

    async def aclose(self) -> None:
        return None


def test_total_request_deadline_bounds_slow_synoptic_body():
    async def handler(request):
        return httpx.Response(200, request=request, stream=SlowStream())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = SynopticCWOPPWSClient(
                token="t",
                http=http,
                request_deadline_seconds=0.05,
                collection_deadline_seconds=0.15,
            )
            started = time.monotonic()
            result = await client.fetch_snapshot(latitude=LAT, longitude=LON, unit="F")
            elapsed = time.monotonic() - started
            assert result.status == PWS_STATUS_TIMEOUT
            assert elapsed < 0.18

    asyncio.run(scenario())


def test_synoptic_snapshot_can_only_create_nonfinancial_diagnostic():
    async def handler(request):
        return httpx.Response(200, request=request, json=_good_payload(temp=85.0))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            snapshot = await SynopticCWOPPWSClient(token="t", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
        cutoff = time.time() + 1.0
        diagnostic = build_pws_diagnostic(
            event_id="event-synoptic",
            station="KLGA",
            target_date="2026-09-14",
            unit="F",
            as_of=cutoff,
            official_observations=[
                {
                    "station": "KLGA",
                    "unit": "F",
                    "observed_at": cutoff - 60.0,
                    "value": 81.0,
                }
            ],
            pws_snapshot=snapshot,
        )
        assert diagnostic.pws_median_temperature == 85.0
        assert diagnostic.latest_official_temperature == 81.0
        assert diagnostic.contradiction is True
        assert diagnostic.may_replace_official_observation is False
        assert diagnostic.may_reweight_probability is False
        assert diagnostic.included_in_validated_pnl is False
        assert diagnostic.same_day_delivery_enabled is False
        assert diagnostic.settlement_authority is False
        assert diagnostic.financial_authority is False

    asyncio.run(scenario())


def test_one_snapshot_uses_one_provider_request_only():
    calls = 0

    async def handler(request):
        nonlocal calls
        calls += 1
        return httpx.Response(200, request=request, json=_good_payload())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SynopticCWOPPWSClient(token="t", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.observations

    asyncio.run(scenario())
    assert calls == 1
