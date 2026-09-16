from pathlib import Path

from polymarket_scanner.weather_only_live_paper_all_signals_final_v7 import FINAL_ALL_PAPER_RUNTIME_V7_VERSION
from polymarket_scanner.weather_only_live_paper_all_signals_final_v8 import FINAL_ALL_PAPER_RUNTIME_V8_VERSION
from polymarket_scanner.weather_only_live_paper_all_signals_final_v9 import FINAL_ALL_PAPER_RUNTIME_V9_VERSION

FINAL_MODULE="polymarket_scanner.weather_only_live_paper_all_signals_final_v9"
def _text(path:str)->str: return Path(path).read_text(encoding="utf-8")


def test_final_renderer_targets_v9_release_venv_and_strict_loader_boundary():
    text=_text("deploy/render-all-paper-unit.py")
    assert f'ALL_PAPER_MODULE = "{FINAL_MODULE}"' in text
    assert '.releases/{release_sha}' in text and '-E -s -m {ALL_PAPER_MODULE}' in text
    assert 'Environment=PYTHONNOUSERSITE=1' in text and 'Environment=ALPHA_DISABLE_DOTENV=1' in text
    for name in ("PYTHONPATH","PYTHONHOME","PYTHONUSERBASE","PYTHONSTARTUP","PYTHONINSPECT","LD_PRELOAD","LD_LIBRARY_PATH","HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY","SSL_CERT_FILE","SSL_CERT_DIR"):
        assert name in text
    assert "HOST_RELEASE_GATE" in text and "--generation-file" in text
    assert FINAL_ALL_PAPER_RUNTIME_V7_VERSION and FINAL_ALL_PAPER_RUNTIME_V8_VERSION and FINAL_ALL_PAPER_RUNTIME_V9_VERSION


def test_prepare_uses_independent_authority_immutable_generation_and_fresh_release_venv():
    wrapper=_text("deploy/prepare-all-paper-candidate.sh"); compat=_text("deploy/prepare-all-paper-candidate-v2.sh"); text=_text("deploy/prepare-all-paper-candidate-v3.sh")
    assert "prepare-all-paper-candidate-v3.sh" in wrapper and "prepare-all-paper-candidate-v3.sh" in compat
    assert f'FINAL_MODULE="{FINAL_MODULE}"' in text
    assert 'GATE="${LIBEXEC}/release-gate.py"' in text and 'HOST_SNAPSHOT="${LIBEXEC}/snapshot-rollback.sh"' in text and 'HOST_RECOVERY="${LIBEXEC}/restore-rollback.sh"' in text
    assert 'verify-authority' in text and 'verify-object' in text and 'verify-generation' in text
    assert text.index('"${HOST_SNAPSHOT}" --candidate-sha') < text.index('checkout --detach "${RELEASE_SHA}"')
    assert '.releases/${RELEASE_SHA}' in text and 'weather-paper-release-venv.py" build' in text and 'requirements-runtime-hashed.txt' in text
    assert '${APP_DIR}/.venv/bin/python" -m pip install' not in text
    for name in ("PYTHONPATH","PYTHONHOME","PYTHONUSERBASE","PYTHONSTARTUP","PYTHONINSPECT","LD_PRELOAD","LD_LIBRARY_PATH"):
        assert name in text


def test_runtime_attestation_v2_targets_v9_and_inspects_actual_code_loading_boundary():
    base=_text("deploy/attest-all-paper-runtime.py"); text=_text("deploy/attest-all-paper-runtime-v2.py")
    assert f'FINAL_MODULE = "{FINAL_MODULE}"' in text
    assert '/proc/{int(pid)}/environ' in text and 'PYTHONNOUSERSITE' in text
    assert 'LD_PRELOAD' in text and 'LD_LIBRARY_PATH' in text and 'PYTHONPATH' in text and 'PYTHONHOME' in text
    assert 'sys_path' in text and 'enable_user_site' in text and 'module_locations' in text
    assert 'ALL_PAPER_PROCESS_FORBIDDEN_ENVIRONMENT_LEAK' in text
    assert 'ALL_PAPER_MODULE_OUTSIDE_APP' in text and 'ALL_PAPER_FOREIGN_SYS_PATH' in text
    assert "ALL_PAPER_ENVIRONMENT_FILE_FORBIDDEN_KEY" in base and '"mode": "0600"' in base


def test_install_preflight_start_are_generation_and_release_interpreter_exact():
    setup=_text("deploy/setup-all-paper-service.sh"); preflight=_text("deploy/preflight-all-paper-deployment.sh"); start=_text("deploy/start-all-paper-candidate.sh"); persistence=_text("deploy/enable-all-paper-persistence.sh")
    assert "deploy/render-all-paper-unit.py" in setup and '.releases/${RELEASE_SHA}/venv' in setup and '--release-sha "${RELEASE_SHA}"' in setup
    assert 'verify-generation --generation-id "${EXPECTED_GENERATION}" --sha "${EXPECTED_SHA}"' in preflight
    assert '.releases/${EXPECTED_SHA}/venv' in preflight and 'deploy/attest-all-paper-runtime-v2.py' in preflight
    assert 'verify-generation --generation-id "${EXPECTED_GENERATION}" --sha "${EXPECTED_SHA}"' in start
    assert '"${HOST_RECOVERY}" --generation-id "${EXPECTED_GENERATION}"' in start
    assert "deploy/verify-all-paper-first-cycle-v2.py" in start and "deploy/verify-operator-sync-complete.py" in start and "deploy/verify-three-layer-validation-status.py" in start
    assert "previous-venv.tar" not in start and "snapshot-generation-v4" not in start and "rollback-manifest-v4.json" not in start
    # Persistence remains a separate later gate and retains mature operator/three-layer checks.
    assert "deploy/attest-all-paper-runtime-v2.py" in persistence and "deploy/verify-all-paper-first-cycle-v2.py" in persistence and "deploy/verify-operator-sync-complete.py" in persistence and "deploy/verify-three-layer-fresh-capture.py" in persistence


def test_host_snapshot_restore_are_generation_specific_and_restore_exact_predecessor_state():
    snapshot=_text("deploy/weather-paper-host-snapshot.sh"); restore=_text("deploy/weather-paper-host-recovery.sh")
    assert 'HOST_PATHS="/etc/polymarket-weather-paper/host-paths.conf"' in snapshot and 'source "${HOST_PATHS}"' in snapshot
    assert 'GENERATIONS="${ROLLBACK_DIR}/generations"' in snapshot and 'GEN="${GENERATIONS}/${GENERATION_ID}"' in snapshot
    assert '[[ "${PREDECESSOR_SHA}" != "${CANDIDATE_SHA}" ]]' in snapshot and 'cutover generation for candidate already exists' in snapshot
    for field in ("generation_id","predecessor_sha","predecessor_tree","candidate_sha","candidate_tree","predecessor_unit_digest","predecessor_db_digest","predecessor_venv_digest","venv_manifest_digest","active","enabled","release_marker","deploy_user","app_dir","creation_timestamp"):
        assert field in snapshot
    assert "snapshot-generation-v4" not in snapshot and "rollback-manifest-v4.json" not in snapshot
    assert 'GEN="${ROLLBACK_DIR}/generations/${GENERATION_ID}"' in restore
    assert 'checkout --detach "${PREDECESSOR_SHA}"' in restore and '"${PREDECESSOR_TREE}"' in restore
    assert 'predecessor-venv.tar' in restore and 'predecessor-venv-manifest.json' in restore
    assert 'PREVIOUS_ENABLED' in restore and 'PREVIOUS_ACTIVE' in restore and 'systemctl enable' in restore and 'systemctl start' in restore


def test_final_acceptance_v2_preserves_mature_operator_network_and_recall_stack():
    base=_text("polymarket_scanner/weather_only_all_paper_deployment_acceptance.py"); text=_text("polymarket_scanner/weather_only_all_paper_deployment_acceptance_v2.py")
    for token in ("FINAL_ALL_PAPER_RUNTIME_V5_VERSION","FINAL_ALL_PAPER_RUNTIME_V6_VERSION","FINAL_ALL_PAPER_RUNTIME_V7_VERSION","FINAL_ALL_PAPER_RUNTIME_V8_VERSION","FINAL_ALL_PAPER_RUNTIME_V9_VERSION","operator_message_sync_healthy","historical_terminal_operator_sync_complete","network_environment_absent_before_http_client_construction","global_weather_recall_complete","global_weather_recall_fresh"):
        assert token in text
    for token in ("post_receipt_future_day_provider_refetch_required","post_receipt_three_layer_thesis_revalidation_required","post_receipt_weather_before_clob_required","maker_settlement_strict_uma_finality"):
        assert token in base
