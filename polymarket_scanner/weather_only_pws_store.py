from __future__ import annotations

"""Persistence for same-day PWS diagnostics and official-capture outcome links.

A link row is created atomically with each official capture by SameDayCaptureStore.
This store transitions that link to AVAILABLE/UNAVAILABLE/FAILED. Any PENDING rows
left by a previous process are marked INTERRUPTED before new same-day work begins;
later observations are never backfilled as though they belonged to the old capture.
"""

import json
import os
import sqlite3
import time
from pathlib import Path

from .weather_only_pws import (
    PWS_DIAGNOSTIC_VERSION,
    PWS_STATUS_AVAILABLE,
    PWS_STATUS_NO_FRESH_QC,
    PWSDiagnosticRecord,
    verify_pws_diagnostic,
)


PWS_DIAGNOSTIC_STORE_VERSION = "weather_pws_diagnostic_store_v2_crash_auditable_links"


class PWSDiagnosticStoreError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class SameDayPWSDiagnosticStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_absolute():
            raise PWSDiagnosticStoreError("PWS_STORE_PATH_NOT_ABSOLUTE")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            raise PWSDiagnosticStoreError("PWS_STORE_FILE_INVALID")
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
                CREATE TABLE IF NOT EXISTS weather_same_day_pws_diagnostics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_version TEXT NOT NULL,
                    diagnostic_version TEXT NOT NULL,
                    diagnostic_sha256 TEXT NOT NULL UNIQUE,
                    capture_sha256 TEXT NOT NULL UNIQUE,
                    event_id TEXT NOT NULL,
                    station TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    as_of REAL NOT NULL,
                    status TEXT NOT NULL,
                    contradiction INTEGER NOT NULL CHECK(contradiction IN (0,1)),
                    payload_json TEXT NOT NULL,
                    included_in_validated_pnl INTEGER NOT NULL DEFAULT 0 CHECK(included_in_validated_pnl=0),
                    same_day_delivery_enabled INTEGER NOT NULL DEFAULT 0 CHECK(same_day_delivery_enabled=0),
                    financial_authority INTEGER NOT NULL DEFAULT 0 CHECK(financial_authority=0)
                );
                CREATE INDEX IF NOT EXISTS idx_weather_same_day_pws_event_date
                    ON weather_same_day_pws_diagnostics(event_id,target_date,as_of);
                CREATE INDEX IF NOT EXISTS idx_weather_same_day_pws_status
                    ON weather_same_day_pws_diagnostics(status,id);

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
            columns = {
                str(row[1])
                for row in db.execute("PRAGMA table_info(weather_same_day_pws_diagnostics)")
            }
            if "capture_sha256" not in columns:
                db.execute(
                    "ALTER TABLE weather_same_day_pws_diagnostics ADD COLUMN capture_sha256 TEXT"
                )
                db.execute(
                    "CREATE UNIQUE INDEX IF NOT EXISTS idx_weather_same_day_pws_capture_sha "
                    "ON weather_same_day_pws_diagnostics(capture_sha256) WHERE capture_sha256 IS NOT NULL"
                )
        if self.path.exists() and self.path.stat().st_mode & 0o077:
            os.chmod(self.path, 0o600)

    @staticmethod
    def _digest(value: object, code: str) -> str:
        digest = str(value or "").strip().lower()
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise PWSDiagnosticStoreError(code)
        return digest

    def mark_interrupted_pending(self, *, completed_at: float | None = None) -> int:
        instant = float(time.time() if completed_at is None else completed_at)
        with self._conn() as db:
            cur = db.execute(
                """
                UPDATE weather_same_day_pws_capture_links
                   SET state='INTERRUPTED',
                       failure_code='PWS_COLLECTION_INTERRUPTED_BEFORE_OUTCOME',
                       completed_at=?
                 WHERE state='PENDING'
                """,
                (instant,),
            )
        return int(cur.rowcount or 0)

    def finalize(self, capture_sha256: str, record: PWSDiagnosticRecord) -> int | None:
        capture_digest = self._digest(capture_sha256, "PWS_STORE_CAPTURE_SHA_INVALID")
        try:
            value = verify_pws_diagnostic(record)
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            raise PWSDiagnosticStoreError(f"PWS_STORE_RECORD_INVALID:{code}") from exc
        try:
            payload = json.dumps(
                value.as_dict(),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise PWSDiagnosticStoreError("PWS_STORE_JSON_INVALID") from None

        # Only a genuine successful query with no fresh/QC-passing station is
        # "UNAVAILABLE". Authentication, transport, provider, timeout, malformed,
        # encoding and configuration failures remain durable diagnostics but their
        # capture link is FAILED so missingness analyses cannot mistake outages for
        # evidence that no nearby PWS existed.
        if value.status == PWS_STATUS_AVAILABLE:
            state = "AVAILABLE"
            failure_code = None
        elif value.status == PWS_STATUS_NO_FRESH_QC:
            state = "UNAVAILABLE"
            failure_code = None
        else:
            state = "FAILED"
            failure_code = value.status

        with self._conn() as db:
            link = db.execute(
                "SELECT state,event_id,target_date FROM weather_same_day_pws_capture_links WHERE capture_sha256=?",
                (capture_digest,),
            ).fetchone()
            if link is None:
                raise PWSDiagnosticStoreError("PWS_STORE_CAPTURE_LINK_MISSING")
            if str(link["state"]) != "PENDING":
                raise PWSDiagnosticStoreError("PWS_STORE_CAPTURE_LINK_NOT_PENDING")
            if str(link["event_id"]) != value.event_id or str(link["target_date"]) != value.target_date:
                raise PWSDiagnosticStoreError("PWS_STORE_CAPTURE_LINK_IDENTITY_MISMATCH")
            try:
                cur = db.execute(
                    """
                    INSERT INTO weather_same_day_pws_diagnostics(
                        store_version,diagnostic_version,diagnostic_sha256,capture_sha256,
                        event_id,station,target_date,unit,as_of,status,contradiction,payload_json,
                        included_in_validated_pnl,same_day_delivery_enabled,financial_authority
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,0,0)
                    """,
                    (
                        PWS_DIAGNOSTIC_STORE_VERSION,
                        PWS_DIAGNOSTIC_VERSION,
                        value.diagnostic_sha256,
                        capture_digest,
                        value.event_id,
                        value.station,
                        value.target_date,
                        value.unit,
                        float(value.as_of),
                        value.status,
                        1 if value.contradiction else 0,
                        payload,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise PWSDiagnosticStoreError("PWS_STORE_DIAGNOSTIC_DUPLICATE") from exc
            db.execute(
                """
                UPDATE weather_same_day_pws_capture_links
                   SET state=?,diagnostic_sha256=?,failure_code=?,completed_at=?
                 WHERE capture_sha256=? AND state='PENDING'
                """,
                (
                    state,
                    value.diagnostic_sha256,
                    failure_code,
                    float(value.as_of),
                    capture_digest,
                ),
            )
        return int(cur.lastrowid)

    def record_failure(
        self,
        capture_sha256: str,
        code: str,
        *,
        completed_at: float | None = None,
    ) -> None:
        capture_digest = self._digest(capture_sha256, "PWS_STORE_CAPTURE_SHA_INVALID")
        failure = str(code or "PWS_UNKNOWN_FAILURE").strip()[:240]
        instant = float(time.time() if completed_at is None else completed_at)
        with self._conn() as db:
            cur = db.execute(
                """
                UPDATE weather_same_day_pws_capture_links
                   SET state='FAILED',failure_code=?,completed_at=?
                 WHERE capture_sha256=? AND state='PENDING'
                """,
                (failure, instant, capture_digest),
            )
            if int(cur.rowcount or 0) != 1:
                raise PWSDiagnosticStoreError("PWS_STORE_CAPTURE_LINK_NOT_PENDING")

    def link_for_capture(self, capture_sha256: str) -> dict | None:
        capture_digest = self._digest(capture_sha256, "PWS_STORE_CAPTURE_SHA_INVALID")
        with self._conn() as db:
            row = db.execute(
                "SELECT * FROM weather_same_day_pws_capture_links WHERE capture_sha256=?",
                (capture_digest,),
            ).fetchone()
        return dict(row) if row is not None else None

    def recent(self, limit: int = 20) -> list[dict]:
        count = max(1, min(200, int(limit)))
        with self._conn() as db:
            rows = db.execute(
                """
                SELECT id,diagnostic_sha256,capture_sha256,event_id,station,target_date,
                       unit,as_of,status,contradiction
                FROM weather_same_day_pws_diagnostics ORDER BY id DESC LIMIT ?
                """,
                (count,),
            ).fetchall()
        return [dict(row) for row in rows]

    def diagnostic_json(self, diagnostic_sha256: str) -> dict | None:
        digest = self._digest(diagnostic_sha256, "PWS_STORE_DIAGNOSTIC_SHA_INVALID")
        with self._conn() as db:
            row = db.execute(
                "SELECT payload_json FROM weather_same_day_pws_diagnostics WHERE diagnostic_sha256=?",
                (digest,),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(str(row["payload_json"]))
        except Exception as exc:
            raise PWSDiagnosticStoreError("PWS_STORE_CORRUPT_JSON") from exc
        if not isinstance(value, dict):
            raise PWSDiagnosticStoreError("PWS_STORE_CORRUPT_JSON")
        return value

    def summary(self) -> dict:
        with self._conn() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN status='AVAILABLE' THEN 1 ELSE 0 END) AS available,
                       SUM(CASE WHEN status='UNCONFIGURED' THEN 1 ELSE 0 END) AS unconfigured,
                       SUM(CASE WHEN contradiction=1 THEN 1 ELSE 0 END) AS contradictions,
                       COUNT(DISTINCT event_id) AS events,
                       COUNT(DISTINCT station || '|' || target_date) AS station_days,
                       MIN(as_of) AS oldest_as_of,
                       MAX(as_of) AS newest_as_of,
                       COALESCE(SUM(LENGTH(payload_json)),0) AS payload_json_bytes
                FROM weather_same_day_pws_diagnostics
                """
            ).fetchone()
            links = db.execute(
                """
                SELECT COUNT(*) AS total,
                       SUM(CASE WHEN state='PENDING' THEN 1 ELSE 0 END) AS pending,
                       SUM(CASE WHEN state='FAILED' THEN 1 ELSE 0 END) AS failed,
                       SUM(CASE WHEN state='INTERRUPTED' THEN 1 ELSE 0 END) AS interrupted,
                       SUM(CASE WHEN state='AVAILABLE' THEN 1 ELSE 0 END) AS linked_available,
                       SUM(CASE WHEN state='UNAVAILABLE' THEN 1 ELSE 0 END) AS linked_unavailable
                FROM weather_same_day_pws_capture_links
                """
            ).fetchone()
        database_bytes = self.path.stat().st_size if self.path.exists() else 0
        wal = Path(str(self.path) + "-wal")
        shm = Path(str(self.path) + "-shm")
        sidecar_bytes = sum(
            path.stat().st_size for path in (wal, shm) if path.exists() and path.is_file()
        )
        return {
            "version": PWS_DIAGNOSTIC_STORE_VERSION,
            "total": int(row["total"] or 0),
            "available": int(row["available"] or 0),
            "unconfigured": int(row["unconfigured"] or 0),
            "contradictions": int(row["contradictions"] or 0),
            "events": int(row["events"] or 0),
            "station_days": int(row["station_days"] or 0),
            "oldest_as_of": row["oldest_as_of"],
            "newest_as_of": row["newest_as_of"],
            "capture_links_total": int(links["total"] or 0),
            "capture_links_pending": int(links["pending"] or 0),
            "capture_links_failed": int(links["failed"] or 0),
            "capture_links_interrupted": int(links["interrupted"] or 0),
            "capture_links_available": int(links["linked_available"] or 0),
            "capture_links_unavailable": int(links["linked_unavailable"] or 0),
            "payload_json_bytes": int(row["payload_json_bytes"] or 0),
            "database_file_bytes": int(database_bytes),
            "sqlite_sidecar_bytes": int(sidecar_bytes),
            "predictive_only": True,
            "may_replace_official_observation": False,
            "may_reweight_probability": False,
            "included_in_validated_pnl": False,
            "same_day_delivery_enabled": False,
            "financial_authority": False,
        }
