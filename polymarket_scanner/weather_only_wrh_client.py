from __future__ import annotations

"""Secret-safe live transport for the exact NWS WRH Hourly Data adapter.

Synoptic ``start``/``end`` request timestamps are UTC even when ``obtimezone=local``
changes the timestamps returned in the response.  The client therefore resolves the
station timezone first, converts each local calendar boundary independently to UTC,
and only then requests the target day plus following day.  This preserves early
local-day observations east of UTC and 23/25-hour DST days.

The browser credential served by NWS is held only in memory and never returned,
persisted or logged.  Successful transport still grants no settlement, calibration
label, probability or financial authority.
"""

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .weather_only_wrh import (
    WRH_SYNOPTIC_ENDPOINT,
    WRH_VIEWER_SCRIPT_SHA256,
    WRH_VIEWER_SCRIPT_URL,
    WRHSourceError,
    WRHSourceSnapshot,
    _normalized_network,
    parse_synoptic_wrh_hourly_snapshot,
)


WRH_LIVE_CLIENT_VERSION = "nws_wrh_live_transport_v5_local_day_utc_bounds_units_asof"
WRH_TIMESERIES_PAGE = "https://www.weather.gov/wrh/timeseries"
WRH_BROWSER_ORIGIN = "https://www.weather.gov"
WRH_API_KEY_SCRIPT_PATH = "/source/wrh/apiKey.js"
WRH_BROWSER_TOKEN_IDENTIFIER = "mesoToken"
WRH_STATION_METADATA_ENDPOINT = "https://api.synopticdata.com/v2/stations/metadata"
WRH_LIVE_QUERY_PROFILE = "WRH_LOCAL_TARGET_PLUS_FOLLOWING_DATE_UTC_BOUNDS_ENGLISH_HOURLY"

_SCRIPT_RE = re.compile(r'<script[^>]+src=["\']([^"\']+)["\']', re.I)
_MESO_TOKEN_ASSIGNMENT_RE = re.compile(
    r"(?i)(?:\b(?:var|let|const)\s+)?\bmesoToken\s*=\s*['\"]([^'\"]+)['\"]"
)
_TOKEN_VALUE_RE = re.compile(r"^[A-Za-z0-9._~+/=-]{8,512}$")
_STATION_RE = re.compile(r"^[A-Z0-9]{4}$")


@dataclass(frozen=True, slots=True)
class WRHLiveFetchResult:
    client_version: str
    query_profile: str
    station: str
    station_timezone: str
    target_date: date
    query_start_date: date
    query_end_date: date
    query_start_utc: str
    query_end_utc: str
    shell_url: str
    viewer_script_url: str
    viewer_script_sha256: str
    api_key_script_url: str
    station_metadata_endpoint: str
    station_metadata_sha256: str
    backend_endpoint: str
    fetched_at: float
    snapshot: WRHSourceSnapshot
    transport_evidence_sha256: str
    token_persisted: bool = field(init=False, default=False)
    calibration_label_authority: bool = field(init=False, default=False)
    settlement_label_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "client_version": self.client_version,
            "query_profile": self.query_profile,
            "station": self.station,
            "station_timezone": self.station_timezone,
            "target_date": self.target_date.isoformat(),
            "query_start_date": self.query_start_date.isoformat(),
            "query_end_date": self.query_end_date.isoformat(),
            "query_start_utc": self.query_start_utc,
            "query_end_utc": self.query_end_utc,
            "shell_url": self.shell_url,
            "viewer_script_url": self.viewer_script_url,
            "viewer_script_sha256": self.viewer_script_sha256,
            "api_key_script_url": self.api_key_script_url,
            "station_metadata_endpoint": self.station_metadata_endpoint,
            "station_metadata_sha256": self.station_metadata_sha256,
            "backend_endpoint": self.backend_endpoint,
            "backend_origin": WRH_BROWSER_ORIGIN,
            "fetched_at": self.fetched_at,
            "snapshot": self.snapshot.as_dict(),
            "transport_evidence_sha256": self.transport_evidence_sha256,
            "token_persisted": self.token_persisted,
            "calibration_label_authority": self.calibration_label_authority,
            "settlement_label_authority": self.settlement_label_authority,
            "financial_authority": self.financial_authority,
        }


def _hash_payload(payload: object) -> str:
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    ).hexdigest()


def _calendar_date(value: object, code: str) -> date:
    if isinstance(value, datetime) or not isinstance(value, date):
        raise WRHSourceError(code)
    return value


def _provided_timestamp(value: object | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WRHSourceError("WRH_LIVE_RECEIVED_AT_INVALID")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WRHSourceError("WRH_LIVE_RECEIVED_AT_INVALID")
    return number


def _now() -> float:
    return time.time()


def _station(value: object) -> str:
    station = str(value or "").strip().upper()
    if not _STATION_RE.fullmatch(station):
        raise WRHSourceError("WRH_LIVE_STATION_INVALID")
    return station


def _safe_get(
    client: httpx.Client,
    url: str,
    *,
    params: dict | None,
    code: str,
    headers: dict[str, str] | None = None,
) -> httpx.Response:
    try:
        response = client.get(url, params=params, headers=headers)
    except httpx.HTTPError:
        raise WRHSourceError(code) from None
    if response.status_code < 200 or response.status_code >= 300:
        raise WRHSourceError(code)
    return response


def _script_urls(shell_body: str) -> tuple[str, ...]:
    values: list[str] = []
    for raw in _SCRIPT_RE.findall(shell_body):
        absolute = urljoin(WRH_TIMESERIES_PAGE, raw)
        if absolute not in values:
            values.append(absolute)
    return tuple(values)


def _discover_viewer_script(shell_body: str) -> str:
    candidates = [
        url for url in _script_urls(shell_body)
        if urlparse(url).path.endswith("/source/wrh/timeseries/obs.js")
    ]
    if len(candidates) != 1:
        raise WRHSourceError("WRH_LIVE_VIEWER_SCRIPT_DISCOVERY_FAILED")
    if candidates[0] != WRH_VIEWER_SCRIPT_URL:
        raise WRHSourceError("WRH_LIVE_VIEWER_SCRIPT_URL_MISMATCH")
    return candidates[0]


def _discover_api_key_script(shell_body: str) -> str:
    candidates = [
        url for url in _script_urls(shell_body)
        if urlparse(url).path == WRH_API_KEY_SCRIPT_PATH
    ]
    if len(candidates) != 1:
        raise WRHSourceError("WRH_LIVE_API_KEY_SCRIPT_DISCOVERY_FAILED")
    parsed = urlparse(candidates[0])
    if (
        parsed.scheme != "https"
        or parsed.netloc.lower() != "www.weather.gov"
        or parsed.path != WRH_API_KEY_SCRIPT_PATH
        or parsed.params
        or parsed.fragment
    ):
        raise WRHSourceError("WRH_LIVE_API_KEY_SCRIPT_IDENTITY_INVALID")
    return candidates[0]


def _verify_viewer_credential_contract(viewer_body: str) -> None:
    if WRH_BROWSER_TOKEN_IDENTIFIER not in viewer_body:
        raise WRHSourceError("WRH_LIVE_VIEWER_TOKEN_IDENTIFIER_MISMATCH")
    if "&token='+mesoToken+'&obtimezone=local" not in viewer_body:
        raise WRHSourceError("WRH_LIVE_VIEWER_TOKEN_QUERY_CONTRACT_MISMATCH")


def _extract_browser_token(script_body: str) -> str:
    matches: list[str] = []
    for raw in _MESO_TOKEN_ASSIGNMENT_RE.findall(script_body):
        value = str(raw).strip()
        if value.lower().startswith("token="):
            raise WRHSourceError("WRH_LIVE_BROWSER_TOKEN_SHAPE_MISMATCH")
        if _TOKEN_VALUE_RE.fullmatch(value) and value not in matches:
            matches.append(value)
    if len(matches) != 1:
        raise WRHSourceError("WRH_LIVE_BROWSER_TOKEN_DISCOVERY_FAILED")
    return matches[0]


def _station_metadata_identity(payload: object, station: str) -> tuple[str, str]:
    if not isinstance(payload, dict):
        raise WRHSourceError("WRH_LIVE_METADATA_PAYLOAD_INVALID")
    summary = payload.get("SUMMARY")
    if not isinstance(summary, dict) or str(summary.get("RESPONSE_MESSAGE") or "") != "OK":
        raise WRHSourceError("WRH_LIVE_METADATA_RESPONSE_NOT_OK")
    rows = payload.get("STATION")
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        raise WRHSourceError("WRH_LIVE_METADATA_STATION_ENVELOPE_INVALID")
    row = rows[0]
    actual = _station(row.get("STID"))
    if actual != station:
        raise WRHSourceError("WRH_LIVE_METADATA_STATION_IDENTITY_MISMATCH")
    timezone_name = str(row.get("TIMEZONE") or "").strip()
    if not timezone_name:
        raise WRHSourceError("WRH_LIVE_METADATA_TIMEZONE_MISSING")
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise WRHSourceError("WRH_LIVE_METADATA_TIMEZONE_INVALID") from None
    raw_network, normalized = _normalized_network(row.get("SHORTNAME"))
    digest = _hash_payload({
        "station": actual,
        "timezone": timezone_name,
        "raw_network": raw_network,
        "normalized_network": normalized,
    })
    return timezone_name, digest


def _local_query_bounds(target: date, timezone_name: str) -> tuple[datetime, datetime]:
    """Return inclusive UTC minute bounds for target + following LOCAL dates."""
    zone = ZoneInfo(timezone_name)
    start_local = datetime(target.year, target.month, target.day, tzinfo=zone)
    after = target + timedelta(days=2)
    after_local = datetime(after.year, after.month, after.day, tzinfo=zone)
    start_utc = start_local.astimezone(timezone.utc)
    # Synoptic end is minute-granular/inclusive. Convert the next local midnight
    # independently (important across DST), then back up one minute.
    end_utc = after_local.astimezone(timezone.utc) - timedelta(minutes=1)
    if end_utc <= start_utc:
        raise WRHSourceError("WRH_LIVE_QUERY_BOUNDS_INVALID")
    return start_utc, end_utc


def _transport_digest_payload(result: WRHLiveFetchResult) -> dict:
    return {
        "client_version": result.client_version,
        "query_profile": result.query_profile,
        "station": result.station,
        "station_timezone": result.station_timezone,
        "target_date": result.target_date.isoformat(),
        "query_start_date": result.query_start_date.isoformat(),
        "query_end_date": result.query_end_date.isoformat(),
        "query_start_utc": result.query_start_utc,
        "query_end_utc": result.query_end_utc,
        "shell_url": result.shell_url,
        "viewer_script_url": result.viewer_script_url,
        "viewer_script_sha256": result.viewer_script_sha256,
        "api_key_script_url": result.api_key_script_url,
        "station_metadata_endpoint": result.station_metadata_endpoint,
        "station_metadata_sha256": result.station_metadata_sha256,
        "backend_endpoint": result.backend_endpoint,
        "backend_origin": WRH_BROWSER_ORIGIN,
        "fetched_at": result.fetched_at,
        "snapshot_evidence_sha256": result.snapshot.evidence_sha256,
        "source_payload_sha256": result.snapshot.source_payload_sha256,
    }


class NWSWRHLiveClient:
    """Fetch exact WRH/Synoptic source payloads with an ephemeral NWS browser token."""

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 20.0,
        user_agent: str = "polymarket-weather-only-wrh-live/5.0 (+https://github.com/heroo2123/Alpha)",
    ) -> None:
        if isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float)):
            raise ValueError("timeout_seconds must be numeric")
        timeout = float(timeout_seconds)
        if not math.isfinite(timeout) or timeout <= 0.0:
            raise ValueError("timeout_seconds must be positive")
        if not isinstance(user_agent, str) or not user_agent.strip() or user_agent != user_agent.strip():
            raise ValueError("user_agent must be a non-empty trimmed string")
        self._external_client = http_client
        self._timeout_seconds = timeout
        self._user_agent = user_agent

    def _client(self) -> tuple[httpx.Client, bool]:
        if self._external_client is not None:
            return self._external_client, False
        return httpx.Client(
            headers={"User-Agent": self._user_agent, "Accept": "*/*"},
            timeout=self._timeout_seconds,
            follow_redirects=True,
        ), True

    def fetch_snapshot(
        self,
        *,
        station: str,
        target_date: date,
        received_at: float | None = None,
    ) -> WRHLiveFetchResult:
        station_id = _station(station)
        target = _calendar_date(target_date, "WRH_LIVE_TARGET_DATE_INVALID")
        following = target + timedelta(days=1)
        received_override = _provided_timestamp(received_at)
        client, owned = self._client()
        try:
            shell_params = {
                "site": station_id,
                "hours": "48",
                "units": "english",
                "hourly": "true",
                "obs": "tabular",
                "headers": "none",
                "chart": "off",
            }
            shell = _safe_get(
                client, WRH_TIMESERIES_PAGE, params=shell_params,
                code="WRH_LIVE_SHELL_HTTP_ERROR",
            )
            viewer_url = _discover_viewer_script(shell.text)
            key_url = _discover_api_key_script(shell.text)
            viewer = _safe_get(
                client, viewer_url, params=None, code="WRH_LIVE_VIEWER_HTTP_ERROR",
            )
            viewer_sha = hashlib.sha256(viewer.content).hexdigest()
            if viewer_sha != WRH_VIEWER_SCRIPT_SHA256:
                raise WRHSourceError("WRH_LIVE_VIEWER_SCRIPT_SHA_MISMATCH")
            _verify_viewer_credential_contract(viewer.text)
            key_script = _safe_get(
                client, key_url, params=None, code="WRH_LIVE_API_KEY_HTTP_ERROR",
            )
            browser_token = _extract_browser_token(key_script.text)

            metadata = _safe_get(
                client,
                WRH_STATION_METADATA_ENDPOINT,
                params={"stid": station_id, "complete": "1", "token": browser_token},
                headers={"Origin": WRH_BROWSER_ORIGIN},
                code="WRH_LIVE_METADATA_HTTP_ERROR",
            )
            try:
                metadata_payload = metadata.json()
            except ValueError:
                raise WRHSourceError("WRH_LIVE_METADATA_JSON_INVALID") from None
            station_timezone, metadata_sha = _station_metadata_identity(metadata_payload, station_id)
            start_utc, end_utc = _local_query_bounds(target, station_timezone)
            start_value = start_utc.strftime("%Y%m%d%H%M")
            end_value = end_utc.strftime("%Y%m%d%H%M")

            backend_params = {
                "STID": station_id,
                "showemptystations": "1",
                "units": "temp|F,speed|mph,english",
                "start": start_value,
                "end": end_value,
                "complete": "1",
                "token": browser_token,
                "obtimezone": "local",
            }
            backend = _safe_get(
                client,
                WRH_SYNOPTIC_ENDPOINT,
                params=backend_params,
                headers={"Origin": WRH_BROWSER_ORIGIN},
                code="WRH_LIVE_BACKEND_HTTP_ERROR",
            )
            fetched_at = received_override if received_override is not None else _now()
            try:
                payload = backend.json()
            except ValueError:
                raise WRHSourceError("WRH_LIVE_BACKEND_JSON_INVALID") from None
            if not isinstance(payload, dict):
                raise WRHSourceError("WRH_LIVE_BACKEND_PAYLOAD_INVALID")

            snapshot = parse_synoptic_wrh_hourly_snapshot(
                payload,
                station=station_id,
                target_date=target,
                query_start_date=target,
                query_end_date=following,
                received_at=fetched_at,
                expected_timezone=station_timezone,
            )
        finally:
            if owned:
                client.close()

        shell_url = str(httpx.URL(WRH_TIMESERIES_PAGE).copy_merge_params(shell_params))
        shell_result = WRHLiveFetchResult(
            client_version=WRH_LIVE_CLIENT_VERSION,
            query_profile=WRH_LIVE_QUERY_PROFILE,
            station=station_id,
            station_timezone=station_timezone,
            target_date=target,
            query_start_date=target,
            query_end_date=following,
            query_start_utc=start_utc.isoformat(),
            query_end_utc=end_utc.isoformat(),
            shell_url=shell_url,
            viewer_script_url=viewer_url,
            viewer_script_sha256=viewer_sha,
            api_key_script_url=key_url,
            station_metadata_endpoint=WRH_STATION_METADATA_ENDPOINT,
            station_metadata_sha256=metadata_sha,
            backend_endpoint=WRH_SYNOPTIC_ENDPOINT,
            fetched_at=fetched_at,
            snapshot=snapshot,
            transport_evidence_sha256="0" * 64,
        )
        return WRHLiveFetchResult(
            **{
                name: getattr(shell_result, name)
                for name, definition in shell_result.__dataclass_fields__.items()
                if definition.init and name != "transport_evidence_sha256"
            },
            transport_evidence_sha256=_hash_payload(_transport_digest_payload(shell_result)),
        )
