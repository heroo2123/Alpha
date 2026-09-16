from __future__ import annotations

"""Independent-review corrective boundaries for the final all-PAPER runtime.

This module is deliberately additive.  It hardens the V5 PAPER accounting ledger,
maker activation idempotency, source-shock episode deduplication, and operator health
reporting without adding any real-order, wallet, signing, or financial authority.
"""

import hashlib
import json
import math

from .weather_only_maker_paper_accounting_v5 import MakerPaperAccountingStoreV5
from .weather_only_maker_store import WeatherMakerStoreError
from .weather_only_paper_commands_all import AllPaperCommandController
from .weather_only_paper_positions import WeatherPaperPositionError, _payload
from .weather_only_paper_post_receipt import (
    PAPER_EXECUTION_PROTOCOL_V5,
    PostReceiptWeatherPaperStore,
)


INDEPENDENT_REVIEW_CORRECTIVE_VERSION = (
    "weather_all_paper_independent_review_corrective_v1_strict_ledger_operator"
)
SOURCE_SHOCK_LANE = "weather_official_extreme_new_exclusion"
_EPSILON = 1e-9


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


def _text(value: object, code: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise WeatherPaperPositionError(code)
    return text


def _same(left: float, right: float, *, tolerance: float = _EPSILON) -> bool:
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=tolerance)


def _canonical_sha(value: object) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class IndependentReviewPostReceiptStore(PostReceiptWeatherPaperStore):
    """Fail closed when a caller supplies internally inconsistent V5 execution data."""

    @staticmethod
    def _normalized_execution(execution: dict) -> dict:
        if not isinstance(execution, dict):
            raise WeatherPaperPositionError("V5_EXECUTION_INVALID")
        if execution.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V5:
            raise WeatherPaperPositionError("V5_EXECUTION_PROTOCOL_MISMATCH")

        started = _finite(
            execution.get("post_receipt_recheck_started_at"),
            "V5_RECHECK_STARTED_AT_INVALID",
        )
        finished = _finite(
            execution.get("post_receipt_recheck_finished_at"),
            "V5_RECHECK_FINISHED_AT_INVALID",
        )
        entry_cost = _finite(execution.get("entry_cost_per_unit"), "V5_ENTRY_COST_INVALID")
        visible = _finite(execution.get("visible_units"), "V5_VISIBLE_UNITS_INVALID")
        minimum = _finite(execution.get("minimum_order_size"), "V5_MINIMUM_ORDER_INVALID")
        payout = _finite(
            execution.get("theoretical_payout_per_unit"), "V5_PAYOUT_INVALID"
        )
        expires = _finite(execution.get("decision_expires_at"), "V5_EXPIRY_INVALID")
        decision_id = _text(execution.get("decision_id"), "V5_DECISION_ID_MISSING")
        if started < 0.0 or finished < started or entry_cost <= 0.0:
            raise WeatherPaperPositionError("V5_EXECUTION_NUMBER_INVALID")
        if visible < 0.0 or minimum < 0.0 or payout <= 0.0 or expires <= 0.0:
            raise WeatherPaperPositionError("V5_EXECUTION_NUMBER_INVALID")

        raw_legs = execution.get("legs")
        if not isinstance(raw_legs, list) or not raw_legs:
            raise WeatherPaperPositionError("V5_EXECUTION_LEGS_INVALID")
        legs: list[dict] = []
        for item in raw_legs:
            if not isinstance(item, dict):
                raise WeatherPaperPositionError("V5_EXECUTION_LEG_INVALID")
            side = _text(item.get("side"), "V5_EXECUTION_SIDE_MISSING").upper()
            if side not in {"YES", "NO"}:
                raise WeatherPaperPositionError("V5_EXECUTION_SIDE_INVALID")
            ask = _finite(item.get("ask"), "V5_EXECUTION_ASK_INVALID")
            fee = _finite(item.get("fee"), "V5_EXECUTION_FEE_INVALID")
            quote_at = _finite(
                item.get("quote_observed_at"), "V5_EXECUTION_QUOTE_TIME_INVALID"
            )
            leg_minimum = _finite(
                item.get("minimum_order_size"), "V5_EXECUTION_MINIMUM_INVALID"
            )
            if not 0.0 < ask < 1.0 or fee < 0.0 or leg_minimum < 0.0:
                raise WeatherPaperPositionError("V5_EXECUTION_LEG_NUMBER_INVALID")
            if quote_at + _EPSILON < started or quote_at > finished + _EPSILON:
                raise WeatherPaperPositionError("V5_EXECUTION_QUOTE_OUTSIDE_RECHECK")
            legs.append(
                {
                    "market_id": _text(item.get("market_id"), "V5_EXECUTION_MARKET_ID_MISSING"),
                    "condition_id": _text(
                        item.get("condition_id"), "V5_EXECUTION_CONDITION_ID_MISSING"
                    ),
                    "token_id": _text(item.get("token_id"), "V5_EXECUTION_TOKEN_ID_MISSING"),
                    "side": side,
                    "ask": ask,
                    "fee": fee,
                    "quote_observed_at": quote_at,
                    "minimum_order_size": leg_minimum,
                    "book_hash": _text(
                        item.get("book_hash"), "V5_EXECUTION_BOOK_HASH_MISSING"
                    ),
                }
            )

        recomputed_cost = sum(float(leg["ask"]) + float(leg["fee"]) for leg in legs)
        if not _same(entry_cost, recomputed_cost):
            raise WeatherPaperPositionError("V5_ENTRY_COST_LEG_SUM_MISMATCH")
        recomputed_minimum = max(float(leg["minimum_order_size"]) for leg in legs)
        if not _same(minimum, recomputed_minimum):
            raise WeatherPaperPositionError("V5_MINIMUM_ORDER_LEG_MISMATCH")

        return {
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
            "decision_id": decision_id,
            "decision_expires_at": expires,
            "post_receipt_recheck_started_at": started,
            "post_receipt_recheck_finished_at": finished,
            "entry_cost_per_unit": entry_cost,
            "visible_units": visible,
            "minimum_order_size": minimum,
            "theoretical_payout_per_unit": payout,
            "legs": legs,
        }

    @staticmethod
    def _execution_identity(execution: dict) -> str:
        normalized = IndependentReviewPostReceiptStore._normalized_execution(execution)
        return _canonical_sha(normalized)

    def save_signal(self, **kwargs):
        """Make source-shock dedupe stable per official exclusion episode, not forever."""
        lane = str(kwargs.get("lane") or "")
        payload = kwargs.get("payload")
        if lane == SOURCE_SHOCK_LANE:
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
            kwargs = dict(kwargs)
            kwargs["fingerprint"] = _canonical_sha(episode)
        return super().save_signal(**kwargs)

    def admit_post_receipt_position(
        self, signal_id: int, target_stake_usd: float, execution: dict
    ) -> dict:
        normalized = self._normalized_execution(execution)
        sid = int(signal_id)
        with self._conn() as db:
            signal = db.execute(
                "SELECT * FROM weather_paper_signals WHERE id=?", (sid,)
            ).fetchone()
            existing = db.execute(
                "SELECT id FROM weather_paper_positions WHERE signal_id=?", (sid,)
            ).fetchone()
        if signal is None:
            raise WeatherPaperPositionError("V5_SIGNAL_NOT_FOUND")

        signal_dict = dict(signal)
        payload = _payload(signal_dict.get("payload_json"))
        if payload.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V5:
            raise WeatherPaperPositionError("V5_SIGNAL_PROTOCOL_MISMATCH")
        if str(payload.get("decision_id") or "") != normalized["decision_id"]:
            raise WeatherPaperPositionError("V5_DECISION_IDENTITY_MISMATCH")
        try:
            payload_expiry = float(payload.get("decision_expires_at"))
        except (TypeError, ValueError, OverflowError):
            raise WeatherPaperPositionError("V5_SIGNAL_EXPIRY_INVALID") from None
        if not math.isfinite(payload_expiry) or not _same(
            payload_expiry, normalized["decision_expires_at"]
        ):
            raise WeatherPaperPositionError("V5_EXPIRY_IDENTITY_MISMATCH")

        signal_payout = signal_dict.get("theoretical_payout")
        if signal_payout is None or not _same(
            _finite(signal_payout, "V5_SIGNAL_PAYOUT_INVALID"),
            normalized["theoretical_payout_per_unit"],
        ):
            raise WeatherPaperPositionError("V5_PAYOUT_IDENTITY_MISMATCH")

        legs = normalized["legs"]
        if len(legs) == 1:
            leg = legs[0]
            if str(signal_dict.get("market_id") or "") != leg["market_id"]:
                raise WeatherPaperPositionError("V5_SIGNAL_MARKET_IDENTITY_MISMATCH")
            if str(signal_dict.get("token_id") or "") != leg["token_id"]:
                raise WeatherPaperPositionError("V5_SIGNAL_TOKEN_IDENTITY_MISMATCH")
            if str(signal_dict.get("side") or "").upper() != leg["side"]:
                raise WeatherPaperPositionError("V5_SIGNAL_SIDE_IDENTITY_MISMATCH")
        else:
            expected_markets = tuple(str(value) for value in (payload.get("market_ids") or ()))
            expected_tokens = tuple(str(value) for value in (payload.get("token_ids") or ()))
            actual_markets = tuple(str(leg["market_id"]) for leg in legs)
            actual_tokens = tuple(str(leg["token_id"]) for leg in legs)
            if not expected_markets or not expected_tokens:
                raise WeatherPaperPositionError("V5_STRUCTURAL_SIGNAL_LEGS_MISSING")
            if expected_markets != actual_markets or expected_tokens != actual_tokens:
                raise WeatherPaperPositionError("V5_STRUCTURAL_SIGNAL_LEGS_MISMATCH")

        if existing is None:
            if str(signal_dict.get("status") or "") != "POST_RECEIPT_RECHECK":
                raise WeatherPaperPositionError("V5_SIGNAL_PRESTATE_INVALID")
        else:
            stored_execution = payload.get("post_receipt_execution")
            if not isinstance(stored_execution, dict):
                raise WeatherPaperPositionError("V5_EXISTING_EXECUTION_EVIDENCE_MISSING")
            if self._execution_identity(stored_execution) != self._execution_identity(normalized):
                raise WeatherPaperPositionError("V5_EXISTING_EXECUTION_IDENTITY_CONFLICT")

        return super().admit_post_receipt_position(sid, target_stake_usd, normalized)


class IndependentReviewMakerAccountingStore(MakerPaperAccountingStoreV5):
    """Bind idempotent maker activation to the exact Telegram linkage evidence."""

    def activate_after_telegram(
        self,
        order,
        *,
        signal_id: int,
        telegram_message_id: int,
        telegram_sent_at: float,
        post_delivery_exact_finished_at: float,
        recorded_at: float | None = None,
    ):
        with self._db_lock:
            existing = self.db.execute(
                "SELECT 1 FROM weather_maker_shadow_orders WHERE order_id=?",
                (order.order_id,),
            ).fetchone()
            if existing is not None:
                rows = self.db.execute(
                    "SELECT payload_json FROM weather_maker_shadow_events "
                    "WHERE order_id=? AND event_type='TELEGRAM_SIGNAL_LINKED' ORDER BY seq",
                    (order.order_id,),
                ).fetchall()
                if len(rows) != 1:
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_LINK_IDENTITY_CONFLICT")
                try:
                    linked = json.loads(str(rows[0]["payload_json"]))
                except json.JSONDecodeError:
                    raise WeatherMakerStoreError(
                        "MAKER_ACTIVATION_LINK_IDENTITY_CONFLICT"
                    ) from None
                if not isinstance(linked, dict):
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_LINK_IDENTITY_CONFLICT")
                expected = {
                    "signal_id": int(signal_id),
                    "telegram_message_id": int(telegram_message_id),
                    "telegram_sent_at": float(telegram_sent_at),
                    "post_delivery_exact_finished_at": float(
                        post_delivery_exact_finished_at
                    ),
                }
                try:
                    actual = {
                        "signal_id": int(linked.get("signal_id")),
                        "telegram_message_id": int(linked.get("telegram_message_id")),
                        "telegram_sent_at": float(linked.get("telegram_sent_at")),
                        "post_delivery_exact_finished_at": float(
                            linked.get("post_delivery_exact_finished_at")
                        ),
                    }
                except (TypeError, ValueError, OverflowError):
                    raise WeatherMakerStoreError(
                        "MAKER_ACTIVATION_LINK_IDENTITY_CONFLICT"
                    ) from None
                if actual["signal_id"] != expected["signal_id"]:
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_LINK_IDENTITY_CONFLICT")
                if actual["telegram_message_id"] != expected["telegram_message_id"]:
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_LINK_IDENTITY_CONFLICT")
                if not _same(actual["telegram_sent_at"], expected["telegram_sent_at"], tolerance=1e-6):
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_LINK_IDENTITY_CONFLICT")
                if not _same(
                    actual["post_delivery_exact_finished_at"],
                    expected["post_delivery_exact_finished_at"],
                    tolerance=1e-6,
                ):
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_LINK_IDENTITY_CONFLICT")
                if linked.get("activation_atomic") is not True:
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_LINK_IDENTITY_CONFLICT")
            return super().activate_after_telegram(
                order,
                signal_id=signal_id,
                telegram_message_id=telegram_message_id,
                telegram_sent_at=telegram_sent_at,
                post_delivery_exact_finished_at=post_delivery_exact_finished_at,
                recorded_at=recorded_at,
            )


class IndependentReviewAllPaperCommandController(AllPaperCommandController):
    """Never show the all-lanes headline as green when maker evidence is degraded."""

    def _read_status(self) -> dict:
        status = dict(super()._read_status())
        core_healthy = status.get("cycle_ok") is True
        maker_healthy = (
            status.get("maker_healthy") is True
            and status.get("maker_stream_degraded") is not True
        )
        status["cycle_ok"] = bool(core_healthy and maker_healthy)
        status["operator_all_lanes_healthy"] = bool(core_healthy and maker_healthy)
        return status
