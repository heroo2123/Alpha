from __future__ import annotations

"""Atomic delivered-terminal transition with exact identity and immediate-sync state."""

import math
import time

from .weather_only_live_paper_all_signals_final_v9 import FinalOperatorStatePostReceiptStore
from .weather_only_operator_state_corrective import (
    OPERATOR_STATE_CORRECTIVE_VERSION,
    OPERATOR_SYNC_APPLIED,
    OPERATOR_SYNC_FAILED,
    OPERATOR_SYNC_PENDING,
    TERMINAL_VISIBLE_STATUSES,
    _sha,
)
from .weather_only_paper_positions import WeatherPaperPositionError, _payload


OPERATOR_STATE_CORRECTIVE_V5_VERSION = (
    "weather_operator_state_v5_atomic_delivered_terminal_sync"
)
OPERATOR_SYNC_ABSENT = "ABSENT"
_ALLOWED_PRESTATES = {
    "EXPIRED": {"PENDING_DELIVERY", "POST_RECEIPT_RECHECK"},
    "PAPER_ACCOUNTING_ERROR": {"POST_RECEIPT_RECHECK"},
    "POST_RECEIPT_NOT_ACTIONABLE": {"POST_RECEIPT_RECHECK"},
    "MAKER_NOT_ACTIVATED": {"POST_RECEIPT_RECHECK"},
    "MAKER_NOT_ACTIVATED_RESTART_COVERAGE_LOST": {
        "POST_RECEIPT_RECHECK",
        "PENDING_DELIVERY",
    },
    "ACTIONABILITY_UNPROVEN": {"POST_RECEIPT_RECHECK"},
}


class OperatorStatePostReceiptStoreV5(FinalOperatorStatePostReceiptStore):
    """One atomic terminal transition for every confirmed delivered PAPER signal."""

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
        token_id: str | None = None,
        recorded_at: float | None = None,
    ) -> None:
        sid = int(signal_id)
        status = str(terminal_status or "").strip().upper()
        why = str(reason or "").strip()
        decision = str(decision_id or "").strip()
        expected_event = str(event_id or "").strip()
        expected_market = None if market_id is None else str(market_id).strip()
        expected_side = None if side is None else str(side).strip().upper()
        expected_token = None if token_id is None else str(token_id).strip()
        if (
            sid <= 0
            or status not in TERMINAL_VISIBLE_STATUSES
            or status not in _ALLOWED_PRESTATES
        ):
            raise WeatherPaperPositionError("DELIVERED_TERMINAL_STATUS_INVALID")
        if not why or not decision or not expected_event:
            raise WeatherPaperPositionError("DELIVERED_TERMINAL_IDENTITY_INVALID")
        at = time.time() if recorded_at is None else float(recorded_at)
        if not math.isfinite(at) or at < 0.0:
            raise WeatherPaperPositionError("DELIVERED_TERMINAL_TIME_INVALID")

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
                receipt = signal.get("telegram_message_id")
                sent_at = signal.get("telegram_sent_at")
                if (
                    isinstance(receipt, bool)
                    or not isinstance(receipt, int)
                    or receipt <= 0
                ):
                    raise WeatherPaperPositionError(
                        "DELIVERED_TERMINAL_RECEIPT_MISSING"
                    )
                if (
                    isinstance(sent_at, bool)
                    or not isinstance(sent_at, (int, float))
                    or not math.isfinite(float(sent_at))
                    or float(sent_at) < 0.0
                ):
                    raise WeatherPaperPositionError(
                        "DELIVERED_TERMINAL_RECEIPT_TIME_INVALID"
                    )

                current = str(signal.get("status") or "").strip().upper()
                if current == status:
                    existing = db.execute(
                        "SELECT state,reason FROM weather_paper_operator_sync WHERE signal_id=?",
                        (sid,),
                    ).fetchone()
                    if existing is None or str(existing["reason"] or "") != why:
                        raise WeatherPaperPositionError(
                            "DELIVERED_TERMINAL_IDEMPOTENCY_CONFLICT"
                        )
                    db.execute("COMMIT")
                    return
                if current not in _ALLOWED_PRESTATES[status]:
                    raise WeatherPaperPositionError("DELIVERED_TERMINAL_PRESTATE_INVALID")
                if str(signal.get("event_id") or "") != expected_event:
                    raise WeatherPaperPositionError(
                        "DELIVERED_TERMINAL_EVENT_ID_MISMATCH"
                    )
                actual_market = str(signal.get("market_id") or "").strip() or None
                if actual_market != expected_market:
                    raise WeatherPaperPositionError(
                        "DELIVERED_TERMINAL_MARKET_ID_MISMATCH"
                    )
                actual_side = str(signal.get("side") or "").strip().upper() or None
                if actual_side != expected_side:
                    raise WeatherPaperPositionError("DELIVERED_TERMINAL_SIDE_MISMATCH")
                actual_token = str(signal.get("token_id") or "").strip() or None
                if expected_token is not None and actual_token != expected_token:
                    raise WeatherPaperPositionError(
                        "DELIVERED_TERMINAL_TOKEN_ID_MISMATCH"
                    )
                if actual_side in {"YES", "NO"} and not actual_token:
                    raise WeatherPaperPositionError(
                        "DELIVERED_TERMINAL_TOKEN_IDENTITY_MISSING"
                    )

                payload_decision = str(payload.get("decision_id") or "").strip()
                lane = str(signal.get("lane") or "")
                if lane == "weather_maker_virtual_bid":
                    order_id = str(payload.get("order_id") or "").strip()
                    if not order_id or payload_decision != order_id or decision != order_id:
                        raise WeatherPaperPositionError(
                            "DELIVERED_TERMINAL_MAKER_IDENTITY_INVALID"
                        )
                elif not payload_decision or payload_decision != decision:
                    raise WeatherPaperPositionError(
                        "DELIVERED_TERMINAL_DECISION_ID_MISMATCH"
                    )

                root_fingerprint = str(
                    payload.get("operator_retry_base_fingerprint")
                    or signal.get("fingerprint")
                    or ""
                ).strip()
                if not root_fingerprint:
                    raise WeatherPaperPositionError(
                        "OPERATOR_SYNC_BASE_FINGERPRINT_MISSING"
                    )

                db.execute(
                    "UPDATE weather_paper_signals SET status=? WHERE id=?",
                    (status, sid),
                )
                db.execute(
                    """
                    INSERT INTO weather_paper_decisions(
                        decision_id,event_id,market_id,side,outcome,reason,created_at
                    ) VALUES(?,?,?,?,?,?,?)
                    """,
                    (
                        decision,
                        expected_event,
                        expected_market,
                        expected_side,
                        status,
                        why,
                        at,
                    ),
                )
                sync_row = dict(signal)
                sync_row["status"] = status
                message_text = self._sync_message(sync_row, why)
                message_sha = _sha(message_text)
                existing = db.execute(
                    "SELECT state,reason,message_sha256 FROM weather_paper_operator_sync WHERE signal_id=?",
                    (sid,),
                ).fetchone()
                if existing is not None and str(existing["state"]) in {
                    OPERATOR_SYNC_APPLIED,
                    OPERATOR_SYNC_ABSENT,
                }:
                    raise WeatherPaperPositionError(
                        "DELIVERED_TERMINAL_ALREADY_VISIBLE_CONFLICT"
                    )
                db.execute(
                    """
                    INSERT INTO weather_paper_operator_sync(
                        signal_id,version,telegram_message_id,base_fingerprint,
                        terminal_status,reason,message_sha256,state,attempts,last_error,
                        created_at,updated_at,applied_at,fingerprint_released
                    ) VALUES(?,?,?,?,?,?,?,?,0,NULL,?,?,NULL,0)
                    ON CONFLICT(signal_id) DO UPDATE SET
                        version=excluded.version,
                        terminal_status=excluded.terminal_status,
                        reason=excluded.reason,
                        telegram_message_id=excluded.telegram_message_id,
                        base_fingerprint=excluded.base_fingerprint,
                        message_sha256=excluded.message_sha256,
                        state=?,
                        last_error=NULL,
                        updated_at=excluded.updated_at,
                        applied_at=NULL
                    """,
                    (
                        sid,
                        OPERATOR_STATE_CORRECTIVE_VERSION,
                        int(receipt),
                        root_fingerprint,
                        status,
                        why,
                        message_sha,
                        OPERATOR_SYNC_PENDING,
                        at,
                        at,
                        OPERATOR_SYNC_PENDING,
                    ),
                )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def pending_operator_sync(self, limit: int = 50) -> list[dict]:
        count = max(1, min(200, int(limit)))
        with self._conn() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT o.*,s.lane,s.event_id,s.market_id,s.side,s.payload_json,
                           s.status AS signal_status
                    FROM weather_paper_operator_sync o
                    JOIN weather_paper_signals s ON s.id=o.signal_id
                    WHERE o.state NOT IN (?,?)
                    ORDER BY o.signal_id LIMIT ?
                    """,
                    (OPERATOR_SYNC_APPLIED, OPERATOR_SYNC_ABSENT, count),
                )
            ]
        for row in rows:
            row["message_text"] = self._sync_message(
                row, str(row.get("reason") or "")
            )
        return rows

    def mark_operator_sync_absent(
        self,
        signal_id: int,
        telegram_message_id: int,
        detail: str = "TELEGRAM_MESSAGE_TO_EDIT_NOT_FOUND",
    ) -> None:
        sid = int(signal_id)
        # V9 first persists the explicit remote-absence receipt and performs the same
        # visibility-confirmed retry release as an applied edit. Preserve ABSENT as a
        # distinct operator-sync result afterward rather than collapsing it to APPLIED.
        super().mark_operator_sync_absent(sid, int(telegram_message_id), detail)
        with self._conn() as db:
            cur = db.execute(
                "UPDATE weather_paper_operator_sync SET state=?,updated_at=? WHERE signal_id=?",
                (OPERATOR_SYNC_ABSENT, time.time(), sid),
            )
            if cur.rowcount != 1:
                raise WeatherPaperPositionError("OPERATOR_SYNC_SIGNAL_NOT_FOUND")

    def operator_sync_summary(self) -> dict:
        with self._conn() as db:
            total = int(
                db.execute("SELECT COUNT(*) FROM weather_paper_operator_sync").fetchone()[0]
            )
            applied = int(
                db.execute(
                    "SELECT COUNT(*) FROM weather_paper_operator_sync WHERE state=?",
                    (OPERATOR_SYNC_APPLIED,),
                ).fetchone()[0]
            )
            absent = int(
                db.execute(
                    "SELECT COUNT(*) FROM weather_paper_operator_sync WHERE state=?",
                    (OPERATOR_SYNC_ABSENT,),
                ).fetchone()[0]
            )
            failed = int(
                db.execute(
                    "SELECT COUNT(*) FROM weather_paper_operator_sync WHERE state=?",
                    (OPERATOR_SYNC_FAILED,),
                ).fetchone()[0]
            )
        confirmed = applied + absent
        return {
            "version": OPERATOR_STATE_CORRECTIVE_V5_VERSION,
            "total": total,
            "applied": applied,
            "confirmed_absent": absent,
            "confirmed_terminal": confirmed,
            "unconfirmed": total - confirmed,
            "failed": failed,
            "healthy": total == confirmed,
            "deleted_message_is_terminal_confirmation": True,
        }
