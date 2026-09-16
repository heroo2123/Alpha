from pathlib import Path

from polymarket_scanner.weather_only_live_paper_all_signals_final_v7 import (
    FINAL_ALL_PAPER_RUNTIME_V7_VERSION,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final_v8 import (
    FINAL_ALL_PAPER_RUNTIME_V8_VERSION,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final_v10 import (
    FINAL_ALL_PAPER_RUNTIME_V9_VERSION,
)


FINAL_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final_v10"


def _text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_final_acceptance_v2_requires_complete_v9_operator_network_and_recall_stack():
    base = _text("polymarket_scanner/weather_only_all_paper_deployment_acceptance.py")
    text = _text("polymarket_scanner/weather_only_all_paper_deployment_acceptance_v2.py")
    assert "FINAL_ALL_PAPER_RUNTIME_V5_VERSION" in text
    assert "FINAL_ALL_PAPER_RUNTIME_V6_VERSION" in text
    assert "FINAL_ALL_PAPER_RUNTIME_V7_VERSION" in text
    assert "FINAL_ALL_PAPER_RUNTIME_V8_VERSION" in text
    assert "FINAL_ALL_PAPER_RUNTIME_V9_VERSION" in text
    assert "OPERATOR_STATE_CORRECTIVE_VERSION" in text
    assert "OPERATOR_STATE_CORRECTIVE_V2_VERSION" in text
    assert "OPERATOR_STATE_CORRECTIVE_V3_VERSION" in text
    assert "OPERATOR_STATE_CORRECTIVE_V4_VERSION" in text
    assert "operator_visible_invalidation_required" in text
    assert "operator_retry_release_requires_visible_invalidation" in text
    assert "operator_retry_preserves_original_signal_fingerprint" in text
    assert "operator_message_sync_healthy" in text
    assert "operator_sync_restart_pagination_required" in text
    assert "operator_deleted_message_terminal_confirmation" in text
    assert "source_shock_retry_guard_final_episode_identity" in text
    assert "dotenv_loading_disabled" in text
    assert "implicit_nontelegram_settings_defaulted" in text
    assert "terminal_invalidation_identity_strict" in text
    assert "terminal_invalidation_requires_post_receipt_prestate" in text
    assert "operator_restart_visibility_required" in text
    assert "operator_sync_before_startup_required" in text
    assert "operator_recent_terminal_reason_visible" in text
    assert "maker_proposal_queue_uncertified_label" in text
    assert "isolated_settings_overrides" in text
    assert "operator_retry_max_per_evidence" in text
    assert "operator_retry_cooldown_seconds" in text
    assert "historical_terminal_operator_sync_complete" in text
    assert "network_environment_absent_before_http_client_construction" in text
    assert "global_weather_recall_complete" in text
    assert "global_weather_recall_fresh" in text
    assert "global_weather_recall_max_reuse_seconds" in text
    assert "global_weather_recall_certified_at" in text
    assert "global_weather_recall_age_seconds" in text
    assert "GLOBAL_WEATHER_RECALL_AGE_EVIDENCE_MISMATCH" in text
    assert "global_weather_recall" in text
    # Preserve the mature lower-layer gates as part of the additive acceptance chain.
    assert "post_receipt_future_day_provider_refetch_required" in base
    assert "post_receipt_three_layer_thesis_revalidation_required" in base
    assert "post_receipt_weather_before_clob_required" in base
    assert "maker_settlement_strict_uma_finality" in base
    assert "source_shock_full_ttl_before_midnight_margin_required" in base
