from __future__ import annotations

"""Second independent-review corrective boundary for V5 PAPER accounting.

This layer closes residual integrity gaps without adding any order, wallet, signing or
financial authority:

* every executable leg carries its own visible top-of-book quantity and aggregate
  capacity is recomputed as the minimum across legs;
* the complete strong-identity precheck plus parent BEGIN IMMEDIATE admission is
  serialized with an OS file lock, so a conflicting concurrent retry cannot pass a
  stale outer check and then hit the parent's weaker existing-position fast path; and
* multi-leg structural baskets remain useful Telegram research signals but are never
  admitted to validated PAPER P&L because the public CLOB snapshots do not provide an
  atomic cross-market basket execution primitive.

The lock is deliberately external to SQLite. It covers the entire two-stage inherited
validation/admission sequence across threads and processes on the single host.
"""

import fcntl
import json
import math
import os
from pathlib import Path

from .weather_only_independent_review_corrective import (
    IndependentReviewPostReceiptStore,
    _canonical_sha,
    _finite,
    _same,
)
from .weather_only_paper_positions import WeatherPaperPositionError, _payload
from .weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5


INDEPENDENT_REVIEW_CORRECTIVE_V2_VERSION = (
    "weather_all_paper_independent_review_corrective_v2_capacity_serialized_structural_theoretical"
)
STRUCTURAL_THEORETICAL_ONLY_REASON = "STRUCTURAL_MULTI_LEG_ATOMIC_EXECUTION_UNPROVEN"


def _validated_weather_evidence(value: object) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not value:
        raise WeatherPaperPositionError("V5_POST_RECEIPT_WEATHER_EVIDENCE_INVALID")
    try:
        encoded = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        )
        decoded = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        raise WeatherPaperPositionError("V5_POST_RECEIPT_WEATHER_EVIDENCE_INVALID") from None
    if not isinstance(decoded, dict):
        raise WeatherPaperPositionError("V5_POST_RECEIPT_WEATHER_EVIDENCE_INVALID")
    return decoded


class IndependentReviewPostReceiptStoreV2(IndependentReviewPostReceiptStore):
    """Recompute capacity from exact legs and serialize full V5 admission identity."""

    @staticmethod
    def _normalized_execution(execution: dict) -> dict:
        normalized = IndependentReviewPostReceiptStore._normalized_execution(execution)
        raw_legs = execution.get("legs") if isinstance(execution, dict) else None
        if not isinstance(raw_legs, list) or len(raw_legs) != len(normalized["legs"]):
            raise WeatherPaperPositionError("V5_EXECUTION_LEGS_INVALID")

        visible_by_leg: list[float] = []
        upgraded_legs: list[dict] = []
        for raw, leg in zip(raw_legs, normalized["legs"]):
            if not isinstance(raw, dict):
                raise WeatherPaperPositionError("V5_EXECUTION_LEG_INVALID")
            visible = _finite(
                raw.get("visible_units"), "V5_EXECUTION_LEG_VISIBLE_UNITS_INVALID"
            )
            if visible <= 0.0:
                raise WeatherPaperPositionError("V5_EXECUTION_LEG_VISIBLE_UNITS_INVALID")
            upgraded = dict(leg)
            upgraded["visible_units"] = visible
            upgraded_legs.append(upgraded)
            visible_by_leg.append(visible)

        derived_visible = min(visible_by_leg)
        claimed_visible = _finite(
            execution.get("visible_units"), "V5_VISIBLE_UNITS_INVALID"
        )
        if not _same(claimed_visible, derived_visible):
            raise WeatherPaperPositionError("V5_VISIBLE_CAPACITY_LEG_MISMATCH")

        upgraded = dict(normalized)
        upgraded["visible_units"] = derived_visible
        upgraded["legs"] = upgraded_legs
        weather_evidence = _validated_weather_evidence(
            execution.get("post_receipt_weather_evidence")
        )
        if weather_evidence is not None:
            upgraded["post_receipt_weather_evidence"] = weather_evidence
        return upgraded

    @staticmethod
    def _execution_identity(execution: dict) -> str:
        return _canonical_sha(IndependentReviewPostReceiptStoreV2._normalized_execution(execution))

    def _admission_lock_path(self) -> Path:
        return self.path.with_name(self.path.name + ".v5-admission.lock")

    def _mark_structural_theoretical_only(self, signal_id: int, normalized: dict) -> dict:
        """Validate exact signal identity, then atomically terminate without a position."""
        sid = int(signal_id)
        with self._conn() as db:
            signal = db.execute(
                "SELECT * FROM weather_paper_signals WHERE id=?", (sid,)
            ).fetchone()
            existing = db.execute(
                "SELECT * FROM weather_paper_positions WHERE signal_id=?", (sid,)
            ).fetchone()
        if signal is None:
            raise WeatherPaperPositionError("V5_SIGNAL_NOT_FOUND")
        if existing is not None:
            signal_dict = dict(signal)
            payload = _payload(signal_dict.get("payload_json"))
            stored = payload.get("post_receipt_execution")
            if not isinstance(stored, dict):
                raise WeatherPaperPositionError("V5_EXISTING_EXECUTION_EVIDENCE_MISSING")
            if self._execution_identity(stored) != self._execution_identity(normalized):
                raise WeatherPaperPositionError("V5_EXISTING_EXECUTION_IDENTITY_CONFLICT")
            return self._decode_position(dict(existing))

        signal_dict = dict(signal)
        payload = _payload(signal_dict.get("payload_json"))
        if payload.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V5:
            raise WeatherPaperPositionError("V5_SIGNAL_PROTOCOL_MISMATCH")
        if str(payload.get("decision_id") or "") != str(normalized["decision_id"]):
            raise WeatherPaperPositionError("V5_DECISION_IDENTITY_MISMATCH")
        try:
            payload_expiry = float(payload.get("decision_expires_at"))
        except (TypeError, ValueError, OverflowError):
            raise WeatherPaperPositionError("V5_SIGNAL_EXPIRY_INVALID") from None
        if not math.isfinite(payload_expiry) or not _same(
            payload_expiry, normalized["decision_expires_at"]
        ):
            raise WeatherPaperPositionError("V5_EXPIRY_IDENTITY_MISMATCH")
        if signal_dict.get("telegram_message_id") is None or signal_dict.get("telegram_sent_at") is None:
            raise WeatherPaperPositionError("V5_TELEGRAM_RECEIPT_MISSING")
        sent_at = _finite(signal_dict.get("telegram_sent_at"), "V5_TELEGRAM_SENT_AT_INVALID")
        if normalized["post_receipt_recheck_started_at"] + 1e-9 < sent_at:
            raise WeatherPaperPositionError("V5_POST_RECEIPT_RECHECK_NOT_CAUSAL")
        if (
            sent_at >= normalized["decision_expires_at"]
            or normalized["post_receipt_recheck_finished_at"] >= normalized["decision_expires_at"]
        ):
            raise WeatherPaperPositionError("V5_DECISION_EXPIRED")
        signal_payout = signal_dict.get("theoretical_payout")
        if signal_payout is None or not _same(
            _finite(signal_payout, "V5_SIGNAL_PAYOUT_INVALID"),
            normalized["theoretical_payout_per_unit"],
        ):
            raise WeatherPaperPositionError("V5_PAYOUT_IDENTITY_MISMATCH")
        expected_markets = tuple(str(value) for value in (payload.get("market_ids") or ()))
        expected_tokens = tuple(str(value) for value in (payload.get("token_ids") or ()))
        actual_markets = tuple(str(leg["market_id"]) for leg in normalized["legs"])
        actual_tokens = tuple(str(leg["token_id"]) for leg in normalized["legs"])
        if not expected_markets or not expected_tokens:
            raise WeatherPaperPositionError("V5_STRUCTURAL_SIGNAL_LEGS_MISSING")
        if expected_markets != actual_markets or expected_tokens != actual_tokens:
            raise WeatherPaperPositionError("V5_STRUCTURAL_SIGNAL_LEGS_MISMATCH")
        if str(signal_dict.get("status") or "") != "POST_RECEIPT_RECHECK":
            raise WeatherPaperPositionError("V5_SIGNAL_PRESTATE_INVALID")

        self.mark_post_receipt_not_actionable(
            sid,
            decision_id=str(normalized["decision_id"]),
            event_id=str(signal_dict.get("event_id") or ""),
            market_id=None,
            side="BASKET",
            reason=STRUCTURAL_THEORETICAL_ONLY_REASON,
            recorded_at=float(normalized["post_receipt_recheck_finished_at"]),
        )
        return {
            "status": "THEORETICAL_ONLY",
            "signal_id": sid,
            "decision_id": str(normalized["decision_id"]),
            "reason": STRUCTURAL_THEORETICAL_ONLY_REASON,
            "validated_paper_position_created": False,
            "financial_authority": False,
        }

    def admit_post_receipt_position(
        self, signal_id: int, target_stake_usd: float, execution: dict
    ) -> dict:
        lock_path = self._admission_lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(fd, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX)
            normalized = self._normalized_execution(execution)
            if len(normalized["legs"]) > 1:
                return self._mark_structural_theoretical_only(signal_id, normalized)
            return super().admit_post_receipt_position(
                signal_id, target_stake_usd, normalized
            )
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
