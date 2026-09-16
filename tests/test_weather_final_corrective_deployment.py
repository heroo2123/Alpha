from __future__ import annotations

import importlib.util
import os
import runpy
from pathlib import Path

import pytest

from polymarket_scanner.weather_only_all_paper_deployment_acceptance import (
    AllPaperDeploymentAcceptanceError,
)
from polymarket_scanner.weather_only_all_paper_deployment_acceptance_v2 import (
    accept_first_all_paper_cycle_v2,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final_v5 import (
    FINAL_ALL_PAPER_RUNTIME_V5_VERSION,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final_v6 import (
    FINAL_ALL_PAPER_RUNTIME_V6_VERSION,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final_v7 import (
    FINAL_ALL_PAPER_RUNTIME_V7_VERSION,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final_v8 import (
    FINAL_ALL_PAPER_RUNTIME_V8_VERSION,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final_v10 import (
    FINAL_ALL_PAPER_RUNTIME_V9_VERSION,
)
from polymarket_scanner.weather_only_operator_state_corrective import (
    OPERATOR_STATE_CORRECTIVE_VERSION,
)
from polymarket_scanner.weather_only_operator_state_corrective_v2 import (
    OPERATOR_STATE_CORRECTIVE_V2_VERSION,
)
from polymarket_scanner.weather_only_operator_state_corrective_v3 import (
    OPERATOR_STATE_CORRECTIVE_V3_VERSION,
)
from polymarket_scanner.weather_only_operator_state_corrective_v4 import (
    OPERATOR_STATE_CORRECTIVE_V4_VERSION,
)


ROOT = Path(__file__).resolve().parents[1]
SHA = "a" * 40
NOW = 1_800_000_000.0


def _load_script(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _status() -> dict:
    namespace = runpy.run_path(
        str(ROOT / "tests" / "test_weather_all_paper_deployment_acceptance.py"),
        run_name="_all_paper_acceptance_fixture",
    )
    status = namespace["_status"]()
    status.update(
        {
            "final_all_paper_runtime_v5_version": FINAL_ALL_PAPER_RUNTIME_V5_VERSION,
            "final_all_paper_runtime_v6_version": FINAL_ALL_PAPER_RUNTIME_V6_VERSION,
            "final_all_paper_runtime_v7_version": FINAL_ALL_PAPER_RUNTIME_V7_VERSION,
            "final_all_paper_runtime_v8_version": FINAL_ALL_PAPER_RUNTIME_V8_VERSION,
            "final_all_paper_runtime_v9_version": FINAL_ALL_PAPER_RUNTIME_V9_VERSION,
            "final_all_paper_runtime_v10_version": __import__("polymarket_scanner.weather_only_live_paper_all_signals_final_v10",fromlist=["FINAL_ALL_PAPER_RUNTIME_V10_VERSION"]).FINAL_ALL_PAPER_RUNTIME_V10_VERSION,
            "operator_state_corrective_v5_version": __import__("polymarket_scanner.weather_only_operator_state_corrective_v5",fromlist=["OPERATOR_STATE_CORRECTIVE_V5_VERSION"]).OPERATOR_STATE_CORRECTIVE_V5_VERSION,
            "operator_state_corrective_version": OPERATOR_STATE_CORRECTIVE_VERSION,
            "operator_state_corrective_v2_version": OPERATOR_STATE_CORRECTIVE_V2_VERSION,
            "operator_state_corrective_v3_version": OPERATOR_STATE_CORRECTIVE_V3_VERSION,
            "operator_state_corrective_v4_version": OPERATOR_STATE_CORRECTIVE_V4_VERSION,
            "operator_invalidation_transport": "IDEMPOTENT_EDIT_MESSAGE_TEXT",
            "operator_visible_invalidation_required": True,
            "operator_retry_release_requires_visible_invalidation": True,
            "operator_retry_preserves_original_signal_fingerprint": True,
            "operator_retry_max_per_evidence": 3,
            "operator_retry_cooldown_seconds": 180.0,
            "operator_message_sync_healthy": True,
            "operator_message_sync": {
                "healthy": True,
                "unconfirmed": 0,
                "failed": 0,
                "restart_pagination_required": False,
            },
            "operator_message_sync_errors": [],
            "operator_sync_restart_pagination_required": False,
            "operator_deleted_message_terminal_confirmation": True,
            "source_shock_retry_guard_final_episode_identity": True,
            "implicit_dotenv_forbidden": True,
            "dotenv_loading_disabled": True,
            "implicit_nontelegram_settings_defaulted": True,
            "isolated_settings_overrides": ["telegram_bot_token", "telegram_chat_id"],
            "terminal_invalidation_identity_strict": True,
            "terminal_invalidation_requires_post_receipt_prestate": True,
            "operator_restart_visibility_required": True,
            "operator_sync_before_startup_required": True,
            "operator_recent_terminal_reason_visible": True,
            "maker_proposal_queue_uncertified_label": True,
            "maker_queue_certified": False,
            "maker_queue_position_certified": False,
            "historical_terminal_operator_sync_backfill_required": True,
            "historical_terminal_operator_sync_complete": True,
            "historical_terminal_operator_sync_missing": 0,
            "network_environment_isolated": True,
            "network_environment_absent_before_http_client_construction": True,
            "global_weather_recall_required": True,
            "global_weather_recall_complete": True,
            "gamma_census_complete": True,
            "code_loading_environment_isolated": True,
            "python_user_site_disabled": True,
            "maker_legacy_queue_pnl_excluded": True,
            "weather_semantic_product_policy": "STRICT_SUPPORTED_SUBSET",
            "weather_semantic_coverage_complete": False,
            "global_weather_coverage_complete": False,
            "unsupported_weather_events": 1,
            "global_weather_recall_fresh": True,
            "global_weather_recall_max_reuse_seconds": 300.0,
            "global_weather_recall_certified_at": NOW - 1.0,
            "global_weather_recall_age_seconds": 1.0,
            "global_weather_recall": {
                "complete": True,
                "cache_hit": False,
                "pages": 1,
                "scanned_events": 1,
                "retained_events": 1,
                "census_completed_at": NOW - 1.0,
                "age_seconds": 1.0,
                "max_reuse_seconds": 300.0,
            },
        }
    )
    return status


def test_final_acceptance_binds_top_level_recall_evidence_to_nested_census():
    status = _status()
    status["global_weather_recall_age_seconds"] = 2.0
    with pytest.raises(AllPaperDeploymentAcceptanceError) as exc:
        accept_first_all_paper_cycle_v2(
            status, expected_release_sha=SHA, not_before=NOW - 10.0, now=NOW
        )
    assert exc.value.code == "ALL_PAPER_GLOBAL_WEATHER_RECALL_AGE_EVIDENCE_MISMATCH"

    status = _status()
    status["global_weather_recall_certified_at"] = NOW - 2.0
    with pytest.raises(AllPaperDeploymentAcceptanceError) as exc:
        accept_first_all_paper_cycle_v2(
            status, expected_release_sha=SHA, not_before=NOW - 10.0, now=NOW
        )
    assert exc.value.code == "ALL_PAPER_GLOBAL_WEATHER_RECALL_COMPLETION_EVIDENCE_MISMATCH"

    status = _status()
    status["global_weather_recall_max_reuse_seconds"] = 299.0
    with pytest.raises(AllPaperDeploymentAcceptanceError) as exc:
        accept_first_all_paper_cycle_v2(
            status, expected_release_sha=SHA, not_before=NOW - 10.0, now=NOW
        )
    assert exc.value.code == "ALL_PAPER_GLOBAL_WEATHER_RECALL_REUSE_EVIDENCE_MISMATCH"
