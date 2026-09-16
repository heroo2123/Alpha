from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

PREPARE=Path("deploy/prepare-weather-paper-candidate.sh")
PREFLIGHT=Path("deploy/preflight-weather-paper-deployment.sh")
SETUP=Path("deploy/setup-weather-paper-service.sh")
START=Path("deploy/start-weather-paper-candidate.sh")
BACKUP=Path("deploy/pre-release-weather-paper-backup.sh")
RENDERER=Path("deploy/render-weather-paper-unit.py")
NETWORK=Path("deploy/check-weather-paper-network.py")
FIRST_CYCLE=Path("deploy/verify-weather-paper-first-cycle.py")
THREE_LAYER_STATUS=Path("deploy/verify-three-layer-validation-status.py")
SHA="1"*40


def _text(path: Path)->str: return path.read_text(encoding="utf-8")


def test_all_weather_deploy_steps_use_dedicated_app_and_release_marker():
    for path in (PREPARE,PREFLIGHT,SETUP,START,BACKUP):
        text=_text(path); assert "ALPHA_WEATHER_APP_DIR" in text; assert "polymarket-weather-paper-app" in text; assert "weather-paper-release.sha" in text; assert '${CONFIG_DIR}/release.sha' not in text


def test_prepare_script_does_not_start_enable_or_modify_legacy_release_marker():
    text=_text(PREPARE); executable=[line.strip() for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")]
    assert not any("systemctl start" in line for line in executable); assert not any("systemctl enable" in line for line in executable)
    assert "checkout --detach" in text and "merge-base --is-ancestor" in text and "systemctl is-enabled" in text


def test_prepare_default_source_ref_is_current_stage1_corrective_branch():
    assert 'weather-stage1-findings1-4-corrective-2026-09-16' in _text(PREPARE)


def test_prepare_snapshots_before_checkout_and_builds_fresh_release_venv_before_marker():
    text=_text(PREPARE)
    assert 'FINAL_MODULE="polymarket_scanner.weather_only_live_paper_three_layer_validation"' in text
    snapshot=text.index('"${HOST_SNAPSHOT}" --candidate-sha'); checkout=text.index('checkout --detach "${RELEASE_SHA}"'); build=text.index('weather-paper-release-venv.py" build'); publish=text.index('mv -f "${TMP_REL}" "${RELEASE_FILE}"')
    assert snapshot < checkout < build < publish
    assert '.releases/${RELEASE_SHA}' in text and 'requirements-runtime-hashed.txt' in text
    assert '${APP_DIR}/.venv/bin/python" -m pip install' not in text


def test_renderer_binds_unit_to_release_specific_interpreter_strict_env_and_three_layer_wrapper(tmp_path):
    spec=importlib.util.spec_from_file_location("weather_renderer_isolated",RENDERER); assert spec and spec.loader
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    app=tmp_path/"weather-paper"; cfg=tmp_path/"config"; app.mkdir(); cfg.mkdir()
    unit=module.render(app,cfg,"testuser",SHA)
    assert f"{cfg}/weather-paper-release.sha" in unit; assert f"EnvironmentFile={cfg}/weather-paper.env" in unit; assert f"{cfg}/bot.env" not in unit
    assert f"ExecStart={app}/.releases/{SHA}/venv/bin/python -E -s -m" in unit
    assert "Environment=PYTHONNOUSERSITE=1" in unit and "UnsetEnvironment=" in unit
    assert "weather_only_live_paper_three_layer_validation" in unit and "weather_only_live_paper_final" in unit


def test_legacy_scanner_service_names_are_not_operated_by_weather_deploy_scripts():
    combined="\n".join(_text(path) for path in (PREPARE,PREFLIGHT,SETUP,START))
    for forbidden in ("systemctl start polymarket-edge-scanner","systemctl stop polymarket-edge-scanner","systemctl enable polymarket-edge-scanner","systemctl restart polymarket-edge-scanner","systemctl start polymarket-edge-command","systemctl enable polymarket-edge-command"): assert forbidden not in combined


def test_preflight_requires_legacy_services_disabled_not_merely_stopped():
    assert 'check-weather-paper-service-isolation.sh" --require-disabled' in _text(PREFLIGHT)


def test_start_gate_requires_exact_generation_disabled_candidate_and_recovery_on_failure():
    text=_text(START)
    assert "systemctl is-enabled" in text and "verify-three-layer-validation-status.py" in text
    assert 'verify-generation --generation-id "${EXPECTED_GENERATION}" --sha "${EXPECTED_SHA}"' in text
    assert '"${HOST_RECOVERY}" --generation-id "${EXPECTED_GENERATION}"' in text
    assert "weather-paper-three-layer-runtime-acceptance.json" in text


def test_deployment_python_helpers_import_from_unrelated_cwd(tmp_path: Path):
    env=dict(os.environ); env.pop("PYTHONPATH",None); root=Path.cwd().resolve()
    for relative in (NETWORK,FIRST_CYCLE,THREE_LAYER_STATUS):
        script=(root/relative).resolve(); result=subprocess.run([sys.executable,str(script),"--help"],cwd=tmp_path,env=env,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
        assert result.returncode==0,f"{relative}: {result.stderr}"; assert "ModuleNotFoundError" not in result.stderr; assert "usage:" in result.stdout.lower()
