from __future__ import annotations

"""Persistence for same-day PWS diagnostics.

This store is deliberately isolated from paper positions, settlement and validated
P&L. PWS evidence is predictive-only and cannot create delivery or financial authority.
"""

import json
import os
import sqlite3
from pathlib import Path

from .weather_only_pws import (
    PWS_DIAGNOSTIC_VERSION,
    PWSDiagnosticRecord,
    verify_pws_diagnostic,
)


PWS_DIAGNOSTIC_STORE_VERSION = "weather_pws_diagnostic_store_v1_research_only"


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
                """
            )
        if self.path.exists() and self.path.stat().st_mode & 0o077:
            os.chmod(self.path, 0o600)

    def save(self, record: PWSDiagnosticRecord) -> int | None:
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
        with self._conn() as db:
            try:
                cur = db.execute(
                    """
                    INSERT INTO weather_same_day_pws_diagnostics(
                        store_version,diagnostic_version,diagnostic_sha256,event_id,
                        station,target_date,unit,as_of,status,contradiction,payload_json,
                        included_in_validated_pnl,same_day_delivery_enabled,financial_authority
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,0,0,0)
                    """,
                    (
                        PWS_DIAGNOSTIC_STORE_VERSION,
                        PWS_DIAGNOSTIC_VERSION,
                        value.diagnostic_sha256,
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
            except sqlite3.IntegrityError:
                return None
        return int(cur.lastrowid)

    def recent(self, limit: int = 20) -> list[dict]:
        count = max(1, min(200, int(limit)))
        with self._conn() as db:
            rows = db.execute(
                """
                SELECT id,diagnostic_sha256,event_id,station,target_date,unit,as_of,
                       status,contradiction
                FROM weather_same_day_pws_diagnostics ORDER BY id DESC LIMIT ?
                """,
                (count,),
            ).fetchall()
        return [dict(row) for row in rows]

    def diagnostic_json(self, diagnostic_sha256: str) -> dict | None:
        digest = str(diagnostic_sha256 or "").strip().lower()
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
