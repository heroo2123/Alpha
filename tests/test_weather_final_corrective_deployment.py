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
from polymarket_scanner.weather_only_live_paper_all_signals_final_v9 import (
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
            "global_weather_recall_fresh": True,
            "global_weather_recall_max_reuse_seconds": 300.0,
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


def test_final_acceptance_requires_every_new_operator_config_network_and_recall_invariant():
    accepted = accept_first_all_paper_cycle_v2(
        _status(), expected_release_sha=SHA, not_before=NOW - 10.0, now=NOW
    )
    assert accepted.accepted is True
    assert accepted.runtime_version == FINAL_ALL_PAPER_RUNTIME_V9_VERSION
    assert accepted.operator_state_v3_version == OPERATOR_STATE_CORRECTIVE_V3_VERSION
    assert accepted.operator_state_v4_version == OPERATOR_STATE_CORRECTIVE_V4_VERSION

    for key, expected_code in (
        ("operator_message_sync_healthy", "ALL_PAPER_OPERATOR_MESSAGE_SYNC_UNHEALTHY"),
        ("operator_retry_preserves_original_signal_fingerprint", "ALL_PAPER_RETRY_MUTATES_ORIGINAL_FINGERPRINT"),
        ("dotenv_loading_disabled", "ALL_PAPER_DOTENV_LOADING_NOT_DISABLED"),
        ("terminal_invalidation_requires_post_receipt_prestate", "ALL_PAPER_TERMINAL_PRESTATE_NOT_STRICT"),
        ("operator_restart_visibility_required", "ALL_PAPER_OPERATOR_RESTART_VISIBILITY_NOT_REQUIRED"),
        ("operator_sync_before_startup_required", "ALL_PAPER_OPERATOR_STARTUP_SYNC_NOT_REQUIRED"),
        ("maker_proposal_queue_uncertified_label", "ALL_PAPER_MAKER_QUEUE_LABEL_NOT_PROVEN"),
        ("historical_terminal_operator_sync_complete", "ALL_PAPER_HISTORICAL_TERMINAL_BACKFILL_INCOMPLETE"),
        ("network_environment_isolated", "ALL_PAPER_NETWORK_ENVIRONMENT_NOT_ISOLATED"),
        ("network_environment_absent_before_http_client_construction", "ALL_PAPER_NETWORK_ENVIRONMENT_CONSTRUCTION_BOUNDARY_NOT_PROVEN"),
        ("global_weather_recall_complete", "ALL_PAPER_GLOBAL_WEATHER_RECALL_INCOMPLETE"),
        ("global_weather_recall_fresh", "ALL_PAPER_GLOBAL_WEATHER_RECALL_STALE"),
        ("operator_deleted_message_terminal_confirmation", "ALL_PAPER_DELETED_MESSAGE_TERMINAL_CONFIRMATION_MISSING"),
    ):
        status = _status()
        status[key] = False
        with pytest.raises(AllPaperDeploymentAcceptanceError) as exc:
            accept_first_all_paper_cycle_v2(
                status, expected_release_sha=SHA, not_before=NOW - 10.0, now=NOW
            )
        assert exc.value.code == expected_code

    status = _status()
    status["operator_sync_restart_pagination_required"] = True
    with pytest.raises(AllPaperDeploymentAcceptanceError) as exc:
        accept_first_all_paper_cycle_v2(
            status, expected_release_sha=SHA, not_before=NOW - 10.0, now=NOW
        )
    assert exc.value.code == "ALL_PAPER_OPERATOR_SYNC_STILL_RESTART_PAGINATED"

    status = _status()
    status["historical_terminal_operator_sync_missing"] = 1
    with pytest.raises(AllPaperDeploymentAcceptanceError) as exc:
        accept_first_all_paper_cycle_v2(
            status, expected_release_sha=SHA, not_before=NOW - 10.0, now=NOW
        )
    assert exc.value.code == "ALL_PAPER_HISTORICAL_TERMINAL_SYNC_MISSING"

    status = _status()
    status["global_weather_recall"] = {"complete": True, "pages": 1, "scanned_events": 0}
    with pytest.raises(AllPaperDeploymentAcceptanceError) as exc:
        accept_first_all_paper_cycle_v2(
            status, expected_release_sha=SHA, not_before=NOW - 10.0, now=NOW
        )
    assert exc.value.code == "ALL_PAPER_GLOBAL_WEATHER_RECALL_EVIDENCE_INVALID"

    status = _status()
    status["global_weather_recall"]["age_seconds"] = 301.0
    with pytest.raises(AllPaperDeploymentAcceptanceError) as exc:
        accept_first_all_paper_cycle_v2(
            status, expected_release_sha=SHA, not_before=NOW - 10.0, now=NOW
        )
    assert exc.value.code == "ALL_PAPER_GLOBAL_WEATHER_RECALL_STALE"


def test_renderer_uses_final_v9_and_disables_dotenv_and_network_environment():
    renderer = _load_script(ROOT / "deploy" / "render-all-paper-unit.py", "render_all_paper_unit")
    text = renderer.render(Path("/home/test/app"), Path("/home/test/config"), "tester")
    assert renderer.ALL_PAPER_MODULE == "polymarket_scanner.weather_only_live_paper_all_signals_final_v9"
    assert text.count("Environment=ALPHA_DISABLE_DOTENV=1") == 1
    assert "weather_only_live_paper_all_signals_final_v9" in text
    assert "EnvironmentFile=/home/test/config/weather-paper.env" in text
    assert "UnsetEnvironment=" in text
    assert "HTTP_PROXY" in text and "SSL_CERT_FILE" in text
    assert "release-gate.py verify-checkout" in text


def test_deployment_scripts_route_through_final_attestation_and_root_custodied_rollback():
    preflight = (ROOT / "deploy" / "preflight-all-paper-deployment.sh").read_text()
    start = (ROOT / "deploy" / "start-all-paper-candidate.sh").read_text()
    persistence = (ROOT / "deploy" / "enable-all-paper-persistence.sh").read_text()
    prepare = (ROOT / "deploy" / "prepare-all-paper-candidate-v3.sh").read_text()
    restore_wrapper = (ROOT / "deploy" / "restore-all-paper-rollback.sh").read_text()
    host_restore = (ROOT / "deploy" / "weather-paper-host-recovery.sh").read_text()
    host_snapshot = (ROOT / "deploy" / "weather-paper-host-snapshot.sh").read_text()
    attester = (ROOT / "deploy" / "attest-all-paper-runtime-v2.py").read_text()

    assert "attest-all-paper-runtime-v2.py" in preflight
    assert "verify-checkout" in preflight
    assert "attest-all-paper-runtime-v2.py" in start
    assert "verify-all-paper-first-cycle-v2.py" in start
    assert "verify-operator-sync-complete.py" in start
    assert "attest-all-paper-runtime-v2.py" in persistence
    assert "verify-all-paper-first-cycle-v2.py" in persistence
    assert "verify-operator-sync-complete.py" in persistence
    assert 'ROLLBACK_DIR="/var/lib/polymarket-weather-paper-rollback"' in prepare
    assert "snapshot-generation-v4" in prepare
    assert "rollback-manifest-v4.json" in prepare
    assert "previous-venv.tar" in prepare
    assert '"${HOST_VENV}" verify-tree' in prepare
    assert "weather_only_live_paper_all_signals_final_v9.py" in prepare
    assert "/usr/local/libexec/polymarket-weather-paper/restore-rollback.sh" in restore_wrapper
    assert "previous-venv.tar" in host_restore
    assert "rollback-manifest-v4.json" in host_restore
    assert "weather-paper-venv-snapshot.py" in (ROOT / "deploy" / "install-weather-paper-host-trust.sh").read_text()
    assert "snapshot-generation-v4" in host_snapshot
    assert "all-paper-rollback-v4-root-custody-hash-bound" in host_snapshot
    assert "rollback-manifest-v4.json" in host_snapshot
    assert "FINAL_MODULE = \"polymarket_scanner.weather_only_live_paper_all_signals_final_v9\"" in attester


def test_exact_venv_snapshot_round_trip(tmp_path):
    helper = _load_script(
        ROOT / "deploy" / "weather-paper-venv-snapshot.py",
        "weather_paper_venv_snapshot",
    )
    app = tmp_path / "app"
    venv = app / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "lib" / "pkg").mkdir(parents=True)
    python = venv / "bin" / "python"
    python.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    python.chmod(0o755)
    data = venv / "lib" / "pkg" / "data.txt"
    data.write_bytes(b"known-good-bytes\n")
    os.symlink("python", venv / "bin" / "python3")

    archive = tmp_path / "previous-venv.tar"
    manifest = tmp_path / "previous-venv.json"
    snap = helper.snapshot(venv, archive, manifest)
    assert snap["entry_count"] >= 6
    helper.verify_archive(archive, manifest)

    data.write_bytes(b"candidate-mutated\n")
    (venv / "lib" / "pkg" / "extra.txt").write_text("candidate only", encoding="utf-8")
    helper.restore(venv, archive, manifest)
    helper.verify_tree(venv, manifest)

    assert data.read_bytes() == b"known-good-bytes\n"
    assert not (venv / "lib" / "pkg" / "extra.txt").exists()
    assert os.readlink(venv / "bin" / "python3") == "python"
