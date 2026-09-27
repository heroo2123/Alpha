from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from polymarket_scanner.weather_only_station_region import (
    NWSStationRegionClient,
    NWS_STATION_REGION_ROLE,
    NWS_STATION_REGION_VERSION,
    WeatherStationRegionError,
    parse_nws_office_region,
    parse_nws_point_office,
)


def _point_payload(*, lat=39.8466, lon=-104.6562, cwa="BOU"):
    return {
        "id": f"https://api.weather.gov/points/{lat},{lon}",
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": {
            "@id": f"https://api.weather.gov/points/{lat},{lon}",
            "cwa": cwa,
            "forecastOffice": f"https://api.weather.gov/offices/{cwa}",
            "gridId": cwa,
        },
    }


def _office_payload(*, office="BOU", region="cr"):
    return {
        "@id": f"https://api.weather.gov/offices/{office}",
        "id": office,
        "name": "Denver/Boulder, CO",
        "nwsRegion": region,
        "parentOrganization": f"https://api.weather.gov/offices/{region.upper()}H",
    }


def test_parse_nws_point_office_binds_coordinates_and_cwa_no_authority():
    row = parse_nws_point_office(
        _point_payload(), requested_latitude=39.8466, requested_longitude=-104.6562, received_at=1000.0,
    )
    assert row.adapter == NWS_STATION_REGION_VERSION
    assert row.source_role == NWS_STATION_REGION_ROLE
    assert row.cwa == "BOU"
    assert row.forecast_office.endswith("/offices/BOU")
    assert row.latitude == pytest.approx(39.8466)
    assert row.longitude == pytest.approx(-104.6562)
    assert len(row.source_payload_sha256) == 64
    assert len(row.evidence_sha256) == 64
    assert row.settlement_authority is False
    assert row.financial_authority is False


def test_point_preserves_exact_coordinates_while_query_is_rounded():
    row = parse_nws_point_office(
        _point_payload(), requested_latitude=39.84658, requested_longitude=-104.65622, received_at=1000.0,
    )
    assert row.latitude == 39.84658
    assert row.longitude == -104.65622
    assert row.source_url.endswith("/points/39.8466,-104.6562")


def test_point_coordinate_mismatch_fails_closed():
    payload = _point_payload()
    payload["geometry"]["coordinates"] = [-73.8803, 40.7794]
    with pytest.raises(WeatherStationRegionError) as raised:
        parse_nws_point_office(payload, requested_latitude=39.8466, requested_longitude=-104.6562, received_at=1000.0)
    assert raised.value.code == "STATION_REGION_POINT_COORDINATE_MISMATCH"


def test_point_office_url_mismatch_fails_closed():
    payload = _point_payload()
    payload["properties"]["forecastOffice"] = "https://api.weather.gov/offices/XYZ"
    with pytest.raises(WeatherStationRegionError) as raised:
        parse_nws_point_office(payload, requested_latitude=39.8466, requested_longitude=-104.6562, received_at=1000.0)
    assert raised.value.code == "STATION_REGION_OFFICE_URL_MISMATCH"


@pytest.mark.parametrize("cwa", ["", "toolongcode", "1234", "bo"])
def test_point_invalid_cwa_fails_closed(cwa):
    payload = _point_payload(cwa=cwa or "BOU")
    payload["properties"]["cwa"] = cwa
    with pytest.raises(WeatherStationRegionError) as raised:
        parse_nws_point_office(payload, requested_latitude=39.8466, requested_longitude=-104.6562, received_at=1000.0)
    assert raised.value.code == "STATION_REGION_OFFICE_INVALID"


def test_point_source_identity_conflict_fails_closed():
    payload = _point_payload()
    payload["id"] = "https://api.weather.gov/points/40.0,-100.0"
    with pytest.raises(WeatherStationRegionError) as raised:
        parse_nws_point_office(payload, requested_latitude=39.8466, requested_longitude=-104.6562, received_at=1000.0)
    assert raised.value.code == "STATION_REGION_POINT_SOURCE_IDENTITY_MISMATCH"


def test_parse_nws_office_region_binds_real_region_code():
    row = parse_nws_office_region(_office_payload(), requested_office="bou", received_at=2000.0)
    assert row.office == "BOU"
    assert row.nws_region_code == "cr"
    assert row.nws_region == "CENTRAL"
    assert row.source_role == NWS_STATION_REGION_ROLE
    assert len(row.evidence_sha256) == 64


@pytest.mark.parametrize("code,name", [
    ("er", "EASTERN"), ("sr", "SOUTHERN"), ("cr", "CENTRAL"),
    ("wr", "WESTERN"), ("pr", "PACIFIC"), ("ar", "ALASKA"),
])
def test_all_six_real_nws_regions_recognized(code, name):
    row = parse_nws_office_region(_office_payload(office="XXX", region=code), requested_office="XXX", received_at=1.0)
    assert row.nws_region == name


def test_office_region_unrecognized_code_fails_closed():
    with pytest.raises(WeatherStationRegionError) as raised:
        parse_nws_office_region(_office_payload(region="zz"), requested_office="BOU", received_at=1.0)
    assert raised.value.code == "STATION_REGION_CODE_UNRECOGNIZED"


def test_office_identity_mismatch_fails_closed():
    with pytest.raises(WeatherStationRegionError) as raised:
        parse_nws_office_region(_office_payload(office="OUN"), requested_office="BOU", received_at=1.0)
    assert raised.value.code == "STATION_REGION_OFFICE_IDENTITY_MISMATCH"


@pytest.mark.parametrize("location,field,value", [
    ("properties", "@id", "https://api.weather.gov/points/40,-100"),
    ("properties", "gridId", "OUN"),
    ("properties", "forecastOffice", "https://other.invalid/offices/BOU"),
    ("properties", "forecastOffice", "https://api.weather.gov/offices/BOU?x=1"),
    ("top", "id", "https://other.invalid/points/39.8466,-104.6562"),
    ("top", "id", "https://api.weather.gov/unrelated"),
    ("top", "id", "http://api.weather.gov/points/39.8466,-104.6562"),
    ("top", "id", "https://api.weather.gov:443/points/39.8466,-104.6562"),
    ("top", "id", "https://api.weather.gov/points/39.8466,-104.6562#other"),
    ("top", "id", None),
])
def test_all_present_point_identities_must_agree(location, field, value):
    payload = _point_payload()
    (payload if location == "top" else payload["properties"])[field] = value
    with pytest.raises(WeatherStationRegionError):
        parse_nws_point_office(payload, requested_latitude=39.8466, requested_longitude=-104.6562, received_at=1.0)


@pytest.mark.parametrize("field,value", [
    ("@id", "https://other.invalid/offices/BOU"),
    ("@id", "https://api.weather.gov/unrelated"),
    ("@id", "https://api.weather.gov/offices/OUN"),
    ("@id", None),
    ("parentOrganization", "https://api.weather.gov/offices/ERH"),
])
def test_all_present_office_identities_must_agree(field, value):
    payload = _office_payload()
    payload[field] = value
    with pytest.raises(WeatherStationRegionError):
        parse_nws_office_region(payload, requested_office="BOU", received_at=1.0)


def test_missing_point_identity_fails_closed():
    payload = _point_payload()
    del payload["id"], payload["properties"]["@id"]
    with pytest.raises(WeatherStationRegionError):
        parse_nws_point_office(payload, requested_latitude=39.8466, requested_longitude=-104.6562, received_at=1.0)


def test_equivalent_numeric_point_url_format_is_accepted():
    payload = _point_payload(lat=40.0, lon=-100.0)
    payload["id"] = "https://api.weather.gov/points/40.0000,-100"
    parse_nws_point_office(payload, requested_latitude=40.0, requested_longitude=-100.0, received_at=1.0)


@pytest.mark.parametrize("lat,lon", [(90.00001, 0), (0, -180.00001), (float("nan"), 0), (True, 0)])
def test_invalid_coordinates_rejected_before_rounding_or_http(lat, lon):
    async def run():
        client = NWSStationRegionClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: pytest.fail("unexpected HTTP")))
        try:
            with pytest.raises(WeatherStationRegionError, match="COORDINATES_INVALID"):
                await client.point_office(lat, lon)
        finally:
            await client.close()
    asyncio.run(run())


def test_client_chains_fixed_official_endpoints_and_preserves_precision():
    paths = []
    def respond(request):
        paths.append(str(request.url))
        payload = _point_payload() if len(paths) == 1 else _office_payload()
        return httpx.Response(200, json=payload)
    async def run():
        client = NWSStationRegionClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            point, office = await client.region(39.84658, -104.65622)
            assert (point.latitude, point.longitude) == (39.84658, -104.65622)
            assert point.cwa == office.office == "BOU"
            point.validate(); office.validate()
        finally:
            await client.close()
    asyncio.run(run())
    assert paths == ["https://api.weather.gov/points/39.8466,-104.6562", "https://api.weather.gov/offices/BOU"]


@pytest.mark.parametrize("failure,expected,attempts", [
    ("timeout", "POINT_TIMEOUT", 3), ("transport", "POINT_TRANSPORT", 3),
    (429, "POINT_HTTP_STATUS", 3), (503, "POINT_HTTP_STATUS", 3),
    (404, "POINT_HTTP_STATUS", 1), (302, "POINT_HTTP_STATUS", 1),
    ("json", "POINT_JSON_INVALID", 1), ("identity", "POINT_SOURCE_IDENTITY_MISMATCH", 1),
])
def test_client_failures_are_bounded_and_do_not_fetch_office(monkeypatch, failure, expected, attempts):
    from polymarket_scanner import weather_only_station_region as module
    paths = []
    async def no_sleep(delay):
        pass
    monkeypatch.setattr(module.asyncio, "sleep", no_sleep)
    def respond(request):
        paths.append(str(request.url))
        if failure == "timeout":
            raise httpx.ReadTimeout("fixture", request=request)
        if failure == "transport":
            raise httpx.ConnectError("fixture", request=request)
        if failure == "identity":
            payload = _point_payload()
            payload["properties"]["@id"] = "https://api.weather.gov/points/40,-100"
            return httpx.Response(200, json=payload)
        return httpx.Response(failure if type(failure) is int else 200, content=b"not json")
    async def run():
        client = NWSStationRegionClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        try:
            with pytest.raises(WeatherStationRegionError, match=expected):
                await client.region(39.8466, -104.6562)
        finally:
            await client.close()
    asyncio.run(run())
    assert len(paths) == attempts
    assert all("/points/" in path for path in paths)


def test_client_response_cap_stops_stream_before_entire_body(monkeypatch):
    from polymarket_scanner import weather_only_station_region as module
    monkeypatch.setattr(module, "MAX_RESPONSE_BYTES", 16 * 1024)
    class Oversized(httpx.AsyncByteStream):
        chunks = 0
        closed = False
        async def __aiter__(self):
            for _ in range(100):
                self.chunks += 1
                yield b"x" * (16 * 1024)
        async def aclose(self):
            self.closed = True
    stream = Oversized()
    async def run():
        client = NWSStationRegionClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, stream=stream)))
        try:
            with pytest.raises(WeatherStationRegionError, match="POINT_RESPONSE_CAP"):
                await client.point_office(39.8466, -104.6562)
        finally:
            await client.close()
    asyncio.run(run())
    assert stream.chunks == 2
    assert stream.closed
