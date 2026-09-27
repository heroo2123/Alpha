from __future__ import annotations

"""Strict NWS regional-office identity for supplied station coordinates.

Master upgrade I2 requires "a versioned mapping or clustering layer capable of
representing station -> city; city/station -> region ... common model/source
dependence" for regional/correlated-exposure ceilings. This adapter supplies the
region half of that layer from the official public ``api.weather.gov`` service,
using two real, free, unauthenticated endpoints chained on supplied station
coordinates (station certification is a separate gate):

- ``/points/{lat},{lon}`` resolves the real NWS county warning area (``cwa``)
  responsible for the four-decimal query point; exact supplied coordinates
  remain bound separately in the normalized evidence;
- ``/offices/{cwa}`` returns that office's real ``nwsRegion`` code.

This module performs no HTTP by default (the client class does) and never
invents, interpolates or defaults a region: an unrecognized region code, a
coordinate mismatch or a broken point->office chain fails closed. It grants no
settlement, calibration, or financial authority.
"""

import asyncio
import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from urllib.parse import urlsplit

import httpx

from .config import settings


NWS_POINTS_ENDPOINT = "https://api.weather.gov/points/{lat},{lon}"
NWS_OFFICES_ENDPOINT = "https://api.weather.gov/offices/{office}"
NWS_STATION_REGION_VERSION = "nws_point_office_region_v2_strict_identity"
NWS_STATION_REGION_ROLE = "REGION_IDENTITY_ONLY"
# Real, public NWS regional-headquarters codes (api.weather.gov ``nwsRegion``).
NWS_REGION_NAMES = {
    "er": "EASTERN",
    "sr": "SOUTHERN",
    "cr": "CENTRAL",
    "wr": "WESTERN",
    "pr": "PACIFIC",
    "ar": "ALASKA",
}
_OFFICE_RE = re.compile(r"^[A-Z]{3,4}$")
MAX_RESPONSE_BYTES = 512 * 1024
MAX_RETRIES = 3
# api.weather.gov rejects/redirects excess coordinate precision.
_COORDINATE_DECIMALS = 4


class WeatherStationRegionError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise WeatherStationRegionError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherStationRegionError(code) from None
    if not math.isfinite(number):
        raise WeatherStationRegionError(code)
    return number


def _office(value: object) -> str:
    office = str(value or "").strip().upper()
    if not _OFFICE_RE.fullmatch(office):
        raise WeatherStationRegionError("STATION_REGION_OFFICE_INVALID")
    return office


def _canonical_hash(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _coordinates(latitude: object, longitude: object) -> tuple[float, float]:
    lat = _finite(latitude, "STATION_REGION_COORDINATES_INVALID")
    lon = _finite(longitude, "STATION_REGION_COORDINATES_INVALID")
    if not -90 <= lat <= 90 or not -180 <= lon <= 180:
        raise WeatherStationRegionError("STATION_REGION_COORDINATES_INVALID")
    return lat, lon


def _official_path(value: object, code: str) -> str:
    if not isinstance(value, str) or value != value.strip():
        raise WeatherStationRegionError(code)
    try:
        url = urlsplit(value)
    except ValueError:
        raise WeatherStationRegionError(code) from None
    if (url.scheme != "https" or url.netloc != "api.weather.gov"
            or url.query or url.fragment):
        raise WeatherStationRegionError(code)
    return url.path.rstrip("/")


def _office_url(value: object, office: str, code: str) -> str:
    if _official_path(value, code) != f"/offices/{office}":
        raise WeatherStationRegionError(code)
    return NWS_OFFICES_ENDPOINT.format(office=office)


def _point_url(value: object, latitude: float, longitude: float) -> str:
    code = "STATION_REGION_POINT_SOURCE_IDENTITY_MISMATCH"
    path = _official_path(value, code)
    match = re.fullmatch(r"/points/(-?\d+(?:\.\d+)?),(-?\d+(?:\.\d+)?)", path)
    if (match is None or float(match[1]) != latitude or float(match[2]) != longitude):
        raise WeatherStationRegionError(code)
    return NWS_POINTS_ENDPOINT.format(lat=latitude, lon=longitude)


def _evidence_hash(row: dict) -> str:
    return _canonical_hash({key: value for key, value in row.items()
                            if key not in {"evidence_sha256", "settlement_authority",
                                           "calibration_label_authority",
                                           "calibrated_probability_authority", "financial_authority"}})


def _validate_evidence(row) -> None:
    value = row.as_dict()
    if (row.adapter != NWS_STATION_REGION_VERSION or row.source != "National Weather Service API"
            or row.source_role != NWS_STATION_REGION_ROLE
            or any(value[key] is not False for key in ("settlement_authority", "calibration_label_authority",
                                                       "calibrated_probability_authority", "financial_authority"))
            or not isinstance(row.source_payload_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", row.source_payload_sha256)
            or _finite(row.received_at, "STATION_REGION_RECEIPT_INVALID") < 0
            or row.evidence_sha256 != _evidence_hash(value)):
        raise WeatherStationRegionError("STATION_REGION_EVIDENCE_INTEGRITY")


@dataclass(frozen=True, slots=True)
class NWSPointOffice:
    adapter: str
    source: str
    source_url: str
    latitude: float
    longitude: float
    cwa: str
    forecast_office: str
    received_at: float
    source_payload_sha256: str
    evidence_sha256: str
    source_role: str = field(init=False, default=NWS_STATION_REGION_ROLE)
    settlement_authority: bool = field(init=False, default=False)
    calibration_label_authority: bool = field(init=False, default=False)
    calibrated_probability_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def validate(self) -> None:
        _validate_evidence(self)
        lat, lon = _coordinates(self.latitude, self.longitude)
        _point_url(self.source_url, round(lat, _COORDINATE_DECIMALS), round(lon, _COORDINATE_DECIMALS))
        if _office(self.cwa) != self.cwa:
            raise WeatherStationRegionError("STATION_REGION_OFFICE_INVALID")
        _office_url(self.forecast_office, self.cwa, "STATION_REGION_OFFICE_URL_MISMATCH")

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class NWSOfficeRegion:
    adapter: str
    source: str
    source_url: str
    office: str
    nws_region_code: str
    nws_region: str
    received_at: float
    source_payload_sha256: str
    evidence_sha256: str
    source_role: str = field(init=False, default=NWS_STATION_REGION_ROLE)
    settlement_authority: bool = field(init=False, default=False)
    calibration_label_authority: bool = field(init=False, default=False)
    calibrated_probability_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def validate(self) -> None:
        _validate_evidence(self)
        if _office(self.office) != self.office:
            raise WeatherStationRegionError("STATION_REGION_OFFICE_INVALID")
        if NWS_REGION_NAMES.get(self.nws_region_code) != self.nws_region:
            raise WeatherStationRegionError("STATION_REGION_CODE_UNRECOGNIZED")
        _office_url(self.source_url, self.office, "STATION_REGION_OFFICE_SOURCE_IDENTITY_MISMATCH")

    def as_dict(self) -> dict:
        return asdict(self)


def parse_nws_point_office(
    payload: object,
    *,
    requested_latitude: float,
    requested_longitude: float,
    received_at: float,
) -> NWSPointOffice:
    # Keep exact station identity; only the HTTP query uses four decimals.
    latitude, longitude = _coordinates(requested_latitude, requested_longitude)
    query_latitude = round(latitude, _COORDINATE_DECIMALS)
    query_longitude = round(longitude, _COORDINATE_DECIMALS)
    receipt = _finite(received_at, "STATION_REGION_RECEIPT_INVALID")
    if receipt < 0.0:
        raise WeatherStationRegionError("STATION_REGION_RECEIPT_INVALID")
    if not isinstance(payload, dict) or payload.get("type") != "Feature":
        raise WeatherStationRegionError("STATION_REGION_POINT_ENVELOPE_INVALID")

    properties = payload.get("properties")
    geometry = payload.get("geometry")
    if not isinstance(properties, dict):
        raise WeatherStationRegionError("STATION_REGION_POINT_PROPERTIES_INVALID")
    if not isinstance(geometry, dict) or geometry.get("type") != "Point":
        raise WeatherStationRegionError("STATION_REGION_POINT_GEOMETRY_INVALID")

    coordinates = geometry.get("coordinates")
    if not isinstance(coordinates, list) or len(coordinates) < 2:
        raise WeatherStationRegionError("STATION_REGION_POINT_GEOMETRY_INVALID")
    echoed_longitude = _finite(coordinates[0], "STATION_REGION_POINT_GEOMETRY_INVALID")
    echoed_latitude = _finite(coordinates[1], "STATION_REGION_POINT_GEOMETRY_INVALID")
    if echoed_latitude != query_latitude or echoed_longitude != query_longitude:
        raise WeatherStationRegionError("STATION_REGION_POINT_COORDINATE_MISMATCH")

    cwa = _office(properties.get("cwa"))
    forecast_office = str(properties.get("forecastOffice") or "").strip()
    forecast_office = _office_url(forecast_office, cwa, "STATION_REGION_OFFICE_URL_MISMATCH")
    if "gridId" in properties and _office(properties["gridId"]) != cwa:
        raise WeatherStationRegionError("STATION_REGION_OFFICE_CHAIN_MISMATCH")
    candidates = [obj[key] for obj, key in ((payload, "id"), (properties, "@id")) if key in obj]
    if not candidates:
        raise WeatherStationRegionError("STATION_REGION_POINT_SOURCE_IDENTITY_MISMATCH")
    for value in candidates:
        source_url = _point_url(value, query_latitude, query_longitude)

    source_payload_sha = _canonical_hash(payload)
    values = dict(
        adapter=NWS_STATION_REGION_VERSION,
        source="National Weather Service API",
        source_url=source_url,
        latitude=latitude,
        longitude=longitude,
        cwa=cwa,
        forecast_office=forecast_office,
        received_at=receipt,
        source_payload_sha256=source_payload_sha,
        source_role=NWS_STATION_REGION_ROLE,
    )
    evidence = _evidence_hash(values)
    values.pop("source_role")
    row = NWSPointOffice(**values, evidence_sha256=evidence)
    row.validate()
    return row


def parse_nws_office_region(
    payload: object,
    *,
    requested_office: str,
    received_at: float,
) -> NWSOfficeRegion:
    office = _office(requested_office)
    receipt = _finite(received_at, "STATION_REGION_RECEIPT_INVALID")
    if receipt < 0.0:
        raise WeatherStationRegionError("STATION_REGION_RECEIPT_INVALID")
    if not isinstance(payload, dict):
        raise WeatherStationRegionError("STATION_REGION_OFFICE_ENVELOPE_INVALID")

    actual_office = _office(payload.get("id"))
    if actual_office != office:
        raise WeatherStationRegionError("STATION_REGION_OFFICE_IDENTITY_MISMATCH")

    region_code = str(payload.get("nwsRegion") or "").strip().lower()
    region = NWS_REGION_NAMES.get(region_code)
    if region is None:
        raise WeatherStationRegionError("STATION_REGION_CODE_UNRECOGNIZED")

    source_url = _office_url(payload.get("@id"), office, "STATION_REGION_OFFICE_SOURCE_IDENTITY_MISMATCH")
    if "parentOrganization" in payload:
        _office_url(payload["parentOrganization"], region_code.upper()+"H",
                    "STATION_REGION_OFFICE_SOURCE_IDENTITY_MISMATCH")

    source_payload_sha = _canonical_hash(payload)
    values = dict(
        adapter=NWS_STATION_REGION_VERSION,
        source="National Weather Service API",
        source_url=source_url,
        office=office,
        nws_region_code=region_code,
        nws_region=region,
        received_at=receipt,
        source_payload_sha256=source_payload_sha,
        source_role=NWS_STATION_REGION_ROLE,
    )
    evidence = _evidence_hash(values)
    values.pop("source_role")
    row = NWSOfficeRegion(**values, evidence_sha256=evidence)
    row.validate()
    return row


class NWSStationRegionClient:
    """Bounded read-only point->office->region client. No fallback, no guessing."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2, keepalive_expiry=20.0),
            headers={
                "User-Agent": "polymarket-weather-calibration/0.2 (+https://github.com/heroo2123/Alpha)",
                "Accept": "application/geo+json",
            },
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def _get(self, url: str, *, timeout_code: str, transport_code: str, status_code: str,
                    cap_code: str, json_code: str):
        for attempt in range(MAX_RETRIES):
            try:
                async with self.http.stream("GET", url) as response:
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt + 1 >= MAX_RETRIES:
                            raise WeatherStationRegionError(status_code)
                        retry = True
                    else:
                        retry = False
                        if not 200 <= response.status_code < 300:
                            raise WeatherStationRegionError(status_code)
                        content = bytearray()
                        async for chunk in response.aiter_bytes(chunk_size=16 * 1024):
                            if len(content) + len(chunk) > MAX_RESPONSE_BYTES:
                                raise WeatherStationRegionError(cap_code)
                            content.extend(chunk)
                        received = time.time()
                        try:
                            payload = json.loads(content)
                        except (ValueError, UnicodeError):
                            raise WeatherStationRegionError(json_code) from None
                        return payload, received
                if retry:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
            except httpx.TimeoutException:
                if attempt + 1 >= MAX_RETRIES:
                    raise WeatherStationRegionError(timeout_code)
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            except httpx.RequestError:
                if attempt + 1 >= MAX_RETRIES:
                    raise WeatherStationRegionError(transport_code)
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue

        raise WeatherStationRegionError(transport_code)

    async def point_office(self, latitude: float, longitude: float) -> NWSPointOffice:
        latitude, longitude = _coordinates(latitude, longitude)
        lat = round(latitude, _COORDINATE_DECIMALS)
        lon = round(longitude, _COORDINATE_DECIMALS)
        url = NWS_POINTS_ENDPOINT.format(lat=lat, lon=lon)
        payload, received = await self._get(
            url,
            timeout_code="STATION_REGION_POINT_TIMEOUT",
            transport_code="STATION_REGION_POINT_TRANSPORT",
            status_code="STATION_REGION_POINT_HTTP_STATUS",
            cap_code="STATION_REGION_POINT_RESPONSE_CAP",
            json_code="STATION_REGION_POINT_JSON_INVALID",
        )
        return parse_nws_point_office(payload, requested_latitude=latitude, requested_longitude=longitude, received_at=received)

    async def office_region(self, office: str) -> NWSOfficeRegion:
        office_id = _office(office)
        url = NWS_OFFICES_ENDPOINT.format(office=office_id)
        payload, received = await self._get(
            url,
            timeout_code="STATION_REGION_OFFICE_TIMEOUT",
            transport_code="STATION_REGION_OFFICE_TRANSPORT",
            status_code="STATION_REGION_OFFICE_HTTP_STATUS",
            cap_code="STATION_REGION_OFFICE_RESPONSE_CAP",
            json_code="STATION_REGION_OFFICE_JSON_INVALID",
        )
        return parse_nws_office_region(payload, requested_office=office_id, received_at=received)

    async def region(self, latitude: float, longitude: float) -> tuple[NWSPointOffice, NWSOfficeRegion]:
        point = await self.point_office(latitude, longitude)
        office = await self.office_region(point.cwa)
        return point, office
