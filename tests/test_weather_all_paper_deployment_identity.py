from pathlib import Path

from polymarket_scanner.weather_only_live_paper_all_signals_final import (
    FINAL_ALL_PAPER_RUNTIME_VERSION,
)


FINAL_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final"


def _text(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_final_renderer_targets_final_wrapper():
    text = _text("deploy/render-all-paper-unit.py")
    assert f'ALL_PAPER_MODULE = "{FINAL_MODULE}"' in text
    assert "-m {ALL_PAPER_MODULE}" in text
    assert FINAL_ALL_PAPER_RUNTIME_VERSION


def test_prepare_requires_and_imports_same_final_wrapper():
    text = _text("deploy/prepare-all-paper-candidate.sh")
    assert f'FINAL_MODULE="{FINAL_MODULE}"' in text
    assert "polymarket_scanner/weather_only_live_paper_all_signals_final.py" in text
    assert 'import ${FINAL_MODULE}' in text
    assert "renderer does not point to final entrypoint" in text


def test_runtime_attestation_targets_same_final_wrapper_and_inventories_it():
    text = _text("deploy/attest-all-paper-runtime.py")
    assert f'FINAL_ALL_PAPER_MODULE = "{FINAL_MODULE}"' in text
    assert '"weather_only_live_paper_all_signals_final.py"' in text
    assert "expected_module=FINAL_ALL_PAPER_MODULE" in text


def test_install_start_and_persistence_use_all_paper_final_gates():
    setup = _text("deploy/setup-all-paper-service.sh")
    start = _text("deploy/start-all-paper-candidate.sh")
    persistence = _text("deploy/enable-all-paper-persistence.sh")

    assert "deploy/render-all-paper-unit.py" in setup
    assert "deploy/attest-all-paper-runtime.py" in start
    assert "deploy/verify-all-paper-first-cycle.py" in start
    assert "deploy/verify-three-layer-validation-status.py" in start
    assert "deploy/attest-all-paper-runtime.py" in persistence
    assert "deploy/verify-all-paper-first-cycle.py" in persistence
    assert "deploy/verify-three-layer-fresh-capture.py" in persistence


def test_final_acceptance_requires_final_wrapper_marker():
    text = _text("polymarket_scanner/weather_only_all_paper_deployment_acceptance.py")
    assert "FINAL_ALL_PAPER_RUNTIME_VERSION" in text
    assert 'status.get("final_all_paper_runtime_version")' in text
    assert "ALL_PAPER_FINAL_WRAPPER_VERSION_MISMATCH" in text
