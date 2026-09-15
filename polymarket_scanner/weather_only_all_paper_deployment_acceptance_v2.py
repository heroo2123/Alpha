from __future__ import annotations

"""Final additive first-cycle acceptance for the operator-state corrective runtime."""

from dataclasses import asdict, dataclass

from .weather_only_all_paper_deployment_acceptance import (
    AllPaperDeploymentAcceptanceError,
    accept_first_all_paper_cycle,
)
from .weather_only_live_paper_all_signals_final_v5 import FINAL_ALL_PAPER_RUNTIME_V5_VERSION
from .weather_only_live_paper_all_signals_final_v6 import FINAL_ALL_PAPER_RUNTIME_V6_VERSION
from .weather_only_live_paper_all_signals_final_v7 import FINAL_ALL_PAPER_RUNTIME_V7_VERSION
from .weather_only_operator_state_corrective import OPERATOR_STATE_CORRECTIVE_VERSION
from .weather_only_operator_state_corrective_v2 import OPERATOR_STATE_CORRECTIVE_V2_VERSION
from .weather_only_operator_state_corrective_v3 import OPERATOR_STATE_CORRECTIVE_V3_VERSION


ALL_PAPER_DEPLOYMENT_ACCEPTANCE_V2_VERSION = (
    "weather_all_paper_first_cycle_acceptance_v11_restart_visibility_startup_sync"
)


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
        ("operator_state_corrective_version", OPERATOR_STATE_CORRECTIVE_VERSION, "ALL_PAPER_OPERATOR_STATE_VERSION_MISMATCH"),
        ("operator_state_corrective_v2_version", OPERATOR_STATE_CORRECTIVE_V2_VERSION, "ALL_PAPER_OPERATOR_STATE_V2_VERSION_MISMATCH"),
        ("operator_state_corrective_v3_version", OPERATOR_STATE_CORRECTIVE_V3_VERSION, "ALL_PAPER_OPERATOR_STATE_V3_VERSION_MISMATCH"),
        ("operator_invalidation_transport", "IDEMPOTENT_EDIT_MESSAGE_TEXT", "ALL_PAPER_OPERATOR_INVALIDATION_TRANSPORT_MISMATCH"),
    ):
        if status.get(key) != expected:
            raise AllPaperDeploymentAcceptanceError(code)

    for key, code in (
        ("operator_visible_invalidation_required", "ALL_PAPER_OPERATOR_VISIBLE_INVALIDATION_NOT_REQUIRED"),
        ("operator_retry_release_requires_visible_invalidation", "ALL_PAPER_RETRY_RELEASE_NOT_VISIBILITY_GATED"),
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
    ):
        _true(status, key, code)

    for key, code in (
        ("maker_queue_certified", "ALL_PAPER_MAKER_QUEUE_UNEXPECTEDLY_CERTIFIED"),
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
    if list(status.get("operator_message_sync_errors") or []):
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_SYNC_ERRORS_PRESENT")
    if status.get("isolated_settings_overrides") != ["telegram_bot_token", "telegram_chat_id"]:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_ISOLATED_SETTINGS_OVERRIDE_SET_MISMATCH")

    return AllPaperFirstCycleAcceptanceV2(
        version=ALL_PAPER_DEPLOYMENT_ACCEPTANCE_V2_VERSION,
        release_sha=base.release_sha,
        cycle_finished_at=base.cycle_finished_at,
        cycle_age_seconds=base.cycle_age_seconds,
        runtime_version=FINAL_ALL_PAPER_RUNTIME_V7_VERSION,
        execution_protocol=base.execution_protocol,
        maker_accounting_version=base.maker_accounting_version,
        operator_state_version=OPERATOR_STATE_CORRECTIVE_VERSION,
        operator_state_v2_version=OPERATOR_STATE_CORRECTIVE_V2_VERSION,
        operator_state_v3_version=OPERATOR_STATE_CORRECTIVE_V3_VERSION,
        accepted=True,
    )
