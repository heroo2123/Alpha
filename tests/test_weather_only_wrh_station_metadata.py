from __future__ import annotations

import asyncio

import httpx
import pytest

from polymarket_scanner.weather_only_station_metadata import (
    NWSStationMetadataClient,
    WeatherStationMetadataError,
)
from polymarket_scanner.weather_only_wrh_station_metadata import (
    WRH_STATION_METADATA_ROLE,
    WRH_STATION_METADATA_VERSION,
    WRHStationMetadataError,
    parse_wrh_synoptic_station_metadata,
)


def _wrh_payload(*, station="ZBAA", timezone="Asia/Shanghai", network="GLOBAL-METAR"):
    return {
        "SUMMARY": {
            "RESPONSE_CODE": 1,
            "RESPONSE_MESSAGE": "OK",
            "NUMBER_OF_OBJECTS": 1,
        },
        "STATION": [
            {
                "STID": station,
                "LATITUDE": "40.0801",
                "LONGITUDE": "116.5846",
                "TIMEZONE": timezone,
                "SHORTNAME": network,
            }
        ],
    }


def test_parse_wrh_metadata_binds_global_station_location_and_never_grants_authority():
    row = parse_wrh_synoptic_station_metadata(
        _wrh_payload(),
        requested_station="zbaa",
        received_at=1000.0,
    )
    assert row.adapter == WRH_STATION_METADATA_VERSION
    assert row.source_role == WRH_STATION_METADATA_ROLE
    assert row.station == "ZBAA"
    assert row.latitude == pytest.approx(40.0801)
    assert row.longitude == pytest.approx(116.5846)
    assert row.timezone == "Asia/Shanghai"
    assert row.raw_network == "GLOBAL-METAR"
    assert row.normalized_network == "ASOS/AWOS"
    assert len(row.source_payload_sha256) == 64
    assert len(row.evidence_sha256) == 64
    assert row.token_persisted is False
    assert row.settlement_authority is False
    assert row.calibration_label_authority is False
    assert row.calibrated_probability_authority is False
    assert row.financial_authority is False


@pytest.mark.parametrize(
    ("mutator", "code_prefix"),
    [
        (lambda p: p["SUMMARY"].update(RESPONSE_MESSAGE="Zero Results"), "WRH_STATION_METADATA_RESPONSE_NOT_OK"),
        (lambda p: p["STATION"][0].update(STID="KLGA"), "WRH_STATION_METADATA_IDENTITY_MISMATCH"),
        (lambda p: p["STATION"][0].update(LATITUDE="nan"), "WRH_STATION_METADATA_COORDINATES_INVALID"),
        (lambda p: p["STATION"][0].update(LONGITUDE="181"), "WRH_STATION_METADATA_COORDINATES_INVALID"),
        (lambda p: p["STATION"][0].update(TIMEZONE="Not/AZone"), "WRH_STATION_METADATA_TIMEZONE_INVALID"),
        (lambda p: p["STATION"][0].update(SHORTNAME="RAWS"), "WRH_STATION_METADATA_NETWORK:WRH_NETWORK_UNSUPPORTED"),
    ],
)
def test_wrh_station_metadata_schema_and_identity_drift_fail_closed(mutator, code_prefix):
    payload = _wrh_payload()
    mutator(payload)
    with pytest.raises(WRHStationMetadataError) as raised:
        parse_wrh_synoptic_station_metadata(
            payload,
            requested_station="ZBAA",
            received_at=1000.0,
        )
    assert raised.value.code == code_prefix


def test_nws_client_uses_global_fallback_only_for_explicit_not_found():
    async def scenario():
        client = NWSStationMetadataClient()
        await client.http.aclose()
        calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, request=request, json={"title": "Not Found"})

        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def fallback(station_id):
            calls.append(station_id)
            return parse_wrh_synoptic_station_metadata(
                _wrh_payload(station=station_id),
                requested_station=station_id,
                received_at=1000.0,
            )

        client._global_wrh_station = fallback
        try:
            row = await client.station("ZBAA")
            assert row.station == "ZBAA"
            assert calls == ["ZBAA"]
        finally:
            await client.close()

    asyncio.run(scenario())


def test_nws_successful_but_malformed_payload_fails_closed_without_global_fallback():
    async def scenario():
        client = NWSStationMetadataClient()
        await client.http.aclose()
        fallback_calls = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, request=request, json={"type": "Feature", "properties": {}})

        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

        async def fallback(station_id):
            fallback_calls.append(station_id)
            raise AssertionError("fallback must not mask NWS schema drift")

        client._global_wrh_station = fallback
        try:
            with pytest.raises(WeatherStationMetadataError):
                await client.station("KLGA")
            assert fallback_calls == []
        finally:
            await client.close()

    asyncio.run(scenario())
