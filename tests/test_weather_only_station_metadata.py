from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_station_metadata import (
    NWS_STATION_METADATA_ROLE,
    NWS_STATION_METADATA_VERSION,
    WeatherStationMetadataError,
    parse_nws_station_metadata,
)


def _payload(*, station="KLGA", timezone="America/New_York", coordinates=None):
    return {
        "id": f"https://api.weather.gov/stations/{station}",
        "type": "Feature",
        "geometry": {
            "type": "Point",
            "coordinates": coordinates or [-73.8803, 40.7794],
        },
        "properties": {
            "@id": f"https://api.weather.gov/stations/{station}",
            "stationIdentifier": station,
            "name": "La Guardia Airport",
            "timeZone": timezone,
        },
    }


def test_parse_nws_station_metadata_binds_station_coordinate_timezone_and_no_authority():
    row = parse_nws_station_metadata(
        _payload(),
        requested_station="klga",
        received_at=1000.0,
    )
    assert row.adapter == NWS_STATION_METADATA_VERSION
    assert row.source_role == NWS_STATION_METADATA_ROLE
    assert row.station == "KLGA"
    assert row.latitude == pytest.approx(40.7794)
    assert row.longitude == pytest.approx(-73.8803)
    assert row.timezone == "America/New_York"
    assert len(row.source_payload_sha256) == 64
    assert len(row.evidence_sha256) == 64
    assert row.settlement_authority is False
    assert row.calibration_label_authority is False
    assert row.calibrated_probability_authority is False
    assert row.financial_authority is False


def test_station_identity_mismatch_fails_closed():
    with pytest.raises(WeatherStationMetadataError) as raised:
        parse_nws_station_metadata(
            _payload(station="KJFK"),
            requested_station="KLGA",
            received_at=1000.0,
        )
    assert raised.value.code == "STATION_METADATA_IDENTITY_MISMATCH"


def test_source_url_identity_conflict_fails_closed():
    payload = _payload()
    payload["id"] = "https://api.weather.gov/stations/KJFK"
    with pytest.raises(WeatherStationMetadataError) as raised:
        parse_nws_station_metadata(payload, requested_station="KLGA", received_at=1000.0)
    assert raised.value.code == "STATION_METADATA_SOURCE_IDENTITY_MISMATCH"


@pytest.mark.parametrize(
    "timezone",
    ["", "Not/A_Real_Timezone"],
)
def test_missing_or_invalid_timezone_fails_closed(timezone):
    with pytest.raises(WeatherStationMetadataError) as raised:
        parse_nws_station_metadata(
            _payload(timezone=timezone),
            requested_station="KLGA",
            received_at=1000.0,
        )
    assert raised.value.code in {
        "STATION_METADATA_TIMEZONE_MISSING",
        "STATION_METADATA_TIMEZONE_INVALID",
    }


@pytest.mark.parametrize(
    "coordinates",
    [
        [181.0, 40.0],
        [-73.0, 91.0],
        [float("nan"), 40.0],
        [-73.0],
        "-73,40",
    ],
)
def test_invalid_coordinates_fail_closed(coordinates):
    with pytest.raises(WeatherStationMetadataError) as raised:
        parse_nws_station_metadata(
            _payload(coordinates=coordinates),
            requested_station="KLGA",
            received_at=1000.0,
        )
    assert raised.value.code == "STATION_METADATA_COORDINATES_INVALID"
