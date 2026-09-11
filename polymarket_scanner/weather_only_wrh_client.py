from __future__ import annotations

"""Secret-safe live transport for the exact NWS WRH Hourly Data adapter.

The public WRH page is a browser application: its pinned viewer JavaScript obtains
station observations from Synoptic Data using a browser credential served by NWS.
This client mirrors that transport without ever returning, persisting or logging the
credential. Token-bearing HTTP exceptions are converted to fixed error codes before
they can stringify request URLs.

A successful fetch proves only source/transport acquisition. The returned snapshot
keeps calibration, settlement and financial authority false; prospective finality and
frozen contract rules must upgrade it later.
"""

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlparse

import httpx

from .weather_only_wrh import (
    WRH_SYNOPTIC_ENDPOINT,
    WRH_VIEWER_SCRIPT_SHA256,
    WRH_VIEWER_SCRIPT_URL,
    WRHSourceError,
    WRHSourceSnapshot,
    parse_synoptic_wrh_hourly_snapshot,
)


WRH_LIVE_CLIENT_VERSION = "nws_wrh_live_transport_v4_response_receipt_timestamp"
WRH_TIMESERIES_PAGE = "https://www.weather.gov/wrh/timeseries"
WRH_BROWSER_ORIGIN = "https://www.weather.gov"
WRH_API_KEY_SCRIPT_PATH = "/source/wrh/apiKey.js"
WRH_BROWSER_TOKEN_IDENTIFIER = "mesoToken"
WRH_LIVE_QUERY_PROFILE = "WRH_HISTORY_TARGET_PLUS_FOLLOWING_DATE_ENGLISH_HOURLY"

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
    target_date: date
    query_start_date: date
    query_end_date: date
    shell_url: str
    viewer_script_url: str
    viewer_script_sha256: str
    api_key_script_url: str
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
            "target_date": self.target_date.isoformat(),
            "query_start_date": self.query_start_date.isoformat(),
            "query_end_date": self.query_end_date.isoformat(),
            "shell_url": self.shell_url,
            "viewer_script_url": self.viewer_script_url,
            "viewer_script_sha256": self.viewer_script_sha256,
            "api_key_script_url": self.api_key_script_url,
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
    """Validate a deterministic test override without inventing a production receipt time."""
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
    """Perform one GET without allowing a tokenized URL to escape via exceptions."""
    try:
        response = client.get(url, params=params, headers=headers)
    except httpx.HTTPError:
        # Do not chain: several httpx exception repr/messages include request.url.
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
    # The exact script is SHA-pinned as well. This explicit check makes the transport
    # dependency legible and fails closed if a future pinned version changes how its
    # credential is injected into the Synoptic query.
    if WRH_BROWSER_TOKEN_IDENTIFIER not in viewer_body:
        raise WRHSourceError("WRH_LIVE_VIEWER_TOKEN_IDENTIFIER_MISMATCH")
    if "&token='+mesoToken+'&obtimezone=local" not in viewer_body:
        raise WRHSourceError("WRH_LIVE_VIEWER_TOKEN_QUERY_CONTRACT_MISMATCH")


def _extract_browser_token(script_body: str) -> str:
    """Extract NWS's bare mesoToken in memory; never serialize the credential."""
    matches: list[str] = []
    for raw in _MESO_TOKEN_ASSIGNMENT_RE.findall(script_body):
        value = str(raw).strip()
        # The pinned WRH viewer itself supplies ``&token=`` and concatenates the
        # bare mesoToken. A prefixed assignment would change query semantics.
        if value.lower().startswith("token="):
            raise WRHSourceError("WRH_LIVE_BROWSER_TOKEN_SHAPE_MISMATCH")
        if _TOKEN_VALUE_RE.fullmatch(value) and value not in matches:
            matches.append(value)
    if len(matches) != 1:
        raise WRHSourceError("WRH_LIVE_BROWSER_TOKEN_DISCOVERY_FAILED")
    return matches[0]


def _transport_digest_payload(result: WRHLiveFetchResult) -> dict:
    return {
        "client_version": result.client_version,
        "query_profile": result.query_profile,
        "station": result.station,
        "target_date": result.target_date.isoformat(),
        "query_start_date": result.query_start_date.isoformat(),
        "query_end_date": result.query_end_date.isoformat(),
        "shell_url": result.shell_url,
        "viewer_script_url": result.viewer_script_url,
        "viewer_script_sha256": result.viewer_script_sha256,
        "api_key_script_url": result.api_key_script_url,
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
        user_agent: str = "polymarket-weather-only-wrh-live/4.0 (+https://github.com/heroo2123/Alpha)",
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
                client,
                WRH_TIMESERIES_PAGE,
                params=shell_params,
                code="WRH_LIVE_SHELL_HTTP_ERROR",
            )
            viewer_url = _discover_viewer_script(shell.text)
            key_url = _discover_api_key_script(shell.text)

            viewer = _safe_get(
                client,
                viewer_url,
                params=None,
                code="WRH_LIVE_VIEWER_HTTP_ERROR",
            )
            viewer_sha = hashlib.sha256(viewer.content).hexdigest()
            if viewer_sha != WRH_VIEWER_SCRIPT_SHA256:
                raise WRHSourceError("WRH_LIVE_VIEWER_SCRIPT_SHA_MISMATCH")
            _verify_viewer_credential_contract(viewer.text)

            key_script = _safe_get(
                client,
                key_url,
                params=None,
                code="WRH_LIVE_API_KEY_HTTP_ERROR",
            )
            browser_token = _extract_browser_token(key_script.text)

            # Match the WRH viewer's historical query construction and browser
            # authorization context exactly. The live Synoptic token is origin-bound:
            # without weather.gov as Origin, the same credential is rejected with 403.
            backend_params = {
                "STID": station_id,
                "showemptystations": "1",
                "units": "temp|F,speed|mph,english",
                "start": target.strftime("%Y%m%d") + "0000",
                "end": following.strftime("%Y%m%d") + "2359",
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
            # For production finality evidence the timestamp must represent a state
            # that has actually arrived, never the beginning of a possibly slow HTTP
            # request. Deterministic tests may still supply an explicit override.
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
            )
        finally:
            if owned:
                client.close()

        shell_url = str(httpx.URL(WRH_TIMESERIES_PAGE).copy_merge_params(shell_params))
        shell_result = WRHLiveFetchResult(
            client_version=WRH_LIVE_CLIENT_VERSION,
            query_profile=WRH_LIVE_QUERY_PROFILE,
            station=station_id,
            target_date=target,
            query_start_date=target,
            query_end_date=following,
            shell_url=shell_url,
            viewer_script_url=viewer_url,
            viewer_script_sha256=viewer_sha,
            api_key_script_url=key_url,
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
