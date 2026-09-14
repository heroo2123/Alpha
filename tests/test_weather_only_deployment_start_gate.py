from __future__ import annotations

import importlib.util
from pathlib import Path


START = Path("deploy/start-weather-paper-candidate.sh")
PREFLIGHT = Path("deploy/preflight-weather-paper-deployment.sh")
SETUP = Path("deploy/setup-weather-paper-service.sh")
FIRST_CYCLE = Path("deploy/verify-weather-paper-first-cycle.py")
SHA = "a" * 40


def _first_cycle_module():
    spec = importlib.util.spec_from_file_location("weather_first_cycle_verifier", FIRST_CYCLE)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_start_gate_requires_exact_sha_runtime_attestation_and_fresh_first_cycle():
    text = START.read_text(encoding="utf-8")
    assert "EXPECTED_SHA=\"${1:-}\"" in text
    assert "checkout is not the explicitly approved candidate" in text
    assert "release marker is not the explicitly approved candidate" in text
    assert "preflight-weather-paper-deployment.sh" in text
    assert "--require-active" in text
    assert "weather-paper-active-attestation.json" in text
    assert "verify-weather-paper-first-cycle.py" in text
    assert 'START_ACCEPTANCE_EPOCH="$(date +%s)"' in text
    assert '--not-before "${START_ACCEPTANCE_EPOCH}"' in text
    assert "weather-paper-first-cycle-acceptance.json" in text


def test_failed_start_acceptance_stops_service_and_never_enables_it():
    text = START.read_text(encoding="utf-8")
    assert 'sudo systemctl stop "${UNIT}"' in text
    assert 'sudo systemctl start "${UNIT}"' in text
    # The rollback guard is armed before systemctl is called, so even a non-zero
    # start command that partially launches the service is contained.
    assert text.index("start_attempted=1") < text.index('sudo systemctl start "${UNIT}"')
    assert "(( start_attempted == 1 ))" in text
    # Start acceptance intentionally never grants boot persistence.
    executable_lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert not any("systemctl enable" in line for line in executable_lines)
    assert not any("systemctl restart" in line for line in executable_lines)


def test_preflight_backups_paper_ledger_before_installing_unit():
    text = PREFLIGHT.read_text(encoding="utf-8")
    backup_index = text.index("pre-release-weather-paper-backup.sh")
    install_index = text.index("setup-weather-paper-service.sh")
    assert backup_index < install_index
    assert "attest-weather-paper-runtime.py" in text


def test_weather_service_environment_is_telegram_only():
    text = SETUP.read_text(encoding="utf-8")
    assert "TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID" in text
    assert "grep -E" in text
    for forbidden in (
        "PRIVATE_KEY",
        "WALLET",
        "POLYMARKET_API_KEY",
        "POLYMARKET_SECRET",
        "BINANCE_API_KEY",
    ):
        assert forbidden not in text


def test_first_cycle_verifier_waits_past_old_release_snapshot_from_before_start():
    verifier = _first_cycle_module()
    status = {
        "release_sha": "b" * 40,
        "finished_at": 1_000.0,
        "final_paper_runtime_version": None,
    }
    assert verifier._wait_reason_before_strict_acceptance(
        status,
        expected_release_sha=SHA,
        not_before=1_001.0,
    ) == "DEPLOY_STATUS_PREDATES_START"


def test_first_cycle_verifier_waits_for_fresh_v4_intermediate_snapshot():
    verifier = _first_cycle_module()
    status = {
        "release_sha": SHA,
        "finished_at": 1_002.0,
        "version": "weather_live_paper_v4_example",
    }
    assert verifier._wait_reason_before_strict_acceptance(
        status,
        expected_release_sha=SHA,
        not_before=1_001.0,
    ) == verifier.INTERMEDIATE_WAIT_CODE


def test_first_cycle_verifier_waits_for_fresh_corrective_intermediate_snapshot():
    verifier = _first_cycle_module()
    status = {
        "release_sha": SHA,
        "finished_at": 1_002.0,
        "canonical_corrective_version": "weather_live_paper_corrective_example",
    }
    assert verifier._wait_reason_before_strict_acceptance(
        status,
        expected_release_sha=SHA,
        not_before=1_001.0,
    ) == verifier.INTERMEDIATE_WAIT_CODE


def test_first_cycle_verifier_never_waits_past_a_fresh_claimed_final_snapshot():
    verifier = _first_cycle_module()
    status = {
        "release_sha": SHA,
        "finished_at": 1_002.0,
        "canonical_corrective_version": "WRONG",
        "final_paper_runtime_version": "WRONG_FINAL",
    }
    assert verifier._wait_reason_before_strict_acceptance(
        status,
        expected_release_sha=SHA,
        not_before=1_001.0,
    ) is None


def test_first_cycle_verifier_never_waits_past_fresh_wrong_release_snapshot():
    verifier = _first_cycle_module()
    status = {
        "release_sha": "b" * 40,
        "finished_at": 1_002.0,
    }
    assert verifier._wait_reason_before_strict_acceptance(
        status,
        expected_release_sha=SHA,
        not_before=1_001.0,
    ) is None
