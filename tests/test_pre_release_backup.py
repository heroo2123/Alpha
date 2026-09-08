from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from polymarket_scanner.models import Signal
from polymarket_scanner.store import Store

ROOT = Path(__file__).resolve().parents[1]


def _seed_database(path: Path) -> None:
    store = Store(str(path))
    signal = Signal(
        detector="backup_test",
        confidence="WATCH",
        event_id="e1",
        market_id="m1",
        title="backup test",
        detail="backup test",
        url="https://example.com",
        edge=None,
        entry_cost=None,
        theoretical_payout=None,
        token_ids=["t1"],
        metadata={"fingerprint_key": "pre-release-backup"},
    )
    assert store.save_signal(signal) is not None


def test_pre_release_backup_helper_creates_verified_restorable_snapshot(tmp_path: Path):
    app_dir = tmp_path / "app"
    data_dir = tmp_path / "config" / "data"
    backup_dir = tmp_path / "config" / "backups"
    python_link = app_dir / ".venv" / "bin" / "python"
    python_link.parent.mkdir(parents=True)
    os.symlink(sys.executable, python_link)

    db = data_dir / "signals.db"
    data_dir.mkdir(parents=True)
    _seed_database(db)

    result = subprocess.run(
        [
            "bash",
            str(ROOT / "deploy/pre-release-backup.sh"),
            str(app_dir),
            str(data_dir),
            str(backup_dir),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert '"restore_verified": true' in result.stdout
    assert "Pre-release backup verified and restorable" in result.stdout
    backups = sorted(backup_dir.glob("signals-*.sqlite3"))
    assert len(backups) == 1
    assert backups[0].with_suffix(backups[0].suffix + ".json").is_file()


def test_pre_release_backup_helper_cleanly_skips_brand_new_database(tmp_path: Path):
    result = subprocess.run(
        [
            "bash",
            str(ROOT / "deploy/pre-release-backup.sh"),
            str(tmp_path / "app"),
            str(tmp_path / "data"),
            str(tmp_path / "backups"),
        ],
        cwd=ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "pre-release backup not required" in result.stdout


def test_oracle_update_backs_up_before_immutable_release_pin():
    source = (ROOT / "deploy/oracle/update.sh").read_text(encoding="utf-8")
    backup_call = source.index('deploy/pre-release-backup.sh')
    release_pin_call = source.index('deploy/release-pin.sh', backup_call)
    assert backup_call < release_pin_call
    assert "Existing database found but current release lacks pre-release backup authority" in source


def test_all_production_install_paths_enable_verified_daily_backup_restore_timer():
    for relative in (
        "deploy/oracle/install.sh",
        "deploy/oracle/update.sh",
        "deploy/gcp/install.sh",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "deploy/setup-db-backup-service.sh" in source
