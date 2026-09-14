from __future__ import annotations

"""Persistence for silent three-layer captures, including blocked scientific states.

Official three-layer capture persistence is authoritative for research continuity.
Each newly saved capture atomically creates a PWS outcome link in PENDING state so a
crash between official persistence and optional PWS persistence is auditable rather
than silently losing the sample relationship.
"""

import json
import os
import sqlite3
from pathlib import Path

from .weather_only_same_day_capture import (
    SAME_DAY_CAPTURE_BLOCKED,
    SAME_DAY_CAPTURE_READY,
    SAME_DAY_CAPTURE_VERSION,
    SameDayCaptureRecord,
    verify_same_day_capture_record,
)


SAME_DAY_CAPTURE_STORE_VERSION = "weather_same_day_capture_store_v3_atomic_pws_intent"


class SameDayCaptureStoreError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class SameDayCaptureStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
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

                CREATE TABLE IF NOT EXISTS weather_same_day_pws_capture_links (
                    capture_sha256 TEXT PRIMARY KEY,
                    event_id TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    capture_as_of REAL NOT NULL,
                    state TEXT NOT NULL CHECK(state IN ('PENDING','AVAILABLE','UNAVAILABLE','FAILED','INTERRUPTED')),
                    diagnostic_sha256 TEXT,
                    failure_code TEXT,
                    started_at REAL NOT NULL,
                    completed_at REAL,
                    included_in_validated_pnl INTEGER NOT NULL DEFAULT 0 CHECK(included_in_validated_pnl=0),
                    same_day_delivery_enabled INTEGER NOT NULL DEFAULT 0 CHECK(same_day_delivery_enabled=0),
                    financial_authority INTEGER NOT NULL DEFAULT 0 CHECK(financial_authority=0)
                );
                CREATE INDEX IF NOT EXISTS idx_weather_same_day_pws_link_state
                    ON weather_same_day_pws_capture_links(state,event_id,capture_as_of);
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
                db.execute(
                    """
                    INSERT INTO weather_same_day_pws_capture_links(
                        capture_sha256,event_id,target_date,capture_as_of,state,
                        diagnostic_sha256,failure_code,started_at,completed_at,
                        included_in_validated_pnl,same_day_delivery_enabled,financial_authority
                    ) VALUES(?,?,?,?, 'PENDING',NULL,NULL,?,NULL,0,0,0)
                    """,
                    (
                        value.capture_sha256,
                        value.event_id,
                        value.target_date,
                        float(value.as_of),
                        float(value.as_of),
                    ),
                )
            except sqlite3.IntegrityError:
                return None
        return int(cur.lastrowid)

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
        return {
            "version": SAME_DAY_CAPTURE_STORE_VERSION,
            "total": int(row["total"] or 0),
            "blocked": int(row["blocked"] or 0),
            "ready_uncalibrated": int(row["ready"] or 0),
            "events": int(row["events"] or 0),
            "station_days": int(row["station_days"] or 0),
            "oldest_as_of": row["oldest_as_of"],
            "newest_as_of": row["newest_as_of"],
            "capture_json_bytes": int(row["capture_json_bytes"] or 0),
            "database_file_bytes": int(database_bytes),
            "sqlite_sidecar_bytes": int(sidecar_bytes),
            "automatic_evidence_pruning": False,
            "persistent_cadence_supported": True,
            "pws_outcome_intent_atomic_with_capture": True,
            "included_in_validated_pnl": False,
            "same_day_delivery_enabled": False,
            "financial_authority": False,
        }
