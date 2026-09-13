from __future__ import annotations

"""Validated paper execution ledger for weather LIVE PAPER v4.

This ledger separates economic lifecycle from evidence validity.  Historical rows
remain auditable but are excluded from validated P&L unless a frozen v4 decision and
execution envelope proves what was known, quoted and paper-filled at the time.
Nothing in this module can place, sign or cancel a real order.
"""

import hashlib
import json
import math
import sqlite3
import time
from pathlib import Path

from .weather_only_paper_positions import (
    RESOLVED_STATUSES,
    WeatherPaperPositionError,
    WeatherPaperPositionStore,
    _json,
    _payload,
)


WEATHER_PAPER_POSITION_V4_VERSION = "weather_paper_positions_v4_frozen_execution_validated_cohorts"
PAPER_EXECUTION_PROTOCOL_V4 = "weather_paper_execution_v4_exact_quote_capacity_minimums"
VALIDATED = "VALIDATED"
UNVERIFIED = "UNVERIFIED"
QUARANTINED = "QUARANTINED"
MAX_PAPER_QUOTE_AGE_SECONDS = 15.0


def _number(value: object, code: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool):
        raise WeatherPaperPositionError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherPaperPositionError(code) from None
    if not math.isfinite(number) or (minimum is not None and number < minimum):
        raise WeatherPaperPositionError(code)
    return number


def _column_names(db: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in db.execute(f"PRAGMA table_info({table})")}


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _tick_aligned(price: float, tick: float) -> bool:
    if tick <= 0.0:
        return False
    units = price / tick
    return abs(units - round(units)) <= 1e-7


class ValidatedWeatherPaperPositionStore(WeatherPaperPositionStore):
    def __init__(self, path: str | Path) -> None:
        super().__init__(path)
        self._init_v4()

    def _init_v4(self) -> None:
        with self._conn() as db:
            columns = _column_names(db, "weather_paper_positions")
            additions = {
                "validation_state": "TEXT NOT NULL DEFAULT 'UNVERIFIED'",
                "validation_reason": "TEXT",
                "decision_id": "TEXT",
                "cohort_id": "TEXT",
                "execution_protocol_version": "TEXT",
                "capacity_key": "TEXT",
                "quote_expires_at": "REAL",
                "paper_fill_at": "REAL",
                "entry_evidence_json": "TEXT",
            }
            for name, ddl in additions.items():
                if name not in columns:
                    db.execute(f"ALTER TABLE weather_paper_positions ADD COLUMN {name} {ddl}")
            db.executescript(
                """
                CREATE INDEX IF NOT EXISTS idx_weather_paper_positions_validation
                    ON weather_paper_positions(validation_state,status,id);
                CREATE INDEX IF NOT EXISTS idx_weather_paper_positions_capacity
                    ON weather_paper_positions(capacity_key,validation_state,status);
                CREATE TABLE IF NOT EXISTS weather_paper_position_transitions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    position_id INTEGER NOT NULL,
                    changed_at REAL NOT NULL,
                    transition_type TEXT NOT NULL,
                    old_status TEXT,
                    new_status TEXT,
                    old_validation_state TEXT,
                    new_validation_state TEXT,
                    reason TEXT,
                    old_settlement_evidence_json TEXT,
                    new_settlement_evidence_json TEXT
                );
                """
            )

    def _transition(
        self,
        db: sqlite3.Connection,
        row: sqlite3.Row | dict,
        *,
        transition_type: str,
        new_status: str | None = None,
        new_validation_state: str | None = None,
        reason: str | None = None,
        new_settlement_evidence_json: str | None = None,
    ) -> None:
        old = dict(row)
        db.execute(
            """
            INSERT INTO weather_paper_position_transitions(
                position_id,changed_at,transition_type,old_status,new_status,
                old_validation_state,new_validation_state,reason,
                old_settlement_evidence_json,new_settlement_evidence_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            (
                int(old["id"]), time.time(), str(transition_type), old.get("status"),
                new_status if new_status is not None else old.get("status"),
                old.get("validation_state") or UNVERIFIED,
                new_validation_state if new_validation_state is not None else (old.get("validation_state") or UNVERIFIED),
                reason, old.get("settlement_evidence_json"), new_settlement_evidence_json,
            ),
        )

    @staticmethod
    def _decision_fields(data: dict) -> tuple[str, str, str]:
        envelope = data.get("decision_envelope")
        if not isinstance(envelope, dict):
            raise WeatherPaperPositionError("PAPER_DECISION_ENVELOPE_MISSING")
        release = str(envelope.get("release_sha") or "").strip().lower()
        if len(release) != 40 or any(ch not in "0123456789abcdef" for ch in release):
            raise WeatherPaperPositionError("PAPER_DECISION_RELEASE_INVALID")
        semantic_digest = str(envelope.get("semantic_digest") or "").strip().lower()
        if len(semantic_digest) != 64 or any(ch not in "0123456789abcdef" for ch in semantic_digest):
            raise WeatherPaperPositionError("PAPER_DECISION_SEMANTIC_DIGEST_INVALID")
        decision_id = str(envelope.get("decision_id") or "").strip().lower()
        if len(decision_id) != 64 or any(ch not in "0123456789abcdef" for ch in decision_id):
            raise WeatherPaperPositionError("PAPER_DECISION_ID_INVALID")
        cohort_id = str(envelope.get("cohort_id") or "").strip()
        if not cohort_id:
            raise WeatherPaperPositionError("PAPER_DECISION_COHORT_MISSING")
        expected = hashlib.sha256(
            _canonical({k: v for k, v in envelope.items() if k != "decision_id"}).encode("utf-8")
        ).hexdigest()
        if decision_id != expected:
            raise WeatherPaperPositionError("PAPER_DECISION_ID_MISMATCH")
        return decision_id, cohort_id, release

    @staticmethod
    def _execution(data: dict) -> dict:
        execution = data.get("paper_execution")
        if not isinstance(execution, dict):
            raise WeatherPaperPositionError("PAPER_EXECUTION_ENVELOPE_MISSING")
        if str(execution.get("version") or "") != PAPER_EXECUTION_PROTOCOL_V4:
            raise WeatherPaperPositionError("PAPER_EXECUTION_PROTOCOL_INVALID")
        return execution

    def _validated_spec(self, signal: dict) -> dict:
        data = _payload(signal.get("payload_json"))
        decision_id, cohort_id, _release = self._decision_fields(data)
        execution = self._execution(data)

        target = _number(execution.get("target_stake_usd"), "PAPER_POSITION_STAKE_INVALID", minimum=0.000001)
        aggregate_cost = _number(execution.get("aggregate_cost_per_unit"), "PAPER_POSITION_ENTRY_COST_INVALID", minimum=0.000001)
        signal_cost = _number(signal.get("entry_cost"), "PAPER_POSITION_ENTRY_COST_INVALID", minimum=0.000001)
        if abs(aggregate_cost - signal_cost) > 1e-9:
            raise WeatherPaperPositionError("PAPER_POSITION_COST_ENVELOPE_MISMATCH")

        quote_at = _number(execution.get("quote_observed_at"), "PAPER_POSITION_QUOTE_TIME_INVALID", minimum=0.000001)
        quote_expires = _number(execution.get("quote_expires_at"), "PAPER_POSITION_QUOTE_EXPIRY_INVALID", minimum=quote_at)
        fill_at = _number(execution.get("paper_fill_at"), "PAPER_POSITION_FILL_TIME_INVALID", minimum=quote_at)
        if fill_at > quote_expires or fill_at - quote_at > MAX_PAPER_QUOTE_AGE_SECONDS:
            raise WeatherPaperPositionError("PAPER_POSITION_QUOTE_EXPIRED")

        capacity_key = str(execution.get("capacity_key") or "").strip().lower()
        if len(capacity_key) != 64 or any(ch not in "0123456789abcdef" for ch in capacity_key):
            raise WeatherPaperPositionError("PAPER_POSITION_CAPACITY_KEY_INVALID")
        legs_raw = execution.get("legs")
        if not isinstance(legs_raw, list) or not legs_raw:
            raise WeatherPaperPositionError("PAPER_POSITION_LEGS_INVALID")

        lane = str(signal.get("lane") or "").strip()
        event_id = str(signal.get("event_id") or "").strip()
        title = str(data.get("event_title") or event_id or f"Signal {signal.get('id')}").strip()
        expected_total = 0.0
        common_visible: float | None = None
        minimum_units = 0.0
        legs: list[dict] = []
        for index, raw in enumerate(legs_raw):
            if not isinstance(raw, dict):
                raise WeatherPaperPositionError("PAPER_POSITION_LEG_INVALID")
            market_id = str(raw.get("market_id") or "").strip()
            condition_id = str(raw.get("condition_id") or "").strip()
            token_id = str(raw.get("token_id") or "").strip()
            outcome = str(raw.get("outcome") or raw.get("side") or "").strip().upper()
            if not market_id or not condition_id or not token_id or outcome not in {"YES", "NO"}:
                raise WeatherPaperPositionError("PAPER_POSITION_LEG_IDENTITY_INVALID")
            ask = _number(raw.get("ask"), "PAPER_POSITION_LEG_ASK_INVALID", minimum=0.000001)
            fee = _number(raw.get("fee"), "PAPER_POSITION_LEG_FEE_INVALID", minimum=0.0)
            visible = _number(raw.get("visible_units"), "PAPER_POSITION_LEG_VISIBLE_INVALID", minimum=0.0)
            min_order = _number(raw.get("minimum_order_size"), "PAPER_POSITION_LEG_MIN_ORDER_INVALID", minimum=0.0)
            tick = _number(raw.get("minimum_tick_size"), "PAPER_POSITION_LEG_TICK_INVALID", minimum=0.000001)
            if not (0.0 < ask < 1.0) or not _tick_aligned(ask, tick):
                raise WeatherPaperPositionError("PAPER_POSITION_LEG_PRICE_OFF_TICK")
            expected_total += ask + fee
            common_visible = visible if common_visible is None else min(common_visible, visible)
            minimum_units = max(minimum_units, min_order)
            legs.append({
                "market_id": market_id,
                "condition_id": condition_id,
                "token_id": token_id,
                "outcome": outcome,
                "ask": ask,
                "fee": fee,
                "visible_units": visible,
                "minimum_order_size": min_order,
                "minimum_tick_size": tick,
                "book_hash": str(raw.get("book_hash") or ""),
                "provider_timestamp": str(raw.get("provider_timestamp") or ""),
            })
        if abs(expected_total - aggregate_cost) > 1e-7:
            raise WeatherPaperPositionError("PAPER_POSITION_LEG_COST_SUM_MISMATCH")

        if lane == "weather_forecast_raw_gap":
            if len(legs) != 1:
                raise WeatherPaperPositionError("PAPER_POSITION_DIRECTIONAL_LEG_COUNT_INVALID")
            market_id = str(signal.get("market_id") or "").strip()
            token_id = str(signal.get("token_id") or "").strip()
            side = str(signal.get("side") or "").strip().upper()
            if market_id != legs[0]["market_id"] or token_id != legs[0]["token_id"] or side != legs[0]["outcome"]:
                raise WeatherPaperPositionError("PAPER_POSITION_DIRECTIONAL_IDENTITY_MISMATCH")
            kind = "DIRECTIONAL"
        elif lane.startswith("weather_"):
            market_id = None
            token_id = None
            side = "BASKET"
            kind = "STRUCTURAL"
        else:
            raise WeatherPaperPositionError("PAPER_POSITION_LANE_UNSUPPORTED")

        return {
            "lane": lane,
            "evidence_class": str(signal.get("evidence_class") or ""),
            "position_kind": kind,
            "event_id": event_id,
            "market_id": market_id,
            "token_id": token_id,
            "side": side,
            "title": title,
            "target_stake_usd": target,
            "entry_cost_per_unit": aggregate_cost,
            "visible_units": float(common_visible or 0.0),
            "minimum_units": minimum_units,
            "legs": legs,
            "quote_observed_at": quote_at,
            "quote_expires_at": quote_expires,
            "paper_fill_at": fill_at,
            "decision_id": decision_id,
            "cohort_id": cohort_id,
            "capacity_key": capacity_key,
            "entry_evidence_json": _json({"decision_envelope": data["decision_envelope"], "paper_execution": execution}),
        }

    def _unverified_spec(self, signal: dict, reason: str) -> dict:
        data = _payload(signal.get("payload_json"))
        return {
            "lane": str(signal.get("lane") or ""),
            "evidence_class": str(signal.get("evidence_class") or ""),
            "position_kind": "DIRECTIONAL" if str(signal.get("lane") or "") == "weather_forecast_raw_gap" else "STRUCTURAL",
            "event_id": str(signal.get("event_id") or ""),
            "market_id": str(signal.get("market_id") or "") or None,
            "token_id": str(signal.get("token_id") or "") or None,
            "side": str(signal.get("side") or "BASKET"),
            "title": str(data.get("event_title") or signal.get("event_id") or "Legacy paper signal"),
            "target_stake_usd": 0.0,
            "entry_cost_per_unit": float(signal.get("entry_cost") or 0.0),
            "visible_units": 0.0,
            "minimum_units": 0.0,
            "legs": [],
            "quote_observed_at": None,
            "quote_expires_at": None,
            "paper_fill_at": None,
            "decision_id": None,
            "cohort_id": "legacy-unverified",
            "capacity_key": None,
            "entry_evidence_json": _json({"legacy_signal_payload": data}),
            "validation_reason": reason,
        }

    def ensure_position_for_signal(self, signal_id: int, target_stake_usd: float | None = None) -> dict | None:
        sid = int(signal_id)
        with self._conn() as db:
            existing = db.execute("SELECT * FROM weather_paper_positions WHERE signal_id=?", (sid,)).fetchone()
            if existing:
                return self._decode_position(dict(existing))
            signal_row = db.execute("SELECT * FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()
        if not signal_row:
            return None
        signal = dict(signal_row)

        try:
            spec = self._validated_spec(signal)
            validation_state = VALIDATED
            validation_reason = None
        except WeatherPaperPositionError as exc:
            spec = self._unverified_spec(signal, exc.code)
            validation_state = UNVERIFIED
            validation_reason = exc.code

        status = "UNVERIFIED"
        filled_units = 0.0
        capital = 0.0
        no_fill_reason = validation_reason
        if validation_state == VALIDATED:
            with self._conn() as db:
                used = db.execute(
                    """
                    SELECT COALESCE(SUM(filled_units),0) FROM weather_paper_positions
                    WHERE capacity_key=? AND validation_state='VALIDATED'
                      AND status IN ('OPEN','WON','LOST','RESOLVED_PARTIAL')
                    """,
                    (spec["capacity_key"],),
                ).fetchone()[0]
            remaining_visible = max(0.0, float(spec["visible_units"]) - float(used or 0.0))
            requested = float(spec["target_stake_usd"]) / float(spec["entry_cost_per_unit"])
            filled_units = min(requested, remaining_visible)
            if filled_units <= 0.0:
                status = "NO_FILL"
                no_fill_reason = "NO_REMAINING_VALIDATED_TOP_OF_BOOK_CAPACITY"
                filled_units = 0.0
            elif filled_units + 1e-12 < float(spec["minimum_units"]):
                status = "NO_FILL"
                no_fill_reason = "BELOW_MARKET_MINIMUM_ORDER_SIZE"
                filled_units = 0.0
            else:
                status = "OPEN"
                no_fill_reason = None
                capital = filled_units * float(spec["entry_cost_per_unit"])

        telegram_sent = signal.get("telegram_sent_at")
        if telegram_sent is None:
            telegram_sent = spec.get("paper_fill_at") or signal.get("created_at") or time.time()
        opened = spec.get("paper_fill_at") or telegram_sent
        maximum_payout = filled_units if spec["position_kind"] == "DIRECTIONAL" else filled_units

        with self._conn() as db:
            try:
                cur = db.execute(
                    """
                    INSERT INTO weather_paper_positions(
                        position_version,signal_id,lane,evidence_class,position_kind,event_id,
                        market_id,token_id,side,title,target_stake_usd,entry_cost_per_unit,
                        visible_units,filled_units,capital_used,maximum_payout,legs_json,
                        quote_observed_at,telegram_sent_at,opened_at,status,no_fill_reason,
                        financial_authority,automatic_order_placement,validation_state,
                        validation_reason,decision_id,cohort_id,execution_protocol_version,
                        capacity_key,quote_expires_at,paper_fill_at,entry_evidence_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        WEATHER_PAPER_POSITION_V4_VERSION, sid, spec["lane"], spec["evidence_class"],
                        spec["position_kind"], spec["event_id"], spec["market_id"], spec["token_id"],
                        spec["side"], spec["title"], spec["target_stake_usd"], spec["entry_cost_per_unit"],
                        spec["visible_units"], filled_units, capital, maximum_payout, _json(spec["legs"]),
                        spec["quote_observed_at"], float(telegram_sent), float(opened), status, no_fill_reason,
                        validation_state, validation_reason, spec.get("decision_id"), spec.get("cohort_id"),
                        PAPER_EXECUTION_PROTOCOL_V4 if validation_state == VALIDATED else None,
                        spec.get("capacity_key"), spec.get("quote_expires_at"), spec.get("paper_fill_at"),
                        spec.get("entry_evidence_json"),
                    ),
                )
                pid = int(cur.lastrowid)
            except sqlite3.IntegrityError:
                row = db.execute("SELECT * FROM weather_paper_positions WHERE signal_id=?", (sid,)).fetchone()
                return self._decode_position(dict(row)) if row else None
            row = db.execute("SELECT * FROM weather_paper_positions WHERE id=?", (pid,)).fetchone()
            return self._decode_position(dict(row)) if row else None

    def ensure_sent_positions(self, target_stake_usd: float | None = None) -> list[dict]:
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                """
                SELECT s.id FROM weather_paper_signals s
                LEFT JOIN weather_paper_positions p ON p.signal_id=s.id
                WHERE s.telegram_message_id IS NOT NULL AND p.id IS NULL
                ORDER BY s.id
                """
            )]
        return [position for row in rows if (position := self.ensure_position_for_signal(int(row["id"]), target_stake_usd)) is not None]

    def quarantine_signal(self, signal_id: int, reason: str) -> bool:
        sid = int(signal_id)
        why = str(reason or "QUARANTINED")
        with self._conn() as db:
            signal = db.execute("SELECT id,status FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()
            if not signal:
                return False
            db.execute("UPDATE weather_paper_signals SET status='QUARANTINED' WHERE id=?", (sid,))
            row = db.execute("SELECT * FROM weather_paper_positions WHERE signal_id=?", (sid,)).fetchone()
            if row:
                self._transition(
                    db, row, transition_type="QUARANTINE", new_status="QUARANTINED",
                    new_validation_state=QUARANTINED, reason=why,
                )
                db.execute(
                    "UPDATE weather_paper_positions SET status='QUARANTINED',validation_state='QUARANTINED',validation_reason=?,no_fill_reason=COALESCE(no_fill_reason,?) WHERE signal_id=?",
                    (why, why, sid),
                )
            return True

    def settlement_page(self, limit: int = 200) -> list[dict]:
        count = max(1, min(500, int(limit)))
        cursor = int(self.get_state("settlement_cursor_position_id", "0") or 0)
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                """
                SELECT * FROM weather_paper_positions
                WHERE validation_state='VALIDATED' AND status='OPEN' AND id>?
                ORDER BY id LIMIT ?
                """,
                (cursor, count),
            )]
            if not rows:
                rows = [dict(row) for row in db.execute(
                    """
                    SELECT * FROM weather_paper_positions
                    WHERE validation_state='VALIDATED' AND status='OPEN'
                    ORDER BY id LIMIT ?
                    """,
                    (count,),
                )]
        if rows:
            self.set_state("settlement_cursor_position_id", int(rows[-1]["id"]))
        else:
            self.set_state("settlement_cursor_position_id", 0)
        return [self._decode_position(row) for row in rows]

    def open_positions(self, limit: int = 200, *, validated_only: bool = False) -> list[dict]:
        count = max(1, min(1000, int(limit)))
        clause = "AND validation_state='VALIDATED'" if validated_only else ""
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                f"SELECT * FROM weather_paper_positions WHERE status='OPEN' {clause} ORDER BY id LIMIT ?",
                (count,),
            )]
        return [self._decode_position(row) for row in rows]

    def recent_positions(self, limit: int = 10, *, resolved_only: bool = False) -> list[dict]:
        count = max(1, min(100, int(limit)))
        where = "WHERE status IN ('WON','LOST','RESOLVED_PARTIAL')" if resolved_only else ""
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                f"SELECT * FROM weather_paper_positions {where} ORDER BY id DESC LIMIT ?", (count,)
            )]
        return [self._decode_position(row) for row in rows]

    def recent_signals(self, limit: int = 10) -> list[dict]:
        count = max(1, min(100, int(limit)))
        with self._conn() as db:
            return [dict(row) for row in db.execute(
                """
                SELECT s.id,s.lane,s.event_id,s.market_id,s.side,s.entry_cost,s.raw_gap,s.status AS signal_status,
                       s.created_at,s.telegram_message_id,p.id AS position_id,p.status AS position_status,
                       p.validation_state,p.validation_reason,p.capital_used,p.pnl
                FROM weather_paper_signals s
                LEFT JOIN weather_paper_positions p ON p.signal_id=s.id
                ORDER BY s.id DESC LIMIT ?
                """, (count,)
            )]

    def resolve_position(self, position_id: int, payout_per_unit: float, evidence: dict) -> dict | None:
        payout = _number(payout_per_unit, "PAPER_POSITION_PAYOUT_INVALID", minimum=0.0)
        if not isinstance(evidence, dict) or evidence.get("finality") != "CTF_ONCHAIN_FINAL":
            raise WeatherPaperPositionError("PAPER_POSITION_SETTLEMENT_NOT_FINAL")
        now = time.time()
        with self._conn() as db:
            row = db.execute(
                "SELECT * FROM weather_paper_positions WHERE id=? AND status='OPEN' AND validation_state='VALIDATED'",
                (int(position_id),),
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
                status = "WON" if payout >= 1.0 - 1e-12 else "LOST" if payout <= 1e-12 else "RESOLVED_PARTIAL"
            else:
                status = "WON" if pnl > 1e-12 else "LOST" if pnl < -1e-12 else "RESOLVED_PARTIAL"
            evidence_json = _json(evidence)
            self._transition(
                db, row, transition_type="FINAL_SETTLEMENT", new_status=status,
                new_validation_state=VALIDATED, reason="CTF_ONCHAIN_FINAL", new_settlement_evidence_json=evidence_json,
            )
            db.execute(
                """
                UPDATE weather_paper_positions
                SET status=?,settlement_payout_per_unit=?,proceeds=?,pnl=?,roi=?,settled_at=?,
                    settlement_evidence_json=?
                WHERE id=?
                """,
                (status, payout, proceeds, pnl, roi, now, evidence_json, int(position_id)),
            )
            resolved = db.execute("SELECT * FROM weather_paper_positions WHERE id=?", (int(position_id),)).fetchone()
            return self._decode_position(dict(resolved)) if resolved else None

    def resolved_without_notification(self, limit: int = 50) -> list[dict]:
        count = max(1, min(200, int(limit)))
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                """
                SELECT * FROM weather_paper_positions
                WHERE validation_state='VALIDATED'
                  AND status IN ('WON','LOST','RESOLVED_PARTIAL')
                  AND settlement_telegram_message_id IS NULL
                ORDER BY id LIMIT ?
                """, (count,)
            )]
        return [self._decode_position(row) for row in rows]

    def stats(self) -> dict:
        with self._conn() as db:
            all_counts = dict(db.execute(
                """
                SELECT COUNT(*) total,
                       SUM(CASE WHEN validation_state='VALIDATED' THEN 1 ELSE 0 END) validated,
                       SUM(CASE WHEN validation_state='UNVERIFIED' THEN 1 ELSE 0 END) unverified,
                       SUM(CASE WHEN validation_state='QUARANTINED' THEN 1 ELSE 0 END) quarantined,
                       SUM(CASE WHEN validation_state='VALIDATED' AND status='OPEN' THEN 1 ELSE 0 END) open_n,
                       SUM(CASE WHEN validation_state='VALIDATED' AND status='NO_FILL' THEN 1 ELSE 0 END) no_fill,
                       SUM(CASE WHEN validation_state='VALIDATED' AND status='WON' THEN 1 ELSE 0 END) won,
                       SUM(CASE WHEN validation_state='VALIDATED' AND status='LOST' THEN 1 ELSE 0 END) lost,
                       SUM(CASE WHEN validation_state='VALIDATED' AND status='RESOLVED_PARTIAL' THEN 1 ELSE 0 END) partial,
                       SUM(CASE WHEN validation_state='VALIDATED' AND status='OPEN' THEN capital_used ELSE 0 END) open_capital,
                       SUM(CASE WHEN validation_state='VALIDATED' AND status IN ('WON','LOST','RESOLVED_PARTIAL') THEN capital_used ELSE 0 END) resolved_capital,
                       SUM(CASE WHEN validation_state='VALIDATED' AND status IN ('WON','LOST','RESOLVED_PARTIAL') THEN proceeds ELSE 0 END) resolved_proceeds,
                       SUM(CASE WHEN validation_state='VALIDATED' AND status IN ('WON','LOST','RESOLVED_PARTIAL') THEN pnl ELSE 0 END) pnl
                FROM weather_paper_positions
                """
            ).fetchone())
            lanes = [dict(row) for row in db.execute(
                """
                SELECT lane,
                       SUM(CASE WHEN validation_state='VALIDATED' THEN 1 ELSE 0 END) AS validated,
                       SUM(CASE WHEN validation_state='VALIDATED' AND status='OPEN' THEN 1 ELSE 0 END) AS open_n,
                       SUM(CASE WHEN validation_state='VALIDATED' AND status='WON' THEN 1 ELSE 0 END) AS won,
                       SUM(CASE WHEN validation_state='VALIDATED' AND status='LOST' THEN 1 ELSE 0 END) AS lost,
                       SUM(CASE WHEN validation_state='VALIDATED' AND status='RESOLVED_PARTIAL' THEN 1 ELSE 0 END) AS partial,
                       SUM(CASE WHEN validation_state='VALIDATED' AND status IN ('WON','LOST','RESOLVED_PARTIAL') THEN pnl ELSE 0 END) AS pnl
                FROM weather_paper_positions GROUP BY lane ORDER BY lane
                """
            )]
        won = int(all_counts.get("won") or 0)
        lost = int(all_counts.get("lost") or 0)
        partial = int(all_counts.get("partial") or 0)
        resolved = won + lost + partial
        resolved_capital = float(all_counts.get("resolved_capital") or 0.0)
        pnl = float(all_counts.get("pnl") or 0.0)
        return {
            "version": WEATHER_PAPER_POSITION_V4_VERSION,
            "total_rows": int(all_counts.get("total") or 0),
            "validated": int(all_counts.get("validated") or 0),
            "unverified": int(all_counts.get("unverified") or 0),
            "quarantined": int(all_counts.get("quarantined") or 0),
            "open": int(all_counts.get("open_n") or 0),
            "no_fill": int(all_counts.get("no_fill") or 0),
            "resolved": resolved,
            "won": won,
            "lost": lost,
            "partial": partial,
            "win_rate": won / (won + lost) if won + lost else None,
            "open_capital": float(all_counts.get("open_capital") or 0.0),
            "resolved_capital": resolved_capital,
            "resolved_proceeds": float(all_counts.get("resolved_proceeds") or 0.0),
            "pnl": pnl,
            "resolved_roi": pnl / resolved_capital if resolved_capital > 0.0 else None,
            "by_lane": lanes,
            "financial_authority": False,
            "automatic_order_placement": False,
        }
