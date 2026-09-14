from __future__ import annotations

import asyncio
import gzip
import json
import time

import httpx

from polymarket_scanner.weather_only_pws import (
    PWS_STATUS_AUTH_ERROR,
    PWS_STATUS_AVAILABLE,
    PWS_STATUS_MALFORMED_RESPONSE,
    PWS_STATUS_NO_FRESH_QC,
    PWS_STATUS_RESPONSE_TOO_LARGE,
    PWS_STATUS_UNCONFIGURED,
    PWS_STATUS_UNSUPPORTED_CONTENT_ENCODING,
)
from polymarket_scanner.weather_only_synoptic_pws import (
    SYNOPTIC_CWOP_NETWORK_ID,
    SYNOPTIC_PWS_SOURCE,
    SynopticCWOPPWSClient,
)


LAT = 40.7769
LON = -73.8740


def _station(
    stid: str,
    *,
    lat: float = LAT + 0.01,
    lon: float = LON,
    temp: float = 80.0,
    observed_at: float | None = None,
    mnet: str = SYNOPTIC_CWOP_NETWORK_ID,
    status: str = "ACTIVE",
    restricted: object = False,
    qc_flagged: object = False,
    sensor_key: str = "air_temp_value_1",
    extra_observations: dict | None = None,
) -> dict:
    when = time.time() - 30.0 if observed_at is None else observed_at
    observations = {sensor_key: {"date_time": str(int(when)), "value": temp}}
    if extra_observations:
        observations.update(extra_observations)
    return {
        "STID": stid,
        "MNET_ID": mnet,
        "STATUS": status,
        "RESTRICTED": restricted,
        "QC_FLAGGED": qc_flagged,
        "LATITUDE": str(lat),
        "LONGITUDE": str(lon),
        "OBSERVATIONS": observations,
    }


def _payload(
    stations: list[dict],
    *,
    code: int = 1,
    unit: str = "Fahrenheit",
    qc_checks: list[str] | None = None,
    number_of_objects: int | None = None,
) -> dict:
    checks = (
        ["sl_range_check", "sl_rate_check", "sl_pers_check"]
        if qc_checks is None
        else qc_checks
    )
    return {
        "UNITS": {"air_temp": unit},
        "QC_SUMMARY": {"QC_CHECKS_APPLIED": checks},
        "STATION": stations,
        "SUMMARY": {
            "RESPONSE_CODE": code,
            "RESPONSE_MESSAGE": "OK" if code == 1 else "not ok",
            "NUMBER_OF_OBJECTS": (
                len(stations) if number_of_objects is None else number_of_objects
            ),
        },
    }


def test_missing_token_is_unconfigured_and_does_zero_network():
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(500, request=request)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SynopticCWOPPWSClient(token="", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_UNCONFIGURED
            assert result.configured is False
            assert result.observations == ()
            assert result.financial_authority is False
            assert result.settlement_authority is False
            assert result.may_replace_official_observation is False
            assert result.may_reweight_probability is False

    asyncio.run(scenario())
    assert calls == []


def test_request_is_restricted_to_cwop_nearby_temperature_with_strong_qc():
    seen = {}

    async def handler(request):
        seen.update(request.url.params)
        assert request.headers.get("accept-encoding") == "identity"
        return httpx.Response(200, request=request, json=_payload([_station("CW1234")]))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SynopticCWOPPWSClient(token="public-token", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_AVAILABLE

    asyncio.run(scenario())
    assert seen["network"] == "65"
    assert seen["vars"] == "air_temp"
    assert seen["limit"] == "5"
    assert seen["within"] == "15"
    assert seen["status"] == "active"
    assert seen["units"] == "english,temp|F"
    assert seen["timeformat"] == "%s"
    assert seen["qc"] == "on"
    assert seen["qc_remove_data"] == "on"
    assert seen["qc_flags"] == "on"
    assert seen["qc_checks"] == "synopticlabs"
    assert seen["token"] == "public-token"
    radius = seen["radius"].split(",")
    assert float(radius[0]) == LAT
    assert float(radius[1]) == LON
    assert 9.2 < float(radius[2]) < 9.4


def test_token_never_enters_normalized_evidence():
    token = "synoptic-secret-sentinel"

    async def handler(request):
        return httpx.Response(200, request=request, json=_payload([_station("CW1")]))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SynopticCWOPPWSClient(token=token, http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert token not in json.dumps(result.as_dict(), sort_keys=True)

    asyncio.run(scenario())


def test_synoptic_summary_codes_fail_closed():
    cases = [
        (2, PWS_STATUS_NO_FRESH_QC),
        (200, PWS_STATUS_AUTH_ERROR),
        (400, "PWS_PROVIDER_ERROR"),
    ]
    for code, expected in cases:
        async def handler(request, code=code):
            return httpx.Response(200, request=request, json=_payload([], code=code))

        async def scenario():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                result = await SynopticCWOPPWSClient(token="t", http=http).fetch_snapshot(
                    latitude=LAT, longitude=LON, unit="F"
                )
                assert result.status == expected
                assert result.observations == ()

        asyncio.run(scenario())


def test_success_response_requires_station_count_and_core_qc_evidence():
    malformed_bodies = []
    missing_station = _payload([_station("CW1")])
    missing_station.pop("STATION")
    malformed_bodies.append(missing_station)
    malformed_bodies.append(_payload([_station("CW1")], number_of_objects=2))
    missing_qc = _payload([_station("CW1")])
    missing_qc.pop("QC_SUMMARY")
    malformed_bodies.append(missing_qc)
    malformed_bodies.append(
        _payload([_station("CW1")], qc_checks=["sl_range_check"])
    )

    for body in malformed_bodies:
        async def handler(request, body=body):
            return httpx.Response(200, request=request, json=body)

        async def scenario():
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
                result = await SynopticCWOPPWSClient(token="t", http=http).fetch_snapshot(
                    latitude=LAT, longitude=LON, unit="F"
                )
                assert result.status == PWS_STATUS_MALFORMED_RESPONSE
                assert result.observations == ()

        asyncio.run(scenario())


def test_wrong_network_inactive_restricted_qc_flagged_and_distant_stations_are_rejected():
    stations = [
        _station("WRONGNET", mnet="1"),
        _station("INACTIVE", status="INACTIVE"),
        _station("RESTRICTED", restricted=True),
        _station("QCFLAG", qc_flagged=True),
        _station("DISTANT", lat=LAT + 1.0),
    ]

    async def handler(request):
        return httpx.Response(200, request=request, json=_payload(stations))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SynopticCWOPPWSClient(token="t", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_NO_FRESH_QC
            assert result.observations == ()
            outcomes = {row.outcome for row in result.attempts}
            assert "REJECT_NETWORK" in outcomes
            assert "REJECT_INACTIVE" in outcomes
            assert "REJECT_RESTRICTED" in outcomes
            assert "REJECT_QC_FLAGGED" in outcomes
            assert "PWS_OBSERVATION_DISTANCE_EXCEEDED" in outcomes

    asyncio.run(scenario())


def test_malformed_provider_booleans_do_not_coerce_to_safe_values():
    stations = [
        _station("BADRESTRICTED", restricted="false"),
        _station("BADQC", qc_flagged=0),
    ]

    async def handler(request):
        return httpx.Response(200, request=request, json=_payload(stations))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SynopticCWOPPWSClient(token="t", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_NO_FRESH_QC
            outcomes = {row.outcome for row in result.attempts}
            assert "PWS_SYNOPTIC_RESTRICTED_INVALID" in outcomes
            assert "PWS_SYNOPTIC_QC_FLAGGED_INVALID" in outcomes

    asyncio.run(scenario())


def test_stale_and_future_observations_are_rejected_locally():
    stations = [
        _station("STALE", observed_at=time.time() - 3600),
        _station("FUTURE", observed_at=time.time() + 3600),
    ]

    async def handler(request):
        return httpx.Response(200, request=request, json=_payload(stations))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SynopticCWOPPWSClient(token="t", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_NO_FRESH_QC
            outcomes = {row.outcome for row in result.attempts}
            assert "PWS_OBSERVATION_STALE" in outcomes
            assert "PWS_OBSERVATION_FROM_FUTURE" in outcomes

    asyncio.run(scenario())


def test_fahrenheit_and_celsius_units_are_exact_and_provider_units_are_checked():
    requests = []

    async def handler(request):
        requests.append(request.url.params["units"])
        if request.url.params["units"].startswith("english"):
            body = _payload([_station("CWF", temp=77.0)], unit="Fahrenheit")
        else:
            body = _payload([_station("CWC", temp=25.0)], unit="Celsius")
        return httpx.Response(200, request=request, json=body)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = SynopticCWOPPWSClient(token="t", http=http)
            f = await client.fetch_snapshot(latitude=LAT, longitude=LON, unit="F")
            c = await client.fetch_snapshot(latitude=LAT, longitude=LON, unit="C")
            assert f.median_temperature == 77.0
            assert c.median_temperature == 25.0
            assert f.unit == "F"
            assert c.unit == "C"

    asyncio.run(scenario())
    assert requests == ["english,temp|F", "metric,temp|C"]


def test_wrong_provider_unit_is_rejected():
    async def handler(request):
        return httpx.Response(
            200, request=request, json=_payload([_station("CW1")], unit="Celsius")
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SynopticCWOPPWSClient(token="t", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_MALFORMED_RESPONSE

    asyncio.run(scenario())


def test_multiple_direct_sensors_and_derived_only_temperature_fail_closed():
    ambiguous = _station(
        "AMB",
        sensor_key="air_temp_value_1",
        temp=79.0,
        extra_observations={
            "air_temp_value_2": {"date_time": str(int(time.time() - 20)), "value": 80.0}
        },
    )
    derived = _station("DERIVED", sensor_key="air_temp_value_1d", temp=82.0)
    direct = _station("DIRECT", sensor_key="air_temp_value_3", temp=81.0)

    async def handler(request):
        return httpx.Response(200, request=request, json=_payload([ambiguous, derived, direct]))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SynopticCWOPPWSClient(token="t", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_AVAILABLE
            assert len(result.observations) == 1
            row = result.observations[0]
            assert row.station_id == "DIRECT"
            assert row.temperature == 81.0
            assert row.provider_sensor_id == "air_temp_value_3"
            assert row.source == SYNOPTIC_PWS_SOURCE
            assert row.settlement_authority is False
            assert row.financial_authority is False
            outcomes = {attempt.station_id: attempt.outcome for attempt in result.attempts}
            assert outcomes["AMB"] == "REJECT_TEMPERATURE_MISSING_OR_AMBIGUOUS"
            assert outcomes["DERIVED"] == "REJECT_TEMPERATURE_MISSING_OR_AMBIGUOUS"

    asyncio.run(scenario())


def test_nonempty_observation_qc_payload_is_rejected_even_if_station_flag_is_false():
    station = _station("QCROW")
    station["OBSERVATIONS"]["air_temp_value_1"]["qc"] = ["sl_rate_check"]

    async def handler(request):
        return httpx.Response(200, request=request, json=_payload([station]))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SynopticCWOPPWSClient(token="t", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert result.status == PWS_STATUS_NO_FRESH_QC
            assert result.attempts[0].outcome == "REJECT_OBSERVATION_QC_FLAGGED"

    asyncio.run(scenario())


def test_nearest_three_are_retained_and_median_is_diagnostic_only():
    stations = [
        _station("CW1", lat=LAT + 0.005, temp=80.0),
        _station("CW2", lat=LAT + 0.010, temp=82.0),
        _station("CW3", lat=LAT + 0.015, temp=84.0),
        _station("CW4", lat=LAT + 0.020, temp=100.0),
    ]

    async def handler(request):
        return httpx.Response(200, request=request, json=_payload(stations))

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            result = await SynopticCWOPPWSClient(token="t", http=http).fetch_snapshot(
                latitude=LAT, longitude=LON, unit="F"
            )
            assert [row.station_id for row in result.observations] == ["CW1", "CW2", "CW3"]
            assert result.median_temperature == 82.0
            assert result.may_replace_official_observation is False
            assert result.may_reweight_probability is False
            assert result.financial_authority is False

    asyncio.run(scenario())


class TrackingStream(httpx.AsyncByteStream):
    def __init__(self, chunks: list[bytes]) -> None:
        self.chunks = chunks
        self.iterated = False

    async def __aiter__(self):
        self.iterated = True
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


def test_compressed_synoptic_response_is_rejected_before_body_iteration():
    encoded = gzip.compress(b'{"payload":"' + b"A" * 1_000_000 + b'"}')
    stream = TrackingStream([encoded])

    async def handler(request):
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Encoding": "gzip"},
            stream=stream,
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = SynopticCWOPPWSClient(token="t", http=http)
            status, body = await client._json(client._request_params(LAT, LON, "F"))
            assert status == PWS_STATUS_UNSUPPORTED_CONTENT_ENCODING
            assert body is None
            assert stream.iterated is False

    asyncio.run(scenario())


def test_stacked_content_encoding_is_rejected_before_body_iteration():
    stream = TrackingStream([b"compressed"])

    async def handler(request):
        return httpx.Response(
            200,
            request=request,
            headers={"Content-Encoding": "gzip, deflate"},
            stream=stream,
        )

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = SynopticCWOPPWSClient(token="t", http=http)
            status, _ = await client._json(client._request_params(LAT, LON, "F"))
            assert status == PWS_STATUS_UNSUPPORTED_CONTENT_ENCODING
            assert stream.iterated is False

    asyncio.run(scenario())


def test_raw_body_byte_cap_is_enforced_before_json_parse():
    stream = TrackingStream([b"x" * 80, b"y" * 80])

    async def handler(request):
        return httpx.Response(200, request=request, stream=stream)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            client = SynopticCWOPPWSClient(token="t", http=http, max_response_bytes=100)
            status, body = await client._json(client._request_params(LAT, LON, "F"))
            assert status == PWS_STATUS_RESPONSE_TOO_LARGE
            assert body is None

    asyncio.run(scenario())
