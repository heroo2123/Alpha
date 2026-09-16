from __future__ import annotations

from pathlib import Path


PERSIST = Path("deploy/enable-weather-paper-persistence.sh")
START = Path("deploy/start-weather-paper-candidate.sh")
BACKUP_SETUP = Path("deploy/setup-weather-paper-backup-service.sh")
SERVICE_ISOLATION = Path("deploy/check-weather-paper-service-isolation.sh")


def _exec_lines(path: Path) -> list[str]:
    return [
        line.strip() for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_persistence_requires_active_attested_fresh_exact_candidate_before_enable():
    text = PERSIST.read_text(encoding="utf-8")
    enable_index = text.index('sudo systemctl enable "${UNIT}"')
    assert text.index("check-weather-paper-service-isolation.sh") < enable_index
    assert text.index("attest-weather-paper-runtime.py") < enable_index
    assert text.index("verify-weather-paper-first-cycle.py") < enable_index
    assert text.index("verify-three-layer-validation-status.py") < enable_index
    assert text.index("verify-three-layer-fresh-capture.py") < enable_index
    assert text.index('sudo systemctl start "${BACKUP_UNIT}"') < enable_index
    assert "--require-disabled" in text
    assert "--require-active" in text
    assert "weather-paper-release.sha" in text
    assert "weather-paper-three-layer-start.epoch" in text
    assert "ALPHA_WEATHER_APP_DIR" in text


def test_start_gate_persists_exact_start_boundary_and_rolls_it_back_on_failure():
    text = START.read_text(encoding="utf-8")
    assert 'START_EPOCH_FILE="${CONFIG_DIR}/weather-paper-three-layer-start.epoch"' in text
    assert "START_ACCEPTANCE_EPOCH" in text
    assert 'mv -f "${TMP_START}" "${START_EPOCH_FILE}"' in text
    assert 'rm -f "${START_EPOCH_FILE}"' in text
    assert "Persistence still requires a fresh saved three-layer capture" in text


def test_persistence_fresh_capture_gate_is_bound_to_candidate_start_boundary_and_durable_db():
    text = PERSIST.read_text(encoding="utf-8")
    fresh_index = text.index("verify-three-layer-fresh-capture.py")
    enable_index = text.index('sudo systemctl enable "${UNIT}"')
    assert fresh_index < enable_index
    assert '--not-before "${START_ACCEPTANCE_EPOCH}"' in text
    assert '--db "${DB_PATH}"' in text
    assert "weather-paper-persistence-fresh-capture.json" in text
    assert "durable post-start WRH+NWS+GEFS research capture" in text


def test_persistence_rollback_is_armed_before_first_enable_operation():
    text = PERSIST.read_text(encoding="utf-8")
    arm_index = text.index("persistence_attempted=1")
    enable_index = text.index('sudo systemctl enable "${UNIT}"')
    assert arm_index < enable_index
    assert "(( persistence_attempted == 1 ))" in text
    assert 'sudo systemctl disable --now "${BACKUP_TIMER}"' in text
    assert 'sudo systemctl disable "${UNIT}"' in text


def test_persistence_never_restarts_or_operates_legacy_scanner():
    lines = _exec_lines(PERSIST)
    assert not any("systemctl restart" in line for line in lines)
    for line in lines:
        if "systemctl" in line:
            assert "polymarket-edge-scanner" not in line
            assert "polymarket-edge-command" not in line


def test_service_isolation_guard_is_read_only_and_covers_superseded_stack():
    text = SERVICE_ISOLATION.read_text(encoding="utf-8")
    for unit in (
        "polymarket-edge-scanner.service",
        "polymarket-edge-command.service",
        "polymarket-universe-builder.service",
        "polymarket-weather-shadow.service",
        "polymarket-weather-calibration.service",
    ):
        assert unit in text
    assert "systemctl is-active" in text
    assert "systemctl is-enabled" in text
    assert "--require-disabled" in text
    lines = _exec_lines(SERVICE_ISOLATION)
    forbidden = (
        "systemctl start ",
        "systemctl stop ",
        "systemctl restart ",
        "systemctl enable ",
        "systemctl disable ",
    )
    assert not any(any(command in line for command in forbidden) for line in lines)


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
