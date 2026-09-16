from pathlib import Path

from polymarket_scanner.weather_only_live_paper_all_signals_final_v7 import (
    FINAL_ALL_PAPER_RUNTIME_V7_VERSION,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final_v8 import (
    FINAL_ALL_PAPER_RUNTIME_V8_VERSION,
)
from polymarket_scanner.weather_only_live_paper_all_signals_final_v9 import (
    FINAL_ALL_PAPER_RUNTIME_V9_VERSION,
)


FINAL_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final_v9"


def _text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_final_renderer_targets_final_v9_wrapper_and_enforces_environment_boundary():
    text = _text("deploy/render-all-paper-unit.py")
    assert f'ALL_PAPER_MODULE = "{FINAL_MODULE}"' in text
    assert "-m {ALL_PAPER_MODULE}" in text
    assert "Environment=ALPHA_DISABLE_DOTENV=1" in text
    assert "UnsetEnvironment=" in text
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "no_proxy",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    ):
        assert name in text
    assert "HOST_RELEASE_GATE" in text
    assert "verify-checkout" in text
    assert FINAL_ALL_PAPER_RUNTIME_V7_VERSION
    assert FINAL_ALL_PAPER_RUNTIME_V8_VERSION
    assert FINAL_ALL_PAPER_RUNTIME_V9_VERSION


def test_prepare_routes_to_root_custody_v4_and_requires_complete_operator_stack():
    wrapper = _text("deploy/prepare-all-paper-candidate.sh")
    compat = _text("deploy/prepare-all-paper-candidate-v2.sh")
    text = _text("deploy/prepare-all-paper-candidate-v3.sh")
    assert "prepare-all-paper-candidate-v3.sh" in wrapper
    assert "prepare-all-paper-candidate-v3.sh" in compat
    assert f'FINAL_MODULE="{FINAL_MODULE}"' in text
    assert "polymarket_scanner/weather_only_live_paper_all_signals_final_v9.py" in text
    assert "polymarket_scanner/weather_only_all_paper_deployment_acceptance_v2.py" in text
    assert 'ROLLBACK_DIR="/var/lib/polymarket-weather-paper-rollback"' in text
    assert 'GATE="${LIBEXEC}/release-gate.py"' in text
    assert 'HOST_VENV="${LIBEXEC}/weather-paper-venv-snapshot.py"' in text
    assert 'HOST_RECOVERY="${LIBEXEC}/restore-rollback.sh"' in text
    assert 'HOST_PATHS="/etc/polymarket-weather-paper/host-paths.conf"' in text
    assert "verify-checkout" in text
    assert "verify-object" in text
    assert "snapshot-generation-v4" in text
    assert "all-paper-rollback-v4-root-custody-hash-bound" in text
    assert "rollback-manifest-v4.json" in text
    assert '"${HOST_VENV}" verify' in text
    assert '"${HOST_VENV}" verify-tree' in text
    assert "requirements-runtime-hashed.txt" in text
    assert "--require-hashes" in text
    assert "assert_attested_all_paper_configuration" in text
    assert "assert_network_environment_isolated" in text
    assert "renderer is not final-v9" in text
    assert "renderer does not isolate proxy environment" in text


def test_runtime_attestation_v2_targets_final_v9_and_proves_network_dotenv_boundary():
    base = _text("deploy/attest-all-paper-runtime.py")
    text = _text("deploy/attest-all-paper-runtime-v2.py")
    assert f'FINAL_MODULE = "{FINAL_MODULE}"' in text
    assert 'DOTENV_ENV_LINE = "Environment=ALPHA_DISABLE_DOTENV=1"' in text
    assert '"weather_only_live_paper_all_signals_final_v9.py"' in text
    assert "ALL_PAPER_IMPLICIT_DOTENV_PRESENT" in text
    assert "ALL_PAPER_UNIT_DOTENV_DISABLE_MISSING_OR_DUPLICATED" in text
    assert "ALL_PAPER_PROCESS_DOTENV_DISABLE_NOT_PROVEN" in text
    assert "ALL_PAPER_UNIT_NETWORK_ENV_UNSET_INCOMPLETE" in text
    assert "ALL_PAPER_PROCESS_NETWORK_ENVIRONMENT_LEAK" in text
    assert "base.FINAL_ALL_PAPER_MODULE = FINAL_MODULE" in text
    assert "ALL_PAPER_ENVIRONMENT_FILE_FORBIDDEN_KEY" in base
    assert '"mode": "0600"' in base


def test_install_start_and_persistence_use_v9_host_and_final_acceptance_gates():
    setup = _text("deploy/setup-all-paper-service.sh")
    preflight = _text("deploy/preflight-all-paper-deployment.sh")
    start = _text("deploy/start-all-paper-candidate.sh")
    persistence = _text("deploy/enable-all-paper-persistence.sh")
    assert "deploy/render-all-paper-unit.py" in setup
    assert "deploy/attest-all-paper-runtime-v2.py" in preflight
    assert 'GATE="${LIBEXEC}/release-gate.py"' in preflight
    assert "verify-checkout" in preflight
    assert "deploy/attest-all-paper-runtime-v2.py" in start
    assert "deploy/verify-all-paper-first-cycle-v2.py" in start
    assert "deploy/verify-operator-sync-complete.py" in start
    assert "deploy/verify-three-layer-validation-status.py" in start
    assert 'ROLLBACK_DIR="/var/lib/polymarket-weather-paper-rollback"' in start
    assert "previous-db-present" in start
    assert "previous-venv.tar" in start
    assert "snapshot-generation-v4" in start
    assert "all-paper-rollback-v4-root-custody-hash-bound" in start
    assert "rollback-manifest-v4.json" in start
    assert 'HOST_RECOVERY="${LIBEXEC}/restore-rollback.sh"' in start
    assert "deploy/attest-all-paper-runtime-v2.py" in persistence
    assert "deploy/verify-all-paper-first-cycle-v2.py" in persistence
    assert "deploy/verify-operator-sync-complete.py" in persistence
    assert "deploy/verify-three-layer-fresh-capture.py" in persistence
    assert "ignored .env exists" in persistence
    assert "verify-checkout" in persistence


def test_root_custodied_rollback_snapshot_and_restore_bind_database_source_and_exact_venv():
    snapshot_wrapper = _text("deploy/snapshot-all-paper-rollback.sh")
    snapshot_compat = _text("deploy/snapshot-all-paper-rollback-v2.sh")
    restore_wrapper = _text("deploy/restore-all-paper-rollback.sh")
    restore_compat = _text("deploy/restore-all-paper-rollback-v2.sh")
    snapshot = _text("deploy/weather-paper-host-snapshot.sh")
    restore = _text("deploy/weather-paper-host-recovery.sh")
    prepare = _text("deploy/prepare-all-paper-candidate-v3.sh")

    assert "/usr/local/libexec/polymarket-weather-paper/snapshot-rollback.sh" in snapshot_wrapper
    assert "/usr/local/libexec/polymarket-weather-paper/snapshot-rollback.sh" in snapshot_compat
    assert "/usr/local/libexec/polymarket-weather-paper/restore-rollback.sh" in restore_wrapper
    assert "/usr/local/libexec/polymarket-weather-paper/restore-rollback.sh" in restore_compat
    assert "HOME=/nonexistent" in snapshot_wrapper
    assert "HOME=/nonexistent" in snapshot_compat

    assert "previous-release.sha" in snapshot
    assert "previous-tree.sha" in snapshot
    assert "previous-db-present" in snapshot
    assert "previous-weather-paper.sqlite3" in snapshot
    assert "previous-db.sha256" in snapshot
    assert "previous-venv.tar" in snapshot
    assert "previous-venv.json" in snapshot
    assert "snapshot-generation-v4" in snapshot
    assert "all-paper-rollback-v4-root-custody-hash-bound" in snapshot
    assert "rollback-manifest-v4.json" in snapshot
    assert 'rm -f "${GENERATION}" "${MANIFEST}"' in snapshot
    assert snapshot.count('"${VENV_HELPER}" verify-tree') >= 1
    assert "verify-checkout" in snapshot

    assert 'HOST_PATHS="/etc/polymarket-weather-paper/host-paths.conf"' in restore
    assert 'source "${HOST_PATHS}"' in restore
    assert "rollback-manifest-v4.json" in restore
    assert "PASS_ROOT_CUSTODY_ROLLBACK_MANIFEST" in restore
    assert "previous-db-present" in restore
    assert "previous-weather-paper.sqlite3" in restore
    assert 'Path(str(target)+\'-wal\').unlink' in restore
    assert restore.index("PASS_ROOT_CUSTODY_ROLLBACK_MANIFEST") < restore.index(
        'if [[ "${PREVIOUS_DB_PRESENT}" == "1" ]]'
    )
    assert "/usr/bin/python3" in restore
    assert '"${VENV_HELPER}" restore' in restore
    assert '"${VENV_HELPER}" verify-tree' in restore
    assert "verify-object" in restore
    assert "verify-checkout" in restore
    assert 'checkout --detach "${PREVIOUS_SHA}"' in restore
    assert "systemctl enable" in restore
    assert "systemctl start" in restore

    assert "rollback_on_error" in prepare
    assert 'HOST_RECOVERY="${LIBEXEC}/restore-rollback.sh"' in prepare


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
