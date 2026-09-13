from __future__ import annotations

from pathlib import Path


START = Path("deploy/start-weather-paper-candidate.sh")
PREFLIGHT = Path("deploy/preflight-weather-paper-deployment.sh")
SETUP = Path("deploy/setup-weather-paper-service.sh")


def test_start_gate_requires_exact_sha_and_active_runtime_attestation():
    text = START.read_text(encoding="utf-8")
    assert "EXPECTED_SHA=\"${1:-}\"" in text
    assert "checkout is not the explicitly approved candidate" in text
    assert "release marker is not the explicitly approved candidate" in text
    assert "preflight-weather-paper-deployment.sh" in text
    assert "--require-active" in text
    assert "weather-paper-active-attestation.json" in text


def test_failed_start_acceptance_stops_service_and_never_enables_it():
    text = START.read_text(encoding="utf-8")
    assert 'sudo systemctl stop "${UNIT}"' in text
    assert 'sudo systemctl start "${UNIT}"' in text
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
