"""Offline, read-only plan for a future daily evidence genesis.

This module deliberately cannot publish a database or grant review authority.
The caller must supply a sealed, checkpointed SQLite snapshot of MASTER.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import stat

from .evidence import VERSION, canonical, digest

NAMESPACE = "CHALLENGER:katl-shadow"
BASE_IDS = (
    "decision-shadow:station:KATL:raw",
    "decision-shadow:station:KATL:metadata",
    "decision-shadow:technical-readiness:0dd809ea4b42",
)
MAX_SNAPSHOT_BYTES = 256 * 1024 * 1024
MAX_RECORD_BYTES = 1024 * 1024
COLUMNS = ("seq", "record_id", "kind", "event_id", "recorded_at", "available_at", "body", "body_sha256")
SCHEMA_SQL = {
    "v11_meta": "CREATE TABLE v11_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL)",
    "v11_records": "CREATE TABLE v11_records(seq INTEGER PRIMARY KEY, record_id TEXT NOT NULL UNIQUE, kind TEXT NOT NULL, event_id TEXT NOT NULL, recorded_at REAL NOT NULL, available_at REAL NOT NULL, body TEXT NOT NULL, body_sha256 TEXT NOT NULL)",
    "v11_causal": "CREATE INDEX v11_causal ON v11_records(event_id,available_at,seq)",
    "v11_kind": "CREATE INDEX v11_kind ON v11_records(kind,seq)",
    "v11_time": "CREATE INDEX v11_time ON v11_records(recorded_at)",
    "v11_no_update": "CREATE TRIGGER v11_no_update BEFORE UPDATE ON v11_records BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END",
    "v11_no_delete": "CREATE TRIGGER v11_no_delete BEFORE DELETE ON v11_records BEGIN SELECT RAISE(ABORT,'APPEND_ONLY'); END",
}


class SeedPlanError(RuntimeError):
    pass


@dataclass(frozen=True)
class SeedPlan:
    # Original body text, ID, hash and timestamps are retained in these rows.
    rows: tuple[tuple, ...]
    manifest: dict
    manifest_sha256: str


def _regular_sealed(path: Path) -> os.stat_result:
    if not path.is_absolute() or ".." in path.parts:
        raise SeedPlanError("SNAPSHOT_ABSOLUTE_PATH_REQUIRED")
    for part in (path, *path.parents):
        if part.is_symlink():
            raise SeedPlanError("SNAPSHOT_SYMLINK_REFUSED")
    try:
        info = path.stat()
    except OSError as exc:
        raise SeedPlanError("SNAPSHOT_MISSING") from exc
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > MAX_SNAPSHOT_BYTES:
        raise SeedPlanError("SNAPSHOT_FILE_BOUNDS_OR_ALIAS")
    if any(Path(str(path) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")):
        raise SeedPlanError("SNAPSHOT_SIDECAR_REFUSED")
    return info


def _file_sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _json_unique(raw: str) -> dict:
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise SeedPlanError("BODY_DUPLICATE_KEY")
            value[key] = item
        return value
    try:
        body = json.loads(raw, object_pairs_hook=unique)
    except (ValueError, TypeError) as exc:
        raise SeedPlanError("BODY_JSON_INVALID") from exc
    if type(body) is not dict or canonical(body) != raw:
        raise SeedPlanError("BODY_NOT_CANONICAL")
    return body


def _validate_schema(db: sqlite3.Connection) -> None:
    try:
        meta = dict(db.execute("SELECT key,value FROM v11_meta"))
        columns = tuple(row[1] for row in db.execute("PRAGMA table_info(v11_records)"))
        objects = {row[0]: row[1] for row in db.execute(
            "SELECT name,sql FROM sqlite_master WHERE type IN ('table','index','trigger') AND name NOT LIKE 'sqlite_%'")}
    except sqlite3.DatabaseError as exc:
        raise SeedPlanError("SNAPSHOT_SCHEMA_INVALID") from exc
    if meta != {"version": VERSION, "namespace": NAMESPACE} or columns != COLUMNS:
        raise SeedPlanError("SNAPSHOT_SCHEMA_OR_NAMESPACE")
    if set(objects) != set(SCHEMA_SQL):
        raise SeedPlanError("SNAPSHOT_SCHEMA_OBJECTS")
    for name, expected in SCHEMA_SQL.items():
        if "".join(objects[name].upper().split()) != "".join(expected.upper().split()):
            raise SeedPlanError("SNAPSHOT_SCHEMA_SQL_MISMATCH:" + name)


def _row(db: sqlite3.Connection, record_id: str) -> sqlite3.Row:
    rows = db.execute("SELECT * FROM v11_records WHERE record_id=? LIMIT 2", (record_id,)).fetchall()
    if len(rows) != 1:
        raise SeedPlanError("BASELINE_MISSING_OR_DUPLICATE:" + record_id)
    return rows[0]


def _validate_row(row: sqlite3.Row) -> dict:
    if type(row["body"]) is not str or len(row["body"].encode("utf-8")) > MAX_RECORD_BYTES:
        raise SeedPlanError("BODY_BYTES_LIMIT")
    body = _json_unique(row["body"])
    if (type(row["seq"]) is not int or row["seq"] <= 0
            or body.get("namespace") != NAMESPACE or body.get("financial_authority") is not False
            or any(type(row[field]) not in (int, float) or not math.isfinite(row[field])
                   for field in ("recorded_at", "available_at"))
            or any(body.get(field) != row[field] for field in
                   ("record_id", "kind", "event_id", "recorded_at", "available_at"))
            or digest(body) != row["body_sha256"]):
        raise SeedPlanError("BASELINE_ENVELOPE_OR_HASH:" + str(row["record_id"]))
    return body


def _refs(body: dict) -> tuple[tuple[str, str], ...]:
    refs = body.get("evidence", [])
    if type(refs) is not list:
        raise SeedPlanError("BASELINE_REFERENCES_INVALID")
    result = []
    for ref in refs:
        if type(ref) is not dict or type(ref.get("id")) is not str or type(ref.get("sha256")) is not str:
            raise SeedPlanError("BASELINE_REFERENCES_INVALID")
        result.append((ref["id"], ref["sha256"]))
    payload = body.get("payload", {})
    if type(payload) is dict and "source_capture_id" in payload:
        if type(payload.get("source_capture_id")) is not str or type(payload.get("source_capture_sha256")) is not str:
            raise SeedPlanError("BASELINE_REFERENCES_INVALID")
        result.append((payload["source_capture_id"], payload["source_capture_sha256"]))
    return tuple(result)


def plan_daily_seed(snapshot: Path, expected_source_sha256: str) -> SeedPlan:
    """Reject unresolved baseline closure; assign deterministic local seq 1..N.

    The source is a separately sealed, WAL-free snapshot, never a live DB.
    No file is created or modified here.
    """
    snapshot = Path(snapshot)
    before = _regular_sealed(snapshot)
    source_sha = _file_sha(snapshot)
    if source_sha != expected_source_sha256:
        raise SeedPlanError("SOURCE_SNAPSHOT_IDENTITY_CHANGED")
    try:
        with sqlite3.connect(snapshot.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
            db.row_factory = sqlite3.Row
            db.execute("PRAGMA query_only=ON")
            _validate_schema(db)
            if db.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise SeedPlanError("SOURCE_INTEGRITY_FAILED")
            source_rows = [_row(db, record_id) for record_id in BASE_IDS]
            bodies = {row["record_id"]: _validate_row(row) for row in source_rows}
            if len({row["seq"] for row in source_rows}) != len(BASE_IDS):
                raise SeedPlanError("BASELINE_SEQUENCE_DUPLICATE")
            by_id = {row["record_id"]: row for row in source_rows}
            for body in bodies.values():
                for ref_id, ref_sha in _refs(body):
                    if ref_id not in by_id:
                        raise SeedPlanError("BASELINE_CLOSURE_UNREVIEWED:" + ref_id)
                    if by_id[ref_id]["body_sha256"] != ref_sha:
                        raise SeedPlanError("BASELINE_REFERENCE_HASH_MISMATCH:" + ref_id)
            ordered = sorted(source_rows, key=lambda row: row["seq"])
            rows = tuple((i, *(row[field] for field in COLUMNS[1:]))
                         for i, row in enumerate(ordered, 1))
            mapping = [dict(source_seq=row["seq"], local_seq=i,
                            record_id=row["record_id"], body_sha256=row["body_sha256"])
                       for i, row in enumerate(ordered, 1)]
    except sqlite3.DatabaseError as exc:
        raise SeedPlanError("SOURCE_SQLITE_INVALID") from exc
    after = _regular_sealed(snapshot)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ) or _file_sha(snapshot) != source_sha:
        raise SeedPlanError("SOURCE_SNAPSHOT_CHANGED_DURING_READ")
    manifest = dict(version="alpha_v11_daily_seed_plan_v1", namespace=NAMESPACE,
                    source_snapshot_sha256=source_sha, mapping=mapping,
                    financial_authority=False)
    return SeedPlan(rows, manifest, digest(manifest))


def verify_existing_daily(path: Path, plan: SeedPlan, expected_manifest_sha256: str) -> int:
    """Read-only admission of a same-generation DB; returns its contiguous tip.

    The manifest hash must be independently pinned by the caller. This verifier
    does not create a path and does not infer a review or generation binding.
    """
    if (plan.manifest_sha256 != expected_manifest_sha256
            or digest(plan.manifest) != expected_manifest_sha256
            or plan.manifest.get("namespace") != NAMESPACE
            or plan.manifest.get("financial_authority") is not False
            or len(plan.manifest.get("mapping", [])) != len(plan.rows)):
        raise SeedPlanError("SEED_MANIFEST_IDENTITY_MISMATCH")
    for row, mapped in zip(plan.rows, plan.manifest["mapping"]):
        if (type(mapped) is not dict or mapped.get("local_seq") != row[0]
                or mapped.get("record_id") != row[1] or mapped.get("body_sha256") != row[7]):
            raise SeedPlanError("SEED_MANIFEST_MAPPING_MISMATCH")
    path = Path(path)
    before = _regular_sealed(path)
    try:
        with sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
            db.row_factory = sqlite3.Row
            _validate_schema(db)
            count, maximum = db.execute("SELECT count(*),max(seq) FROM v11_records").fetchone()
            if count < len(plan.rows) or count != maximum:
                raise SeedPlanError("EXISTING_DAILY_SPARSE_OR_PARTIAL")
            for index, row in enumerate(db.execute("SELECT * FROM v11_records ORDER BY seq"), 1):
                if row["seq"] != index:
                    raise SeedPlanError("EXISTING_DAILY_SPARSE_OR_PARTIAL")
                _validate_row(row)
                if index <= len(plan.rows) and tuple(row[field] for field in COLUMNS) != plan.rows[index - 1]:
                    raise SeedPlanError("EXISTING_DAILY_GENESIS_MISMATCH")
    except sqlite3.DatabaseError as exc:
        raise SeedPlanError("EXISTING_DAILY_SQLITE_INVALID") from exc
    after = _regular_sealed(path)
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
            after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        raise SeedPlanError("EXISTING_DAILY_CHANGED_DURING_READ")
    return count
