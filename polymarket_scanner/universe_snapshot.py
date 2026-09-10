"""Immutable discovery generations. This module deliberately uses only stdlib.

The SQLite file is never the trading/accounting database. One locked writer builds
it privately; a small, atomically replaced pointer is the only publication point.
Readers keep their accepted objects if a build, validation, or publication fails.
"""
from __future__ import annotations

import contextlib
import fcntl
import hashlib
import json
import math
import os
import re
import sqlite3
import time
import uuid
import zlib
from pathlib import Path

from .universe_failures import SnapshotError

SNAPSHOT_VERSION = "complete_gamma_snapshot_v2_bounded_compressed_payloads"
RUNTIME_MODE = "isolated_gamma_builder_immutable_sqlite_shadow"
DISCOVERY_CAP = 250_000
MATERIALIZED_CAP = 30_000
INVENTORY_CAP = 1_000_000
MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_ROW_BYTES = 256 * 1024
MAX_PARENT_BYTES = 4 * 1024 * 1024
MAX_DECODED_BYTES = 128 * 1024 * 1024
GAMMA_PAGE_SIZE = 25
BUILD_INTERVAL_SECONDS = 600
BUILD_DEADLINE_SECONDS = 900
UNIVERSE_MAX_AGE_SECONDS = 1800
GAMMA_QUOTE_MAX_AGE_SECONDS = 900
POLL_SECONDS = 5
KEEP_GENERATIONS = 3
_NAME = re.compile(r"g-[0-9]{12}-[0-9a-f]{32}\.sqlite\Z")
_SHA = re.compile(r"[0-9a-f]{40}\Z")


def snapshot_directory() -> Path:
    return Path(os.environ.get("UNIVERSE_SNAPSHOT_DIR", "~/.polymarket-edge-scanner/universe")).expanduser()


def encoded(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def decode_payload(blob: bytes, raw_bytes: int, *, parent: bool = False) -> dict:
    """Independent per-record and aggregate limits prevent compressed expansion."""
    cap = MAX_PARENT_BYTES if parent else MAX_ROW_BYTES
    if type(raw_bytes) is not int or not 0 < raw_bytes <= cap or not isinstance(blob, bytes) or len(blob) > cap + 2048:
        raise SnapshotError("PAYLOAD_INVALID")
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(blob, raw_bytes + 1)
        if len(raw) != raw_bytes or not decoder.eof or decoder.unused_data or decoder.unconsumed_tail:
            raise SnapshotError("PAYLOAD_INVALID")
        value = json.loads(raw)
    except (ValueError, zlib.error) as exc:
        raise SnapshotError("PAYLOAD_INVALID") from exc
    if not isinstance(value, dict):
        raise SnapshotError("PAYLOAD_INVALID")
    return value


def fsync_directory(directory: Path) -> None:
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("wb") as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    fsync_directory(path.parent)


def read_json(path: Path, *, limit: int = 65536) -> dict:
    if path.stat().st_size > limit:
        raise SnapshotError("METADATA_SIZE_CAP")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise SnapshotError("METADATA_INVALID")
    return value


@contextlib.contextmanager
def builder_lock(directory: Path):
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "builder.lock").open("a+b") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise SnapshotError("BUILDER_LOCKED") from exc
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def generation_age(manifest: dict, *, now: float | None = None) -> float:
    # Age starts at FIRST observation, never completion/publication/acceptance.
    current = time.time() if now is None else now
    started = manifest.get("started_at")
    if not isinstance(started, (int, float)) or isinstance(started, bool) or not math.isfinite(started):
        return math.inf
    age = current - started
    return age if math.isfinite(age) and age >= 0 else math.inf


def current_pointer(directory: Path) -> dict | None:
    try:
        pointer = read_json(directory / "current.json")
    except FileNotFoundError:
        return None
    if not _NAME.fullmatch(str(pointer.get("file", ""))):
        raise SnapshotError("GENERATION_PATH")
    if type(pointer.get("sequence")) is not int or pointer["sequence"] < 1:
        raise SnapshotError("GENERATION_SEQUENCE")
    if int(pointer["file"].split("-")[1]) != pointer["sequence"]:
        raise SnapshotError("GENERATION_IDENTITY")
    if not re.fullmatch(r"[0-9a-f]{64}", str(pointer.get("sha256", ""))):
        raise SnapshotError("GENERATION_CHECKSUM")
    return pointer


class SnapshotWriter:
    """Use only while holding builder_lock. All large ledgers stay on disk."""

    def __init__(self, directory: Path, *, producer_sha: str, filter_version: str, started_at: float | None = None):
        if not _SHA.fullmatch(producer_sha):
            raise SnapshotError("RELEASE_MISMATCH")
        self.directory = directory
        self.started_monotonic = time.monotonic()
        old = current_pointer(directory)
        self.sequence = old["sequence"] + 1 if old else 1
        self.name = f"g-{self.sequence:012d}-{uuid.uuid4().hex}.sqlite"
        self.path = directory / (self.name + ".building")
        self.manifest = {
            "version": SNAPSHOT_VERSION, "generation_id": self.name[:-7],
            "sequence": self.sequence, "producer_sha": producer_sha,
            "filter_version": filter_version, "started_at": time.time() if started_at is None else started_at,
            "source": "https://gamma-api.polymarket.com/events/keyset",
            "query": {"active": True, "closed": False, "limit": GAMMA_PAGE_SIZE},
            "payload_encoding": "zlib_level_1_canonical_json_lossless",
            "scope": "natural_keyset_exhaustion_over_a_walk_not_transactional_point_in_time",
            "screening_source": "gamma_bbo_screening_only_not_executable",
            "source_timestamp_policy": "page_receipt_is_observation_time_not_exchange_quote_time",
            "discovery_hard_cap": DISCOVERY_CAP, "materialized_hard_cap": MATERIALIZED_CAP,
            "complete": False,
        }
        self.db = None
        try:
            self.db = sqlite3.connect(self.path)
            self.db.executescript("""
                PRAGMA journal_mode=OFF;
                PRAGMA synchronous=OFF;
                PRAGMA cache_size=-4096;
                PRAGMA temp_store=FILE;
                CREATE TABLE metadata (value TEXT NOT NULL);
                CREATE TABLE events (id TEXT PRIMARY KEY, payload BLOB NOT NULL, raw_bytes INTEGER NOT NULL,
                                     digest BLOB NOT NULL) WITHOUT ROWID;
                CREATE TABLE inventory (id TEXT PRIMARY KEY, event_id TEXT NOT NULL, identity BLOB NOT NULL,
                                        active INTEGER NOT NULL, selected INTEGER NOT NULL) WITHOUT ROWID;
                CREATE TABLE markets (id TEXT PRIMARY KEY, event_id TEXT NOT NULL, payload BLOB NOT NULL,
                                      raw_bytes INTEGER NOT NULL) WITHOUT ROWID;
                CREATE TABLE pages (number INTEGER PRIMARY KEY, cursor_in TEXT, cursor_out TEXT,
                                    receipt REAL NOT NULL, seconds REAL NOT NULL, response_sha256 TEXT NOT NULL);
            """)
        except BaseException:
            if self.db is not None:
                self.db.close()
            self.path.unlink(missing_ok=True)
            raise
        self.discovered = self.materialized = self.inventory_count = self.events_count = self.pages = 0
        self.decoded_bytes = self.parent_bytes = self.market_bytes = 0
        self.max_parent_bytes = self.max_market_bytes = 0
        self.closed = False
        self.published = False

    def inventory(self, market_id: str, event_id: str, identity: dict, *, active: bool, selected: bool) -> bool:
        digest = hashlib.sha256(encoded(identity)).digest()
        prior = self.db.execute("SELECT event_id,identity FROM inventory WHERE id=?", (market_id,)).fetchone()
        if prior:
            if prior != (event_id, digest):
                raise SnapshotError("DUPLICATE_IDENTITY_CONFLICT")
            return False
        self.db.execute("INSERT INTO inventory VALUES (?,?,?,?,?)", (market_id, event_id, digest, active, selected))
        self.inventory_count += 1
        self.discovered += int(active)
        if self.discovered > DISCOVERY_CAP:
            raise SnapshotError("DISCOVERY_CAP", count=self.discovered, limit=DISCOVERY_CAP)
        if self.inventory_count > INVENTORY_CAP:
            raise SnapshotError("INVENTORY_CAP", count=self.inventory_count, limit=INVENTORY_CAP)
        return True

    def _payload_budget(self, amount: int) -> None:
        self.decoded_bytes += amount
        if self.decoded_bytes > MAX_DECODED_BYTES:
            raise SnapshotError("DECODED_SIZE_CAP", observed_bytes=self.decoded_bytes, limit_bytes=MAX_DECODED_BYTES)

    def event(self, event_id: str, event: dict) -> None:
        # Encode/store a parent ONCE per event observation, never per selected leg.
        raw = encoded(event)
        size = len(raw)
        self.max_parent_bytes = max(self.max_parent_bytes, size)
        if size > MAX_PARENT_BYTES:
            raise SnapshotError("PARENT_SIZE_CAP", observed_bytes=size, limit_bytes=MAX_PARENT_BYTES)
        digest = hashlib.sha256(raw).digest()
        prior = self.db.execute("SELECT digest FROM events WHERE id=?", (event_id,)).fetchone()
        if prior:
            if prior[0] != digest:
                raise SnapshotError("PARENT_IDENTITY_CONFLICT")
            return
        self._payload_budget(size)
        self.parent_bytes += size
        self.db.execute("INSERT INTO events VALUES (?,?,?,?)", (event_id, zlib.compress(raw, 1), size, digest))

    def market(self, payload: dict, event: dict | None = None) -> None:
        # The full original parent membership is shared once on disk and in RAM.
        event_id = payload["event_id"]
        body = dict(payload)
        body["raw"] = {k: v for k, v in body["raw"].items() if k != "_event"}
        raw = encoded(body)
        size = len(raw)
        self.max_market_bytes = max(self.max_market_bytes, size)
        if size > MAX_ROW_BYTES:
            raise SnapshotError("ROW_SIZE_CAP", observed_bytes=size, limit_bytes=MAX_ROW_BYTES)
        if event is not None:
            self.event(event_id, event)
        self._payload_budget(size)
        self.market_bytes += size
        self.db.execute("INSERT INTO markets VALUES (?,?,?,?)", (payload["id"], event_id, zlib.compress(raw, 1), size))
        self.materialized += 1
        if self.materialized > MATERIALIZED_CAP:
            raise SnapshotError("MATERIALIZED_CAP", count=self.materialized, limit=MATERIALIZED_CAP)

    def page(self, *, cursor_in, cursor_out, receipt: float, seconds: float, response_sha256: str, events: int) -> None:
        self.pages += 1
        self.events_count += events
        if self.pages > 5000:
            raise SnapshotError("PAGE_CAP")
        if cursor_out is not None and (cursor_out == cursor_in or self.db.execute(
            "SELECT 1 FROM pages WHERE cursor_out=?", (cursor_out,)
        ).fetchone()):
            raise SnapshotError("CURSOR_REPEAT")
        self.db.execute("INSERT INTO pages VALUES (?,?,?,?,?,?)", (self.pages, cursor_in, cursor_out, receipt, seconds, response_sha256))
        self.db.commit()
        if self.path.stat().st_size > MAX_FILE_BYTES:
            raise SnapshotError("GENERATION_FILE_CAP", observed_bytes=self.path.stat().st_size, limit_bytes=MAX_FILE_BYTES)

    def publish(self, *, finished_at: float | None = None, before_publish=None) -> dict:
        last = self.db.execute("SELECT cursor_out FROM pages ORDER BY number DESC LIMIT 1").fetchone()
        if not last or last[0] is not None or self.discovered == 0 or self.materialized == 0:
            raise SnapshotError("INCOMPLETE_EXHAUSTION")
        finish = time.time() if finished_at is None else finished_at
        elapsed = finish - self.manifest["started_at"]
        if not 0 <= elapsed <= BUILD_DEADLINE_SECONDS:
            raise SnapshotError("BUILD_DEADLINE")
        timing = self.db.execute("SELECT avg(seconds),max(seconds),sum(seconds>=2),min(receipt),max(receipt) FROM pages").fetchone()
        self.manifest.update({
            "complete": True, "natural_exhaustion": True, "finished_at": finish,
            "build_seconds": elapsed, "keyset_pages": self.pages, "event_observations": self.events_count,
            "inventory_market_count": self.inventory_count, "discovered_market_count": self.discovered,
            "materialized_market_count": self.materialized,
            "decoded_payload_bytes": self.decoded_bytes, "parent_payload_bytes": self.parent_bytes,
            "market_payload_bytes": self.market_bytes, "max_parent_bytes": self.max_parent_bytes,
            "max_market_bytes": self.max_market_bytes,
            "page_average_seconds": timing[0], "page_max_seconds": timing[1], "slow_pages": timing[2],
            "first_page_received_at": timing[3], "last_page_received_at": timing[4],
            "recall_scope": "detector_subset_at_observation_time_not_all_market_opportunities",
        })
        self.db.execute("INSERT INTO metadata VALUES (?)", (encoded(self.manifest).decode(),))
        # This PRIVATE scratch database is disposable after any error/crash. No
        # per-page rollback journal or durability is needed. Publication still
        # requires all writes closed + fsync(file) + fsync(dir) before the pointer.
        self.db.commit()
        if self.db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise SnapshotError("SQLITE_INTEGRITY")
        self.db.close()
        self.closed = True
        if self.path.stat().st_size > MAX_FILE_BYTES:
            raise SnapshotError("GENERATION_FILE_CAP")
        with self.path.open("rb") as stream:
            os.fsync(stream.fileno())
        target = self.directory / self.name
        os.replace(self.path, target)
        fsync_directory(self.directory)
        pointer = {"file": self.name, "sequence": self.sequence, "sha256": file_hash(target)}
        # Verify the same independent reader contract before the commit point.
        db, _ = open_generation(self.directory, pointer, producer_sha=self.manifest["producer_sha"],
                                filter_version=self.manifest["filter_version"], now=finish)
        db.close()
        if before_publish is not None:
            before_publish()
        # Hashing, fsync and validation are synchronous: an asyncio timeout alone
        # cannot enforce their deadline. Check again at the publication boundary.
        if (time.monotonic() - self.started_monotonic > BUILD_DEADLINE_SECONDS
                or (finished_at is None and generation_age(self.manifest) > BUILD_DEADLINE_SECONDS)):
            raise SnapshotError("PUBLICATION_DEADLINE")
        pointer["published_at"] = time.time() if finished_at is None else finish
        atomic_json(self.directory / "current.json", pointer)
        self.published = True
        self.prune()
        return self.manifest

    def prune(self) -> None:
        accepted = current_pointer(self.directory)
        files = sorted(p for p in self.directory.glob("g-*.sqlite")
                       if _NAME.fullmatch(p.name) and p.name != accepted["file"])
        for path in files[:max(0, len(files) - KEEP_GENERATIONS + 1)]:
            path.unlink(missing_ok=True)
        fsync_directory(self.directory)

    def abort(self) -> None:
        if not self.closed:
            self.db.close()
            self.closed = True
        self.path.unlink(missing_ok=True)
        Path(str(self.path) + "-journal").unlink(missing_ok=True)
        if not self.published:
            # A post-rename validation/publication failure must not leak files.
            pointer = current_pointer(self.directory)
            if pointer is None or pointer["file"] != self.name:
                (self.directory / self.name).unlink(missing_ok=True)


def open_generation(directory: Path, pointer: dict, *, producer_sha: str, filter_version: str,
                    now: float | None = None) -> tuple[sqlite3.Connection, dict]:
    name = str(pointer.get("file", ""))
    if not _NAME.fullmatch(name):
        raise SnapshotError("GENERATION_PATH")
    path = directory / name
    if path.is_symlink() or path.stat().st_size > MAX_FILE_BYTES or file_hash(path) != pointer.get("sha256"):
        raise SnapshotError("GENERATION_CHECKSUM")
    db = sqlite3.connect(path.resolve().as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        db.execute("PRAGMA cache_size=-4096")
        db.execute("PRAGMA query_only=ON")
        rows = db.execute("SELECT value FROM metadata").fetchall()
        if len(rows) != 1:
            raise SnapshotError("MANIFEST_INVALID")
        manifest = json.loads(rows[0][0])
        expected = {"version": SNAPSHOT_VERSION, "producer_sha": producer_sha,
                    "filter_version": filter_version, "sequence": pointer.get("sequence"),
                    "generation_id": name[:-7], "complete": True, "natural_exhaustion": True,
                    "payload_encoding": "zlib_level_1_canonical_json_lossless",
                    "query": {"active": True, "closed": False, "limit": GAMMA_PAGE_SIZE}}
        if any(type(manifest.get(key)) is not type(value) or manifest.get(key) != value
               for key, value in expected.items()):
            raise SnapshotError("GENERATION_POLICY")
        if generation_age(manifest, now=now) >= UNIVERSE_MAX_AGE_SECONDS:
            raise SnapshotError("GENERATION_STALE")
        finish = manifest.get("finished_at", float("inf"))
        current = time.time() if now is None else now
        if not manifest["started_at"] <= finish <= current or finish - manifest["started_at"] > BUILD_DEADLINE_SECONDS:
            raise SnapshotError("OBSERVATION_INTERVAL")
        checks = (("markets", "materialized_market_count", MATERIALIZED_CAP),
                  ("inventory", "inventory_market_count", INVENTORY_CAP), ("pages", "keyset_pages", 5000))
        for table, key, cap in checks:
            count = db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            if not 0 < count <= cap or type(manifest.get(key)) is not int or count != manifest.get(key):
                raise SnapshotError("LEDGER_MISMATCH")
        decoded = 0
        for table, cap in (("events", MAX_PARENT_BYTES), ("markets", MAX_ROW_BYTES)):
            amount, largest, smallest = db.execute(f"SELECT sum(raw_bytes),max(raw_bytes),min(raw_bytes) FROM {table}").fetchone()
            if not amount or not 0 < smallest <= largest <= cap:
                raise SnapshotError("PAYLOAD_INVALID")
            decoded += amount
        if decoded > MAX_DECODED_BYTES or decoded != manifest.get("decoded_payload_bytes"):
            raise SnapshotError("DECODED_SIZE_CAP", observed_bytes=decoded, limit_bytes=MAX_DECODED_BYTES)
        active = db.execute("SELECT count(*) FROM inventory WHERE active=1").fetchone()[0]
        selected = db.execute("SELECT count(*) FROM inventory WHERE selected=1").fetchone()[0]
        if not 0 < active <= DISCOVERY_CAP or active != manifest.get("discovered_market_count") or selected != manifest["materialized_market_count"]:
            raise SnapshotError("DISCOVERY_LEDGER_MISMATCH")
        previous, seen_cursors = None, set()
        for expected_number, (number, cin, cout, receipt) in enumerate(db.execute(
                "SELECT number,cursor_in,cursor_out,receipt FROM pages ORDER BY number"), start=1):
            if number != expected_number or cin != previous or (number < manifest["keyset_pages"] and not cout):
                raise SnapshotError("CURSOR_CHAIN")
            if cout is not None:
                if not isinstance(cout, str) or not cout.strip() or cout in seen_cursors:
                    raise SnapshotError("CURSOR_REPEAT")
                seen_cursors.add(cout)
            if not manifest["started_at"] <= receipt <= finish:
                raise SnapshotError("PAGE_OBSERVATION_INTERVAL")
            previous = cout
        if previous is not None or db.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise SnapshotError("SQLITE_INTEGRITY")
        return db, manifest
    except BaseException:
        db.close()
        raise
