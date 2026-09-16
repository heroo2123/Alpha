from __future__ import annotations

"""Isolated persistence for replayable same-day research evidence.

Same-day three-layer artifacts are deliberately stored outside the validated paper
position ledger.  Merely recording a research envelope must never create an OPEN
position, a Telegram receipt, a win/loss row or financial authority.

The full canonical envelope JSON is retained so a future calibration/review pass can
replay decisions without current network data.  Duplicate envelope digests are
idempotent.  This table may share the weather-paper SQLite file and therefore follows
its WAL/backup lifecycle, but its status remains ``RESEARCH_ONLY`` until a separately
frozen promotion protocol exists.
"""

import json
import os
import sqlite3
from pathlib import Path

from .weather_only_same_day_envelope import (
    SameDayEvidenceEnvelope,
    verify_same_day_evidence_envelope,
)


SAME_DAY_STORE_VERSION = "weather_same_day_research_store_v1_envelope_excluded_from_pnl"
SAME_DAY_RESEARCH_STATUS = "RESEARCH_ONLY"


class SameDayStoreError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class SameDayResearchStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_absolute():
            raise SameDayStoreError("SAME_DAY_STORE_PATH_NOT_ABSOLUTE")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            raise SameDayStoreError("SAME_DAY_STORE_FILE_INVALID")
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
                CREATE TABLE IF NOT EXISTS weather_same_day_research (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_version TEXT NOT NULL,
                    envelope_sha256 TEXT NOT NULL UNIQUE,
                    event_id TEXT NOT NULL,
                    station TEXT NOT NULL,
                    target_date TEXT NOT NULL,
                    family TEXT NOT NULL,
                    unit TEXT NOT NULL,
                    observation_population TEXT NOT NULL,
                    release_sha TEXT NOT NULL,
                    config_sha256 TEXT NOT NULL,
                    execution_protocol_id TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    status TEXT NOT NULL CHECK(status='RESEARCH_ONLY'),
                    envelope_json TEXT NOT NULL,
                    calibrated_probability INTEGER NOT NULL DEFAULT 0 CHECK(calibrated_probability=0),
                    same_day_delivery_enabled INTEGER NOT NULL DEFAULT 0 CHECK(same_day_delivery_enabled=0),
                    financial_authority INTEGER NOT NULL DEFAULT 0 CHECK(financial_authority=0)
                );
                CREATE INDEX IF NOT EXISTS idx_weather_same_day_event_date
                    ON weather_same_day_research(event_id, target_date, created_at);
                CREATE INDEX IF NOT EXISTS idx_weather_same_day_created
                    ON weather_same_day_research(created_at);
                """
            )
        if self.path.exists() and self.path.stat().st_mode & 0o077:
            os.chmod(self.path, 0o600)

    def save_envelope(self, envelope: SameDayEvidenceEnvelope) -> int | None:
        try:
            value = verify_same_day_evidence_envelope(envelope)
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            raise SameDayStoreError(f"SAME_DAY_STORE_ENVELOPE_INVALID:{code}") from exc
        payload = value.as_dict()
        semantics = payload.get("contract_semantics")
        if not isinstance(semantics, dict):
            raise SameDayStoreError("SAME_DAY_STORE_CONTRACT_SEMANTICS_MISSING")
        try:
            text = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise SameDayStoreError("SAME_DAY_STORE_JSON_INVALID") from None
        with self._conn() as db:
            try:
                cur = db.execute(
                    """
                    INSERT INTO weather_same_day_research(
                        store_version,envelope_sha256,event_id,station,target_date,family,
                        unit,observation_population,release_sha,config_sha256,
                        execution_protocol_id,created_at,status,envelope_json,
                        calibrated_probability,same_day_delivery_enabled,financial_authority
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,0)
                    """,
                    (
                        SAME_DAY_STORE_VERSION,
                        value.envelope_sha256,
                        str(payload["final_decision"]["event_id"]),
                        str(payload["final_decision"]["station"]),
                        str(payload["final_decision"]["target_date"]),
                        str(payload["final_decision"]["family"]),
                        str(payload["final_decision"]["unit"]),
                        str(semantics.get("observation_population") or ""),
                        value.release_sha,
                        value.config_sha256,
                        value.execution_protocol_id,
                        float(value.created_at),
                        SAME_DAY_RESEARCH_STATUS,
                        text,
                    ),
                )
            except sqlite3.IntegrityError:
                return None
        return int(cur.lastrowid)

    def recent(self, limit: int = 20) -> list[dict]:
        count = max(1, min(200, int(limit)))
        with self._conn() as db:
            return [dict(row) for row in db.execute(
                """
                SELECT id,envelope_sha256,event_id,station,target_date,family,unit,
                       observation_population,release_sha,config_sha256,
                       execution_protocol_id,created_at,status
                FROM weather_same_day_research
                ORDER BY id DESC LIMIT ?
                """,
                (count,),
            )]

    def envelope_json(self, envelope_sha256: str) -> dict | None:
        digest = str(envelope_sha256 or "").strip().lower()
        with self._conn() as db:
            row = db.execute(
                "SELECT envelope_json FROM weather_same_day_research WHERE envelope_sha256=?",
                (digest,),
            ).fetchone()
        if row is None:
            return None
        try:
            value = json.loads(str(row["envelope_json"]))
        except Exception as exc:
            raise SameDayStoreError("SAME_DAY_STORE_CORRUPT_ENVELOPE_JSON") from exc
        if not isinstance(value, dict):
            raise SameDayStoreError("SAME_DAY_STORE_CORRUPT_ENVELOPE_JSON")
        return value

    def summary(self) -> dict:
        with self._conn() as db:
            row = db.execute(
                """
                SELECT COUNT(*) AS total,
                       COUNT(DISTINCT event_id) AS events,
                       COUNT(DISTINCT station || '|' || target_date) AS station_days,
                       MIN(created_at) AS oldest_created_at,
                       MAX(created_at) AS newest_created_at
                FROM weather_same_day_research
                """
            ).fetchone()
        return {
            "version": SAME_DAY_STORE_VERSION,
            "status": SAME_DAY_RESEARCH_STATUS,
            "total": int(row["total"] or 0),
            "events": int(row["events"] or 0),
            "station_days": int(row["station_days"] or 0),
            "oldest_created_at": row["oldest_created_at"],
            "newest_created_at": row["newest_created_at"],
            "included_in_validated_pnl": False,
            "calibrated_probability": False,
            "same_day_delivery_enabled": False,
            "financial_authority": False,
        }
