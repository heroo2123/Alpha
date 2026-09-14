from __future__ import annotations

"""Official NWS Layer-2 near-term forecast-path adapter.

This adapter is deliberately narrower than "the NWS says the daily high/low will be
X". It retrieves the raw NWS forecast grid for the exact station coordinates and
projects only the short Layer-2 interval onto a 15-minute sampled path. NWS documents
raw grid values as applying to their ``validTime`` intervals and exposes ``updateTime``
as the grid-data update/version time.

Network acquisition and decision-time projection are deliberately separate. Raw NWS
points/grid responses are first frozen into ``NWSNearTermRawSnapshot``. A caller may
then choose an immutable decision time *after* all source receipts and project that
already-received snapshot onto U(t). This prevents an awaited provider request from
being backdated into a decision made before its response existed.

The resulting path is supporting forecast evidence only. It is not the WRH settlement
population, not a calibrated probability, and cannot authorize Telegram or financial
delivery. Any missing interval, unit drift, stale/future update identity, redirect to
an unexpected host, grid-identity mismatch, or uncovered sample fails closed.
"""

import hashlib
import json
import math
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlparse

import httpx

from .config import settings
from .weather_only_conditioned_extremes import OFFICIAL_NOWCAST_ROLE, TimeSegment
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW
from .weather_only_near_term_path import (
    NearTermPathPoint,
    VerifiedNearTermPath,
    build_near_term_sample_path,
)


NWS_NEAR_TERM_VERSION = "nws_raw_grid_temperature_near_term_v3_grid_identity_4dp"
NWS_RAW_SNAPSHOT_VERSION = "nws_near_term_raw_snapshot_v2_grid_identity_4dp"
NWS_API_ORIGIN = "https://api.weather.gov"
NWS_POINTS_ENDPOINT = NWS_API_ORIGIN + "/points/{latitude},{longitude}"
# api.weather.gov documents support for no more than four decimal places in /points
# coordinates. Keep the original station coordinates in evidence, but make the actual
# request URL and persisted request identity exactly match that supported precision.
NWS_POINTS_COORDINATE_DECIMALS = 4
NWS_NEAR_TERM_STEP_SECONDS = 900
NWS_TEMPERATURE_UOM_C = "wmoUnit:degC"
NWS_TEMPERATURE_UOM_F = "wmoUnit:degF"
NWS_NEAR_TERM_HYPOTHESIS = "NWS_GRID_INTERVAL_VALUE_SAMPLED_15MIN_LEFT_CLOSED_V1"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_GRID_URL_RE = re.compile(
    r"^/gridpoints/(?P<grid_id>[A-Z]{3})/(?P<grid_x>\d+),(?P<grid_y>\d+)/?$"
)
_DURATION_RE = re.compile(
    r"^P(?:(?P<days>\d+)D)?(?:T(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?(?:(?P<seconds>\d+(?:\.\d+)?)S)?)?$"
)


class NWSNearTermError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical(value: object) -> str:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
    except (TypeError, ValueError):
        raise NWSNearTermError("NWS_NEAR_TERM_JSON_INVALID") from None


def _canonical_sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise NWSNearTermError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise NWSNearTermError(code) from None
    if not math.isfinite(number):
        raise NWSNearTermError(code)
    return number


def _aware_timestamp(value: object, code: str) -> float:
    if not isinstance(value, str) or not value.strip():
        raise NWSNearTermError(code)
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        raise NWSNearTermError(code) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise NWSNearTermError(code)
    return parsed.astimezone(timezone.utc).timestamp()


def _duration_seconds(value: str) -> float:
    match = _DURATION_RE.fullmatch(str(value or ""))
    if match is None:
        raise NWSNearTermError("NWS_NEAR_TERM_VALID_DURATION_INVALID")
    parts = {name: float(raw or 0) for name, raw in match.groupdict().items()}
    seconds = (
        parts["days"] * 86400.0
        + parts["hours"] * 3600.0
        + parts["minutes"] * 60.0
        + parts["seconds"]
    )
    if not math.isfinite(seconds) or seconds <= 0.0:
        raise NWSNearTermError("NWS_NEAR_TERM_VALID_DURATION_INVALID")
    return seconds


def _valid_interval(value: object) -> tuple[float, float]:
    if not isinstance(value, str) or "/" not in value:
        raise NWSNearTermError("NWS_NEAR_TERM_VALID_TIME_INVALID")
    start_text, duration = value.split("/", 1)
    start = _aware_timestamp(start_text, "NWS_NEAR_TERM_VALID_TIME_INVALID")
    end = start + _duration_seconds(duration)
    return start, end


def _points_url(latitude: object, longitude: object) -> str:
    lat = _finite(latitude, "NWS_NEAR_TERM_COORDINATE_INVALID")
    lon = _finite(longitude, "NWS_NEAR_TERM_COORDINATE_INVALID")
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise NWSNearTermError("NWS_NEAR_TERM_COORDINATE_INVALID")
    return NWS_POINTS_ENDPOINT.format(
        latitude=f"{lat:.{NWS_POINTS_COORDINATE_DECIMALS}f}",
        longitude=f"{lon:.{NWS_POINTS_COORDINATE_DECIMALS}f}",
    )


def _grid_url(value: object) -> str:
    text = str(value or "").strip()
    try:
        parsed = urlparse(text)
    except ValueError:
        raise NWSNearTermError("NWS_NEAR_TERM_GRID_URL_INVALID") from None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "api.weather.gov"
        or parsed.params
        or parsed.query
        or parsed.fragment
        or not _GRID_URL_RE.fullmatch(parsed.path)
    ):
        raise NWSNearTermError("NWS_NEAR_TERM_GRID_URL_INVALID")
    return text.rstrip("/")


def _grid_identity(value: object) -> tuple[str, str, int, int]:
    url = _grid_url(value)
    match = _GRID_URL_RE.fullmatch(urlparse(url).path)
    if match is None:  # defensive; _grid_url already proves this shape
        raise NWSNearTermError("NWS_NEAR_TERM_GRID_URL_INVALID")
    return (
        url,
        match.group("grid_id"),
        int(match.group("grid_x")),
        int(match.group("grid_y")),
    )


def _verified_grid_properties(grid_payload: object, forecast_grid_url: str) -> dict:
    if not isinstance(grid_payload, dict) or grid_payload.get("type") != "Feature":
        raise NWSNearTermError("NWS_NEAR_TERM_GRID_ENVELOPE_INVALID")
    properties = grid_payload.get("properties")
    if not isinstance(properties, dict):
        raise NWSNearTermError("NWS_NEAR_TERM_GRID_PROPERTIES_INVALID")
    _url, expected_id, expected_x, expected_y = _grid_identity(forecast_grid_url)
    actual_id = properties.get("gridId")
    actual_x = properties.get("gridX")
    actual_y = properties.get("gridY")
    if (
        not isinstance(actual_id, str)
        or actual_id != expected_id
        or isinstance(actual_x, bool)
        or not isinstance(actual_x, int)
        or actual_x != expected_x
        or isinstance(actual_y, bool)
        or not isinstance(actual_y, int)
        or actual_y != expected_y
    ):
        raise NWSNearTermError("NWS_NEAR_TERM_GRID_IDENTITY_MISMATCH")
    return properties


def _to_requested_unit(value: float, source_uom: str, target_unit: str) -> float:
    if target_unit not in {"F", "C"}:
        raise NWSNearTermError("NWS_NEAR_TERM_UNIT_UNSUPPORTED")
    if source_uom == NWS_TEMPERATURE_UOM_C:
        return value if target_unit == "C" else value * 9.0 / 5.0 + 32.0
    if source_uom == NWS_TEMPERATURE_UOM_F:
        return value if target_unit == "F" else (value - 32.0) * 5.0 / 9.0
    raise NWSNearTermError("NWS_NEAR_TERM_SOURCE_UNIT_UNSUPPORTED")


def _expected_sample_times(segment: TimeSegment) -> tuple[float, ...]:
    start = float(segment.start)
    end = float(segment.end)
    if end <= start:
        raise NWSNearTermError("NWS_NEAR_TERM_SEGMENT_INVALID")
    result = [start]
    current = start
    while current + NWS_NEAR_TERM_STEP_SECONDS < end - 1e-6:
        current += NWS_NEAR_TERM_STEP_SECONDS
        result.append(current)
    if end - result[-1] > 1e-6:
        result.append(end)
    return tuple(result)


@dataclass(frozen=True, slots=True)
class NWSGridTemperatureInterval:
    start: float
    end: float
    value: float


@dataclass(frozen=True, slots=True)
class NWSNearTermRawSnapshot:
    """Network evidence acquired before a same-day decision time is frozen."""

    version: str
    station: str
    latitude: float
    longitude: float
    points_url: str
    forecast_grid_url: str
    points_received_at: float
    grid_received_at: float
    points_payload: dict
    grid_payload: dict
    evidence_sha256: str
    settlement_authority: bool = field(init=False, default=False)
    calibration_label_authority: bool = field(init=False, default=False)
    same_day_delivery_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    @property
    def received_at(self) -> float:
        return max(float(self.points_received_at), float(self.grid_received_at))

    def as_dict(self) -> dict:
        value = asdict(self)
        value["received_at"] = self.received_at
        return value


def _raw_snapshot_payload(value: NWSNearTermRawSnapshot) -> dict:
    return {
        "version": value.version,
        "station": value.station,
        "latitude": value.latitude,
        "longitude": value.longitude,
        "points_url": value.points_url,
        "forecast_grid_url": value.forecast_grid_url,
        "points_received_at": value.points_received_at,
        "grid_received_at": value.grid_received_at,
        "points_payload": value.points_payload,
        "grid_payload": value.grid_payload,
        "settlement_authority": value.settlement_authority,
        "calibration_label_authority": value.calibration_label_authority,
        "same_day_delivery_authority": value.same_day_delivery_authority,
        "financial_authority": value.financial_authority,
    }


def build_nws_raw_snapshot(
    points_payload: object,
    grid_payload: object,
    *,
    station: str,
    latitude: float,
    longitude: float,
    points_received_at: float,
    grid_received_at: float,
) -> NWSNearTermRawSnapshot:
    station_id = str(station or "").strip().upper()
    if len(station_id) != 4 or not station_id.isalnum():
        raise NWSNearTermError("NWS_NEAR_TERM_STATION_INVALID")
    lat = _finite(latitude, "NWS_NEAR_TERM_COORDINATE_INVALID")
    lon = _finite(longitude, "NWS_NEAR_TERM_COORDINATE_INVALID")
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise NWSNearTermError("NWS_NEAR_TERM_COORDINATE_INVALID")
    points_receipt = _finite(points_received_at, "NWS_NEAR_TERM_RECEIPT_INVALID")
    grid_receipt = _finite(grid_received_at, "NWS_NEAR_TERM_RECEIPT_INVALID")
    if points_receipt < 0.0 or grid_receipt < points_receipt - 1e-6:
        raise NWSNearTermError("NWS_NEAR_TERM_RECEIPT_ORDER_INVALID")
    if not isinstance(points_payload, dict) or points_payload.get("type") != "Feature":
        raise NWSNearTermError("NWS_NEAR_TERM_POINTS_ENVELOPE_INVALID")
    point_properties = points_payload.get("properties")
    if not isinstance(point_properties, dict):
        raise NWSNearTermError("NWS_NEAR_TERM_POINTS_PROPERTIES_INVALID")
    forecast_grid_url = _grid_url(point_properties.get("forecastGridData"))
    points_url = _points_url(lat, lon)
    _verified_grid_properties(grid_payload, forecast_grid_url)

    shell = NWSNearTermRawSnapshot(
        version=NWS_RAW_SNAPSHOT_VERSION,
        station=station_id,
        latitude=lat,
        longitude=lon,
        points_url=points_url,
        forecast_grid_url=forecast_grid_url,
        points_received_at=points_receipt,
        grid_received_at=grid_receipt,
        points_payload=json.loads(_canonical(points_payload)),
        grid_payload=json.loads(_canonical(grid_payload)),
        evidence_sha256="0" * 64,
    )
    return NWSNearTermRawSnapshot(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "evidence_sha256"
        },
        evidence_sha256=_canonical_sha(_raw_snapshot_payload(shell)),
    )


def verify_nws_raw_snapshot(value: object) -> NWSNearTermRawSnapshot:
    if not isinstance(value, NWSNearTermRawSnapshot):
        raise NWSNearTermError("NWS_NEAR_TERM_RAW_SNAPSHOT_TYPE_INVALID")
    if value.version != NWS_RAW_SNAPSHOT_VERSION:
        raise NWSNearTermError("NWS_NEAR_TERM_RAW_SNAPSHOT_VERSION_INVALID")
    if value.evidence_sha256 != _canonical_sha(_raw_snapshot_payload(value)):
        raise NWSNearTermError("NWS_NEAR_TERM_RAW_SNAPSHOT_DIGEST_MISMATCH")
    if value.points_url != _points_url(value.latitude, value.longitude):
        raise NWSNearTermError("NWS_NEAR_TERM_POINTS_URL_IDENTITY_MISMATCH")
    if _grid_url(value.forecast_grid_url) != value.forecast_grid_url:
        raise NWSNearTermError("NWS_NEAR_TERM_GRID_URL_INVALID")
    if not isinstance(value.points_payload, dict) or value.points_payload.get("type") != "Feature":
        raise NWSNearTermError("NWS_NEAR_TERM_POINTS_ENVELOPE_INVALID")
    point_properties = value.points_payload.get("properties")
    if not isinstance(point_properties, dict):
        raise NWSNearTermError("NWS_NEAR_TERM_POINTS_PROPERTIES_INVALID")
    if _grid_url(point_properties.get("forecastGridData")) != value.forecast_grid_url:
        raise NWSNearTermError("NWS_NEAR_TERM_GRID_URL_IDENTITY_MISMATCH")
    _verified_grid_properties(value.grid_payload, value.forecast_grid_url)
    if any((
        value.settlement_authority,
        value.calibration_label_authority,
        value.same_day_delivery_authority,
        value.financial_authority,
    )):
        raise NWSNearTermError("NWS_NEAR_TERM_RAW_AUTHORITY_BOUNDARY_BROKEN")
    return value


def parse_nws_near_term_grid_path(
    points_payload: object,
    grid_payload: object,
    *,
    station: str,
    latitude: float,
    longitude: float,
    unit: str,
    family: str,
    segment: TimeSegment,
    received_at: float,
) -> VerifiedNearTermPath:
    """Parse a points lookup + raw grid response into one immutable Layer-2 path."""
    station_id = str(station or "").strip().upper()
    if len(station_id) != 4 or not station_id.isalnum():
        raise NWSNearTermError("NWS_NEAR_TERM_STATION_INVALID")
    if family not in {DAILY_HIGH, DAILY_LOW}:
        raise NWSNearTermError("NWS_NEAR_TERM_FAMILY_INVALID")
    lat = _finite(latitude, "NWS_NEAR_TERM_COORDINATE_INVALID")
    lon = _finite(longitude, "NWS_NEAR_TERM_COORDINATE_INVALID")
    if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
        raise NWSNearTermError("NWS_NEAR_TERM_COORDINATE_INVALID")
    receipt = _finite(received_at, "NWS_NEAR_TERM_RECEIPT_INVALID")
    if receipt < 0.0:
        raise NWSNearTermError("NWS_NEAR_TERM_RECEIPT_INVALID")
    if not isinstance(segment, TimeSegment):
        raise NWSNearTermError("NWS_NEAR_TERM_SEGMENT_INVALID")

    if not isinstance(points_payload, dict) or points_payload.get("type") != "Feature":
        raise NWSNearTermError("NWS_NEAR_TERM_POINTS_ENVELOPE_INVALID")
    point_properties = points_payload.get("properties")
    if not isinstance(point_properties, dict):
        raise NWSNearTermError("NWS_NEAR_TERM_POINTS_PROPERTIES_INVALID")
    forecast_grid_url = _grid_url(point_properties.get("forecastGridData"))
    properties = _verified_grid_properties(grid_payload, forecast_grid_url)
    update_at = _aware_timestamp(properties.get("updateTime"), "NWS_NEAR_TERM_UPDATE_TIME_INVALID")
    if update_at > receipt + 1e-6:
        raise NWSNearTermError("NWS_NEAR_TERM_UPDATE_AFTER_RECEIPT")

    temperature = properties.get("temperature")
    if not isinstance(temperature, dict):
        raise NWSNearTermError("NWS_NEAR_TERM_TEMPERATURE_LAYER_MISSING")
    source_uom = str(temperature.get("uom") or "").strip()
    values = temperature.get("values")
    if not isinstance(values, list) or not values:
        raise NWSNearTermError("NWS_NEAR_TERM_TEMPERATURE_VALUES_MISSING")

    intervals: list[NWSGridTemperatureInterval] = []
    prior_start = None
    for row in values:
        if not isinstance(row, dict):
            raise NWSNearTermError("NWS_NEAR_TERM_TEMPERATURE_ROW_INVALID")
        start, end = _valid_interval(row.get("validTime"))
        raw_value = _finite(row.get("value"), "NWS_NEAR_TERM_TEMPERATURE_VALUE_INVALID")
        converted = _to_requested_unit(raw_value, source_uom, unit)
        if prior_start is not None and start < prior_start - 1e-6:
            raise NWSNearTermError("NWS_NEAR_TERM_INTERVAL_ORDER_INVALID")
        if intervals and start < intervals[-1].end - 1e-6:
            raise NWSNearTermError("NWS_NEAR_TERM_INTERVAL_OVERLAP")
        intervals.append(NWSGridTemperatureInterval(start, end, converted))
        prior_start = start

    def value_at(instant: float) -> float:
        # Layer-2 is [start,end). The endpoint sample represents the left-hand limit
        # so it cannot borrow the first Layer-3 instant from the next interval.
        lookup = instant if instant < float(segment.end) - 1e-6 else float(segment.end) - 1e-3
        matches = [row for row in intervals if row.start - 1e-6 <= lookup < row.end - 1e-6]
        if len(matches) != 1:
            raise NWSNearTermError("NWS_NEAR_TERM_SAMPLE_UNCOVERED")
        return matches[0].value

    samples = tuple(
        NearTermPathPoint(valid_at=instant, value=value_at(instant))
        for instant in _expected_sample_times(segment)
    )
    source_payload_sha = _canonical_sha({
        "adapter": NWS_NEAR_TERM_VERSION,
        "points_payload": points_payload,
        "grid_payload": grid_payload,
        "station": station_id,
        "requested_latitude": lat,
        "requested_longitude": lon,
        "points_url": _points_url(lat, lon),
        "forecast_grid_url": forecast_grid_url,
    })
    return build_near_term_sample_path(
        station=station_id,
        unit=unit,
        family=family,
        source_role=OFFICIAL_NOWCAST_ROLE,
        source_id=f"NWS_GRID:{forecast_grid_url}|update={properties.get('updateTime')}",
        issued_at=update_at,
        received_at=receipt,
        as_of=float(segment.start),
        segment=segment,
        sampling_step_seconds=NWS_NEAR_TERM_STEP_SECONDS,
        sampling_hypothesis_id=NWS_NEAR_TERM_HYPOTHESIS,
        points=samples,
        source_payload_sha256=source_payload_sha,
    )


def path_from_nws_raw_snapshot(
    snapshot: NWSNearTermRawSnapshot,
    *,
    unit: str,
    family: str,
    segment: TimeSegment,
) -> VerifiedNearTermPath:
    """Project only evidence that existed no later than the frozen decision time."""
    source = verify_nws_raw_snapshot(snapshot)
    if not isinstance(segment, TimeSegment):
        raise NWSNearTermError("NWS_NEAR_TERM_SEGMENT_INVALID")
    if source.received_at > float(segment.start) + 1e-6:
        raise NWSNearTermError("NWS_NEAR_TERM_SNAPSHOT_POSTDATES_DECISION")
    return parse_nws_near_term_grid_path(
        source.points_payload,
        source.grid_payload,
        station=source.station,
        latitude=source.latitude,
        longitude=source.longitude,
        unit=unit,
        family=family,
        segment=segment,
        received_at=source.received_at,
    )


class NWSNearTermGridClient:
    """Read-only two-step NWS client. Fetch first; freeze decision time second."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            follow_redirects=False,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            headers={
                "User-Agent": "polymarket-weather-only-near-term/1.0 (+https://github.com/heroo2123/Alpha)",
                "Accept": "application/geo+json",
            },
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def _json_get(self, url: str) -> tuple[dict, float]:
        try:
            response = await self.http.get(url)
        except httpx.TimeoutException:
            raise NWSNearTermError("NWS_NEAR_TERM_PROVIDER_TIMEOUT") from None
        except httpx.RequestError:
            raise NWSNearTermError("NWS_NEAR_TERM_PROVIDER_TRANSPORT") from None
        if response.is_redirect:
            raise NWSNearTermError("NWS_NEAR_TERM_PROVIDER_REDIRECT")
        if response.status_code >= 400:
            raise NWSNearTermError("NWS_NEAR_TERM_PROVIDER_HTTP_STATUS")
        if len(response.content) > MAX_RESPONSE_BYTES:
            raise NWSNearTermError("NWS_NEAR_TERM_PROVIDER_RESPONSE_CAP")
        received = time.time()
        try:
            payload = response.json()
        except Exception:
            raise NWSNearTermError("NWS_NEAR_TERM_PROVIDER_JSON_INVALID") from None
        if not isinstance(payload, dict):
            raise NWSNearTermError("NWS_NEAR_TERM_PROVIDER_JSON_INVALID")
        return payload, received

    async def fetch_snapshot(
        self,
        *,
        station: str,
        latitude: float,
        longitude: float,
    ) -> NWSNearTermRawSnapshot:
        """Acquire immutable raw source state without choosing an as-of decision yet."""
        lat = _finite(latitude, "NWS_NEAR_TERM_COORDINATE_INVALID")
        lon = _finite(longitude, "NWS_NEAR_TERM_COORDINATE_INVALID")
        points_url = _points_url(lat, lon)
        points_payload, points_received = await self._json_get(points_url)
        try:
            properties = points_payload["properties"]
            grid_url = _grid_url(properties["forecastGridData"])
        except (KeyError, TypeError):
            raise NWSNearTermError("NWS_NEAR_TERM_GRID_URL_INVALID") from None
        grid_payload, grid_received = await self._json_get(grid_url)
        return build_nws_raw_snapshot(
            points_payload,
            grid_payload,
            station=station,
            latitude=lat,
            longitude=lon,
            points_received_at=points_received,
            grid_received_at=grid_received,
        )

    async def path(
        self,
        *,
        station: str,
        latitude: float,
        longitude: float,
        unit: str,
        family: str,
        segment: TimeSegment,
    ) -> VerifiedNearTermPath:
        """Compatibility helper that remains fail-closed against await-time backdating.

        Live same-day code should use ``fetch_snapshot`` before freezing decision time,
        then call ``path_from_nws_raw_snapshot``. If this helper's network response
        arrives after the supplied segment start, projection is rejected.
        """
        snapshot = await self.fetch_snapshot(
            station=station,
            latitude=latitude,
            longitude=longitude,
        )
        return path_from_nws_raw_snapshot(
            snapshot,
            unit=unit,
            family=family,
            segment=segment,
        )
