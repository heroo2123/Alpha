from __future__ import annotations

"""Forecast integrity helpers for weather LIVE PAPER v4.

The existing Open-Meteo adapter remains the network/parser boundary.  This module
adds the missing decision-time guarantees without inventing unavailable provider
metadata: model-grid proximity is checked, the cache key is bound to the complete
compiled proposition + station metadata + mapping policy, and forecast run age is
explicitly UNKNOWN unless a future provider adapter proves an initialization ID.
"""

import hashlib
import json
import math
import time
from dataclasses import asdict

from .weather_only_contracts import CompiledWeatherEvent
from .weather_only_forecast import EnsembleExtremeDistribution, EnsembleMappingPolicy, WeatherForecastError


FORECAST_V4_POLICY_VERSION = "weather_forecast_integrity_v4_semantic_cache_grid_bound"
MAX_FORECAST_GRID_DISTANCE_KM = 75.0
MAX_FORECAST_RECEIPT_FUTURE_SKEW_SECONDS = 5.0


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _finite(value: object, code: str) -> float:
    if isinstance(value, bool):
        raise WeatherForecastError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherForecastError(code) from None
    if not math.isfinite(number):
        raise WeatherForecastError(code)
    return number


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    values = [float(lat1), float(lon1), float(lat2), float(lon2)]
    if any(not math.isfinite(value) for value in values):
        raise WeatherForecastError("FORECAST_GRID_COORDINATE_NONFINITE")
    phi1, phi2 = math.radians(values[0]), math.radians(values[2])
    dphi = math.radians(values[2] - values[0])
    dlambda = math.radians(values[3] - values[1])
    a = math.sin(dphi / 2.0) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2.0) ** 2
    a = min(1.0, max(0.0, a))
    return 6371.0088 * 2.0 * math.asin(math.sqrt(a))


def validate_distribution_for_station(
    distribution: EnsembleExtremeDistribution,
    *,
    station_latitude: float,
    station_longitude: float,
    now_epoch: float | None = None,
) -> dict:
    """Return explicit provenance or fail closed on a gross grid/receipt mismatch."""
    if not isinstance(distribution, EnsembleExtremeDistribution):
        raise WeatherForecastError("FORECAST_DISTRIBUTION_TYPE_INVALID")
    station_lat = _finite(station_latitude, "FORECAST_STATION_COORDINATE_INVALID")
    station_lon = _finite(station_longitude, "FORECAST_STATION_COORDINATE_INVALID")
    if not -90.0 <= station_lat <= 90.0 or not -180.0 <= station_lon <= 180.0:
        raise WeatherForecastError("FORECAST_STATION_COORDINATE_INVALID")
    if abs(float(distribution.requested_latitude) - station_lat) > 1e-8 or abs(float(distribution.requested_longitude) - station_lon) > 1e-8:
        raise WeatherForecastError("FORECAST_REQUEST_STATION_COORDINATE_MISMATCH")
    distance = haversine_km(
        station_lat,
        station_lon,
        float(distribution.resolved_latitude),
        float(distribution.resolved_longitude),
    )
    if distance > MAX_FORECAST_GRID_DISTANCE_KM:
        raise WeatherForecastError("FORECAST_RESOLVED_GRID_TOO_FAR")
    receipt = _finite(distribution.received_at, "FORECAST_RECEIPT_TIME_INVALID")
    now = time.time() if now_epoch is None else _finite(now_epoch, "FORECAST_NOW_INVALID")
    if receipt <= 0.0 or receipt - now > MAX_FORECAST_RECEIPT_FUTURE_SKEW_SECONDS:
        raise WeatherForecastError("FORECAST_RECEIPT_TIME_INVALID")
    return {
        "version": FORECAST_V4_POLICY_VERSION,
        "provider": distribution.provider,
        "provider_model": distribution.provider_model,
        "adapter": distribution.adapter,
        "station": distribution.station,
        "target_date": distribution.target_date.isoformat(),
        "family": distribution.family,
        "unit": distribution.unit,
        "timezone": distribution.timezone,
        "requested_latitude": float(distribution.requested_latitude),
        "requested_longitude": float(distribution.requested_longitude),
        "resolved_latitude": float(distribution.resolved_latitude),
        "resolved_longitude": float(distribution.resolved_longitude),
        "grid_distance_km": distance,
        "grid_distance_limit_km": MAX_FORECAST_GRID_DISTANCE_KM,
        "received_at": receipt,
        "provider_run_id": None,
        "provider_run_initialized_at": None,
        "provider_run_age_verified": False,
        "provider_run_freshness_claim": "UNKNOWN_PROVIDER_RUN_TIME",
        "member_labels": list(distribution.member_labels),
        "member_values": [float(value) for value in distribution.member_values],
        "source_evidence_sha256": distribution.evidence_sha256,
        "settlement_authority": False,
        "calibrated_probability": False,
        "financial_authority": False,
    }


def semantic_contract_payload(
    compiled: CompiledWeatherEvent,
    *,
    station_metadata,
    mapping_policy: EnsembleMappingPolicy,
) -> dict:
    buckets = [
        {
            "market_id": bucket.market_id,
            "condition_id": bucket.condition_id,
            "question": bucket.question,
            "lower": bucket.lower,
            "upper": bucket.upper,
            "unit": bucket.unit,
            "yes_token": bucket.yes_token,
            "no_token": bucket.no_token,
            "trade_open": bool(bucket.trade_open),
        }
        for bucket in compiled.buckets
    ]
    return {
        "version": FORECAST_V4_POLICY_VERSION,
        "compiler_version": compiled.compiler_version,
        "event_id": compiled.event_id,
        "event_slug": compiled.event_slug,
        "title": compiled.title,
        "family": compiled.family,
        "target_date": compiled.target_date.isoformat() if compiled.target_date else None,
        "unit": compiled.unit,
        "source_family": compiled.source_family,
        "source_urls": list(compiled.source_urls),
        "station_hint": compiled.station_hint,
        "partition_shape_complete": bool(compiled.partition_shape_complete),
        "exactly_one_outcome_proven": bool(compiled.exactly_one_outcome_proven),
        "buckets": buckets,
        "station_metadata": {
            "station": str(getattr(station_metadata, "station", getattr(station_metadata, "station_id", compiled.station_hint)) or ""),
            "latitude": float(getattr(station_metadata, "latitude")),
            "longitude": float(getattr(station_metadata, "longitude")),
            "timezone": str(getattr(station_metadata, "timezone")),
        },
        "mapping_policy": asdict(mapping_policy),
    }


def semantic_digest(
    compiled: CompiledWeatherEvent,
    *,
    station_metadata,
    mapping_policy: EnsembleMappingPolicy,
) -> str:
    return hashlib.sha256(
        _canonical(semantic_contract_payload(compiled, station_metadata=station_metadata, mapping_policy=mapping_policy)).encode("utf-8")
    ).hexdigest()


def forecast_cache_key(
    compiled: CompiledWeatherEvent,
    *,
    station_metadata,
    mapping_policy: EnsembleMappingPolicy,
) -> str:
    return semantic_digest(compiled, station_metadata=station_metadata, mapping_policy=mapping_policy)
