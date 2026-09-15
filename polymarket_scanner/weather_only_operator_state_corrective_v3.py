from __future__ import annotations

"""Final terminal-audit identity guard for operator-synchronized PAPER signals."""

import math

from .weather_only_operator_state_corrective_v2 import OperatorStatePostReceiptStoreV2
from .weather_only_paper_positions import WeatherPaperPositionError, _payload
from .weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5


OPERATOR_STATE_CORRECTIVE_V3_VERSION = (
    "weather_operator_state_v3_terminal_prestate_receipt_token_identity"
)


class OperatorStatePostReceiptStoreV3(OperatorStatePostReceiptStoreV2):
    """Require the exact delivered/recheck state before terminal invalidation."""

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
