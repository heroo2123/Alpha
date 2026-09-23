"""Bounded read-only SQLite control snapshot. Standalone standard library only.

Run with an already authorized identity. This tool does not obtain privileges,
change source permissions, stop services, checkpoint a live WAL, or delete history.
The returned database and metadata are PRIVATE evidence, never Git artifacts.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import tempfile
import time


VERSION = "alpha_v11_control_snapshot_v1"


class SnapshotError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def checked_path(path: Path, *, exists: bool = True) -> Path:
    if not path.is_absolute() or ".." in path.parts:
        raise SnapshotError("ABSOLUTE_NONTRAVERSING_PATH_REQUIRED")
    for item in (path, *path.parents):
        if item.is_symlink():
            raise SnapshotError("SYMLINK_PATH_REFUSED")
    if exists and not path.exists():
        raise SnapshotError("PATH_MISSING")
    return path


def _identifier(value: str, length: int) -> str:
    if len(value) != length or any(c not in "0123456789abcdef" for c in value):
        raise SnapshotError("INVALID_IDENTITY_DIGEST")
    return value


def snapshot_database(source: Path, destination: Path, *, release_sha: str,
                      tree_sha: str, config_sha256: str, max_seconds: float = 45,
                      max_bytes: int = 1024 * 1024 * 1024,
                      context_files: dict[str, Path] | None = None) -> dict:
    """Pin a read transaction and use SQLite backup so committed WAL is included.

    Capture verification runs against the copy only. A changing source is never
    separately hashed or rescanned and then incorrectly compared with a later copy.
    Failure discards only this attempt's temporary output, never prior snapshots.
    """
    source = checked_path(Path(source))
    destination = checked_path(Path(destination), exists=False)
    if not source.is_file() or destination.exists():
        raise SnapshotError("SOURCE_OR_NEW_DESTINATION_INVALID")
    _identifier(release_sha, 40)
    _identifier(tree_sha, 40)
    _identifier(config_sha256, 64)
    if (type(max_bytes) is not int or not 4096 <= max_bytes <= 8 * 1024**3
            or isinstance(max_seconds, bool) or not 0 < max_seconds <= 120):
        raise SnapshotError("INVALID_RESOURCE_BOUNDS")
    checked_path(destination.parent)
    context_files = context_files or {}
    if len(context_files) > 4:
        raise SnapshotError("CONTEXT_FILE_LIMIT")
    for name, path in context_files.items():
        if not name or len(name) > 32 or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name):
            raise SnapshotError("CONTEXT_NAME_INVALID")
        checked_path(Path(path))
    started = datetime.now(timezone.utc).isoformat()
    deadline = time.monotonic() + max_seconds
    before = source.stat()
    partial = Path(tempfile.mkdtemp(prefix=".v11-snapshot-", dir=destination.parent))
    os.chmod(partial, 0o700)
    target = partial / "control.sqlite3"
    destination_created = False
    try:
        context = []

        def preserve_context(phase: str) -> None:
            for name, path in sorted(context_files.items()):
                with Path(path).open("rb") as stream:
                    data = stream.read(2 * 1024**2 + 1)
                if len(data) > 2 * 1024**2:
                    raise SnapshotError("CONTEXT_BYTES_LIMIT")
                output = partial / f"{name}-{phase}.bin"
                with output.open("xb") as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.chmod(output, 0o400)
                context.append({"source": str(path), "phase": phase,
                                "filename": output.name, "bytes": len(data),
                                "sha256": hashlib.sha256(data).hexdigest(),
                                "captured_at": datetime.now(timezone.utc).isoformat()})

        preserve_context("before")
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True,
                                     timeout=1.0)) as src:
            src.execute("PRAGMA query_only=ON")
            src.execute("PRAGMA busy_timeout=1000")
            src.execute("BEGIN")
            src.execute("SELECT COUNT(*) FROM sqlite_schema").fetchone()
            pages = src.execute("PRAGMA page_count").fetchone()[0]
            page_size = src.execute("PRAGMA page_size").fetchone()[0]
            if pages * page_size > max_bytes:
                raise SnapshotError("SNAPSHOT_SIZE_LIMIT")
            if shutil.disk_usage(destination.parent).free < pages * page_size + 64 * 1024**2:
                raise SnapshotError("SNAPSHOT_DISK_HEADROOM")
            journal = src.execute("PRAGMA journal_mode").fetchone()[0]

            def progress(status: int, remaining: int, total: int) -> None:
                if time.monotonic() > deadline:
                    raise SnapshotError("SNAPSHOT_DEADLINE")
                if total * page_size > max_bytes:
                    raise SnapshotError("SNAPSHOT_SIZE_LIMIT")
                # Yield I/O/CPU between small chunks on the control host.
                if remaining:
                    time.sleep(0.002)

            with closing(sqlite3.connect(target, timeout=1.0)) as dst:
                src.backup(dst, pages=64, progress=progress, sleep=0.02)
                # Backup inherits a WAL-mode header. Normalize ONLY the copy to
                # a standalone file; never checkpoint/change the control DB.
                dst.execute("PRAGMA journal_mode=DELETE").fetchone()
            src.rollback()
        after = source.stat()
        if (before.st_dev, before.st_ino) != (after.st_dev, after.st_ino):
            raise SnapshotError("SOURCE_REPLACED_DURING_CAPTURE")
        with closing(sqlite3.connect(target.as_uri() + "?mode=ro", uri=True)) as copy:
            copy.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            if copy.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
                raise SnapshotError("SNAPSHOT_INTEGRITY_FAILED")
            schema = copy.execute(
                "SELECT type,name,tbl_name,sql FROM sqlite_schema ORDER BY type,name"
            ).fetchall()
        digest = sha256_file(target)
        preserve_context("after")
        if time.monotonic() > deadline:
            raise SnapshotError("SNAPSHOT_DEADLINE")
        os.chmod(target, 0o400)
        manifest = {
            "version": VERSION, "namespace": "V10_CONTROL",
            "capture_started_at": started,
            "capture_completed_at": datetime.now(timezone.utc).isoformat(),
            "source_path": str(source), "source_device": before.st_dev,
            "source_inode": before.st_ino, "source_journal_mode": journal,
            "source_release_sha": release_sha, "source_tree_sha": tree_sha,
            "source_config_sha256": config_sha256,
            "identity_binding": "OPERATOR_SUPPLIED_REQUIRES_SEPARATE_RUNTIME_ATTESTATION",
            "context_files": context,
            "context_consistency": "BRACKETING_READS_NOT_SQL_TRANSACTION_ATOMIC",
            "snapshot_filename": target.name, "snapshot_sha256": digest,
            "snapshot_bytes": target.stat().st_size,
            "schema_sha256": hashlib.sha256(json.dumps(schema, separators=(",", ":"),
                                                       ensure_ascii=True).encode()).hexdigest(),
            "quick_check": "ok", "method": "SQLITE_BACKUP_PINNED_READ_TRANSACTION",
            "committed_wal_included": True, "financial_authority": False,
            "source_mutated": False, "classification": "PRIVATE_CONTROL_EVIDENCE",
        }
        manifest_path = partial / "manifest.json"
        with manifest_path.open("x") as stream:
            json.dump(manifest, stream, sort_keys=True, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(manifest_path, 0o400)
        with target.open("rb") as stream:
            os.fsync(stream.fileno())
        # Atomic mkdir prevents replacing even an empty existing destination.
        # The manifest is the completion marker and is published last.
        destination.mkdir(mode=0o700)
        destination_created = True
        for path in partial.iterdir():
            if path != manifest_path:
                path.rename(destination / path.name)
        manifest_path.rename(destination / manifest_path.name)
        partial.rmdir()
        fd = os.open(destination.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        return manifest
    except Exception:
        shutil.rmtree(partial, ignore_errors=True)
        if destination_created:
            shutil.rmtree(destination, ignore_errors=True)
        raise


def open_verified_snapshot(directory: Path) -> tuple[sqlite3.Connection, dict]:
    """Read a completed, standalone copy; reject active WAL companions."""
    directory = checked_path(Path(directory))
    manifest_path = checked_path(directory / "manifest.json")
    if manifest_path.stat().st_size > 32768:
        raise SnapshotError("MANIFEST_TOO_LARGE")
    manifest = json.loads(manifest_path.read_text())
    if (manifest.get("version") != VERSION or manifest.get("namespace") != "V10_CONTROL"
            or manifest.get("snapshot_filename") != "control.sqlite3"):
        raise SnapshotError("SNAPSHOT_MANIFEST_INVALID")
    context = manifest.get("context_files", [])
    if not isinstance(context, list) or len(context) > 8:
        raise SnapshotError("CONTEXT_MANIFEST_INVALID")
    for record in context:
        name = record.get("filename", "")
        if not name or Path(name).name != name or not name.endswith(".bin"):
            raise SnapshotError("CONTEXT_MANIFEST_INVALID")
        path = checked_path(directory / name)
        if (path.stat().st_size > 2 * 1024**2 or path.stat().st_size != record.get("bytes")
                or sha256_file(path) != record.get("sha256")):
            raise SnapshotError("CONTEXT_HASH_MISMATCH")
    target = checked_path(directory / "control.sqlite3")
    if any(Path(str(target) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise SnapshotError("SNAPSHOT_NOT_STANDALONE")
    if target.stat().st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH):
        raise SnapshotError("SNAPSHOT_MUST_BE_READ_ONLY")
    if target.stat().st_size != manifest["snapshot_bytes"] or sha256_file(target) != manifest["snapshot_sha256"]:
        raise SnapshotError("SNAPSHOT_HASH_MISMATCH")
    db = sqlite3.connect(target.as_uri() + "?mode=ro&immutable=1", uri=True)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db, manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--tree-sha", required=True)
    parser.add_argument("--config-sha256", required=True)
    parser.add_argument("--context-file", action="append", default=[], metavar="NAME=ABSOLUTE_PATH")
    parser.add_argument("--output-owner", help="Optional owner of the NEW private copy; source permissions never change")
    args = parser.parse_args()
    try:
        context = {}
        for entry in args.context_file:
            name, separator, path = entry.partition("=")
            if not separator or name in context:
                raise SnapshotError("CONTEXT_ARGUMENT_INVALID")
            context[name] = Path(path)
        owner = None
        if args.output_owner:
            import pwd
            owner = pwd.getpwnam(args.output_owner)
            if os.geteuid() not in (0, owner.pw_uid):
                raise SnapshotError("OUTPUT_OWNER_NOT_AUTHORIZED")
        manifest = snapshot_database(args.source, args.destination, release_sha=args.release_sha,
                                     tree_sha=args.tree_sha, config_sha256=args.config_sha256,
                                     context_files=context)
        if owner:
            # Only files created by this successful attempt are reassigned.
            for path in args.destination.iterdir():
                os.chown(path, owner.pw_uid, owner.pw_gid, follow_symlinks=False)
            os.chown(args.destination, owner.pw_uid, owner.pw_gid, follow_symlinks=False)
    except (SnapshotError, OSError, sqlite3.Error, ValueError, KeyError) as exc:
        # No DB row, raw provider payload, credential, or environment is printed.
        print(json.dumps({"status": "BLOCKED", "reason": str(exc) if isinstance(exc, SnapshotError)
                          else type(exc).__name__}))
        return 2
    print(json.dumps({"status": "CAPTURED", "destination": str(args.destination),
                      "snapshot_sha256": manifest["snapshot_sha256"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
