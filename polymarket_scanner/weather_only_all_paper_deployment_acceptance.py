from __future__ import annotations

"""Fail-closed first-cycle acceptance for the final all-weather PAPER runtime."""

import math
import time
from dataclasses import asdict, dataclass

from .weather_only_live_paper import MODE
from .weather_only_live_paper_all_signals_final import FINAL_ALL_PAPER_RUNTIME_VERSION
from .weather_only_live_paper_all_signals_v7 import ALL_PAPER_V7_RUNTIME_VERSION
from .weather_only_live_paper_all_signals_v8 import ALL_PAPER_V8_RUNTIME_VERSION
from .weather_only_live_paper_final import FINAL_MARKET_STATE_POLICY, FINAL_PAPER_RUNTIME_VERSION
from .weather_only_maker_paper_accounting_v5 import MAKER_PAPER_ACCOUNTING_V5_VERSION
from .weather_only_maker_trade_stream_v3 import MAKER_TRADE_STREAM_V3_VERSION
from .weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5, PAPER_POSITION_VERSION_V5


ALL_PAPER_DEPLOYMENT_ACCEPTANCE_VERSION = (
    "weather_all_paper_first_cycle_acceptance_v4_complete_maker_safety_profile"
)
RESULT_LAG_BLOCK_REASON = "EXACT_WRH_CUTOFF_STATE_NOT_PROVEN"
MAKER_NOTIFICATION_RETRY_POLICY = "AT_MOST_ONCE_AFTER_DURABLE_CLAIM"


class AllPaperDeploymentAcceptanceError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AllPaperDeploymentAcceptanceError(code)
    number = float(value)
    if not math.isfinite(number):
        raise AllPaperDeploymentAcceptanceError(code)
    return number


def _sha(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text):
        raise AllPaperDeploymentAcceptanceError(code)
    return text


def _require_false(payload: dict, key: str, code: str) -> None:
    if payload.get(key) is not False:
        raise AllPaperDeploymentAcceptanceError(code)


def _require_true(payload: dict, key: str, code: str) -> None:
    if payload.get(key) is not True:
        raise AllPaperDeploymentAcceptanceError(code)


@dataclass(frozen=True, slots=True)
class AllPaperFirstCycleAcceptance:
    version: str
    release_sha: str
    cycle_finished_at: float
    cycle_age_seconds: float
    runtime_version: str
    execution_protocol: str
    maker_accounting_version: str
    accepted: bool
    financial_authority: bool = False
    automatic_order_placement: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def accept_first_all_paper_cycle(
    status: object,
    *,
    expected_release_sha: str,
    not_before: float,
    now: float | None = None,
    max_age_seconds: float = 600.0,
) -> AllPaperFirstCycleAcceptance:
    if not isinstance(status, dict):
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_STATUS_TYPE_INVALID")
    release = _sha(expected_release_sha, "ALL_PAPER_EXPECTED_RELEASE_INVALID")
    if _sha(status.get("release_sha"), "ALL_PAPER_STATUS_RELEASE_INVALID") != release:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_STATUS_RELEASE_MISMATCH")
    if status.get("mode") != MODE:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_STATUS_MODE_MISMATCH")
    if status.get("final_paper_runtime_version") != FINAL_PAPER_RUNTIME_VERSION:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_INHERITED_FINAL_RUNTIME_MISSING")
    if status.get("current_market_state_policy") != FINAL_MARKET_STATE_POLICY:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_MARKET_STATE_POLICY_MISMATCH")
    _require_true(status, "exclusive_writer_lease", "ALL_PAPER_EXCLUSIVE_WRITER_LEASE_NOT_PROVEN")

    if status.get("final_all_paper_runtime_version") != FINAL_ALL_PAPER_RUNTIME_VERSION:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_FINAL_WRAPPER_VERSION_MISMATCH")
    if status.get("all_paper_v8_runtime_version") != ALL_PAPER_V8_RUNTIME_VERSION:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_V8_RUNTIME_VERSION_MISMATCH")
    if status.get("all_paper_v7_runtime_version") != ALL_PAPER_V7_RUNTIME_VERSION:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_V7_RUNTIME_VERSION_MISMATCH")
    if status.get("paper_execution_protocol_version") != PAPER_EXECUTION_PROTOCOL_V5:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_EXECUTION_PROTOCOL_MISMATCH")
    if status.get("paper_position_version") != PAPER_POSITION_VERSION_V5:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_POSITION_VERSION_MISMATCH")
    if status.get("maker_paper_accounting_version") != MAKER_PAPER_ACCOUNTING_V5_VERSION:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_MAKER_ACCOUNTING_VERSION_MISMATCH")
    if status.get("maker_trade_stream_version") != MAKER_TRADE_STREAM_V3_VERSION:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_MAKER_STREAM_VERSION_MISMATCH")
    if status.get("maker_settlement_notification_retry_policy") != MAKER_NOTIFICATION_RETRY_POLICY:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_MAKER_NOTIFICATION_POLICY_MISMATCH")

    if status.get("cycle_ok") is not True:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_FIRST_CYCLE_UNHEALTHY")
    if list(status.get("errors") or []):
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_FIRST_CYCLE_ERRORS_PRESENT")
    if list(status.get("maker_errors") or []):
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_MAKER_ERRORS_PRESENT")
    if list(status.get("source_shock_errors") or []):
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_SOURCE_SHOCK_ERRORS_PRESENT")

    finished = _finite(status.get("finished_at"), "ALL_PAPER_FINISHED_AT_INVALID")
    boundary = _finite(not_before, "ALL_PAPER_NOT_BEFORE_INVALID")
    current = time.time() if now is None else _finite(now, "ALL_PAPER_NOW_INVALID")
    max_age = _finite(max_age_seconds, "ALL_PAPER_MAX_AGE_INVALID")
    if max_age <= 0.0:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_MAX_AGE_INVALID")
    if finished < boundary:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_STATUS_PREDATES_START")
    if finished > current + 5.0:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_STATUS_FROM_FUTURE")
    age = max(0.0, current - finished)
    if age > max_age:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_STATUS_STALE")

    for key, code in (
        ("paper_telegram_delivery", "ALL_PAPER_FUTURE_DAY_DELIVERY_NOT_ENABLED"),
        ("same_day_paper_delivery_enabled", "ALL_PAPER_SAME_DAY_DELIVERY_NOT_ENABLED"),
        ("structural_paper_delivery_enabled", "ALL_PAPER_STRUCTURAL_DELIVERY_NOT_ENABLED"),
        ("maker_paper_delivery_enabled", "ALL_PAPER_MAKER_DELIVERY_NOT_ENABLED"),
        ("source_shock_paper_delivery_enabled", "ALL_PAPER_SOURCE_SHOCK_DELIVERY_NOT_ENABLED"),
        ("post_receipt_execution_required", "ALL_PAPER_POST_RECEIPT_EXECUTION_NOT_REQUIRED"),
        ("post_receipt_exact_clob_required", "ALL_PAPER_POST_RECEIPT_CLOB_NOT_REQUIRED"),
        ("maker_public_ws_prospective_fill_required", "ALL_PAPER_MAKER_PROSPECTIVE_WS_NOT_REQUIRED"),
        ("maker_healthy", "ALL_PAPER_MAKER_NOT_HEALTHY"),
        ("maker_post_delivery_expiry_rechecked", "ALL_PAPER_MAKER_EXPIRY_RECHECK_NOT_PROVEN"),
        ("maker_subscription_lifecycle_bounded", "ALL_PAPER_MAKER_SUBSCRIPTIONS_NOT_BOUNDED"),
        ("maker_activation_failure_cleanup_complete", "ALL_PAPER_MAKER_ACTIVATION_CLEANUP_NOT_PROVEN"),
        ("maker_settlement_duplicate_after_restart_guard", "ALL_PAPER_MAKER_RESULT_DUPLICATE_GUARD_NOT_PROVEN"),
        ("maker_activation_accounting_atomic", "ALL_PAPER_MAKER_ACTIVATION_NOT_ATOMIC"),
        ("legacy_partial_hourly_summary_suppressed", "ALL_PAPER_LEGACY_SUMMARY_NOT_SUPPRESSED"),
        ("v5_terminal_not_actionable_audit_atomic", "ALL_PAPER_V5_TERMINAL_AUDIT_NOT_ATOMIC"),
    ):
        _require_true(status, key, code)

    for key, code in (
        ("same_day_paper_calibrated_probability", "ALL_PAPER_SAME_DAY_CALIBRATION_NOT_FALSE"),
        ("source_shock_calibrated_probability", "ALL_PAPER_SOURCE_SHOCK_CALIBRATION_NOT_FALSE"),
        ("maker_value_calibrated_probability", "ALL_PAPER_MAKER_CALIBRATION_NOT_FALSE"),
        ("maker_book_touch_counts_as_fill", "ALL_PAPER_MAKER_BOOK_TOUCH_FILL_NOT_FALSE"),
        ("maker_stream_degraded", "ALL_PAPER_MAKER_STREAM_DEGRADED"),
        ("result_lag_paper_delivery_enabled", "ALL_PAPER_RESULT_LAG_NOT_GATED"),
        ("financial_delivery", "ALL_PAPER_FINANCIAL_DELIVERY_NOT_FALSE"),
        ("financial_authority", "ALL_PAPER_FINANCIAL_AUTHORITY_NOT_FALSE"),
        ("automatic_order_placement", "ALL_PAPER_ORDER_PLACEMENT_NOT_FALSE"),
        ("wallet_or_order_api_loaded", "ALL_PAPER_WALLET_ORDER_API_NOT_FALSE"),
        ("pws_enabled", "ALL_PAPER_PWS_NOT_FALSE"),
    ):
        _require_false(status, key, code)
    _require_true(
        status,
        "source_shock_revision_sensitive",
        "ALL_PAPER_SOURCE_SHOCK_REVISION_LABEL_MISSING",
    )
    if status.get("result_lag_block_reason") != RESULT_LAG_BLOCK_REASON:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_RESULT_LAG_BLOCK_REASON_MISMATCH")

    same_day = status.get("same_day_three_layer")
    if not isinstance(same_day, dict):
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_THREE_LAYER_STATUS_MISSING")
    _require_true(same_day, "enabled", "ALL_PAPER_THREE_LAYER_NOT_ENABLED")
    if list(same_day.get("errors") or []):
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_THREE_LAYER_ERRORS_PRESENT")
    _require_false(
        same_day,
        "population_alignment_certified",
        "ALL_PAPER_THREE_LAYER_ALIGNMENT_UNEXPECTEDLY_CERTIFIED",
    )
    _require_false(
        same_day,
        "financial_authority",
        "ALL_PAPER_THREE_LAYER_FINANCIAL_AUTHORITY_NOT_FALSE",
    )

    return AllPaperFirstCycleAcceptance(
        version=ALL_PAPER_DEPLOYMENT_ACCEPTANCE_VERSION,
        release_sha=release,
        cycle_finished_at=finished,
        cycle_age_seconds=age,
        runtime_version=FINAL_ALL_PAPER_RUNTIME_VERSION,
        execution_protocol=PAPER_EXECUTION_PROTOCOL_V5,
        maker_accounting_version=MAKER_PAPER_ACCOUNTING_V5_VERSION,
        accepted=True,
    )
