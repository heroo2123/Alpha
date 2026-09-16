from __future__ import annotations

"""Read-only live preflight for the prospective weather calibration worker.

This diagnostic exercises the worker's *actual* event gate and station-local capture
window logic against current Gamma events without registering captures or mutating a
database. Every eligible Fahrenheit station is resolved through the production
station-metadata client. GEFS is sampled deterministically across distinct still-
prospective stations and mapped onto the exact current Polymarket bucket partition.

The preflight never calls CLOB, never calls WRH settlement observations, never writes
worker/collector state, never sends Telegram and never grants calibration or financial
authority. Its purpose is operational coverage only.
"""

import argparse
import asyncio
import json
import math
import time
from collections import Counter
from pathlib import Path

from .weather_only_calibration_worker import (
    MAPPING_POLICY,
    WeatherCalibrationResearchWorker,
    _station_capture_status,
)
from .weather_only_discovery import DEFAULT_TAGS, WeatherOnlyDiscovery
from .weather_only_forecast import (
    GEFS_TOTAL_MEMBERS,
    OpenMeteoGEFSEnsembleClient,
    WeatherForecastError,
    map_ensemble_to_contract_buckets,
)
from .weather_only_station_metadata import NWSStationMetadataClient, WeatherStationMetadataError


WEATHER_CALIBRATION_LIVE_PREFLIGHT_VERSION = "weather_calibration_live_preflight_v1_worker_gate_station_windows_gefs_sample"
MAX_ELIGIBLE_EVENTS = 256
MAX_STATIONS = 32
MAX_FORECAST_PROBES = 12


class WeatherCalibrationLivePreflightError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _safe_adapter(metadata: object) -> str:
    return str(getattr(metadata, "adapter", "UNKNOWN"))


def _authority_boundary_false(value: object) -> bool:
    return all(
        getattr(value, name, False) is False
        for name in (
            "settlement_authority",
            "calibration_label_authority",
            "financial_authority",
        )
    )


async def run_live_preflight(
    *,
    discovery: WeatherOnlyDiscovery | None = None,
    station_client: NWSStationMetadataClient | None = None,
    forecast_client: OpenMeteoGEFSEnsembleClient | None = None,
    now: float | None = None,
    max_forecast_probes: int = MAX_FORECAST_PROBES,
) -> dict:
    if isinstance(max_forecast_probes, bool) or not isinstance(max_forecast_probes, int):
        raise ValueError("max_forecast_probes must be an integer")
    if not 0 <= max_forecast_probes <= MAX_FORECAST_PROBES:
        raise ValueError(f"max_forecast_probes must be in 0..{MAX_FORECAST_PROBES}")

    owned_discovery = discovery is None
    owned_station = station_client is None
    owned_forecast = forecast_client is None
    discovery = discovery or WeatherOnlyDiscovery()
    station_client = station_client or NWSStationMetadataClient()
    forecast_client = forecast_client or OpenMeteoGEFSEnsembleClient()
    decision_time = time.time() if now is None else float(now)
    if not math.isfinite(decision_time) or decision_time < 0.0:
        raise WeatherCalibrationLivePreflightError("PREFLIGHT_CLOCK_INVALID")

    errors = Counter()
    metadata_adapters = Counter()
    metadata_by_station: dict[str, object] = {}
    eligible: list[tuple[dict, object]] = []
    window_rows: list[tuple[dict, object, object, str, float, float]] = []

    try:
        snapshot = await discovery.discover(DEFAULT_TAGS)
        for event in snapshot.events:
            compiled = WeatherCalibrationResearchWorker._eligible_event(event)
            if compiled is not None:
                eligible.append((event, compiled))
        if len(eligible) > MAX_ELIGIBLE_EVENTS:
            raise WeatherCalibrationLivePreflightError("PREFLIGHT_ELIGIBLE_EVENT_CAP")

        stations = sorted({str(compiled.station_hint).upper() for _, compiled in eligible})
        if len(stations) > MAX_STATIONS:
            raise WeatherCalibrationLivePreflightError("PREFLIGHT_STATION_CAP")

        for station in stations:
            try:
                metadata = await station_client.station(station)
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                errors[f"STATION:{station}:{code}"] += 1
                continue
            if str(getattr(metadata, "station", "")).strip().upper() != station:
                errors[f"STATION:{station}:IDENTITY_MISMATCH"] += 1
                continue
            if not _authority_boundary_false(metadata):
                errors[f"STATION:{station}:AUTHORITY_BOUNDARY"] += 1
                continue
            metadata_by_station[station] = metadata
            metadata_adapters[_safe_adapter(metadata)] += 1

        statuses = Counter()
        for event, compiled in eligible:
            station = str(compiled.station_hint).upper()
            metadata = metadata_by_station.get(station)
            if metadata is None:
                continue
            try:
                status, start, end = _station_capture_status(
                    decision_time,
                    compiled.target_date,
                    str(getattr(metadata, "timezone", "")),
                )
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                errors[f"WINDOW:{compiled.event_id}:{code}"] += 1
                continue
            statuses[status] += 1
            window_rows.append((event, compiled, metadata, status, start, end))

        still_prospective = [row for row in window_rows if row[3] in {"BEFORE", "ACTIVE"}]
        # Cover as many distinct stations as possible before taking second samples.
        ordered = sorted(
            still_prospective,
            key=lambda row: (
                row[1].target_date,
                str(row[1].station_hint),
                row[1].family,
                row[1].event_id,
            ),
        )
        selected = []
        selected_ids: set[str] = set()
        seen_stations: set[str] = set()
        for row in ordered:
            station = str(row[1].station_hint).upper()
            if station in seen_stations:
                continue
            selected.append(row)
            selected_ids.add(row[1].event_id)
            seen_stations.add(station)
            if len(selected) >= max_forecast_probes:
                break
        if len(selected) < max_forecast_probes:
            for row in ordered:
                if row[1].event_id in selected_ids:
                    continue
                selected.append(row)
                selected_ids.add(row[1].event_id)
                if len(selected) >= max_forecast_probes:
                    break

        probe_rows: list[dict] = []
        for event, compiled, metadata, status, start, end in selected:
            try:
                distribution = await forecast_client.daily_extreme(
                    station=str(getattr(metadata, "station")),
                    latitude=float(getattr(metadata, "latitude")),
                    longitude=float(getattr(metadata, "longitude")),
                    target_date=compiled.target_date,
                    family=compiled.family,
                    unit=compiled.unit,
                    timezone=str(getattr(metadata, "timezone")),
                )
                forecast = map_ensemble_to_contract_buckets(compiled, distribution, MAPPING_POLICY)
                if len(distribution.member_values) != GEFS_TOTAL_MEMBERS:
                    raise WeatherCalibrationLivePreflightError("PREFLIGHT_GEFS_MEMBER_COUNT")
                if forecast.member_count != GEFS_TOTAL_MEMBERS:
                    raise WeatherCalibrationLivePreflightError("PREFLIGHT_FORECAST_MEMBER_COUNT")
                if abs(float(forecast.probability_sum) - 1.0) > 1e-12:
                    raise WeatherCalibrationLivePreflightError("PREFLIGHT_FORECAST_PROBABILITY_SUM")
                if (
                    distribution.settlement_authority
                    or distribution.calibration_label_authority
                    or distribution.calibrated_probability
                    or distribution.financial_authority
                    or forecast.settlement_authority
                    or forecast.calibrated
                    or forecast.financial_authority
                ):
                    raise WeatherCalibrationLivePreflightError("PREFLIGHT_FORECAST_AUTHORITY_BOUNDARY")
                probe_rows.append({
                    "event_id": compiled.event_id,
                    "station": str(compiled.station_hint).upper(),
                    "family": compiled.family,
                    "target_date": compiled.target_date.isoformat(),
                    "window_status": status,
                    "window_start": start,
                    "window_end": end,
                    "metadata_adapter": _safe_adapter(metadata),
                    "forecast_adapter": forecast.adapter,
                    "member_count": forecast.member_count,
                    "bucket_count": len(forecast.bucket_frequencies),
                    "mapping_policy_id": forecast.mapping_policy_id,
                    "source_evidence_sha256_present": len(forecast.source_evidence_sha256) == 64,
                    "financial_authority": False,
                })
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                errors[f"FORECAST:{compiled.event_id}:{code}"] += 1

        return {
            "version": WEATHER_CALIBRATION_LIVE_PREFLIGHT_VERSION,
            "decision_time": decision_time,
            "read_only": True,
            "database_mutation": False,
            "collector_registration": False,
            "wrh_settlement_requests": False,
            "clob_requests": False,
            "telegram_delivery": False,
            "automatic_order_placement": False,
            "financial_authority": False,
            "discovery": snapshot.summary(),
            "eligible_fahrenheit_event_count": len(eligible),
            "eligible_station_count": len(stations),
            "metadata_resolved_station_count": len(metadata_by_station),
            "metadata_adapter_counts": dict(sorted(metadata_adapters.items())),
            "window_status_counts": dict(sorted(statuses.items())),
            "still_prospective_event_count": len(still_prospective),
            "forecast_probe_limit": max_forecast_probes,
            "forecast_probe_attempt_count": len(selected),
            "forecast_probe_success_count": len(probe_rows),
            "forecast_probes": probe_rows,
            "errors": dict(sorted(errors.items())),
            "preflight_ok": not errors and len(metadata_by_station) == len(stations) and len(probe_rows) == len(selected),
        }
    finally:
        if owned_discovery:
            await discovery.close()
        if owned_station:
            await station_client.close()
        if owned_forecast:
            await forecast_client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--max-forecast-probes", type=int, default=MAX_FORECAST_PROBES)
    args = parser.parse_args()
    report = asyncio.run(run_live_preflight(max_forecast_probes=args.max_forecast_probes))
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(payload + "\n")
    print(payload)
    raise SystemExit(0 if report.get("preflight_ok") else 2)


if __name__ == "__main__":
    main()
