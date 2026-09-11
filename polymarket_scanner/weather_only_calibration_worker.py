from __future__ import annotations

"""Isolated prospective calibration worker for the weather-only research program.

This is deliberately *not* a trading runtime.  Its only responsibilities are to:

1. discover currently open weather events during one preregistered capture window;
2. certify exact NWS/WRH Fahrenheit daily-high/daily-low contract semantics;
3. fetch strict official station location identity plus one GEFS ensemble snapshot;
4. deterministically freeze exactly one raw-model bucket prediction per event;
5. durably reserve that event before collector registration, preventing crash-driven
   forecast replacement or cherry-picking;
6. keep ticking the independent WRH prospective collector so exact settlement labels
   can be authorized later from prospectively bracketed source snapshots.

The capture schedule is part of model identity through the selection-policy id.  V1
freezes the forecast on the UTC day before the target between 17:00 inclusive and
17:15 exclusive.  Captures outside that window are impossible through this runtime.
A missed window is a lost research sample; it is never repaired retrospectively.

No Telegram sender, order endpoint, trading key, CLOB execution path, calibrated
probability promotion or financial authority exists in this module.
"""

import argparse
import asyncio
import hashlib
import json
import math
import os
import sqlite3
import time
from collections import Counter
from datetime import date, datetime, time as wall_time, timedelta, timezone
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .weather_only_calibration_capture import (
    WeatherCalibrationCaptureError,
    _canonical_rule_source_payload,
    _hash_payload as _capture_hash_payload,
    capture_prospective_weather_calibration_candidate,
)
from .weather_only_contracts import (
    DAILY_HIGH,
    DAILY_LOW,
    SOURCE_NWS_WRH,
    compile_weather_event,
)
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


WEATHER_CALIBRATION_WORKER_VERSION = "weather_calibration_worker_v1_fixed_tminus1_1700z_append_reservation"
CAPTURE_POLICY_ID = "weather_gefs_tminus1_1700z_window15m_v1"
MAPPING_POLICY = EnsembleMappingPolicy(
    policy_id="weather_gefs_nearest_whole_include_control_tminus1_1700z_v1",
    include_control=True,
)
SELECTION_POLICY = ProspectiveSelectionPolicy(
    policy_id="weather_gefs_top_bucket_tminus1_1700z_v1",
)
CAPTURE_UTC_HOUR = 17
CAPTURE_UTC_MINUTE = 0
CAPTURE_WINDOW_SECONDS = 15 * 60
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


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _capture_window(now: float) -> tuple[float, float, date]:
    instant = datetime.fromtimestamp(_finite_timestamp(now, "WORKER_CLOCK_INVALID"), timezone.utc)
    start = datetime.combine(
        instant.date(),
        wall_time(CAPTURE_UTC_HOUR, CAPTURE_UTC_MINUTE),
        tzinfo=timezone.utc,
    )
    end = start + timedelta(seconds=CAPTURE_WINDOW_SECONDS)
    return start.timestamp(), end.timestamp(), instant.date() + timedelta(days=1)


def _inside_capture_window(now: float) -> tuple[bool, date, float, float]:
    start, end, target = _capture_window(now)
    return start <= now < end, target, start, end


class WeatherCalibrationWorkerState:
    """Append-style event reservation journal sharing the collector SQLite file.

    The reservation is committed *before* collector registration.  A crash between
    those writes can lose a sample but cannot authorize a second forecast for the
    event.  This is intentionally fail-closed.
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
        total = sum(counts.values())
        return {
            "state_version": "weather_calibration_worker_state_v1_append_reservation",
            "total_events": total,
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
    def _eligible_event(event: dict, target_date: date):
        compiled = compile_weather_event(event)
        if (
            not compiled.event_id
            or compiled.source_family != SOURCE_NWS_WRH
            or compiled.family not in {DAILY_HIGH, DAILY_LOW}
            or compiled.unit != "F"
            or compiled.target_date != target_date
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
        return compiled

    async def _capture_window_cycle(self, *, now: float, expected_target: date) -> dict:
        report = {
            "capture_policy_id": CAPTURE_POLICY_ID,
            "expected_target_date": expected_target.isoformat(),
            "mapping_policy_id": MAPPING_POLICY.policy_id,
            "selection_policy_id": SELECTION_POLICY.policy_id,
            "eligible_events": 0,
            "already_reserved_events": 0,
            "reserved_events": 0,
            "registered_events": 0,
            "errors": {},
        }
        errors = Counter()
        try:
            snapshot = await self.discovery.discover(DEFAULT_TAGS)
        except WeatherDiscoveryError as exc:
            report["errors"] = {f"DISCOVERY:{exc.code}": 1}
            return report
        report["discovery"] = snapshot.summary()

        known = self.state.known_event_ids()
        eligible: list[tuple[str, dict, object]] = []
        for event in snapshot.events:
            compiled = self._eligible_event(event, expected_target)
            if compiled is None:
                continue
            if compiled.event_id in known:
                report["already_reserved_events"] += 1
                continue
            eligible.append((compiled.event_id, event, compiled))
        eligible.sort(key=lambda row: row[0])
        report["eligible_events"] = len(eligible)
        if len(eligible) > MAX_CAPTURE_EVENTS_PER_WINDOW:
            report["errors"] = {"CAPTURE_EVENT_CAP_EXCEEDED": len(eligible)}
            return report

        distribution_cache: dict[tuple[str, date, str, str, str], EnsembleExtremeDistribution] = {}
        for event_id, event, compiled in eligible:
            captured_now = self._now()
            inside, _, _, end = _inside_capture_window(captured_now)
            if not inside:
                errors["CAPTURE_WINDOW_CLOSED_DURING_CYCLE"] += 1
                break
            try:
                metadata = await self._station_metadata(str(compiled.station_hint))
                try:
                    station_zone = ZoneInfo(metadata.timezone)
                except ZoneInfoNotFoundError:
                    raise WeatherCalibrationWorkerError("WORKER_STATION_TIMEZONE_INVALID") from None
                station_local_date = datetime.fromtimestamp(captured_now, station_zone).date()
                if compiled.target_date != station_local_date + timedelta(days=1):
                    errors["TARGET_NOT_NEXT_LOCAL_DAY"] += 1
                    continue

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

                forecast = map_ensemble_to_contract_buckets(
                    compiled,
                    distribution,
                    MAPPING_POLICY,
                )
                capture_time = self._now()
                if capture_time < distribution.received_at:
                    raise WeatherCalibrationWorkerError("WORKER_CAPTURE_BEFORE_FORECAST_RECEIPT")
                if not (now <= capture_time < end):
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
                except (WeatherWRHCollectorError, Exception) as exc:
                    # The event remains reserved even on failure, deliberately
                    # preventing a second forecast from replacing the chosen one.
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
                code = getattr(exc, "code", type(exc).__name__)
                errors[str(code)] += 1
                continue

        report["errors"] = dict(sorted(errors.items()))
        return report

    async def run_cycle(self) -> dict:
        started = self._now()
        active, expected_target, window_start, window_end = _inside_capture_window(started)
        report: dict = {
            "version": WEATHER_CALIBRATION_WORKER_VERSION,
            "mode": "PROSPECTIVE_CALIBRATION_RESEARCH_ONLY",
            "capture_policy_id": CAPTURE_POLICY_ID,
            "capture_window_active": active,
            "capture_window_start_utc": datetime.fromtimestamp(window_start, timezone.utc).isoformat(),
            "capture_window_end_utc": datetime.fromtimestamp(window_end, timezone.utc).isoformat(),
            "expected_target_date": expected_target.isoformat(),
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

        if active:
            report["capture"] = await self._capture_window_cycle(
                now=started,
                expected_target=expected_target,
            )
        else:
            report["capture"] = {
                "capture_policy_id": CAPTURE_POLICY_ID,
                "attempted": False,
                "reason": "OUTSIDE_PREREGISTERED_CAPTURE_WINDOW",
            }

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
