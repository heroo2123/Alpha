from __future__ import annotations

"""Bounded, secret-free SQLite history for operational shadow-run evidence."""

import json
import math
import threading
import time

SHADOW_TELEMETRY_VERSION = "shadow_history_v3_builder_failure_codes_pressure_60s_14d"
SHADOW_SAMPLE_SECONDS = 60.0
SHADOW_RETENTION_DAYS = 14
SHADOW_MAX_ROWS = int(SHADOW_RETENTION_DAYS * 86400 / SHADOW_SAMPLE_SECONDS)

_THROTTLE_LOCK = threading.Lock()
_last_sample_monotonic: float | None = None


def _finite(value: object) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _integer(value: object) -> int | None:
    if isinstance(value, bool):
        return int(value)
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def _mapping(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _safe_payload(snapshot: dict) -> dict:
    """Whitelisted evidence only; never persist arbitrary health/error/config text."""
    resources = _mapping(snapshot.get("runtime_resources"))
    feeds = _mapping(snapshot.get("feed_progress"))
    market = _mapping(feeds.get("market_clob"))
    sports = _mapping(feeds.get("sports"))
    crypto = _mapping(feeds.get("crypto_rtds"))
    price = _mapping(snapshot.get("price_discovery_authority"))
    manifest = _mapping(snapshot.get("runtime_manifest"))
    universe = _mapping(snapshot.get("universe_authority"))
    builder = _mapping(universe.get("builder"))
    from .universe_failures import MESSAGES
    cgroup = _mapping(builder.get("cgroup"))

    return {
        "version": SHADOW_TELEMETRY_VERSION,
        "release_sha": manifest.get("authorized_release_sha"),
        "runtime_authority_complete": bool(snapshot.get("production_runtime_authority_complete")),
        "p0_containment": bool(snapshot.get("p0_containment")),
        "universe": {
            "generation_id": universe.get("generation_id"),
            "complete": universe.get("complete") is True,
            "age_seconds": _finite(universe.get("age_seconds")),
            "discovered_market_count": _integer(universe.get("discovered_market_count")),
            "materialized_market_count": _integer(universe.get("materialized_market_count")),
            "keyset_pages": _integer(universe.get("keyset_pages")),
            "builder_state": builder.get("state") if builder.get("state") in {"BUILDING", "PUBLISHED", "FAILED", "UNKNOWN"} else None,
            "builder_elapsed_seconds": _finite(builder.get("elapsed_seconds")),
            "builder_pages": _integer(builder.get("keyset_pages")),
            "builder_rss_bytes": _integer(builder.get("process_rss_bytes")),
            "builder_swap_bytes": _integer(builder.get("process_swap_bytes")),
            "builder_failure_code": builder.get("failure_code") if builder.get("failure_code") in MESSAGES else None,
            "builder_file_bytes": _integer(builder.get("generation_file_bytes")),
            "builder_max_page_bytes": _integer(builder.get("max_page_bytes")),
            "builder_decoded_payload_bytes": _integer(builder.get("decoded_payload_bytes")),
            "builder_cpu_seconds": _finite(builder.get("process_cpu_seconds")),
            "builder_cgroup_memory_bytes": _integer(cgroup.get("memory.current")),
            "builder_reclaim_high_events": _integer(_mapping(cgroup.get("memory.events")).get("high")),
            "builder_reclaim_scanned_pages": _integer(_mapping(cgroup.get("memory.stat")).get("pgscan")),
        },
        "resources": {
            "process_rss_bytes": _integer(resources.get("process_rss_bytes")),
            "process_virtual_bytes": _integer(resources.get("process_virtual_bytes")),
            "process_swap_bytes": _integer(resources.get("process_swap_bytes")),
            "process_threads": _integer(resources.get("process_threads")),
            "open_file_descriptors": _integer(resources.get("open_file_descriptors")),
            "logical_cpu_count": _integer(resources.get("logical_cpu_count")),
            "load_average_1m": _finite(resources.get("load_average_1m")),
            "load_average_5m": _finite(resources.get("load_average_5m")),
            "load_average_15m": _finite(resources.get("load_average_15m")),
            "disk_free_bytes": _integer(resources.get("disk_free_bytes")),
            "database_bytes": _integer(resources.get("database_bytes")),
            "database_wal_bytes": _integer(resources.get("database_wal_bytes")),
            "database_shm_bytes": _integer(resources.get("database_shm_bytes")),
        },
        "feeds": {
            "market_connected_workers": _integer(market.get("connected_workers")),
            "market_cached_books": _integer(market.get("cached_synchronized_books")),
            "market_valid_age_seconds": _finite(market.get("last_valid_book_update_age_seconds")),
            "market_full_book_age_seconds": _finite(market.get("last_full_book_age_seconds")),
            "market_invalidated_total": _integer(market.get("books_invalidated_total")),
            "market_out_of_order_total": _integer(market.get("out_of_order_ignored_total")),
            "sports_connected": bool(sports.get("connected")),
            "sports_cached_payloads": _integer(sports.get("cached_result_payloads")),
            "sports_source_age_seconds": _finite(sports.get("latest_source_age_seconds")),
            "sports_causal_quarantined_slugs": _integer(sports.get("causal_quarantined_slugs")),
            "crypto_connected": bool(crypto.get("connected")),
            "crypto_valid_progress_now": bool(crypto.get("valid_progress_now")),
            "crypto_valid_update_age_seconds": _finite(crypto.get("last_valid_update_age_seconds")),
            "crypto_invalid_rows_total": _integer(crypto.get("invalid_rows_total")),
            "crypto_out_of_order_total": _integer(crypto.get("out_of_order_ignored_total")),
            "crypto_conflict_total": _integer(crypto.get("conflicting_timestamp_rows_total")),
        },
        "scanner": {
            "universe_safe_for_detection": bool(snapshot.get("universe_safe_for_detection")),
            "price_snapshot_age_seconds": _finite(price.get("snapshot_age_seconds")),
            "price_usable_coverage_ratio": _finite(price.get("usable_coverage_ratio")),
            "signal_batches_pending": _integer(snapshot.get("signal_batches_pending")),
            "candidate_overflow_total": _integer(snapshot.get("candidate_overflow_total")),
            "candidate_expired_total": _integer(snapshot.get("candidate_expired_total")),
            "last_compute_seconds": _finite(snapshot.get("last_compute_seconds")),
            "gamma_missing_or_stale_tokens": _integer(price.get("missing_or_stale_tokens")),
            "gamma_quote_time_known": False,
            "scan_in_progress": bool(snapshot.get("scan_in_progress")),
            "settlement_in_progress": bool(snapshot.get("settlement_in_progress")),
        },
    }


def ensure_shadow_telemetry_schema(store) -> None:
    with store._lock, store._conn() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS runtime_shadow_samples (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sampled_at REAL NOT NULL,
                release_sha TEXT,
                runtime_authority_complete INTEGER NOT NULL,
                process_rss_bytes INTEGER,
                process_swap_bytes INTEGER,
                load_average_1m REAL,
                disk_free_bytes INTEGER,
                market_valid_age_seconds REAL,
                sports_source_age_seconds REAL,
                crypto_valid_update_age_seconds REAL,
                price_snapshot_age_seconds REAL,
                price_usable_coverage_ratio REAL,
                signal_batches_pending INTEGER,
                payload_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_runtime_shadow_samples_time
                ON runtime_shadow_samples(sampled_at);
            """
        )


def record_shadow_sample(
    store,
    snapshot: dict,
    *,
    sampled_at: float | None = None,
    retention_days: int = SHADOW_RETENTION_DAYS,
    max_rows: int = SHADOW_MAX_ROWS,
) -> dict:
    """Persist one whitelisted sample and prune history by age and absolute row cap."""
    if not isinstance(snapshot, dict):
        raise ValueError("shadow telemetry snapshot must be a mapping")
    now = time.time() if sampled_at is None else float(sampled_at)
    if not math.isfinite(now) or now <= 0:
        raise ValueError("shadow telemetry sample time must be positive and finite")
    retention_seconds = max(1, int(retention_days)) * 86400.0
    cap = max(60, int(max_rows))
    payload = _safe_payload(snapshot)
    resources = payload["resources"]
    feeds = payload["feeds"]
    scanner = payload["scanner"]
    serialized = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False)

    ensure_shadow_telemetry_schema(store)
    with store._lock, store._conn() as connection:
        cursor = connection.execute(
            """
            INSERT INTO runtime_shadow_samples(
                sampled_at,release_sha,runtime_authority_complete,process_rss_bytes,
                process_swap_bytes,load_average_1m,disk_free_bytes,
                market_valid_age_seconds,sports_source_age_seconds,
                crypto_valid_update_age_seconds,price_snapshot_age_seconds,
                price_usable_coverage_ratio,signal_batches_pending,payload_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                now,
                payload.get("release_sha"),
                int(payload["runtime_authority_complete"]),
                resources.get("process_rss_bytes"),
                resources.get("process_swap_bytes"),
                resources.get("load_average_1m"),
                resources.get("disk_free_bytes"),
                feeds.get("market_valid_age_seconds"),
                feeds.get("sports_source_age_seconds"),
                feeds.get("crypto_valid_update_age_seconds"),
                scanner.get("price_snapshot_age_seconds"),
                scanner.get("price_usable_coverage_ratio"),
                scanner.get("signal_batches_pending"),
                serialized,
            ),
        )
        sample_id = int(cursor.lastrowid)
        connection.execute(
            "DELETE FROM runtime_shadow_samples WHERE sampled_at < ?",
            (now - retention_seconds,),
        )
        connection.execute(
            """
            DELETE FROM runtime_shadow_samples
            WHERE id NOT IN (
                SELECT id FROM runtime_shadow_samples ORDER BY id DESC LIMIT ?
            )
            """,
            (cap,),
        )
        row = connection.execute(
            "SELECT COUNT(*), MIN(sampled_at), MAX(sampled_at) FROM runtime_shadow_samples"
        ).fetchone()

    return {
        "version": SHADOW_TELEMETRY_VERSION,
        "sample_id": sample_id,
        "sampled_at": now,
        "rows_retained": int(row[0]),
        "oldest_sample_at": float(row[1]) if row[1] is not None else None,
        "latest_sample_at": float(row[2]) if row[2] is not None else None,
        "retention_days": max(1, int(retention_days)),
        "max_rows": cap,
        "sample_interval_seconds": SHADOW_SAMPLE_SECONDS,
        "payload_scope": "WHITELISTED_SECRET_FREE_OPERATIONAL_EVIDENCE",
    }


def maybe_record_shadow_health_state(
    store,
    value: str,
    *,
    monotonic_now: float | None = None,
    sampled_at: float | None = None,
) -> dict | None:
    """Sample the persisted trade-only health record at most once per minute.

    The normal health snapshot is written every ~2 seconds in ``asyncio.to_thread``.
    This hook runs in that same worker thread after the latest-state transaction has
    released the Store lock. It refuses to sample until the production schema gate is
    visible as compatible, so startup/migration transients never become shadow proof.
    """
    global _last_sample_monotonic
    try:
        snapshot = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None
    if not isinstance(snapshot, dict):
        return None
    database_schema = _mapping(snapshot.get("database_schema"))
    if database_schema.get("compatible") is not True:
        return None

    current_mono = time.monotonic() if monotonic_now is None else float(monotonic_now)
    if not math.isfinite(current_mono) or current_mono < 0:
        return None
    with _THROTTLE_LOCK:
        if (
            _last_sample_monotonic is not None
            and current_mono - _last_sample_monotonic < SHADOW_SAMPLE_SECONDS
        ):
            return None
        result = record_shadow_sample(store, snapshot, sampled_at=sampled_at)
        _last_sample_monotonic = current_mono
        return result


def _reset_shadow_throttle_for_tests() -> None:
    global _last_sample_monotonic
    with _THROTTLE_LOCK:
        _last_sample_monotonic = None


def recent_shadow_samples(store, *, limit: int = 120) -> list[dict]:
    """Read recent samples for audit/reporting without exposing arbitrary app state."""
    ensure_shadow_telemetry_schema(store)
    bounded = max(1, min(20_000, int(limit)))
    with store._conn() as connection:
        rows = connection.execute(
            "SELECT * FROM runtime_shadow_samples ORDER BY id DESC LIMIT ?",
            (bounded,),
        ).fetchall()
    return [dict(row) for row in rows]
