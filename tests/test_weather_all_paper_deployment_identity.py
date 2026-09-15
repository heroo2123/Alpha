from pathlib import Path

from polymarket_scanner.weather_only_live_paper_all_signals_final_v3 import (
    FINAL_ALL_PAPER_RUNTIME_V3_VERSION,
)


FINAL_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final_v3"


def _text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_final_renderer_targets_final_wrapper():
    text = _text("deploy/render-all-paper-unit.py")
    assert f'ALL_PAPER_MODULE = "{FINAL_MODULE}"' in text
    assert "-m {ALL_PAPER_MODULE}" in text
    assert FINAL_ALL_PAPER_RUNTIME_V3_VERSION


def test_prepare_requires_and_imports_same_final_wrapper():
    text = _text("deploy/prepare-all-paper-candidate.sh")
    assert f'FINAL_MODULE="{FINAL_MODULE}"' in text
    assert "polymarket_scanner/weather_only_live_paper_all_signals_final_v3.py" in text
    assert 'import ${FINAL_MODULE}' in text
    assert "renderer does not point to final entrypoint" in text
    assert "requirements-runtime-hashed.txt" in text
    assert "--require-hashes" in text


def test_runtime_attestation_targets_same_final_wrapper_and_inventories_it():
    text = _text("deploy/attest-all-paper-runtime.py")
    assert f'FINAL_ALL_PAPER_MODULE = "{FINAL_MODULE}"' in text
    assert '"weather_only_live_paper_all_signals_final_v3.py"' in text
    assert "expected_module=FINAL_ALL_PAPER_MODULE" in text
    assert "ALL_PAPER_ENVIRONMENT_FILE_FORBIDDEN_KEY" in text
    assert '"mode": "0600"' in text


def test_install_start_and_persistence_use_all_paper_final_gates():
    setup = _text("deploy/setup-all-paper-service.sh")
    start = _text("deploy/start-all-paper-candidate.sh")
    persistence = _text("deploy/enable-all-paper-persistence.sh")

    assert "deploy/render-all-paper-unit.py" in setup
    assert "deploy/attest-all-paper-runtime.py" in start
    assert "deploy/verify-all-paper-first-cycle.py" in start
    assert "deploy/verify-three-layer-validation-status.py" in start
    assert "previous-db-present" in start
    assert "previous-weather-paper.sqlite3" in start
    assert "deploy/attest-all-paper-runtime.py" in persistence
    assert "deploy/verify-all-paper-first-cycle.py" in persistence
    assert "deploy/verify-three-layer-fresh-capture.py" in persistence


def test_rollback_snapshot_and_restore_include_database_state():
    snapshot = _text("deploy/snapshot-all-paper-rollback.sh")
    restore = _text("deploy/restore-all-paper-rollback.sh")
    prepare = _text("deploy/prepare-all-paper-candidate.sh")

    assert "previous-db-present" in snapshot
    assert "previous-weather-paper.sqlite3" in snapshot
    assert "verify_weather_paper_restore" in snapshot
    assert "previous-db-present" in restore
    assert "previous-weather-paper.sqlite3" in restore
    assert 'Path(str(target) + "-wal").unlink' in restore
    assert "failed-candidate-weather-paper" in restore
    assert "rollback_prepare_on_error" in prepare
    assert "restore-all-paper-rollback.sh" in prepare


def test_final_acceptance_requires_all_corrective_wrapper_markers():
    text = _text("polymarket_scanner/weather_only_all_paper_deployment_acceptance.py")
    assert "FINAL_ALL_PAPER_RUNTIME_V2_VERSION" in text
    assert "FINAL_ALL_PAPER_RUNTIME_V3_VERSION" in text
    assert 'status.get("final_all_paper_runtime_v2_version")' in text
    assert 'status.get("final_all_paper_runtime_v3_version")' in text
    assert "ALL_PAPER_FINAL_V3_WRAPPER_VERSION_MISMATCH" in text
    assert "same_day_dynamic_fee_semantics_fail_closed" in text
    assert "maker_settlement_strict_uma_finality" in text
    assert "structural_validated_pnl_enabled" in text
    assert "future_day_forecast_run_age_known" in text
    assert "source_shock_full_ttl_before_midnight_margin_required" in text
    assert "structural_telegram_theoretical_only_label" in text
