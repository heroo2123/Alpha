from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from polymarket_scanner.weather_calibration_backup import (
    WeatherCalibrationBackupError,
    create_weather_calibration_backup,
    verify_weather_calibration_backup,
)
from polymarket_scanner.weather_calibration_policy import WEATHER_GEFS_CALIBRATION_POLICY_ID


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "weather-calibration.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
        db.executemany("INSERT INTO evidence(value) VALUES (?)", [("alpha",), ("beta",), ("gamma",)])
        db.commit()
    path.chmod(0o600)
    return path


def _backup_dir(tmp_path: Path) -> Path:
    path = tmp_path / "backups"
    path.mkdir(mode=0o700)
    path.chmod(0o700)
    return path


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def test_backup_is_consistent_self_digest_bound_verifiable_and_never_mutates_source(tmp_path):
    source = _source(tmp_path)
    backups = _backup_dir(tmp_path)
    with sqlite3.connect(source) as db:
        before_rows = db.execute("SELECT id, value FROM evidence ORDER BY id").fetchall()

    report = create_weather_calibration_backup(
        source.resolve(),
        backups.resolve(),
        created_at=1_799_700_000.0,
    )
    backup = Path(report["backup_path"])
    manifest_path = Path(report["manifest_path"])
    assert backup.is_file() and manifest_path.is_file()
    assert backup.stat().st_mode & 0o777 == 0o600
    assert manifest_path.stat().st_mode & 0o777 == 0o600
    assert report["backup_sha256"] == _sha(backup)
    assert report["backup_bytes"] == backup.stat().st_size
    assert len(report["manifest_sha256"]) == 64
    assert report["sqlite_integrity"] == "ok"
    assert report["source_opened_read_only"] is True
    assert report["online_sqlite_backup"] is True
    assert report["automatic_rotation"] is False
    assert report["automatic_deletion"] is False
    assert report["network_upload"] is False
    assert report["statistical_policy_id"] == WEATHER_GEFS_CALIBRATION_POLICY_ID
    assert len(report["experiment_manifest_sha256"]) == 64
    assert len(report["statistical_policy_sha256"]) == 64
    assert report["calibrated_probability_authority"] is False
    assert report["financial_authority"] is False

    with sqlite3.connect(backup) as copied:
        assert copied.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert copied.execute("SELECT id, value FROM evidence ORDER BY id").fetchall() == before_rows
    with sqlite3.connect(source) as original:
        assert original.execute("SELECT id, value FROM evidence ORDER BY id").fetchall() == before_rows

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["backup_sha256"] == report["backup_sha256"]
    assert manifest["manifest_sha256"] == report["manifest_sha256"]
    assert manifest["backup_filename"] == backup.name
    assert "backup_path" not in manifest and "manifest_path" not in manifest
    assert manifest["financial_delivery"] is False
    assert manifest["automatic_order_placement"] is False

    verified = verify_weather_calibration_backup(manifest_path.resolve())
    assert verified["verified"] is True
    assert verified["manifest_sha256"] == report["manifest_sha256"]
    assert verified["backup_sha256"] == report["backup_sha256"]
    assert verified["sqlite_integrity"] == "ok"
    assert verified["network_requests"] is False
    assert verified["database_mutation"] is False
    assert verified["calibrated_probability_authority"] is False
    assert verified["financial_authority"] is False


def test_backup_refuses_insecure_source_permissions(tmp_path):
    source = _source(tmp_path)
    source.chmod(0o644)
    backups = _backup_dir(tmp_path)
    with pytest.raises(WeatherCalibrationBackupError) as raised:
        create_weather_calibration_backup(source.resolve(), backups.resolve(), created_at=1.0)
    assert raised.value.code == "BACKUP_SOURCE_INVALID_PERMISSIONS_TOO_BROAD"


def test_backup_refuses_insecure_backup_directory_permissions(tmp_path):
    source = _source(tmp_path)
    backups = _backup_dir(tmp_path)
    backups.chmod(0o755)
    with pytest.raises(WeatherCalibrationBackupError) as raised:
        create_weather_calibration_backup(source.resolve(), backups.resolve(), created_at=1.0)
    assert raised.value.code == "BACKUP_DIRECTORY_PERMISSIONS_TOO_BROAD"


def test_backup_refuses_symlink_source(tmp_path):
    source = _source(tmp_path)
    link = tmp_path / "linked.sqlite"
    link.symlink_to(source)
    backups = _backup_dir(tmp_path)
    with pytest.raises(WeatherCalibrationBackupError) as raised:
        create_weather_calibration_backup(link.absolute(), backups.resolve(), created_at=1.0)
    assert raised.value.code == "BACKUP_SOURCE_INVALID"


def test_backup_never_overwrites_identical_existing_artifact(tmp_path):
    source = _source(tmp_path)
    backups = _backup_dir(tmp_path)
    first = create_weather_calibration_backup(source.resolve(), backups.resolve(), created_at=1_799_700_000.0)
    first_bytes = Path(first["backup_path"]).read_bytes()
    with pytest.raises(WeatherCalibrationBackupError) as raised:
        create_weather_calibration_backup(source.resolve(), backups.resolve(), created_at=1_799_700_000.0)
    assert raised.value.code == "BACKUP_ARTIFACT_ALREADY_EXISTS"
    assert Path(first["backup_path"]).read_bytes() == first_bytes


def test_verifier_rejects_tampered_manifest_even_when_backup_bytes_are_untouched(tmp_path):
    source = _source(tmp_path)
    report = create_weather_calibration_backup(source.resolve(), _backup_dir(tmp_path).resolve(), created_at=1_799_700_000.0)
    manifest_path = Path(report["manifest_path"])
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["financial_authority"] = True
    manifest_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    manifest_path.chmod(0o600)
    with pytest.raises(WeatherCalibrationBackupError) as raised:
        verify_weather_calibration_backup(manifest_path.resolve())
    assert raised.value.code == "BACKUP_MANIFEST_DIGEST_MISMATCH"


def test_verifier_rejects_tampered_backup_bytes(tmp_path):
    source = _source(tmp_path)
    report = create_weather_calibration_backup(source.resolve(), _backup_dir(tmp_path).resolve(), created_at=1_799_700_000.0)
    backup = Path(report["backup_path"])
    with backup.open("ab") as handle:
        handle.write(b"tamper")
    backup.chmod(0o600)
    with pytest.raises(WeatherCalibrationBackupError) as raised:
        verify_weather_calibration_backup(Path(report["manifest_path"]).resolve())
    assert raised.value.code == "BACKUP_FILE_DIGEST_MISMATCH"
