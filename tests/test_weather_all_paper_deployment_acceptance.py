from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_all_paper_deployment_acceptance import (
    AllPaperDeploymentAcceptanceError,
    MAKER_NOTIFICATION_RETRY_POLICY,
    STRUCTURAL_EXECUTION_MODEL,
    accept_first_all_paper_cycle,
)
from polymarket_scanner.weather_only_independent_review_corrective import (
    INDEPENDENT_REVIEW_CORRECTIVE_VERSION,
)
from polymarket_scanner.weather_only_independent_review_corrective_v2 import (
    INDEPENDENT_REVIEW_CORRECTIVE_V2_VERSION,
)
from polymarket_scanner.weather_only_live_paper import MODE
from polymarket_scanner.weather_only_live_paper_all_signals_final import FINAL_ALL_PAPER_RUNTIME_VERSION
from polymarket_scanner.weather_only_live_paper_all_signals_final_v2 import FINAL_ALL_PAPER_RUNTIME_V2_VERSION
from polymarket_scanner.weather_only_live_paper_all_signals_v7 import ALL_PAPER_V7_RUNTIME_VERSION
from polymarket_scanner.weather_only_live_paper_all_signals_v8 import ALL_PAPER_V8_RUNTIME_VERSION
from polymarket_scanner.weather_only_live_paper_final import FINAL_MARKET_STATE_POLICY, FINAL_PAPER_RUNTIME_VERSION
from polymarket_scanner.weather_only_maker_paper_accounting_v5 import MAKER_PAPER_ACCOUNTING_V5_VERSION
from polymarket_scanner.weather_only_maker_trade_stream_v3 import MAKER_TRADE_STREAM_V3_VERSION
from polymarket_scanner.weather_only_paper_post_receipt import PAPER_EXECUTION_PROTOCOL_V5, PAPER_POSITION_VERSION_V5

SHA = "a" * 40
NOW = 1_800_000_000.0


def _status() -> dict:
    return {
        "release_sha": SHA,
        "mode": MODE,
        "final_paper_runtime_version": FINAL_PAPER_RUNTIME_VERSION,
        "current_market_state_policy": FINAL_MARKET_STATE_POLICY,
        "exclusive_writer_lease": True,
        "final_all_paper_runtime_version": FINAL_ALL_PAPER_RUNTIME_VERSION,
        "final_all_paper_runtime_v2_version": FINAL_ALL_PAPER_RUNTIME_V2_VERSION,
        "independent_review_corrective_version": INDEPENDENT_REVIEW_CORRECTIVE_VERSION,
        "independent_review_corrective_v2_version": INDEPENDENT_REVIEW_CORRECTIVE_V2_VERSION,
        "all_paper_v8_runtime_version": ALL_PAPER_V8_RUNTIME_VERSION,
        "all_paper_v7_runtime_version": ALL_PAPER_V7_RUNTIME_VERSION,
        "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V5,
        "paper_position_version": PAPER_POSITION_VERSION_V5,
        "maker_paper_accounting_version": MAKER_PAPER_ACCOUNTING_V5_VERSION,
        "maker_trade_stream_version": MAKER_TRADE_STREAM_V3_VERSION,
        "maker_settlement_notification_retry_policy": MAKER_NOTIFICATION_RETRY_POLICY,
        "cycle_ok": True,
        "errors": [],
        "maker_errors": [],
        "source_shock_errors": [],
        "finished_at": NOW - 1.0,
        "paper_telegram_delivery": True,
        "same_day_paper_delivery_enabled": True,
        "structural_paper_delivery_enabled": True,
        "maker_paper_delivery_enabled": True,
        "maker_proposal_delivery_enabled": True,
        "source_shock_paper_delivery_enabled": True,
        "post_receipt_execution_required": True,
        "post_receipt_exact_clob_required": True,
        "maker_public_ws_prospective_fill_required": True,
        "maker_healthy": True,
        "maker_stream_degraded": False,
        "maker_post_delivery_expiry_rechecked": True,
        "maker_subscription_lifecycle_bounded": True,
        "maker_activation_failure_cleanup_complete": True,
        "maker_settlement_duplicate_after_restart_guard": True,
        "maker_activation_accounting_atomic": True,
        "legacy_partial_hourly_summary_suppressed": True,
        "v5_terminal_not_actionable_audit_atomic": True,
        "inherited_safety_boundary_verified": True,
        "v5_independent_execution_integrity_verified": True,
        "maker_activation_link_identity_strict": True,
        "source_shock_episode_dedupe_revision_aware": True,
        "operator_all_lanes_healthy": True,
        "same_day_dynamic_fee_semantics_fail_closed": True,
        "post_receipt_wrh_thesis_revalidation_required": True,
        "v5_per_leg_visible_capacity_required": True,
        "v5_capacity_recomputed_from_legs": True,
        "v5_admission_cross_process_serialized": True,
        "maker_settlement_strict_uma_finality": True,
        "future_day_forecast_run_age_claim_suppressed": True,
        "same_day_paper_calibrated_probability": False,
        "source_shock_calibrated_probability": False,
        "maker_value_calibrated_probability": False,
        "maker_book_touch_counts_as_fill": False,
        "source_shock_revision_sensitive": True,
        "result_lag_paper_delivery_enabled": False,
        "result_lag_block_reason": "EXACT_WRH_CUTOFF_STATE_NOT_PROVEN",
        "structural_execution_model": STRUCTURAL_EXECUTION_MODEL,
        "structural_validated_pnl_enabled": False,
        "maker_queue_position_certified": False,
        "maker_simulated_fill_accounting_enabled": False,
        "future_day_forecast_run_age_known": False,
        "financial_delivery": False,
        "financial_authority": False,
        "automatic_order_placement": False,
        "wallet_or_order_api_loaded": False,
        "pws_enabled": False,
        "same_day_three_layer": {
            "enabled": True,
            "errors": [],
            "population_alignment_certified": False,
            "financial_authority": False,
        },
    }


def test_final_all_paper_first_cycle_accepts_only_complete_safe_profile():
    accepted = accept_first_all_paper_cycle(
        _status(), expected_release_sha=SHA, not_before=NOW - 10.0, now=NOW
    )
    assert accepted.accepted is True
    assert accepted.release_sha == SHA
    assert accepted.runtime_version == FINAL_ALL_PAPER_RUNTIME_V2_VERSION


@pytest.mark.parametrize(
    "key,value,code",
    [
        ("final_all_paper_runtime_version", None, "ALL_PAPER_FINAL_WRAPPER_VERSION_MISMATCH"),
        ("final_all_paper_runtime_v2_version", None, "ALL_PAPER_FINAL_V2_WRAPPER_VERSION_MISMATCH"),
        ("independent_review_corrective_version", None, "ALL_PAPER_INDEPENDENT_REVIEW_CORRECTIVE_MISSING"),
        ("independent_review_corrective_v2_version", None, "ALL_PAPER_INDEPENDENT_REVIEW_CORRECTIVE_V2_MISSING"),
        ("post_receipt_execution_required", False, "ALL_PAPER_POST_RECEIPT_EXECUTION_NOT_REQUIRED"),
        ("v5_terminal_not_actionable_audit_atomic", False, "ALL_PAPER_V5_TERMINAL_AUDIT_NOT_ATOMIC"),
        ("inherited_safety_boundary_verified", False, "ALL_PAPER_INHERITED_SAFETY_NOT_VERIFIED"),
        ("v5_independent_execution_integrity_verified", False, "ALL_PAPER_V5_INTEGRITY_NOT_VERIFIED"),
        ("maker_activation_accounting_atomic", False, "ALL_PAPER_MAKER_ACTIVATION_NOT_ATOMIC"),
        ("maker_activation_link_identity_strict", False, "ALL_PAPER_MAKER_LINK_IDENTITY_NOT_STRICT"),
        ("source_shock_episode_dedupe_revision_aware", False, "ALL_PAPER_SOURCE_SHOCK_DEDUPE_NOT_REVISION_AWARE"),
        ("operator_all_lanes_healthy", False, "ALL_PAPER_OPERATOR_ALL_LANES_NOT_HEALTHY"),
        ("maker_healthy", False, "ALL_PAPER_MAKER_NOT_HEALTHY"),
        ("maker_stream_degraded", True, "ALL_PAPER_MAKER_STREAM_DEGRADED"),
        ("maker_subscription_lifecycle_bounded", False, "ALL_PAPER_MAKER_SUBSCRIPTIONS_NOT_BOUNDED"),
        ("maker_settlement_duplicate_after_restart_guard", False, "ALL_PAPER_MAKER_RESULT_DUPLICATE_GUARD_NOT_PROVEN"),
        ("structural_paper_delivery_enabled", False, "ALL_PAPER_STRUCTURAL_DELIVERY_NOT_ENABLED"),
        ("maker_book_touch_counts_as_fill", True, "ALL_PAPER_MAKER_BOOK_TOUCH_FILL_NOT_FALSE"),
        ("result_lag_paper_delivery_enabled", True, "ALL_PAPER_RESULT_LAG_NOT_GATED"),
        ("same_day_dynamic_fee_semantics_fail_closed", False, "ALL_PAPER_SAME_DAY_FEE_SEMANTICS_NOT_FAIL_CLOSED"),
        ("post_receipt_wrh_thesis_revalidation_required", False, "ALL_PAPER_POST_RECEIPT_WRH_REVALIDATION_NOT_REQUIRED"),
        ("v5_per_leg_visible_capacity_required", False, "ALL_PAPER_V5_PER_LEG_CAPACITY_NOT_REQUIRED"),
        ("v5_capacity_recomputed_from_legs", False, "ALL_PAPER_V5_CAPACITY_NOT_RECOMPUTED"),
        ("v5_admission_cross_process_serialized", False, "ALL_PAPER_V5_ADMISSION_NOT_SERIALIZED"),
        ("maker_settlement_strict_uma_finality", False, "ALL_PAPER_MAKER_SETTLEMENT_FINALITY_NOT_STRICT"),
        ("future_day_forecast_run_age_claim_suppressed", False, "ALL_PAPER_FORECAST_RUN_AGE_CLAIM_NOT_SUPPRESSED"),
        ("structural_validated_pnl_enabled", True, "ALL_PAPER_STRUCTURAL_VALIDATED_PNL_NOT_FALSE"),
        ("maker_queue_position_certified", True, "ALL_PAPER_MAKER_QUEUE_UNEXPECTEDLY_CERTIFIED"),
        ("maker_simulated_fill_accounting_enabled", True, "ALL_PAPER_MAKER_SIMULATED_FILL_ACCOUNTING_NOT_FALSE"),
        ("future_day_forecast_run_age_known", True, "ALL_PAPER_FORECAST_RUN_AGE_UNEXPECTEDLY_KNOWN"),
        ("financial_authority", True, "ALL_PAPER_FINANCIAL_AUTHORITY_NOT_FALSE"),
        ("wallet_or_order_api_loaded", True, "ALL_PAPER_WALLET_ORDER_API_NOT_FALSE"),
        ("pws_enabled", None, "ALL_PAPER_PWS_NOT_FALSE"),
    ],
)
def test_all_paper_first_cycle_fails_closed_on_safety_or_strategy_drift(key, value, code):
    status = _status()
    status[key] = value
    with pytest.raises(AllPaperDeploymentAcceptanceError) as exc:
        accept_first_all_paper_cycle(
            status, expected_release_sha=SHA, not_before=NOW - 10.0, now=NOW
        )
    assert exc.value.code == code


def test_all_paper_first_cycle_rejects_structural_execution_model_drift():
    status = _status()
    status["structural_execution_model"] = "ASSUME_ATOMIC"
    with pytest.raises(AllPaperDeploymentAcceptanceError) as exc:
        accept_first_all_paper_cycle(
            status, expected_release_sha=SHA, not_before=NOW - 10.0, now=NOW
        )
    assert exc.value.code == "ALL_PAPER_STRUCTURAL_EXECUTION_MODEL_MISMATCH"


def test_all_paper_first_cycle_rejects_wrong_maker_stream_or_notification_policy():
    status = _status()
    status["maker_trade_stream_version"] = "wrong-stream"
    with pytest.raises(AllPaperDeploymentAcceptanceError) as exc:
        accept_first_all_paper_cycle(
            status, expected_release_sha=SHA, not_before=NOW - 10.0, now=NOW
        )
    assert exc.value.code == "ALL_PAPER_MAKER_STREAM_VERSION_MISMATCH"

    status = _status()
    status["maker_settlement_notification_retry_policy"] = "retry-everything"
    with pytest.raises(AllPaperDeploymentAcceptanceError) as exc:
        accept_first_all_paper_cycle(
            status, expected_release_sha=SHA, not_before=NOW - 10.0, now=NOW
        )
    assert exc.value.code == "ALL_PAPER_MAKER_NOTIFICATION_POLICY_MISMATCH"


def test_all_paper_first_cycle_rejects_stale_or_wrong_release():
    with pytest.raises(AllPaperDeploymentAcceptanceError) as exc:
        accept_first_all_paper_cycle(
            _status(), expected_release_sha="b" * 40, not_before=NOW - 10.0, now=NOW
        )
    assert exc.value.code == "ALL_PAPER_STATUS_RELEASE_MISMATCH"

    status = _status()
    status["finished_at"] = NOW - 1000.0
    with pytest.raises(AllPaperDeploymentAcceptanceError) as exc:
        accept_first_all_paper_cycle(
            status, expected_release_sha=SHA, not_before=NOW - 2000.0, now=NOW,
            max_age_seconds=600.0,
        )
    assert exc.value.code == "ALL_PAPER_STATUS_STALE"
