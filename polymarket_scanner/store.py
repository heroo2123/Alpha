from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path

from .models import Signal


class Store:
    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _conn(self):
        c = sqlite3.connect(self.path, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def _init(self):
        with self._conn() as c:
            c.executescript("""
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT UNIQUE,
                detector TEXT NOT NULL,
                confidence TEXT NOT NULL,
                event_id TEXT,
                market_id TEXT,
                title TEXT,
                detail TEXT,
                url TEXT,
                edge REAL,
                entry_cost REAL,
                theoretical_payout REAL,
                token_ids TEXT,
                metadata TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN',
                pnl REAL,
                created_at TEXT NOT NULL,
                resolved_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_signals_status ON signals(status);
            CREATE INDEX IF NOT EXISTS idx_signals_detector ON signals(detector);
            CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT);
            """)

    def save_signal(self, s: Signal) -> int | None:
        with self._lock, self._conn() as c:
            try:
                cur = c.execute("""
                    INSERT INTO signals(fingerprint,detector,confidence,event_id,market_id,title,detail,url,edge,entry_cost,theoretical_payout,token_ids,metadata,created_at)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """, (s.fingerprint(), s.detector, s.confidence, s.event_id, s.market_id, s.title, s.detail, s.url, s.edge, s.entry_cost, s.theoretical_payout, json.dumps(s.token_ids), json.dumps(s.metadata), s.created_at.isoformat()))
                return int(cur.lastrowid)
            except sqlite3.IntegrityError:
                return None

    def settle_immediate(self, signal_id: int, stake: float) -> None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT entry_cost,theoretical_payout FROM signals WHERE id=?", (signal_id,)).fetchone()
            if not row or not row["entry_cost"] or not row["theoretical_payout"]:
                return
            shares = stake / float(row["entry_cost"])
            pnl = shares * float(row["theoretical_payout"]) - stake
            c.execute("UPDATE signals SET status='WON', pnl=?, resolved_at=? WHERE id=?", (pnl, datetime.now(timezone.utc).isoformat(), signal_id))

    def open_directional(self):
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT * FROM signals WHERE status='OPEN' AND market_id IS NOT NULL AND confidence='ACTIONABLE' ORDER BY id")]

    def resolve(self, signal_id: int, won: bool, stake: float) -> None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT entry_cost FROM signals WHERE id=?", (signal_id,)).fetchone()
            if not row or not row["entry_cost"]:
                return
            cost = float(row["entry_cost"])
            shares = stake / cost
            pnl = shares - stake if won else -stake
            c.execute("UPDATE signals SET status=?, pnl=?, resolved_at=? WHERE id=?", ("WON" if won else "LOST", pnl, datetime.now(timezone.utc).isoformat(), signal_id))

    def stats(self) -> dict:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) n FROM signals WHERE confidence='ACTIONABLE'").fetchone()[0]
            won = c.execute("SELECT COUNT(*) FROM signals WHERE status='WON'").fetchone()[0]
            lost = c.execute("SELECT COUNT(*) FROM signals WHERE status='LOST'").fetchone()[0]
            open_ = c.execute("SELECT COUNT(*) FROM signals WHERE status='OPEN' AND confidence='ACTIONABLE'").fetchone()[0]
            pnl = c.execute("SELECT COALESCE(SUM(pnl),0) FROM signals").fetchone()[0]
            by_detector = [dict(r) for r in c.execute("SELECT detector, COUNT(*) n, COALESCE(SUM(pnl),0) pnl FROM signals WHERE confidence='ACTIONABLE' GROUP BY detector ORDER BY pnl DESC")]
            return {"total": total, "won": won, "lost": lost, "open": open_, "pnl": float(pnl), "by_detector": by_detector}

    def recent(self, limit: int = 10):
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT id,detector,confidence,title,status,pnl,created_at FROM signals ORDER BY id DESC LIMIT ?", (limit,))]

    def get_state(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            r = c.execute("SELECT value FROM bot_state WHERE key=?", (key,)).fetchone()
            return r[0] if r else default

    def set_state(self, key: str, value: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO bot_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
