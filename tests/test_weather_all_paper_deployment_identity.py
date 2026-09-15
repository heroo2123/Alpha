from pathlib import Path

from polymarket_scanner.weather_only_live_paper_all_signals_final_v7 import (
    FINAL_ALL_PAPER_RUNTIME_V7_VERSION,
)


FINAL_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final_v7"


def _text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_final_renderer_targets_final_v7_wrapper_and_disables_dotenv():
    text = _text("deploy/render-all-paper-unit.py")
    assert f'ALL_PAPER_MODULE = "{FINAL_MODULE}"' in text
    assert "-m {ALL_PAPER_MODULE}" in text
    assert "Environment=ALPHA_DISABLE_DOTENV=1" in text
    assert FINAL_ALL_PAPER_RUNTIME_V7_VERSION


def test_prepare_routes_to_exact_venv_v2_and_requires_complete_operator_stack():
    wrapper = _text("deploy/prepare-all-paper-candidate.sh")
    text = _text("deploy/prepare-all-paper-candidate-v2.sh")
    assert "prepare-all-paper-candidate-v2.sh" in wrapper
    assert f'FINAL_MODULE="{FINAL_MODULE}"' in text
    assert "polymarket_scanner/weather_only_live_paper_all_signals_final_v7.py" in text
    assert "polymarket_scanner/weather_only_operator_state_corrective.py" in text
    assert "polymarket_scanner/weather_only_operator_state_corrective_v2.py" in text
    assert "polymarket_scanner/weather_only_operator_state_corrective_v3.py" in text
    assert "polymarket_scanner/weather_only_operator_state_corrective_v4.py" in text
    assert "polymarket_scanner/weather_only_all_paper_deployment_acceptance_v2.py" in text
    assert "attest-all-paper-runtime-v2.py" in text
    assert "verify-all-paper-first-cycle-v2.py" in text
    assert "previous-venv.tar" in text
    assert "previous-venv.json" in text
    assert "snapshot-generation-v2" in text
    assert "all-paper-rollback-v2-exact-venv" in text
    assert '"${TMP_HELPER}" verify-tree' in text
    assert "renderer does not point to final-v7 entrypoint" in text
    assert "renderer does not disable dotenv" in text
    assert "requirements-runtime-hashed.txt" in text
    assert "--require-hashes" in text
    assert "assert_attested_all_paper_configuration" in text


def test_runtime_attestation_v2_targets_final_v7_and_proves_dotenv_boundary():
    base = _text("deploy/attest-all-paper-runtime.py")
    text = _text("deploy/attest-all-paper-runtime-v2.py")
    assert f'FINAL_MODULE = "{FINAL_MODULE}"' in text
    assert 'DOTENV_ENV_LINE = "Environment=ALPHA_DISABLE_DOTENV=1"' in text
    assert '"weather_only_live_paper_all_signals_final_v7.py"' in text
    assert "ALL_PAPER_IMPLICIT_DOTENV_PRESENT" in text
    assert "ALL_PAPER_UNIT_DOTENV_DISABLE_MISSING_OR_DUPLICATED" in text
    assert "ALL_PAPER_PROCESS_DOTENV_DISABLE_NOT_PROVEN" in text
    assert "base.FINAL_ALL_PAPER_MODULE = FINAL_MODULE" in text
    assert "ALL_PAPER_ENVIRONMENT_FILE_FORBIDDEN_KEY" in base
    assert '"mode": "0600"' in base


def test_install_start_and_persistence_use_final_v2_gates():
    setup = _text("deploy/setup-all-paper-service.sh")
    preflight = _text("deploy/preflight-all-paper-deployment.sh")
    start = _text("deploy/start-all-paper-candidate.sh")
    persistence = _text("deploy/enable-all-paper-persistence.sh")
    assert "deploy/render-all-paper-unit.py" in setup
    assert "deploy/attest-all-paper-runtime-v2.py" in preflight
    assert "deploy/attest-all-paper-runtime-v2.py" in start
    assert "deploy/verify-all-paper-first-cycle-v2.py" in start
    assert "deploy/verify-three-layer-validation-status.py" in start
    assert "previous-db-present" in start
    assert "previous-venv.tar" in start
    assert "snapshot-generation-v2" in start
    assert "deploy/attest-all-paper-runtime-v2.py" in persistence
    assert "deploy/verify-all-paper-first-cycle-v2.py" in persistence
    assert "deploy/verify-three-layer-fresh-capture.py" in persistence
    assert "ignored .env exists in candidate checkout" in persistence


def test_rollback_snapshot_and_restore_include_database_and_exact_venv_state():
    base_snapshot = _text("deploy/snapshot-all-paper-rollback.sh")
    snapshot = _text("deploy/snapshot-all-paper-rollback-v2.sh")
    restore_wrapper = _text("deploy/restore-all-paper-rollback.sh")
    restore = _text("deploy/restore-all-paper-rollback-v2.sh")
    prepare = _text("deploy/prepare-all-paper-candidate-v2.sh")
    assert "previous-db-present" in base_snapshot
    assert "previous-weather-paper.sqlite3" in base_snapshot
    assert "verify_weather_paper_restore" in base_snapshot
    assert "previous-venv.tar" in snapshot
    assert "previous-venv.json" in snapshot
    assert "previous-db.sha256" in snapshot
    assert "snapshot-generation-v2" in snapshot
    assert "all-paper-rollback-v2-exact-venv" in snapshot
    assert 'rm -f "${GENERATION}"' in snapshot
    assert snapshot.count('"${VENV_HELPER}" verify-tree') >= 2
    assert "restore-all-paper-rollback-v2.sh" in restore_wrapper
    assert "previous-db-present" in restore
    assert "previous-weather-paper.sqlite3" in restore
    assert 'Path(str(target) + "-wal").unlink' in restore
    assert "failed-candidate-weather-paper" in restore
    assert "/usr/bin/python3" in restore
    assert '"${TMP_HELPER}" restore' in restore
    assert '"${TMP_HELPER}" verify-tree' in restore
    assert "rollback_prepare_on_error" in prepare
    assert "restore-all-paper-rollback.sh" in prepare


def test_final_acceptance_v2_requires_complete_operator_and_config_stack():
    base = _text("polymarket_scanner/weather_only_all_paper_deployment_acceptance.py")
    text = _text("polymarket_scanner/weather_only_all_paper_deployment_acceptance_v2.py")
    assert "FINAL_ALL_PAPER_RUNTIME_V5_VERSION" in text
    assert "FINAL_ALL_PAPER_RUNTIME_V6_VERSION" in text
    assert "FINAL_ALL_PAPER_RUNTIME_V7_VERSION" in text
    assert "OPERATOR_STATE_CORRECTIVE_VERSION" in text
    assert "OPERATOR_STATE_CORRECTIVE_V2_VERSION" in text
    assert "OPERATOR_STATE_CORRECTIVE_V3_VERSION" in text
    assert "OPERATOR_STATE_CORRECTIVE_V4_VERSION" in text
    assert "operator_visible_invalidation_required" in text
    assert "operator_retry_release_requires_visible_invalidation" in text
    assert "operator_retry_preserves_original_signal_fingerprint" in text
    assert "operator_message_sync_healthy" in text
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
    # Preserve the mature lower-layer gates as part of the additive acceptance chain.
    assert "post_receipt_future_day_provider_refetch_required" in base
    assert "post_receipt_three_layer_thesis_revalidation_required" in base
    assert "post_receipt_weather_before_clob_required" in base
    assert "maker_settlement_strict_uma_finality" in base
    assert "source_shock_full_ttl_before_midnight_margin_required" in base
