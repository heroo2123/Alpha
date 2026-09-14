from __future__ import annotations

"""Persistence for silent three-layer captures, including blocked scientific states.

The ordinary same-day envelope store contains only fully replayable final decisions.
During scientific hardening we also need to retain *why* a live source set could not
be promoted: missing official elapsed cells, unproven population alignment, an NWS
near-term coverage failure, etc. This table stores those immutable captures without
creating paper positions, Telegram delivery rows, settlement rows or validated P&L.

Capture cadence is intentionally queryable from SQLite. The canonical runtime uses
``latest_as_of_for_event`` before source acquisition, so restarting the process cannot
reset an in-memory cooldown and flood the month-scale research database. Evidence is
not silently pruned here; backup/retention policy remains an explicit operator action.
"""

import json
import math
import os
import sqlite3
import time
from pathlib import Path

from .weather_only_same_day_capture import (
    SAME_DAY_CAPTURE_BLOCKED,
    SAME_DAY_CAPTURE_READY,
    SAME_DAY_CAPTURE_VERSION,
    SameDayCaptureRecord,
    verify_same_day_capture_record,
)


SAME_DAY_CAPTURE_STORE_VERSION = "weather_same_day_capture_store_v3_attempt_audit_capacity"
SAME_DAY_ATTEMPT_VERSION = "weather_same_day_capture_attempt_v1"
_ATTEMPT_FINAL_OUTCOMES = {"SAVED", "DUPLICATE", "FAILED"}


def _optional_positive_int(value: int | None, code: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise SameDayCaptureStoreError(code)
    return value


def _nonnegative_time(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SameDayCaptureStoreError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise SameDayCaptureStoreError(code)
    return number


class SameDayCaptureStoreError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class SameDayCaptureStore:
    def __init__(
        self,
        path: str | Path,
        *,
        max_capture_rows: int | None = None,
        max_capture_json_bytes: int | None = None,
        max_attempt_rows: int | None = None,
    ) -> None:
        self.path = Path(path)
        self.max_capture_rows = _optional_positive_int(
            max_capture_rows, "SAME_DAY_CAPTURE_STORE_MAX_CAPTURE_ROWS_INVALID"
        )
        self.max_capture_json_bytes = _optional_positive_int(
            max_capture_json_bytes, "SAME_DAY_CAPTURE_STORE_MAX_CAPTURE_BYTES_INVALID"
        )
        self.max_attempt_rows = _optional_positive_int(
            max_attempt_rows, "SAME_DAY_CAPTURE_STORE_MAX_ATTEMPT_ROWS_INVALID"
        )
        if not self.path.is_absolute():
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_PATH_NOT_ABSOLUTE")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_FILE_INVALID")
        self._init()

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _init(self) -> None:
        with self._conn() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS weather_same_day_captures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_version TEXT NOT NULL,
                    capture_version TEXT NOT NULL,
                    capture_sha256 TEXT NOT NULL UNIQUE,
                    event_id TEXT NOT NULL,
                    station TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    family TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    as_of REAL NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('BLOCKED_RESEARCH','RESEARCH_READY_UNCALIBRATED')),
                    block_reasons_json TEXT NOT NULL,
                    capture_json TEXT NOT NULL,
                    included_in_validated_pnl INTEGER NOT NULL DEFAULT 0 CHECK(included_in_validated_pnl=0),
                    same_day_delivery_enabled INTEGER NOT NULL DEFAULT 0 CHECK(same_day_delivery_enabled=0),
                    financial_authority INTEGER NOT NULL DEFAULT 0 CHECK(financial_authority=0)
                );
                CREATE INDEX IF NOT EXISTS idx_weather_same_day_capture_event_date
                    ON weather_same_day_captures(event_id,target_date,as_of);
                CREATE INDEX IF NOT EXISTS idx_weather_same_day_capture_status
                    ON weather_same_day_captures(status,id);

                CREATE TABLE IF NOT EXISTS weather_same_day_capture_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    attempt_version TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    station TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    family TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    attempted_at REAL NOT NULL,
                    completed_at REAL,
                    outcome TEXT NOT NULL CHECK(outcome IN ('STARTED','SAVED','DUPLICATE','FAILED')),
                    error_code TEXT,
                    capture_sha256 TEXT,
                    included_in_validated_pnl INTEGER NOT NULL DEFAULT 0 CHECK(included_in_validated_pnl=0),
                    same_day_delivery_enabled INTEGER NOT NULL DEFAULT 0 CHECK(same_day_delivery_enabled=0),
                    financial_authority INTEGER NOT NULL DEFAULT 0 CHECK(financial_authority=0)
                );
                CREATE INDEX IF NOT EXISTS idx_weather_same_day_attempt_event_time
                    ON weather_same_day_capture_attempts(event_id,attempted_at);
                CREATE INDEX IF NOT EXISTS idx_weather_same_day_attempt_outcome
                    ON weather_same_day_capture_attempts(outcome,id);
                """
            )
        if self.path.exists() and self.path.stat().st_mode & 0o077:
            os.chmod(self.path, 0o600)

    def save(self, record: SameDayCaptureRecord) -> int | None:
        try:
            value = verify_same_day_capture_record(record)
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            raise SameDayCaptureStoreError(f"SAME_DAY_CAPTURE_STORE_RECORD_INVALID:{code}") from exc
        try:
            payload = json.dumps(
                value.as_dict(), sort_keys=True, separators=(",", ":"),
                ensure_ascii=True, allow_nan=False,
            )
            reasons = json.dumps(
                list(value.block_reasons), sort_keys=True, separators=(",", ":"),
                ensure_ascii=True, allow_nan=False,
            )
        except (TypeError, ValueError):
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_JSON_INVALID") from None
        with self._conn() as db:
            existing = db.execute(
                "SELECT id FROM weather_same_day_captures WHERE capture_sha256=?",
                (value.capture_sha256,),
            ).fetchone()
            if existing is not None:
                return None
            capacity = db.execute(
                "SELECT COUNT(*) AS rows, COALESCE(SUM(LENGTH(capture_json)),0) AS bytes "
                "FROM weather_same_day_captures"
            ).fetchone()
            current_rows = int(capacity["rows"] or 0)
            current_bytes = int(capacity["bytes"] or 0)
            if self.max_capture_rows is not None and current_rows >= self.max_capture_rows:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CAPTURE_ROW_CAP_EXHAUSTED")
            if (
                self.max_capture_json_bytes is not None
                and current_bytes + len(payload) > self.max_capture_json_bytes
            ):
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CAPTURE_BYTE_CAP_EXHAUSTED")
            try:
                cur = db.execute(
                    """
                    INSERT INTO weather_same_day_captures(
                        store_version,capture_version,capture_sha256,event_id,station,
                        target_date,family,unit,as_of,status,block_reasons_json,capture_json,
                        included_in_validated_pnl,same_day_delivery_enabled,financial_authority
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,0,0)
                    """,
                    (
                        SAME_DAY_CAPTURE_STORE_VERSION,
                        SAME_DAY_CAPTURE_VERSION,
                        value.capture_sha256,
                        value.event_id,
                        value.station,
                        value.target_date,
                        value.family,
                        value.unit,
                        float(value.as_of),
                        value.status,
                        reasons,
                        payload,
                    ),
                )
            except sqlite3.IntegrityError:
                return None
        return int(cur.lastrowid)


    def start_attempt(
        self,
        *,
        event_id: str,
        station: str,
        target_date: str,
        family: str,
        unit: str,
        attempted_at: float,
    ) -> int:
        identity = str(event_id or "").strip()
        station_id = str(station or "").strip().upper()
        target = str(target_date or "").strip()
        family_id = str(family or "").strip()
        unit_id = str(unit or "").strip()
        if not all((identity, station_id, target, family_id, unit_id)):
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_IDENTITY_INVALID")
        started = _nonnegative_time(attempted_at, "SAME_DAY_CAPTURE_STORE_ATTEMPT_TIME_INVALID")
        with self._conn() as db:
            row = db.execute(
                "SELECT COUNT(*) AS total FROM weather_same_day_capture_attempts"
            ).fetchone()
            total = int(row["total"] or 0)
            if self.max_attempt_rows is not None and total >= self.max_attempt_rows:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_ROW_CAP_EXHAUSTED")
            cur = db.execute(
                """
                INSERT INTO weather_same_day_capture_attempts(
                    attempt_version,event_id,station,target_date,family,unit,
                    attempted_at,completed_at,outcome,error_code,capture_sha256,
                    included_in_validated_pnl,same_day_delivery_enabled,financial_authority
                ) VALUES(?,?,?,?,?,?,?,NULL,'STARTED',NULL,NULL,0,0,0)
                """,
                (
                    SAME_DAY_ATTEMPT_VERSION,
                    identity,
                    station_id,
                    target,
                    family_id,
                    unit_id,
                    started,
                ),
            )
        return int(cur.lastrowid)

    def finish_attempt(
        self,
        attempt_id: int,
        *,
        outcome: str,
        completed_at: float | None = None,
        error_code: str | None = None,
        capture_sha256: str | None = None,
    ) -> None:
        if isinstance(attempt_id, bool) or not isinstance(attempt_id, int) or attempt_id <= 0:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_ID_INVALID")
        final = str(outcome or "").strip().upper()
        if final not in _ATTEMPT_FINAL_OUTCOMES:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_OUTCOME_INVALID")
        finished = _nonnegative_time(
            time.time() if completed_at is None else completed_at,
            "SAME_DAY_CAPTURE_STORE_ATTEMPT_TIME_INVALID",
        )
        error = None if error_code is None else str(error_code).strip()
        digest = None if capture_sha256 is None else str(capture_sha256).strip().lower()
        if final == "FAILED":
            if not error:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_ERROR_REQUIRED")
            if digest is not None:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_FAILED_ATTEMPT_HAS_CAPTURE")
        else:
            if error:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_SUCCESS_ATTEMPT_HAS_ERROR")
            if digest is None or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_CAPTURE_DIGEST_INVALID")
        with self._conn() as db:
            row = db.execute(
                "SELECT attempted_at,outcome FROM weather_same_day_capture_attempts WHERE id=?",
                (attempt_id,),
            ).fetchone()
            if row is None or row["outcome"] != "STARTED":
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_STATE_INVALID")
            if finished < float(row["attempted_at"]):
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_TIME_INVALID")
            cur = db.execute(
                """
                UPDATE weather_same_day_capture_attempts
                   SET completed_at=?,outcome=?,error_code=?,capture_sha256=?
                 WHERE id=? AND outcome='STARTED'
                """,
                (finished, final, error, digest, attempt_id),
            )
            if cur.rowcount != 1:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_STATE_INVALID")

    def latest_attempt_at_for_event(self, event_id: str) -> float | None:
        identity = str(event_id or "").strip()
        if not identity:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_EVENT_ID_INVALID")
        with self._conn() as db:
            row = db.execute(
                "SELECT MAX(attempted_at) AS latest FROM weather_same_day_capture_attempts WHERE event_id=?",
                (identity,),
            ).fetchone()
        if row is None or row["latest"] is None:
            return None
        return float(row["latest"])

    def reconcile_started_attempts(self, *, completed_at: float | None = None) -> int:
        finished = _nonnegative_time(
            time.time() if completed_at is None else completed_at,
            "SAME_DAY_CAPTURE_STORE_ATTEMPT_TIME_INVALID",
        )
        with self._conn() as db:
            recovered = db.execute(
                """
                UPDATE weather_same_day_capture_attempts AS a
                   SET completed_at=(
                           SELECT MAX(a.attempted_at,c.as_of)
                             FROM weather_same_day_captures AS c
                            WHERE c.event_id=a.event_id AND c.as_of>=a.attempted_at
                            ORDER BY c.as_of ASC LIMIT 1
                       ),
                       outcome='SAVED',
                       capture_sha256=(
                           SELECT c.capture_sha256
                             FROM weather_same_day_captures AS c
                            WHERE c.event_id=a.event_id AND c.as_of>=a.attempted_at
                            ORDER BY c.as_of ASC LIMIT 1
                       )
                 WHERE a.outcome='STARTED'
                   AND EXISTS(
                       SELECT 1 FROM weather_same_day_captures AS c
                        WHERE c.event_id=a.event_id AND c.as_of>=a.attempted_at
                   )
                """
            )
            interrupted = db.execute(
                """
                UPDATE weather_same_day_capture_attempts
                   SET completed_at=CASE WHEN attempted_at>? THEN attempted_at ELSE ? END,
                       outcome='FAILED',
                       error_code='PROCESS_INTERRUPTED_BEFORE_ATTEMPT_FINALIZATION'
                 WHERE outcome='STARTED'
                """,
                (finished, finished),
            )
        return int(recovered.rowcount) + int(interrupted.rowcount)

    def attempt_summary(self) -> dict:
        with self._conn() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN outcome='STARTED' THEN 1 ELSE 0 END) AS started,
                       SUM(CASE WHEN outcome='SAVED' THEN 1 ELSE 0 END) AS saved,
                       SUM(CASE WHEN outcome='DUPLICATE' THEN 1 ELSE 0 END) AS duplicates,
                       SUM(CASE WHEN outcome='FAILED' THEN 1 ELSE 0 END) AS failed,
                       MIN(attempted_at) AS oldest_attempt,
                       MAX(attempted_at) AS newest_attempt
                  FROM weather_same_day_capture_attempts
                """
            ).fetchone()
        total = int(row["total"] or 0)
        return {
            "version": SAME_DAY_ATTEMPT_VERSION,
            "total": total,
            "started": int(row["started"] or 0),
            "saved": int(row["saved"] or 0),
            "duplicates": int(row["duplicates"] or 0),
            "failed": int(row["failed"] or 0),
            "oldest_attempt": row["oldest_attempt"],
            "newest_attempt": row["newest_attempt"],
            "max_attempt_rows": self.max_attempt_rows,
            "capacity_exhausted": (
                self.max_attempt_rows is not None and total >= self.max_attempt_rows
            ),
            "included_in_validated_pnl": False,
            "same_day_delivery_enabled": False,
            "financial_authority": False,
        }

    def latest_as_of_for_event(self, event_id: str) -> float | None:
        identity = str(event_id or "").strip()
        if not identity:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_EVENT_ID_INVALID")
        with self._conn() as db:
            row = db.execute(
                "SELECT MAX(as_of) AS latest FROM weather_same_day_captures WHERE event_id=?",
                (identity,),
            ).fetchone()
        if row is None or row["latest"] is None:
            return None
        return float(row["latest"])

    def recent(self, limit: int = 20) -> list[dict]:
        count = max(1, min(200, int(limit)))
        with self._conn() as db:
            rows = db.execute(
                """
                SELECT id,capture_sha256,event_id,station,target_date,family,unit,
                       as_of,status,block_reasons_json
                FROM weather_same_day_captures ORDER BY id DESC LIMIT ?
                """,
                (count,),
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            try:
                item["block_reasons"] = json.loads(item.pop("block_reasons_json"))
            except Exception as exc:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_REASONS") from exc
            result.append(item)
        return result

    def capture_json(self, capture_sha256: str) -> dict | None:
        digest = str(capture_sha256 or "").strip().lower()
        with self._conn() as db:
            row = db.execute(
                "SELECT capture_json FROM weather_same_day_captures WHERE capture_sha256=?",
                (digest,),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(str(row["capture_json"]))
        except Exception as exc:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_CAPTURE_JSON") from exc
        if not isinstance(value, dict):
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_CAPTURE_JSON")
        return value

    def summary(self) -> dict:
        with self._conn() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status='BLOCKED_RESEARCH' THEN 1 ELSE 0 END) AS blocked,
                       SUM(CASE WHEN status='RESEARCH_READY_UNCALIBRATED' THEN 1 ELSE 0 END) AS ready,
                       COUNT(DISTINCT event_id) AS events,
                       COUNT(DISTINCT station || '|' || target_date) AS station_days,
                       MIN(as_of) AS oldest_as_of,
                       MAX(as_of) AS newest_as_of,
                       COALESCE(SUM(LENGTH(capture_json)),0) AS capture_json_bytes
                FROM weather_same_day_captures
                """
            ).fetchone()
        database_bytes = self.path.stat().st_size if self.path.exists() else 0
        wal = Path(str(self.path) + "-wal")
        shm = Path(str(self.path) + "-shm")
        sidecar_bytes = sum(
            path.stat().st_size for path in (wal, shm) if path.exists() and path.is_file()
        )
        capture_total = int(row["total"] or 0)
        capture_json_bytes = int(row["capture_json_bytes"] or 0)
        attempts = self.attempt_summary()
        return {
            "version": SAME_DAY_CAPTURE_STORE_VERSION,
            "total": capture_total,
            "blocked": int(row["blocked"] or 0),
            "ready_uncalibrated": int(row["ready"] or 0),
            "events": int(row["events"] or 0),
            "station_days": int(row["station_days"] or 0),
            "oldest_as_of": row["oldest_as_of"],
            "newest_as_of": row["newest_as_of"],
            "capture_json_bytes": capture_json_bytes,
            "max_capture_rows": self.max_capture_rows,
            "max_capture_json_bytes": self.max_capture_json_bytes,
            "capture_capacity_exhausted": (
                (self.max_capture_rows is not None and capture_total >= self.max_capture_rows)
                or (
                    self.max_capture_json_bytes is not None
                    and capture_json_bytes >= self.max_capture_json_bytes
                )
            ),
            "attempts": attempts,
            "database_file_bytes": int(database_bytes),
            "sqlite_sidecar_bytes": int(sidecar_bytes),
            "automatic_evidence_pruning": False,
            "persistent_cadence_supported": True,
            "included_in_validated_pnl": False,
            "same_day_delivery_enabled": False,
            "financial_authority": False,
        }
