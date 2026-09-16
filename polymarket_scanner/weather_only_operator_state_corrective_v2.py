from __future__ import annotations

"""Second operator-state corrective: align retry guards with final source-shock episode fingerprints."""

from .weather_only_independent_review_corrective import (
    SOURCE_SHOCK_LANE,
    _canonical_sha,
    _finite,
    _text,
)
from .weather_only_operator_state_corrective import OperatorStatePostReceiptStore
from .weather_only_paper_positions import WeatherPaperPositionError


OPERATOR_STATE_CORRECTIVE_V2_VERSION = (
    "weather_operator_state_v2_source_shock_final_fingerprint_retry_guard"
)


class OperatorStatePostReceiptStoreV2(OperatorStatePostReceiptStore):
    """Run cooldown/dedupe policy on the same source-shock episode identity persisted by V1."""

    @staticmethod
    def _final_fingerprint(kwargs: dict) -> str:
        lane = str(kwargs.get("lane") or "")
        if lane != SOURCE_SHOCK_LANE:
            return str(kwargs.get("fingerprint") or "").strip()
        payload = kwargs.get("payload")
        if not isinstance(payload, dict):
            raise WeatherPaperPositionError("SOURCE_SHOCK_SIGNAL_PAYLOAD_INVALID")
        episode = {
            "lane": SOURCE_SHOCK_LANE,
            "event_id": _text(kwargs.get("event_id"), "SOURCE_SHOCK_EVENT_ID_MISSING"),
            "market_id": _text(kwargs.get("market_id"), "SOURCE_SHOCK_MARKET_ID_MISSING"),
            "token_id": _text(kwargs.get("token_id"), "SOURCE_SHOCK_TOKEN_ID_MISSING"),
            "side": str(kwargs.get("side") or "").upper(),
            "previous_official_extreme": _finite(
                payload.get("previous_official_extreme"),
                "SOURCE_SHOCK_PREVIOUS_EXTREME_INVALID",
            ),
            "new_official_extreme": _finite(
                payload.get("new_official_extreme"),
                "SOURCE_SHOCK_NEW_EXTREME_INVALID",
            ),
            "latest_official_observed_at": _finite(
                payload.get("latest_official_observed_at"),
                "SOURCE_SHOCK_OBSERVED_AT_INVALID",
            ),
        }
        if episode["side"] != "NO":
            raise WeatherPaperPositionError("SOURCE_SHOCK_SIDE_INVALID")
        return _canonical_sha(episode)

    def save_signal(self, **kwargs):
        updated = dict(kwargs)
        updated["fingerprint"] = self._final_fingerprint(updated)
        return super().save_signal(**updated)
