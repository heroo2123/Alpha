from __future__ import annotations

"""Strict NWS station-location metadata for weather-only forecast collection.

The GEFS forecast adapter needs the exact settlement station's latitude, longitude and
local timezone.  Those inputs are location identity only: they do not represent WRH
settlement state and they grant neither calibration-label nor financial authority.

This adapter intentionally uses the official ``api.weather.gov/stations/{id}``
GeoJSON station endpoint instead of hard-coded city coordinates.  Current NWS API
documentation identifies ``/stations/{stationId}`` as a supported endpoint and notes
that GeoJSON is the typical default format.  Schema/identity drift fails closed.
"""

import asyncio
import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .config import settings


NWS_STATION_ENDPOINT = "https://api.weather.gov/stations/{station}"
NWS_STATION_METADATA_VERSION = "nws_station_geojson_metadata_v1_strict_identity"
NWS_STATION_METADATA_ROLE = "FORECAST_LOCATION_IDENTITY_ONLY"
_STATION_RE = re.compile(r"^[A-Z0-9]{4}$")
MAX_RESPONSE_BYTES = 512 * 1024
MAX_RETRIES = 3


class WeatherStationMetadataError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise WeatherStationMetadataError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherStationMetadataError(code) from None
    if not math.isfinite(number):
        raise WeatherStationMetadataError(code)
    return number


def _station(value: object) -> str:
    station = str(value or "").strip().upper()
    if not _STATION_RE.fullmatch(station):
        raise WeatherStationMetadataError("STATION_METADATA_STATION_INVALID")
    return station


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _identity_payload(
    *,
    station: str,
    latitude: float,
    longitude: float,
    timezone_name: str,
    source_url: str,
    source_payload_sha256: str,
) -> dict:
    return {
        "adapter": NWS_STATION_METADATA_VERSION,
        "source": "National Weather Service API",
        "source_url": source_url,
        "station": station,
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone_name,
        "source_payload_sha256": source_payload_sha256,
        "source_role": NWS_STATION_METADATA_ROLE,
    }


@dataclass(frozen=True, slots=True)
class NWSStationMetadata:
    adapter: str
    source: str
    source_url: str
    station: str
    latitude: float
    longitude: float
    timezone: str
    received_at: float
    source_payload_sha256: str
    evidence_sha256: str
    source_role: str = field(init=False, default=NWS_STATION_METADATA_ROLE)
    settlement_authority: bool = field(init=False, default=False)
    calibration_label_authority: bool = field(init=False, default=False)
    calibrated_probability_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def parse_nws_station_metadata(
    payload: object,
    *,
    requested_station: str,
    received_at: float,
) -> NWSStationMetadata:
    station = _station(requested_station)
    receipt = _finite(received_at, "STATION_METADATA_RECEIPT_INVALID")
    if receipt < 0.0:
        raise WeatherStationMetadataError("STATION_METADATA_RECEIPT_INVALID")
    if not isinstance(payload, dict) or payload.get("type") != "Feature":
        raise WeatherStationMetadataError("STATION_METADATA_ENVELOPE_INVALID")

    properties = payload.get("properties")
    geometry = payload.get("geometry")
    if not isinstance(properties, dict):
        raise WeatherStationMetadataError("STATION_METADATA_PROPERTIES_INVALID")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        raise WeatherStationMetadataError("STATION_METADATA_GEOMETRY_INVALID")

    actual_station = _station(properties.get("stationIdentifier"))
    if actual_station != station:
        raise WeatherStationMetadataError("STATION_METADATA_IDENTITY_MISMATCH")

    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise WeatherStationMetadataError("STATION_METADATA_COORDINATES_INVALID")
    longitude = _finite(coordinates[0], "STATION_METADATA_COORDINATES_INVALID")
    latitude = _finite(coordinates[1], "STATION_METADATA_COORDINATES_INVALID")
    if not -180.0 <= longitude <= 180.0 or not -90.0 <= latitude <= 90.0:
        raise WeatherStationMetadataError("STATION_METADATA_COORDINATES_INVALID")

    timezone_name = str(properties.get("timeZone") or "").strip()
    if not timezone_name:
        raise WeatherStationMetadataError("STATION_METADATA_TIMEZONE_MISSING")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise WeatherStationMetadataError("STATION_METADATA_TIMEZONE_INVALID") from None

    expected_suffix = f"/stations/{station}"
    candidates = [payload.get("id"), properties.get("@id")]
    source_url = next(
        (str(value).strip() for value in candidates if isinstance(value, str) and str(value).strip()),
        NWS_STATION_ENDPOINT.format(station=station),
    )
    if "/stations/" in source_url and not source_url.rstrip("/").upper().endswith(expected_suffix.upper()):
        raise WeatherStationMetadataError("STATION_METADATA_SOURCE_IDENTITY_MISMATCH")

    source_payload_sha = _canonical_hash(payload)
    evidence = _canonical_hash(_identity_payload(
        station=station,
        latitude=latitude,
        longitude=longitude,
        timezone_name=timezone_name,
        source_url=source_url,
        source_payload_sha256=source_payload_sha,
    ))
    return NWSStationMetadata(
        adapter=NWS_STATION_METADATA_VERSION,
        source="National Weather Service API",
        source_url=source_url,
        station=station,
        latitude=latitude,
        longitude=longitude,
        timezone=timezone_name,
        received_at=receipt,
        source_payload_sha256=source_payload_sha,
        evidence_sha256=evidence,
    )


class NWSStationMetadataClient:
    """Bounded read-only station metadata client with explicit retry pacing."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2, keepalive_expiry=20.0),
            headers={
                "User-Agent": "polymarket-weather-calibration/0.1 (+https://github.com/heroo2123/Alpha)",
                "Accept": "application/geo+json",
            },
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def station(self, station: str) -> NWSStationMetadata:
        station_id = _station(station)
        url = NWS_STATION_ENDPOINT.format(station=station_id)
        for attempt in range(MAX_RETRIES):
            try:
                response = await self.http.get(url)
            except httpx.TimeoutException:
                if attempt + 1 >= MAX_RETRIES:
                    raise WeatherStationMetadataError("STATION_METADATA_TIMEOUT")
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            except httpx.RequestError:
                if attempt + 1 >= MAX_RETRIES:
                    raise WeatherStationMetadataError("STATION_METADATA_TRANSPORT")
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 >= MAX_RETRIES:
                    raise WeatherStationMetadataError("STATION_METADATA_HTTP_STATUS")
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            if response.status_code >= 400:
                raise WeatherStationMetadataError("STATION_METADATA_HTTP_STATUS")
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise WeatherStationMetadataError("STATION_METADATA_RESPONSE_CAP")
            received = time.time()
            try:
                payload = response.json()
            except Exception:
                raise WeatherStationMetadataError("STATION_METADATA_JSON_INVALID")
            return parse_nws_station_metadata(
                payload,
                requested_station=station_id,
                received_at=received,
            )
        raise WeatherStationMetadataError("STATION_METADATA_RETRY_EXHAUSTED")
