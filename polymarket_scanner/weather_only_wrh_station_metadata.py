from __future__ import annotations

"""Secret-safe WRH/Synoptic station metadata for global forecast location identity.

Current NWS/WRH Polymarket temperature contracts may reference stations outside the
coverage of ``api.weather.gov/stations/{id}``.  The WRH viewer already relies on
Synoptic Data.  This adapter reuses the pinned WRH browser transport to request the
Synoptic Metadata service for one exact station and extracts only latitude,
longitude, timezone and supported-network identity needed by the GEFS research
forecast path.

The browser token is discovered from weather.gov, held only in process memory and
never returned, logged or persisted.  Station metadata is location identity only;
it grants no settlement, calibration-label, probability or financial authority.
"""

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .config import settings
from .weather_only_wrh import WRH_VIEWER_SCRIPT_SHA256, WRHSourceError, _normalized_network
from .weather_only_wrh_client import (
    WRH_BROWSER_ORIGIN,
    WRH_TIMESERIES_PAGE,
    _discover_api_key_script,
    _discover_viewer_script,
    _extract_browser_token,
    _verify_viewer_credential_contract,
)


WRH_STATION_METADATA_ENDPOINT = "https://api.synopticdata.com/v2/stations/metadata"
WRH_STATION_METADATA_VERSION = "wrh_synoptic_station_metadata_v1_pinned_viewer_browser_token"
WRH_STATION_METADATA_ROLE = "FORECAST_LOCATION_IDENTITY_ONLY"
MAX_RESPONSE_BYTES = 1024 * 1024
TOKEN_CACHE_SECONDS = 300.0
_STATION_RE = re.compile(r"^[A-Z0-9]{4}$")


class WRHStationMetadataError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical_hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise WRHStationMetadataError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WRHStationMetadataError(code) from None
    if not math.isfinite(number):
        raise WRHStationMetadataError(code)
    return number


def _station(value: object) -> str:
    station = str(value or "").strip().upper()
    if not _STATION_RE.fullmatch(station):
        raise WRHStationMetadataError("WRH_STATION_METADATA_STATION_INVALID")
    return station


def _identity_payload(
    *,
    station: str,
    latitude: float,
    longitude: float,
    timezone_name: str,
    raw_network: str,
    normalized_network: str,
    source_payload_sha256: str,
) -> dict:
    return {
        "adapter": WRH_STATION_METADATA_VERSION,
        "source": "WRH Synoptic Data Metadata",
        "source_url": WRH_STATION_METADATA_ENDPOINT,
        "station": station,
        "latitude": latitude,
        "longitude": longitude,
        "timezone": timezone_name,
        "raw_network": raw_network,
        "normalized_network": normalized_network,
        "viewer_script_sha256": WRH_VIEWER_SCRIPT_SHA256,
        "source_payload_sha256": source_payload_sha256,
        "source_role": WRH_STATION_METADATA_ROLE,
    }


@dataclass(frozen=True, slots=True)
class WRHStationMetadata:
    adapter: str
    source: str
    source_url: str
    station: str
    latitude: float
    longitude: float
    timezone: str
    raw_network: str
    normalized_network: str
    viewer_script_sha256: str
    received_at: float
    source_payload_sha256: str
    evidence_sha256: str
    source_role: str = field(init=False, default=WRH_STATION_METADATA_ROLE)
    token_persisted: bool = field(init=False, default=False)
    settlement_authority: bool = field(init=False, default=False)
    calibration_label_authority: bool = field(init=False, default=False)
    calibrated_probability_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def parse_wrh_synoptic_station_metadata(
    payload: object,
    *,
    requested_station: str,
    received_at: float,
) -> WRHStationMetadata:
    station = _station(requested_station)
    receipt = _finite(received_at, "WRH_STATION_METADATA_RECEIPT_INVALID")
    if receipt < 0.0:
        raise WRHStationMetadataError("WRH_STATION_METADATA_RECEIPT_INVALID")
    if not isinstance(payload, dict):
        raise WRHStationMetadataError("WRH_STATION_METADATA_ENVELOPE_INVALID")
    summary = payload.get("SUMMARY")
    if not isinstance(summary, dict) or str(summary.get("RESPONSE_MESSAGE") or "") != "OK":
        raise WRHStationMetadataError("WRH_STATION_METADATA_RESPONSE_NOT_OK")
    rows = payload.get("STATION")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise WRHStationMetadataError("WRH_STATION_METADATA_STATION_ENVELOPE_INVALID")
    row = rows[0]
    actual = _station(row.get("STID"))
    if actual != station:
        raise WRHStationMetadataError("WRH_STATION_METADATA_IDENTITY_MISMATCH")

    latitude = _finite(row.get("LATITUDE"), "WRH_STATION_METADATA_COORDINATES_INVALID")
    longitude = _finite(row.get("LONGITUDE"), "WRH_STATION_METADATA_COORDINATES_INVALID")
    if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
        raise WRHStationMetadataError("WRH_STATION_METADATA_COORDINATES_INVALID")

    timezone_name = str(row.get("TIMEZONE") or "").strip()
    if not timezone_name:
        raise WRHStationMetadataError("WRH_STATION_METADATA_TIMEZONE_MISSING")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise WRHStationMetadataError("WRH_STATION_METADATA_TIMEZONE_INVALID") from None

    try:
        raw_network, normalized_network = _normalized_network(row.get("SHORTNAME"))
    except WRHSourceError as exc:
        raise WRHStationMetadataError(f"WRH_STATION_METADATA_NETWORK:{exc.code}") from None

    source_payload_sha = _canonical_hash(payload)
    evidence_sha = _canonical_hash(_identity_payload(
        station=station,
        latitude=latitude,
        longitude=longitude,
        timezone_name=timezone_name,
        raw_network=raw_network,
        normalized_network=normalized_network,
        source_payload_sha256=source_payload_sha,
    ))
    return WRHStationMetadata(
        adapter=WRH_STATION_METADATA_VERSION,
        source="WRH Synoptic Data Metadata",
        source_url=WRH_STATION_METADATA_ENDPOINT,
        station=station,
        latitude=latitude,
        longitude=longitude,
        timezone=timezone_name,
        raw_network=raw_network,
        normalized_network=normalized_network,
        viewer_script_sha256=WRH_VIEWER_SCRIPT_SHA256,
        received_at=receipt,
        source_payload_sha256=source_payload_sha,
        evidence_sha256=evidence_sha,
    )


async def _safe_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None,
    code: str,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    try:
        response = await client.get(url, params=params, headers=headers)
    except httpx.HTTPError:
        # Do not chain token-bearing request objects into an exception message.
        raise WRHStationMetadataError(code) from None
    if response.status_code < 200 or response.status_code >= 300:
        raise WRHStationMetadataError(code)
    if len(response.content) > MAX_RESPONSE_BYTES:
        raise WRHStationMetadataError(f"{code}_RESPONSE_CAP")
    return response


class WRHStationMetadataClient:
    """Read-only global WRH metadata client using an ephemeral NWS browser token."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            follow_redirects=True,
            limits=httpx.Limits(max_connections=3, max_keepalive_connections=2, keepalive_expiry=20.0),
            headers={
                "User-Agent": "polymarket-weather-calibration-wrh-metadata/0.1 (+https://github.com/heroo2123/Alpha)",
                "Accept": "*/*",
            },
        )
        self._browser_token: str | None = None
        self._token_expires_monotonic = 0.0

    async def close(self) -> None:
        self._browser_token = None
        self._token_expires_monotonic = 0.0
        await self.http.aclose()

    async def _token(self, *, force_refresh: bool = False) -> str:
        now = time.monotonic()
        if (
            not force_refresh
            and self._browser_token is not None
            and now < self._token_expires_monotonic
        ):
            return self._browser_token

        shell = await _safe_get(
            self.http,
            WRH_TIMESERIES_PAGE,
            params={"site": "KLGA", "hourly": "true", "obs": "tabular"},
            code="WRH_STATION_METADATA_SHELL_HTTP_ERROR",
        )
        viewer_url = _discover_viewer_script(shell.text)
        key_url = _discover_api_key_script(shell.text)
        viewer = await _safe_get(
            self.http,
            viewer_url,
            params=None,
            code="WRH_STATION_METADATA_VIEWER_HTTP_ERROR",
        )
        if hashlib.sha256(viewer.content).hexdigest() != WRH_VIEWER_SCRIPT_SHA256:
            raise WRHStationMetadataError("WRH_STATION_METADATA_VIEWER_SHA_MISMATCH")
        try:
            _verify_viewer_credential_contract(viewer.text)
        except WRHSourceError as exc:
            raise WRHStationMetadataError(f"WRH_STATION_METADATA_VIEWER:{exc.code}") from None
        key_script = await _safe_get(
            self.http,
            key_url,
            params=None,
            code="WRH_STATION_METADATA_KEY_HTTP_ERROR",
        )
        try:
            token = _extract_browser_token(key_script.text)
        except WRHSourceError as exc:
            raise WRHStationMetadataError(f"WRH_STATION_METADATA_TOKEN:{exc.code}") from None
        self._browser_token = token
        self._token_expires_monotonic = time.monotonic() + TOKEN_CACHE_SECONDS
        return token

    async def station(self, station: str) -> WRHStationMetadata:
        station_id = _station(station)
        response: httpx.Response | None = None
        for attempt in range(2):
            token = await self._token(force_refresh=attempt > 0)
            try:
                response = await _safe_get(
                    self.http,
                    WRH_STATION_METADATA_ENDPOINT,
                    params={"stid": station_id, "complete": 1, "token": token},
                    headers={"Origin": WRH_BROWSER_ORIGIN},
                    code="WRH_STATION_METADATA_BACKEND_HTTP_ERROR",
                )
                break
            except WRHStationMetadataError as exc:
                if exc.code != "WRH_STATION_METADATA_BACKEND_HTTP_ERROR" or attempt == 1:
                    raise
                self._browser_token = None
                self._token_expires_monotonic = 0.0
        if response is None:
            raise WRHStationMetadataError("WRH_STATION_METADATA_BACKEND_HTTP_ERROR")
        received = time.time()
        try:
            payload = response.json()
        except Exception:
            raise WRHStationMetadataError("WRH_STATION_METADATA_JSON_INVALID") from None
        return parse_wrh_synoptic_station_metadata(
            payload,
            requested_station=station_id,
            received_at=received,
        )
