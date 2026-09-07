from __future__ import annotations

import json
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .models import Signal


STRUCTURAL_DETECTORS = {"binary_buy_both", "neg_risk_underround", "nested_threshold_arb"}
EXPERIMENTAL_RESOLUTION_DETECTORS = {"weather_friend_lock", "weather_late_lock"}
SPORTS_MAPPING_VERSION = "home_away_v2"
SPORTS_PRE_FIX_GROUP = "sports_result_lag_PRE_FIX_BUG"


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

    @classmethod
    def _sports_pre_fix(cls, row: dict) -> bool:
        if str(row.get("detector") or "") != "sports_result_lag":
            return False
        return cls._meta(row.get("metadata")).get("sports_mapping_version") != SPORTS_MAPPING_VERSION

    def open_directional(self):
        """Return signals whose selected token can be objectively resolved.

        Structural multi-leg quote math is excluded because settlement cannot prove
        fills. Both weather lanes are scored experimentally. Sports alerts created
        before the explicit home/away mapping fix are quarantined and are not allowed
        to accumulate more apparent strategy results.
        """
        with self._conn() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM signals WHERE status='OPEN' AND market_id IS NOT NULL ORDER BY id"
            )]
        out = []
        for row in rows:
            detector = str(row.get("detector") or "")
            if detector in STRUCTURAL_DETECTORS or self._sports_pre_fix(row):
                continue
            if str(row.get("confidence") or "") == "ACTIONABLE" or detector in EXPERIMENTAL_RESOLUTION_DETECTORS:
                out.append(row)
        return out

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
            if self._sports_pre_fix(dict(sig)):
                raise ValueError("that sports alert came from the pre-fix home/away mapping bug and is quarantined")
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

        Valid resolution evidence, experimental weather evidence, structural quote
        math, research WATCHs, legacy synthetic rows, and the known pre-fix sports
        implementation bug are deliberately reported as different evidence classes.
        """
        with self._conn() as c:
            rows = [dict(r) for r in c.execute("SELECT * FROM signals ORDER BY id")]

        groups: dict[str, list[dict]] = defaultdict(list)
        for row in rows:
            detector = str(row.get("detector") or "unknown")
            if self._sports_pre_fix(row):
                detector = SPORTS_PRE_FIX_GROUP
            groups[detector].append(row)

        detector_rows: list[dict] = []
        resolved_pnl = 0.0
        directional_total = directional_resolved = directional_won = directional_lost = 0
        structural_actionable = 0
        research_unscored = 0
        experimental_total = experimental_resolved = 0
        experimental_won = experimental_lost = 0
        experimental_pnl = 0.0
        bug_total = bug_resolved = 0
        bug_pnl = 0.0

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

            if detector == SPORTS_PRE_FIX_GROUP:
                evidence = "KNOWN_BUG_EXCLUDED"
                bug_total += len(items)
                bug_resolved += resolved
                bug_pnl += pnl
            elif detector in STRUCTURAL_DETECTORS:
                evidence = "EXECUTION_UNVERIFIED"
                structural_actionable += actionable
            elif detector in EXPERIMENTAL_RESOLUTION_DETECTORS:
                evidence = "EXPERIMENTAL_RESOLUTION"
                experimental_total += len(items)
                experimental_resolved += resolved
                experimental_won += won
                experimental_lost += lost
                experimental_pnl += pnl
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

        evidence_rank = {
            "RESOLUTION_SCORED": 0,
            "EXPERIMENTAL_RESOLUTION": 1,
            "KNOWN_BUG_EXCLUDED": 2,
            "EXECUTION_UNVERIFIED": 3,
            "RESEARCH_UNSCORED": 4,
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
                str(r.get("confidence")) == "ACTIONABLE"
                and detector not in STRUCTURAL_DETECTORS
                and detector not in EXPERIMENTAL_RESOLUTION_DETECTORS
                and not self._sports_pre_fix(r)
            )
            if eligible:
                value = self._resolved_return(r)
                if value is not None:
                    resolved_returns.append(value)
        avg_resolved_return = sum(resolved_returns) / len(resolved_returns) if resolved_returns else None

        legacy_by_detector = [
            {"detector": r["detector"], "n": r["actionable"], "pnl": r["pnl"]}
            for r in detector_rows
            if r["actionable"] > 0 and r["evidence"] not in {"KNOWN_BUG_EXCLUDED", "EXPERIMENTAL_RESOLUTION"}
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
                "experimental_won": experimental_won,
                "experimental_lost": experimental_lost,
                "experimental_pnl": float(experimental_pnl),
                "known_bug_excluded": bug_total,
                "known_bug_resolved": bug_resolved,
                "known_bug_pnl": float(bug_pnl),
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