from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from polymarket_scanner.weather_only_paper_backup import (
    WeatherPaperBackupError,
    backup_weather_paper_database,
    verify_weather_paper_database,
    verify_weather_paper_restore,
)
from polymarket_scanner.weather_only_paper_corrective import PAPER_EXECUTION_PROTOCOL_V4
from polymarket_scanner.weather_only_paper_facade import CorrectiveWeatherPaperStore


RELEASE = "a" * 40


def _paper_db(path):
    store = CorrectiveWeatherPaperStore(path)
    signal_id = store.save_signal(
        fingerprint="backup-fixture",
        lane="weather_forecast_raw_gap",
        evidence_class="RESEARCH_ONLY_UNCALIBRATED_V4",
        event_id="event-backup",
        market_id="market-backup",
        side="YES",
        token_id="token-backup",
        model_probability=0.6,
        entry_cost=0.4,
        raw_gap=0.2,
        theoretical_payout=1.0,
        created_at=1000.0,
        payload={
            "event_title": "Backup fixture",
            "station": "KLGA",
            "target_date": "2026-09-20",
            "ask_size": 5.0,
            "paper_execution_protocol_version": PAPER_EXECUTION_PROTOCOL_V4,
        },
    )
    assert signal_id is not None
    store.mark_telegram_sent(signal_id, 1234, sent_at=1001.0)
    store.set_state("fixture_cursor", "17")
    store.record_decision(
        decision_id="decision-backup",
        event_id="event-backup",
        market_id="market-backup",
        side="YES",
        outcome="SKIPPED",
        reason="fixture",
    )
    return store


def test_weather_paper_backup_uses_its_own_schema_and_restore_profile(tmp_path):
    db_path = tmp_path / "weather-paper.sqlite"
    _paper_db(db_path)
    profile = verify_weather_paper_database(db_path)
    assert profile["quick_check"] == "ok"
    assert profile["logical_tables"]["weather_paper_signals"]["row_count"] == 1
    assert profile["logical_tables"]["weather_paper_state"]["row_count"] >= 1
    assert len(profile["schema_sha256"]) == 64

    manifest = backup_weather_paper_database(
        db_path,
        tmp_path / "backups",
        release_sha=RELEASE,
        retention_days=14,
        now=datetime(2026, 9, 13, 18, 0, tzinfo=timezone.utc),
    )
    assert manifest["restore_verified"] is True
    assert manifest["release_sha"] == RELEASE
    assert manifest["source_logical_tables"] == manifest["logical_tables"]
    backup_path = tmp_path / "backups" / "weather-paper-20260913T180000Z.sqlite3"
    manifest_path = backup_path.with_suffix(backup_path.suffix + ".json")
    assert backup_path.exists()
    assert manifest_path.exists()
    assert backup_path.stat().st_mode & 0o077 == 0
    assert manifest_path.stat().st_mode & 0o077 == 0
    on_disk = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert on_disk["backup_file_sha256"] == manifest["backup_file_sha256"]
    restored = verify_weather_paper_restore(backup_path)
    assert restored["restore_verified"] is True
    assert restored["logical_tables"] == profile["logical_tables"]


def test_missing_paper_table_is_not_mistaken_for_valid_legacy_or_partial_backup(tmp_path):
    import sqlite3

    path = tmp_path / "partial.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE weather_paper_signals(id INTEGER PRIMARY KEY)")
    with pytest.raises(WeatherPaperBackupError) as raised:
        verify_weather_paper_database(path)
    assert raised.value.code == "PAPER_BACKUP_REQUIRED_TABLES_MISSING"


def test_restore_verifier_detects_corrupted_backup_bytes(tmp_path):
    db_path = tmp_path / "weather-paper.sqlite"
    _paper_db(db_path)
    manifest = backup_weather_paper_database(
        db_path,
        tmp_path / "backups",
        release_sha=RELEASE,
        now=datetime(2026, 9, 13, 19, 0, tzinfo=timezone.utc),
    )
    backup_path = tmp_path / "backups" / "weather-paper-20260913T190000Z.sqlite3"
    data = bytearray(backup_path.read_bytes())
    data[:16] = b"BROKEN-SQLITE-DB!"
    backup_path.write_bytes(bytes(data))
    with pytest.raises((WeatherPaperBackupError, Exception)):
        verify_weather_paper_restore(backup_path)
    assert manifest["restore_verified"] is True


def test_release_sha_and_retention_policy_are_explicit(tmp_path):
    db_path = tmp_path / "weather-paper.sqlite"
    _paper_db(db_path)
    with pytest.raises(WeatherPaperBackupError) as release:
        backup_weather_paper_database(
            db_path, tmp_path / "backups", release_sha="not-a-release"
        )
    assert release.value.code == "PAPER_BACKUP_RELEASE_SHA_INVALID"
    with pytest.raises(WeatherPaperBackupError) as retention:
        backup_weather_paper_database(
            db_path, tmp_path / "backups", release_sha=RELEASE, retention_days=0
        )
    assert retention.value.code == "PAPER_BACKUP_RETENTION_INVALID"
