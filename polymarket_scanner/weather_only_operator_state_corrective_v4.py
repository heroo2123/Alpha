from __future__ import annotations

"""Strict terminal identity plus restart-visible invalidation on immutable retry V3."""

import math

from .weather_only_operator_state_corrective_v3 import OperatorStatePostReceiptStoreV3
from .weather_only_paper_positions import WeatherPaperPositionError, _payload
from .weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5


OPERATOR_STATE_CORRECTIVE_V4_VERSION = (
    "weather_operator_state_v4_terminal_identity_restart_visibility"
)
_RESTART_UNPROVEN_REASON = "PROCESS_RESTART_BEFORE_DURABLE_POST_RECEIPT_ADMISSION"


class OperatorStatePostReceiptStoreV4(OperatorStatePostReceiptStoreV3):
    """Add terminal-call identity and restart visibility without mutating old evidence."""

    def _restart_unproven_signal_ids(self) -> list[int]:
        recovered: list[int] = []
        with self._conn() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT s.id,s.payload_json
                    FROM weather_paper_signals s
                    LEFT JOIN weather_paper_operator_sync o ON o.signal_id=s.id
                    WHERE s.status='ACTIONABILITY_UNPROVEN'
                      AND s.telegram_message_id IS NOT NULL
                      AND o.signal_id IS NULL
                    ORDER BY s.id
                    """
                )
            ]
            for row in rows:
                payload = _payload(row.get("payload_json"))
                if payload.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V5:
                    continue
                decision_id = str(payload.get("decision_id") or "").strip()
                if not decision_id:
                    continue
                decision = db.execute(
                    """
                    SELECT outcome,reason FROM weather_paper_decisions
                    WHERE decision_id=? ORDER BY id DESC LIMIT 1
                    """,
                    (decision_id,),
                ).fetchone()
                if decision is None:
                    continue
                if (
                    str(decision["outcome"] or "") == "ACTIONABILITY_UNPROVEN"
                    and str(decision["reason"] or "") == _RESTART_UNPROVEN_REASON
                ):
                    recovered.append(int(row["id"]))
        return recovered

    def reconcile_v5_after_restart(self) -> dict:
        result = dict(super().reconcile_v5_after_restart())
        recovered = self._restart_unproven_signal_ids()
        created = self.ensure_operator_sync_records(signal_ids=recovered) if recovered else 0
        result.update(
            {
                "operator_restart_visibility_version": OPERATOR_STATE_CORRECTIVE_V4_VERSION,
                "operator_restart_unproven_candidates": len(recovered),
                "operator_restart_sync_records_created": int(created),
            }
        )
        return result

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
        sid = int(signal_id)
        with self._conn() as db:
            row = db.execute(
                "SELECT * FROM weather_paper_signals WHERE id=?", (sid,)
            ).fetchone()
        if row is None:
            raise WeatherPaperPositionError("V5_SIGNAL_NOT_FOUND")
        signal = dict(row)
        payload = _payload(signal.get("payload_json"))
        if payload.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V5:
            raise WeatherPaperPositionError("V5_TERMINAL_PROTOCOL_MISMATCH")
        if str(signal.get("status") or "") != "POST_RECEIPT_RECHECK":
            raise WeatherPaperPositionError("V5_TERMINAL_PRESTATE_INVALID")
        message_id = signal.get("telegram_message_id")
        if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
            raise WeatherPaperPositionError("V5_TERMINAL_TELEGRAM_RECEIPT_INVALID")
        sent_at = signal.get("telegram_sent_at")
        if isinstance(sent_at, bool) or not isinstance(sent_at, (int, float)):
            raise WeatherPaperPositionError("V5_TERMINAL_TELEGRAM_TIME_INVALID")
        if not math.isfinite(float(sent_at)) or float(sent_at) < 0.0:
            raise WeatherPaperPositionError("V5_TERMINAL_TELEGRAM_TIME_INVALID")
        signal_side = str(signal.get("side") or "").upper()
        if signal_side in {"YES", "NO"} and not str(signal.get("token_id") or "").strip():
            raise WeatherPaperPositionError("V5_TERMINAL_TOKEN_IDENTITY_MISSING")
        if not str(payload.get("decision_id") or "").strip():
            raise WeatherPaperPositionError("V5_TERMINAL_DECISION_IDENTITY_MISSING")

        return super().mark_post_receipt_not_actionable(
            sid,
            decision_id=decision_id,
            event_id=event_id,
            market_id=market_id,
            side=side,
            reason=reason,
            recorded_at=recorded_at,
        )
