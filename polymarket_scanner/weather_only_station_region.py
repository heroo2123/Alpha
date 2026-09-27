from __future__ import annotations

"""Strict NWS regional-office identity for a certified station's real coordinates.

Master upgrade I2 requires "a versioned mapping or clustering layer capable of
representing station -> city; city/station -> region ... common model/source
dependence" for regional/correlated-exposure ceilings. This adapter supplies the
region half of that layer from the official public ``api.weather.gov`` service,
using exactly two real, free, unauthenticated endpoints chained on a certified
station's own coordinates:

- ``/points/{lat},{lon}`` resolves the real NWS county warning area (``cwa``)
  responsible for that exact point;
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

import httpx

from .config import settings


NWS_POINTS_ENDPOINT = "https://api.weather.gov/points/{lat},{lon}"
NWS_OFFICES_ENDPOINT = "https://api.weather.gov/offices/{office}"
NWS_STATION_REGION_VERSION = "nws_point_office_region_v1_strict_identity"
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

    def as_dict(self) -> dict:
        return asdict(self)


def parse_nws_point_office(
    payload: object,
    *,
    requested_latitude: float,
    requested_longitude: float,
    received_at: float,
) -> NWSPointOffice:
    latitude = round(_finite(requested_latitude, "STATION_REGION_COORDINATES_INVALID"), _COORDINATE_DECIMALS)
    longitude = round(_finite(requested_longitude, "STATION_REGION_COORDINATES_INVALID"), _COORDINATE_DECIMALS)
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        raise WeatherStationRegionError("STATION_REGION_COORDINATES_INVALID")
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
    if (round(echoed_latitude, _COORDINATE_DECIMALS) != latitude
            or round(echoed_longitude, _COORDINATE_DECIMALS) != longitude):
        raise WeatherStationRegionError("STATION_REGION_POINT_COORDINATE_MISMATCH")

    cwa = _office(properties.get("cwa"))
    forecast_office = str(properties.get("forecastOffice") or "").strip()
    expected_suffix = f"/offices/{cwa}"
    if not forecast_office or not forecast_office.rstrip("/").upper().endswith(expected_suffix.upper()):
        raise WeatherStationRegionError("STATION_REGION_OFFICE_URL_MISMATCH")

    expected_id_suffix = f"/points/{latitude},{longitude}"
    candidates = [payload.get("id"), properties.get("@id")]
    source_url = next(
        (str(value).strip() for value in candidates if isinstance(value, str) and str(value).strip()),
        NWS_POINTS_ENDPOINT.format(lat=latitude, lon=longitude),
    )
    if "/points/" in source_url and not source_url.rstrip("/").endswith(expected_id_suffix):
        raise WeatherStationRegionError("STATION_REGION_POINT_SOURCE_IDENTITY_MISMATCH")

    source_payload_sha = _canonical_hash(payload)
    evidence = _canonical_hash({
        "adapter": NWS_STATION_REGION_VERSION,
        "source": "National Weather Service API",
        "source_url": source_url,
        "latitude": latitude,
        "longitude": longitude,
        "cwa": cwa,
        "forecast_office": forecast_office,
        "source_payload_sha256": source_payload_sha,
        "source_role": NWS_STATION_REGION_ROLE,
    })
    return NWSPointOffice(
        adapter=NWS_STATION_REGION_VERSION,
        source="National Weather Service API",
        source_url=source_url,
        latitude=latitude,
        longitude=longitude,
        cwa=cwa,
        forecast_office=forecast_office,
        received_at=receipt,
        source_payload_sha256=source_payload_sha,
        evidence_sha256=evidence,
    )


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

    expected_suffix = f"/offices/{office}"
    candidates = [payload.get("@id"), payload.get("id") and NWS_OFFICES_ENDPOINT.format(office=payload.get("id"))]
    source_url = next(
        (str(value).strip() for value in candidates if isinstance(value, str) and str(value).strip()),
        NWS_OFFICES_ENDPOINT.format(office=office),
    )
    if "/offices/" in source_url and not source_url.rstrip("/").upper().endswith(expected_suffix.upper()):
        raise WeatherStationRegionError("STATION_REGION_OFFICE_SOURCE_IDENTITY_MISMATCH")

    source_payload_sha = _canonical_hash(payload)
    evidence = _canonical_hash({
        "adapter": NWS_STATION_REGION_VERSION,
        "source": "National Weather Service API",
        "source_url": source_url,
        "office": office,
        "nws_region_code": region_code,
        "nws_region": region,
        "source_payload_sha256": source_payload_sha,
        "source_role": NWS_STATION_REGION_ROLE,
    })
    return NWSOfficeRegion(
        adapter=NWS_STATION_REGION_VERSION,
        source="National Weather Service API",
        source_url=source_url,
        office=office,
        nws_region_code=region_code,
        nws_region=region,
        received_at=receipt,
        source_payload_sha256=source_payload_sha,
        evidence_sha256=evidence,
    )


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
                response = await self.http.get(url)
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

            if response.status_code == 429 or response.status_code >= 500:
                if attempt + 1 >= MAX_RETRIES:
                    raise WeatherStationRegionError(status_code)
                await asyncio.sleep(0.5 * (2 ** attempt))
                continue
            if response.status_code >= 400:
                raise WeatherStationRegionError(status_code)
            if len(response.content) > MAX_RESPONSE_BYTES:
                raise WeatherStationRegionError(cap_code)
            received = time.time()
            try:
                payload = response.json()
            except Exception:
                raise WeatherStationRegionError(json_code)
            return payload, received
        raise WeatherStationRegionError(transport_code)

    async def point_office(self, latitude: float, longitude: float) -> NWSPointOffice:
        lat = round(_finite(latitude, "STATION_REGION_COORDINATES_INVALID"), _COORDINATE_DECIMALS)
        lon = round(_finite(longitude, "STATION_REGION_COORDINATES_INVALID"), _COORDINATE_DECIMALS)
        url = NWS_POINTS_ENDPOINT.format(lat=lat, lon=lon)
        payload, received = await self._get(
            url,
            timeout_code="STATION_REGION_POINT_TIMEOUT",
            transport_code="STATION_REGION_POINT_TRANSPORT",
            status_code="STATION_REGION_POINT_HTTP_STATUS",
            cap_code="STATION_REGION_POINT_RESPONSE_CAP",
            json_code="STATION_REGION_POINT_JSON_INVALID",
        )
        return parse_nws_point_office(payload, requested_latitude=lat, requested_longitude=lon, received_at=received)

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
