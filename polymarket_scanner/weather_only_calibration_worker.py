from __future__ import annotations

"""Isolated prospective calibration worker for the weather-only research program.

This is deliberately *not* a trading runtime. Its only responsibilities are to
prospectively freeze one raw GEFS bucket prediction per event and later collect exact
WRH settlement labels through the independent trusted collector.

V3 preregisters one comparable forecast horizon across settlement stations: capture
must occur from 17:00 inclusive to 17:15 exclusive in the exact NWS station timezone
on the calendar day before the target date. The schedule is encoded in the immutable
mapping/selection policy ids that feed model identity. A missed window is a lost
research sample and is never repaired retrospectively.

Discovery is cached and attempt-throttled independently from the 30-second collector
loop. The worker may inspect public market/station metadata outside a capture window,
but it cannot fetch GEFS or freeze a prediction until the station-local window opens.

No Telegram sender, order endpoint, trading key, CLOB execution path, calibrated
probability promotion or financial authority exists in this module.
"""

import argparse
import asyncio
import json
import math
import os
import sqlite3
import time
from collections import Counter
from datetime import date, datetime, time as wall_time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .weather_only_calibration_capture import (
    WeatherCalibrationCaptureError,
    _canonical_rule_source_payload,
    _hash_payload as _capture_hash_payload,
    capture_prospective_weather_calibration_candidate,
)
from .weather_only_contracts import DAILY_HIGH, DAILY_LOW, SOURCE_NWS_WRH, compile_weather_event
from .weather_only_discovery import DEFAULT_TAGS, WeatherDiscoveryError, WeatherOnlyDiscovery
from .weather_only_forecast import (
    EnsembleBucketForecast,
    EnsembleExtremeDistribution,
    EnsembleMappingPolicy,
    OpenMeteoGEFSEnsembleClient,
    WeatherForecastError,
    map_ensemble_to_contract_buckets,
)
from .weather_only_predictions import ProspectiveSelectionPolicy, WeatherPredictionError
from .weather_only_rules import apply_rule_authority, compile_temperature_rule_authority
from .weather_only_station_metadata import (
    NWSStationMetadata,
    NWSStationMetadataClient,
    WeatherStationMetadataError,
)
from .weather_only_wrh_collector import CollectorTickReport, WeatherWRHCollectorError
from .weather_only_wrh_collector_authority import TrustedWeatherWRHProspectiveCollector


WEATHER_CALIBRATION_WORKER_VERSION = "weather_calibration_worker_v3_station_local_tminus1_1700_append_reservation"
CAPTURE_POLICY_ID = "weather_gefs_station_local_tminus1_1700_window15m_v1"
MAPPING_POLICY = EnsembleMappingPolicy(
    policy_id="weather_gefs_nearest_whole_include_control_station_local_tminus1_1700_v1",
    include_control=True,
)
SELECTION_POLICY = ProspectiveSelectionPolicy(
    policy_id="weather_gefs_top_bucket_station_local_tminus1_1700_v1",
)
CAPTURE_LOCAL_HOUR = 17
CAPTURE_LOCAL_MINUTE = 0
CAPTURE_WINDOW_SECONDS = 15 * 60
DISCOVERY_REFRESH_SECONDS = 5 * 60.0
LOOP_INTERVAL_SECONDS = 30.0
MIN_LOOP_INTERVAL_SECONDS = 5.0
MAX_CAPTURE_EVENTS_PER_WINDOW = 32

STATE_RESERVED = "RESERVED"
STATE_REGISTERED = "REGISTERED"
STATE_FAILED = "FAILED"


class WeatherCalibrationWorkerError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite_timestamp(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherCalibrationWorkerError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherCalibrationWorkerError(code)
    return number


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _station_capture_window(target_date: date, timezone_name: str) -> tuple[float, float]:
    if type(target_date) is not date:
        raise WeatherCalibrationWorkerError("WORKER_TARGET_DATE_INVALID")
    try:
        zone = ZoneInfo(str(timezone_name or ""))
    except ZoneInfoNotFoundError:
        raise WeatherCalibrationWorkerError("WORKER_STATION_TIMEZONE_INVALID") from None
    start = datetime.combine(
        target_date - timedelta(days=1),
        wall_time(CAPTURE_LOCAL_HOUR, CAPTURE_LOCAL_MINUTE),
        tzinfo=zone,
    )
    end = start + timedelta(seconds=CAPTURE_WINDOW_SECONDS)
    return start.timestamp(), end.timestamp()


def _station_capture_status(now: float, target_date: date, timezone_name: str) -> tuple[str, float, float]:
    current = _finite_timestamp(now, "WORKER_CLOCK_INVALID")
    start, end = _station_capture_window(target_date, timezone_name)
    if current < start:
        return "BEFORE", start, end
    if current >= end:
        return "AFTER", start, end
    return "ACTIVE", start, end


class WeatherCalibrationWorkerState:
    """Append-style event reservation journal sharing the collector SQLite file.

    The reservation is committed before collector registration. A crash between
    those writes can lose a sample but cannot authorize a second forecast for the
    event. This is intentionally fail-closed.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.db = sqlite3.connect(self.path, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS weather_calibration_worker_events (
                event_id TEXT PRIMARY KEY,
                capture_policy_id TEXT NOT NULL,
                capture_evidence_sha256 TEXT NOT NULL,
                prediction_evidence_sha256 TEXT NOT NULL,
                model_version TEXT NOT NULL,
                station TEXT NOT NULL,
                target_date TEXT NOT NULL,
                family TEXT NOT NULL,
                captured_at REAL NOT NULL,
                reserved_at REAL NOT NULL,
                status TEXT NOT NULL,
                registration_status TEXT,
                failure_code TEXT,
                station_metadata_evidence_sha256 TEXT NOT NULL,
                forecast_source_evidence_sha256 TEXT NOT NULL,
                rule_source_sha256 TEXT NOT NULL,
                station_metadata_json TEXT NOT NULL,
                distribution_json TEXT NOT NULL,
                forecast_json TEXT NOT NULL,
                rule_source_json TEXT NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_weather_worker_status
                ON weather_calibration_worker_events(status, target_date);
            """
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def known_event_ids(self) -> set[str]:
        return {
            str(row[0])
            for row in self.db.execute("SELECT event_id FROM weather_calibration_worker_events")
        }

    def reserve(
        self,
        *,
        event: dict,
        capture,
        station_metadata: NWSStationMetadata,
        distribution: EnsembleExtremeDistribution,
        forecast: EnsembleBucketForecast,
        reserved_at: float,
    ) -> bool:
        event_id = str(capture.prediction.event_id).strip()
        if not event_id:
            raise WeatherCalibrationWorkerError("WORKER_EVENT_ID_MISSING")
        rule_source = _canonical_rule_source_payload(event)
        rule_source_json = _canonical_json(rule_source)
        rule_source_sha = _capture_hash_payload(rule_source)
        if rule_source_sha != capture.rule_evidence.source_rules_sha256:
            raise WeatherCalibrationWorkerError("WORKER_RULE_SOURCE_DIGEST_MISMATCH")
        station_json = _canonical_json(station_metadata.as_dict())
        distribution_json = _canonical_json(distribution.as_dict())
        forecast_json = _canonical_json(forecast.as_dict())

        with self.db:
            existing = self.db.execute(
                "SELECT capture_evidence_sha256 FROM weather_calibration_worker_events WHERE event_id = ?",
                (event_id,),
            ).fetchone()
            if existing is not None:
                return False
            self.db.execute(
                """
                INSERT INTO weather_calibration_worker_events (
                    event_id, capture_policy_id, capture_evidence_sha256,
                    prediction_evidence_sha256, model_version, station, target_date,
                    family, captured_at, reserved_at, status, registration_status,
                    failure_code, station_metadata_evidence_sha256,
                    forecast_source_evidence_sha256, rule_source_sha256,
                    station_metadata_json, distribution_json, forecast_json,
                    rule_source_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id,
                    CAPTURE_POLICY_ID,
                    capture.capture_evidence_sha256,
                    capture.prediction.prediction_evidence_sha256,
                    capture.prediction.model_version,
                    capture.prediction.station,
                    capture.prediction.target_date.isoformat(),
                    capture.prediction.family,
                    capture.captured_at,
                    reserved_at,
                    STATE_RESERVED,
                    station_metadata.evidence_sha256,
                    distribution.evidence_sha256,
                    capture.rule_evidence.source_rules_sha256,
                    station_json,
                    distribution_json,
                    forecast_json,
                    rule_source_json,
                    reserved_at,
                ),
            )
        return True

    def mark_registered(self, event_id: str, registration_status: str, now: float) -> None:
        with self.db:
            changed = self.db.execute(
                """
                UPDATE weather_calibration_worker_events
                SET status = ?, registration_status = ?, updated_at = ?
                WHERE event_id = ? AND status = ?
                """,
                (STATE_REGISTERED, str(registration_status), now, event_id, STATE_RESERVED),
            ).rowcount
        if changed != 1:
            raise WeatherCalibrationWorkerError("WORKER_RESERVATION_NOT_PENDING")

    def mark_failed(self, event_id: str, code: str, now: float) -> None:
        with self.db:
            self.db.execute(
                """
                UPDATE weather_calibration_worker_events
                SET status = ?, failure_code = ?, updated_at = ?
                WHERE event_id = ? AND status = ?
                """,
                (STATE_FAILED, str(code), now, event_id, STATE_RESERVED),
            )

    def reconcile_registered_digests(self, diagnostic_rows: list[dict], now: float) -> int:
        present = {
            str(row.get("capture_evidence_sha256") or ""): str(row.get("status") or "")
            for row in diagnostic_rows
            if str(row.get("capture_evidence_sha256") or "")
        }
        reconciled = 0
        rows = list(self.db.execute(
            """
            SELECT event_id, capture_evidence_sha256
            FROM weather_calibration_worker_events
            WHERE status = ?
            """,
            (STATE_RESERVED,),
        ).fetchall())
        with self.db:
            for row in rows:
                digest = str(row["capture_evidence_sha256"])
                if digest not in present:
                    continue
                self.db.execute(
                    """
                    UPDATE weather_calibration_worker_events
                    SET status = ?, registration_status = ?, updated_at = ?
                    WHERE event_id = ? AND status = ?
                    """,
                    (STATE_REGISTERED, present[digest], now, row["event_id"], STATE_RESERVED),
                )
                reconciled += 1
        return reconciled

    def summary(self) -> dict:
        counts = {
            str(row["status"]): int(row["n"])
            for row in self.db.execute(
                "SELECT status, COUNT(*) AS n FROM weather_calibration_worker_events GROUP BY status"
            ).fetchall()
        }
        return {
            "state_version": "weather_calibration_worker_state_v1_append_reservation",
            "total_events": sum(counts.values()),
            "status_counts": dict(sorted(counts.items())),
            "financial_authority": False,
        }


class WeatherCalibrationResearchWorker:
    financial_authority = False
    financial_delivery = False
    automatic_order_placement = False

    def __init__(
        self,
        *,
        db_path: str | Path,
        discovery: WeatherOnlyDiscovery | None = None,
        station_client: NWSStationMetadataClient | None = None,
        forecast_client: OpenMeteoGEFSEnsembleClient | None = None,
        collector: TrustedWeatherWRHProspectiveCollector | None = None,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.discovery = discovery or WeatherOnlyDiscovery()
        self.station_client = station_client or NWSStationMetadataClient()
        self.forecast_client = forecast_client or OpenMeteoGEFSEnsembleClient()
        self.collector = collector or TrustedWeatherWRHProspectiveCollector(db_path=self.db_path)
        self.state = WeatherCalibrationWorkerState(self.db_path)
        self._owns_discovery = discovery is None
        self._owns_station = station_client is None
        self._owns_forecast = forecast_client is None
        self._owns_collector = collector is None
        self._clock = clock or time.time
        if not callable(self._clock):
            raise TypeError("clock must be callable")
        self._last_clock: float | None = None
        self._station_cache: dict[str, NWSStationMetadata] = {}
        self._event_cache: tuple[dict, ...] = ()
        self._last_discovery_attempt_at: float | None = None

    def _now(self) -> float:
        raw = self._clock()
        value = _finite_timestamp(raw, "WORKER_CLOCK_INVALID")
        if self._last_clock is not None and value < self._last_clock:
            raise WeatherCalibrationWorkerError("WORKER_CLOCK_REGRESSION")
        self._last_clock = value
        return value

    async def close(self) -> None:
        self.state.close()
        if self._owns_collector:
            self.collector.close()
        if self._owns_discovery:
            await self.discovery.close()
        if self._owns_station:
            await self.station_client.close()
        if self._owns_forecast:
            await self.forecast_client.close()

    async def _station_metadata(self, station: str) -> NWSStationMetadata:
        station_id = str(station or "").strip().upper()
        cached = self._station_cache.get(station_id)
        if cached is not None:
            return cached
        row = await self.station_client.station(station_id)
        self._station_cache[station_id] = row
        return row

    @staticmethod
    def _eligible_event(event: dict):
        compiled = compile_weather_event(event)
        if (
            not compiled.event_id
            or compiled.source_family != SOURCE_NWS_WRH
            or compiled.family not in {DAILY_HIGH, DAILY_LOW}
            or compiled.unit != "F"
            or compiled.target_date is None
            or not compiled.station_hint
            or not compiled.partition_shape_complete
            or not compiled.shadow_supported
        ):
            return None
        authority = compile_temperature_rule_authority(event, compiled)
        certified = apply_rule_authority(compiled, authority)
        if not authority.rule_semantics_proven or not authority.exactly_one_outcome_proven:
            return None
        if not certified.exactly_one_outcome_proven or certified.financial_authority:
            return None
        return certified

    async def _refresh_discovery(self, now: float) -> dict:
        previous = self._last_discovery_attempt_at
        if previous is not None and now - previous < DISCOVERY_REFRESH_SECONDS:
            return {
                "attempted": False,
                "reason": "DISCOVERY_ATTEMPT_THROTTLED",
                "cached_event_count": len(self._event_cache),
            }
        self._last_discovery_attempt_at = now
        try:
            snapshot = await self.discovery.discover(DEFAULT_TAGS)
        except WeatherDiscoveryError as exc:
            return {
                "attempted": True,
                "success": False,
                "error": f"DISCOVERY:{exc.code}",
                "cached_event_count": len(self._event_cache),
            }
        self._event_cache = tuple(snapshot.events)
        return {
            "attempted": True,
            "success": True,
            "cached_event_count": len(self._event_cache),
            "summary": snapshot.summary(),
        }

    async def _capture_cycle(self, *, now: float) -> dict:
        report = {
            "capture_policy_id": CAPTURE_POLICY_ID,
            "mapping_policy_id": MAPPING_POLICY.policy_id,
            "selection_policy_id": SELECTION_POLICY.policy_id,
            "eligible_events": 0,
            "active_window_events": 0,
            "before_window_events": 0,
            "missed_window_events": 0,
            "already_reserved_events": 0,
            "reserved_events": 0,
            "registered_events": 0,
            "errors": {},
        }
        errors = Counter()
        known = self.state.known_event_ids()
        active: list[tuple[str, dict, object, NWSStationMetadata, float]] = []

        for event in self._event_cache:
            compiled = self._eligible_event(event)
            if compiled is None:
                continue
            report["eligible_events"] += 1
            if compiled.event_id in known:
                report["already_reserved_events"] += 1
                continue
            try:
                metadata = await self._station_metadata(str(compiled.station_hint))
                status, start, end = _station_capture_status(
                    now,
                    compiled.target_date,
                    metadata.timezone,
                )
            except (WeatherStationMetadataError, WeatherCalibrationWorkerError) as exc:
                errors[getattr(exc, "code", type(exc).__name__)] += 1
                continue
            if status == "BEFORE":
                report["before_window_events"] += 1
                continue
            if status == "AFTER":
                report["missed_window_events"] += 1
                continue
            report["active_window_events"] += 1
            active.append((compiled.event_id, event, compiled, metadata, end))

        active.sort(key=lambda row: row[0])
        if len(active) > MAX_CAPTURE_EVENTS_PER_WINDOW:
            report["errors"] = {"CAPTURE_EVENT_CAP_EXCEEDED": len(active)}
            return report

        distribution_cache: dict[tuple[str, date, str, str, str], EnsembleExtremeDistribution] = {}
        for event_id, event, compiled, metadata, window_end in active:
            try:
                cache_key = (
                    metadata.station,
                    compiled.target_date,
                    compiled.family,
                    compiled.unit,
                    metadata.evidence_sha256,
                )
                distribution = distribution_cache.get(cache_key)
                if distribution is None:
                    distribution = await self.forecast_client.daily_extreme(
                        station=metadata.station,
                        latitude=metadata.latitude,
                        longitude=metadata.longitude,
                        target_date=compiled.target_date,
                        family=compiled.family,
                        unit=compiled.unit,
                        timezone=metadata.timezone,
                    )
                    distribution_cache[cache_key] = distribution

                forecast = map_ensemble_to_contract_buckets(compiled, distribution, MAPPING_POLICY)
                capture_time = self._now()
                if capture_time < distribution.received_at:
                    raise WeatherCalibrationWorkerError("WORKER_CAPTURE_BEFORE_FORECAST_RECEIPT")
                status, _, recomputed_end = _station_capture_status(
                    capture_time,
                    compiled.target_date,
                    metadata.timezone,
                )
                if status != "ACTIVE" or abs(recomputed_end - window_end) > 1e-9:
                    errors["CAPTURE_WINDOW_CLOSED_BEFORE_FREEZE"] += 1
                    continue

                capture = capture_prospective_weather_calibration_candidate(
                    event,
                    forecast,
                    selection_policy=SELECTION_POLICY,
                    captured_at=capture_time,
                )
                reserved_at = self._now()
                if reserved_at - capture_time > 120.0:
                    raise WeatherCalibrationWorkerError("WORKER_RESERVATION_DELAY_EXCEEDED")
                if not self.state.reserve(
                    event=event,
                    capture=capture,
                    station_metadata=metadata,
                    distribution=distribution,
                    forecast=forecast,
                    reserved_at=reserved_at,
                ):
                    report["already_reserved_events"] += 1
                    continue
                report["reserved_events"] += 1
                try:
                    registration = self.collector.register_capture(capture)
                except Exception as exc:
                    code = getattr(exc, "code", type(exc).__name__)
                    self.state.mark_failed(event_id, f"COLLECTOR_REGISTER:{code}", self._now())
                    errors[f"COLLECTOR_REGISTER:{code}"] += 1
                    continue
                self.state.mark_registered(event_id, str(registration), self._now())
                report["registered_events"] += 1
            except (
                WeatherStationMetadataError,
                WeatherForecastError,
                WeatherPredictionError,
                WeatherCalibrationCaptureError,
                WeatherCalibrationWorkerError,
            ) as exc:
                errors[getattr(exc, "code", type(exc).__name__)] += 1
                continue

        report["errors"] = dict(sorted(errors.items()))
        return report

    async def run_cycle(self) -> dict:
        started = self._now()
        report: dict = {
            "version": WEATHER_CALIBRATION_WORKER_VERSION,
            "mode": "PROSPECTIVE_CALIBRATION_RESEARCH_ONLY",
            "capture_policy_id": CAPTURE_POLICY_ID,
            "financial_authority": False,
            "financial_delivery": False,
            "automatic_order_placement": False,
            "telegram_delivery": False,
            "started_at": started,
        }

        try:
            reconciled = self.state.reconcile_registered_digests(
                self.collector.diagnostic_status(),
                started,
            )
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            report["state_reconciliation_error"] = str(code)
            reconciled = 0
        report["reconciled_reservations"] = reconciled
        report["discovery"] = await self._refresh_discovery(started)
        report["capture"] = await self._capture_cycle(now=started)

        try:
            collector_report: CollectorTickReport = await asyncio.to_thread(self.collector.tick)
            report["collector"] = collector_report.as_dict()
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            report["collector"] = {
                "error": str(code),
                "financial_authority": False,
                "financial_delivery": False,
                "automatic_order_placement": False,
            }

        report["state"] = self.state.summary()
        report["finished_at"] = self._now()
        report["cycle_ok"] = not bool(report.get("state_reconciliation_error"))
        return report


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(text + "\n")
    os.replace(temporary, path)


async def _main_async(args) -> int:
    worker = WeatherCalibrationResearchWorker(db_path=args.db)
    try:
        interval = max(MIN_LOOP_INTERVAL_SECONDS, float(args.interval_seconds))
        while True:
            report = await worker.run_cycle()
            payload = json.dumps(report, indent=2, sort_keys=True)
            if args.output:
                _atomic_write(args.output, payload)
            print(payload, flush=True)
            if not args.loop:
                return 0 if report.get("cycle_ok") else 2
            elapsed = max(0.0, float(report["finished_at"]) - float(report["started_at"]))
            await asyncio.sleep(max(0.0, interval - elapsed))
    finally:
        await worker.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(os.environ.get("WEATHER_CALIBRATION_DB", "data/weather-calibration.sqlite")),
    )
    parser.add_argument("--loop", action="store_true")
    parser.add_argument("--interval-seconds", type=float, default=LOOP_INTERVAL_SECONDS)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_main_async(args)))


if __name__ == "__main__":
    main()
