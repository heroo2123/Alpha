from __future__ import annotations

"""Strict hourly GEFS evidence for the same-day three-layer research model.

The existing daily-extreme adapter is intentionally unsuitable for same-day
conditioning because it can carry an elapsed model extreme forward after official
observations have replaced that part of the day. This module instead preserves the
full per-member target-day trajectory.

The same-day adapter pins Open-Meteo to the explicit NOAA GEFS 0.25-degree domain,
``nearest`` cell selection and explicit ``hourly`` temporal resolution. Open-Meteo's
GEFS 0.25-degree source is natively coarser in time and the provider supplies hourly
values for this query policy; those values are therefore a provider interpolation
hypothesis, not extra independent native model observations. That policy is frozen in
the evidence identity for prospective validation.

Open-Meteo does not expose an authoritative NCEP initialization timestamp in the
schema used here. We therefore never invent one. ``received_at`` is the only
availability authority, and conversion to an unresolved-path proof conservatively
uses that receipt as the effective issue time. This is safe for future U(t) and
causes already-elapsed unresolved segments to fail closed unless another adapter can
prove the run existed before those segments.

``content_run_id`` is a deterministic identity for the returned model content, not a
claim about an upstream NCEP run identifier. Identical repeated retrievals collapse
to the same content identity so they cannot be mistaken for independent ensemble
votes.

All output remains research-only, uncalibrated and non-financial.
"""

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from .config import settings
from .weather_only_conditioned_extremes import TimeSegment
from .weather_only_conditioned_paths import (
    MemberPathPoint,
    VerifiedRemainingHoursPath,
    build_gefs_remaining_hours_path,
)
from .weather_only_forecast import (
    CELL_SELECTION_POLICY,
    GEFS_PERTURBED_MEMBERS,
    GEFS_TOTAL_MEMBERS,
    OPEN_METEO_ENSEMBLE,
)


GEFS_HOURLY_ADAPTER_VERSION = "open_meteo_ncep_gefs025_hourly_paths_v2_31_members"
GEFS_HOURLY_ROLE = "FORECAST_MEMBER_PATH_RESEARCH_ONLY"
GEFS_HOURLY_VARIABLE = "temperature_2m"
GEFS_HOURLY_STEP_SECONDS = 3600
GEFS_HOURLY_PROVIDER_MODEL = "ncep_gefs025"
GEFS_HOURLY_TEMPORAL_RESOLUTION = "hourly"
GEFS_HOURLY_CELL_SELECTION = CELL_SELECTION_POLICY


class GEFSHourlyError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise GEFSHourlyError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise GEFSHourlyError(code) from None
    if not math.isfinite(number):
        raise GEFSHourlyError(code)
    return number


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
        raise GEFSHourlyError("GEFS_HOURLY_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _member_keys() -> tuple[str, ...]:
    return (GEFS_HOURLY_VARIABLE,) + tuple(
        f"{GEFS_HOURLY_VARIABLE}_member{index:02d}"
        for index in range(1, GEFS_PERTURBED_MEMBERS + 1)
    )


def _member_labels() -> tuple[str, ...]:
    return ("control",) + tuple(
        f"member{index:02d}" for index in range(1, GEFS_PERTURBED_MEMBERS + 1)
    )


def _unit_symbol(unit: str) -> str:
    if unit == "F":
        return "°F"
    if unit == "C":
        return "°C"
    raise GEFSHourlyError("GEFS_HOURLY_UNIT_UNSUPPORTED")


def _station(value: object) -> str:
    station = str(value or "").strip().upper()
    if len(station) != 4 or not station.isalnum():
        raise GEFSHourlyError("GEFS_HOURLY_STATION_INVALID")
    return station


def _local_target_bounds(target: date, timezone_name: str) -> tuple[float, float]:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise GEFSHourlyError("GEFS_HOURLY_TIMEZONE_INVALID") from None
    start = datetime(target.year, target.month, target.day, tzinfo=zone)
    following = target + timedelta(days=1)
    end = datetime(following.year, following.month, following.day, tzinfo=zone)
    return start.timestamp(), end.timestamp()


def _parse_local_valid_times(raw_times: object, *, target: date, timezone_name: str) -> tuple[float, ...]:
    if not isinstance(raw_times, list) or not raw_times:
        raise GEFSHourlyError("GEFS_HOURLY_TIME_SERIES_INVALID")
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise GEFSHourlyError("GEFS_HOURLY_TIMEZONE_INVALID") from None

    parsed: list[float] = []
    seen_raw: set[str] = set()
    for raw in raw_times:
        if not isinstance(raw, str) or not raw.strip() or raw != raw.strip():
            raise GEFSHourlyError("GEFS_HOURLY_TIME_VALUE_INVALID")
        if raw in seen_raw:
            # Local timestamps without offsets cannot disambiguate a repeated DST
            # wall-clock hour. Refuse rather than silently selecting fold=0.
            raise GEFSHourlyError("GEFS_HOURLY_TIME_DUPLICATE_OR_DST_AMBIGUOUS")
        seen_raw.add(raw)
        try:
            value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            raise GEFSHourlyError("GEFS_HOURLY_TIME_VALUE_INVALID") from None
        if value.tzinfo is None or value.utcoffset() is None:
            value = value.replace(tzinfo=zone)
        local = value.astimezone(zone)
        if local.date() != target:
            raise GEFSHourlyError("GEFS_HOURLY_TIME_OUTSIDE_TARGET_LOCAL_DATE")
        parsed.append(local.timestamp())

    if len(set(parsed)) != len(parsed):
        raise GEFSHourlyError("GEFS_HOURLY_TIME_INSTANT_DUPLICATE")
    if parsed != sorted(parsed):
        raise GEFSHourlyError("GEFS_HOURLY_TIME_NOT_MONOTONIC")
    for before, after in zip(parsed, parsed[1:]):
        if abs((after - before) - GEFS_HOURLY_STEP_SECONDS) > 1e-6:
            raise GEFSHourlyError("GEFS_HOURLY_GRID_NOT_EXACT_HOURLY")

    target_start, target_end = _local_target_bounds(target, timezone_name)
    if abs(parsed[0] - target_start) > 1e-6:
        raise GEFSHourlyError("GEFS_HOURLY_TARGET_START_MISSING")
    if abs((parsed[-1] + GEFS_HOURLY_STEP_SECONDS) - target_end) > 1e-6:
        raise GEFSHourlyError("GEFS_HOURLY_TARGET_END_COVERAGE_MISSING")
    return tuple(parsed)


@dataclass(frozen=True, slots=True)
class GEFSHourlyMemberSeries:
    member_label: str
    values: tuple[float, ...]

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class GEFSHourlyTargetDay:
    adapter: str
    provider: str
    provider_model: str
    query_cell_selection: str
    query_temporal_resolution: str
    source_role: str
    station: str
    target_date: date
    timezone: str
    unit: str
    requested_latitude: float
    requested_longitude: float
    resolved_latitude: float
    resolved_longitude: float
    valid_times: tuple[float, ...]
    member_labels: tuple[str, ...]
    member_series: tuple[GEFSHourlyMemberSeries, ...]
    received_at: float
    content_run_id: str
    evidence_sha256: str
    calibrated_probability: bool = field(init=False, default=False)
    settlement_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        value = asdict(self)
        value["target_date"] = self.target_date.isoformat()
        value["member_series"] = [series.as_dict() for series in self.member_series]
        return value


def _content_payload(distribution: GEFSHourlyTargetDay) -> dict:
    return {
        "adapter": distribution.adapter,
        "provider": distribution.provider,
        "provider_model": distribution.provider_model,
        "query_cell_selection": distribution.query_cell_selection,
        "query_temporal_resolution": distribution.query_temporal_resolution,
        "source_role": distribution.source_role,
        "station": distribution.station,
        "target_date": distribution.target_date.isoformat(),
        "timezone": distribution.timezone,
        "unit": distribution.unit,
        "requested_latitude": distribution.requested_latitude,
        "requested_longitude": distribution.requested_longitude,
        "resolved_latitude": distribution.resolved_latitude,
        "resolved_longitude": distribution.resolved_longitude,
        "valid_times": distribution.valid_times,
        "member_labels": distribution.member_labels,
        "member_series": [series.as_dict() for series in distribution.member_series],
    }


def _evidence_payload(distribution: GEFSHourlyTargetDay) -> dict:
    payload = _content_payload(distribution)
    payload.update({
        "received_at": distribution.received_at,
        "content_run_id": distribution.content_run_id,
        "calibrated_probability": distribution.calibrated_probability,
        "settlement_authority": distribution.settlement_authority,
        "financial_authority": distribution.financial_authority,
    })
    return payload


def parse_open_meteo_gefs_hourly_target_day(
    payload: object,
    *,
    station: str,
    target_date: date,
    unit: str,
    timezone: str,
    requested_latitude: float,
    requested_longitude: float,
    received_at: float,
    provider_model: str = GEFS_HOURLY_PROVIDER_MODEL,
    query_cell_selection: str = GEFS_HOURLY_CELL_SELECTION,
    query_temporal_resolution: str = GEFS_HOURLY_TEMPORAL_RESOLUTION,
) -> GEFSHourlyTargetDay:
    station_id = _station(station)
    if type(target_date) is not date:
        raise GEFSHourlyError("GEFS_HOURLY_TARGET_DATE_INVALID")
    if provider_model != GEFS_HOURLY_PROVIDER_MODEL:
        raise GEFSHourlyError("GEFS_HOURLY_PROVIDER_MODEL_MISMATCH")
    if query_cell_selection != GEFS_HOURLY_CELL_SELECTION:
        raise GEFSHourlyError("GEFS_HOURLY_CELL_SELECTION_MISMATCH")
    if query_temporal_resolution != GEFS_HOURLY_TEMPORAL_RESOLUTION:
        raise GEFSHourlyError("GEFS_HOURLY_TEMPORAL_RESOLUTION_MISMATCH")
    if not isinstance(timezone, str) or not timezone.strip() or timezone != timezone.strip():
        raise GEFSHourlyError("GEFS_HOURLY_TIMEZONE_INVALID")
    expected_unit = _unit_symbol(unit)
    request_lat = _finite(requested_latitude, "GEFS_HOURLY_REQUEST_COORDINATE_INVALID")
    request_lon = _finite(requested_longitude, "GEFS_HOURLY_REQUEST_COORDINATE_INVALID")
    if not -90.0 <= request_lat <= 90.0 or not -180.0 <= request_lon <= 180.0:
        raise GEFSHourlyError("GEFS_HOURLY_REQUEST_COORDINATE_INVALID")
    receipt = _finite(received_at, "GEFS_HOURLY_RECEIPT_INVALID")
    if receipt < 0.0:
        raise GEFSHourlyError("GEFS_HOURLY_RECEIPT_INVALID")
    if not isinstance(payload, dict):
        raise GEFSHourlyError("GEFS_HOURLY_ENVELOPE_INVALID")
    if str(payload.get("timezone") or "") != timezone:
        raise GEFSHourlyError("GEFS_HOURLY_TIMEZONE_MISMATCH")

    resolved_lat = _finite(payload.get("latitude"), "GEFS_HOURLY_RESOLVED_COORDINATE_INVALID")
    resolved_lon = _finite(payload.get("longitude"), "GEFS_HOURLY_RESOLVED_COORDINATE_INVALID")
    if not -90.0 <= resolved_lat <= 90.0 or not -180.0 <= resolved_lon <= 180.0:
        raise GEFSHourlyError("GEFS_HOURLY_RESOLVED_COORDINATE_INVALID")

    hourly = payload.get("hourly")
    units = payload.get("hourly_units")
    if not isinstance(hourly, dict) or not isinstance(units, dict):
        raise GEFSHourlyError("GEFS_HOURLY_SCHEMA_INVALID")
    valid_times = _parse_local_valid_times(
        hourly.get("time"), target=target_date, timezone_name=timezone
    )
    expected_keys = _member_keys()
    if set(str(key) for key in hourly if key != "time") != set(expected_keys):
        raise GEFSHourlyError("GEFS_HOURLY_MEMBER_SCHEMA_DRIFT")
    if set(str(key) for key in units) != {"time", *expected_keys}:
        raise GEFSHourlyError("GEFS_HOURLY_UNIT_SCHEMA_DRIFT")
    if str(units.get("time") or "") != "iso8601":
        raise GEFSHourlyError("GEFS_HOURLY_TIME_UNIT_INVALID")

    labels = _member_labels()
    if len(labels) != GEFS_TOTAL_MEMBERS:
        raise GEFSHourlyError("GEFS_HOURLY_MEMBER_COUNT_INTERNAL_INVALID")
    series_rows: list[GEFSHourlyMemberSeries] = []
    for label, key in zip(labels, expected_keys):
        if str(units.get(key) or "") != expected_unit:
            raise GEFSHourlyError("GEFS_HOURLY_TEMPERATURE_UNIT_MISMATCH")
        values = hourly.get(key)
        if not isinstance(values, list) or len(values) != len(valid_times):
            raise GEFSHourlyError("GEFS_HOURLY_MEMBER_SERIES_LENGTH_MISMATCH")
        parsed_values = tuple(
            _finite(value, "GEFS_HOURLY_MEMBER_VALUE_INVALID") for value in values
        )
        series_rows.append(GEFSHourlyMemberSeries(label, parsed_values))

    shell = GEFSHourlyTargetDay(
        adapter=GEFS_HOURLY_ADAPTER_VERSION,
        provider="Open-Meteo Ensemble API",
        provider_model=provider_model,
        query_cell_selection=query_cell_selection,
        query_temporal_resolution=query_temporal_resolution,
        source_role=GEFS_HOURLY_ROLE,
        station=station_id,
        target_date=target_date,
        timezone=timezone,
        unit=unit,
        requested_latitude=request_lat,
        requested_longitude=request_lon,
        resolved_latitude=resolved_lat,
        resolved_longitude=resolved_lon,
        valid_times=valid_times,
        member_labels=labels,
        member_series=tuple(series_rows),
        received_at=receipt,
        content_run_id="0" * 64,
        evidence_sha256="0" * 64,
    )
    content_id = _sha(_content_payload(shell))
    with_content = GEFSHourlyTargetDay(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name not in {"content_run_id", "evidence_sha256"}
        },
        content_run_id=content_id,
        evidence_sha256="0" * 64,
    )
    return GEFSHourlyTargetDay(
        **{
            name: getattr(with_content, name)
            for name, definition in with_content.__dataclass_fields__.items()
            if definition.init and name != "evidence_sha256"
        },
        evidence_sha256=_sha(_evidence_payload(with_content)),
    )


def verify_gefs_hourly_evidence(distribution: object) -> GEFSHourlyTargetDay:
    if not isinstance(distribution, GEFSHourlyTargetDay):
        raise GEFSHourlyError("GEFS_HOURLY_DISTRIBUTION_TYPE_INVALID")
    if (
        distribution.adapter != GEFS_HOURLY_ADAPTER_VERSION
        or distribution.provider != "Open-Meteo Ensemble API"
        or distribution.provider_model != GEFS_HOURLY_PROVIDER_MODEL
        or distribution.query_cell_selection != GEFS_HOURLY_CELL_SELECTION
        or distribution.query_temporal_resolution != GEFS_HOURLY_TEMPORAL_RESOLUTION
        or distribution.source_role != GEFS_HOURLY_ROLE
    ):
        raise GEFSHourlyError("GEFS_HOURLY_ADAPTER_IDENTITY_MISMATCH")
    if (
        distribution.member_labels != _member_labels()
        or len(distribution.member_series) != GEFS_TOTAL_MEMBERS
    ):
        raise GEFSHourlyError("GEFS_HOURLY_MEMBER_IDENTITY_MISMATCH")
    if tuple(series.member_label for series in distribution.member_series) != distribution.member_labels:
        raise GEFSHourlyError("GEFS_HOURLY_MEMBER_SERIES_IDENTITY_MISMATCH")
    if any(len(series.values) != len(distribution.valid_times) for series in distribution.member_series):
        raise GEFSHourlyError("GEFS_HOURLY_MEMBER_SERIES_LENGTH_MISMATCH")
    if distribution.content_run_id != _sha(_content_payload(distribution)):
        raise GEFSHourlyError("GEFS_HOURLY_CONTENT_RUN_DIGEST_MISMATCH")
    if distribution.evidence_sha256 != _sha(_evidence_payload(distribution)):
        raise GEFSHourlyError("GEFS_HOURLY_EVIDENCE_DIGEST_MISMATCH")
    if any((
        distribution.calibrated_probability,
        distribution.settlement_authority,
        distribution.financial_authority,
    )):
        raise GEFSHourlyError("GEFS_HOURLY_AUTHORITY_BOUNDARY_BROKEN")
    return distribution


def build_verified_gefs_path_from_hourly(
    distribution: GEFSHourlyTargetDay,
    *,
    family: str,
    as_of: float,
    target_end: float,
    unresolved_segments: tuple[TimeSegment, ...],
) -> VerifiedRemainingHoursPath:
    """Project full target-day members onto an already-authorized U(t) mask."""
    source = verify_gefs_hourly_evidence(distribution)
    cutoff = _finite(as_of, "GEFS_HOURLY_AS_OF_INVALID")
    if source.received_at > cutoff:
        raise GEFSHourlyError("GEFS_HOURLY_LOOKAHEAD")
    expected_times: set[float] = set()
    for segment in unresolved_segments:
        if not isinstance(segment, TimeSegment):
            raise GEFSHourlyError("GEFS_HOURLY_SEGMENT_TYPE_INVALID")
        cursor = float(segment.start)
        while cursor < float(segment.end) - 1e-6:
            expected_times.add(round(cursor, 6))
            cursor += GEFS_HOURLY_STEP_SECONDS
    available = {
        round(value, 6): index for index, value in enumerate(source.valid_times)
    }
    if not expected_times or not expected_times.issubset(available):
        raise GEFSHourlyError("GEFS_HOURLY_UNRESOLVED_GRID_NOT_COVERED")

    points: list[MemberPathPoint] = []
    for series in source.member_series:
        for valid_at in sorted(expected_times):
            points.append(
                MemberPathPoint(
                    member_label=series.member_label,
                    valid_at=valid_at,
                    value=series.values[available[valid_at]],
                )
            )
    try:
        return build_gefs_remaining_hours_path(
            station=source.station,
            unit=source.unit,
            family=family,
            as_of=cutoff,
            # Open-Meteo does not expose provider initialization authority. Receipt
            # is the conservative availability time: it cannot predate information.
            issued_at=source.received_at,
            received_at=source.received_at,
            target_end=target_end,
            unresolved_segments=unresolved_segments,
            points=tuple(points),
            provider_run_id=f"openmeteo-content:{source.content_run_id}",
            grid_step_seconds=GEFS_HOURLY_STEP_SECONDS,
            grid_anchor=source.valid_times[0],
        )
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise GEFSHourlyError(f"GEFS_HOURLY_PATH_INVALID:{code}") from exc


class OpenMeteoGEFSHourlyClient:
    """Read-only hourly GEFS client with strict 31-member target-day parsing."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            headers={
                "User-Agent": "polymarket-weather-only-gefs-hourly/2.0 (+https://github.com/heroo2123/Alpha)"
            },
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def target_day(
        self,
        *,
        station: str,
        latitude: float,
        longitude: float,
        target_date: date,
        unit: str,
        timezone: str,
    ) -> GEFSHourlyTargetDay:
        station_id = _station(station)
        if type(target_date) is not date:
            raise GEFSHourlyError("GEFS_HOURLY_TARGET_DATE_INVALID")
        unit_name = "fahrenheit" if unit == "F" else "celsius" if unit == "C" else None
        if unit_name is None:
            raise GEFSHourlyError("GEFS_HOURLY_UNIT_UNSUPPORTED")
        lat = _finite(latitude, "GEFS_HOURLY_REQUEST_COORDINATE_INVALID")
        lon = _finite(longitude, "GEFS_HOURLY_REQUEST_COORDINATE_INVALID")
        if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
            raise GEFSHourlyError("GEFS_HOURLY_REQUEST_COORDINATE_INVALID")
        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": GEFS_HOURLY_VARIABLE,
            "models": GEFS_HOURLY_PROVIDER_MODEL,
            "temporal_resolution": GEFS_HOURLY_TEMPORAL_RESOLUTION,
            "temperature_unit": unit_name,
            "timezone": timezone,
            "start_date": target_date.isoformat(),
            "end_date": target_date.isoformat(),
            "cell_selection": GEFS_HOURLY_CELL_SELECTION,
        }
        try:
            response = await self.http.get(OPEN_METEO_ENSEMBLE, params=params)
        except httpx.TimeoutException:
            raise GEFSHourlyError("GEFS_HOURLY_PROVIDER_TIMEOUT") from None
        except httpx.RequestError:
            raise GEFSHourlyError("GEFS_HOURLY_PROVIDER_TRANSPORT") from None
        if response.status_code >= 400:
            raise GEFSHourlyError("GEFS_HOURLY_PROVIDER_HTTP_STATUS")
        received = time.time()
        try:
            payload = response.json()
        except Exception:
            raise GEFSHourlyError("GEFS_HOURLY_PROVIDER_JSON_INVALID") from None
        return parse_open_meteo_gefs_hourly_target_day(
            payload,
            station=station_id,
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
