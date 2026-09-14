from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import datetime, timezone

import httpx
import pytest

from polymarket_scanner.safe_logging import install_secret_safe_logging
from polymarket_scanner.weather_only_pws import (
    PWS_STATUS_AUTH_ERROR,
    PWS_STATUS_AVAILABLE,
    PWS_STATUS_NO_FRESH_QC,
    PWS_STATUS_RESPONSE_TOO_LARGE,
    PWS_STATUS_TIMEOUT,
    PWS_STATUS_UNCONFIGURED,
    PWSError,
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


def _iso(epoch):
    return datetime.fromtimestamp(float(epoch), tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _current(
    station_id,
    lat,
    lon,
    *,
    temp=80.0,
    qc=1,
    epoch=None,
    obs_time_utc=None,
    metric=False,
):
    actual_epoch = int(time.time()) if epoch is None else epoch
    iso = _iso(actual_epoch) if obs_time_utc is None else obs_time_utc
    key = "metric" if metric else "imperial"
    return {
        "observations": [
            {
                "stationID": station_id,
                "epoch": actual_epoch,
                "obsTimeUtc": iso,
                "lat": lat,
                "lon": lon,
                "qcStatus": qc,
                key: {"temp": temp},
            }
        ]
    }


def test_missing_api_key_is_unconfigured_and_does_zero_network():
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(500, request=request)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(api_key="", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_UNCONFIGURED
            assert result.observations == ()
            assert result.financial_authority is False
            assert result.may_replace_official_observation is False
            assert result.may_reweight_probability is False

    asyncio.run(scenario())
    assert calls == []


@pytest.mark.parametrize("bad_qc", [0, -1, 2, 1.9, True, False, "1.9", "yes", None])
def test_nearby_qc_accepts_only_exact_documented_pass_enum(bad_qc):
    paths = []

    async def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, request=request, json=_near([("BADQC", LAT, LON, bad_qc)]))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_NO_FRESH_QC
            assert result.observations == ()

    asyncio.run(scenario())
    assert paths == ["/v3/location/near"]


@pytest.mark.parametrize("good_qc", [1, "1"])
def test_qc_accepts_only_explicit_pass_representations(good_qc):
    async def handler(request):
        if request.url.path.endswith("/near"):
            return httpx.Response(200, request=request, json=_near([("PWS1", LAT, LON, good_qc)]))
        return httpx.Response(200, request=request, json=_current("PWS1", LAT, LON, qc=good_qc))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_AVAILABLE

    asyncio.run(scenario())


def test_provider_distance_cannot_hide_physically_distant_station():
    paths = []

    async def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, request=request, json=_near([("LIAR", 43.0, LON, 1)]))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_NO_FRESH_QC

    asyncio.run(scenario())
    assert paths == ["/v3/location/near"]


def test_same_station_id_conflicting_discovery_and_observation_locations_is_quarantined():
    near_lat = LAT + 0.12
    obs_lat = LAT - 0.12

    async def handler(request):
        if request.url.path.endswith("/near"):
            return httpx.Response(200, request=request, json=_near([("PWS1", near_lat, LON, 1)]))
        return httpx.Response(200, request=request, json=_current("PWS1", obs_lat, LON))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_NO_FRESH_QC
            assert result.observations == ()
            assert any(a.outcome == "PWS_IDENTITY_LOCATION_CONFLICT" for a in result.attempts)
            conflict = next(a for a in result.attempts if a.outcome == "PWS_IDENTITY_LOCATION_CONFLICT")
            assert conflict.observation_latitude == obs_lat
            assert conflict.identity_location_delta_km is not None
            assert conflict.identity_location_delta_km > 20.0

    asyncio.run(scenario())


@pytest.mark.parametrize("offset", [-3600, 3600])
def test_stale_and_far_future_observations_are_rejected(offset):
    async def handler(request):
        if request.url.path.endswith("/near"):
            return httpx.Response(200, request=request, json=_near([("PWS1", LAT, LON, 1)]))
        epoch = int(time.time()) + offset
        return httpx.Response(200, request=request, json=_current("PWS1", LAT, LON, epoch=epoch))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_NO_FRESH_QC
            assert result.observations == ()

    asyncio.run(scenario())


def test_small_provider_future_skew_cannot_cross_diagnostic_asof_boundary():
    future = int(time.time()) + 60

    async def handler(request):
        if request.url.path.endswith("/near"):
            return httpx.Response(200, request=request, json=_near([("PWS1", LAT, LON, 1)]))
        return httpx.Response(200, request=request, json=_current("PWS1", LAT, LON, epoch=future))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            snapshot = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
        assert snapshot.status == PWS_STATUS_AVAILABLE
        with pytest.raises(PWSError) as raised:
            build_pws_diagnostic(
                event_id="event-1",
                station="KLGA",
                target_date="2026-09-14",
                unit="F",
                as_of=time.time() + 1.0,
                official_observations=[],
                pws_snapshot=snapshot,
            )
        assert raised.value.code == "PWS_DIAGNOSTIC_OBSERVATION_AFTER_AS_OF"

    asyncio.run(scenario())


def test_pws_observation_that_ages_out_before_diagnostic_is_rejected():
    epoch = int(time.time()) - 890

    async def handler(request):
        if request.url.path.endswith("/near"):
            return httpx.Response(200, request=request, json=_near([("PWS1", LAT, LON, 1)]))
        return httpx.Response(200, request=request, json=_current("PWS1", LAT, LON, epoch=epoch))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            snapshot = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
        assert snapshot.status == PWS_STATUS_AVAILABLE
        with pytest.raises(PWSError) as raised:
            build_pws_diagnostic(
                event_id="event-1",
                station="KLGA",
                target_date="2026-09-14",
                unit="F",
                as_of=epoch + 920.0,
                official_observations=[],
                pws_snapshot=snapshot,
            )
        assert raised.value.code == "PWS_DIAGNOSTIC_OBSERVATION_STALE_AT_AS_OF"

    asyncio.run(scenario())


def test_conflicting_epoch_and_iso_timestamp_is_rejected_and_auditable():
    epoch = int(time.time())

    async def handler(request):
        if request.url.path.endswith("/near"):
            return httpx.Response(200, request=request, json=_near([("PWS1", LAT, LON, 1)]))
        return httpx.Response(
            200,
            request=request,
            json=_current("PWS1", LAT, LON, epoch=epoch, obs_time_utc=_iso(epoch + 60)),
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(api_key="secret", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.observations == ()
            assert any(a.outcome == "PWS_TIMESTAMP_REPRESENTATION_CONFLICT" for a in result.attempts)

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
        return httpx.Response(200, request=request, json=_current(station_id, row[1], row[2], temp=temps[station_id]))

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
            return httpx.Response(200, request=request, json=_near([("PWS1", LAT, LON, 1)]))
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
            assert key not in json.dumps(result.as_dict(), sort_keys=True)

    asyncio.run(scenario())


def test_httpx_style_info_log_redacts_explicit_pws_query_key(caplog):
    key = "not-in-environment-explicit-key"
    install_secret_safe_logging()
    logger = logging.getLogger("httpx")
    previous = logger.level
    logger.setLevel(logging.INFO)
    try:
        with caplog.at_level(logging.INFO, logger="httpx"):
            logger.info("HTTP Request: GET https://api.weather.com/v3/location/near?apiKey=%s&x=1", key)
        rendered = "\n".join(record.getMessage() for record in caplog.records)
        assert key not in rendered
        assert "apiKey=<redacted-api-key>" in rendered
    finally:
        logger.setLevel(previous)


class _SlowStream(httpx.AsyncByteStream):
    async def __aiter__(self):
        yield b'{"location":'
        await asyncio.sleep(0.2)
        yield b'{}}'


def test_total_request_deadline_stops_trickle_response():
    async def handler(request):
        return httpx.Response(200, request=request, stream=_SlowStream())

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(
                api_key="secret",
                http=http,
                request_deadline_seconds=0.05,
                collection_deadline_seconds=0.2,
            ).fetch_snapshot(latitude=LAT, longitude=LON, unit="F")
            assert result.status == PWS_STATUS_TIMEOUT
            assert result.observations == ()

    asyncio.run(scenario())


def test_response_body_limit_rejects_oversized_payload_before_json_parse():
    async def handler(request):
        return httpx.Response(200, request=request, content=b"x" * 2048)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await WeatherCompanyPWSClient(
                api_key="secret",
                http=http,
                max_response_bytes=128,
            ).fetch_snapshot(latitude=LAT, longitude=LON, unit="F")
            assert result.status == PWS_STATUS_RESPONSE_TOO_LARGE
            assert result.observations == ()

    asyncio.run(scenario())


def test_pws_contradiction_is_diagnostic_only_and_cannot_replace_official_state():
    async def handler(request):
        if request.url.path.endswith("/near"):
            return httpx.Response(200, request=request, json=_near([("PWS1", LAT, LON, 1)]))
        return httpx.Response(200, request=request, json=_current("PWS1", LAT, LON, temp=85.0))

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
                {"station": "KLGA", "unit": "F", "observed_at": time.time() - 60.0, "value": 81.0}
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
