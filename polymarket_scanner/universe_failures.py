"""Closed diagnostic vocabulary. Never serialize exception text or remote data."""
from __future__ import annotations

import errno
import json
import math
import sqlite3
import subprocess

MESSAGES = {
    "METADATA_SIZE_CAP": "oversized snapshot metadata",
    "METADATA_INVALID": "snapshot metadata must be an object",
    "BUILDER_LOCKED": "another universe builder owns the lock",
    "GENERATION_PATH": "invalid generation filename or path",
    "GENERATION_SEQUENCE": "invalid generation sequence",
    "GENERATION_IDENTITY": "generation filename/sequence mismatch",
    "GENERATION_CHECKSUM": "generation file size/path/checksum failure",
    "RELEASE_MISMATCH": "immutable producer release changed or is not attested",
    "RELEASE_LOOKUP_TIMEOUT": "startup release lookup timed out",
    "DUPLICATE_IDENTITY_CONFLICT": "conflicting duplicate market in complete traversal",
    "PARENT_IDENTITY_CONFLICT": "conflicting original parent membership",
    "DISCOVERY_CAP": "complete discovery exceeds hard cap; nothing published",
    "INVENTORY_CAP": "complete inventory exceeds hard cap; nothing published",
    "MATERIALIZED_CAP": "materialized subset exceeds hard cap; nothing published",
    "ROW_SIZE_CAP": "materialized row exceeds size bound",
    "PARENT_SIZE_CAP": "semantic parent exceeds size bound",
    "DECODED_SIZE_CAP": "total decoded generation payload exceeds size bound",
    "PAYLOAD_INVALID": "invalid bounded compressed snapshot payload",
    "PAGE_BYTES_CAP": "Gamma page exceeds decompressed byte bound",
    "SOURCE_JSON": "Gamma response is not valid JSON",
    "SOURCE_ENVELOPE": "malformed Gamma keyset envelope/events",
    "SOURCE_PAGE_COUNT": "Gamma page exceeds requested event bound",
    "CURSOR_MISSING": "full Gamma page missing continuation evidence",
    "CURSOR_INVALID": "malformed Gamma continuation cursor",
    "CURSOR_EMPTY_PAGE": "empty Gamma page with continuation",
    "CURSOR_REPEAT": "keyset cursor repeated before exhaustion",
    "CURSOR_CHAIN": "broken traversal continuation chain",
    "PAGE_CAP": "keyset page cap exceeded",
    "SOURCE_EVENT": "malformed Gamma event or missing child inventory",
    "SOURCE_MARKET": "malformed Gamma child market",
    "SOURCE_OPEN_STATE": "missing or ambiguous Gamma child open state",
    "MATERIALIZATION_MISMATCH": "eligible market could not be materialized losslessly",
    "GENERATION_FILE_CAP": "generation exceeds disk bound",
    "INCOMPLETE_EXHAUSTION": "no complete nonempty natural exhaustion proof",
    "BUILD_DEADLINE": "generation build exceeded deadline or clock regressed",
    "PUBLICATION_DEADLINE": "generation publication exceeded build deadline",
    "SQLITE_INTEGRITY": "generation SQLite integrity failure",
    "SQLITE_BUSY": "generation SQLite contention",
    "MANIFEST_INVALID": "missing or ambiguous generation manifest",
    "GENERATION_POLICY": "generation identity/schema/policy/completeness mismatch",
    "GENERATION_STALE": "generation is hard stale or clock is invalid",
    "OBSERVATION_INTERVAL": "invalid generation observation interval",
    "LEDGER_MISMATCH": "generation ledger count mismatch or cap exceeded",
    "DISCOVERY_LEDGER_MISMATCH": "discovery/materialization ledger mismatch",
    "PAGE_OBSERVATION_INTERVAL": "page receipt outside observation interval",
    "GENERATION_ROLLBACK": "generation rollback rejected",
    "MARKET_IDENTITY": "market/parent identity mismatch",
    "MARKET_OBSERVATION_INTERVAL": "market quote receipt outside generation interval",
    "MARKET_LIFECYCLE": "invalid materialized market identity/lifecycle",
    "TOKEN_OWNERSHIP": "duplicate token ownership in generation",
    "HTTP_STATUS": "Gamma HTTP status failure",
    "HTTP_TIMEOUT": "Gamma request timed out",
    "HTTP_TRANSPORT": "Gamma transport failed",
    "DISK_FULL": "generation storage is full",
    "IO_ERROR": "generation filesystem operation failed",
    "PROCESS_MEMORY_LIMIT": "process allocation failed",
    "CANCELLED": "build was cancelled",
    "INTERNAL_EXCEPTION": "unexpected local failure; category retained without exception text",
    "BOOTSTRAP_DEADLINE": "no ready complete generation within bootstrap deadline",
}


class SnapshotError(RuntimeError):
    def __init__(self, code: str, **metrics):
        if code not in MESSAGES:
            raise ValueError("unknown internal snapshot failure code")
        self.code = code
        self.metrics = {k: v for k, v in metrics.items()
                        if k in {"observed_bytes", "limit_bytes", "count", "limit", "page", "events", "children"}
                        and type(v) in (int, float) and math.isfinite(v) and 0 <= v <= 2**63 - 1}
        super().__init__(f"{code}: {MESSAGES[code]}")


def failure_record(exc: BaseException) -> dict:
    # Imports here keep SnapshotWriter's dependency footprint stdlib-only.
    import asyncio
    import httpx
    metrics = {}
    if isinstance(exc, SnapshotError):
        code, metrics = exc.code, exc.metrics
    elif isinstance(exc, subprocess.TimeoutExpired):
        code = "RELEASE_LOOKUP_TIMEOUT"
    elif isinstance(exc, httpx.HTTPStatusError):
        code = "HTTP_STATUS"
        metrics = {"http_status": int(exc.response.status_code)}
    elif isinstance(exc, httpx.TimeoutException):
        code = "HTTP_TIMEOUT"
    elif isinstance(exc, httpx.TransportError):
        code = "HTTP_TRANSPORT"
    elif isinstance(exc, json.JSONDecodeError):
        code = "SOURCE_JSON"
    elif isinstance(exc, sqlite3.DatabaseError):
        code = "SQLITE_BUSY" if getattr(exc, "sqlite_errorcode", 0) in (5, 6) else "SQLITE_INTEGRITY"
    elif isinstance(exc, TimeoutError):
        code = "BUILD_DEADLINE"
    elif isinstance(exc, MemoryError):
        code = "PROCESS_MEMORY_LIMIT"
    elif isinstance(exc, asyncio.CancelledError):
        code = "CANCELLED"
    elif isinstance(exc, OSError):
        code = "DISK_FULL" if exc.errno == errno.ENOSPC else "IO_ERROR"
    else:
        code = "INTERNAL_EXCEPTION"
    known_types = {"SnapshotError", "TimeoutExpired", "HTTPStatusError", "ReadTimeout", "ConnectTimeout",
                   "TimeoutError", "JSONDecodeError", "OperationalError", "DatabaseError", "OSError",
                   "MemoryError", "CancelledError", "RuntimeError", "ValueError", "SystemExit"}
    category = type(exc).__name__
    return {"failure_code": code, "failure_metrics": metrics,
            "error_type": category if category in known_types else "OtherException"}
