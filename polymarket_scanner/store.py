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
            CREATE TABLE IF NOT EXISTS manual_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL,
                stake REAL NOT NULL,
                entry_cost REAL NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                pnl REAL,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                FOREIGN KEY(signal_id) REFERENCES signals(id)
            );
            CREATE INDEX IF NOT EXISTS idx_manual_signal ON manual_trades(signal_id);
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

    def get_signal(self, signal_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
            return dict(row) if row else None

    def settle_immediate(self, signal_id: int, stake: float) -> None:
        with self._lock, self._conn() as c:
            row = c.execute("SELECT entry_cost,theoretical_payout FROM signals WHERE id=?", (signal_id,)).fetchone()
            if not row or not row["entry_cost"] or not row["theoretical_payout"]:
                return
            shares = stake / float(row["entry_cost"])
            pnl = shares * float(row["theoretical_payout"]) - stake
            now = datetime.now(timezone.utc).isoformat()
            c.execute("UPDATE signals SET status='WON', pnl=?, resolved_at=? WHERE id=?", (pnl, now, signal_id))
            self._resolve_manual_conn(c, signal_id, True, float(row["theoretical_payout"]), now)

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
            now = datetime.now(timezone.utc).isoformat()
            c.execute("UPDATE signals SET status=?, pnl=?, resolved_at=? WHERE id=?", ("WON" if won else "LOST", pnl, now, signal_id))
            self._resolve_manual_conn(c, signal_id, won, 1.0, now)

    def record_manual(self, signal_id: int, stake: float) -> dict:
        if stake <= 0:
            raise ValueError("stake must be positive")
        with self._lock, self._conn() as c:
            sig = c.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
            if not sig or sig["confidence"] != "ACTIONABLE" or not sig["entry_cost"]:
                raise ValueError("unknown/non-actionable alert id")
            now = datetime.now(timezone.utc).isoformat()
            status = "OPEN"
            pnl = None
            resolved_at = None
            if sig["status"] == "WON":
                payout = float(sig["theoretical_payout"] or 1.0)
                pnl = stake / float(sig["entry_cost"]) * payout - stake
                status = "WON"; resolved_at = now
            elif sig["status"] == "LOST":
                pnl = -stake; status = "LOST"; resolved_at = now
            cur = c.execute("INSERT INTO manual_trades(signal_id,stake,entry_cost,status,pnl,created_at,resolved_at) VALUES(?,?,?,?,?,?,?)",
                            (signal_id, stake, float(sig["entry_cost"]), status, pnl, now, resolved_at))
            return {"id": int(cur.lastrowid), "signal_id": signal_id, "stake": stake, "entry_cost": float(sig["entry_cost"]), "status": status, "pnl": pnl}

    def _resolve_manual_conn(self, c, signal_id: int, won: bool, payout: float, now: str) -> None:
        rows = c.execute("SELECT id,stake,entry_cost FROM manual_trades WHERE signal_id=? AND status='OPEN'", (signal_id,)).fetchall()
        for row in rows:
            stake = float(row["stake"]); cost = float(row["entry_cost"])
            pnl = stake / cost * payout - stake if won else -stake
            c.execute("UPDATE manual_trades SET status=?,pnl=?,resolved_at=? WHERE id=?", ("WON" if won else "LOST", pnl, now, row["id"]))

    def stats(self) -> dict:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM signals WHERE confidence='ACTIONABLE'").fetchone()[0]
            won = c.execute("SELECT COUNT(*) FROM signals WHERE confidence='ACTIONABLE' AND status='WON'").fetchone()[0]
            lost = c.execute("SELECT COUNT(*) FROM signals WHERE confidence='ACTIONABLE' AND status='LOST'").fetchone()[0]
            open_ = c.execute("SELECT COUNT(*) FROM signals WHERE status='OPEN' AND confidence='ACTIONABLE'").fetchone()[0]
            pnl = c.execute("SELECT COALESCE(SUM(pnl),0) FROM signals WHERE confidence='ACTIONABLE'").fetchone()[0]
            by_detector = [dict(r) for r in c.execute("SELECT detector, COUNT(*) n, COALESCE(SUM(pnl),0) pnl FROM signals WHERE confidence='ACTIONABLE' GROUP BY detector ORDER BY pnl DESC")]
            return {"total": total, "won": won, "lost": lost, "open": open_, "pnl": float(pnl), "by_detector": by_detector}

    def manual_stats(self) -> dict:
        with self._conn() as c:
            total = c.execute("SELECT COUNT(*) FROM manual_trades").fetchone()[0]
            won = c.execute("SELECT COUNT(*) FROM manual_trades WHERE status='WON'").fetchone()[0]
            lost = c.execute("SELECT COUNT(*) FROM manual_trades WHERE status='LOST'").fetchone()[0]
            open_ = c.execute("SELECT COUNT(*) FROM manual_trades WHERE status='OPEN'").fetchone()[0]
            stake = c.execute("SELECT COALESCE(SUM(stake),0) FROM manual_trades").fetchone()[0]
            pnl = c.execute("SELECT COALESCE(SUM(pnl),0) FROM manual_trades").fetchone()[0]
            return {"total": total, "won": won, "lost": lost, "open": open_, "stake": float(stake), "pnl": float(pnl)}

    def recent(self, limit: int = 10):
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT id,detector,confidence,title,status,pnl,created_at FROM signals ORDER BY id DESC LIMIT ?", (limit,))]

    def recent_manual(self, limit: int = 10):
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT m.id,m.signal_id,m.stake,m.status,m.pnl,s.title FROM manual_trades m JOIN signals s ON s.id=m.signal_id ORDER BY m.id DESC LIMIT ?", (limit,))]

    def get_state(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            r = c.execute("SELECT value FROM bot_state WHERE key=?", (key,)).fetchone()
            return r[0] if r else default

    def set_state(self, key: str, value: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO bot_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
