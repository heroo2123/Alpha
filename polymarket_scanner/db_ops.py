from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REQUIRED_CORE_TABLES = {"signals", "manual_trades", "bot_state"}
DEFAULT_BUSY_TIMEOUT_MS = 5_000
DEFAULT_BACKUP_RETENTION_DAYS = 14


def _connect(path: str | Path, *, readonly: bool = False) -> sqlite3.Connection:
    target = Path(path).expanduser().resolve()
    if readonly:
        connection = sqlite3.connect(
            f"file:{target}?mode=ro",
            uri=True,
            timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000.0,
        )
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(
            str(target),
            timeout=DEFAULT_BUSY_TIMEOUT_MS / 1000.0,
        )
    connection.row_factory = sqlite3.Row
    connection.execute(f"PRAGMA busy_timeout={DEFAULT_BUSY_TIMEOUT_MS}")
    return connection


def configure_database_runtime(path: str | Path) -> dict:
    """Set persistent WAL mode and verify the production SQLite runtime contract.

    WAL permits the scanner's writes and command worker's reads to make progress
    concurrently. Python's sqlite timeout plus explicit busy_timeout gives bounded
    cross-process contention rather than immediate `database is locked` failures.
    Strategy/network event loops still perform DB calls through asyncio.to_thread.
    """
    with _connect(path) as connection:
        journal_mode = str(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
        connection.execute("PRAGMA synchronous=NORMAL")
        connection.execute("PRAGMA wal_autocheckpoint=1000")
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if journal_mode != "wal":
            raise RuntimeError(f"SQLite WAL activation failed: journal_mode={journal_mode}")
        if quick.lower() != "ok":
            raise RuntimeError(f"SQLite quick_check failed: {quick}")
        return {
            "journal_mode": journal_mode,
            "quick_check": quick,
            "busy_timeout_ms": int(connection.execute("PRAGMA busy_timeout").fetchone()[0]),
            "wal_autocheckpoint": int(connection.execute("PRAGMA wal_autocheckpoint").fetchone()[0]),
        }


def database_health(path: str | Path) -> dict:
    target = Path(path).expanduser().resolve()
    if not target.exists():
        return {"exists": False, "path": str(target)}
    with _connect(target, readonly=True) as connection:
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        journal_mode = str(connection.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        page_count = int(connection.execute("PRAGMA page_count").fetchone()[0])
        page_size = int(connection.execute("PRAGMA page_size").fetchone()[0])
        freelist_count = int(connection.execute("PRAGMA freelist_count").fetchone()[0])
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    return {
        "exists": True,
        "path": str(target),
        "quick_check": quick,
        "journal_mode": journal_mode,
        "page_count": page_count,
        "page_size": page_size,
        "database_bytes": page_count * page_size,
        "freelist_pages": freelist_count,
        "wal_bytes": target.with_name(target.name + "-wal").stat().st_size
        if target.with_name(target.name + "-wal").exists()
        else 0,
        "core_tables_present": sorted(REQUIRED_CORE_TABLES & tables),
        "core_tables_missing": sorted(REQUIRED_CORE_TABLES - tables),
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_database(path: str | Path, *, require_core_tables: bool = True) -> dict:
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        raise FileNotFoundError(str(target))
    with _connect(target, readonly=True) as connection:
        quick = str(connection.execute("PRAGMA quick_check").fetchone()[0])
        if quick.lower() != "ok":
            raise RuntimeError(f"SQLite quick_check failed for {target.name}: {quick}")
        tables = {
            str(row[0])
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = REQUIRED_CORE_TABLES - tables
        if require_core_tables and missing:
            raise RuntimeError(f"backup is missing core tables: {sorted(missing)}")
        counts = {}
        for table in sorted(REQUIRED_CORE_TABLES & tables):
            counts[table] = int(connection.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
    return {
        "path": str(target),
        "quick_check": quick,
        "sha256": _sha256(target),
        "bytes": target.stat().st_size,
        "tables": sorted(tables),
        "core_counts": counts,
    }


def verify_restore(path: str | Path) -> dict:
    """Exercise the restore mechanism into a brand-new temporary SQLite file."""
    source_path = Path(path).expanduser().resolve()
    verified_source = verify_database(source_path)
    with tempfile.TemporaryDirectory(prefix="alpha-db-restore-") as tmp:
        restored_path = Path(tmp) / "restored.sqlite3"
        with _connect(source_path, readonly=True) as source, _connect(restored_path) as restored:
            source.backup(restored)
        verified_restored = verify_database(restored_path)
        if verified_source["core_counts"] != verified_restored["core_counts"]:
            raise RuntimeError("restore verification row counts differ from backup")
        return {
            "restore_verified": True,
            "source_sha256": verified_source["sha256"],
            "source_bytes": verified_source["bytes"],
            "core_counts": verified_source["core_counts"],
        }


def backup_database(
    source: str | Path,
    backup_dir: str | Path,
    *,
    retention_days: int = DEFAULT_BACKUP_RETENTION_DAYS,
    now: datetime | None = None,
) -> dict:
    source_path = Path(source).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(str(source_path))
    destination_dir = Path(backup_dir).expanduser().resolve()
    destination_dir.mkdir(parents=True, exist_ok=True)

    instant = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = instant.strftime("%Y%m%dT%H%M%SZ")
    final_path = destination_dir / f"signals-{stamp}.sqlite3"
    temp_path = destination_dir / f".{final_path.name}.tmp-{os.getpid()}"
    if final_path.exists():
        raise FileExistsError(str(final_path))

    try:
        with _connect(source_path, readonly=True) as source_conn, _connect(temp_path) as backup_conn:
            source_conn.backup(backup_conn, pages=256, sleep=0.01)
        verify_database(temp_path)
        os.replace(temp_path, final_path)
        restore = verify_restore(final_path)
        manifest = {
            "created_at": instant.isoformat(),
            "source": str(source_path),
            "backup": str(final_path),
            "sha256": _sha256(final_path),
            "bytes": final_path.stat().st_size,
            **restore,
        }
        manifest_path = final_path.with_suffix(final_path.suffix + ".json")
        manifest_path.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        os.chmod(final_path, 0o600)
        os.chmod(manifest_path, 0o600)
    finally:
        temp_path.unlink(missing_ok=True)

    cutoff = instant.timestamp() - max(1, int(retention_days)) * 86400
    deleted = []
    for candidate in destination_dir.glob("signals-*.sqlite3"):
        if candidate == final_path or candidate.stat().st_mtime >= cutoff:
            continue
        manifest_candidate = candidate.with_suffix(candidate.suffix + ".json")
        candidate.unlink(missing_ok=True)
        manifest_candidate.unlink(missing_ok=True)
        deleted.append(candidate.name)

    manifest["retention_days"] = max(1, int(retention_days))
    manifest["old_backups_deleted"] = sorted(deleted)
    return manifest


def _main() -> int:
    parser = argparse.ArgumentParser(description="Alpha SQLite operations")
    sub = parser.add_subparsers(dest="command", required=True)

    configure = sub.add_parser("configure")
    configure.add_argument("--db", required=True)

    health = sub.add_parser("health")
    health.add_argument("--db", required=True)

    backup = sub.add_parser("backup")
    backup.add_argument("--db", required=True)
    backup.add_argument("--backup-dir", required=True)
    backup.add_argument("--retention-days", type=int, default=DEFAULT_BACKUP_RETENTION_DAYS)

    verify = sub.add_parser("verify")
    verify.add_argument("--backup", required=True)

    args = parser.parse_args()
    if args.command == "configure":
        result = configure_database_runtime(args.db)
    elif args.command == "health":
        result = database_health(args.db)
    elif args.command == "backup":
        result = backup_database(args.db, args.backup_dir, retention_days=args.retention_days)
    else:
        result = verify_restore(args.backup)
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
