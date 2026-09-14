from __future__ import annotations

"""Isolated persistence for weather-only live-paper signals.

This database is deliberately separate from the legacy scanner database and from the
W7 containment database.  It records what the paper service actually observed and
what Telegram accepted so later analysis can distinguish model quality from delivery
or execution assumptions.  It contains no order, wallet or authenticated trading
state.
"""

import json
import math
import os
import sqlite3
import time
from pathlib import Path


WEATHER_PAPER_STORE_VERSION = "weather_live_paper_store_v1_isolated_signal_evidence"


class WeatherPaperStoreError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite_optional(value: object | None, code: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherPaperStoreError(code)
    number = float(value)
    if not math.isfinite(number):
        raise WeatherPaperStoreError(code)
    return number


class WeatherPaperStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_absolute():
            raise WeatherPaperStoreError("PAPER_STORE_PATH_NOT_ABSOLUTE")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists() and (self.path.is_symlink() or not self.path.is_file()):
            raise WeatherPaperStoreError("PAPER_STORE_FILE_INVALID")
        existed = self.path.exists()
        with self._conn() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS weather_paper_signals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    store_version TEXT NOT NULL,
                    fingerprint TEXT NOT NULL UNIQUE,
                    lane TEXT NOT NULL,
                    evidence_class TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    market_id TEXT,
                    side TEXT,
                    token_id TEXT,
                    model_probability REAL,
                    entry_cost REAL,
                    raw_gap REAL,
                    theoretical_payout REAL,
                    execution_verified INTEGER NOT NULL DEFAULT 0 CHECK(execution_verified=0),
                    payload_json TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    telegram_message_id INTEGER,
                    telegram_sent_at REAL,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    settlement_payout REAL,
                    settled_at REAL,
                    paper_return REAL,
                    financial_authority INTEGER NOT NULL DEFAULT 0 CHECK(financial_authority=0),
                    automatic_order_placement INTEGER NOT NULL DEFAULT 0 CHECK(automatic_order_placement=0)
                );
                CREATE INDEX IF NOT EXISTS idx_weather_paper_lane
                    ON weather_paper_signals(lane, created_at);
                CREATE INDEX IF NOT EXISTS idx_weather_paper_status
                    ON weather_paper_signals(status, created_at);
                """
            )
        if not existed:
            os.chmod(self.path, 0o600)
        if self.path.stat().st_mode & 0o077:
            raise WeatherPaperStoreError("PAPER_STORE_PERMISSIONS_TOO_BROAD")

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def save_signal(
        self,
        *,
        fingerprint: str,
        lane: str,
        evidence_class: str,
        event_id: str,
        payload: dict,
        market_id: str | None = None,
        side: str | None = None,
        token_id: str | None = None,
        model_probability: float | None = None,
        entry_cost: float | None = None,
        raw_gap: float | None = None,
        theoretical_payout: float | None = None,
        created_at: float | None = None,
    ) -> int | None:
        fp = str(fingerprint or "").strip()
        lane_name = str(lane or "").strip()
        evidence = str(evidence_class or "").strip()
        event = str(event_id or "").strip()
        if not fp or not lane_name or not evidence or not event or not isinstance(payload, dict):
            raise WeatherPaperStoreError("PAPER_STORE_SIGNAL_IDENTITY_INVALID")
        probability = _finite_optional(model_probability, "PAPER_STORE_PROBABILITY_INVALID")
        cost = _finite_optional(entry_cost, "PAPER_STORE_ENTRY_COST_INVALID")
        gap = _finite_optional(raw_gap, "PAPER_STORE_GAP_INVALID")
        payout = _finite_optional(theoretical_payout, "PAPER_STORE_PAYOUT_INVALID")
        if probability is not None and not 0.0 <= probability <= 1.0:
            raise WeatherPaperStoreError("PAPER_STORE_PROBABILITY_INVALID")
        if cost is not None and not 0.0 < cost < 2.0:
            raise WeatherPaperStoreError("PAPER_STORE_ENTRY_COST_INVALID")
        if payout is not None and payout < 0.0:
            raise WeatherPaperStoreError("PAPER_STORE_PAYOUT_INVALID")
        at = time.time() if created_at is None else float(created_at)
        if not math.isfinite(at) or at < 0.0:
            raise WeatherPaperStoreError("PAPER_STORE_TIMESTAMP_INVALID")
        try:
            payload_text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        except (TypeError, ValueError):
            raise WeatherPaperStoreError("PAPER_STORE_PAYLOAD_INVALID") from None

        with self._conn() as db:
            try:
                cur = db.execute(
                    """
                    INSERT INTO weather_paper_signals(
                        store_version,fingerprint,lane,evidence_class,event_id,market_id,
                        side,token_id,model_probability,entry_cost,raw_gap,theoretical_payout,
                        execution_verified,payload_json,created_at,financial_authority,
                        automatic_order_placement
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,?,?,0,0)
                    """,
                    (
                        WEATHER_PAPER_STORE_VERSION, fp, lane_name, evidence, event,
                        None if market_id is None else str(market_id),
                        None if side is None else str(side),
                        None if token_id is None else str(token_id),
                        probability, cost, gap, payout, payload_text, at,
                    ),
                )
                return int(cur.lastrowid)
            except sqlite3.IntegrityError:
                return None

    def mark_telegram_sent(self, signal_id: int, message_id: int, *, sent_at: float | None = None) -> None:
        if isinstance(signal_id, bool) or int(signal_id) <= 0 or isinstance(message_id, bool) or int(message_id) <= 0:
            raise WeatherPaperStoreError("PAPER_STORE_TELEGRAM_RECEIPT_INVALID")
        at = time.time() if sent_at is None else float(sent_at)
        if not math.isfinite(at) or at < 0.0:
            raise WeatherPaperStoreError("PAPER_STORE_TIMESTAMP_INVALID")
        with self._conn() as db:
            cur = db.execute(
                "UPDATE weather_paper_signals SET telegram_message_id=?,telegram_sent_at=? WHERE id=?",
                (int(message_id), at, int(signal_id)),
            )
            if cur.rowcount != 1:
                raise WeatherPaperStoreError("PAPER_STORE_SIGNAL_NOT_FOUND")

    def recent(self, limit: int = 20) -> list[dict]:
        count = max(1, min(200, int(limit)))
        with self._conn() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM weather_paper_signals ORDER BY id DESC LIMIT ?", (count,)
            )]

    def summary(self) -> dict:
        with self._conn() as db:
            total = int(db.execute("SELECT COUNT(*) FROM weather_paper_signals").fetchone()[0])
            sent = int(db.execute(
                "SELECT COUNT(*) FROM weather_paper_signals WHERE telegram_message_id IS NOT NULL"
            ).fetchone()[0])
            open_count = int(db.execute(
                "SELECT COUNT(*) FROM weather_paper_signals WHERE status='OPEN'"
            ).fetchone()[0])
            rows = [dict(row) for row in db.execute(
                "SELECT lane,COUNT(*) AS n FROM weather_paper_signals GROUP BY lane ORDER BY lane"
            )]
        return {
            "version": WEATHER_PAPER_STORE_VERSION,
            "total": total,
            "telegram_sent": sent,
            "open": open_count,
            "by_lane": rows,
            "financial_authority": False,
            "automatic_order_placement": False,
        }
