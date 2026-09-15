from __future__ import annotations

"""Final first-cycle acceptance for operator-synchronized all-weather PAPER runtime."""

from dataclasses import asdict, dataclass

from .weather_only_all_paper_deployment_acceptance import (
    AllPaperDeploymentAcceptanceError,
    accept_first_all_paper_cycle as accept_legacy_cycle,
)
from .weather_only_live_paper_all_signals_final_v5 import FINAL_ALL_PAPER_RUNTIME_V5_VERSION
from .weather_only_live_paper_all_signals_final_v6 import FINAL_ALL_PAPER_RUNTIME_V6_VERSION
from .weather_only_live_paper_all_signals_final_v7 import FINAL_ALL_PAPER_RUNTIME_V7_VERSION
from .weather_only_operator_state_corrective import OPERATOR_STATE_CORRECTIVE_VERSION
from .weather_only_operator_state_corrective_v2 import OPERATOR_STATE_CORRECTIVE_V2_VERSION
from .weather_only_operator_state_corrective_v3 import OPERATOR_STATE_CORRECTIVE_V3_VERSION


ALL_PAPER_DEPLOYMENT_ACCEPTANCE_V2_VERSION = (
    "weather_all_paper_first_cycle_acceptance_v9_operator_visible_terminal_state"
)


@dataclass(frozen=True, slots=True)
class OperatorStateFirstCycleAcceptance:
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


def _true(status: dict, key: str, code: str) -> None:
    if status.get(key) is not True:
        raise AllPaperDeploymentAcceptanceError(code)


def _false(status: dict, key: str, code: str) -> None:
    if status.get(key) is not False:
        raise AllPaperDeploymentAcceptanceError(code)


def accept_first_all_paper_cycle(
    status: object,
    *,
    expected_release_sha: str,
    not_before: float,
    now: float | None = None,
    max_age_seconds: float = 600.0,
) -> OperatorStateFirstCycleAcceptance:
    legacy = accept_legacy_cycle(
        status,
        expected_release_sha=expected_release_sha,
        not_before=not_before,
        now=now,
        max_age_seconds=max_age_seconds,
    )
    if not isinstance(status, dict):
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_STATUS_TYPE_INVALID")

    for key, expected, code in (
        (
            "final_all_paper_runtime_v5_version",
            FINAL_ALL_PAPER_RUNTIME_V5_VERSION,
            "ALL_PAPER_FINAL_V5_WRAPPER_VERSION_MISMATCH",
        ),
        (
            "final_all_paper_runtime_v6_version",
            FINAL_ALL_PAPER_RUNTIME_V6_VERSION,
            "ALL_PAPER_FINAL_V6_WRAPPER_VERSION_MISMATCH",
        ),
        (
            "final_all_paper_runtime_v7_version",
            FINAL_ALL_PAPER_RUNTIME_V7_VERSION,
            "ALL_PAPER_FINAL_V7_WRAPPER_VERSION_MISMATCH",
        ),
        (
            "operator_state_corrective_version",
            OPERATOR_STATE_CORRECTIVE_VERSION,
            "ALL_PAPER_OPERATOR_STATE_CORRECTIVE_MISSING",
        ),
        (
            "operator_state_corrective_v2_version",
            OPERATOR_STATE_CORRECTIVE_V2_VERSION,
            "ALL_PAPER_OPERATOR_STATE_CORRECTIVE_V2_MISSING",
        ),
        (
            "operator_state_corrective_v3_version",
            OPERATOR_STATE_CORRECTIVE_V3_VERSION,
            "ALL_PAPER_OPERATOR_STATE_CORRECTIVE_V3_MISSING",
        ),
    ):
        if status.get(key) != expected:
            raise AllPaperDeploymentAcceptanceError(code)

    for key, code in (
        (
            "operator_visible_invalidation_required",
            "ALL_PAPER_VISIBLE_INVALIDATION_NOT_REQUIRED",
        ),
        (
            "operator_retry_release_requires_visible_invalidation",
            "ALL_PAPER_RETRY_RELEASE_NOT_BOUND_TO_VISIBLE_INVALIDATION",
        ),
        (
            "implicit_dotenv_forbidden",
            "ALL_PAPER_IMPLICIT_DOTENV_NOT_FORBIDDEN",
        ),
        (
            "source_shock_retry_guard_final_episode_identity",
            "ALL_PAPER_SOURCE_SHOCK_RETRY_IDENTITY_NOT_FINAL",
        ),
        (
            "operator_retry_preserves_original_signal_fingerprint",
            "ALL_PAPER_RETRY_MUTATES_ORIGINAL_FINGERPRINT",
        ),
        (
            "operator_message_sync_healthy",
            "ALL_PAPER_OPERATOR_MESSAGE_SYNC_NOT_HEALTHY",
        ),
        (
            "maker_proposal_queue_uncertified_label",
            "ALL_PAPER_MAKER_PROPOSAL_TRUTH_LABEL_MISSING",
        ),
    ):
        _true(status, key, code)

    _false(
        status,
        "maker_simulated_fill_accounting_enabled",
        "ALL_PAPER_MAKER_SIMULATED_FILL_ACCOUNTING_NOT_FALSE",
    )
    _false(
        status,
        "maker_queue_position_certified",
        "ALL_PAPER_MAKER_QUEUE_UNEXPECTEDLY_CERTIFIED",
    )

    sync = status.get("operator_message_sync")
    if not isinstance(sync, dict):
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_SYNC_STATUS_MISSING")
    if sync.get("healthy") is not True:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_SYNC_STATUS_UNHEALTHY")
    if int(sync.get("unconfirmed") or 0) != 0:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_SYNC_UNCONFIRMED")
    if int(sync.get("failed") or 0) != 0:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_SYNC_FAILED")
    if list(status.get("operator_message_sync_errors") or []):
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_SYNC_ERRORS_PRESENT")

    if int(status.get("operator_retry_max_per_evidence") or 0) != 3:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_RETRY_BOUND_MISMATCH")
    if float(status.get("operator_retry_cooldown_seconds") or 0.0) < 60.0:
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_OPERATOR_RETRY_COOLDOWN_TOO_SHORT")

    return OperatorStateFirstCycleAcceptance(
        version=ALL_PAPER_DEPLOYMENT_ACCEPTANCE_V2_VERSION,
        release_sha=legacy.release_sha,
        cycle_finished_at=legacy.cycle_finished_at,
        cycle_age_seconds=legacy.cycle_age_seconds,
        runtime_version=FINAL_ALL_PAPER_RUNTIME_V7_VERSION,
        execution_protocol=legacy.execution_protocol,
        maker_accounting_version=legacy.maker_accounting_version,
        accepted=True,
    )
