from __future__ import annotations

import html
import math
import time

from .weather_only_live_paper_all_signals_final_v9 import FinalOperatorStatePostReceiptStore
from .weather_only_operator_state_corrective import (
    OPERATOR_STATE_CORRECTIVE_VERSION,
    OPERATOR_SYNC_APPLIED,
    OPERATOR_SYNC_FAILED,
    TERMINAL_VISIBLE_STATUSES,
    _sha,
)
from .weather_only_paper_positions import WeatherPaperPositionError, _payload


OPERATOR_STATE_CORRECTIVE_V5_VERSION = (
    "weather_operator_state_v5_atomic_delivered_terminalization"
)
OPERATOR_SYNC_ABSENT = "ABSENT"
_DELIVERED_TERMINAL_PRESTATES = {
    "PENDING_DELIVERY",
    "ACKNOWLEDGED",
    "POST_RECEIPT_RECHECK",
    "MAKER_RESTING",
}


class OperatorStatePostReceiptStoreV5(FinalOperatorStatePostReceiptStore):
    """Atomic durable terminalization plus explicit APPLIED/ABSENT/FAILED sync state."""

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
    ) -> dict:
        sid = int(signal_id)
        status = str(terminal_status or "").strip()
        why = str(reason or "").strip()
        decision = str(decision_id or "").strip()
        event = str(event_id or "").strip()
        at = time.time() if recorded_at is None else float(recorded_at)
        if (
            sid <= 0
            or status not in TERMINAL_VISIBLE_STATUSES
            or not why
            or not decision
            or not event
            or not math.isfinite(at)
            or at < 0.0
        ):
            raise WeatherPaperPositionError("V5_TERMINAL_ARGUMENT_INVALID")

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
                prestate = str(signal.get("status") or "")
                if prestate not in _DELIVERED_TERMINAL_PRESTATES:
                    raise WeatherPaperPositionError("V5_TERMINAL_PRESTATE_INVALID")
                if str(signal.get("event_id") or "") != event:
                    raise WeatherPaperPositionError("V5_TERMINAL_EVENT_IDENTITY_MISMATCH")
                if str(signal.get("market_id") or "") != str(market_id or ""):
                    raise WeatherPaperPositionError("V5_TERMINAL_MARKET_IDENTITY_MISMATCH")
                if str(signal.get("side") or "").upper() != str(side or "").upper():
                    raise WeatherPaperPositionError("V5_TERMINAL_SIDE_IDENTITY_MISMATCH")
                signal_token = str(signal.get("token_id") or "")
                supplied_token = str(token_id or "")
                if signal_token != supplied_token:
                    raise WeatherPaperPositionError("V5_TERMINAL_TOKEN_IDENTITY_MISMATCH")
                payload_decision = str(payload.get("decision_id") or "").strip()
                if payload_decision and payload_decision != decision:
                    raise WeatherPaperPositionError(
                        "V5_TERMINAL_DECISION_IDENTITY_MISMATCH"
                    )

                title = str(payload.get("event_title") or signal.get("event_id") or "Weather signal")
                lane = str(signal.get("lane") or "unknown")
                former_side = str(signal.get("side") or "BASKET")
                text = "\n".join(
                    [
                        "⛔ <b>INVALIDATED — DO NOT ACT</b>",
                        f"<b>{html.escape(title[:180])}</b>",
                        f"Lane: <code>{html.escape(lane)}</code>",
                        f"Former side: <b>{html.escape(former_side)}</b>",
                        "",
                        f"Final state: <b>{html.escape(status)}</b>",
                        f"Reason: <code>{html.escape(why[:800])}</code>",
                        "",
                        "🚫 <b>Do not place a trade from the earlier alert.</b>",
                        "No validated PAPER position was opened from that alert.",
                    ]
                )
                base_fingerprint = str(
                    payload.get("operator_retry_base_fingerprint")
                    or signal.get("fingerprint")
                    or ""
                ).strip()
                if not base_fingerprint:
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
                    (decision, event, market_id, side, status, why, at),
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
                        base_fingerprint=excluded.base_fingerprint,
                        message_sha256=excluded.message_sha256,
                        state=CASE
                            WHEN weather_paper_operator_sync.state IN ('APPLIED','ABSENT')
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
                        base_fingerprint,
                        _sha(text),
                        "PENDING",
                        at,
                        at,
                    ),
                )
                db.execute("COMMIT")
                return {
                    "signal_id": sid,
                    "telegram_message_id": int(message_id),
                    "terminal_status": status,
                    "reason": why,
                    "prestate": prestate,
                    "base_fingerprint": base_fingerprint,
                }
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
        # Parent records the explicit remote absence and performs the same immutable
        # retry-release transaction used for an applied edit. We then preserve the
        # distinct terminal transport result instead of collapsing it into APPLIED.
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
