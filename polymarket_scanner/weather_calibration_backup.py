from __future__ import annotations

"""Consistent local backup + verification for prospective weather calibration evidence.

Prospective captures and exact settlement snapshots cannot be recreated after the
fact without destroying their prospective status. Creation therefore uses SQLite's
online backup API from a read-only source connection, verifies the copied database,
hashes the exact bytes, and emits a self-digesting companion manifest bound to the
frozen experiment/policy identity.

Verification independently rechecks manifest integrity, backup bytes, SQLite
integrity, permissions, and current experiment/policy lineage. It never rotates or
deletes backups, never uploads data, never starts services, and grants no calibrated-
probability or financial authority. Orphaned/tampered artifacts are never certified.
"""

import argparse
import hashlib
import json
import math
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path

from .weather_calibration_experiment import (
    build_weather_calibration_experiment_manifest,
    validate_weather_calibration_experiment_manifest,
)


WEATHER_CALIBRATION_BACKUP_VERSION = "weather_calibration_backup_v2_sqlite_online_self_digest_manifest"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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


def _sha256_payload(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _exclusive_file(path: Path) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
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
    _fsync_directory(final.parent)


def _experiment_manifest():
    return validate_weather_calibration_experiment_manifest(
        build_weather_calibration_experiment_manifest()
    )


def _safe_private_regular_file(path: Path, code: str) -> None:
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise WeatherCalibrationBackupError(code)
    if path.stat().st_mode & 0o077:
        raise WeatherCalibrationBackupError(f"{code}_PERMISSIONS_TOO_BROAD")


def create_weather_calibration_backup(
    source_db: str | Path,
    backup_dir: str | Path,
    *,
    created_at: float | None = None,
) -> dict:
    source = Path(source_db)
    destination = Path(backup_dir)
    captured = _timestamp(created_at)
    _safe_private_regular_file(source, "BACKUP_SOURCE_INVALID")
    if not destination.is_absolute() or not destination.is_dir() or destination.is_symlink():
        raise WeatherCalibrationBackupError("BACKUP_DIRECTORY_INVALID")
    if destination.stat().st_mode & 0o077:
        raise WeatherCalibrationBackupError("BACKUP_DIRECTORY_PERMISSIONS_TOO_BROAD")
    if source.resolve().parent == destination.resolve() and source.name.startswith("weather-calibration-backup-"):
        raise WeatherCalibrationBackupError("BACKUP_SOURCE_APPEARS_TO_BE_BACKUP")

    manifest = _experiment_manifest()
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

        base_payload = {
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
        payload = {**base_payload, "manifest_sha256": _sha256_payload(base_payload)}
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


def verify_weather_calibration_backup(manifest_path: str | Path) -> dict:
    manifest_file = Path(manifest_path)
    _safe_private_regular_file(manifest_file, "BACKUP_MANIFEST_INVALID")
    try:
        payload = json.loads(manifest_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise WeatherCalibrationBackupError("BACKUP_MANIFEST_JSON_INVALID") from None
    if not isinstance(payload, dict) or payload.get("version") != WEATHER_CALIBRATION_BACKUP_VERSION:
        raise WeatherCalibrationBackupError("BACKUP_MANIFEST_VERSION_INVALID")
    supplied_manifest_sha = str(payload.get("manifest_sha256") or "").lower()
    if not _SHA256_RE.fullmatch(supplied_manifest_sha):
        raise WeatherCalibrationBackupError("BACKUP_MANIFEST_SHA_INVALID")
    base_payload = dict(payload)
    base_payload.pop("manifest_sha256", None)
    if _sha256_payload(base_payload) != supplied_manifest_sha:
        raise WeatherCalibrationBackupError("BACKUP_MANIFEST_DIGEST_MISMATCH")

    backup_name = payload.get("backup_filename")
    if not isinstance(backup_name, str) or not backup_name or Path(backup_name).name != backup_name:
        raise WeatherCalibrationBackupError("BACKUP_FILENAME_INVALID")
    backup_file = manifest_file.parent / backup_name
    _safe_private_regular_file(backup_file, "BACKUP_FILE_INVALID")
    supplied_backup_sha = str(payload.get("backup_sha256") or "").lower()
    if not _SHA256_RE.fullmatch(supplied_backup_sha) or _sha256_file(backup_file) != supplied_backup_sha:
        raise WeatherCalibrationBackupError("BACKUP_FILE_DIGEST_MISMATCH")
    if payload.get("backup_bytes") != backup_file.stat().st_size:
        raise WeatherCalibrationBackupError("BACKUP_FILE_SIZE_MISMATCH")

    uri = f"file:{backup_file.resolve().as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5.0) as db:
            db.execute("PRAGMA query_only=ON")
            integrity = db.execute("PRAGMA integrity_check").fetchone()
    except sqlite3.Error:
        raise WeatherCalibrationBackupError("BACKUP_VERIFY_SQLITE_ERROR") from None
    if integrity is None or str(integrity[0]).lower() != "ok":
        raise WeatherCalibrationBackupError("BACKUP_VERIFY_INTEGRITY_FAILED")

    current = _experiment_manifest()
    if payload.get("experiment_manifest_sha256") != current.manifest_sha256:
        raise WeatherCalibrationBackupError("BACKUP_EXPERIMENT_MANIFEST_MISMATCH")
    if payload.get("capture_policy_id") != current.capture_policy_id:
        raise WeatherCalibrationBackupError("BACKUP_CAPTURE_POLICY_MISMATCH")
    if payload.get("statistical_policy_id") != current.statistical_policy_id:
        raise WeatherCalibrationBackupError("BACKUP_STATISTICAL_POLICY_MISMATCH")
    if payload.get("statistical_policy_sha256") != current.statistical_policy_sha256:
        raise WeatherCalibrationBackupError("BACKUP_STATISTICAL_POLICY_DIGEST_MISMATCH")
    if (
        payload.get("source_opened_read_only") is not True
        or payload.get("online_sqlite_backup") is not True
        or payload.get("automatic_rotation") is not False
        or payload.get("automatic_deletion") is not False
        or payload.get("network_upload") is not False
        or payload.get("calibrated_probability_authority") is not False
        or payload.get("financial_authority") is not False
        or payload.get("financial_delivery") is not False
        or payload.get("automatic_order_placement") is not False
    ):
        raise WeatherCalibrationBackupError("BACKUP_AUTHORITY_OR_OPERATION_BOUNDARY_INVALID")

    return {
        "version": WEATHER_CALIBRATION_BACKUP_VERSION,
        "verified": True,
        "manifest_sha256": supplied_manifest_sha,
        "backup_sha256": supplied_backup_sha,
        "backup_bytes": backup_file.stat().st_size,
        "sqlite_integrity": "ok",
        "experiment_manifest_sha256": current.manifest_sha256,
        "statistical_policy_sha256": current.statistical_policy_sha256,
        "network_requests": False,
        "database_mutation": False,
        "calibrated_probability_authority": False,
        "financial_authority": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--db", type=Path, required=True)
    create.add_argument("--backup-dir", type=Path, required=True)
    create.add_argument("--output", type=Path)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "create":
        report = create_weather_calibration_backup(args.db, args.backup_dir)
        output = args.output
    else:
        report = verify_weather_calibration_backup(args.manifest)
        output = None
    payload = json.dumps(report, indent=2, sort_keys=True)
    if output:
        output.write_text(payload + "\n", encoding="utf-8")
    print(payload)


if __name__ == "__main__":
    main()
