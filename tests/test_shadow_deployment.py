import importlib.util
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def rendered():
    spec = importlib.util.spec_from_file_location("render_units", ROOT / "deploy/render-shadow-units.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.render(Path("/srv/alpha"), Path("/srv/alpha-state"), "alpha")


def test_rendered_services_are_separate_bounded_and_attested():
    units = rendered()
    builder = units["polymarket-universe-builder.service"]
    scanner = units["polymarket-edge-scanner.service"]
    command = units["polymarket-edge-command.service"]
    assert "universe_builder --ipv6" in builder
    assert "bot.env" not in builder
    assert "ReadWritePaths=/srv/alpha-state/universe" in builder
    assert "Nice=10" in builder and "MemoryMax=160M" in builder
    assert "app_trade_only:app" in scanner and "--workers 1" in scanner
    assert "command_worker_trade_only.py" in command
    for text in (builder, scanner, command):
        assert "verify-runtime-release.sh" in text
        assert "polymarket_scanner.shadow_preflight" in text
        assert "MemorySwapMax=0" in text
        assert "StartLimitBurst=3" in text
        assert "Requires=polymarket-universe-builder" not in text
    assert "MemoryMax=640M" in units["polymarket-shadow.slice"]


def test_retired_installers_make_no_changes_and_fail_with_migration_instruction():
    for relative in ("deploy/gcp/install.sh", "deploy/oracle/install.sh", "deploy/oracle/update.sh", "deploy/oracle/setup-command-service.sh"):
        result = subprocess.run(["bash", str(ROOT / relative)], capture_output=True, text=True)
        assert result.returncode == 2
        assert "retired" in result.stderr.lower() or "prepare-shadow" in result.stderr


def test_release_preparation_gates_and_never_starts_services():
    text = (ROOT / "deploy/prepare-shadow-release.sh").read_text()
    assert text.index("deploy/pre-release-backup.sh") < text.index("deploy/release-pin.sh")
    assert "--required-only" in text and "--release-sha" in text and "--output" in text
    assert "require_database_schema" in text
    assert 'chmod 600 "${PREFLIGHT_FILE}"' in text
    for path in ("deploy/prepare-shadow-release.sh", "deploy/setup-shadow-services.sh"):
        body = (ROOT / path).read_text()
        assert "systemctl start " not in body
        assert "systemctl restart " not in body
        assert "systemctl enable " not in body


def test_backup_helpers_share_a_crash_released_lock():
    for path in ("deploy/pre-release-backup.sh", "deploy/run-db-backup.sh"):
        text = (ROOT / path).read_text()
        assert '.backup.lockfile' in text
        assert 'flock -n 9' in text
