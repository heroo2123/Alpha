from __future__ import annotations

import json
import math
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
RESOLVED_STATUSES = {"WON", "LOST", "RESOLVED_PARTIAL"}


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
                settlement_payout REAL,
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
                entry_source TEXT NOT NULL DEFAULT 'LEGACY_ALERT_ESTIMATE',
                execution_at TEXT,
                status TEXT NOT NULL DEFAULT 'OPEN',
                pnl REAL,
                settlement_payout REAL,
                created_at TEXT NOT NULL,
                resolved_at TEXT,
                FOREIGN KEY(signal_id) REFERENCES signals(id)
            );
            CREATE INDEX IF NOT EXISTS idx_manual_signal ON manual_trades(signal_id);
            CREATE TABLE IF NOT EXISTS bot_state (key TEXT PRIMARY KEY, value TEXT);
            """)

            signal_cols = {str(row[1]) for row in c.execute("PRAGMA table_info(signals)")}
            if "settlement_payout" not in signal_cols:
                c.execute("ALTER TABLE signals ADD COLUMN settlement_payout REAL")

            manual_cols = {str(row[1]) for row in c.execute("PRAGMA table_info(manual_trades)")}
            if "entry_source" not in manual_cols:
                c.execute(
                    "ALTER TABLE manual_trades ADD COLUMN entry_source TEXT NOT NULL DEFAULT 'LEGACY_ALERT_ESTIMATE'"
                )
            if "execution_at" not in manual_cols:
                c.execute("ALTER TABLE manual_trades ADD COLUMN execution_at TEXT")
            if "settlement_payout" not in manual_cols:
                c.execute("ALTER TABLE manual_trades ADD COLUMN settlement_payout REAL")

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
        """Legacy API intentionally disabled: quote snapshots are not executions."""
        raise RuntimeError(
            "synthetic immediate settlement is disabled; structural quote math cannot be booked as realized P&L"
        )

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

    @staticmethod
    def _status_for_payout(payout: float) -> str:
        if payout >= 1.0 - 1e-9:
            return "WON"
        if payout <= 1e-9:
            return "LOST"
        return "RESOLVED_PARTIAL"

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

    def resolve_payout(self, signal_id: int, payout: float, stake: float) -> dict | None:
        """Resolve using the actual final payout of the selected token (0..1).

        A disputed/partial market such as a 0.5 payout is therefore neither silently
        converted to a full loss nor treated as a normal win. P&L uses that exact
        payout vector component.
        """
        if isinstance(payout, bool) or not math.isfinite(float(payout)):
            raise ValueError("settlement payout must be finite")
        payout_f = float(payout)
        if payout_f < 0.0 or payout_f > 1.0:
            raise ValueError("settlement payout must be between 0 and 1")
        if isinstance(stake, bool) or not math.isfinite(float(stake)) or float(stake) <= 0:
            raise ValueError("stake must be positive and finite")

        with self._lock, self._conn() as c:
            row = c.execute("SELECT entry_cost FROM signals WHERE id=?", (signal_id,)).fetchone()
            if not row or row["entry_cost"] is None:
                return None
            cost = float(row["entry_cost"])
            if not math.isfinite(cost) or cost <= 0:
                return None
            shares = float(stake) / cost
            pnl = shares * payout_f - float(stake)
            now = datetime.now(timezone.utc).isoformat()
            status = self._status_for_payout(payout_f)
            c.execute(
                "UPDATE signals SET status=?, pnl=?, settlement_payout=?, resolved_at=? WHERE id=?",
                (status, pnl, payout_f, now, signal_id),
            )
            self._resolve_manual_conn(c, signal_id, payout_f, now)
            return {"status": status, "payout": payout_f, "pnl": pnl}

    def resolve(self, signal_id: int, won: bool, stake: float) -> None:
        """Compatibility wrapper for old tests/callers; new code should pass payout."""
        self.resolve_payout(signal_id, 1.0 if won else 0.0, stake)

    def record_manual(self, signal_id: int, stake: float, actual_entry_cost: float | None = None) -> dict:
        """Record one user-reported execution while the source alert is unresolved.

        ``actual_entry_cost`` is what the user really paid per $1 payout unit for a
        single-leg trade, or the combined cost per complete payout bundle for a
        multi-leg trade. It is intentionally required. The execution is recorded OPEN
        and can only acquire P&L from a *later* settlement write. Once the signal has
        any resolved/quarantined terminal state or known settlement payout, a new
        manual trade is rejected: retrospective entry after the answer is known would
        be look-ahead, not execution evidence.

        A second USER_REPORTED_EXECUTION for the same alert is also rejected. The
        command surface models one manually executed position per TRADE NOW alert;
        repeated /took commands must not double-count one fill.
        """
        if isinstance(stake, bool) or not math.isfinite(float(stake)) or float(stake) <= 0:
            raise ValueError("stake must be positive and finite")
        if actual_entry_cost is None:
            raise ValueError("actual executed cost is required; do not use the old alert quote")
        if isinstance(actual_entry_cost, bool) or not math.isfinite(float(actual_entry_cost)) or float(actual_entry_cost) <= 0:
            raise ValueError("actual executed cost must be positive and finite")

        stake_f = float(stake)
        cost_f = float(actual_entry_cost)
        with self._lock, self._conn() as c:
            sig = c.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
            if not sig or sig["confidence"] != "ACTIONABLE":
                raise ValueError("unknown/non-actionable alert id")
            if self._sports_pre_fix(dict(sig)):
                raise ValueError("that sports alert came from the pre-fix home/away mapping bug and is quarantined")

            signal_status = str(sig["status"] or "")
            if (
                signal_status != "OPEN"
                or sig["settlement_payout"] is not None
                or sig["resolved_at"] is not None
            ):
                if signal_status in RESOLVED_STATUSES or sig["settlement_payout"] is not None:
                    raise ValueError("cannot record an execution after the alert has settled; retrospective P&L is prohibited")
                raise ValueError("cannot record an execution for an alert that is no longer open")

            existing = c.execute(
                """
                SELECT id FROM manual_trades
                WHERE signal_id=? AND entry_source='USER_REPORTED_EXECUTION'
                ORDER BY id LIMIT 1
                """,
                (signal_id,),
            ).fetchone()
            if existing:
                raise ValueError("an actual execution is already recorded for this alert")

            now = datetime.now(timezone.utc).isoformat()
            cur = c.execute(
                """
                INSERT INTO manual_trades(
                    signal_id,stake,entry_cost,entry_source,execution_at,status,pnl,
                    settlement_payout,created_at,resolved_at
                ) VALUES(?,?,?,'USER_REPORTED_EXECUTION',?,'OPEN',NULL,NULL,?,NULL)
                """,
                (signal_id, stake_f, cost_f, now, now),
            )
            return {
                "id": int(cur.lastrowid), "signal_id": signal_id, "stake": stake_f,
                "entry_cost": cost_f, "entry_source": "USER_REPORTED_EXECUTION",
                "status": "OPEN", "pnl": None, "settlement_payout": None,
            }

    def _resolve_manual_conn(self, c, signal_id: int, payout: float, now: str) -> None:
        rows = c.execute(
            """
            SELECT id,stake,entry_cost FROM manual_trades
            WHERE signal_id=? AND status='OPEN' AND entry_source='USER_REPORTED_EXECUTION'
            """,
            (signal_id,),
        ).fetchall()
        status = self._status_for_payout(payout)
        for row in rows:
            stake = float(row["stake"])
            cost = float(row["entry_cost"])
            pnl = stake / cost * payout - stake
            c.execute(
                "UPDATE manual_trades SET status=?,pnl=?,settlement_payout=?,resolved_at=? WHERE id=?",
                (status, pnl, payout, now, row["id"]),
            )

    @staticmethod
    def _resolved_return(row: dict) -> float | None:
        """Return per-$1 paper return from entry cost and actual settlement payout."""
        status = str(row.get("status") or "")
        if status not in RESOLVED_STATUSES:
            return None
        try:
            cost = float(row.get("entry_cost") or 0.0)
        except (TypeError, ValueError):
            return None
        if cost <= 0 or not math.isfinite(cost):
            return None
        raw_payout = row.get("settlement_payout")
        if raw_payout is None:
            # Backward-compatible audit of historical rows only. New settlements
            # always persist the payout explicitly.
            payout = 1.0 if status == "WON" else 0.0 if status == "LOST" else None
            if payout is None:
                return None
        else:
            try:
                payout = float(raw_payout)
            except (TypeError, ValueError):
                return None
        if not math.isfinite(payout) or payout < 0 or payout > 1:
            return None
        return payout / cost - 1.0

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
        directional_total = directional_resolved = directional_won = directional_lost = directional_partial = 0
        structural_actionable = 0
        research_unscored = 0
        experimental_total = experimental_resolved = 0
        experimental_won = experimental_lost = experimental_partial = 0
        experimental_pnl = 0.0
        bug_total = bug_resolved = 0
        bug_pnl = 0.0

        for detector, items in groups.items():
            actionable = sum(str(r.get("confidence")) == "ACTIONABLE" for r in items)
            watch = sum(str(r.get("confidence")) == "WATCH" for r in items)
            legacy = sum(str(r.get("confidence")) == "LEGACY_THEORETICAL" or str(r.get("status")) == "LEGACY_THEORETICAL" for r in items)
            won = sum(str(r.get("status")) == "WON" for r in items)
            lost = sum(str(r.get("status")) == "LOST" for r in items)
            partial = sum(str(r.get("status")) == "RESOLVED_PARTIAL" for r in items)
            resolved = won + lost + partial
            open_ = sum(str(r.get("status")) == "OPEN" for r in items)
            pnl = sum(float(r.get("pnl") or 0.0) for r in items if str(r.get("status")) in RESOLVED_STATUSES)
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
                experimental_partial += partial
                experimental_pnl += pnl
            elif actionable > 0 or resolved > 0:
                evidence = "RESOLUTION_SCORED"
                scoreable = [r for r in items if str(r.get("confidence")) == "ACTIONABLE"]
                score_resolved = [r for r in scoreable if str(r.get("status")) in RESOLVED_STATUSES]
                directional_total += len(scoreable)
                directional_resolved += len(score_resolved)
                directional_won += sum(str(r.get("status")) == "WON" for r in score_resolved)
                directional_lost += sum(str(r.get("status")) == "LOST" for r in score_resolved)
                directional_partial += sum(str(r.get("status")) == "RESOLVED_PARTIAL" for r in score_resolved)
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
                "partial": partial,
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
        decided = directional_won + directional_lost
        win_rate = directional_won / decided if decided else None

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
            "partial": directional_partial,
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
                "directional_partial": directional_partial,
                "resolved_pnl": float(resolved_pnl),
                "win_rate": win_rate,
                "avg_resolved_return": avg_resolved_return,
                "structural_actionable_unverified": structural_actionable,
                "research_unscored": research_unscored,
                "experimental_total": experimental_total,
                "experimental_resolved": experimental_resolved,
                "experimental_won": experimental_won,
                "experimental_lost": experimental_lost,
                "experimental_partial": experimental_partial,
                "experimental_pnl": float(experimental_pnl),
                "known_bug_excluded": bug_total,
                "known_bug_resolved": bug_resolved,
                "known_bug_pnl": float(bug_pnl),
            },
            "detectors": detector_rows,
        }

    @staticmethod
    def _audit_time(value: object) -> datetime | None:
        if not value:
            return None
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(timezone.utc)

    def manual_stats(self) -> dict:
        """Return only prospective user-reported executions as valid manual P&L.

        Historical rows are never rewritten. Instead they are classified at read
        time and excluded from the valid totals when they came from a known-bug
        sports version, predate execution timestamps, were recorded at/after known
        settlement, or represent a structural multi-leg basket without per-leg fill
        evidence. This preserves the audit trail while preventing old rows from
        masquerading as trustworthy realized performance.
        """
        with self._conn() as c:
            rows = [dict(r) for r in c.execute(
                """
                SELECT
                    m.id,m.signal_id,m.stake,m.entry_cost,m.entry_source,m.execution_at,
                    m.status,m.pnl,m.settlement_payout,m.created_at,m.resolved_at,
                    s.detector AS signal_detector,s.metadata AS signal_metadata,
                    s.status AS signal_status,s.resolved_at AS signal_resolved_at
                FROM manual_trades m
                LEFT JOIN signals s ON s.id=m.signal_id
                ORDER BY m.id
                """
            )]

        valid: list[dict] = []
        legacy_excluded = 0
        known_bug_excluded = 0
        timing_unknown_excluded = 0
        retrospective_excluded = 0
        structural_unverified_excluded = 0
        orphan_excluded = 0

        for row in rows:
            if str(row.get("entry_source") or "") != "USER_REPORTED_EXECUTION":
                legacy_excluded += 1
                continue
            detector = str(row.get("signal_detector") or "")
            if not detector:
                orphan_excluded += 1
                continue
            signal_like = {
                "detector": detector,
                "metadata": row.get("signal_metadata"),
            }
            if self._sports_pre_fix(signal_like):
                known_bug_excluded += 1
                continue
            execution_at = self._audit_time(row.get("execution_at"))
            if execution_at is None:
                timing_unknown_excluded += 1
                continue
            signal_resolved_at = self._audit_time(row.get("signal_resolved_at"))
            if row.get("signal_resolved_at") and signal_resolved_at is None:
                timing_unknown_excluded += 1
                continue
            if signal_resolved_at is not None and execution_at >= signal_resolved_at:
                retrospective_excluded += 1
                continue
            if detector in STRUCTURAL_DETECTORS:
                # A combined user-entered cost is useful personal history, but it is
                # not evidence that every required leg/quantity/fee filled. Keep it
                # visible only as an exclusion until per-leg fill records exist.
                structural_unverified_excluded += 1
                continue
            valid.append(row)

        won = sum(str(row.get("status") or "") == "WON" for row in valid)
        lost = sum(str(row.get("status") or "") == "LOST" for row in valid)
        partial = sum(str(row.get("status") or "") == "RESOLVED_PARTIAL" for row in valid)
        open_ = sum(str(row.get("status") or "") == "OPEN" for row in valid)
        stake = sum(float(row.get("stake") or 0.0) for row in valid)
        pnl = sum(float(row.get("pnl") or 0.0) for row in valid if row.get("pnl") is not None)
        excluded_total = (
            legacy_excluded + known_bug_excluded + timing_unknown_excluded
            + retrospective_excluded + structural_unverified_excluded + orphan_excluded
        )
        return {
            "total": len(valid),
            "won": won,
            "lost": lost,
            "partial": partial,
            "open": open_,
            "stake": float(stake),
            "pnl": float(pnl),
            "legacy_excluded": legacy_excluded,
            "known_bug_excluded": known_bug_excluded,
            "timing_unknown_excluded": timing_unknown_excluded,
            "retrospective_excluded": retrospective_excluded,
            "structural_unverified_excluded": structural_unverified_excluded,
            "orphan_excluded": orphan_excluded,
            "excluded_total": excluded_total,
        }

    def recent(self, limit: int = 10):
        with self._conn() as c:
            return [dict(r) for r in c.execute("SELECT id,detector,confidence,title,status,pnl,created_at FROM signals ORDER BY id DESC LIMIT ?", (limit,))]

    def recent_manual(self, limit: int = 10):
        with self._conn() as c:
            return [dict(r) for r in c.execute(
                """
                SELECT m.id,m.signal_id,m.stake,m.entry_cost,m.entry_source,m.status,m.pnl,s.title
                FROM manual_trades m JOIN signals s ON s.id=m.signal_id
                ORDER BY m.id DESC LIMIT ?
                """,
                (limit,),
            )]

    def get_state(self, key: str, default: str = "") -> str:
        with self._conn() as c:
            r = c.execute("SELECT value FROM bot_state WHERE key=?", (key,)).fetchone()
            return r[0] if r else default

    def set_state(self, key: str, value: str) -> None:
        with self._lock, self._conn() as c:
            c.execute("INSERT INTO bot_state(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
