from __future__ import annotations

import importlib.util
from pathlib import Path


PREPARE = Path("deploy/prepare-weather-paper-candidate.sh")
PREFLIGHT = Path("deploy/preflight-weather-paper-deployment.sh")
SETUP = Path("deploy/setup-weather-paper-service.sh")
START = Path("deploy/start-weather-paper-candidate.sh")
BACKUP = Path("deploy/pre-release-weather-paper-backup.sh")
RENDERER = Path("deploy/render-weather-paper-unit.py")


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
