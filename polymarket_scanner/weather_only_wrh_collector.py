from __future__ import annotations

"""Durable prospective WRH cutoff collector for weather-only calibration.

This service is intentionally separate from the trading runtime.  It accepts only
freshly registered prospective forecast/rule captures, keeps one bounded WRH snapshot
stream per station/date, and attempts exact settlement labeling only when the first
following-date Hourly Data row is prospectively bracketed.

The collector never repairs a missed cutoff from later history.  It never stores the
NWS browser credential or raw Synoptic payload and never grants financial/trading
authority.  Successful rows are authorized only through the strict full-lineage
calibration authority gate.
"""

import json
import math
import sqlite3
import time
from dataclasses import dataclass
from datetime import date, datetime, time as wall_time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .weather_only_calibration_authority import (
    AuthorizedWRHCalibrationSample,
    WeatherCalibrationAuthorityError,
    authorize_wrh_calibration_sample,
)
from .weather_only_calibration_capture import (
    FrozenRuleBucket,
    ProspectiveNWSRuleEvidence,
    ProspectiveWeatherCalibrationCapture,
    WeatherCalibrationCaptureError,
    build_wrh_exact_settlement_evidence,
    _validate_capture,
)
from .weather_only_predictions import ProspectiveBucketPrediction
from .weather_only_wrh import (
    WRHHourlyRow,
    WRHSourceError,
    WRHSourceSnapshot,
)
from .weather_only_wrh_finality import _validate_snapshot
from .weather_only_wrh_client import NWSWRHLiveClient


WRH_PROSPECTIVE_COLLECTOR_VERSION = "weather_wrh_prospective_collector_v1_sqlite_fail_closed"
WRH_PROSPECTIVE_COLLECTOR_POLICY_ID = "wrh_collector_v1_reg120_poll30_window2355_0210_cap512"
REGISTRATION_MAX_LAG_SECONDS = 120.0
POLL_INTERVAL_SECONDS = 30.0
ACTIVE_START_HOUR = 23
ACTIVE_START_MINUTE = 55
ACTIVE_END_HOUR = 2
ACTIVE_END_MINUTE = 10
BOOTSTRAP_UTC_HOUR = 12
MAX_SNAPSHOTS_PER_STATION_DATE = 512

CAPTURE_PENDING = "PENDING"
CAPTURE_AUTHORIZED = "AUTHORIZED"
CAPTURE_FAILED = "FAILED"
KEY_ACTIVE = "ACTIVE"
KEY_COMPLETE = "COMPLETE"


class WeatherWRHCollectorError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _now() -> float:
    return time.time()


def _finite_timestamp(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherWRHCollectorError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherWRHCollectorError(code)
    return number


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _date_from_iso(value: object, code: str) -> date:
    if not isinstance(value, str):
        raise WeatherWRHCollectorError(code)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise WeatherWRHCollectorError(code) from None
    return parsed


def _datetime_from_iso(value: object, code: str) -> datetime:
    if not isinstance(value, str):
        raise WeatherWRHCollectorError(code)
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise WeatherWRHCollectorError(code) from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise WeatherWRHCollectorError(code)
    return parsed


def _init_kwargs(cls, payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise WeatherWRHCollectorError("PERSISTED_OBJECT_NOT_MAPPING")
    values: dict[str, Any] = {}
    for name, definition in cls.__dataclass_fields__.items():
        if definition.init:
            if name not in payload:
                raise WeatherWRHCollectorError(f"PERSISTED_FIELD_MISSING:{cls.__name__}:{name}")
            values[name] = payload[name]
    return values


def _capture_from_dict(payload: object) -> ProspectiveWeatherCalibrationCapture:
    if not isinstance(payload, dict):
        raise WeatherWRHCollectorError("PERSISTED_CAPTURE_NOT_MAPPING")
    prediction_payload = payload.get("prediction")
    rule_payload = payload.get("rule_evidence")
    if not isinstance(prediction_payload, dict) or not isinstance(rule_payload, dict):
        raise WeatherWRHCollectorError("PERSISTED_CAPTURE_COMPONENT_MISSING")

    prediction_values = _init_kwargs(ProspectiveBucketPrediction, prediction_payload)
    prediction_values["target_date"] = _date_from_iso(
        prediction_values["target_date"], "PERSISTED_PREDICTION_TARGET_DATE_INVALID"
    )
    prediction = ProspectiveBucketPrediction(**prediction_values)

    buckets_payload = rule_payload.get("bucket_partition")
    if not isinstance(buckets_payload, list) or not buckets_payload:
        raise WeatherWRHCollectorError("PERSISTED_RULE_BUCKETS_INVALID")
    buckets = tuple(FrozenRuleBucket(**_init_kwargs(FrozenRuleBucket, row)) for row in buckets_payload)
    rule_values = _init_kwargs(ProspectiveNWSRuleEvidence, rule_payload)
    rule_values["target_date"] = _date_from_iso(
        rule_values["target_date"], "PERSISTED_RULE_TARGET_DATE_INVALID"
    )
    rule_values["bucket_partition"] = buckets
    rule = ProspectiveNWSRuleEvidence(**rule_values)

    capture_values = _init_kwargs(ProspectiveWeatherCalibrationCapture, payload)
    capture_values["prediction"] = prediction
    capture_values["rule_evidence"] = rule
    capture = ProspectiveWeatherCalibrationCapture(**capture_values)
    try:
        return _validate_capture(capture)
    except WeatherCalibrationCaptureError as exc:
        raise WeatherWRHCollectorError(f"PERSISTED_CAPTURE_INVALID:{exc.code}") from exc


def _row_from_dict(payload: object) -> WRHHourlyRow:
    if not isinstance(payload, dict):
        raise WeatherWRHCollectorError("PERSISTED_WRH_ROW_INVALID")
    values = _init_kwargs(WRHHourlyRow, payload)
    values["observation_time_local"] = _datetime_from_iso(
        values["observation_time_local"], "PERSISTED_WRH_ROW_TIME_INVALID"
    )
    values["local_date"] = _date_from_iso(values["local_date"], "PERSISTED_WRH_ROW_DATE_INVALID")
    return WRHHourlyRow(**values)


def _snapshot_from_dict(payload: object) -> WRHSourceSnapshot:
    if not isinstance(payload, dict):
        raise WeatherWRHCollectorError("PERSISTED_SNAPSHOT_NOT_MAPPING")
    values = _init_kwargs(WRHSourceSnapshot, payload)
    for field_name in ("target_date", "query_start_date", "query_end_date"):
        values[field_name] = _date_from_iso(
            values[field_name], f"PERSISTED_SNAPSHOT_{field_name.upper()}_INVALID"
        )
    selected_payload = payload.get("selected_rows")
    target_payload = payload.get("target_rows")
    if not isinstance(selected_payload, list) or not isinstance(target_payload, list):
        raise WeatherWRHCollectorError("PERSISTED_SNAPSHOT_ROWS_INVALID")
    values["selected_rows"] = tuple(_row_from_dict(row) for row in selected_payload)
    values["target_rows"] = tuple(_row_from_dict(row) for row in target_payload)
    following_payload = payload.get("first_following_row")
    values["first_following_row"] = (
        None if following_payload is None else _row_from_dict(following_payload)
    )
    temperatures = payload.get("target_display_temperatures_f")
    if not isinstance(temperatures, (list, tuple)):
        raise WeatherWRHCollectorError("PERSISTED_SNAPSHOT_TEMPERATURES_INVALID")
    values["target_display_temperatures_f"] = tuple(temperatures)
    snapshot = WRHSourceSnapshot(**values)
    try:
        return _validate_snapshot(snapshot, "PERSISTED")
    except WRHSourceError as exc:
        raise WeatherWRHCollectorError(f"PERSISTED_SNAPSHOT_INVALID:{exc.code}") from exc


def _local_window(target: date, timezone_name: str) -> tuple[float, float]:
    try:
        zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError:
        raise WeatherWRHCollectorError("COLLECTOR_TIMEZONE_INVALID") from None
    start = datetime.combine(
        target,
        wall_time(ACTIVE_START_HOUR, ACTIVE_START_MINUTE),
        tzinfo=zone,
    )
    end = datetime.combine(
        target + timedelta(days=1),
        wall_time(ACTIVE_END_HOUR, ACTIVE_END_MINUTE),
        tzinfo=zone,
    )
    return start.timestamp(), end.timestamp()


def _bootstrap_not_before(target: date) -> float:
    return datetime(
        target.year,
        target.month,
        target.day,
        BOOTSTRAP_UTC_HOUR,
        0,
        tzinfo=timezone.utc,
    ).timestamp()


@dataclass(frozen=True, slots=True)
class CollectorTickReport:
    collector_version: str
    policy_id: str
    evaluated_station_dates: int
    fetched_snapshots: int
    authorized_captures: int
    failed_captures: int
    deferred_station_dates: int
    fetch_errors: tuple[str, ...]
    financial_authority: bool = False
    financial_delivery: bool = False
    automatic_order_placement: bool = False

    def as_dict(self) -> dict:
        return {
            "collector_version": self.collector_version,
            "policy_id": self.policy_id,
            "evaluated_station_dates": self.evaluated_station_dates,
            "fetched_snapshots": self.fetched_snapshots,
            "authorized_captures": self.authorized_captures,
            "failed_captures": self.failed_captures,
            "deferred_station_dates": self.deferred_station_dates,
            "fetch_errors": list(self.fetch_errors),
            "financial_authority": self.financial_authority,
            "financial_delivery": self.financial_delivery,
            "automatic_order_placement": self.automatic_order_placement,
        }


class WRHCollectorStore:
    def __init__(self, path: str | Path) -> None:
        self.path = str(path)
        self.db = sqlite3.connect(self.path, timeout=5.0)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA busy_timeout=5000")
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS wrh_collector_keys (
                station TEXT NOT NULL,
                target_date TEXT NOT NULL,
                timezone TEXT,
                last_poll_at REAL,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                failure_code TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (station, target_date)
            );

            CREATE TABLE IF NOT EXISTS wrh_collector_captures (
                capture_evidence_sha256 TEXT PRIMARY KEY,
                station TEXT NOT NULL,
                target_date TEXT NOT NULL,
                family TEXT NOT NULL,
                registered_at REAL NOT NULL,
                status TEXT NOT NULL,
                failure_code TEXT,
                capture_json TEXT NOT NULL,
                settlement_evidence_json TEXT,
                authorized_json TEXT,
                updated_at REAL NOT NULL,
                FOREIGN KEY (station, target_date)
                    REFERENCES wrh_collector_keys(station, target_date)
            );

            CREATE INDEX IF NOT EXISTS idx_wrh_captures_key_status
                ON wrh_collector_captures(station, target_date, status);

            CREATE TABLE IF NOT EXISTS wrh_collector_snapshots (
                station TEXT NOT NULL,
                target_date TEXT NOT NULL,
                evidence_sha256 TEXT NOT NULL,
                received_at REAL NOT NULL,
                snapshot_json TEXT NOT NULL,
                PRIMARY KEY (station, target_date, evidence_sha256),
                FOREIGN KEY (station, target_date)
                    REFERENCES wrh_collector_keys(station, target_date)
            );

            CREATE INDEX IF NOT EXISTS idx_wrh_snapshots_key_received
                ON wrh_collector_snapshots(station, target_date, received_at);
            """
        )
        self.db.commit()

    def close(self) -> None:
        self.db.close()

    def register_capture(self, capture: ProspectiveWeatherCalibrationCapture, registered_at: float) -> str:
        payload = _canonical_json(capture.as_dict())
        station = capture.rule_evidence.station
        target_text = capture.rule_evidence.target_date.isoformat()
        digest = capture.capture_evidence_sha256
        with self.db:
            self.db.execute(
                """
                INSERT OR IGNORE INTO wrh_collector_keys
                    (station, target_date, status, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (station, target_text, KEY_ACTIVE, registered_at),
            )
            existing = self.db.execute(
                "SELECT capture_json, status FROM wrh_collector_captures WHERE capture_evidence_sha256 = ?",
                (digest,),
            ).fetchone()
            if existing is not None:
                if existing["capture_json"] != payload:
                    raise WeatherWRHCollectorError("CAPTURE_DIGEST_COLLISION")
                return str(existing["status"])
            self.db.execute(
                """
                INSERT INTO wrh_collector_captures
                    (capture_evidence_sha256, station, target_date, family,
                     registered_at, status, capture_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    digest,
                    station,
                    target_text,
                    capture.rule_evidence.family,
                    registered_at,
                    CAPTURE_PENDING,
                    payload,
                    registered_at,
                ),
            )
        return CAPTURE_PENDING

    def pending_keys(self) -> list[sqlite3.Row]:
        return list(self.db.execute(
            """
            SELECT DISTINCT k.*
            FROM wrh_collector_keys k
            JOIN wrh_collector_captures c
              ON c.station = k.station AND c.target_date = k.target_date
            WHERE c.status = ?
            ORDER BY k.target_date, k.station
            """,
            (CAPTURE_PENDING,),
        ).fetchall())

    def pending_capture_rows(self, station: str, target: date) -> list[sqlite3.Row]:
        return list(self.db.execute(
            """
            SELECT * FROM wrh_collector_captures
            WHERE station = ? AND target_date = ? AND status = ?
            ORDER BY capture_evidence_sha256
            """,
            (station, target.isoformat(), CAPTURE_PENDING),
        ).fetchall())

    def update_key(
        self,
        station: str,
        target: date,
        *,
        timezone_name: str | None = None,
        last_poll_at: float | None = None,
        status: str | None = None,
        failure_code: str | None = None,
        updated_at: float,
    ) -> None:
        current = self.db.execute(
            "SELECT * FROM wrh_collector_keys WHERE station = ? AND target_date = ?",
            (station, target.isoformat()),
        ).fetchone()
        if current is None:
            raise WeatherWRHCollectorError("COLLECTOR_KEY_MISSING")
        with self.db:
            self.db.execute(
                """
                UPDATE wrh_collector_keys
                SET timezone = ?, last_poll_at = ?, status = ?, failure_code = ?, updated_at = ?
                WHERE station = ? AND target_date = ?
                """,
                (
                    timezone_name if timezone_name is not None else current["timezone"],
                    last_poll_at if last_poll_at is not None else current["last_poll_at"],
                    status if status is not None else current["status"],
                    failure_code if failure_code is not None else current["failure_code"],
                    updated_at,
                    station,
                    target.isoformat(),
                ),
            )

    def store_snapshot(self, snapshot: WRHSourceSnapshot) -> bool:
        payload = _canonical_json(snapshot.as_dict())
        with self.db:
            before = self.db.total_changes
            self.db.execute(
                """
                INSERT OR IGNORE INTO wrh_collector_snapshots
                    (station, target_date, evidence_sha256, received_at, snapshot_json)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    snapshot.station,
                    snapshot.target_date.isoformat(),
                    snapshot.evidence_sha256,
                    snapshot.received_at,
                    payload,
                ),
            )
            inserted = self.db.total_changes > before
            count = self.db.execute(
                """
                SELECT COUNT(*) AS n FROM wrh_collector_snapshots
                WHERE station = ? AND target_date = ?
                """,
                (snapshot.station, snapshot.target_date.isoformat()),
            ).fetchone()["n"]
            if count > MAX_SNAPSHOTS_PER_STATION_DATE:
                raise WeatherWRHCollectorError("COLLECTOR_SNAPSHOT_CAP_EXCEEDED")
        return inserted

    def load_snapshots(self, station: str, target: date) -> list[WRHSourceSnapshot]:
        rows = self.db.execute(
            """
            SELECT snapshot_json FROM wrh_collector_snapshots
            WHERE station = ? AND target_date = ?
            ORDER BY received_at ASC, evidence_sha256 ASC
            """,
            (station, target.isoformat()),
        ).fetchall()
        values: list[WRHSourceSnapshot] = []
        for row in rows:
            try:
                payload = json.loads(row["snapshot_json"])
            except json.JSONDecodeError:
                raise WeatherWRHCollectorError("PERSISTED_SNAPSHOT_JSON_INVALID") from None
            values.append(_snapshot_from_dict(payload))
        return values

    def load_capture(self, row: sqlite3.Row) -> ProspectiveWeatherCalibrationCapture:
        try:
            payload = json.loads(row["capture_json"])
        except json.JSONDecodeError:
            raise WeatherWRHCollectorError("PERSISTED_CAPTURE_JSON_INVALID") from None
        return _capture_from_dict(payload)

    def mark_failed(self, digest: str, code: str, updated_at: float) -> None:
        with self.db:
            self.db.execute(
                """
                UPDATE wrh_collector_captures
                SET status = ?, failure_code = ?, updated_at = ?
                WHERE capture_evidence_sha256 = ? AND status = ?
                """,
                (CAPTURE_FAILED, code, updated_at, digest, CAPTURE_PENDING),
            )

    def mark_authorized(
        self,
        digest: str,
        *,
        settlement_payload: dict,
        authorized: AuthorizedWRHCalibrationSample,
        updated_at: float,
    ) -> None:
        with self.db:
            self.db.execute(
                """
                UPDATE wrh_collector_captures
                SET status = ?, failure_code = NULL,
                    settlement_evidence_json = ?, authorized_json = ?, updated_at = ?
                WHERE capture_evidence_sha256 = ? AND status = ?
                """,
                (
                    CAPTURE_AUTHORIZED,
                    _canonical_json(settlement_payload),
                    _canonical_json(authorized.as_dict()),
                    updated_at,
                    digest,
                    CAPTURE_PENDING,
                ),
            )

    def complete_key_if_terminal(self, station: str, target: date, updated_at: float) -> None:
        pending = self.db.execute(
            """
            SELECT COUNT(*) AS n FROM wrh_collector_captures
            WHERE station = ? AND target_date = ? AND status = ?
            """,
            (station, target.isoformat(), CAPTURE_PENDING),
        ).fetchone()["n"]
        if pending == 0:
            self.update_key(
                station,
                target,
                status=KEY_COMPLETE,
                updated_at=updated_at,
            )

    def capture_records(self) -> list[dict]:
        rows = self.db.execute(
            """
            SELECT capture_evidence_sha256, station, target_date, family,
                   registered_at, status, failure_code,
                   settlement_evidence_json, authorized_json, updated_at
            FROM wrh_collector_captures
            ORDER BY target_date, station, capture_evidence_sha256
            """
        ).fetchall()
        result: list[dict] = []
        for row in rows:
            value = dict(row)
            for name in ("settlement_evidence_json", "authorized_json"):
                if value[name] is not None:
                    value[name.removesuffix("_json")] = json.loads(value.pop(name))
                else:
                    value.pop(name)
            result.append(value)
        return result


class WeatherWRHProspectiveCollector:
    def __init__(
        self,
        *,
        db_path: str | Path,
        client: NWSWRHLiveClient | None = None,
    ) -> None:
        self.store = WRHCollectorStore(db_path)
        self.client = client or NWSWRHLiveClient()

    def close(self) -> None:
        self.store.close()

    def register_capture(
        self,
        capture: ProspectiveWeatherCalibrationCapture,
        *,
        registered_at: float | None = None,
    ) -> str:
        try:
            frozen = _validate_capture(capture)
        except WeatherCalibrationCaptureError as exc:
            raise WeatherWRHCollectorError(f"REGISTER_CAPTURE_INVALID:{exc.code}") from exc
        registered = _now() if registered_at is None else _finite_timestamp(
            registered_at, "REGISTERED_AT_INVALID"
        )
        lag = registered - float(frozen.captured_at)
        if lag < 0.0:
            raise WeatherWRHCollectorError("CAPTURE_REGISTERED_BEFORE_CAPTURE")
        if lag > REGISTRATION_MAX_LAG_SECONDS:
            raise WeatherWRHCollectorError("CAPTURE_REGISTRATION_STALE")
        return self.store.register_capture(frozen, registered)

    def _fail_pending_key(self, station: str, target: date, code: str, now: float) -> int:
        rows = self.store.pending_capture_rows(station, target)
        for row in rows:
            self.store.mark_failed(row["capture_evidence_sha256"], code, now)
        self.store.complete_key_if_terminal(station, target, now)
        return len(rows)

    def _validate_prospective_capture_window(
        self,
        station: str,
        target: date,
        timezone_name: str,
        now: float,
    ) -> int:
        start, _ = _local_window(target, timezone_name)
        failed = 0
        for row in self.store.pending_capture_rows(station, target):
            try:
                capture = self.store.load_capture(row)
            except WeatherWRHCollectorError as exc:
                self.store.mark_failed(
                    row["capture_evidence_sha256"],
                    f"PERSISTED_CAPTURE:{exc.code}",
                    now,
                )
                failed += 1
                continue
            if capture.captured_at >= start:
                self.store.mark_failed(
                    capture.capture_evidence_sha256,
                    "CAPTURE_NOT_FROZEN_BEFORE_COLLECTION_WINDOW",
                    now,
                )
                failed += 1
        self.store.complete_key_if_terminal(station, target, now)
        return failed

    def _fetch_snapshot(self, station: str, target: date) -> WRHSourceSnapshot:
        result = self.client.fetch_snapshot(station=station, target_date=target)
        snapshot = result.snapshot
        try:
            return _validate_snapshot(snapshot, "COLLECTOR")
        except WRHSourceError as exc:
            raise WeatherWRHCollectorError(f"LIVE_SNAPSHOT_INVALID:{exc.code}") from exc

    def _resolve_current(
        self,
        station: str,
        target: date,
        current: WRHSourceSnapshot,
        now: float,
    ) -> tuple[int, int]:
        if current.first_following_row is None:
            return 0, 0
        following_timestamp = current.first_following_row.observation_time_local.timestamp()
        snapshots = self.store.load_snapshots(station, target)
        candidates = [
            snapshot
            for snapshot in snapshots
            if snapshot.evidence_sha256 != current.evidence_sha256
            and snapshot.first_following_row is None
            and snapshot.received_at < following_timestamp
        ]
        previous = max(candidates, key=lambda row: row.received_at) if candidates else None
        if previous is None:
            return 0, self._fail_pending_key(
                station,
                target,
                "FINALITY_PRE_CUTOFF_SNAPSHOT_MISSING",
                now,
            )

        authorized_count = 0
        failed_count = 0
        for row in self.store.pending_capture_rows(station, target):
            digest = row["capture_evidence_sha256"]
            try:
                capture = self.store.load_capture(row)
                settlement = build_wrh_exact_settlement_evidence(capture, previous, current)
                authorized = authorize_wrh_calibration_sample(capture, settlement)
            except WeatherWRHCollectorError as exc:
                self.store.mark_failed(digest, f"PERSISTED_EVIDENCE:{exc.code}", now)
                failed_count += 1
                continue
            except WRHSourceError as exc:
                self.store.mark_failed(digest, f"FINALITY:{exc.code}", now)
                failed_count += 1
                continue
            except WeatherCalibrationCaptureError as exc:
                self.store.mark_failed(digest, f"SETTLEMENT:{exc.code}", now)
                failed_count += 1
                continue
            except WeatherCalibrationAuthorityError as exc:
                self.store.mark_failed(digest, f"AUTHORITY:{exc.code}", now)
                failed_count += 1
                continue
            self.store.mark_authorized(
                digest,
                settlement_payload=settlement.as_dict(),
                authorized=authorized,
                updated_at=now,
            )
            authorized_count += 1
        self.store.complete_key_if_terminal(station, target, now)
        return authorized_count, failed_count

    def tick(self, *, now: float | None = None) -> CollectorTickReport:
        decision_time = _now() if now is None else _finite_timestamp(now, "COLLECTOR_NOW_INVALID")
        evaluated = 0
        fetched = 0
        authorized = 0
        failed = 0
        deferred = 0
        fetch_errors: list[str] = []

        for key in self.store.pending_keys():
            evaluated += 1
            station = str(key["station"])
            target = _date_from_iso(key["target_date"], "COLLECTOR_KEY_TARGET_DATE_INVALID")
            timezone_name = key["timezone"]
            last_poll_at = key["last_poll_at"]
            current: WRHSourceSnapshot | None = None

            if timezone_name is None:
                if decision_time < _bootstrap_not_before(target):
                    deferred += 1
                    continue
                if last_poll_at is not None and decision_time - float(last_poll_at) < POLL_INTERVAL_SECONDS:
                    deferred += 1
                    continue
                try:
                    current = self._fetch_snapshot(station, target)
                    inserted = self.store.store_snapshot(current)
                except (WRHSourceError, WeatherWRHCollectorError) as exc:
                    fetch_errors.append(f"{station}:{target.isoformat()}:{getattr(exc, 'code', type(exc).__name__)}")
                    continue
                fetched += 1 if inserted else 0
                timezone_name = current.timezone
                self.store.update_key(
                    station,
                    target,
                    timezone_name=timezone_name,
                    last_poll_at=current.received_at,
                    updated_at=decision_time,
                )
                failed += self._validate_prospective_capture_window(
                    station, target, timezone_name, decision_time
                )
                if not self.store.pending_capture_rows(station, target):
                    continue

            start, end = _local_window(target, str(timezone_name))
            if decision_time > end:
                failed += self._fail_pending_key(
                    station,
                    target,
                    "FINALITY_COLLECTION_WINDOW_EXPIRED",
                    decision_time,
                )
                continue
            if decision_time < start:
                deferred += 1
                continue

            if current is None:
                key_fresh = self.store.db.execute(
                    "SELECT last_poll_at FROM wrh_collector_keys WHERE station = ? AND target_date = ?",
                    (station, target.isoformat()),
                ).fetchone()
                last = key_fresh["last_poll_at"] if key_fresh is not None else None
                if last is not None and decision_time - float(last) < POLL_INTERVAL_SECONDS:
                    deferred += 1
                    continue
                try:
                    current = self._fetch_snapshot(station, target)
                    inserted = self.store.store_snapshot(current)
                except (WRHSourceError, WeatherWRHCollectorError) as exc:
                    fetch_errors.append(f"{station}:{target.isoformat()}:{getattr(exc, 'code', type(exc).__name__)}")
                    continue
                fetched += 1 if inserted else 0
                self.store.update_key(
                    station,
                    target,
                    last_poll_at=current.received_at,
                    updated_at=decision_time,
                )

            newly_authorized, newly_failed = self._resolve_current(
                station, target, current, decision_time
            )
            authorized += newly_authorized
            failed += newly_failed

        return CollectorTickReport(
            collector_version=WRH_PROSPECTIVE_COLLECTOR_VERSION,
            policy_id=WRH_PROSPECTIVE_COLLECTOR_POLICY_ID,
            evaluated_station_dates=evaluated,
            fetched_snapshots=fetched,
            authorized_captures=authorized,
            failed_captures=failed,
            deferred_station_dates=deferred,
            fetch_errors=tuple(fetch_errors),
        )

    def records(self) -> list[dict]:
        return self.store.capture_records()
