from __future__ import annotations

import math
import time

from .weather_only_live_paper_all_signals_final_v9 import FinalOperatorStatePostReceiptStore
from .weather_only_operator_state_corrective import (
    OPERATOR_STATE_CORRECTIVE_VERSION,
    OPERATOR_SYNC_APPLIED,
    TERMINAL_VISIBLE_STATUSES,
    _sha,
)
from .weather_only_paper_positions import WeatherPaperPositionError, _payload
from .weather_only_paper_post_receipt import (
    PAPER_EXECUTION_PROTOCOL_V5,
    PAPER_RECOVERY_V5_VERSION,
)


OPERATOR_STATE_CORRECTIVE_V5_VERSION = (
    "weather_operator_state_v5_atomic_idempotent_delivered_terminalization"
)
_RESTART_UNPROVEN_REASON = "PROCESS_RESTART_BEFORE_DURABLE_POST_RECEIPT_ADMISSION"


def _identity(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise WeatherPaperPositionError(code)
    return text


class OperatorStatePostReceiptStoreV5(FinalOperatorStatePostReceiptStore):
    """One durable transition primitive for every confirmed-delivery terminal state."""

    def terminalize_delivered_signal(
        self,
        signal_id: int,
        *,
        terminal_status: str,
        reason: str,
        decision_id: str,
        event_id: str,
        market_id: str | None,
        side: str | None,
        recorded_at: float | None = None,
    ) -> dict:
        if isinstance(signal_id, bool):
            raise WeatherPaperPositionError("V5_TERMINAL_SIGNAL_ID_INVALID")
        sid = int(signal_id)
        status = str(terminal_status or "").strip()
        why = _identity(reason, "V5_TERMINAL_REASON_MISSING")
        did = _identity(decision_id, "V5_TERMINAL_DECISION_ID_MISSING")
        eid = _identity(event_id, "V5_TERMINAL_EVENT_ID_MISSING")
        at = time.time() if recorded_at is None else float(recorded_at)
        if sid <= 0 or status not in TERMINAL_VISIBLE_STATUSES:
            raise WeatherPaperPositionError("V5_TERMINAL_ARGUMENT_INVALID")
        if not math.isfinite(at) or at < 0.0:
            raise WeatherPaperPositionError("V5_TERMINAL_ARGUMENT_INVALID")

        wanted_market = str(market_id or "")
        wanted_side = str(side or "").upper()
        with self._conn() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute(
                    "SELECT * FROM weather_paper_signals WHERE id=?", (sid,)
                ).fetchone()
                if row is None:
                    raise WeatherPaperPositionError("V5_SIGNAL_NOT_FOUND")
                signal = dict(row)
                payload = _payload(signal.get("payload_json"))
                message_id = signal.get("telegram_message_id")
                sent_at = signal.get("telegram_sent_at")
                if (
                    isinstance(message_id, bool)
                    or not isinstance(message_id, int)
                    or message_id <= 0
                    or isinstance(sent_at, bool)
                    or not isinstance(sent_at, (int, float))
                    or not math.isfinite(float(sent_at))
                    or float(sent_at) < 0.0
                ):
                    raise WeatherPaperPositionError(
                        "V5_TERMINAL_TELEGRAM_RECEIPT_INVALID"
                    )

                if (
                    str(signal.get("event_id") or "") != eid
                    or str(signal.get("market_id") or "") != wanted_market
                    or str(signal.get("side") or "").upper() != wanted_side
                ):
                    raise WeatherPaperPositionError("V5_TERMINAL_IDENTITY_MISMATCH")
                payload_decision = str(payload.get("decision_id") or "").strip()
                if payload_decision and payload_decision != did:
                    raise WeatherPaperPositionError(
                        "V5_TERMINAL_DECISION_IDENTITY_MISMATCH"
                    )

                current = str(signal.get("status") or "")
                if current in TERMINAL_VISIBLE_STATUSES and current != status:
                    raise WeatherPaperPositionError("V5_TERMINAL_STATE_CONFLICT")
                position = db.execute(
                    "SELECT id FROM weather_paper_positions WHERE signal_id=? LIMIT 1",
                    (sid,),
                ).fetchone()
                if position is not None:
                    raise WeatherPaperPositionError(
                        "V5_TERMINAL_VALIDATED_POSITION_ALREADY_EXISTS"
                    )

                existing_decision = db.execute(
                    """
                    SELECT reason FROM weather_paper_decisions
                    WHERE decision_id=? AND event_id=?
                      AND COALESCE(market_id,'')=? AND UPPER(COALESCE(side,''))=?
                      AND outcome=?
                    ORDER BY id DESC LIMIT 1
                    """,
                    (did, eid, wanted_market, wanted_side, status),
                ).fetchone()
                if existing_decision is not None:
                    if str(existing_decision["reason"] or "") != why:
                        raise WeatherPaperPositionError("V5_TERMINAL_REASON_CONFLICT")
                else:
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
                            status,
                            why,
                            at,
                        ),
                    )

                if current != status:
                    db.execute(
                        "UPDATE weather_paper_signals SET status=? WHERE id=?",
                        (status, sid),
                    )

                title = str(
                    payload.get("event_title")
                    or signal.get("event_id")
                    or "Weather signal"
                )
                lane = str(signal.get("lane") or "unknown")
                former = str(signal.get("side") or "BASKET")
                text = "\n".join(
                    [
                        "⛔ <b>INVALIDATED — DO NOT ACT</b>",
                        f"<b>{title[:180]}</b>",
                        f"Lane: <code>{lane}</code>",
                        f"Former side: <b>{former}</b>",
                        "",
                        f"Final state: <b>{status}</b>",
                        f"Reason: <code>{why[:800]}</code>",
                        "",
                        "🚫 <b>Do not place a trade from the earlier alert.</b>",
                    ]
                )
                base = str(
                    payload.get("operator_retry_base_fingerprint")
                    or signal.get("fingerprint")
                    or ""
                ).strip()
                if not base:
                    raise WeatherPaperPositionError(
                        "OPERATOR_SYNC_BASE_FINGERPRINT_MISSING"
                    )

                db.execute(
                    """
                    INSERT INTO weather_paper_operator_sync(
                        signal_id,version,terminal_status,reason,telegram_message_id,
                        base_fingerprint,message_sha256,state,attempts,last_error,
                        created_at,updated_at,applied_at,fingerprint_released
                    ) VALUES(?,?,?,?,?,?,?,?,0,NULL,?,?,NULL,0)
                    ON CONFLICT(signal_id) DO UPDATE SET
                        terminal_status=excluded.terminal_status,
                        reason=excluded.reason,
                        telegram_message_id=excluded.telegram_message_id,
                        message_sha256=excluded.message_sha256,
                        state=CASE
                            WHEN weather_paper_operator_sync.state=?
                                THEN weather_paper_operator_sync.state
                            ELSE 'PENDING'
                        END,
                        updated_at=excluded.updated_at
                    """,
                    (
                        sid,
                        OPERATOR_STATE_CORRECTIVE_VERSION,
                        status,
                        why,
                        int(message_id),
                        base,
                        _sha(text),
                        "PENDING",
                        at,
                        at,
                        OPERATOR_SYNC_APPLIED,
                    ),
                )
                sync = db.execute(
                    "SELECT state,fingerprint_released FROM weather_paper_operator_sync WHERE signal_id=?",
                    (sid,),
                ).fetchone()
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

        return {
            "signal_id": sid,
            "telegram_message_id": int(message_id),
            "terminal_status": status,
            "reason": why,
            "operator_sync_state": str(sync["state"]),
            "fingerprint_released": bool(sync["fingerprint_released"]),
        }

    def reconcile_v5_after_restart(self) -> dict:
        """Recover interrupted V5 work; confirmed receipts use the same terminal primitive."""
        delivery_uncertain: list[int] = []
        actionability: list[dict] = []
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
                    elif receipt is not None and status not in TERMINAL_VISIBLE_STATUSES and status not in {
                        "DELIVERY_FAILED",
                        "DELIVERY_UNCERTAIN",
                    }:
                        actionability.append(
                            {
                                "signal_id": sid,
                                "decision_id": str(
                                    payload.get("decision_id")
                                    or row.get("fingerprint")
                                    or sid
                                ),
                                "event_id": str(row.get("event_id") or ""),
                                "market_id": str(row.get("market_id") or "") or None,
                                "side": str(row.get("side") or "") or None,
                            }
                        )

                by_id = {int(row["id"]): row for row in rows}
                for sid in delivery_uncertain:
                    signal = by_id[sid]
                    payload = _payload(signal.get("payload_json"))
                    did = str(
                        payload.get("decision_id")
                        or signal.get("fingerprint")
                        or sid
                    )
                    exists = db.execute(
                        """
                        SELECT 1 FROM weather_paper_decisions
                        WHERE decision_id=? AND outcome='DELIVERY_UNCERTAIN'
                          AND reason='PROCESS_RESTART_WITH_INFLIGHT_V5_DELIVERY'
                        LIMIT 1
                        """,
                        (did,),
                    ).fetchone()
                    if exists is None:
                        db.execute(
                            """
                            INSERT INTO weather_paper_decisions(
                                decision_id,event_id,market_id,side,outcome,reason,created_at
                            ) VALUES(?,?,?,?,?,?,?)
                            """,
                            (
                                did,
                                str(signal.get("event_id") or ""),
                                str(signal.get("market_id") or "") or None,
                                str(signal.get("side") or "") or None,
                                "DELIVERY_UNCERTAIN",
                                "PROCESS_RESTART_WITH_INFLIGHT_V5_DELIVERY",
                                now,
                            ),
                        )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

        for item in actionability:
            self.terminalize_delivered_signal(
                int(item["signal_id"]),
                terminal_status="ACTIONABILITY_UNPROVEN",
                reason=_RESTART_UNPROVEN_REASON,
                decision_id=str(item["decision_id"]),
                event_id=str(item["event_id"]),
                market_id=item["market_id"],
                side=item["side"],
                recorded_at=now,
            )

        return {
            "version": PAPER_RECOVERY_V5_VERSION,
            "operator_state_version": OPERATOR_STATE_CORRECTIVE_V5_VERSION,
            "delivery_uncertain_recovered": len(delivery_uncertain),
            "actionability_unproven_after_restart": len(actionability),
            "presend_abandoned": len(presend_abandoned),
            "reconstructed_fills": 0,
            "financial_authority": False,
            "automatic_order_placement": False,
        }
