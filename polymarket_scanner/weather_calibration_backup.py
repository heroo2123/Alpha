from __future__ import annotations

"""Consistent local backup primitive for prospective weather calibration evidence.

Prospective captures and exact settlement snapshots cannot be recreated after the
fact without destroying their prospective status. This helper therefore uses
SQLite's online backup API from a read-only source connection, verifies the copied
database, hashes the exact backup bytes, and emits a companion manifest bound to the
frozen experiment/policy identity.

It never rotates or deletes older backups, never uploads data, never starts services,
and grants no calibration-probability or financial authority. An incomplete/orphaned
file without a matching manifest is not a certified backup artifact.
"""

import argparse
import hashlib
import json
import math
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .weather_calibration_experiment import (
    build_weather_calibration_experiment_manifest,
    validate_weather_calibration_experiment_manifest,
)


WEATHER_CALIBRATION_BACKUP_VERSION = "weather_calibration_backup_v1_sqlite_online_digest_manifest"


class WeatherCalibrationBackupError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _timestamp(value: object) -> float:
    if value is None:
        return time.time()
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherCalibrationBackupError("BACKUP_TIMESTAMP_INVALID")
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherCalibrationBackupError("BACKUP_TIMESTAMP_INVALID")
    return number


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _exclusive_file(path: Path) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)


def _publish_without_overwrite(temporary: Path, final: Path) -> None:
    if final.exists() or final.is_symlink():
        raise WeatherCalibrationBackupError("BACKUP_ARTIFACT_ALREADY_EXISTS")
    try:
        os.link(temporary, final)
    except FileExistsError:
        raise WeatherCalibrationBackupError("BACKUP_ARTIFACT_ALREADY_EXISTS") from None
    finally:
        if temporary.exists():
            temporary.unlink()


def create_weather_calibration_backup(
    source_db: str | Path,
    backup_dir: str | Path,
    *,
    created_at: float | None = None,
) -> dict:
    source = Path(source_db)
    destination = Path(backup_dir)
    captured = _timestamp(created_at)
    if not source.is_absolute() or not source.is_file() or source.is_symlink():
        raise WeatherCalibrationBackupError("BACKUP_SOURCE_INVALID")
    if not destination.is_absolute() or not destination.is_dir() or destination.is_symlink():
        raise WeatherCalibrationBackupError("BACKUP_DIRECTORY_INVALID")
    if source.resolve().parent == destination.resolve() and source.name.startswith("weather-calibration-backup-"):
        raise WeatherCalibrationBackupError("BACKUP_SOURCE_APPEARS_TO_BE_BACKUP")

    source_mode = source.stat().st_mode & 0o777
    if source_mode & 0o077:
        raise WeatherCalibrationBackupError("BACKUP_SOURCE_PERMISSIONS_TOO_BROAD")

    manifest = validate_weather_calibration_experiment_manifest(
        build_weather_calibration_experiment_manifest()
    )
    stamp = datetime.fromtimestamp(captured, tz=timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    temporary_db = destination / f".weather-calibration-backup-{stamp}-{os.getpid()}.sqlite.tmp"
    if temporary_db.exists() or temporary_db.is_symlink():
        raise WeatherCalibrationBackupError("BACKUP_TEMPORARY_COLLISION")
    _exclusive_file(temporary_db)

    try:
        source_uri = f"file:{source.resolve().as_posix()}?mode=ro"
        try:
            with sqlite3.connect(source_uri, uri=True, timeout=5.0) as source_conn:
                source_conn.execute("PRAGMA query_only=ON")
                source_integrity = source_conn.execute("PRAGMA integrity_check").fetchone()
                if source_integrity is None or str(source_integrity[0]).lower() != "ok":
                    raise WeatherCalibrationBackupError("BACKUP_SOURCE_INTEGRITY_FAILED")
                with sqlite3.connect(temporary_db, timeout=5.0) as backup_conn:
                    source_conn.backup(backup_conn)
                    backup_conn.commit()
                    copied_integrity = backup_conn.execute("PRAGMA integrity_check").fetchone()
                    if copied_integrity is None or str(copied_integrity[0]).lower() != "ok":
                        raise WeatherCalibrationBackupError("BACKUP_COPY_INTEGRITY_FAILED")
        except WeatherCalibrationBackupError:
            raise
        except sqlite3.Error:
            raise WeatherCalibrationBackupError("BACKUP_SQLITE_ERROR") from None

        os.chmod(temporary_db, 0o600)
        with temporary_db.open("rb") as handle:
            os.fsync(handle.fileno())
        backup_sha = _sha256_file(temporary_db)
        backup_bytes = temporary_db.stat().st_size
        final_db = destination / f"weather-calibration-backup-{stamp}-{backup_sha[:16]}.sqlite"
        final_manifest = Path(str(final_db) + ".manifest.json")
        _publish_without_overwrite(temporary_db, final_db)
        os.chmod(final_db, 0o600)

        payload = {
            "version": WEATHER_CALIBRATION_BACKUP_VERSION,
            "created_at": captured,
            "source_filename": source.name,
            "backup_filename": final_db.name,
            "backup_sha256": backup_sha,
            "backup_bytes": backup_bytes,
            "sqlite_integrity": "ok",
            "experiment_manifest_sha256": manifest.manifest_sha256,
            "capture_policy_id": manifest.capture_policy_id,
            "statistical_policy_id": manifest.statistical_policy_id,
            "statistical_policy_sha256": manifest.statistical_policy_sha256,
            "source_opened_read_only": True,
            "online_sqlite_backup": True,
            "automatic_rotation": False,
            "automatic_deletion": False,
            "network_upload": False,
            "calibrated_probability_authority": False,
            "financial_authority": False,
            "financial_delivery": False,
            "automatic_order_placement": False,
        }
        manifest_text = _canonical_json(payload) + "\n"
        temporary_manifest = destination / f".{final_manifest.name}.{os.getpid()}.tmp"
        _exclusive_file(temporary_manifest)
        try:
            with temporary_manifest.open("w", encoding="utf-8") as handle:
                handle.write(manifest_text)
                handle.flush()
                os.fsync(handle.fileno())
            _publish_without_overwrite(temporary_manifest, final_manifest)
            os.chmod(final_manifest, 0o600)
        finally:
            if temporary_manifest.exists():
                temporary_manifest.unlink()
        return {**payload, "backup_path": str(final_db), "manifest_path": str(final_manifest)}
    finally:
        if temporary_db.exists():
            temporary_db.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--backup-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = create_weather_calibration_backup(args.db, args.backup_dir)
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
