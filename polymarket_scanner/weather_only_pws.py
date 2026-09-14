from __future__ import annotations

"""Read-only Weather Company PWS evidence for same-day weather research.

PWS observations are predictive/diagnostic evidence only. They never replace the
official settlement-station observation population, never reweight the three-layer
probability, never create Telegram alerts, and never grant financial authority.

The client intentionally stores only normalized evidence. API keys and raw request
URLs are never placed in snapshots, diagnostics or exception text.
"""

import hashlib
import json
import math
import os
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from statistics import median

import httpx


PWS_SOURCE = "WEATHER_COMPANY_PWS"
PWS_SNAPSHOT_VERSION = "weather_pws_snapshot_v1_diagnostic_only"
PWS_DIAGNOSTIC_VERSION = "weather_pws_diagnostic_v1_no_settlement_authority"
PWS_STATUS_UNCONFIGURED = "UNCONFIGURED"
PWS_STATUS_AVAILABLE = "AVAILABLE"
PWS_STATUS_NO_FRESH_QC = "NO_FRESH_QC_PWS"
PWS_STATUS_AUTH_ERROR = "PWS_AUTH_ERROR"
PWS_STATUS_TRANSPORT_ERROR = "PWS_TRANSPORT_ERROR"
PWS_STATUS_PROVIDER_ERROR = "PWS_PROVIDER_ERROR"
PWS_ALLOWED_STATUSES = {
    PWS_STATUS_UNCONFIGURED,
    PWS_STATUS_AVAILABLE,
    PWS_STATUS_NO_FRESH_QC,
    PWS_STATUS_AUTH_ERROR,
    PWS_STATUS_TRANSPORT_ERROR,
    PWS_STATUS_PROVIDER_ERROR,
}
DEFAULT_PWS_MAX_DISTANCE_KM = 15.0
DEFAULT_PWS_MAX_AGE_SECONDS = 900.0
DEFAULT_PWS_MAX_FUTURE_SKEW_SECONDS = 120.0
DEFAULT_PWS_MAX_CANDIDATES = 5
DEFAULT_PWS_MAX_ACCEPTED = 3
DEFAULT_PWS_CONTRADICTION_F = 2.0
DEFAULT_PWS_CONTRADICTION_C = 1.1


class PWSError(RuntimeError):
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
        raise PWSError("PWS_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise PWSError(code)
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        raise PWSError(code) from None
    if not math.isfinite(result):
        raise PWSError(code)
    return result


def _latitude(value: object) -> float:
    result = _finite(value, "PWS_LATITUDE_INVALID")
    if not -90.0 <= result <= 90.0:
        raise PWSError("PWS_LATITUDE_INVALID")
    return result


def _longitude(value: object) -> float:
    result = _finite(value, "PWS_LONGITUDE_INVALID")
    if not -180.0 <= result <= 180.0:
        raise PWSError("PWS_LONGITUDE_INVALID")
    return result


def _unit(value: object) -> str:
    result = str(value or "").strip().upper()
    if result not in {"F", "C"}:
        raise PWSError("PWS_UNIT_INVALID")
    return result


def _haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    from math import asin, cos, radians, sin, sqrt

    earth = 6371.0088
    p1 = radians(lat1)
    p2 = radians(lat2)
    dlat = radians(lat2 - lat1)
    dlon = radians(lon2 - lon1)
    value = sin(dlat / 2.0) ** 2 + cos(p1) * cos(p2) * sin(dlon / 2.0) ** 2
    return 2.0 * earth * asin(sqrt(value))


def _epoch(value: object) -> float:
    if value not in (None, ""):
        try:
            number = float(value)
        except (TypeError, ValueError, OverflowError):
            number = 0.0
        if math.isfinite(number) and number > 0.0:
            if number >= 100_000_000_000.0:
                number /= 1000.0
            return number
    raise PWSError("PWS_OBSERVED_AT_INVALID")


def _iso_epoch(value: object) -> float:
    text = str(value or "").strip()
    if not text:
        raise PWSError("PWS_OBSERVED_AT_INVALID")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise PWSError("PWS_OBSERVED_AT_INVALID") from None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


def _observed_at(row: dict) -> float:
    try:
        return _epoch(row.get("epoch"))
    except PWSError:
        return _iso_epoch(row.get("obsTimeUtc"))


def _qc_pass(value: object) -> bool:
    if isinstance(value, bool):
        return value is True
    try:
        return int(value) == 1
    except (TypeError, ValueError, OverflowError):
        return False


@dataclass(frozen=True, slots=True)
class PWSObservation:
    station_id: str
    latitude: float
    longitude: float
    distance_km: float
    observed_at: float
    received_at: float
    unit: str
    temperature: float
    qc_status: int
    source: str = field(init=False, default=PWS_SOURCE)
    settlement_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class PWSDiagnosticSnapshot:
    version: str
    status: str
    configured: bool
    requested_latitude: float
    requested_longitude: float
    unit: str
    received_at: float
    observations: tuple[PWSObservation, ...]
    median_temperature: float | None
    min_temperature: float | None
    max_temperature: float | None
    spread: float | None
    max_distance_km: float
    max_age_seconds: float
    max_future_skew_seconds: float
    evidence_sha256: str
    diagnostic_only: bool = field(init=False, default=True)
    may_replace_official_observation: bool = field(init=False, default=False)
    may_reweight_probability: bool = field(init=False, default=False)
    settlement_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        result = asdict(self)
        result["observations"] = [row.as_dict() for row in self.observations]
        return result


def _snapshot_payload(value: PWSDiagnosticSnapshot) -> dict:
    payload = value.as_dict()
    payload.pop("evidence_sha256", None)
    return payload


def verify_pws_snapshot(value: object) -> PWSDiagnosticSnapshot:
    if not isinstance(value, PWSDiagnosticSnapshot):
        raise PWSError("PWS_SNAPSHOT_TYPE_INVALID")
    if value.version != PWS_SNAPSHOT_VERSION:
        raise PWSError("PWS_SNAPSHOT_VERSION_INVALID")
    if value.status not in PWS_ALLOWED_STATUSES:
        raise PWSError("PWS_SNAPSHOT_STATUS_INVALID")
    if value.status == PWS_STATUS_AVAILABLE and not value.observations:
        raise PWSError("PWS_SNAPSHOT_AVAILABLE_WITHOUT_OBSERVATIONS")
    if value.status != PWS_STATUS_AVAILABLE and value.observations:
        raise PWSError("PWS_SNAPSHOT_UNAVAILABLE_WITH_OBSERVATIONS")
    if value.configured is False and value.status != PWS_STATUS_UNCONFIGURED:
        raise PWSError("PWS_SNAPSHOT_CONFIGURATION_STATE_INVALID")
    if value.configured is True and value.status == PWS_STATUS_UNCONFIGURED:
        raise PWSError("PWS_SNAPSHOT_CONFIGURATION_STATE_INVALID")
    if value.status == PWS_STATUS_AVAILABLE:
        temperatures = [float(row.temperature) for row in value.observations]
        expected_median = float(median(temperatures))
        expected_min = float(min(temperatures))
        expected_max = float(max(temperatures))
        if value.median_temperature != expected_median:
            raise PWSError("PWS_SNAPSHOT_MEDIAN_MISMATCH")
        if value.min_temperature != expected_min or value.max_temperature != expected_max:
            raise PWSError("PWS_SNAPSHOT_RANGE_MISMATCH")
        if value.spread != expected_max - expected_min:
            raise PWSError("PWS_SNAPSHOT_SPREAD_MISMATCH")
        for row in value.observations:
            if row.unit != value.unit or row.qc_status != 1:
                raise PWSError("PWS_SNAPSHOT_OBSERVATION_INVALID")
            if row.distance_km > value.max_distance_km + 1e-9:
                raise PWSError("PWS_SNAPSHOT_DISTANCE_INVALID")
            age = value.received_at - row.observed_at
            if age > value.max_age_seconds + 1e-9:
                raise PWSError("PWS_SNAPSHOT_OBSERVATION_STALE")
            if age < -value.max_future_skew_seconds - 1e-9:
                raise PWSError("PWS_SNAPSHOT_OBSERVATION_FROM_FUTURE")
    else:
        if any(
            item is not None
            for item in (
                value.median_temperature,
                value.min_temperature,
                value.max_temperature,
                value.spread,
            )
        ):
            raise PWSError("PWS_SNAPSHOT_UNAVAILABLE_HAS_STATISTICS")
    if value.evidence_sha256 != _sha(_snapshot_payload(value)):
        raise PWSError("PWS_SNAPSHOT_DIGEST_MISMATCH")
    if any(
        (
            not value.diagnostic_only,
            value.may_replace_official_observation,
            value.may_reweight_probability,
            value.settlement_authority,
            value.financial_authority,
        )
    ):
        raise PWSError("PWS_SNAPSHOT_AUTHORITY_BOUNDARY_BROKEN")
    return value


def _build_snapshot(
    *,
    status: str,
    configured: bool,
    latitude: float,
    longitude: float,
    unit: str,
    received_at: float,
    observations: tuple[PWSObservation, ...] = (),
    max_distance_km: float,
    max_age_seconds: float,
    max_future_skew_seconds: float,
) -> PWSDiagnosticSnapshot:
    temperatures = [float(row.temperature) for row in observations]
    middle = float(median(temperatures)) if temperatures else None
    low = float(min(temperatures)) if temperatures else None
    high = float(max(temperatures)) if temperatures else None
    spread = high - low if high is not None and low is not None else None
    shell = PWSDiagnosticSnapshot(
        version=PWS_SNAPSHOT_VERSION,
        status=status,
        configured=configured,
        requested_latitude=latitude,
        requested_longitude=longitude,
        unit=unit,
        received_at=received_at,
        observations=observations,
        median_temperature=middle,
        min_temperature=low,
        max_temperature=high,
        spread=spread,
        max_distance_km=max_distance_km,
        max_age_seconds=max_age_seconds,
        max_future_skew_seconds=max_future_skew_seconds,
        evidence_sha256="0" * 64,
    )
    result = PWSDiagnosticSnapshot(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "evidence_sha256"
        },
        evidence_sha256=_sha(_snapshot_payload(shell)),
    )
    return verify_pws_snapshot(result)


@dataclass(frozen=True, slots=True)
class PWSDiagnosticRecord:
    version: str
    event_id: str
    station: str
    target_date: str
    unit: str
    as_of: float
    pws_snapshot: dict
    latest_official_temperature: float | None
    latest_official_observed_at: float | None
    pws_median_temperature: float | None
    pws_minus_official: float | None
    contradiction_threshold: float
    contradiction: bool
    status: str
    diagnostic_sha256: str
    predictive_only: bool = field(init=False, default=True)
    may_replace_official_observation: bool = field(init=False, default=False)
    may_reweight_probability: bool = field(init=False, default=False)
    included_in_validated_pnl: bool = field(init=False, default=False)
    same_day_delivery_enabled: bool = field(init=False, default=False)
    settlement_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def _diagnostic_payload(value: PWSDiagnosticRecord) -> dict:
    payload = value.as_dict()
    payload.pop("diagnostic_sha256", None)
    return payload


def verify_pws_diagnostic(value: object) -> PWSDiagnosticRecord:
    if not isinstance(value, PWSDiagnosticRecord):
        raise PWSError("PWS_DIAGNOSTIC_TYPE_INVALID")
    if value.version != PWS_DIAGNOSTIC_VERSION:
        raise PWSError("PWS_DIAGNOSTIC_VERSION_INVALID")
    if value.status not in PWS_ALLOWED_STATUSES:
        raise PWSError("PWS_DIAGNOSTIC_STATUS_INVALID")
    if value.diagnostic_sha256 != _sha(_diagnostic_payload(value)):
        raise PWSError("PWS_DIAGNOSTIC_DIGEST_MISMATCH")
    if value.contradiction and value.pws_minus_official is None:
        raise PWSError("PWS_DIAGNOSTIC_CONTRADICTION_WITHOUT_DELTA")
    if any(
        (
            not value.predictive_only,
            value.may_replace_official_observation,
            value.may_reweight_probability,
            value.included_in_validated_pnl,
            value.same_day_delivery_enabled,
            value.settlement_authority,
            value.financial_authority,
        )
    ):
        raise PWSError("PWS_DIAGNOSTIC_AUTHORITY_BOUNDARY_BROKEN")
    return value


def build_pws_diagnostic(
    *,
    event_id: str,
    station: str,
    target_date: str,
    unit: str,
    as_of: float,
    official_observations: tuple[dict, ...] | list[dict],
    pws_snapshot: PWSDiagnosticSnapshot,
) -> PWSDiagnosticRecord:
    snapshot = verify_pws_snapshot(pws_snapshot)
    cutoff = _finite(as_of, "PWS_DIAGNOSTIC_AS_OF_INVALID")
    if snapshot.received_at > cutoff + 1e-6:
        raise PWSError("PWS_DIAGNOSTIC_SOURCE_LOOKAHEAD")
    normalized_unit = _unit(unit)
    if snapshot.unit != normalized_unit:
        raise PWSError("PWS_DIAGNOSTIC_UNIT_MISMATCH")
    identity = str(event_id or "").strip()
    official_station = str(station or "").strip().upper()
    date_text = str(target_date or "").strip()
    if not identity or not official_station or not date_text:
        raise PWSError("PWS_DIAGNOSTIC_IDENTITY_INVALID")

    latest: dict | None = None
    for row in official_observations:
        if not isinstance(row, dict):
            continue
        observed_at = _finite(row.get("observed_at"), "PWS_OFFICIAL_OBSERVED_AT_INVALID")
        if observed_at > cutoff + 1e-6:
            raise PWSError("PWS_OFFICIAL_OBSERVATION_LOOKAHEAD")
        if str(row.get("station") or "").strip().upper() != official_station:
            raise PWSError("PWS_OFFICIAL_STATION_MISMATCH")
        if str(row.get("unit") or "").strip().upper() != normalized_unit:
            raise PWSError("PWS_OFFICIAL_UNIT_MISMATCH")
        if latest is None or observed_at > float(latest["observed_at"]):
            latest = row

    latest_temp = None
    latest_time = None
    if latest is not None:
        latest_temp = _finite(latest.get("value"), "PWS_OFFICIAL_TEMPERATURE_INVALID")
        latest_time = _finite(latest.get("observed_at"), "PWS_OFFICIAL_OBSERVED_AT_INVALID")

    pws_temp = snapshot.median_temperature
    delta = None
    threshold = DEFAULT_PWS_CONTRADICTION_F if normalized_unit == "F" else DEFAULT_PWS_CONTRADICTION_C
    contradiction = False
    if pws_temp is not None and latest_temp is not None:
        delta = float(pws_temp) - float(latest_temp)
        contradiction = abs(delta) >= threshold

    shell = PWSDiagnosticRecord(
        version=PWS_DIAGNOSTIC_VERSION,
        event_id=identity,
        station=official_station,
        target_date=date_text,
        unit=normalized_unit,
        as_of=cutoff,
        pws_snapshot=snapshot.as_dict(),
        latest_official_temperature=latest_temp,
        latest_official_observed_at=latest_time,
        pws_median_temperature=pws_temp,
        pws_minus_official=delta,
        contradiction_threshold=threshold,
        contradiction=contradiction,
        status=snapshot.status,
        diagnostic_sha256="0" * 64,
    )
    result = PWSDiagnosticRecord(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "diagnostic_sha256"
        },
        diagnostic_sha256=_sha(_diagnostic_payload(shell)),
    )
    return verify_pws_diagnostic(result)


class WeatherCompanyPWSClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        http: httpx.AsyncClient | None = None,
        base_url: str = "https://api.weather.com",
        max_distance_km: float = DEFAULT_PWS_MAX_DISTANCE_KM,
        max_age_seconds: float = DEFAULT_PWS_MAX_AGE_SECONDS,
        max_future_skew_seconds: float = DEFAULT_PWS_MAX_FUTURE_SKEW_SECONDS,
        max_candidates: int = DEFAULT_PWS_MAX_CANDIDATES,
        max_accepted: int = DEFAULT_PWS_MAX_ACCEPTED,
    ) -> None:
        self.api_key = str(
            os.environ.get("WEATHER_PWS_API_KEY", "") if api_key is None else api_key
        ).strip()
        self.base_url = str(base_url).rstrip("/")
        self.max_distance_km = _finite(max_distance_km, "PWS_MAX_DISTANCE_INVALID")
        self.max_age_seconds = _finite(max_age_seconds, "PWS_MAX_AGE_INVALID")
        self.max_future_skew_seconds = _finite(
            max_future_skew_seconds, "PWS_MAX_FUTURE_SKEW_INVALID"
        )
        self.max_candidates = int(max_candidates)
        self.max_accepted = int(max_accepted)
        if self.max_distance_km <= 0.0 or self.max_age_seconds <= 0.0:
            raise PWSError("PWS_CLIENT_LIMIT_INVALID")
        if self.max_future_skew_seconds < 0.0:
            raise PWSError("PWS_CLIENT_LIMIT_INVALID")
        if not 1 <= self.max_candidates <= 10 or not 1 <= self.max_accepted <= self.max_candidates:
            raise PWSError("PWS_CLIENT_LIMIT_INVALID")
        self._owns_http = http is None
        self.http = http or httpx.AsyncClient(
            timeout=httpx.Timeout(12.0, connect=5.0),
            limits=httpx.Limits(
                max_connections=4,
                max_keepalive_connections=2,
                keepalive_expiry=60.0,
            ),
            trust_env=False,
        )

    async def close(self) -> None:
        if self._owns_http:
            await self.http.aclose()

    async def _json(self, path: str, params: dict[str, object]) -> tuple[str, object | None]:
        safe_params = dict(params)
        safe_params["apiKey"] = self.api_key
        try:
            response = await self.http.get(f"{self.base_url}{path}", params=safe_params)
        except httpx.HTTPError:
            return PWS_STATUS_TRANSPORT_ERROR, None
        if response.status_code in {401, 403}:
            return PWS_STATUS_AUTH_ERROR, None
        if response.status_code >= 400:
            return PWS_STATUS_PROVIDER_ERROR, None
        try:
            body = response.json()
        except ValueError:
            return PWS_STATUS_PROVIDER_ERROR, None
        return "OK", body

    @staticmethod
    def _near_rows(body: object) -> list[dict]:
        if not isinstance(body, dict):
            return []
        location = body.get("location")
        if isinstance(location, list):
            return [row for row in location if isinstance(row, dict)]
        if not isinstance(location, dict):
            return []
        station_ids = location.get("stationId")
        if not isinstance(station_ids, list):
            return []
        result: list[dict] = []
        keys = ("stationId", "latitude", "longitude", "qcStatus", "updateTimeUtc")
        for index, station_id in enumerate(station_ids):
            row: dict[str, object] = {}
            for key in keys:
                values = location.get(key)
                if isinstance(values, list) and index < len(values):
                    row[key] = values[index]
            row["stationId"] = station_id
            result.append(row)
        return result

    def _candidate_rows(self, body: object, latitude: float, longitude: float) -> list[tuple]:
        result: list[tuple[float, str, float, float]] = []
        seen: set[str] = set()
        for row in self._near_rows(body):
            station_id = str(row.get("stationId") or "").strip()
            if not station_id or station_id in seen or not _qc_pass(row.get("qcStatus")):
                continue
            try:
                lat = _latitude(row.get("latitude"))
                lon = _longitude(row.get("longitude"))
            except PWSError:
                continue
            distance = _haversine_km(latitude, longitude, lat, lon)
            if distance > self.max_distance_km:
                continue
            seen.add(station_id)
            result.append((distance, station_id, lat, lon))
        result.sort(key=lambda item: (item[0], item[1]))
        return result[: self.max_candidates]

    async def fetch_snapshot(
        self,
        *,
        latitude: float,
        longitude: float,
        unit: str,
    ) -> PWSDiagnosticSnapshot:
        lat = _latitude(latitude)
        lon = _longitude(longitude)
        normalized_unit = _unit(unit)
        received_at = time.time()
        if not self.api_key:
            return _build_snapshot(
                status=PWS_STATUS_UNCONFIGURED,
                configured=False,
                latitude=lat,
                longitude=lon,
                unit=normalized_unit,
                received_at=received_at,
                max_distance_km=self.max_distance_km,
                max_age_seconds=self.max_age_seconds,
                max_future_skew_seconds=self.max_future_skew_seconds,
            )

        status, nearby = await self._json(
            "/v3/location/near",
            {
                "geocode": f"{lat:.6f},{lon:.6f}",
                "product": "pws",
                "format": "json",
            },
        )
        received_at = time.time()
        if status != "OK":
            return _build_snapshot(
                status=status,
                configured=True,
                latitude=lat,
                longitude=lon,
                unit=normalized_unit,
                received_at=received_at,
                max_distance_km=self.max_distance_km,
                max_age_seconds=self.max_age_seconds,
                max_future_skew_seconds=self.max_future_skew_seconds,
            )

        candidates = self._candidate_rows(nearby, lat, lon)
        accepted: list[PWSObservation] = []
        failure_status = PWS_STATUS_NO_FRESH_QC
        api_units = "e" if normalized_unit == "F" else "m"
        value_key = "imperial" if normalized_unit == "F" else "metric"
        for _near_distance, station_id, _near_lat, _near_lon in candidates:
            status, body = await self._json(
                "/v2/pws/observations/current",
                {
                    "stationId": station_id,
                    "format": "json",
                    "units": api_units,
                    "numericPrecision": "decimal",
                },
            )
            received_at = time.time()
            if status == PWS_STATUS_AUTH_ERROR:
                failure_status = status
                break
            if status != "OK":
                if failure_status == PWS_STATUS_NO_FRESH_QC:
                    failure_status = status
                continue
            if not isinstance(body, dict):
                continue
            rows = body.get("observations")
            if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
                continue
            row = rows[0]
            if str(row.get("stationID") or "").strip() != station_id:
                continue
            if not _qc_pass(row.get("qcStatus")):
                continue
            try:
                obs_lat = _latitude(row.get("lat"))
                obs_lon = _longitude(row.get("lon"))
                distance = _haversine_km(lat, lon, obs_lat, obs_lon)
                if distance > self.max_distance_km:
                    continue
                observed_at = _observed_at(row)
                age = received_at - observed_at
                if age > self.max_age_seconds or age < -self.max_future_skew_seconds:
                    continue
                units = row.get(value_key)
                if not isinstance(units, dict):
                    continue
                temperature = _finite(units.get("temp"), "PWS_TEMPERATURE_INVALID")
            except PWSError:
                continue
            accepted.append(
                PWSObservation(
                    station_id=station_id,
                    latitude=obs_lat,
                    longitude=obs_lon,
                    distance_km=distance,
                    observed_at=observed_at,
                    received_at=received_at,
                    unit=normalized_unit,
                    temperature=temperature,
                    qc_status=1,
                )
            )
            if len(accepted) >= self.max_accepted:
                break

        accepted.sort(key=lambda row: (row.distance_km, row.station_id))
        final_status = PWS_STATUS_AVAILABLE if accepted else failure_status
        return _build_snapshot(
            status=final_status,
            configured=True,
            latitude=lat,
            longitude=lon,
            unit=normalized_unit,
            received_at=received_at,
            observations=tuple(accepted),
            max_distance_km=self.max_distance_km,
            max_age_seconds=self.max_age_seconds,
            max_future_skew_seconds=self.max_future_skew_seconds,
        )
