from __future__ import annotations

"""Atomic delivered-terminal transition with exact identity and operator-sync creation."""

import math
import time

from .weather_only_operator_state_corrective import TERMINAL_VISIBLE_STATUSES, OPERATOR_SYNC_APPLIED, OPERATOR_SYNC_PENDING, _sha
from .weather_only_operator_state_corrective_v4 import OperatorStatePostReceiptStoreV4
from .weather_only_paper_positions import WeatherPaperPositionError, _payload

OPERATOR_STATE_CORRECTIVE_V5_VERSION = "weather_operator_state_v5_atomic_delivered_terminal_sync"
_ALLOWED_PRESTATES = {
    "EXPIRED": {"PENDING_DELIVERY", "POST_RECEIPT_RECHECK"},
    "PAPER_ACCOUNTING_ERROR": {"POST_RECEIPT_RECHECK"},
    "POST_RECEIPT_NOT_ACTIONABLE": {"POST_RECEIPT_RECHECK"},
    "MAKER_NOT_ACTIVATED": {"POST_RECEIPT_RECHECK"},
    "MAKER_NOT_ACTIVATED_RESTART_COVERAGE_LOST": {"POST_RECEIPT_RECHECK", "PENDING_DELIVERY"},
    "ACTIONABILITY_UNPROVEN": {"POST_RECEIPT_RECHECK"},
}


class OperatorStatePostReceiptStoreV5(OperatorStatePostReceiptStoreV4):
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
        if sid <= 0 or status not in TERMINAL_VISIBLE_STATUSES or status not in _ALLOWED_PRESTATES:
            raise WeatherPaperPositionError("DELIVERED_TERMINAL_STATUS_INVALID")
        if not why or not decision or not expected_event:
            raise WeatherPaperPositionError("DELIVERED_TERMINAL_IDENTITY_INVALID")
        at = time.time() if recorded_at is None else float(recorded_at)
        if not math.isfinite(at) or at < 0:
            raise WeatherPaperPositionError("DELIVERED_TERMINAL_TIME_INVALID")

        with self._conn() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                row = db.execute("SELECT * FROM weather_paper_signals WHERE id=?", (sid,)).fetchone()
                if row is None:
                    raise WeatherPaperPositionError("V5_SIGNAL_NOT_FOUND")
                signal = dict(row)
                payload = _payload(signal.get("payload_json"))
                receipt = signal.get("telegram_message_id")
                sent_at = signal.get("telegram_sent_at")
                if isinstance(receipt, bool) or not isinstance(receipt, int) or receipt <= 0:
                    raise WeatherPaperPositionError("DELIVERED_TERMINAL_RECEIPT_MISSING")
                if isinstance(sent_at, bool) or not isinstance(sent_at, (int, float)) or not math.isfinite(float(sent_at)) or float(sent_at) < 0:
                    raise WeatherPaperPositionError("DELIVERED_TERMINAL_RECEIPT_TIME_INVALID")
                current = str(signal.get("status") or "").strip().upper()
                if current == status:
                    existing = db.execute(
                        "SELECT state,reason FROM weather_paper_operator_sync WHERE signal_id=?", (sid,)
                    ).fetchone()
                    if existing is None or str(existing["reason"] or "") != why:
                        raise WeatherPaperPositionError("DELIVERED_TERMINAL_IDEMPOTENCY_CONFLICT")
                    db.execute("COMMIT")
                    return
                if current not in _ALLOWED_PRESTATES[status]:
                    raise WeatherPaperPositionError("DELIVERED_TERMINAL_PRESTATE_INVALID")
                if str(signal.get("event_id") or "") != expected_event:
                    raise WeatherPaperPositionError("DELIVERED_TERMINAL_EVENT_ID_MISMATCH")
                actual_market = str(signal.get("market_id") or "").strip() or None
                if actual_market != expected_market:
                    raise WeatherPaperPositionError("DELIVERED_TERMINAL_MARKET_ID_MISMATCH")
                actual_side = str(signal.get("side") or "").strip().upper() or None
                if actual_side != expected_side:
                    raise WeatherPaperPositionError("DELIVERED_TERMINAL_SIDE_MISMATCH")
                actual_token = str(signal.get("token_id") or "").strip() or None
                if expected_token is not None and actual_token != expected_token:
                    raise WeatherPaperPositionError("DELIVERED_TERMINAL_TOKEN_ID_MISMATCH")
                if actual_side in {"YES", "NO"} and not actual_token:
                    raise WeatherPaperPositionError("DELIVERED_TERMINAL_TOKEN_IDENTITY_MISSING")

                lane = str(signal.get("lane") or "")
                payload_decision = str(payload.get("decision_id") or "").strip()
                if lane == "weather_maker_virtual_bid":
                    if decision != expected_event or str(payload.get("order_id") or "").strip() == "":
                        raise WeatherPaperPositionError("DELIVERED_TERMINAL_MAKER_IDENTITY_INVALID")
                    if str(payload.get("fingerprint") or "").strip() and str(payload.get("fingerprint")) != str(signal.get("fingerprint") or ""):
                        raise WeatherPaperPositionError("DELIVERED_TERMINAL_FINGERPRINT_MISMATCH")
                else:
                    if not payload_decision or payload_decision != decision:
                        raise WeatherPaperPositionError("DELIVERED_TERMINAL_DECISION_ID_MISMATCH")

                db.execute("UPDATE weather_paper_signals SET status=? WHERE id=?", (status, sid))
                db.execute(
                    """INSERT INTO weather_paper_decisions(
                         decision_id,event_id,market_id,side,outcome,reason,created_at
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (decision, expected_event, expected_market, expected_side, status, why, at),
                )
                sync_row = dict(signal)
                sync_row["status"] = status
                message_text = self._sync_message(sync_row, why)
                message_sha = _sha(message_text)
                existing = db.execute(
                    "SELECT state,reason,message_sha256 FROM weather_paper_operator_sync WHERE signal_id=?",
                    (sid,),
                ).fetchone()
                if existing is not None and str(existing["state"]) == OPERATOR_SYNC_APPLIED:
                    raise WeatherPaperPositionError("DELIVERED_TERMINAL_ALREADY_VISIBLE_CONFLICT")
                db.execute(
                    """INSERT INTO weather_paper_operator_sync(
                         signal_id,telegram_message_id,base_fingerprint,terminal_status,
                         reason,message_sha256,state,attempts,last_error,created_at,updated_at,
                         applied_at,fingerprint_released
                       ) VALUES(?,?,?,?,?,?,?,0,NULL,?,?,NULL,0)
                       ON CONFLICT(signal_id) DO UPDATE SET
                         terminal_status=excluded.terminal_status,
                         reason=excluded.reason,
                         message_sha256=excluded.message_sha256,
                         state=?,
                         last_error=NULL,
                         updated_at=excluded.updated_at,
                         applied_at=NULL""",
                    (
                        sid, int(receipt), str(signal.get("fingerprint") or ""), status, why,
                        message_sha, OPERATOR_SYNC_PENDING, at, at, OPERATOR_SYNC_PENDING,
                    ),
                )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise
