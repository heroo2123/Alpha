from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path


PREPARE = Path("deploy/prepare-weather-paper-candidate.sh")
PREFLIGHT = Path("deploy/preflight-weather-paper-deployment.sh")
SETUP = Path("deploy/setup-weather-paper-service.sh")
START = Path("deploy/start-weather-paper-candidate.sh")
BACKUP = Path("deploy/pre-release-weather-paper-backup.sh")
RENDERER = Path("deploy/render-weather-paper-unit.py")
NETWORK = Path("deploy/check-weather-paper-network.py")
FIRST_CYCLE = Path("deploy/verify-weather-paper-first-cycle.py")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_all_weather_deploy_steps_use_dedicated_app_and_release_marker():
    for path in (PREPARE, PREFLIGHT, SETUP, START, BACKUP):
        text = _text(path)
        assert "ALPHA_WEATHER_APP_DIR" in text
        assert "polymarket-weather-paper-app" in text
        assert "weather-paper-release.sha" in text
        assert '${CONFIG_DIR}/release.sha' not in text


def test_prepare_script_does_not_start_enable_or_modify_legacy_release_marker():
    text = _text(PREPARE)
    executable = [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any("systemctl start" in line for line in executable)
    assert not any("systemctl enable" in line for line in executable)
    assert "weather-paper-release.sha" in text
    assert 'RELEASE_FILE="${CONFIG_DIR}/release.sha"' not in text
    assert "checkout --detach" in text
    assert "merge-base --is-ancestor" in text


def test_prepare_requires_and_import_smokes_exact_final_runtime_before_release_marker():
    text = _text(PREPARE)
    assert "polymarket_scanner/weather_only_paper_recovery_final.py" in text
    assert 'FINAL_MODULE="polymarket_scanner.weather_only_live_paper_final"' in text
    assert 'import ${FINAL_MODULE}' in text
    import_pos = text.index('import ${FINAL_MODULE}')
    marker_publish_pos = text.index('mv -f "${TMP_MARKER}" "${RELEASE_FILE}"')
    assert import_pos < marker_publish_pos


def test_renderer_binds_unit_to_isolated_marker_and_paper_environment():
    spec = importlib.util.spec_from_file_location("weather_renderer_isolated", RENDERER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    unit = module.render(Path("/opt/weather-paper"), Path("/home/test/.config-alpha"), "testuser")
    assert "/home/test/.config-alpha/weather-paper-release.sha" in unit
    assert "EnvironmentFile=/home/test/.config-alpha/weather-paper.env" in unit
    assert "EnvironmentFile=/home/test/.config-alpha/bot.env" not in unit
    assert "WorkingDirectory=/opt/weather-paper" in unit


def test_legacy_scanner_service_names_are_not_operated_by_weather_deploy_scripts():
    combined = "\n".join(_text(path) for path in (PREPARE, PREFLIGHT, SETUP, START))
    for forbidden in (
        "systemctl start polymarket-edge-scanner",
        "systemctl stop polymarket-edge-scanner",
        "systemctl enable polymarket-edge-scanner",
        "systemctl restart polymarket-edge-scanner",
        "systemctl start polymarket-edge-command",
        "systemctl enable polymarket-edge-command",
    ):
        assert forbidden not in combined


def test_preflight_requires_legacy_services_disabled_not_merely_stopped():
    text = _text(PREFLIGHT)
    assert 'check-weather-paper-service-isolation.sh" --require-disabled' in text


def test_deployment_python_helpers_import_from_unrelated_cwd(tmp_path: Path):
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    root = Path.cwd().resolve()
    for relative in (NETWORK, FIRST_CYCLE):
        script = (root / relative).resolve()
        result = subprocess.run(
            [sys.executable, str(script), "--help"],
            cwd=tmp_path,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 0, f"{relative}: {result.stderr}"
        assert "ModuleNotFoundError" not in result.stderr
        assert "usage:" in result.stdout.lower()
