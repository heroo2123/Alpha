from __future__ import annotations

"""Automatic simulated-position ledger for weather LIVE PAPER alerts.

A position is created only for a signal that Telegram explicitly accepted.  The
entry price is the exact cost captured in that signal; sizing targets a small fixed
paper stake and is capped by the visible top-of-book quantity captured with the
alert.  Nothing in this module can place, sign or cancel an order.
"""

import json
import math
import os
import sqlite3
import time
from pathlib import Path


WEATHER_PAPER_POSITION_VERSION = "weather_paper_positions_v1_sent_signal_exact_quote_visible_cap"
RESOLVED_STATUSES = {"WON", "LOST", "RESOLVED_PARTIAL"}


class WeatherPaperPositionError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherPaperPositionError(code)
    number = float(value)
    if not math.isfinite(number):
        raise WeatherPaperPositionError(code)
    return number


def _payload(raw: object) -> dict:
    if isinstance(raw, dict):
        return dict(raw)
    try:
        value = json.loads(str(raw or ""))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _json(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise WeatherPaperPositionError("PAPER_POSITION_JSON_INVALID") from None


class WeatherPaperPositionStore:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.is_absolute():
            raise WeatherPaperPositionError("PAPER_POSITION_DB_NOT_ABSOLUTE")
        self.path.parent.mkdir(parents=True, exist_ok=True)
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
                CREATE TABLE IF NOT EXISTS weather_paper_positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position_version TEXT NOT NULL,
                    signal_id INTEGER NOT NULL UNIQUE,
                    lane TEXT NOT NULL,
                    evidence_class TEXT NOT NULL,
                    position_kind TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    market_id TEXT,
                    token_id TEXT,
                    side TEXT NOT NULL,
                    title TEXT NOT NULL,
                    target_stake_usd REAL NOT NULL,
                    entry_cost_per_unit REAL NOT NULL,
                    visible_units REAL NOT NULL,
                    filled_units REAL NOT NULL,
                    capital_used REAL NOT NULL,
                    maximum_payout REAL NOT NULL,
                    legs_json TEXT NOT NULL,
                    quote_observed_at REAL,
                    telegram_sent_at REAL NOT NULL,
                    opened_at REAL NOT NULL,
                    status TEXT NOT NULL,
                    no_fill_reason TEXT,
                    settlement_payout_per_unit REAL,
                    proceeds REAL,
                    pnl REAL,
                    roi REAL,
                    settled_at REAL,
                    settlement_evidence_json TEXT,
                    settlement_telegram_message_id INTEGER,
                    financial_authority INTEGER NOT NULL DEFAULT 0 CHECK(financial_authority=0),
                    automatic_order_placement INTEGER NOT NULL DEFAULT 0 CHECK(automatic_order_placement=0),
                    FOREIGN KEY(signal_id) REFERENCES weather_paper_signals(id)
                );
                CREATE INDEX IF NOT EXISTS idx_weather_paper_positions_status
                    ON weather_paper_positions(status, opened_at);
                CREATE INDEX IF NOT EXISTS idx_weather_paper_positions_lane
                    ON weather_paper_positions(lane, opened_at);
                CREATE TABLE IF NOT EXISTS weather_paper_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
        if self.path.exists() and self.path.stat().st_mode & 0o077:
            os.chmod(self.path, 0o600)

    def get_state(self, key: str, default: str = "") -> str:
        with self._conn() as db:
            row = db.execute("SELECT value FROM weather_paper_state WHERE key=?", (str(key),)).fetchone()
        return str(row["value"]) if row else str(default)

    def set_state(self, key: str, value: object) -> None:
        with self._conn() as db:
            db.execute(
                "INSERT INTO weather_paper_state(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(key), str(value)),
            )

    def _position_spec(self, signal: dict, target_stake_usd: float) -> dict | None:
        if signal.get("telegram_message_id") is None or signal.get("telegram_sent_at") is None:
            return None
        target = _finite(target_stake_usd, "PAPER_POSITION_STAKE_INVALID")
        if target <= 0.0:
            raise WeatherPaperPositionError("PAPER_POSITION_STAKE_INVALID")
        cost = _finite(signal.get("entry_cost"), "PAPER_POSITION_ENTRY_COST_INVALID")
        if cost <= 0.0:
            raise WeatherPaperPositionError("PAPER_POSITION_ENTRY_COST_INVALID")

        data = _payload(signal.get("payload_json"))
        lane = str(signal.get("lane") or "").strip()
        evidence = str(signal.get("evidence_class") or "").strip()
        event_id = str(signal.get("event_id") or "").strip()
        title = str(data.get("event_title") or event_id or f"Signal {signal.get('id')}").strip()
        sent_at = _finite(signal.get("telegram_sent_at"), "PAPER_POSITION_SENT_AT_INVALID")
        quote_at_raw = data.get("quote_observed_at", data.get("source_timestamp"))
        quote_at = None
        if isinstance(quote_at_raw, (int, float)) and not isinstance(quote_at_raw, bool):
            q = float(quote_at_raw)
            if math.isfinite(q) and q >= 0.0:
                quote_at = q

        if lane == "weather_forecast_raw_gap":
            market_id = str(signal.get("market_id") or "").strip()
            token_id = str(signal.get("token_id") or "").strip()
            side = str(signal.get("side") or "").strip().upper()
            visible = data.get("ask_size")
            if not market_id or not token_id or side not in {"YES", "NO"}:
                raise WeatherPaperPositionError("PAPER_POSITION_DIRECTIONAL_IDENTITY_INVALID")
            kind = "DIRECTIONAL"
            legs = [{"market_id": market_id, "token_id": token_id}]
        elif lane.startswith("weather_") and data.get("market_ids") and data.get("token_ids"):
            market_ids = [str(value) for value in data.get("market_ids") or []]
            token_ids = [str(value) for value in data.get("token_ids") or []]
            if not market_ids or len(market_ids) != len(token_ids) or any(not value for value in market_ids + token_ids):
                raise WeatherPaperPositionError("PAPER_POSITION_STRUCTURAL_IDENTITY_INVALID")
            market_id = None
            token_id = None
            side = "BASKET"
            visible = data.get("common_best_ask_shares")
            kind = "STRUCTURAL"
            legs = [
                {"market_id": mid, "token_id": tid}
                for mid, tid in zip(market_ids, token_ids)
            ]
        else:
            return None

        try:
            visible_f = float(visible)
        except (TypeError, ValueError, OverflowError):
            visible_f = 0.0
        if not math.isfinite(visible_f) or visible_f < 0.0:
            visible_f = 0.0

        requested_units = target / cost
        filled_units = min(requested_units, visible_f) if visible_f > 0.0 else 0.0
        if not math.isfinite(filled_units) or filled_units <= 0.0:
            status = "NO_FILL"
            filled_units = 0.0
            capital = 0.0
            no_fill = "NO_VISIBLE_TOP_OF_BOOK_CAPACITY"
        else:
            status = "OPEN"
            capital = filled_units * cost
            no_fill = None

        # One directional token pays at most $1/share. A certified structural bundle
        # is tracked per equal-share set and its final exact-token payouts are summed.
        maximum_payout = filled_units
        return {
            "lane": lane,
            "evidence_class": evidence,
            "position_kind": kind,
            "event_id": event_id,
            "market_id": market_id,
            "token_id": token_id,
            "side": side,
            "title": title,
            "target_stake_usd": target,
            "entry_cost_per_unit": cost,
            "visible_units": visible_f,
            "filled_units": filled_units,
            "capital_used": capital,
            "maximum_payout": maximum_payout,
            "legs_json": _json(legs),
            "quote_observed_at": quote_at,
            "telegram_sent_at": sent_at,
            "opened_at": sent_at,
            "status": status,
            "no_fill_reason": no_fill,
        }

    def ensure_position_for_signal(self, signal_id: int, target_stake_usd: float) -> dict | None:
        sid = int(signal_id)
        with self._conn() as db:
            existing = db.execute(
                "SELECT * FROM weather_paper_positions WHERE signal_id=?", (sid,)
            ).fetchone()
            if existing:
                return dict(existing)
            signal = db.execute(
                "SELECT * FROM weather_paper_signals WHERE id=?", (sid,)
            ).fetchone()
        if not signal:
            return None
        spec = self._position_spec(dict(signal), target_stake_usd)
        if spec is None:
            return None
        with self._conn() as db:
            try:
                cur = db.execute(
                    """
                    INSERT INTO weather_paper_positions(
                        position_version,signal_id,lane,evidence_class,position_kind,event_id,
                        market_id,token_id,side,title,target_stake_usd,entry_cost_per_unit,
                        visible_units,filled_units,capital_used,maximum_payout,legs_json,
                        quote_observed_at,telegram_sent_at,opened_at,status,no_fill_reason,
                        financial_authority,automatic_order_placement
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0)
                    """,
                    (
                        WEATHER_PAPER_POSITION_VERSION, sid, spec["lane"], spec["evidence_class"],
                        spec["position_kind"], spec["event_id"], spec["market_id"], spec["token_id"],
                        spec["side"], spec["title"], spec["target_stake_usd"], spec["entry_cost_per_unit"],
                        spec["visible_units"], spec["filled_units"], spec["capital_used"],
                        spec["maximum_payout"], spec["legs_json"], spec["quote_observed_at"],
                        spec["telegram_sent_at"], spec["opened_at"], spec["status"], spec["no_fill_reason"],
                    ),
                )
                position_id = int(cur.lastrowid)
            except sqlite3.IntegrityError:
                row = db.execute(
                    "SELECT * FROM weather_paper_positions WHERE signal_id=?", (sid,)
                ).fetchone()
                return dict(row) if row else None
            row = db.execute("SELECT * FROM weather_paper_positions WHERE id=?", (position_id,)).fetchone()
            return dict(row) if row else None

    def ensure_sent_positions(self, target_stake_usd: float) -> list[dict]:
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                """
                SELECT s.id
                FROM weather_paper_signals s
                LEFT JOIN weather_paper_positions p ON p.signal_id=s.id
                WHERE s.telegram_message_id IS NOT NULL AND p.id IS NULL
                ORDER BY s.id
                """
            )]
        created: list[dict] = []
        for row in rows:
            position = self.ensure_position_for_signal(int(row["id"]), target_stake_usd)
            if position is not None:
                created.append(position)
        return created

    @staticmethod
    def _decode_position(row: dict) -> dict:
        out = dict(row)
        try:
            legs = json.loads(str(out.get("legs_json") or "[]"))
        except Exception:
            legs = []
        out["legs"] = legs if isinstance(legs, list) else []
        return out

    def open_positions(self, limit: int = 200) -> list[dict]:
        count = max(1, min(1000, int(limit)))
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT * FROM weather_paper_positions WHERE status='OPEN' ORDER BY id LIMIT ?",
                (count,),
            )]
        return [self._decode_position(row) for row in rows]

    def recent_positions(self, limit: int = 10, *, resolved_only: bool = False) -> list[dict]:
        count = max(1, min(100, int(limit)))
        where = "WHERE status IN ('WON','LOST','RESOLVED_PARTIAL')" if resolved_only else ""
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                f"SELECT * FROM weather_paper_positions {where} ORDER BY id DESC LIMIT ?",
                (count,),
            )]
        return [self._decode_position(row) for row in rows]

    def recent_signals(self, limit: int = 10) -> list[dict]:
        count = max(1, min(100, int(limit)))
        with self._conn() as db:
            return [dict(row) for row in db.execute(
                """
                SELECT s.id,s.lane,s.event_id,s.market_id,s.side,s.entry_cost,s.raw_gap,
                       s.created_at,s.telegram_message_id,p.id AS position_id,p.status AS position_status
                FROM weather_paper_signals s
                LEFT JOIN weather_paper_positions p ON p.signal_id=s.id
                ORDER BY s.id DESC LIMIT ?
                """,
                (count,),
            )]

    def resolve_position(self, position_id: int, payout_per_unit: float, evidence: dict) -> dict | None:
        payout = _finite(payout_per_unit, "PAPER_POSITION_PAYOUT_INVALID")
        if payout < 0.0:
            raise WeatherPaperPositionError("PAPER_POSITION_PAYOUT_INVALID")
        now = time.time()
        with self._conn() as db:
            row = db.execute(
                "SELECT * FROM weather_paper_positions WHERE id=? AND status='OPEN'", (int(position_id),)
            ).fetchone()
            if not row:
                return None
            item = dict(row)
            units = float(item["filled_units"])
            capital = float(item["capital_used"])
            proceeds = units * payout
            pnl = proceeds - capital
            roi = pnl / capital if capital > 0.0 else None
            if item["position_kind"] == "DIRECTIONAL":
                if payout >= 1.0 - 1e-9:
                    status = "WON"
                elif payout <= 1e-9:
                    status = "LOST"
                else:
                    status = "RESOLVED_PARTIAL"
            else:
                if pnl > 1e-9:
                    status = "WON"
                elif pnl < -1e-9:
                    status = "LOST"
                else:
                    status = "RESOLVED_PARTIAL"
            db.execute(
                """
                UPDATE weather_paper_positions
                SET status=?,settlement_payout_per_unit=?,proceeds=?,pnl=?,roi=?,settled_at=?,
                    settlement_evidence_json=?
                WHERE id=?
                """,
                (status, payout, proceeds, pnl, roi, now, _json(evidence), int(position_id)),
            )
            resolved = db.execute(
                "SELECT * FROM weather_paper_positions WHERE id=?", (int(position_id),)
            ).fetchone()
            return self._decode_position(dict(resolved)) if resolved else None

    def resolved_without_notification(self, limit: int = 50) -> list[dict]:
        count = max(1, min(200, int(limit)))
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                """
                SELECT * FROM weather_paper_positions
                WHERE status IN ('WON','LOST','RESOLVED_PARTIAL')
                  AND settlement_telegram_message_id IS NULL
                ORDER BY id LIMIT ?
                """,
                (count,),
            )]
        return [self._decode_position(row) for row in rows]

    def mark_settlement_notified(self, position_id: int, message_id: int) -> None:
        if int(position_id) <= 0 or int(message_id) <= 0:
            raise WeatherPaperPositionError("PAPER_POSITION_NOTIFICATION_INVALID")
        with self._conn() as db:
            db.execute(
                "UPDATE weather_paper_positions SET settlement_telegram_message_id=? WHERE id=?",
                (int(message_id), int(position_id)),
            )

    def stats(self) -> dict:
        with self._conn() as db:
            row = db.execute(
                """
                SELECT
                    COUNT(*) AS total,
                    SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_n,
                    SUM(CASE WHEN status='NO_FILL' THEN 1 ELSE 0 END) AS no_fill,
                    SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) AS won,
                    SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) AS lost,
                    SUM(CASE WHEN status='RESOLVED_PARTIAL' THEN 1 ELSE 0 END) AS partial,
                    SUM(CASE WHEN status!='NO_FILL' THEN capital_used ELSE 0 END) AS capital_all,
                    SUM(CASE WHEN status='OPEN' THEN capital_used ELSE 0 END) AS open_capital,
                    SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL') THEN capital_used ELSE 0 END) AS resolved_capital,
                    SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL') THEN proceeds ELSE 0 END) AS resolved_proceeds,
                    SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL') THEN pnl ELSE 0 END) AS pnl
                FROM weather_paper_positions
                """
            ).fetchone()
            lanes = [dict(value) for value in db.execute(
                """
                SELECT lane,COUNT(*) AS total,
                       SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_n,
                       SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) AS won,
                       SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) AS lost,
                       SUM(CASE WHEN status='RESOLVED_PARTIAL' THEN 1 ELSE 0 END) AS partial,
                       SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL') THEN pnl ELSE 0 END) AS pnl
                FROM weather_paper_positions GROUP BY lane ORDER BY lane
                """
            )]
        data = dict(row) if row else {}
        won = int(data.get("won") or 0)
        lost = int(data.get("lost") or 0)
        partial = int(data.get("partial") or 0)
        resolved = won + lost + partial
        resolved_capital = float(data.get("resolved_capital") or 0.0)
        pnl = float(data.get("pnl") or 0.0)
        return {
            "version": WEATHER_PAPER_POSITION_VERSION,
            "total": int(data.get("total") or 0),
            "open": int(data.get("open_n") or 0),
            "no_fill": int(data.get("no_fill") or 0),
            "resolved": resolved,
            "won": won,
            "lost": lost,
            "partial": partial,
            "win_rate": (won / (won + lost)) if won + lost else None,
            "capital_all": float(data.get("capital_all") or 0.0),
            "open_capital": float(data.get("open_capital") or 0.0),
            "resolved_capital": resolved_capital,
            "resolved_proceeds": float(data.get("resolved_proceeds") or 0.0),
            "pnl": pnl,
            "resolved_roi": (pnl / resolved_capital) if resolved_capital > 0.0 else None,
            "by_lane": lanes,
            "financial_authority": False,
            "automatic_order_placement": False,
        }
