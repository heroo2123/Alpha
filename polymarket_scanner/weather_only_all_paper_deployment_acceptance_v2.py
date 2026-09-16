from __future__ import annotations

"""Final additive first-cycle acceptance for the complete V9 operator/deployment stack."""

import math
from dataclasses import asdict, dataclass

from .weather_only_all_paper_deployment_acceptance import (
    AllPaperDeploymentAcceptanceError,
    accept_first_all_paper_cycle,
)
from .weather_only_discovery import GLOBAL_CENSUS_TTL_SECONDS
from .weather_only_live_paper_all_signals_final_v5 import FINAL_ALL_PAPER_RUNTIME_V5_VERSION
from .weather_only_live_paper_all_signals_final_v6 import FINAL_ALL_PAPER_RUNTIME_V6_VERSION
from .weather_only_live_paper_all_signals_final_v7 import FINAL_ALL_PAPER_RUNTIME_V7_VERSION
from .weather_only_live_paper_all_signals_final_v8 import FINAL_ALL_PAPER_RUNTIME_V8_VERSION
from .weather_only_live_paper_all_signals_final_v9 import FINAL_ALL_PAPER_RUNTIME_V9_VERSION
from .weather_only_operator_state_corrective import OPERATOR_STATE_CORRECTIVE_VERSION
from .weather_only_operator_state_corrective_v2 import OPERATOR_STATE_CORRECTIVE_V2_VERSION
from .weather_only_operator_state_corrective_v3 import OPERATOR_STATE_CORRECTIVE_V3_VERSION
from .weather_only_operator_state_corrective_v4 import OPERATOR_STATE_CORRECTIVE_V4_VERSION

ALL_PAPER_DEPLOYMENT_ACCEPTANCE_V2_VERSION = (
    "weather_all_paper_first_cycle_acceptance_v16_v9_root_custody_operator_drain_recall_binding"
)
RECALL_EVIDENCE_FLOAT_TOLERANCE = 1e-6


@dataclass(frozen=True, slots=True)
class AllPaperFirstCycleAcceptanceV2:
    version: str
    release_sha: str
    cycle_finished_at: float
    cycle_age_seconds: float
    runtime_version: str
    execution_protocol: str
    maker_accounting_version: str
    operator_state_version: str
    operator_state_v2_version: str
    operator_state_v3_version: str
    operator_state_v4_version: str
    accepted: bool
    financial_authority: bool = False
    automatic_order_placement: bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _true(status: dict, key: str, code: str) -> None:
    if status.get(key) is not True:
        raise AllPaperDeploymentAcceptanceError(code)


def _false(status: dict, key: str, code: str) -> None:
    if status.get(key) is not False:
        raise AllPaperDeploymentAcceptanceError(code)


def _finite_number(value: object, code: str) -> float:
    if isinstance(value, bool):
        raise AllPaperDeploymentAcceptanceError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise AllPaperDeploymentAcceptanceError(code)
    if not math.isfinite(number):
        raise AllPaperDeploymentAcceptanceError(code)
    return number


def accept_first_all_paper_cycle_v2(
    status: object,
    *,
    expected_release_sha: str,
    not_before: float,
    now: float | None = None,
    max_age_seconds: float = 600.0,
) -> AllPaperFirstCycleAcceptanceV2:
    base = accept_first_all_paper_cycle(
        status,
        expected_release_sha=expected_release_sha,
        not_before=not_before,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    if not isinstance(status, dict):
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_STATUS_TYPE_INVALID")
    for key, expected, code in (
        ("final_all_paper_runtime_v5_version", FINAL_ALL_PAPER_RUNTIME_V5_VERSION, "ALL_PAPER_FINAL_V5_WRAPPER_VERSION_MISMATCH"),
        ("final_all_paper_runtime_v6_version", FINAL_ALL_PAPER_RUNTIME_V6_VERSION, "ALL_PAPER_FINAL_V6_WRAPPER_VERSION_MISMATCH"),
        ("final_all_paper_runtime_v7_version", FINAL_ALL_PAPER_RUNTIME_V7_VERSION, "ALL_PAPER_FINAL_V7_WRAPPER_VERSION_MISMATCH"),
        ("final_all_paper_runtime_v8_version", FINAL_ALL_PAPER_RUNTIME_V8_VERSION, "ALL_PAPER_FINAL_V8_WRAPPER_VERSION_MISMATCH"),
        ("final_all_paper_runtime_v9_version", FINAL_ALL_PAPER_RUNTIME_V9_VERSION, "ALL_PAPER_FINAL_V9_WRAPPER_VERSION_MISMATCH"),
        ("operator_state_corrective_version", OPERATOR_STATE_CORRECTIVE_VERSION, "ALL_PAPER_OPERATOR_STATE_VERSION_MISMATCH"),
        ("operator_state_corrective_v2_version", OPERATOR_STATE_CORRECTIVE_V2_VERSION, "ALL_PAPER_OPERATOR_STATE_V2_VERSION_MISMATCH"),
        ("operator_state_corrective_v3_version", OPERATOR_STATE_CORRECTIVE_V3_VERSION, "ALL_PAPER_OPERATOR_STATE_V3_VERSION_MISMATCH"),
        ("operator_state_corrective_v4_version", OPERATOR_STATE_CORRECTIVE_V4_VERSION, "ALL_PAPER_OPERATOR_STATE_V4_VERSION_MISMATCH"),
        ("operator_invalidation_transport", "IDEMPOTENT_EDIT_MESSAGE_TEXT", "ALL_PAPER_OPERATOR_INVALIDATION_TRANSPORT_MISMATCH"),
    ):
        if status.get(key) != expected:
            raise AllPaperDeploymentAcceptanceError(code)

    for key, code in (
        ("operator_visible_invalidation_required", "ALL_PAPER_OPERATOR_VISIBLE_INVALIDATION_NOT_REQUIRED"),
        ("operator_retry_release_requires_visible_invalidation", "ALL_PAPER_RETRY_RELEASE_NOT_VISIBILITY_GATED"),
        ("operator_retry_preserves_original_signal_fingerprint", "ALL_PAPER_RETRY_MUTATES_ORIGINAL_FINGERPRINT"),
        ("operator_message_sync_healthy", "ALL_PAPER_OPERATOR_MESSAGE_SYNC_UNHEALTHY"),
        ("source_shock_retry_guard_final_episode_identity", "ALL_PAPER_SOURCE_SHOCK_RETRY_IDENTITY_NOT_FINAL"),
        ("implicit_dotenv_forbidden", "ALL_PAPER_IMPLICIT_DOTENV_NOT_FORBIDDEN"),
        ("dotenv_loading_disabled", "ALL_PAPER_DOTENV_LOADING_NOT_DISABLED"),
        ("implicit_nontelegram_settings_defaulted", "ALL_PAPER_NONTELEGRAM_SETTINGS_NOT_DEFAULTED"),
        ("terminal_invalidation_identity_strict", "ALL_PAPER_TERMINAL_IDENTITY_NOT_STRICT"),
        ("terminal_invalidation_requires_post_receipt_prestate", "ALL_PAPER_TERMINAL_PRESTATE_NOT_STRICT"),
        ("operator_restart_visibility_required", "ALL_PAPER_OPERATOR_RESTART_VISIBILITY_NOT_REQUIRED"),
        ("operator_sync_before_startup_required", "ALL_PAPER_OPERATOR_STARTUP_SYNC_NOT_REQUIRED"),
        ("operator_recent_terminal_reason_visible", "ALL_PAPER_RECENT_TERMINAL_REASON_NOT_VISIBLE"),
        ("maker_proposal_queue_uncertified_label", "ALL_PAPER_MAKER_QUEUE_LABEL_NOT_PROVEN"),
        ("historical_terminal_operator_sync_backfill_required", "ALL_PAPER_HISTORICAL_TERMINAL_BACKFILL_NOT_REQUIRED"),
        ("historical_terminal_operator_sync_complete", "ALL_PAPER_HISTORICAL_TERMINAL_BACKFILL_INCOMPLETE"),
        ("network_environment_isolated", "ALL_PAPER_NETWORK_ENVIRONMENT_NOT_ISOLATED"),
        ("network_environment_absent_before_http_client_construction", "ALL_PAPER_NETWORK_ENVIRONMENT_CONSTRUCTION_BOUNDARY_NOT_PROVEN"),
        ("global_weather_recall_required", "ALL_PAPER_GLOBAL_WEATHER_RECALL_NOT_REQUIRED"),
        ("global_weather_recall_complete", "ALL_PAPER_GLOBAL_WEATHER_RECALL_INCOMPLETE"),
        ("global_weather_recall_fresh", "ALL_PAPER_GLOBAL_WEATHER_RECALL_STALE"),
        ("operator_deleted_message_terminal_confirmation", "ALL_PAPER_DELETED_MESSAGE_TERMINAL_CONFIRMATION_MISSING"),
    ):
        _true(status, key, code)

    _false(
        status,
        "operator_sync_restart_pagination_required",
        "ALL_PAPER_OPERATOR_SYNC_STILL_RESTART_PAGINATED",
    )
    if int(status.get("historical_terminal_operator_sync_missing") or 0) != 0:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_HISTORICAL_TERMINAL_SYNC_MISSING")

    recall = status.get("global_weather_recall")
    if not isinstance(recall, dict) or recall.get("complete") is not True:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_GLOBAL_WEATHER_RECALL_EVIDENCE_INVALID")
    if int(recall.get("pages") or 0) <= 0 or int(recall.get("scanned_events") or 0) <= 0:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_GLOBAL_WEATHER_RECALL_EVIDENCE_INVALID")
    census_completed_at = _finite_number(
        recall.get("census_completed_at"),
        "ALL_PAPER_GLOBAL_WEATHER_RECALL_COMPLETION_INVALID",
    )
    census_age = _finite_number(
        recall.get("age_seconds"),
        "ALL_PAPER_GLOBAL_WEATHER_RECALL_AGE_INVALID",
    )
    max_reuse = _finite_number(
        recall.get("max_reuse_seconds"),
        "ALL_PAPER_GLOBAL_WEATHER_RECALL_REUSE_INVALID",
    )
    if census_completed_at <= 0.0 or census_age < 0.0:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_GLOBAL_WEATHER_RECALL_FRESHNESS_INVALID")
    if census_age > GLOBAL_CENSUS_TTL_SECONDS:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_GLOBAL_WEATHER_RECALL_STALE")
    if not (0.0 < max_reuse <= GLOBAL_CENSUS_TTL_SECONDS):
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_GLOBAL_WEATHER_RECALL_REUSE_TOO_LONG")

    status_completed_at = _finite_number(
        status.get("global_weather_recall_certified_at"),
        "ALL_PAPER_GLOBAL_WEATHER_RECALL_STATUS_COMPLETION_INVALID",
    )
    status_age = _finite_number(
        status.get("global_weather_recall_age_seconds"),
        "ALL_PAPER_GLOBAL_WEATHER_RECALL_STATUS_AGE_INVALID",
    )
    status_max_reuse = _finite_number(
        status.get("global_weather_recall_max_reuse_seconds"),
        "ALL_PAPER_GLOBAL_WEATHER_RECALL_STATUS_REUSE_INVALID",
    )
    if status_completed_at <= 0.0 or status_age < 0.0:
        raise AllPaperDeploymentAcceptanceError(
            "ALL_PAPER_GLOBAL_WEATHER_RECALL_STATUS_FRESHNESS_INVALID"
        )
    if status_age > GLOBAL_CENSUS_TTL_SECONDS:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_GLOBAL_WEATHER_RECALL_STATUS_STALE")
    if not (0.0 < status_max_reuse <= GLOBAL_CENSUS_TTL_SECONDS):
        raise AllPaperDeploymentAcceptanceError(
            "ALL_PAPER_GLOBAL_WEATHER_RECALL_STATUS_REUSE_INVALID"
        )
    if abs(status_completed_at - census_completed_at) > RECALL_EVIDENCE_FLOAT_TOLERANCE:
        raise AllPaperDeploymentAcceptanceError(
            "ALL_PAPER_GLOBAL_WEATHER_RECALL_COMPLETION_EVIDENCE_MISMATCH"
        )
    if abs(status_age - census_age) > RECALL_EVIDENCE_FLOAT_TOLERANCE:
        raise AllPaperDeploymentAcceptanceError(
            "ALL_PAPER_GLOBAL_WEATHER_RECALL_AGE_EVIDENCE_MISMATCH"
        )
    if abs(status_max_reuse - max_reuse) > RECALL_EVIDENCE_FLOAT_TOLERANCE:
        raise AllPaperDeploymentAcceptanceError(
            "ALL_PAPER_GLOBAL_WEATHER_RECALL_REUSE_EVIDENCE_MISMATCH"
        )

    for key, code in (
        ("maker_queue_certified", "ALL_PAPER_MAKER_QUEUE_UNEXPECTEDLY_CERTIFIED"),
        ("maker_queue_position_certified", "ALL_PAPER_MAKER_QUEUE_POSITION_UNEXPECTEDLY_CERTIFIED"),
        ("maker_simulated_fill_accounting_enabled", "ALL_PAPER_MAKER_SIMULATED_FILL_ACCOUNTING_NOT_FALSE"),
        ("financial_delivery", "ALL_PAPER_FINANCIAL_DELIVERY_NOT_FALSE"),
        ("financial_authority", "ALL_PAPER_FINANCIAL_AUTHORITY_NOT_FALSE"),
        ("automatic_order_placement", "ALL_PAPER_ORDER_PLACEMENT_NOT_FALSE"),
        ("wallet_or_order_api_loaded", "ALL_PAPER_WALLET_ORDER_API_NOT_FALSE"),
    ):
        _false(status, key, code)

    sync = status.get("operator_message_sync")
    if not isinstance(sync, dict) or sync.get("healthy") is not True:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_SYNC_SUMMARY_UNHEALTHY")
    if int(sync.get("unconfirmed") or 0) != 0:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_SYNC_UNCONFIRMED")
    if int(sync.get("failed") or 0) != 0:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_SYNC_FAILED")
    if sync.get("restart_pagination_required") is not False:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_SYNC_RESTART_PAGINATION_PRESENT")
    if list(status.get("operator_message_sync_errors") or []):
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_SYNC_ERRORS_PRESENT")
    if int(status.get("operator_retry_max_per_evidence") or 0) != 3:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_RETRY_BOUND_MISMATCH")
    if float(status.get("operator_retry_cooldown_seconds") or 0.0) < 60.0:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_RETRY_COOLDOWN_TOO_SHORT")
    if status.get("isolated_settings_overrides") != ["telegram_bot_token", "telegram_chat_id"]:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_ISOLATED_SETTINGS_OVERRIDE_SET_MISMATCH")

    return AllPaperFirstCycleAcceptanceV2(
        version=ALL_PAPER_DEPLOYMENT_ACCEPTANCE_V2_VERSION,
        release_sha=base.release_sha,
        cycle_finished_at=base.cycle_finished_at,
        cycle_age_seconds=base.cycle_age_seconds,
        runtime_version=FINAL_ALL_PAPER_RUNTIME_V9_VERSION,
        execution_protocol=base.execution_protocol,
        maker_accounting_version=base.maker_accounting_version,
        operator_state_version=OPERATOR_STATE_CORRECTIVE_VERSION,
        operator_state_v2_version=OPERATOR_STATE_CORRECTIVE_V2_VERSION,
        operator_state_v3_version=OPERATOR_STATE_CORRECTIVE_V3_VERSION,
        operator_state_v4_version=OPERATOR_STATE_CORRECTIVE_V4_VERSION,
        accepted=True,
    )
