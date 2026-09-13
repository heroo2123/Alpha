from __future__ import annotations

"""Backup/restore verification for the isolated weather-paper SQLite ledger.

The legacy scanner backup profile requires unrelated ``signals/manual_trades`` tables
and therefore cannot prove recoverability of ``weather-paper.sqlite``.  This module
uses SQLite's online backup API and a paper-specific logical profile: required tables,
schema SQL, row counts and deterministic row digests are checked before publication
and again after restoring into a brand-new temporary database.

This is deployment-neutral library code.  Creating/installing a timer remains an
explicit later operator action; importing this module never writes or schedules work.
"""

import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


WEATHER_PAPER_BACKUP_VERSION = "weather_paper_backup_v1_restore_verified_logical_profile"
WEATHER_PAPER_REQUIRED_TABLES = (
    "weather_paper_capacity_usage",
    "weather_paper_decisions",
    "weather_paper_positions",
    "weather_paper_signals",
    "weather_paper_state",
)


class WeatherPaperBackupError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _connect(path: Path, *, readonly: bool) -> sqlite3.Connection:
    if readonly:
        connection = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True, timeout=5.0)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=5.0)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA busy_timeout=5000")
    return connection


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json_value(value):
    if isinstance(value, bytes):
        return {"__bytes_hex__": value.hex()}
    if value is None or isinstance(value, (str, int, float)):
        return value
    raise WeatherPaperBackupError("PAPER_BACKUP_UNSUPPORTED_SQLITE_VALUE")


def _table_digest(connection: sqlite3.Connection, table: str) -> tuple[int, str]:
    columns = [
        str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    ]
    if not columns:
        raise WeatherPaperBackupError("PAPER_BACKUP_TABLE_SCHEMA_MISSING")
    order = ",".join(f'"{column}"' for column in columns)
    digest = hashlib.sha256()
    count = 0
    for row in connection.execute(f'SELECT * FROM "{table}" ORDER BY {order}'):
        payload = {
            column: _json_value(row[column])
            for column in columns
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
        count += 1
    return count, digest.hexdigest()


def verify_weather_paper_database(path: str | Path) -> dict:
    target = Path(path).expanduser().resolve()
    if not target.is_file() or target.is_symlink():
        raise WeatherPaperBackupError("PAPER_BACKUP_DATABASE_FILE_INVALID")
    with _connect(target, readonly=True) as db:
        quick = str(db.execute("PRAGMA quick_check").fetchone()[0])
        if quick.lower() != "ok":
            raise WeatherPaperBackupError("PAPER_BACKUP_QUICK_CHECK_FAILED")
        tables = {
            str(row[0])
            for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = set(WEATHER_PAPER_REQUIRED_TABLES) - tables
        if missing:
            raise WeatherPaperBackupError("PAPER_BACKUP_REQUIRED_TABLES_MISSING")
        schema_rows = [
            (str(row[0]), str(row[1] or ""))
            for row in db.execute(
                "SELECT name,sql FROM sqlite_master WHERE type='table' ORDER BY name"
            )
            if str(row[0]) in WEATHER_PAPER_REQUIRED_TABLES
        ]
        schema_sha = hashlib.sha256(
            json.dumps(schema_rows, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
        ).hexdigest()
        logical = {}
        for table in WEATHER_PAPER_REQUIRED_TABLES:
            count, digest = _table_digest(db, table)
            logical[table] = {"row_count": count, "row_digest_sha256": digest}
    return {
        "version": WEATHER_PAPER_BACKUP_VERSION,
        "path": str(target),
        "quick_check": quick,
        "file_sha256": _sha_file(target),
        "bytes": target.stat().st_size,
        "schema_sha256": schema_sha,
        "logical_tables": logical,
    }


def verify_weather_paper_restore(path: str | Path) -> dict:
    source = Path(path).expanduser().resolve()
    source_profile = verify_weather_paper_database(source)
    with tempfile.TemporaryDirectory(prefix="weather-paper-restore-") as tmp:
        restored = Path(tmp) / "restored-weather-paper.sqlite"
        with _connect(source, readonly=True) as src, _connect(restored, readonly=False) as dst:
            src.backup(dst, pages=256, sleep=0.01)
        restored_profile = verify_weather_paper_database(restored)
        if restored_profile["schema_sha256"] != source_profile["schema_sha256"]:
            raise WeatherPaperBackupError("PAPER_BACKUP_RESTORE_SCHEMA_MISMATCH")
        if restored_profile["logical_tables"] != source_profile["logical_tables"]:
            raise WeatherPaperBackupError("PAPER_BACKUP_RESTORE_LOGICAL_MISMATCH")
    return {
        "restore_verified": True,
        "schema_sha256": source_profile["schema_sha256"],
        "logical_tables": source_profile["logical_tables"],
        "source_file_sha256": source_profile["file_sha256"],
    }


def backup_weather_paper_database(
    source: str | Path,
    backup_dir: str | Path,
    *,
    release_sha: str,
    retention_days: int = 14,
    now: datetime | None = None,
) -> dict:
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file() or source_path.is_symlink():
        raise WeatherPaperBackupError("PAPER_BACKUP_SOURCE_INVALID")
    release = str(release_sha or "").strip().lower()
    if len(release) != 40 or any(char not in "0123456789abcdef" for char in release):
        raise WeatherPaperBackupError("PAPER_BACKUP_RELEASE_SHA_INVALID")
    if isinstance(retention_days, bool) or not isinstance(retention_days, int) or retention_days < 1:
        raise WeatherPaperBackupError("PAPER_BACKUP_RETENTION_INVALID")

    destination = Path(backup_dir).expanduser().resolve()
    destination.mkdir(parents=True, exist_ok=True)
    os.chmod(destination, 0o700)
    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = instant.strftime("%Y%m%dT%H%M%SZ")
    final_path = destination / f"weather-paper-{stamp}.sqlite3"
    temporary = destination / f".{final_path.name}.tmp-{os.getpid()}"
    if final_path.exists():
        raise WeatherPaperBackupError("PAPER_BACKUP_DESTINATION_EXISTS")

    source_profile = verify_weather_paper_database(source_path)
    try:
        with _connect(source_path, readonly=True) as src, _connect(temporary, readonly=False) as dst:
            src.backup(dst, pages=256, sleep=0.01)
        temp_profile = verify_weather_paper_database(temporary)
        if temp_profile["schema_sha256"] != source_profile["schema_sha256"]:
            raise WeatherPaperBackupError("PAPER_BACKUP_COPY_SCHEMA_MISMATCH")
        if temp_profile["logical_tables"] != source_profile["logical_tables"]:
            raise WeatherPaperBackupError("PAPER_BACKUP_COPY_LOGICAL_MISMATCH")
        os.replace(temporary, final_path)
        os.chmod(final_path, 0o600)
        restore = verify_weather_paper_restore(final_path)
        manifest = {
            "version": WEATHER_PAPER_BACKUP_VERSION,
            "created_at": instant.isoformat(),
            "release_sha": release,
            "source": str(source_path),
            "backup": str(final_path),
            "backup_file_sha256": _sha_file(final_path),
            "backup_bytes": final_path.stat().st_size,
            "source_schema_sha256": source_profile["schema_sha256"],
            "source_logical_tables": source_profile["logical_tables"],
            **restore,
            "retention_days": retention_days,
        }
        manifest_path = final_path.with_suffix(final_path.suffix + ".json")
        manifest_path.write_text(
            json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8"
        )
        os.chmod(manifest_path, 0o600)
    finally:
        temporary.unlink(missing_ok=True)

    cutoff = instant.timestamp() - retention_days * 86400
    deleted: list[str] = []
    for candidate in destination.glob("weather-paper-*.sqlite3"):
        if candidate == final_path or candidate.stat().st_mtime >= cutoff:
            continue
        candidate.with_suffix(candidate.suffix + ".json").unlink(missing_ok=True)
        candidate.unlink(missing_ok=True)
        deleted.append(candidate.name)
    manifest["old_backups_deleted"] = sorted(deleted)
    return manifest
