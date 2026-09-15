from __future__ import annotations

"""Post-receipt executable PAPER admission for the all-weather research runtime.

V4 historically treated the exact quote captured immediately before Telegram delivery
as the simulated fill. V5 changes that experiment definition for new signals only:
Telegram acceptance is persisted first, then a fresh exact-CLOB snapshot must prove
executable actionability after the user could have seen the alert.

The admission transaction persists the post-receipt evidence, capacity consumption,
fully identified settlement legs, position row, decision audit row and terminal signal
status together. A crash before commit therefore cannot leave a partially valid fill.
Restart reconciliation never reconstructs a V5 fill without that durable transaction.

This module contains no authenticated trading, wallet, signing, order or cancellation
capability.
"""

import math
import time

from .weather_only_paper_corrective import _sha
from .weather_only_paper_positions import WeatherPaperPositionError, _json, _payload
from .weather_only_paper_recovery_final import FinalCrashSafeWeatherPaperStore


PAPER_EXECUTION_PROTOCOL_V5 = "weather_paper_execution_v5_post_receipt_exact_clob"
PAPER_POSITION_VERSION_V5 = "weather_paper_position_v5_post_receipt_atomic_admission"
PAPER_RECOVERY_V5_VERSION = "weather_paper_recovery_v5_no_reconstructed_post_receipt_fill"


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise WeatherPaperPositionError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherPaperPositionError(code) from None
    if not math.isfinite(number):
        raise WeatherPaperPositionError(code)
    return number


def _identity_text(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise WeatherPaperPositionError(code)
    return text


class PostReceiptWeatherPaperStore(FinalCrashSafeWeatherPaperStore):
    """Atomic V5 PAPER admission while retaining V4 history and settlement support."""

    @staticmethod
    def _validate_legs(raw: object) -> list[dict]:
        if not isinstance(raw, list) or not raw:
            raise WeatherPaperPositionError("V5_EXECUTION_LEGS_INVALID")
        legs: list[dict] = []
        tokens: set[str] = set()
        for item in raw:
            if not isinstance(item, dict):
                raise WeatherPaperPositionError("V5_EXECUTION_LEGS_INVALID")
            market_id = _identity_text(item.get("market_id"), "V5_EXECUTION_MARKET_ID_MISSING")
            condition_id = _identity_text(
                item.get("condition_id"), "V5_EXECUTION_CONDITION_ID_MISSING"
            )
            token_id = _identity_text(item.get("token_id"), "V5_EXECUTION_TOKEN_ID_MISSING")
            side = str(item.get("side") or "").strip().upper()
            if side not in {"YES", "NO"}:
                raise WeatherPaperPositionError("V5_EXECUTION_SIDE_INVALID")
            if token_id in tokens:
                raise WeatherPaperPositionError("V5_EXECUTION_DUPLICATE_TOKEN")
            tokens.add(token_id)
            ask = _finite(item.get("ask"), "V5_EXECUTION_ASK_INVALID")
            fee = _finite(item.get("fee"), "V5_EXECUTION_FEE_INVALID")
            quote_at = _finite(
                item.get("quote_observed_at"), "V5_EXECUTION_QUOTE_TIME_INVALID"
            )
            minimum = _finite(
                item.get("minimum_order_size"), "V5_EXECUTION_MINIMUM_INVALID"
            )
            book_hash = _identity_text(
                item.get("book_hash"), "V5_EXECUTION_BOOK_HASH_MISSING"
            )
            if not 0.0 < ask < 1.0 or fee < 0.0 or minimum < 0.0 or quote_at < 0.0:
                raise WeatherPaperPositionError("V5_EXECUTION_LEG_NUMBER_INVALID")
            legs.append(
                {
                    "market_id": market_id,
                    "condition_id": condition_id,
                    "token_id": token_id,
                    "side": side,
                    "ask": ask,
                    "fee": fee,
                    "quote_observed_at": quote_at,
                    "minimum_order_size": minimum,
                    "book_hash": book_hash,
                }
            )
        return legs

    def admit_post_receipt_position(
        self,
        signal_id: int,
        target_stake_usd: float,
        execution: dict,
    ) -> dict:
        if isinstance(signal_id, bool) or int(signal_id) <= 0:
            raise WeatherPaperPositionError("V5_SIGNAL_ID_INVALID")
        if not isinstance(execution, dict):
            raise WeatherPaperPositionError("V5_EXECUTION_INVALID")
        if execution.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V5:
            raise WeatherPaperPositionError("V5_EXECUTION_PROTOCOL_MISMATCH")

        target = _finite(target_stake_usd, "V5_STAKE_INVALID")
        entry_cost = _finite(execution.get("entry_cost_per_unit"), "V5_ENTRY_COST_INVALID")
        visible = _finite(execution.get("visible_units"), "V5_VISIBLE_UNITS_INVALID")
        minimum = _finite(execution.get("minimum_order_size"), "V5_MINIMUM_ORDER_INVALID")
        payout_per_unit = _finite(
            execution.get("theoretical_payout_per_unit"), "V5_PAYOUT_INVALID"
        )
        recheck_started = _finite(
            execution.get("post_receipt_recheck_started_at"),
            "V5_RECHECK_STARTED_AT_INVALID",
        )
        fill_at = _finite(
            execution.get("post_receipt_recheck_finished_at"),
            "V5_RECHECK_FINISHED_AT_INVALID",
        )
        expires_at = _finite(execution.get("decision_expires_at"), "V5_EXPIRY_INVALID")
        decision_id = _identity_text(execution.get("decision_id"), "V5_DECISION_ID_MISSING")
        if (
            target <= 0.0
            or entry_cost <= 0.0
            or visible < 0.0
            or minimum < 0.0
            or payout_per_unit <= 0.0
            or recheck_started < 0.0
            or fill_at < recheck_started
            or expires_at <= 0.0
        ):
            raise WeatherPaperPositionError("V5_EXECUTION_NUMBER_INVALID")

        legs = self._validate_legs(execution.get("legs"))
        position_kind = "DIRECTIONAL" if len(legs) == 1 else "STRUCTURAL"
        if position_kind == "DIRECTIONAL":
            market_id = legs[0]["market_id"]
            token_id = legs[0]["token_id"]
            side = legs[0]["side"]
        else:
            market_id = None
            token_id = None
            side = "BASKET"

        quote_at = min(float(leg["quote_observed_at"]) for leg in legs)
        minimum = max(minimum, *(float(leg["minimum_order_size"]) for leg in legs))
        capacity_identity = {
            "protocol": PAPER_EXECUTION_PROTOCOL_V5,
            "legs": [
                {
                    "token_id": leg["token_id"],
                    "book_hash": leg["book_hash"],
                    "ask": leg["ask"],
                    "quote_observed_at": leg["quote_observed_at"],
                }
                for leg in legs
            ],
            "visible_units": visible,
            "entry_cost_per_unit": entry_cost,
        }
        capacity_key = _sha(capacity_identity)

        with self._conn() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                existing = db.execute(
                    "SELECT * FROM weather_paper_positions WHERE signal_id=?",
                    (int(signal_id),),
                ).fetchone()
                if existing is not None:
                    if (
                        str(existing["execution_protocol"] or "") != PAPER_EXECUTION_PROTOCOL_V5
                        or str(existing["decision_id"] or "") != decision_id
                    ):
                        raise WeatherPaperPositionError("V5_EXISTING_POSITION_IDENTITY_CONFLICT")
                    db.execute("COMMIT")
                    return self._decode_position(dict(existing))

                signal = db.execute(
                    "SELECT * FROM weather_paper_signals WHERE id=?", (int(signal_id),)
                ).fetchone()
                if signal is None:
                    raise WeatherPaperPositionError("V5_SIGNAL_NOT_FOUND")
                if signal["telegram_message_id"] is None or signal["telegram_sent_at"] is None:
                    raise WeatherPaperPositionError("V5_TELEGRAM_RECEIPT_MISSING")
                sent_at = _finite(signal["telegram_sent_at"], "V5_TELEGRAM_SENT_AT_INVALID")
                if recheck_started + 1e-9 < sent_at:
                    raise WeatherPaperPositionError("V5_POST_RECEIPT_RECHECK_NOT_CAUSAL")
                if sent_at >= expires_at or fill_at >= expires_at:
                    raise WeatherPaperPositionError("V5_DECISION_EXPIRED")

                payload = _payload(signal["payload_json"])
                if payload.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V5:
                    raise WeatherPaperPositionError("V5_SIGNAL_PROTOCOL_MISMATCH")
                if str(payload.get("decision_id") or "") != decision_id:
                    raise WeatherPaperPositionError("V5_DECISION_IDENTITY_MISMATCH")

                cap = db.execute(
                    "SELECT visible_units,consumed_units FROM weather_paper_capacity_usage "
                    "WHERE capacity_key=?",
                    (capacity_key,),
                ).fetchone()
                consumed = float(cap["consumed_units"]) if cap is not None else 0.0
                if cap is not None and abs(float(cap["visible_units"]) - visible) > 1e-9:
                    raise WeatherPaperPositionError("V5_CAPACITY_IDENTITY_CONFLICT")
                available = max(0.0, visible - consumed)
                requested = target / entry_cost
                filled = min(requested, available)
                if not math.isfinite(filled) or filled <= 0.0:
                    filled = 0.0
                    status = "NO_FILL"
                    no_fill_reason = "NO_POST_RECEIPT_VISIBLE_CAPACITY"
                elif filled + 1e-12 < minimum:
                    filled = 0.0
                    status = "NO_FILL"
                    no_fill_reason = "BELOW_MINIMUM_ORDER_SIZE"
                else:
                    status = "OPEN"
                    no_fill_reason = None

                capital = filled * entry_cost
                maximum_payout = filled * payout_per_unit
                title = str(
                    payload.get("event_title") or signal["event_id"] or f"Signal {int(signal_id)}"
                ).strip()
                evidence_class = str(signal["evidence_class"] or "").strip()
                lane = str(signal["lane"] or "").strip()
                event_id = str(signal["event_id"] or "").strip()
                if not lane or not evidence_class or not event_id or not title:
                    raise WeatherPaperPositionError("V5_SIGNAL_IDENTITY_INVALID")

                execution_copy = dict(execution)
                execution_copy["capacity_key"] = capacity_key
                execution_copy["filled_units"] = filled
                execution_copy["capital_used"] = capital
                execution_copy["admission_status"] = status
                execution_copy["financial_authority"] = False
                execution_copy["automatic_order_placement"] = False
                payload["post_receipt_execution"] = execution_copy
                payload["paper_fill_at"] = fill_at

                cur = db.execute(
                    """
                    INSERT INTO weather_paper_positions(
                        position_version,signal_id,lane,evidence_class,position_kind,event_id,
                        market_id,token_id,side,title,target_stake_usd,entry_cost_per_unit,
                        visible_units,filled_units,capital_used,maximum_payout,legs_json,
                        quote_observed_at,telegram_sent_at,opened_at,status,no_fill_reason,
                        financial_authority,automatic_order_placement,decision_id,
                        execution_protocol,decision_expires_at,paper_fill_at,validation_state,
                        capacity_key,settlement_notification_state
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0,0,?,?,?,?,?,?,?)
                    """,
                    (
                        PAPER_POSITION_VERSION_V5,
                        int(signal_id),
                        lane,
                        evidence_class,
                        position_kind,
                        event_id,
                        market_id,
                        token_id,
                        side,
                        title,
                        target,
                        entry_cost,
                        visible,
                        filled,
                        capital,
                        maximum_payout,
                        _json(
                            [
                                {
                                    "market_id": leg["market_id"],
                                    "condition_id": leg["condition_id"],
                                    "token_id": leg["token_id"],
                                    "side": leg["side"],
                                }
                                for leg in legs
                            ]
                        ),
                        quote_at,
                        sent_at,
                        fill_at,
                        status,
                        no_fill_reason,
                        decision_id,
                        PAPER_EXECUTION_PROTOCOL_V5,
                        expires_at,
                        fill_at,
                        "VALIDATED",
                        capacity_key,
                        "PENDING" if status == "OPEN" else None,
                    ),
                )
                position_id = int(cur.lastrowid)

                if filled > 0.0:
                    if cap is None:
                        db.execute(
                            "INSERT INTO weather_paper_capacity_usage("
                            "capacity_key,visible_units,consumed_units,updated_at"
                            ") VALUES(?,?,?,?)",
                            (capacity_key, visible, filled, fill_at),
                        )
                    else:
                        db.execute(
                            "UPDATE weather_paper_capacity_usage SET consumed_units=?,updated_at=? "
                            "WHERE capacity_key=?",
                            (consumed + filled, fill_at, capacity_key),
                        )

                db.execute(
                    "UPDATE weather_paper_signals SET payload_json=?,status=? WHERE id=?",
                    (
                        _json(payload),
                        "PAPER_OPENED" if status == "OPEN" else "NO_FILL",
                        int(signal_id),
                    ),
                )
                db.execute(
                    """
                    INSERT INTO weather_paper_decisions(
                        decision_id,event_id,market_id,side,outcome,reason,created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        decision_id,
                        event_id,
                        market_id,
                        side,
                        "OPENED_V5" if status == "OPEN" else "NO_FILL_V5",
                        no_fill_reason,
                        fill_at,
                    ),
                )
                final = db.execute(
                    "SELECT * FROM weather_paper_positions WHERE id=?", (position_id,)
                ).fetchone()
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
        if final is None:
            raise WeatherPaperPositionError("V5_POSITION_LOST_AFTER_COMMIT")
        return self._decode_position(dict(final))

    def mark_post_receipt_not_actionable(
        self,
        signal_id: int,
        *,
        decision_id: str,
        event_id: str,
        market_id: str | None,
        side: str | None,
        reason: str,
        recorded_at: float | None = None,
    ) -> None:
        """Persist terminal non-actionability and its exact reason atomically."""
        sid = int(signal_id)
        if isinstance(signal_id, bool) or sid <= 0:
            raise WeatherPaperPositionError("V5_SIGNAL_ID_INVALID")
        did = _identity_text(decision_id, "V5_DECISION_ID_MISSING")
        eid = _identity_text(event_id, "V5_EVENT_ID_MISSING")
        why = _identity_text(reason, "V5_NOT_ACTIONABLE_REASON_MISSING")
        at = time.time() if recorded_at is None else _finite(
            recorded_at, "V5_NOT_ACTIONABLE_TIME_INVALID"
        )
        if at < 0.0:
            raise WeatherPaperPositionError("V5_NOT_ACTIONABLE_TIME_INVALID")
        with self._conn() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                signal = db.execute(
                    "SELECT payload_json,telegram_message_id FROM weather_paper_signals WHERE id=?",
                    (sid,),
                ).fetchone()
                if signal is None:
                    raise WeatherPaperPositionError("V5_SIGNAL_NOT_FOUND")
                if signal["telegram_message_id"] is None:
                    raise WeatherPaperPositionError("V5_TELEGRAM_RECEIPT_MISSING")
                payload = _payload(signal["payload_json"])
                if payload.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V5:
                    raise WeatherPaperPositionError("V5_SIGNAL_PROTOCOL_MISMATCH")
                existing = db.execute(
                    "SELECT id FROM weather_paper_positions WHERE signal_id=?",
                    (sid,),
                ).fetchone()
                if existing is not None:
                    raise WeatherPaperPositionError("V5_NOT_ACTIONABLE_POSITION_ALREADY_EXISTS")
                db.execute(
                    "UPDATE weather_paper_signals SET status='POST_RECEIPT_NOT_ACTIONABLE' WHERE id=?",
                    (sid,),
                )
                db.execute(
                    """
                    INSERT INTO weather_paper_decisions(
                        decision_id,event_id,market_id,side,outcome,reason,created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        did,
                        eid,
                        None if market_id is None else str(market_id),
                        None if side is None else str(side),
                        "POST_RECEIPT_NOT_ACTIONABLE",
                        why,
                        at,
                    ),
                )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def reconcile_v5_after_restart(self) -> dict:
        """Classify interrupted V5 work without reconstructing executable fills."""
        delivery_uncertain: list[int] = []
        actionability_unproven: list[int] = []
        presend_abandoned: list[int] = []
        now = time.time()

        with self._conn() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                rows = [
                    dict(row)
                    for row in db.execute(
                        """
                        SELECT s.*,p.id AS position_id
                        FROM weather_paper_signals s
                        LEFT JOIN weather_paper_positions p ON p.signal_id=s.id
                        ORDER BY s.id
                        """
                    )
                ]
                for row in rows:
                    payload = _payload(row.get("payload_json"))
                    if payload.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V5:
                        continue
                    if row.get("position_id") is not None:
                        continue
                    sid = int(row["id"])
                    status = str(row.get("status") or "")
                    receipt = row.get("telegram_message_id")
                    if status == "OPEN" and receipt is None:
                        db.execute(
                            "UPDATE weather_paper_signals SET status='ABANDONED_PRE_SEND' WHERE id=?",
                            (sid,),
                        )
                        presend_abandoned.append(sid)
                    elif status == "PENDING_DELIVERY" and receipt is None:
                        db.execute(
                            "UPDATE weather_paper_signals SET status='DELIVERY_UNCERTAIN' WHERE id=?",
                            (sid,),
                        )
                        delivery_uncertain.append(sid)
                    elif receipt is not None and status not in {
                        "DELIVERY_FAILED",
                        "DELIVERY_UNCERTAIN",
                        "EXPIRED",
                        "ACTIONABILITY_UNPROVEN",
                        "POST_RECEIPT_NOT_ACTIONABLE",
                        "PAPER_ACCOUNTING_ERROR",
                    }:
                        db.execute(
                            "UPDATE weather_paper_signals SET status='ACTIONABILITY_UNPROVEN' WHERE id=?",
                            (sid,),
                        )
                        actionability_unproven.append(sid)

                by_id = {int(row["id"]): row for row in rows}
                for sid in delivery_uncertain:
                    signal = by_id[sid]
                    payload = _payload(signal.get("payload_json"))
                    db.execute(
                        """
                        INSERT INTO weather_paper_decisions(
                            decision_id,event_id,market_id,side,outcome,reason,created_at
                        ) VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            str(payload.get("decision_id") or signal.get("fingerprint") or sid),
                            str(signal.get("event_id") or ""),
                            str(signal.get("market_id") or "") or None,
                            str(signal.get("side") or "") or None,
                            "DELIVERY_UNCERTAIN",
                            "PROCESS_RESTART_WITH_INFLIGHT_V5_DELIVERY",
                            now,
                        ),
                    )
                for sid in actionability_unproven:
                    signal = by_id[sid]
                    payload = _payload(signal.get("payload_json"))
                    db.execute(
                        """
                        INSERT INTO weather_paper_decisions(
                            decision_id,event_id,market_id,side,outcome,reason,created_at
                        ) VALUES(?,?,?,?,?,?,?)
                        """,
                        (
                            str(payload.get("decision_id") or signal.get("fingerprint") or sid),
                            str(signal.get("event_id") or ""),
                            str(signal.get("market_id") or "") or None,
                            str(signal.get("side") or "") or None,
                            "ACTIONABILITY_UNPROVEN",
                            "PROCESS_RESTART_BEFORE_DURABLE_POST_RECEIPT_ADMISSION",
                            now,
                        ),
                    )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

        return {
            "version": PAPER_RECOVERY_V5_VERSION,
            "delivery_uncertain_recovered": len(delivery_uncertain),
            "actionability_unproven_after_restart": len(actionability_unproven),
            "presend_abandoned": len(presend_abandoned),
            "reconstructed_fills": 0,
            "financial_authority": False,
            "automatic_order_placement": False,
        }

    def lane_stats(self) -> list[dict]:
        with self._conn() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT lane,
                           COUNT(*) AS total,
                           SUM(CASE WHEN status='OPEN' THEN 1 ELSE 0 END) AS open_n,
                           SUM(CASE WHEN status='NO_FILL' THEN 1 ELSE 0 END) AS no_fill,
                           SUM(CASE WHEN status='WON' THEN 1 ELSE 0 END) AS won,
                           SUM(CASE WHEN status='LOST' THEN 1 ELSE 0 END) AS lost,
                           SUM(CASE WHEN status='RESOLVED_PARTIAL' THEN 1 ELSE 0 END) AS partial,
                           SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL')
                                    THEN capital_used ELSE 0 END) AS resolved_capital,
                           SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL')
                                    THEN proceeds ELSE 0 END) AS resolved_proceeds,
                           SUM(CASE WHEN status IN ('WON','LOST','RESOLVED_PARTIAL')
                                    THEN pnl ELSE 0 END) AS pnl
                    FROM weather_paper_positions
                    WHERE validation_state='VALIDATED'
                    GROUP BY lane ORDER BY lane
                    """
                )
            ]
        for row in rows:
            capital = float(row.get("resolved_capital") or 0.0)
            pnl = float(row.get("pnl") or 0.0)
            row["resolved_roi"] = pnl / capital if capital > 0.0 else None
        return rows
