from __future__ import annotations

"""Forecast-distribution evidence for the weather-only research program.

This module deliberately separates *forecast evidence* from settlement evidence and
from calibrated probabilities. Open-Meteo's public ensemble API is useful because
it exposes individual NCEP GEFS ensemble members, but those model values are not the
WRH settlement source and raw member frequencies are not calibrated Polymarket fair
values.

The adapter is intentionally narrow and versioned around the live schema certified
in September 2026: ``ncep_gefs_seamless`` returns one control series plus member01
through member30 for a requested daily extreme. Any member-count/key/unit/timezone
schema drift fails closed.

A caller must also supply an explicit, frozen mapping policy before continuous model
values are quantized onto a whole-degree contract lattice. The only mapping
implemented in this foundation is nearest whole degree with half values away from
zero. That is a *forecast-model preprocessing hypothesis*, not a claim about how a
settlement source rounds raw measurements. It must be calibrated prospectively.
"""

import hashlib
import json
import math
import time
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

import httpx

from .config import settings
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW, CompiledWeatherEvent, WeatherBucket


OPEN_METEO_ENSEMBLE = "https://ensemble-api.open-meteo.com/v1/ensemble"
OPEN_METEO_GEFS_MODEL = "ncep_gefs_seamless"
GEFS_CONTROL_KEY_HIGH = "temperature_2m_max"
GEFS_CONTROL_KEY_LOW = "temperature_2m_min"
GEFS_PERTURBED_MEMBERS = 30
GEFS_TOTAL_MEMBERS = 31
FORECAST_ADAPTER_VERSION = "open_meteo_ncep_gefs_seamless_daily_extreme_v2_grid_bound_31_members"
FORECAST_ROLE = "FORECAST_RESEARCH_ONLY"
SUPPORTED_QUANTIZATION = "NEAREST_WHOLE_DEGREE_HALF_AWAY_FROM_ZERO"
CELL_SELECTION_POLICY = "nearest"


class WeatherForecastError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class EnsembleExtremeDistribution:
    adapter: str
    provider: str
    provider_model: str
    station: str
    target_date: date
    family: str
    unit: str
    timezone: str
    requested_latitude: float
    requested_longitude: float
    resolved_latitude: float
    resolved_longitude: float
    member_labels: tuple[str, ...]
    member_values: tuple[float, ...]
    received_at: float
    evidence_sha256: str
    source_role: str
    settlement_authority: bool
    calibration_label_authority: bool
    calibrated_probability: bool
    financial_authority: bool

    def as_dict(self) -> dict:
        value = asdict(self)
        value["target_date"] = self.target_date.isoformat()
        return value


@dataclass(frozen=True, slots=True)
class EnsembleMappingPolicy:
    policy_id: str
    include_control: bool
    quantization: str = SUPPORTED_QUANTIZATION

    def __post_init__(self) -> None:
        if not isinstance(self.policy_id, str) or not self.policy_id.strip():
            raise ValueError("policy_id is required")
        if type(self.include_control) is not bool:
            raise ValueError("include_control must be boolean")
        if self.quantization != SUPPORTED_QUANTIZATION:
            raise ValueError("unsupported forecast quantization policy")


@dataclass(frozen=True, slots=True)
class BucketEnsembleFrequency:
    market_id: str
    condition_id: str
    yes_token: str
    no_token: str
    lower: float | None
    upper: float | None
    member_hits: int
    member_count: int
    raw_member_frequency: float
    raw_no_frequency: float
    mapping_policy_id: str
    calibrated: bool
    financial_authority: bool

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class EnsembleBucketForecast:
    adapter: str
    event_id: str
    station: str
    target_date: date
    family: str
    unit: str
    provider_model: str
    source_evidence_sha256: str
    mapping_policy_id: str
    quantization: str
    included_control: bool
    member_count: int
    bucket_frequencies: tuple[BucketEnsembleFrequency, ...]
    probability_sum: float
    calibrated: bool
    settlement_authority: bool
    financial_authority: bool

    def as_dict(self) -> dict:
        value = asdict(self)
        value["target_date"] = self.target_date.isoformat()
        return value


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise WeatherForecastError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherForecastError(code)
    if not math.isfinite(number):
        raise WeatherForecastError(code)
    return number


def _daily_variable(family: str) -> str:
    if family == DAILY_HIGH:
        return GEFS_CONTROL_KEY_HIGH
    if family == DAILY_LOW:
        return GEFS_CONTROL_KEY_LOW
    raise WeatherForecastError("FORECAST_FAMILY_UNSUPPORTED")


def _expected_member_keys(variable: str) -> tuple[str, ...]:
    return (variable,) + tuple(f"{variable}_member{index:02d}" for index in range(1, GEFS_PERTURBED_MEMBERS + 1))


def _expected_member_labels() -> tuple[str, ...]:
    return ("control",) + tuple(f"member{index:02d}" for index in range(1, GEFS_PERTURBED_MEMBERS + 1))


def _unit_symbol(unit: str) -> str:
    if unit == "F":
        return "°F"
    if unit == "C":
        return "°C"
    raise WeatherForecastError("FORECAST_UNIT_UNSUPPORTED")


def _evidence_digest(
    *,
    model: str,
    station: str,
    target_date: date,
    family: str,
    unit: str,
    timezone: str,
    requested_latitude: float,
    requested_longitude: float,
    resolved_latitude: float,
    resolved_longitude: float,
    labels: tuple[str, ...],
    values: tuple[float, ...],
) -> str:
    payload = {
        "adapter": FORECAST_ADAPTER_VERSION,
        "provider": "Open-Meteo Ensemble API",
        "model": model,
        "cell_selection": CELL_SELECTION_POLICY,
        "station": station,
        "target_date": target_date.isoformat(),
        "family": family,
        "unit": unit,
        "timezone": timezone,
        "requested_latitude": requested_latitude,
        "requested_longitude": requested_longitude,
        "resolved_latitude": resolved_latitude,
        "resolved_longitude": resolved_longitude,
        "member_labels": labels,
        "member_values": values,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _distribution_digest(distribution: EnsembleExtremeDistribution) -> str:
    return _evidence_digest(
        model=distribution.provider_model,
        station=distribution.station,
        target_date=distribution.target_date,
        family=distribution.family,
        unit=distribution.unit,
        timezone=distribution.timezone,
        requested_latitude=distribution.requested_latitude,
        requested_longitude=distribution.requested_longitude,
        resolved_latitude=distribution.resolved_latitude,
        resolved_longitude=distribution.resolved_longitude,
        labels=distribution.member_labels,
        values=distribution.member_values,
    )


def parse_open_meteo_gefs_daily_extreme(
    payload: object,
    *,
    station: str,
    target_date: date,
    family: str,
    unit: str,
    timezone: str,
    requested_latitude: float,
    requested_longitude: float,
    received_at: float,
) -> EnsembleExtremeDistribution:
    """Parse exactly one target-day GEFS distribution from the certified schema."""
    station_id = str(station or "").strip().upper()
    if len(station_id) != 4 or not station_id.isalnum():
        raise WeatherForecastError("FORECAST_STATION_INVALID")
    if type(target_date) is not date:
        raise WeatherForecastError("FORECAST_TARGET_DATE_INVALID")
    variable = _daily_variable(family)
    expected_unit = _unit_symbol(unit)
    if not isinstance(timezone, str) or not timezone.strip() or timezone != timezone.strip():
        raise WeatherForecastError("FORECAST_TIMEZONE_INVALID")
    request_lat = _finite(requested_latitude, "FORECAST_REQUEST_COORDINATE_INVALID")
    request_lon = _finite(requested_longitude, "FORECAST_REQUEST_COORDINATE_INVALID")
    if not -90.0 <= request_lat <= 90.0 or not -180.0 <= request_lon <= 180.0:
        raise WeatherForecastError("FORECAST_REQUEST_COORDINATE_INVALID")
    receipt = _finite(received_at, "FORECAST_RECEIPT_TIME_INVALID")
    if receipt < 0.0:
        raise WeatherForecastError("FORECAST_RECEIPT_TIME_INVALID")
    if not isinstance(payload, dict):
        raise WeatherForecastError("FORECAST_ENVELOPE_INVALID")
    if str(payload.get("timezone") or "") != timezone:
        raise WeatherForecastError("FORECAST_TIMEZONE_MISMATCH")

    resolved_lat = _finite(payload.get("latitude"), "FORECAST_RESOLVED_COORDINATE_INVALID")
    resolved_lon = _finite(payload.get("longitude"), "FORECAST_RESOLVED_COORDINATE_INVALID")
    if not -90.0 <= resolved_lat <= 90.0 or not -180.0 <= resolved_lon <= 180.0:
        raise WeatherForecastError("FORECAST_RESOLVED_COORDINATE_INVALID")

    daily = payload.get("daily")
    units = payload.get("daily_units")
    if not isinstance(daily, dict) or not isinstance(units, dict):
        raise WeatherForecastError("FORECAST_DAILY_SCHEMA_INVALID")
    times = daily.get("time")
    if not isinstance(times, list) or not times:
        raise WeatherForecastError("FORECAST_DAILY_TIME_INVALID")
    parsed_dates: list[date] = []
    for raw in times:
        try:
            parsed_dates.append(date.fromisoformat(str(raw)))
        except ValueError:
            raise WeatherForecastError("FORECAST_DAILY_TIME_INVALID")
    if len(set(parsed_dates)) != len(parsed_dates):
        raise WeatherForecastError("FORECAST_DAILY_TIME_DUPLICATE")
    if parsed_dates.count(target_date) != 1:
        raise WeatherForecastError("FORECAST_TARGET_DATE_NOT_UNIQUE")
    target_index = parsed_dates.index(target_date)

    expected_keys = _expected_member_keys(variable)
    actual_data_keys = set(str(key) for key in daily if key != "time")
    if actual_data_keys != set(expected_keys):
        raise WeatherForecastError("FORECAST_MEMBER_SCHEMA_DRIFT")
    if set(str(key) for key in units) != {"time", *expected_keys}:
        raise WeatherForecastError("FORECAST_UNIT_SCHEMA_DRIFT")
    if str(units.get("time") or "") != "iso8601":
        raise WeatherForecastError("FORECAST_TIME_UNIT_INVALID")

    values: list[float] = []
    for key in expected_keys:
        if str(units.get(key) or "") != expected_unit:
            raise WeatherForecastError("FORECAST_TEMPERATURE_UNIT_MISMATCH")
        series = daily.get(key)
        if not isinstance(series, list) or len(series) != len(parsed_dates):
            raise WeatherForecastError("FORECAST_MEMBER_SERIES_LENGTH_MISMATCH")
        values.append(_finite(series[target_index], "FORECAST_MEMBER_VALUE_INVALID"))
    if len(values) != GEFS_TOTAL_MEMBERS:
        raise WeatherForecastError("FORECAST_MEMBER_COUNT_MISMATCH")

    labels = _expected_member_labels()
    member_values = tuple(values)
    digest = _evidence_digest(
        model=OPEN_METEO_GEFS_MODEL,
        station=station_id,
        target_date=target_date,
        family=family,
        unit=unit,
        timezone=timezone,
        requested_latitude=request_lat,
        requested_longitude=request_lon,
        resolved_latitude=resolved_lat,
        resolved_longitude=resolved_lon,
        labels=labels,
        values=member_values,
    )
    return EnsembleExtremeDistribution(
        adapter=FORECAST_ADAPTER_VERSION,
        provider="Open-Meteo Ensemble API",
        provider_model=OPEN_METEO_GEFS_MODEL,
        station=station_id,
        target_date=target_date,
        family=family,
        unit=unit,
        timezone=timezone,
        requested_latitude=request_lat,
        requested_longitude=request_lon,
        resolved_latitude=resolved_lat,
        resolved_longitude=resolved_lon,
        member_labels=labels,
        member_values=member_values,
        received_at=receipt,
        evidence_sha256=digest,
        source_role=FORECAST_ROLE,
        settlement_authority=False,
        calibration_label_authority=False,
        calibrated_probability=False,
        financial_authority=False,
    )


def _quantize_whole_degree(value: float, policy: EnsembleMappingPolicy) -> float:
    if policy.quantization != SUPPORTED_QUANTIZATION:
        raise WeatherForecastError("FORECAST_QUANTIZATION_UNSUPPORTED")
    return float(Decimal(str(value)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _bucket_contains(value: float, bucket: WeatherBucket) -> bool:
    return (bucket.lower is None or value >= bucket.lower) and (bucket.upper is None or value <= bucket.upper)


def map_ensemble_to_contract_buckets(
    compiled: CompiledWeatherEvent,
    distribution: EnsembleExtremeDistribution,
    policy: EnsembleMappingPolicy,
) -> EnsembleBucketForecast:
    """Map a forecast ensemble onto certified whole-degree bucket labels.

    This is research evidence only. The returned frequencies are empirical member
    frequencies under the named mapping policy; they are not calibrated probabilities.
    """
    if not compiled.partition_shape_complete or not compiled.exactly_one_outcome_proven:
        raise WeatherForecastError("FORECAST_CONTRACT_PARTITION_UNPROVEN")
    if compiled.target_date != distribution.target_date:
        raise WeatherForecastError("FORECAST_CONTRACT_DATE_MISMATCH")
    if compiled.family != distribution.family:
        raise WeatherForecastError("FORECAST_CONTRACT_FAMILY_MISMATCH")
    if compiled.unit != distribution.unit:
        raise WeatherForecastError("FORECAST_CONTRACT_UNIT_MISMATCH")
    if not compiled.station_hint or compiled.station_hint.upper() != distribution.station.upper():
        raise WeatherForecastError("FORECAST_CONTRACT_STATION_MISMATCH")
    if distribution.adapter != FORECAST_ADAPTER_VERSION or distribution.provider != "Open-Meteo Ensemble API" or distribution.provider_model != OPEN_METEO_GEFS_MODEL:
        raise WeatherForecastError("FORECAST_ADAPTER_IDENTITY_MISMATCH")
    expected_labels = _expected_member_labels()
    if distribution.member_labels != expected_labels or len(distribution.member_values) != GEFS_TOTAL_MEMBERS:
        raise WeatherForecastError("FORECAST_MEMBER_IDENTITY_MISMATCH")
    if any(not math.isfinite(float(value)) for value in distribution.member_values):
        raise WeatherForecastError("FORECAST_MEMBER_VALUE_INVALID")
    if distribution.evidence_sha256 != _distribution_digest(distribution):
        raise WeatherForecastError("FORECAST_EVIDENCE_DIGEST_MISMATCH")
    if not compiled.buckets or any(not bucket.yes_token or not bucket.no_token for bucket in compiled.buckets):
        raise WeatherForecastError("FORECAST_BUCKET_TOKEN_IDENTITY_INCOMPLETE")

    start_index = 0 if policy.include_control else 1
    selected = distribution.member_values[start_index:]
    if not selected:
        raise WeatherForecastError("FORECAST_NO_SELECTED_MEMBERS")
    hits = [0 for _ in compiled.buckets]
    for raw_value in selected:
        value = _quantize_whole_degree(raw_value, policy)
        matched = [index for index, bucket in enumerate(compiled.buckets) if _bucket_contains(value, bucket)]
        if len(matched) != 1:
            raise WeatherForecastError("FORECAST_MEMBER_BUCKET_MAPPING_NOT_EXACTLY_ONE")
        hits[matched[0]] += 1

    n = len(selected)
    rows: list[BucketEnsembleFrequency] = []
    for bucket, count in zip(compiled.buckets, hits):
        frequency = count / n
        rows.append(BucketEnsembleFrequency(
            market_id=bucket.market_id,
            condition_id=bucket.condition_id,
            yes_token=str(bucket.yes_token),
            no_token=str(bucket.no_token),
            lower=bucket.lower,
            upper=bucket.upper,
            member_hits=count,
            member_count=n,
            raw_member_frequency=frequency,
            raw_no_frequency=1.0 - frequency,
            mapping_policy_id=policy.policy_id,
            calibrated=False,
            financial_authority=False,
        ))
    probability_sum = sum(row.raw_member_frequency for row in rows)
    if abs(probability_sum - 1.0) > 1e-12 or sum(hits) != n:
        raise WeatherForecastError("FORECAST_BUCKET_FREQUENCY_SUM_INVALID")

    return EnsembleBucketForecast(
        adapter=FORECAST_ADAPTER_VERSION,
        event_id=compiled.event_id,
        station=distribution.station,
        target_date=distribution.target_date,
        family=distribution.family,
        unit=distribution.unit,
        provider_model=distribution.provider_model,
        source_evidence_sha256=distribution.evidence_sha256,
        mapping_policy_id=policy.policy_id,
        quantization=policy.quantization,
        included_control=policy.include_control,
        member_count=n,
        bucket_frequencies=tuple(rows),
        probability_sum=probability_sum,
        calibrated=False,
        settlement_authority=False,
        financial_authority=False,
    )


class OpenMeteoGEFSEnsembleClient:
    """Read-only GEFS daily-extreme client with a strict current-schema parser."""

    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2),
            headers={"User-Agent": "polymarket-weather-only-ensemble/0.2 (+https://github.com/heroo2123/Alpha)"},
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def daily_extreme(
        self,
        *,
        station: str,
        latitude: float,
        longitude: float,
        target_date: date,
        family: str,
        unit: str,
        timezone: str,
    ) -> EnsembleExtremeDistribution:
        station_id = str(station or "").strip().upper()
        if len(station_id) != 4 or not station_id.isalnum():
            raise WeatherForecastError("FORECAST_STATION_INVALID")
        if type(target_date) is not date:
            raise WeatherForecastError("FORECAST_TARGET_DATE_INVALID")
        if not isinstance(timezone, str) or not timezone.strip() or timezone != timezone.strip():
            raise WeatherForecastError("FORECAST_TIMEZONE_INVALID")
        variable = _daily_variable(family)
        unit_name = "fahrenheit" if unit == "F" else "celsius" if unit == "C" else None
        if unit_name is None:
            raise WeatherForecastError("FORECAST_UNIT_UNSUPPORTED")
        lat = _finite(latitude, "FORECAST_REQUEST_COORDINATE_INVALID")
        lon = _finite(longitude, "FORECAST_REQUEST_COORDINATE_INVALID")
        if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
            raise WeatherForecastError("FORECAST_REQUEST_COORDINATE_INVALID")
        params = {
            "latitude": lat,
            "longitude": lon,
            "daily": variable,
            "models": OPEN_METEO_GEFS_MODEL,
            "temperature_unit": unit_name,
            "timezone": timezone,
            "start_date": target_date.isoformat(),
            "end_date": target_date.isoformat(),
            "cell_selection": CELL_SELECTION_POLICY,
        }
        try:
            response = await self.http.get(OPEN_METEO_ENSEMBLE, params=params)
        except httpx.TimeoutException:
            raise WeatherForecastError("FORECAST_PROVIDER_TIMEOUT")
        except httpx.RequestError:
            raise WeatherForecastError("FORECAST_PROVIDER_TRANSPORT")
        if response.status_code >= 400:
            raise WeatherForecastError("FORECAST_PROVIDER_HTTP_STATUS")
        received = time.time()
        try:
            payload = response.json()
        except Exception:
            raise WeatherForecastError("FORECAST_PROVIDER_JSON_INVALID")
        return parse_open_meteo_gefs_daily_extreme(
            payload,
            station=station_id,
            target_date=target_date,
            family=family,
            unit=unit,
            timezone=timezone,
            requested_latitude=lat,
            requested_longitude=lon,
            received_at=received,
        )
