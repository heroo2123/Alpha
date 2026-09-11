from __future__ import annotations

"""Read-only containment evidence for weather W7 silent-shadow acceptance.

The W7 recorder must prove containment rather than write convenient zero counters.
This module therefore reads the existing scanner SQLite database in ``mode=ro`` and
binds complete state digests for the three financially relevant legacy tables used by
the repository: Telegram outbox rows, signal rows, and manual-trade rows.  It also
binds the Linux scanner process identity and attests that the weather runtime/CLOB
classes expose only their frozen read-only method surfaces.

No constructor from Store/TelegramOutbox is used because those constructors run DDL.
No service control, network call, Telegram API, order API, or database mutation exists
here.
"""

import hashlib
import inspect
import json
import math
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

from .weather_only_clob import WeatherCLOBClient
from .weather_only_runtime import WeatherOnlyShadowRuntime


WEATHER_W7_CONTAINMENT_VERSION = "weather_w7_containment_v1_ro_sqlite_proc_identity_readonly_surface"
_SHA64_RE = re.compile(r"^[0-9a-f]{64}$")

_REQUIRED_COLUMNS = {
    "telegram_outbox": frozenset({"id", "signal_id", "status", "attempts", "created_at"}),
    "signals": frozenset({"id", "detector", "confidence", "created_at"}),
    "manual_trades": frozenset({"id", "signal_id", "status", "created_at"}),
}
_EXPECTED_CLOB_METHODS = frozenset({"__init__", "close", "market_info", "market_infos", "books", "exact_event_snapshot"})
_EXPECTED_RUNTIME_METHODS = frozenset({"__init__", "close", "_prescreen_books", "run_cycle"})


class WeatherW7ContainmentError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    except (TypeError, ValueError):
        raise WeatherW7ContainmentError("W7_CONTAINMENT_JSON_INVALID") from None


def _sha(value: object) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _sha64(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if not _SHA64_RE.fullmatch(text):
        raise WeatherW7ContainmentError(code)
    return text


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherW7ContainmentError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherW7ContainmentError(code)
    return number


def _positive_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise WeatherW7ContainmentError(code)
    return value


def _row_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float)):
        if isinstance(value, float) and not math.isfinite(value):
            raise WeatherW7ContainmentError("W7_CONTAINMENT_DB_NONFINITE_VALUE")
        return value
    if isinstance(value, bytes):
        return {"blob_sha256": hashlib.sha256(value).hexdigest(), "length": len(value)}
    raise WeatherW7ContainmentError("W7_CONTAINMENT_DB_VALUE_TYPE_UNSUPPORTED")


def _class_method_names(cls: type) -> frozenset[str]:
    return frozenset(
        name
        for name, value in vars(cls).items()
        if callable(value) and (name == "__init__" or not (name.startswith("__") and name.endswith("__")))
    )


@dataclass(frozen=True, slots=True)
class WeatherW7ReadOnlySurfaceAttestation:
    version: str
    clob_methods: tuple[str, ...]
    runtime_methods: tuple[str, ...]
    clob_source_sha256: str
    runtime_source_sha256: str
    evidence_sha256: str
    order_api_exposed: bool = field(init=False, default=False)
    financial_delivery_api_exposed: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def _surface_payload(row: WeatherW7ReadOnlySurfaceAttestation) -> dict:
    value = row.as_dict()
    value.pop("evidence_sha256", None)
    return value


def attest_weather_w7_read_only_surface() -> WeatherW7ReadOnlySurfaceAttestation:
    clob_methods = _class_method_names(WeatherCLOBClient)
    runtime_methods = _class_method_names(WeatherOnlyShadowRuntime)
    if clob_methods != _EXPECTED_CLOB_METHODS:
        raise WeatherW7ContainmentError("W7_CONTAINMENT_CLOB_METHOD_SURFACE_DRIFT")
    if runtime_methods != _EXPECTED_RUNTIME_METHODS:
        raise WeatherW7ContainmentError("W7_CONTAINMENT_RUNTIME_METHOD_SURFACE_DRIFT")
    try:
        clob_source = inspect.getsource(WeatherCLOBClient)
        runtime_source = inspect.getsource(WeatherOnlyShadowRuntime)
    except (OSError, TypeError):
        raise WeatherW7ContainmentError("W7_CONTAINMENT_SOURCE_ATTESTATION_FAILED") from None
    shell = WeatherW7ReadOnlySurfaceAttestation(
        version=WEATHER_W7_CONTAINMENT_VERSION,
        clob_methods=tuple(sorted(clob_methods)),
        runtime_methods=tuple(sorted(runtime_methods)),
        clob_source_sha256=hashlib.sha256(clob_source.encode("utf-8")).hexdigest(),
        runtime_source_sha256=hashlib.sha256(runtime_source.encode("utf-8")).hexdigest(),
        evidence_sha256="0" * 64,
    )
    return WeatherW7ReadOnlySurfaceAttestation(
        version=shell.version,
        clob_methods=shell.clob_methods,
        runtime_methods=shell.runtime_methods,
        clob_source_sha256=shell.clob_source_sha256,
        runtime_source_sha256=shell.runtime_source_sha256,
        evidence_sha256=_sha(_surface_payload(shell)),
    )


def validate_weather_w7_read_only_surface(row: object) -> WeatherW7ReadOnlySurfaceAttestation:
    if not isinstance(row, WeatherW7ReadOnlySurfaceAttestation):
        raise WeatherW7ContainmentError("W7_CONTAINMENT_SURFACE_TYPE_INVALID")
    live = attest_weather_w7_read_only_surface()
    if row != live:
        raise WeatherW7ContainmentError("W7_CONTAINMENT_SURFACE_ATTESTATION_MISMATCH")
    return row


@dataclass(frozen=True, slots=True)
class WeatherW7ProcessIdentity:
    version: str
    process_id: int
    boot_id_sha256: str
    start_time_ticks: int
    cmdline_sha256: str
    evidence_sha256: str
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def _process_payload(row: WeatherW7ProcessIdentity) -> dict:
    value = row.as_dict()
    value.pop("evidence_sha256", None)
    return value


def parse_linux_process_identity(
    *,
    process_id: int,
    stat_text: str,
    boot_id_text: str,
    cmdline_bytes: bytes,
) -> WeatherW7ProcessIdentity:
    pid = _positive_int(process_id, "W7_PROCESS_ID_INVALID")
    if not isinstance(stat_text, str) or not stat_text.strip():
        raise WeatherW7ContainmentError("W7_PROCESS_STAT_INVALID")
    first = stat_text.find("(")
    last = stat_text.rfind(")")
    if first <= 0 or last <= first or last + 2 >= len(stat_text):
        raise WeatherW7ContainmentError("W7_PROCESS_STAT_INVALID")
    try:
        stat_pid = int(stat_text[:first].strip())
    except ValueError:
        raise WeatherW7ContainmentError("W7_PROCESS_STAT_INVALID") from None
    if stat_pid != pid:
        raise WeatherW7ContainmentError("W7_PROCESS_STAT_PID_MISMATCH")
    fields = stat_text[last + 2 :].split()
    # Suffix begins at proc stat field 3 (state); field 22 starttime is index 19.
    if len(fields) <= 19:
        raise WeatherW7ContainmentError("W7_PROCESS_STAT_INVALID")
    try:
        start_ticks = int(fields[19], 10)
    except ValueError:
        raise WeatherW7ContainmentError("W7_PROCESS_START_TIME_INVALID") from None
    if start_ticks <= 0:
        raise WeatherW7ContainmentError("W7_PROCESS_START_TIME_INVALID")
    boot = str(boot_id_text or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f-]{32,64}", boot):
        raise WeatherW7ContainmentError("W7_PROCESS_BOOT_ID_INVALID")
    if not isinstance(cmdline_bytes, bytes) or not cmdline_bytes:
        raise WeatherW7ContainmentError("W7_PROCESS_CMDLINE_INVALID")
    shell = WeatherW7ProcessIdentity(
        version=WEATHER_W7_CONTAINMENT_VERSION,
        process_id=pid,
        boot_id_sha256=hashlib.sha256(boot.encode("ascii")).hexdigest(),
        start_time_ticks=start_ticks,
        cmdline_sha256=hashlib.sha256(cmdline_bytes).hexdigest(),
        evidence_sha256="0" * 64,
    )
    return WeatherW7ProcessIdentity(
        version=shell.version,
        process_id=shell.process_id,
        boot_id_sha256=shell.boot_id_sha256,
        start_time_ticks=shell.start_time_ticks,
        cmdline_sha256=shell.cmdline_sha256,
        evidence_sha256=_sha(_process_payload(shell)),
    )


def validate_weather_w7_process_identity(row: object) -> WeatherW7ProcessIdentity:
    if not isinstance(row, WeatherW7ProcessIdentity) or row.version != WEATHER_W7_CONTAINMENT_VERSION:
        raise WeatherW7ContainmentError("W7_PROCESS_IDENTITY_TYPE_OR_VERSION_INVALID")
    _positive_int(row.process_id, "W7_PROCESS_ID_INVALID")
    if not isinstance(row.start_time_ticks, int) or isinstance(row.start_time_ticks, bool) or row.start_time_ticks <= 0:
        raise WeatherW7ContainmentError("W7_PROCESS_START_TIME_INVALID")
    _sha64(row.boot_id_sha256, "W7_PROCESS_BOOT_SHA_INVALID")
    _sha64(row.cmdline_sha256, "W7_PROCESS_CMDLINE_SHA_INVALID")
    supplied = _sha64(row.evidence_sha256, "W7_PROCESS_EVIDENCE_SHA_INVALID")
    if supplied != _sha(_process_payload(row)):
        raise WeatherW7ContainmentError("W7_PROCESS_EVIDENCE_DIGEST_MISMATCH")
    if row.financial_authority is not False:
        raise WeatherW7ContainmentError("W7_CONTAINMENT_AUTHORITY_BOUNDARY_BROKEN")
    return row


def read_linux_process_identity(
    process_id: int,
    *,
    proc_root: str | Path = "/proc",
) -> WeatherW7ProcessIdentity:
    pid = _positive_int(process_id, "W7_PROCESS_ID_INVALID")
    root = Path(proc_root)
    try:
        stat_text = (root / str(pid) / "stat").read_text(encoding="utf-8")
        boot_text = (root / "sys" / "kernel" / "random" / "boot_id").read_text(encoding="ascii")
        cmdline = (root / str(pid) / "cmdline").read_bytes()
    except (OSError, UnicodeError):
        raise WeatherW7ContainmentError("W7_PROCESS_IDENTITY_READ_FAILED") from None
    return parse_linux_process_identity(
        process_id=pid,
        stat_text=stat_text,
        boot_id_text=boot_text,
        cmdline_bytes=cmdline,
    )


@dataclass(frozen=True, slots=True)
class WeatherW7DatabaseSnapshot:
    version: str
    captured_at: float
    database_identity_sha256: str
    telegram_outbox_rows: int
    signals_rows: int
    manual_trades_rows: int
    telegram_outbox_state_sha256: str
    signals_state_sha256: str
    manual_trades_state_sha256: str
    schema_evidence_sha256: str
    evidence_sha256: str
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def _database_payload(row: WeatherW7DatabaseSnapshot) -> dict:
    value = row.as_dict()
    value.pop("evidence_sha256", None)
    return value


def _table_digest(conn: sqlite3.Connection, table: str, columns: tuple[str, ...]) -> tuple[int, str]:
    quoted_columns = ",".join(f'"{name}"' for name in columns)
    digest = hashlib.sha256()
    count = 0
    try:
        cursor = conn.execute(f'SELECT {quoted_columns} FROM "{table}" ORDER BY "id" ASC')
        for raw in cursor:
            count += 1
            values = [_row_value(value) for value in raw]
            digest.update(_canonical(values).encode("utf-8"))
            digest.update(b"\n")
    except sqlite3.Error:
        raise WeatherW7ContainmentError("W7_CONTAINMENT_DB_READ_FAILED") from None
    return count, digest.hexdigest()


def read_weather_w7_database_snapshot(
    database_path: str | Path,
    *,
    captured_at: float | None = None,
) -> WeatherW7DatabaseSnapshot:
    path = Path(database_path)
    if path.is_symlink():
        raise WeatherW7ContainmentError("W7_CONTAINMENT_DB_PATH_INVALID")
    try:
        stat = path.stat()
    except OSError:
        raise WeatherW7ContainmentError("W7_CONTAINMENT_DB_PATH_INVALID") from None
    if not path.is_file():
        raise WeatherW7ContainmentError("W7_CONTAINMENT_DB_PATH_INVALID")
    captured = time.time() if captured_at is None else _finite(captured_at, "W7_CONTAINMENT_CAPTURE_TIME_INVALID")
    database_identity = _sha({"device": stat.st_dev, "inode": stat.st_ino})

    uri = path.resolve().as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error:
        raise WeatherW7ContainmentError("W7_CONTAINMENT_DB_OPEN_FAILED") from None
    try:
        conn.row_factory = sqlite3.Row
        schema: dict[str, tuple[str, ...]] = {}
        for table, required in _REQUIRED_COLUMNS.items():
            try:
                rows = conn.execute(f'PRAGMA table_info("{table}")').fetchall()
            except sqlite3.Error:
                raise WeatherW7ContainmentError("W7_CONTAINMENT_DB_SCHEMA_READ_FAILED") from None
            columns = tuple(str(row[1]) for row in rows)
            if not required.issubset(columns) or "id" not in columns:
                raise WeatherW7ContainmentError(f"W7_CONTAINMENT_DB_SCHEMA_INVALID:{table}")
            schema[table] = columns

        outbox_count, outbox_sha = _table_digest(conn, "telegram_outbox", schema["telegram_outbox"])
        signal_count, signal_sha = _table_digest(conn, "signals", schema["signals"])
        manual_count, manual_sha = _table_digest(conn, "manual_trades", schema["manual_trades"])
        schema_sha = _sha({table: columns for table, columns in sorted(schema.items())})
    finally:
        conn.close()

    shell = WeatherW7DatabaseSnapshot(
        version=WEATHER_W7_CONTAINMENT_VERSION,
        captured_at=captured,
        database_identity_sha256=database_identity,
        telegram_outbox_rows=outbox_count,
        signals_rows=signal_count,
        manual_trades_rows=manual_count,
        telegram_outbox_state_sha256=outbox_sha,
        signals_state_sha256=signal_sha,
        manual_trades_state_sha256=manual_sha,
        schema_evidence_sha256=schema_sha,
        evidence_sha256="0" * 64,
    )
    return WeatherW7DatabaseSnapshot(
        version=shell.version,
        captured_at=shell.captured_at,
        database_identity_sha256=shell.database_identity_sha256,
        telegram_outbox_rows=shell.telegram_outbox_rows,
        signals_rows=shell.signals_rows,
        manual_trades_rows=shell.manual_trades_rows,
        telegram_outbox_state_sha256=shell.telegram_outbox_state_sha256,
        signals_state_sha256=shell.signals_state_sha256,
        manual_trades_state_sha256=shell.manual_trades_state_sha256,
        schema_evidence_sha256=shell.schema_evidence_sha256,
        evidence_sha256=_sha(_database_payload(shell)),
    )


def validate_weather_w7_database_snapshot(row: object) -> WeatherW7DatabaseSnapshot:
    if not isinstance(row, WeatherW7DatabaseSnapshot) or row.version != WEATHER_W7_CONTAINMENT_VERSION:
        raise WeatherW7ContainmentError("W7_CONTAINMENT_DB_SNAPSHOT_TYPE_OR_VERSION_INVALID")
    _finite(row.captured_at, "W7_CONTAINMENT_CAPTURE_TIME_INVALID")
    for value in (row.telegram_outbox_rows, row.signals_rows, row.manual_trades_rows):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise WeatherW7ContainmentError("W7_CONTAINMENT_DB_COUNT_INVALID")
    for value in (
        row.database_identity_sha256,
        row.telegram_outbox_state_sha256,
        row.signals_state_sha256,
        row.manual_trades_state_sha256,
        row.schema_evidence_sha256,
    ):
        _sha64(value, "W7_CONTAINMENT_DB_SHA_INVALID")
    if _sha64(row.evidence_sha256, "W7_CONTAINMENT_DB_EVIDENCE_SHA_INVALID") != _sha(_database_payload(row)):
        raise WeatherW7ContainmentError("W7_CONTAINMENT_DB_EVIDENCE_DIGEST_MISMATCH")
    if row.financial_authority is not False:
        raise WeatherW7ContainmentError("W7_CONTAINMENT_AUTHORITY_BOUNDARY_BROKEN")
    return row


@dataclass(frozen=True, slots=True)
class WeatherW7ContainmentManifest:
    version: str
    before_database: WeatherW7DatabaseSnapshot
    after_database: WeatherW7DatabaseSnapshot
    before_process: WeatherW7ProcessIdentity
    after_process: WeatherW7ProcessIdentity
    read_only_surface: WeatherW7ReadOnlySurfaceAttestation
    evidence_sha256: str
    financial_authority: bool = field(init=False, default=False)
    financial_delivery: bool = field(init=False, default=False)
    automatic_order_placement: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "before_database": self.before_database.as_dict(),
            "after_database": self.after_database.as_dict(),
            "before_process": self.before_process.as_dict(),
            "after_process": self.after_process.as_dict(),
            "read_only_surface": self.read_only_surface.as_dict(),
            "evidence_sha256": self.evidence_sha256,
            "financial_authority": False,
            "financial_delivery": False,
            "automatic_order_placement": False,
        }


def _manifest_payload(row: WeatherW7ContainmentManifest) -> dict:
    value = row.as_dict()
    value.pop("evidence_sha256", None)
    return value


def build_weather_w7_containment_manifest(
    *,
    before_database: WeatherW7DatabaseSnapshot,
    after_database: WeatherW7DatabaseSnapshot,
    before_process: WeatherW7ProcessIdentity,
    after_process: WeatherW7ProcessIdentity,
    read_only_surface: WeatherW7ReadOnlySurfaceAttestation | None = None,
) -> WeatherW7ContainmentManifest:
    before_db = validate_weather_w7_database_snapshot(before_database)
    after_db = validate_weather_w7_database_snapshot(after_database)
    before_proc = validate_weather_w7_process_identity(before_process)
    after_proc = validate_weather_w7_process_identity(after_process)
    surface = validate_weather_w7_read_only_surface(read_only_surface or attest_weather_w7_read_only_surface())
    if after_db.captured_at < before_db.captured_at:
        raise WeatherW7ContainmentError("W7_CONTAINMENT_CAPTURE_TIME_ORDER_INVALID")
    shell = WeatherW7ContainmentManifest(
        version=WEATHER_W7_CONTAINMENT_VERSION,
        before_database=before_db,
        after_database=after_db,
        before_process=before_proc,
        after_process=after_proc,
        read_only_surface=surface,
        evidence_sha256="0" * 64,
    )
    final = WeatherW7ContainmentManifest(
        version=shell.version,
        before_database=shell.before_database,
        after_database=shell.after_database,
        before_process=shell.before_process,
        after_process=shell.after_process,
        read_only_surface=shell.read_only_surface,
        evidence_sha256=_sha(_manifest_payload(shell)),
    )
    return validate_weather_w7_containment_manifest(final)


def validate_weather_w7_containment_manifest(row: object) -> WeatherW7ContainmentManifest:
    if not isinstance(row, WeatherW7ContainmentManifest) or row.version != WEATHER_W7_CONTAINMENT_VERSION:
        raise WeatherW7ContainmentError("W7_CONTAINMENT_MANIFEST_TYPE_OR_VERSION_INVALID")
    validate_weather_w7_database_snapshot(row.before_database)
    validate_weather_w7_database_snapshot(row.after_database)
    validate_weather_w7_process_identity(row.before_process)
    validate_weather_w7_process_identity(row.after_process)
    validate_weather_w7_read_only_surface(row.read_only_surface)
    if row.after_database.captured_at < row.before_database.captured_at:
        raise WeatherW7ContainmentError("W7_CONTAINMENT_CAPTURE_TIME_ORDER_INVALID")
    if _sha64(row.evidence_sha256, "W7_CONTAINMENT_MANIFEST_SHA_INVALID") != _sha(_manifest_payload(row)):
        raise WeatherW7ContainmentError("W7_CONTAINMENT_MANIFEST_DIGEST_MISMATCH")
    if any((row.financial_authority is not False, row.financial_delivery is not False, row.automatic_order_placement is not False)):
        raise WeatherW7ContainmentError("W7_CONTAINMENT_AUTHORITY_BOUNDARY_BROKEN")
    return row


def weather_w7_containment_reasons(row: WeatherW7ContainmentManifest) -> tuple[str, ...]:
    manifest = validate_weather_w7_containment_manifest(row)
    before, after = manifest.before_database, manifest.after_database
    reasons: list[str] = []
    if before.database_identity_sha256 != after.database_identity_sha256:
        reasons.append("CONTAINMENT_DATABASE_IDENTITY_CHANGED")
    if before.schema_evidence_sha256 != after.schema_evidence_sha256:
        reasons.append("CONTAINMENT_DATABASE_SCHEMA_CHANGED")
    if before.telegram_outbox_state_sha256 != after.telegram_outbox_state_sha256:
        reasons.append("TELEGRAM_OUTBOX_STATE_CHANGED")
    if before.signals_state_sha256 != after.signals_state_sha256:
        reasons.append("SIGNAL_REGISTRY_STATE_CHANGED")
    if before.manual_trades_state_sha256 != after.manual_trades_state_sha256:
        reasons.append("MANUAL_TRADE_STATE_CHANGED")
    if (
        manifest.before_process.process_id != manifest.after_process.process_id
        or manifest.before_process.boot_id_sha256 != manifest.after_process.boot_id_sha256
        or manifest.before_process.start_time_ticks != manifest.after_process.start_time_ticks
        or manifest.before_process.cmdline_sha256 != manifest.after_process.cmdline_sha256
    ):
        reasons.append("SCANNER_PROCESS_IDENTITY_CHANGED")
    return tuple(reasons)


def derive_weather_w7_containment_counters(row: WeatherW7ContainmentManifest) -> dict[str, int]:
    manifest = validate_weather_w7_containment_manifest(row)
    reasons = set(weather_w7_containment_reasons(manifest))
    return {
        "telegram_outbox_before": manifest.before_database.telegram_outbox_rows,
        "telegram_outbox_after": manifest.after_database.telegram_outbox_rows,
        "detector_promotions": 1 if "SIGNAL_REGISTRY_STATE_CHANGED" in reasons else 0,
        "order_attempts": 0,
        "actual_orders_placed": 0,
        "actual_fills_recorded": 1 if "MANUAL_TRADE_STATE_CHANGED" in reasons else 0,
        "service_restart_count": 1 if "SCANNER_PROCESS_IDENTITY_CHANGED" in reasons else 0,
    }
