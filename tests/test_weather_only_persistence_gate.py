from __future__ import annotations

from pathlib import Path


PERSIST = Path("deploy/enable-weather-paper-persistence.sh")
BACKUP_SETUP = Path("deploy/setup-weather-paper-backup-service.sh")


def _exec_lines(path: Path) -> list[str]:
    return [
        line.strip() for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_persistence_requires_active_attested_fresh_exact_candidate_before_enable():
    text = PERSIST.read_text(encoding="utf-8")
    enable_index = text.index('sudo systemctl enable "${UNIT}"')
    assert text.index("attest-weather-paper-runtime.py") < enable_index
    assert text.index("verify-weather-paper-first-cycle.py") < enable_index
    assert text.index('sudo systemctl start "${BACKUP_UNIT}"') < enable_index
    assert "--require-active" in text
    assert "weather-paper-release.sha" in text
    assert "ALPHA_WEATHER_APP_DIR" in text


def test_persistence_never_restarts_or_operates_legacy_scanner():
    lines = _exec_lines(PERSIST)
    assert not any("systemctl restart" in line for line in lines)
    for line in lines:
        if "systemctl" in line:
            assert "polymarket-edge-scanner" not in line
            assert "polymarket-edge-command" not in line


def test_backup_timer_is_verified_restorable_paper_backup_and_not_auto_enabled_on_install():
    text = BACKUP_SETUP.read_text(encoding="utf-8")
    assert "pre-release-weather-paper-backup.sh" in text
    assert "WEATHER_PAPER_BACKUP_DIR" in text
    assert "/var/lib/polymarket-weather-paper/backups" in text or '${STATE_DIR}/backups' in text
    assert "Persistent=true" in text
    assert "OnCalendar=daily" in text
    lines = _exec_lines(BACKUP_SETUP)
    assert not any("systemctl enable" in line for line in lines)
    assert not any("systemctl start" in line for line in lines)


def test_backup_service_has_no_secret_environment_file():
    text = BACKUP_SETUP.read_text(encoding="utf-8")
    assert "EnvironmentFile=" not in text
    assert "TELEGRAM_BOT_TOKEN" not in text
    assert "PRIVATE_KEY" not in text
    assert "WALLET" not in text
