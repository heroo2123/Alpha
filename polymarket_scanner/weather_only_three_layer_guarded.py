from __future__ import annotations

"""Guarded transports for the silent same-day three-layer research lane.

These wrappers do not change the weather model. They harden network acquisition so
one malformed/compressed provider response cannot allocate an unbounded body or hold
the e2-micro service indefinitely. They also require the Open-Meteo GEFS grid point
to remain geographically close to the requested settlement-station coordinates.

All returned evidence keeps the authority flags of the underlying research adapters;
this module grants no probability, Telegram, settlement, order, or financial authority.
"""

import asyncio
import json
import math
import threading
import time
from datetime import date
from urllib.parse import urljoin, urlparse

import httpx

from .weather_only_forecast import OPEN_METEO_ENSEMBLE
from .weather_only_gefs_hourly import (
    GEFS_HOURLY_CELL_SELECTION,
    GEFS_HOURLY_PROVIDER_MODEL,
    GEFS_HOURLY_TEMPORAL_RESOLUTION,
    GEFS_HOURLY_VARIABLE,
    GEFSHourlyError,
    OpenMeteoGEFSHourlyClient,
    parse_open_meteo_gefs_hourly_target_day,
)
from .weather_only_nws_near_term import (
    MAX_RESPONSE_BYTES as NWS_BASE_MAX_RESPONSE_BYTES,
    NWSNearTermError,
    NWSNearTermGridClient,
)
from .weather_only_wrh_client import NWSWRHLiveClient


NWS_REQUEST_DEADLINE_SECONDS = 12.0
NWS_SNAPSHOT_DEADLINE_SECONDS = 20.0
GEFS_TOTAL_RESPONSE_DEADLINE_SECONDS = 20.0
GEFS_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
# The pinned same-day source is the nearest 0.25-degree GEFS grid. A nearest regular
# 0.25-degree cell center should be materially closer than this; the extra margin
# tolerates provider coordinate representation while still rejecting a wrong region.
GEFS_MAX_RESOLVED_DISTANCE_KM = 30.0
WRH_TOTAL_RESPONSE_DEADLINE_SECONDS = 20.0
WRH_MAX_RESPONSE_BYTES = 4 * 1024 * 1024
_ALLOWED_TRANSFER_ENCODINGS = {"identity", "chunked"}


def _content_length(headers: httpx.Headers, *, code: str) -> int | None:
    raw = headers.get("content-length")
    if raw in (None, ""):
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError, OverflowError):
        raise RuntimeError(code) from None
    if value < 0:
        raise RuntimeError(code)
    return value


def _encoding_is_identity(headers: httpx.Headers) -> bool:
    value = str(headers.get("content-encoding") or "").strip().lower()
    return value in {"", "identity"}


def _transfer_encoding_supported(headers: httpx.Headers) -> bool:
    raw = str(headers.get("transfer-encoding") or "").strip().lower()
    if not raw:
        return True
    tokens = {part.strip() for part in raw.split(",") if part.strip()}
    return bool(tokens) and tokens.issubset(_ALLOWED_TRANSFER_ENCODINGS)


async def _bounded_async_json(
    http: httpx.AsyncClient,
    url: str,
    *,
    params: dict | None,
    max_bytes: int,
    total_deadline_seconds: float,
    redirect_code: str,
    status_code: str,
    encoding_code: str,
    size_code: str,
    json_code: str,
    timeout_code: str,
    transport_code: str,
) -> tuple[dict, float]:
    try:
        async with asyncio.timeout(total_deadline_seconds):
            async with http.stream(
                "GET",
                url,
                params=params,
                headers={"Accept-Encoding": "identity"},
            ) as response:
                if response.is_redirect:
                    raise RuntimeError(redirect_code)
                if response.status_code >= 400:
                    raise RuntimeError(status_code)
                if not _encoding_is_identity(response.headers) or not _transfer_encoding_supported(
                    response.headers
                ):
                    raise RuntimeError(encoding_code)
                length = _content_length(response.headers, code=size_code)
                if length is not None and length > max_bytes:
                    raise RuntimeError(size_code)

                # Production httpx.stream(..., stream=True) enters here unconsumed.
                # The consumed branch exists only for injected test transports and
                # remains safe because encoding is rejected before content access.
                if response.is_stream_consumed:
                    raw = bytes(response.content)
                    if len(raw) > max_bytes:
                        raise RuntimeError(size_code)
                else:
                    payload = bytearray()
                    async for chunk in response.aiter_raw():
                        if len(payload) + len(chunk) > max_bytes:
                            raise RuntimeError(size_code)
                        payload.extend(chunk)
                    raw = bytes(payload)
                received = time.time()
    except TimeoutError:
        raise RuntimeError(timeout_code) from None
    except httpx.TimeoutException:
        raise RuntimeError(timeout_code) from None
    except httpx.RequestError:
        raise RuntimeError(transport_code) from None

    try:
        body = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise RuntimeError(json_code) from None
    if not isinstance(body, dict):
        raise RuntimeError(json_code)
    return body, received


class GuardedNWSNearTermGridClient(NWSNearTermGridClient):
    """NWS Layer-2 client with raw-byte and whole-snapshot time bounds."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(8.0, connect=4.0, read=8.0, write=5.0, pool=4.0),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            headers={
                "User-Agent": "polymarket-weather-only-near-term-guarded/1.0 (+https://github.com/heroo2123/Alpha)",
                "Accept": "application/geo+json",
                "Accept-Encoding": "identity",
            },
        )

    async def _json_get(self, url: str) -> tuple[dict, float]:
        try:
            return await _bounded_async_json(
                self.http,
                url,
                params=None,
                max_bytes=NWS_BASE_MAX_RESPONSE_BYTES,
                total_deadline_seconds=NWS_REQUEST_DEADLINE_SECONDS,
                redirect_code="NWS_NEAR_TERM_PROVIDER_REDIRECT",
                status_code="NWS_NEAR_TERM_PROVIDER_HTTP_STATUS",
                encoding_code="NWS_NEAR_TERM_PROVIDER_UNSUPPORTED_ENCODING",
                size_code="NWS_NEAR_TERM_PROVIDER_RESPONSE_CAP",
                json_code="NWS_NEAR_TERM_PROVIDER_JSON_INVALID",
                timeout_code="NWS_NEAR_TERM_PROVIDER_TIMEOUT",
                transport_code="NWS_NEAR_TERM_PROVIDER_TRANSPORT",
            )
        except RuntimeError as exc:
            raise NWSNearTermError(str(exc)) from None

    async def fetch_snapshot(self, *, station: str, latitude: float, longitude: float):
        try:
            async with asyncio.timeout(NWS_SNAPSHOT_DEADLINE_SECONDS):
                return await super().fetch_snapshot(
                    station=station,
                    latitude=latitude,
                    longitude=longitude,
                )
        except TimeoutError:
            raise NWSNearTermError("NWS_NEAR_TERM_PROVIDER_TIMEOUT") from None


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    earth = 6371.0088
    p1 = radians(lat1)
    p2 = radians(lat2)
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    a = sin(dlat / 2.0) ** 2 + cos(p1) * cos(p2) * sin(dlon / 2.0) ** 2
    return 2.0 * earth * asin(sqrt(a))


class GuardedOpenMeteoGEFSHourlyClient(OpenMeteoGEFSHourlyClient):
    """Layer-3 client with bounded raw reads and resolved-grid location binding."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(10.0, connect=4.0, read=10.0, write=5.0, pool=4.0),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            headers={
                "User-Agent": "polymarket-weather-only-gefs-hourly-guarded/2.0 (+https://github.com/heroo2123/Alpha)",
                "Accept": "application/json",
                "Accept-Encoding": "identity",
            },
        )

    async def target_day(
        self,
        *,
        station: str,
        latitude: float,
        longitude: float,
        target_date: date,
        unit: str,
        timezone: str,
    ):
        if type(target_date) is not date:
            raise GEFSHourlyError("GEFS_HOURLY_TARGET_DATE_INVALID")
        if unit not in {"F", "C"}:
            raise GEFSHourlyError("GEFS_HOURLY_UNIT_UNSUPPORTED")
        try:
            lat = float(latitude)
            lon = float(longitude)
        except (TypeError, ValueError, OverflowError):
            raise GEFSHourlyError("GEFS_HOURLY_REQUEST_COORDINATE_INVALID") from None
        if (
            isinstance(latitude, bool)
            or isinstance(longitude, bool)
            or not math.isfinite(lat)
            or not math.isfinite(lon)
            or not -90.0 <= lat <= 90.0
            or not -180.0 <= lon <= 180.0
        ):
            raise GEFSHourlyError("GEFS_HOURLY_REQUEST_COORDINATE_INVALID")
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": GEFS_HOURLY_VARIABLE,
            "models": GEFS_HOURLY_PROVIDER_MODEL,
            "temporal_resolution": GEFS_HOURLY_TEMPORAL_RESOLUTION,
            "temperature_unit": "fahrenheit" if unit == "F" else "celsius",
            "timezone": timezone,
            "start_date": target_date.isoformat(),
            "end_date": target_date.isoformat(),
            "cell_selection": GEFS_HOURLY_CELL_SELECTION,
        }
        try:
            payload, received = await _bounded_async_json(
                self.http,
                OPEN_METEO_ENSEMBLE,
                params=params,
                max_bytes=GEFS_MAX_RESPONSE_BYTES,
                total_deadline_seconds=GEFS_TOTAL_RESPONSE_DEADLINE_SECONDS,
                redirect_code="GEFS_HOURLY_PROVIDER_REDIRECT",
                status_code="GEFS_HOURLY_PROVIDER_HTTP_STATUS",
                encoding_code="GEFS_HOURLY_PROVIDER_UNSUPPORTED_ENCODING",
                size_code="GEFS_HOURLY_PROVIDER_RESPONSE_CAP",
                json_code="GEFS_HOURLY_PROVIDER_JSON_INVALID",
                timeout_code="GEFS_HOURLY_PROVIDER_TIMEOUT",
                transport_code="GEFS_HOURLY_PROVIDER_TRANSPORT",
            )
        except RuntimeError as exc:
            raise GEFSHourlyError(str(exc)) from None

        result = parse_open_meteo_gefs_hourly_target_day(
            payload,
            station=str(station).strip().upper(),
            target_date=target_date,
            unit=unit,
            timezone=timezone,
            requested_latitude=lat,
            requested_longitude=lon,
            received_at=received,
            provider_model=GEFS_HOURLY_PROVIDER_MODEL,
            query_cell_selection=GEFS_HOURLY_CELL_SELECTION,
            query_temporal_resolution=GEFS_HOURLY_TEMPORAL_RESOLUTION,
        )
        distance = _haversine_km(
            lat,
            lon,
            float(result.resolved_latitude),
            float(result.resolved_longitude),
        )
        if distance > GEFS_MAX_RESOLVED_DISTANCE_KM:
            raise GEFSHourlyError("GEFS_HOURLY_RESOLVED_LOCATION_TOO_FAR")
        return result


class _BoundedIdentityHTTPClient(httpx.Client):
    """Sync identity-only client used by the exact WRH transport."""

    def __init__(self) -> None:
        self._budget = threading.local()
        super().__init__(
            headers={
                "User-Agent": "polymarket-weather-only-wrh-live-guarded/1.0 (+https://github.com/heroo2123/Alpha)",
                "Accept": "*/*",
                "Accept-Encoding": "identity",
            },
            timeout=httpx.Timeout(5.0, connect=4.0, read=5.0, write=5.0, pool=4.0),
            follow_redirects=False,
            trust_env=False,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
        )

    def begin_snapshot_budget(self) -> None:
        self._budget.deadline = time.monotonic() + WRH_TOTAL_RESPONSE_DEADLINE_SECONDS

    def end_snapshot_budget(self) -> None:
        if hasattr(self._budget, "deadline"):
            del self._budget.deadline

    def _deadline(self) -> float:
        value = getattr(self._budget, "deadline", None)
        return float(value) if value is not None else time.monotonic() + WRH_TOTAL_RESPONSE_DEADLINE_SECONDS

    def get(self, url, *, params=None, headers=None, **kwargs):  # type: ignore[override]
        deadline = self._deadline()
        current = str(url)
        current_params = params
        merged_headers = dict(headers or {})
        merged_headers["Accept-Encoding"] = "identity"

        for _redirect_index in range(4):
            if time.monotonic() > deadline:
                raise httpx.TimeoutException("WRH total response deadline")
            with super().stream(
                "GET",
                current,
                params=current_params,
                headers=merged_headers,
                follow_redirects=False,
                **kwargs,
            ) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        return httpx.Response(
                            response.status_code,
                            headers=response.headers,
                            request=response.request,
                            content=b"",
                        )
                    nxt = urljoin(str(response.request.url), location)
                    before = urlparse(str(response.request.url))
                    after = urlparse(nxt)
                    # Never carry the ephemeral WRH browser credential across hosts.
                    if (
                        after.scheme != "https"
                        or not before.hostname
                        or after.hostname != before.hostname
                    ):
                        return httpx.Response(
                            response.status_code,
                            headers=response.headers,
                            request=response.request,
                            content=b"",
                        )
                    current = nxt
                    current_params = None
                    continue

                if not _encoding_is_identity(response.headers) or not _transfer_encoding_supported(
                    response.headers
                ):
                    raise httpx.RequestError(
                        "unsupported response encoding", request=response.request
                    )
                try:
                    length = _content_length(
                        response.headers, code="WRH_LIVE_RESPONSE_LENGTH_INVALID"
                    )
                except RuntimeError:
                    raise httpx.RequestError(
                        "invalid response length", request=response.request
                    ) from None
                if length is not None and length > WRH_MAX_RESPONSE_BYTES:
                    raise httpx.RequestError("response too large", request=response.request)

                payload = bytearray()
                if response.is_stream_consumed:
                    raw = bytes(response.content)
                    if len(raw) > WRH_MAX_RESPONSE_BYTES:
                        raise httpx.RequestError("response too large", request=response.request)
                    payload.extend(raw)
                else:
                    for chunk in response.iter_raw():
                        if time.monotonic() > deadline:
                            raise httpx.TimeoutException(
                                "WRH total response deadline", request=response.request
                            )
                        if len(payload) + len(chunk) > WRH_MAX_RESPONSE_BYTES:
                            raise httpx.RequestError(
                                "response too large", request=response.request
                            )
                        payload.extend(chunk)
                return httpx.Response(
                    response.status_code,
                    headers=response.headers,
                    request=response.request,
                    content=bytes(payload),
                )
        raise httpx.RequestError(
            "too many WRH redirects", request=httpx.Request("GET", current)
        )


class GuardedNWSWRHLiveClient(NWSWRHLiveClient):
    """Exact Layer-1 WRH client using one deadline across the entire source walk."""

    def __init__(self) -> None:
        self._guarded_http = _BoundedIdentityHTTPClient()
        super().__init__(
            http_client=self._guarded_http,
            timeout_seconds=WRH_TOTAL_RESPONSE_DEADLINE_SECONDS,
            user_agent="polymarket-weather-only-wrh-live-guarded/1.0 (+https://github.com/heroo2123/Alpha)",
        )

    def fetch_snapshot(self, **kwargs):
        self._guarded_http.begin_snapshot_budget()
        try:
            return super().fetch_snapshot(**kwargs)
        finally:
            self._guarded_http.end_snapshot_budget()

    def close(self) -> None:
        self._guarded_http.close()
