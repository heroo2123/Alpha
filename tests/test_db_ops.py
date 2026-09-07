from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from polymarket_scanner.db_ops import (
    backup_database,
    configure_database_runtime,
    database_health,
    verify_database,
    verify_restore,
)
from polymarket_scanner.models import Signal
from polymarket_scanner.store import Store


def _seed(path: Path) -> Store:
    store = Store(str(path))
    signal = Signal(
        detector="test",
        confidence="WATCH",
        event_id="e1",
        market_id="m1",
        title="test",
        detail="test",
        url="https://example.com",
        edge=None,
        entry_cost=None,
        theoretical_payout=None,
        token_ids=["t1"],
        metadata={"fingerprint_key": "seed"},
    )
    assert store.save_signal(signal) is not None
    store.set_state("hello", "world")
    return store


def test_configure_database_runtime_enables_wal_and_health(tmp_path):
    db = tmp_path / "signals.db"
    _seed(db)

    configured = configure_database_runtime(db)
    assert configured["journal_mode"] == "wal"
    assert configured["quick_check"] == "ok"
    assert configured["busy_timeout_ms"] == 5000

    health = database_health(db)
    assert health["exists"] is True
    assert health["journal_mode"] == "wal"
    assert health["quick_check"] == "ok"
    assert health["core_tables_missing"] == []
    assert health["database_bytes"] > 0


def test_online_backup_is_verified_and_restorable(tmp_path):
    db = tmp_path / "signals.db"
    store = _seed(db)
    configure_database_runtime(db)
    backup_dir = tmp_path / "backups"
    now = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)

    result = backup_database(db, backup_dir, now=now)
    backup = Path(result["backup"])
    manifest = backup.with_suffix(backup.suffix + ".json")

    assert backup.is_file()
    assert manifest.is_file()
    assert result["restore_verified"] is True
    assert result["core_counts"]["signals"] == 1
    assert result["core_counts"]["bot_state"] == 1
    assert len(result["sha256"]) == 64
    assert json.loads(manifest.read_text())["sha256"] == result["sha256"]
    assert (backup.stat().st_mode & 0o777) == 0o600

    verified = verify_database(backup)
    assert verified["core_counts"]["signals"] == 1
    assert verify_restore(backup)["restore_verified"] is True

    # Mutating the live database after the snapshot cannot change the backup.
    store.set_state("after", "backup")
    assert verify_database(backup)["core_counts"]["bot_state"] == 1


def test_backup_retention_deletes_old_snapshot_and_manifest(tmp_path):
    db = tmp_path / "signals.db"
    _seed(db)
    backup_dir = tmp_path / "backups"
    old = backup_dir / "signals-20260801T000000Z.sqlite3"
    backup_dir.mkdir()

    # Make a valid old backup and matching manifest, then age both files.
    with sqlite3.connect(db) as source, sqlite3.connect(old) as dest:
        source.backup(dest)
    old_manifest = old.with_suffix(old.suffix + ".json")
    old_manifest.write_text("{}\n")
    old_epoch = (datetime(2026, 8, 1, tzinfo=timezone.utc)).timestamp()
    os.utime(old, (old_epoch, old_epoch))
    os.utime(old_manifest, (old_epoch, old_epoch))

    result = backup_database(
        db,
        backup_dir,
        retention_days=14,
        now=datetime(2026, 9, 7, tzinfo=timezone.utc),
    )
    assert old.name in result["old_backups_deleted"]
    assert not old.exists()
    assert not old_manifest.exists()


def test_corrupt_backup_fails_verification(tmp_path):
    bad = tmp_path / "corrupt.sqlite3"
    bad.write_bytes(b"not a sqlite database")
    with pytest.raises((sqlite3.DatabaseError, RuntimeError)):
        verify_database(bad)


def test_missing_core_tables_fail_backup_verification(tmp_path):
    incomplete = tmp_path / "incomplete.sqlite3"
    with sqlite3.connect(incomplete) as connection:
        connection.execute("CREATE TABLE random_table(x INTEGER)")
    with pytest.raises(RuntimeError, match="missing core tables"):
        verify_database(incomplete)
