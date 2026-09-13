from __future__ import annotations

"""Validated decision ledger + frozen paper execution protocol.

The legacy paper tracker could retroactively invent fills from old Telegram rows using
whatever stake happened to be configured after restart.  This module makes a paper
position possible only when a P0 decision envelope and execution protocol were frozen
*before* Telegram delivery.  Legacy or unverifiable history is preserved but excluded
from validated statistics.
"""

import json
import math
import sqlite3
import time
from dataclasses import dataclass
from decimal import Decimal, ROUND_DOWN

from .weather_only_live_paper_v3 import (
    QUARANTINED_STATUS,
    GuardedWeatherPaperPositionStore,
)
from .weather_only_p0_guards import WEATHER_P0_GUARD_VERSION


P0_POSITION_VERSION = "weather_p0_position_v1_frozen_decision_execution_protocol"
P0_EXECUTION_PROTOCOL_VERSION = "paper_execution_v1_exact_ask_visible_capacity_min_order_6dp"
P0_COHORT_ID = "P0_VALIDATED_PROSPECTIVE_2026_09_13"
PRE_P0_HISTORY_REASON = "PRE_P0_OR_UNRECONSTRUCTABLE_DECISION_EVIDENCE"
VALIDATION_PENDING = "PENDING_VALIDATION"
VALIDATION_VALIDATED = "VALIDATED"
VALIDATION_QUARANTINED = "QUARANTINED"
DELIVERY_PENDING = "PENDING"
DELIVERY_SENDING = "SENDING"
DELIVERY_ACKNOWLEDGED = "ACKNOWLEDGED"
DELIVERY_UNCERTAIN = "UNCERTAIN"
DELIVERY_EXPIRED = "EXPIRED"


class WeatherP0PositionError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise WeatherP0PositionError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherP0PositionError(code) from None
    if not math.isfinite(number):
        raise WeatherP0PositionError(code)
    return number


def _json(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise WeatherP0PositionError("P0_JSON_EVIDENCE_INVALID") from None


def _payload(raw: object) -> dict:
    try:
        value = json.loads(str(raw or "{}"))
    except Exception:
        raise WeatherP0PositionError("P0_SIGNAL_PAYLOAD_INVALID") from None
    if not isinstance(value, dict):
        raise WeatherP0PositionError("P0_SIGNAL_PAYLOAD_INVALID")
    return value


def _floor_6(value: float) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_DOWN))


@dataclass(frozen=True, slots=True)
class FrozenDirectionalExecution:
    protocol_version: str
    frozen_at: float
    fill_at: float
    decision_expires_at: float
    target_stake_usd: float
    market_id: str
    condition_id: str
    token_id: str
    side: str
    ask: float
    conservative_fee_per_share: float
    entry_cost_per_unit: float
    visible_units: float
    minimum_order_size: float
    minimum_tick_size: float
    quote_observed_at: float
    provider_timestamp: float
    book_hash: str
    requested_units: float
    filled_units: float
    capital_used: float
    maximum_payout: float
    fill_status: str
    no_fill_reason: str | None

    def as_dict(self) -> dict:
        return {
            key: getattr(self, key)
            for key in self.__dataclass_fields__
        }


def freeze_directional_execution(
    *,
    frozen_at: float,
    decision_expires_at: float,
    target_stake_usd: float,
    market_id: str,
    condition_id: str,
    token_id: str,
    side: str,
    ask: float,
    fee_per_share: float,
    visible_units: float,
    minimum_order_size: float,
    minimum_tick_size: float,
    quote_observed_at: float,
    provider_timestamp: float,
    book_hash: str,
) -> FrozenDirectionalExecution:
    now = _finite(frozen_at, "P0_FILL_TIME_INVALID")
    expiry = _finite(decision_expires_at, "P0_DECISION_EXPIRY_INVALID")
    stake = _finite(target_stake_usd, "P0_PAPER_STAKE_INVALID")
    ask_f = _finite(ask, "P0_PAPER_ASK_INVALID")
    fee = _finite(fee_per_share, "P0_PAPER_FEE_INVALID")
    visible = _finite(visible_units, "P0_PAPER_VISIBLE_SIZE_INVALID")
    min_size = _finite(minimum_order_size, "P0_PAPER_MIN_ORDER_INVALID")
    tick = _finite(minimum_tick_size, "P0_PAPER_TICK_INVALID")
    quote_at = _finite(quote_observed_at, "P0_PAPER_QUOTE_TIME_INVALID")
    provider_at = _finite(provider_timestamp, "P0_PAPER_PROVIDER_TIME_INVALID")
    market = str(market_id or "").strip()
    condition = str(condition_id or "").strip()
    token = str(token_id or "").strip()
    side_name = str(side or "").strip().upper()
    digest = str(book_hash or "").strip()
    if not market or not condition or not token or side_name not in {"YES", "NO"} or not digest:
        raise WeatherP0PositionError("P0_PAPER_IDENTITY_INVALID")
    if now >= expiry:
        raise WeatherP0PositionError("P0_PAPER_DECISION_EXPIRED")
    if stake <= 0.0 or not 0.0 < ask_f < 1.0 or fee < 0.0 or visible < 0.0 or min_size <= 0.0 or tick <= 0.0:
        raise WeatherP0PositionError("P0_PAPER_EXECUTION_VALUES_INVALID")
    tick_units = ask_f / tick
    if abs(tick_units - round(tick_units)) > 1e-7:
        raise WeatherP0PositionError("P0_PAPER_ASK_OFF_TICK")
    entry_cost = ask_f + fee
    if not 0.0 < entry_cost < 2.0:
        raise WeatherP0PositionError("P0_PAPER_ENTRY_COST_INVALID")

    requested = _floor_6(stake / entry_cost)
    filled = _floor_6(min(requested, visible)) if visible > 0.0 else 0.0
    no_fill: str | None = None
    status = "OPEN"
    if filled <= 0.0:
        status = "NO_FILL"
        no_fill = "NO_VISIBLE_TOP_OF_BOOK_CAPACITY"
        filled = 0.0
    elif filled + 1e-9 < min_size:
        status = "NO_FILL"
        no_fill = "BELOW_MARKET_MINIMUM_ORDER_SIZE"
        filled = 0.0
    capital = filled * entry_cost
    maximum = filled
    return FrozenDirectionalExecution(
        protocol_version=P0_EXECUTION_PROTOCOL_VERSION,
        frozen_at=now,
        fill_at=now,
        decision_expires_at=expiry,
        target_stake_usd=stake,
        market_id=market,
        condition_id=condition,
        token_id=token,
        side=side_name,
        ask=ask_f,
        conservative_fee_per_share=fee,
        entry_cost_per_unit=entry_cost,
        visible_units=visible,
        minimum_order_size=min_size,
        minimum_tick_size=tick,
        quote_observed_at=quote_at,
        provider_timestamp=provider_at,
        book_hash=digest,
        requested_units=requested,
        filled_units=filled,
        capital_used=capital,
        maximum_payout=maximum,
        fill_status=status,
        no_fill_reason=no_fill,
    )


class P0WeatherPaperPositionStore(GuardedWeatherPaperPositionStore):
    def __init__(self, path) -> None:
        super().__init__(path)
        with self._conn() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS weather_p0_decisions (
                    signal_id INTEGER PRIMARY KEY,
                    guard_version TEXT NOT NULL,
                    cohort_id TEXT NOT NULL,
                    validation_status TEXT NOT NULL,
                    validation_reason TEXT,
                    release_sha TEXT NOT NULL,
                    semantic_digest TEXT NOT NULL,
                    decision_envelope_json TEXT NOT NULL,
                    execution_protocol_json TEXT NOT NULL,
                    quote_observed_at REAL NOT NULL,
                    fill_at REAL NOT NULL,
                    decision_expires_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    classified_at REAL NOT NULL,
                    FOREIGN KEY(signal_id) REFERENCES weather_paper_signals(id)
                );
                CREATE INDEX IF NOT EXISTS idx_weather_p0_decision_status
                    ON weather_p0_decisions(validation_status, signal_id);

                CREATE TABLE IF NOT EXISTS weather_p0_delivery_outbox (
                    signal_id INTEGER PRIMARY KEY,
                    stable_alert_id TEXT NOT NULL UNIQUE,
                    message_sha256 TEXT NOT NULL,
                    delivery_state TEXT NOT NULL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    first_attempt_at REAL,
                    last_attempt_at REAL,
                    telegram_message_id INTEGER,
                    acknowledged_at REAL,
                    last_error TEXT,
                    decision_expires_at REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(signal_id) REFERENCES weather_paper_signals(id)
                );
                CREATE INDEX IF NOT EXISTS idx_weather_p0_outbox_state
                    ON weather_p0_delivery_outbox(delivery_state, updated_at);

                CREATE TABLE IF NOT EXISTS weather_p0_audit (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER,
                    entity TEXT NOT NULL,
                    from_state TEXT,
                    to_state TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    at REAL NOT NULL,
                    details_json TEXT NOT NULL,
                    FOREIGN KEY(signal_id) REFERENCES weather_paper_signals(id)
                );
                CREATE INDEX IF NOT EXISTS idx_weather_p0_audit_signal
                    ON weather_p0_audit(signal_id, id);
                """
            )

    def _audit(
        self,
        signal_id: int | None,
        *,
        entity: str,
        from_state: str | None,
        to_state: str,
        reason: str,
        details: dict | None = None,
        at: float | None = None,
    ) -> None:
        timestamp = time.time() if at is None else _finite(at, "P0_AUDIT_TIME_INVALID")
        with self._conn() as db:
            db.execute(
                """
                INSERT INTO weather_p0_audit(signal_id,entity,from_state,to_state,reason,at,details_json)
                VALUES(?,?,?,?,?,?,?)
                """,
                (
                    None if signal_id is None else int(signal_id),
                    str(entity),
                    None if from_state is None else str(from_state),
                    str(to_state),
                    str(reason),
                    timestamp,
                    _json(details or {}),
                ),
            )

    def record_validated_decision(
        self,
        signal_id: int,
        *,
        release_sha: str,
        semantic_digest: str,
        decision_envelope: dict,
        execution: FrozenDirectionalExecution,
        cohort_id: str = P0_COHORT_ID,
    ) -> dict:
        sid = int(signal_id)
        release = str(release_sha or "").strip().lower()
        semantic = str(semantic_digest or "").strip().lower()
        if len(release) != 40 or any(ch not in "0123456789abcdef" for ch in release):
            raise WeatherP0PositionError("P0_RELEASE_SHA_INVALID")
        if len(semantic) != 64 or any(ch not in "0123456789abcdef" for ch in semantic):
            raise WeatherP0PositionError("P0_SEMANTIC_DIGEST_INVALID")
        now = time.time()
        with self._conn() as db:
            signal = db.execute("SELECT id FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()
            if not signal:
                raise WeatherP0PositionError("P0_SIGNAL_NOT_FOUND")
            existing = db.execute("SELECT * FROM weather_p0_decisions WHERE signal_id=?", (sid,)).fetchone()
            if existing:
                return dict(existing)
            db.execute(
                """
                INSERT INTO weather_p0_decisions(
                    signal_id,guard_version,cohort_id,validation_status,validation_reason,
                    release_sha,semantic_digest,decision_envelope_json,execution_protocol_json,
                    quote_observed_at,fill_at,decision_expires_at,created_at,classified_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    sid, WEATHER_P0_GUARD_VERSION, str(cohort_id), VALIDATION_VALIDATED, None,
                    release, semantic, _json(decision_envelope), _json(execution.as_dict()),
                    execution.quote_observed_at, execution.fill_at, execution.decision_expires_at,
                    now, now,
                ),
            )
            row = db.execute("SELECT * FROM weather_p0_decisions WHERE signal_id=?", (sid,)).fetchone()
        self._audit(
            sid,
            entity="DECISION",
            from_state=None,
            to_state=VALIDATION_VALIDATED,
            reason="P0_GUARDS_AND_EXECUTION_PROTOCOL_FROZEN",
            details={"cohort_id": cohort_id, "release_sha": release, "semantic_digest": semantic},
            at=now,
        )
        return dict(row)

    def create_outbox(self, signal_id: int, *, stable_alert_id: str, message_sha256: str, decision_expires_at: float) -> dict:
        sid = int(signal_id)
        alert = str(stable_alert_id or "").strip()
        digest = str(message_sha256 or "").strip().lower()
        expiry = _finite(decision_expires_at, "P0_DECISION_EXPIRY_INVALID")
        if not alert or len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise WeatherP0PositionError("P0_OUTBOX_IDENTITY_INVALID")
        now = time.time()
        with self._conn() as db:
            db.execute(
                """
                INSERT INTO weather_p0_delivery_outbox(
                    signal_id,stable_alert_id,message_sha256,delivery_state,decision_expires_at,
                    created_at,updated_at
                ) VALUES(?,?,?, ?,?,?,?)
                ON CONFLICT(signal_id) DO NOTHING
                """,
                (sid, alert, digest, DELIVERY_PENDING, expiry, now, now),
            )
            row = db.execute("SELECT * FROM weather_p0_delivery_outbox WHERE signal_id=?", (sid,)).fetchone()
        return dict(row) if row else {}

    def transition_delivery(
        self,
        signal_id: int,
        to_state: str,
        *,
        error: str | None = None,
        message_id: int | None = None,
        at: float | None = None,
    ) -> dict:
        sid = int(signal_id)
        state = str(to_state)
        if state not in {DELIVERY_PENDING, DELIVERY_SENDING, DELIVERY_ACKNOWLEDGED, DELIVERY_UNCERTAIN, DELIVERY_EXPIRED}:
            raise WeatherP0PositionError("P0_DELIVERY_STATE_INVALID")
        now = time.time() if at is None else _finite(at, "P0_DELIVERY_TIME_INVALID")
        with self._conn() as db:
            row = db.execute("SELECT * FROM weather_p0_delivery_outbox WHERE signal_id=?", (sid,)).fetchone()
            if not row:
                raise WeatherP0PositionError("P0_OUTBOX_NOT_FOUND")
            old = str(row["delivery_state"])
            attempts = int(row["attempt_count"] or 0) + (1 if state == DELIVERY_SENDING else 0)
            first_attempt = row["first_attempt_at"]
            if state == DELIVERY_SENDING and first_attempt is None:
                first_attempt = now
            ack_at = now if state == DELIVERY_ACKNOWLEDGED else row["acknowledged_at"]
            msg = int(message_id) if message_id is not None else row["telegram_message_id"]
            db.execute(
                """
                UPDATE weather_p0_delivery_outbox
                SET delivery_state=?,attempt_count=?,first_attempt_at=?,last_attempt_at=?,
                    telegram_message_id=?,acknowledged_at=?,last_error=?,updated_at=?
                WHERE signal_id=?
                """,
                (state, attempts, first_attempt, now, msg, ack_at, error, now, sid),
            )
            updated = db.execute("SELECT * FROM weather_p0_delivery_outbox WHERE signal_id=?", (sid,)).fetchone()
        self._audit(
            sid,
            entity="DELIVERY",
            from_state=old,
            to_state=state,
            reason=str(error or state),
            details={"telegram_message_id": message_id},
            at=now,
        )
        return dict(updated)

    def decision_for_signal(self, signal_id: int) -> dict | None:
        with self._conn() as db:
            row = db.execute("SELECT * FROM weather_p0_decisions WHERE signal_id=?", (int(signal_id),)).fetchone()
        return dict(row) if row else None

    def outbox_for_signal(self, signal_id: int) -> dict | None:
        with self._conn() as db:
            row = db.execute("SELECT * FROM weather_p0_delivery_outbox WHERE signal_id=?", (int(signal_id),)).fetchone()
        return dict(row) if row else None

    def quarantine_unvalidated_history(self) -> dict:
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                """
                SELECT s.id,s.status,p.status AS position_status
                FROM weather_paper_signals s
                LEFT JOIN weather_p0_decisions d ON d.signal_id=s.id
                LEFT JOIN weather_paper_positions p ON p.signal_id=s.id
                WHERE d.signal_id IS NULL AND s.status!=?
                ORDER BY s.id
                """,
                (QUARANTINED_STATUS,),
            )]
        changed = 0
        for row in rows:
            sid = int(row["id"])
            if self.quarantine_signal(sid, PRE_P0_HISTORY_REASON):
                changed += 1
                self._audit(
                    sid,
                    entity="HISTORY_VALIDATION",
                    from_state=str(row.get("status") or ""),
                    to_state=VALIDATION_QUARANTINED,
                    reason=PRE_P0_HISTORY_REASON,
                    details={"prior_position_status": row.get("position_status")},
                )
        return {"checked": len(rows), "quarantined_now": changed, "reason": PRE_P0_HISTORY_REASON}

    def _validated_signal_and_execution(self, signal_id: int) -> tuple[dict, dict, dict] | None:
        sid = int(signal_id)
        with self._conn() as db:
            row = db.execute(
                """
                SELECT s.*,d.validation_status,d.cohort_id,d.execution_protocol_json,
                       o.delivery_state,o.telegram_message_id AS outbox_message_id,o.acknowledged_at
                FROM weather_paper_signals s
                JOIN weather_p0_decisions d ON d.signal_id=s.id
                JOIN weather_p0_delivery_outbox o ON o.signal_id=s.id
                WHERE s.id=?
                """,
                (sid,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        if data.get("validation_status") != VALIDATION_VALIDATED or data.get("delivery_state") != DELIVERY_ACKNOWLEDGED:
            return None
        try:
            execution = json.loads(str(data.get("execution_protocol_json") or "{}"))
        except Exception:
            raise WeatherP0PositionError("P0_EXECUTION_PROTOCOL_INVALID") from None
        if not isinstance(execution, dict) or execution.get("protocol_version") != P0_EXECUTION_PROTOCOL_VERSION:
            raise WeatherP0PositionError("P0_EXECUTION_PROTOCOL_INVALID")
        return data, _payload(data.get("payload_json")), execution

    def ensure_position_for_signal(self, signal_id: int, target_stake_usd: float | None = None) -> dict | None:
        sid = int(signal_id)
        with self._conn() as db:
            existing = db.execute("SELECT * FROM weather_paper_positions WHERE signal_id=?", (sid,)).fetchone()
        if existing:
            return dict(existing)
        bundle = self._validated_signal_and_execution(sid)
        if bundle is None:
            return None
        signal, payload, execution = bundle
        # The caller's current stake is deliberately ignored.  The frozen protocol is
        # the only permissible source of historical quantity/capital.
        if str(signal.get("lane") or "") != "weather_forecast_raw_gap":
            return None
        status = str(execution.get("fill_status") or "")
        if status not in {"OPEN", "NO_FILL"}:
            raise WeatherP0PositionError("P0_EXECUTION_FILL_STATUS_INVALID")
        title = str(payload.get("event_title") or signal.get("event_id") or f"Signal {sid}")
        legs = [{
            "market_id": str(execution["market_id"]),
            "condition_id": str(execution["condition_id"]),
            "token_id": str(execution["token_id"]),
            "side": str(execution["side"]),
            "outcome_index": 0 if str(execution["side"]).upper() == "YES" else 1,
        }]
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
                        P0_POSITION_VERSION, sid, str(signal["lane"]), str(signal["evidence_class"]),
                        "DIRECTIONAL", str(signal["event_id"]), str(execution["market_id"]),
                        str(execution["token_id"]), str(execution["side"]), title,
                        float(execution["target_stake_usd"]), float(execution["entry_cost_per_unit"]),
                        float(execution["visible_units"]), float(execution["filled_units"]),
                        float(execution["capital_used"]), float(execution["maximum_payout"]),
                        _json(legs), float(execution["quote_observed_at"]),
                        float(signal.get("acknowledged_at") or execution["fill_at"]),
                        float(execution["fill_at"]), status, execution.get("no_fill_reason"),
                    ),
                )
                position_id = int(cur.lastrowid)
            except sqlite3.IntegrityError:
                row = db.execute("SELECT * FROM weather_paper_positions WHERE signal_id=?", (sid,)).fetchone()
                return dict(row) if row else None
            row = db.execute("SELECT * FROM weather_paper_positions WHERE id=?", (position_id,)).fetchone()
        self._audit(
            sid,
            entity="POSITION",
            from_state=None,
            to_state=status,
            reason="FROZEN_EXECUTION_PROTOCOL_APPLIED",
            details={
                "position_id": position_id,
                "filled_units": execution["filled_units"],
                "capital_used": execution["capital_used"],
                "cohort_id": signal.get("cohort_id"),
            },
            at=float(execution["fill_at"]),
        )
        return dict(row) if row else None

    def ensure_sent_positions(self, target_stake_usd: float | None = None) -> list[dict]:
        with self._conn() as db:
            rows = [int(row[0]) for row in db.execute(
                """
                SELECT s.id
                FROM weather_paper_signals s
                JOIN weather_p0_decisions d ON d.signal_id=s.id AND d.validation_status=?
                JOIN weather_p0_delivery_outbox o ON o.signal_id=s.id AND o.delivery_state=?
                LEFT JOIN weather_paper_positions p ON p.signal_id=s.id
                WHERE p.id IS NULL
                ORDER BY s.id
                """,
                (VALIDATION_VALIDATED, DELIVERY_ACKNOWLEDGED),
            )]
        created: list[dict] = []
        for sid in rows:
            value = self.ensure_position_for_signal(sid)
            if value is not None:
                created.append(value)
        return created

    def stats(self) -> dict:
        base = super().stats()
        with self._conn() as db:
            validated = int(db.execute(
                "SELECT COUNT(*) FROM weather_p0_decisions WHERE validation_status=?",
                (VALIDATION_VALIDATED,),
            ).fetchone()[0])
            outbox_rows = {
                str(row["delivery_state"]): int(row["n"])
                for row in db.execute(
                    "SELECT delivery_state,COUNT(*) AS n FROM weather_p0_delivery_outbox GROUP BY delivery_state"
                )
            }
            unvalidated_signals = int(db.execute(
                """
                SELECT COUNT(*) FROM weather_paper_signals s
                LEFT JOIN weather_p0_decisions d ON d.signal_id=s.id
                WHERE d.signal_id IS NULL
                """
            ).fetchone()[0])
        base.update({
            "position_version": P0_POSITION_VERSION,
            "validated_decisions": validated,
            "delivery_states": outbox_rows,
            "legacy_or_unvalidated_signals": unvalidated_signals,
            "validated_cohort": P0_COHORT_ID,
        })
        return base
