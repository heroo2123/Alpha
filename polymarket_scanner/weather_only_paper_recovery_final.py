from __future__ import annotations

"""Final fail-closed paper-store boundary used by the deployable weather runtime.

The inherited v4 position builder already rejects receipts/fills *after* expiry.  The
live send path treats the expiry instant itself as expired as well, so restart recovery
must use the same closed interval.  This wrapper keeps crash recovery from promoting a
receipt or simulated fill that landed exactly on the decision expiry boundary.
"""

import math

from .weather_only_paper_corrective import PAPER_EXECUTION_PROTOCOL_V4
from .weather_only_paper_positions import WeatherPaperPositionError, _payload
from .weather_only_paper_recovery import CrashSafeWeatherPaperStore


FINAL_RECOVERY_GUARD_VERSION = "weather_paper_final_recovery_guard_v1_expiry_boundary"


def _finite_epoch(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherPaperPositionError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherPaperPositionError(code)
    return number


class FinalCrashSafeWeatherPaperStore(CrashSafeWeatherPaperStore):
    def ensure_position_for_signal(
        self, signal_id: int, target_stake_usd: float
    ) -> dict | None:
        signal = self._load_signal(int(signal_id))
        if signal is None:
            return None
        payload = _payload(signal.get("payload_json"))
        if (
            payload.get("paper_execution_protocol_version") == PAPER_EXECUTION_PROTOCOL_V4
            and str(signal.get("status") or "") == "ACKNOWLEDGED"
        ):
            sent_at = _finite_epoch(
                signal.get("telegram_sent_at"), "V4_TELEGRAM_TIME_MISSING"
            )
            fill_at = _finite_epoch(payload.get("paper_fill_at"), "V4_FILL_TIME_MISSING")
            expires_at = _finite_epoch(
                payload.get("decision_expires_at"), "V4_EXPIRY_MISSING"
            )
            if sent_at >= expires_at or fill_at >= expires_at:
                raise WeatherPaperPositionError("V4_DECISION_EXPIRED")
        return super().ensure_position_for_signal(int(signal_id), target_stake_usd)
