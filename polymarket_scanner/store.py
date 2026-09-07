from __future__ import annotations

import json
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .models import Signal


STRUCTURAL_DETECTORS = {"binary_buy_both", "neg_risk_underround", "nested_threshold_arb"}
EXPERIMENTAL_RESOLUTION_DETECTORS = {"weather_friend_lock"}


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
        """Return paper signals whose selected token can be objectively resolved.

        Normal ACTIONABLE single-market signals remain scoreable. The friend-style
        weather lane is intentionally WATCH-only, but we still resolve it after the
        market closes so its heuristic can accumulate honest experimental evidence.
        Structural multi-leg arbitrage is explicitly excluded: settlement does not
        prove that every quoted leg was actually executable/fillable.
        """
        structural = tuple(sorted(STRUCTURAL_DETECTORS))
        qmarks = ",".join("?" for _ in structural)
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                f"""
                SELECT * FROM signals
                WHERE status='OPEN' AND market_id IS NOT NULL
                  AND (
                    (confidence='ACTIONABLE' AND detector NOT IN ({qmarks}))
                    OR detector='weather_friend_lock'
                  )
                ORDER BY id
                """,
                structural,
            )]

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

    @staticmethod
    def _meta(raw: object) -> dict:
        if isinstance(raw, dict):
            return raw
        if not raw:
            return {}
        try:
            value = json.loads(str(raw))
            return value if isinstance(value, dict) else {}
        except Exception:
            return {}

    @staticmethod
    def _resolved_return(row: dict) -> float | None:
        """Return per-$1 paper return from the stored executable-cost estimate."""
        status = str(row.get("status") or "")
        if status not in {"WON", "LOST"}:
            return None
        try:
            cost = float(row.get("entry_cost") or 0.0)
        except (TypeError, ValueError):
            return None
        if cost <= 0:
            return None
        return (1.0 / cost - 1.0) if status == "WON" else -1.0

    def stats(self) -> dict:
        """Evidence-based audit of every stored scanner signal.

        The legacy top-level keys are retained for compatibility with older clients.
        New ``audit`` and ``detectors`` fields deliberately separate resolution-
        scored directional ideas, execution-unverified structural opportunities,
        experimental WATCH outcomes, and research-only noise.
        """
        with self._conn() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM signals ORDER BY id")]

        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            groups[str(row.get("detector") or "unknown")].append(row)

        detector_rows: list[dict] = []
        resolved_pnl = 0.0
        directional_total = directional_resolved = directional_won = directional_lost = 0
        structural_actionable = 0
        research_unscored = 0
        experimental_total = experimental_resolved = 0

        for detector, items in groups.items():
            actionable = sum(str(r.get("confidence")) == "ACTIONABLE" for r in items)
            watch = sum(str(r.get("confidence")) == "WATCH" for r in items)
            legacy = sum(str(r.get("confidence")) == "LEGACY_THEORETICAL" or str(r.get("status")) == "LEGACY_THEORETICAL" for r in items)
            won = sum(str(r.get("status")) == "WON" for r in items)
            lost = sum(str(r.get("status")) == "LOST" for r in items)
            resolved = won + lost
            open_ = sum(str(r.get("status")) == "OPEN" for r in items)
            pnl = sum(float(r.get("pnl") or 0.0) for r in items if str(r.get("status")) in {"WON", "LOST"})
            edges = [float(r["edge"]) for r in items if r.get("edge") is not None]
            returns = [x for r in items if (x := self._resolved_return(r)) is not None]
            avg_edge = sum(edges) / len(edges) if edges else None
            avg_return = sum(returns) / len(returns) if returns else None

            visible = []
            for r in items:
                meta = self._meta(r.get("metadata"))
                try:
                    value = float(meta.get("max_visible_notional_usd"))
                except (TypeError, ValueError):
                    continue
                if value >= 0:
                    visible.append(value)
            avg_visible = sum(visible) / len(visible) if visible else None

            if detector in STRUCTURAL_DETECTORS:
                evidence = "EXECUTION_UNVERIFIED"
                structural_actionable += actionable
            elif detector in EXPERIMENTAL_RESOLUTION_DETECTORS:
                evidence = "EXPERIMENTAL_RESOLUTION"
                experimental_total += len(items)
                experimental_resolved += resolved
                directional_total += len(items)
                directional_resolved += resolved
                directional_won += won
                directional_lost += lost
                resolved_pnl += pnl
            elif actionable > 0 or resolved > 0:
                evidence = "RESOLUTION_SCORED"
                scoreable = [r for r in items if str(r.get("confidence")) == "ACTIONABLE"]
                score_resolved = [r for r in scoreable if str(r.get("status")) in {"WON", "LOST"}]
                directional_total += len(scoreable)
                directional_resolved += len(score_resolved)
                directional_won += sum(str(r.get("status")) == "WON" for r in score_resolved)
                directional_lost += sum(str(r.get("status")) == "LOST" for r in score_resolved)
                resolved_pnl += sum(float(r.get("pnl") or 0.0) for r in score_resolved)
            else:
                evidence = "RESEARCH_UNSCORED"
                research_unscored += len(items)

            detector_rows.append({
                "detector": detector,
                "evidence": evidence,
                "n": len(items),
                "actionable": actionable,
                "watch": watch,
                "legacy": legacy,
                "resolved": resolved,
                "won": won,
                "lost": lost,
                "open": open_,
                "pnl": float(pnl),
                "avg_edge": avg_edge,
                "avg_return": avg_return,
                "avg_visible_notional": avg_visible,
            })

        # Put actual resolution evidence first, then structural/actionable evidence,
        # then large research-only buckets. This keeps the Telegram report useful.
        evidence_rank = {
            "RESOLUTION_SCORED": 0,
            "EXPERIMENTAL_RESOLUTION": 1,
            "EXECUTION_UNVERIFIED": 2,
            "RESEARCH_UNSCORED": 3,
        }
        detector_rows.sort(key=lambda r: (evidence_rank.get(r["evidence"], 9), -int(r["resolved"]), -int(r["n"]), r["detector"]))

        all_alerts = len(rows)
        actionable_total = sum(str(r.get("confidence")) == "ACTIONABLE" for r in rows)
        watch_total = sum(str(r.get("confidence")) == "WATCH" for r in rows)
        legacy_total = sum(str(r.get("confidence")) == "LEGACY_THEORETICAL" or str(r.get("status")) == "LEGACY_THEORETICAL" for r in rows)
        win_rate = directional_won / directional_resolved if directional_resolved else None

        resolved_returns = []
        for r in rows:
            detector = str(r.get("detector") or "")
            eligible = (
                detector in EXPERIMENTAL_RESOLUTION_DETECTORS
                or (str(r.get("confidence")) == "ACTIONABLE" and detector not in STRUCTURAL_DETECTORS)
            )
            if eligible:
                value = self._resolved_return(r)
                if value is not None:
                    resolved_returns.append(value)
        avg_resolved_return = sum(resolved_returns) / len(resolved_returns) if resolved_returns else None

        # Legacy compatibility: /stats clients historically expect these keys to
        # describe ACTIONABLE directional paper performance. Structural quote math
        # never contributes to won/lost/P&L here.
        legacy_by_detector = [
            {"detector": r["detector"], "n": r["actionable"], "pnl": r["pnl"]}
            for r in detector_rows if r["actionable"] > 0
        ]
        return {
            "total": actionable_total,
            "won": directional_won,
            "lost": directional_lost,
            "open": max(0, directional_total - directional_resolved) + structural_actionable,
            "pnl": float(resolved_pnl),
            "by_detector": legacy_by_detector,
            "audit": {
                "all_alerts": all_alerts,
                "actionable": actionable_total,
                "watch": watch_total,
                "legacy_excluded": legacy_total,
                "directional_total": directional_total,
                "directional_resolved": directional_resolved,
                "directional_open": max(0, directional_total - directional_resolved),
                "directional_won": directional_won,
                "directional_lost": directional_lost,
                "resolved_pnl": float(resolved_pnl),
                "win_rate": win_rate,
                "avg_resolved_return": avg_resolved_return,
                "structural_actionable_unverified": structural_actionable,
                "research_unscored": research_unscored,
                "experimental_total": experimental_total,
                "experimental_resolved": experimental_resolved,
            },
            "detectors": detector_rows,
        }

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
