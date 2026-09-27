from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_station_region import (
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


def test_point_coordinate_precision_is_rounded_before_binding():
    row = parse_nws_point_office(
        _point_payload(), requested_latitude=39.84658, requested_longitude=-104.65622, received_at=1000.0,
    )
    assert row.latitude == pytest.approx(39.8466)
    assert row.longitude == pytest.approx(-104.6562)


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
