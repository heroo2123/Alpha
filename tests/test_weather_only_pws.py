from __future__ import annotations

import asyncio
import json
import time

import httpx

from polymarket_scanner.weather_only_pws import (
    PWS_STATUS_AUTH_ERROR,
    PWS_STATUS_AVAILABLE,
    PWS_STATUS_NO_FRESH_QC,
    PWS_STATUS_UNCONFIGURED,
    WeatherCompanyPWSClient,
    build_pws_diagnostic,
)


LAT = 40.7769
LON = -73.8740


def _near(stations):
    return {
        "location": {
            "stationId": [row[0] for row in stations],
            "latitude": [row[1] for row in stations],
            "longitude": [row[2] for row in stations],
            "qcStatus": [row[3] for row in stations],
            "updateTimeUtc": ["2026-09-14T15:00:00Z" for _ in stations],
        }
    }


def _current(station_id, lat, lon, *, temp=80.0, qc=1, epoch=None, metric=False):
    key = "metric" if metric else "imperial"
    return {
        "observations": [
            {
                "stationID": station_id,
                "epoch": int(time.time()) if epoch is None else epoch,
                "obsTimeUtc": "2026-09-14T15:00:00Z",
                "lat": lat,
                "lon": lon,
                "qcStatus": qc,
                key: {"temp": temp},
            }
        ]
    }


def _run(client):
    return asyncio.run(client.fetch_snapshot(latitude=LAT, longitude=LON, unit="F"))


def test_missing_api_key_is_unconfigured_and_does_zero_network():
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(500, request=request)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WeatherCompanyPWSClient(api_key="", http=http)
            result = await client.fetch_snapshot(latitude=LAT, longitude=LON, unit="F")
            assert result.status == PWS_STATUS_UNCONFIGURED
            assert result.configured is False
            assert result.observations == ()
            assert result.financial_authority is False
            assert result.settlement_authority is False
            assert result.may_replace_official_observation is False
            assert result.may_reweight_probability is False

    asyncio.run(scenario())
    assert calls == []


def test_qc_failed_nearby_station_is_rejected_before_observation_fetch():
    paths = []

    async def handler(request):
        paths.append(request.url.path)
        return httpx.Response(
            200,
            request=request,
            json=_near([("BADQC", LAT, LON, 0)]),
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_NO_FRESH_QC
            assert result.observations == ()

    asyncio.run(scenario())
    assert paths == ["/v3/location/near"]


def test_provider_distance_cannot_hide_physically_distant_station():
    paths = []

    async def handler(request):
        paths.append(request.url.path)
        # Coordinates are hundreds of km away. The client recomputes distance and
        # does not trust any provider-side distance metadata.
        return httpx.Response(
            200,
            request=request,
            json=_near([("LIAR", 43.0, -73.8740, 1)]),
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_NO_FRESH_QC

    asyncio.run(scenario())
    assert paths == ["/v3/location/near"]


def test_stale_and_future_observations_are_rejected():
    for offset in (-3600, 3600):
        async def handler(request, offset=offset):
            if request.url.path.endswith("/near"):
                return httpx.Response(
                    200, request=request, json=_near([("PWS1", LAT, LON, 1)])
                )
            return httpx.Response(
                200,
                request=request,
                json=_current("PWS1", LAT, LON, epoch=int(time.time()) + offset),
            )

        async def scenario():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                result = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                    latitude=LAT, longitude=LON, unit="F"
                )
                assert result.status == PWS_STATUS_NO_FRESH_QC
                assert result.observations == ()

        asyncio.run(scenario())


def test_three_fresh_qc_stations_create_median_not_votes():
    stations = [
        ("PWS1", LAT + 0.002, LON, 1),
        ("PWS2", LAT + 0.004, LON, 1),
        ("PWS3", LAT + 0.006, LON, 1),
    ]
    temps = {"PWS1": 80.0, "PWS2": 82.0, "PWS3": 96.0}

    async def handler(request):
        if request.url.path.endswith("/near"):
            return httpx.Response(200, request=request, json=_near(stations))
        station_id = request.url.params["stationId"]
        row = next(row for row in stations if row[0] == station_id)
        return httpx.Response(
            200,
            request=request,
            json=_current(station_id, row[1], row[2], temp=temps[station_id]),
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_AVAILABLE
            assert len(result.observations) == 3
            assert result.median_temperature == 82.0
            assert result.min_temperature == 80.0
            assert result.max_temperature == 96.0
            assert result.spread == 16.0
            assert result.may_reweight_probability is False

    asyncio.run(scenario())


def test_contract_unit_maps_to_exact_provider_unit_object():
    seen_units = []

    async def handler(request):
        if request.url.path.endswith("/near"):
            return httpx.Response(
                200, request=request, json=_near([("PWS1", LAT, LON, 1)])
            )
        seen_units.append(request.url.params["units"])
        metric = request.url.params["units"] == "m"
        return httpx.Response(
            200,
            request=request,
            json=_current("PWS1", LAT, LON, temp=25.0 if metric else 77.0, metric=metric),
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = WeatherCompanyPWSClient(api_key="secret", http=http)
            fahrenheit = await client.fetch_snapshot(latitude=LAT, longitude=LON, unit="F")
            celsius = await client.fetch_snapshot(latitude=LAT, longitude=LON, unit="C")
            assert fahrenheit.median_temperature == 77.0
            assert fahrenheit.unit == "F"
            assert celsius.median_temperature == 25.0
            assert celsius.unit == "C"

    asyncio.run(scenario())
    assert seen_units == ["e", "m"]


def test_auth_failure_is_safe_and_api_key_never_enters_evidence():
    key = "super-secret-pws-key"

    async def handler(request):
        return httpx.Response(401, request=request, json={"error": "no"})

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(api_key=key, http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_AUTH_ERROR
            encoded = json.dumps(result.as_dict(), sort_keys=True)
            assert key not in encoded

    asyncio.run(scenario())


def test_pws_contradiction_is_diagnostic_only_and_cannot_replace_official_state():
    async def handler(request):
        if request.url.path.endswith("/near"):
            return httpx.Response(
                200, request=request, json=_near([("PWS1", LAT, LON, 1)])
            )
        return httpx.Response(
            200,
            request=request,
            json=_current("PWS1", LAT, LON, temp=85.0),
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            snapshot = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
        diagnostic = build_pws_diagnostic(
            event_id="event-1",
            station="KLGA",
            target_date="2026-09-14",
            unit="F",
            as_of=time.time() + 1.0,
            official_observations=[
                {
                    "station": "KLGA",
                    "unit": "F",
                    "observed_at": time.time() - 60.0,
                    "value": 81.0,
                }
            ],
            pws_snapshot=snapshot,
        )
        assert diagnostic.latest_official_temperature == 81.0
        assert diagnostic.pws_median_temperature == 85.0
        assert diagnostic.pws_minus_official == 4.0
        assert diagnostic.contradiction is True
        assert diagnostic.may_replace_official_observation is False
        assert diagnostic.may_reweight_probability is False
        assert diagnostic.included_in_validated_pnl is False
        assert diagnostic.same_day_delivery_enabled is False
        assert diagnostic.financial_authority is False

    asyncio.run(scenario())
