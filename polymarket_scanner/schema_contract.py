from __future__ import annotations

"""Versioned SQLite schema contract used by runtime/release attestation.

This module does not mutate databases. It describes the minimum production schema
that the current scanner, command worker, delivery outbox and per-leg accounting
code require, and can attest an existing SQLite file read-only.
"""

import sqlite3
from pathlib import Path

DATABASE_SCHEMA_VERSION = "alpha_sqlite_v1_exact_payout_outbox_structural_per_leg"

REQUIRED_TABLE_COLUMNS: dict[str, set[str]] = {
    "signals": {
        "id", "fingerprint", "detector", "confidence", "event_id", "market_id",
        "edge", "entry_cost", "theoretical_payout", "token_ids", "metadata",
        "status", "pnl", "settlement_payout", "created_at", "resolved_at",
    },
    "manual_trades": {
        "id", "signal_id", "stake", "entry_cost", "entry_source", "execution_at",
        "status", "pnl", "settlement_payout", "created_at", "resolved_at",
    },
    "bot_state": {"key", "value"},
    "telegram_outbox": {
        "id", "signal_id", "priority", "status", "attempts", "next_attempt_at",
        "last_error", "created_at", "sent_at", "claimed_at",
    },
    "manual_structural_trades": {
        "id", "signal_id", "detector", "shares", "total_cash_cost", "bundle_cost",
        "entry_source", "certificate_version", "trade_ready_version",
        "within_cert_limits", "within_cert_capacity", "execution_at", "recorded_at",
        "status", "total_payout_per_bundle", "pnl", "resolved_at",
    },
    "manual_structural_legs": {
        "id", "trade_id", "leg_index", "market_id", "condition_id", "token_id",
        "outcome", "shares", "avg_price", "fee_usd", "cash_cost",
        "certified_limit", "certified_safe_depth", "within_cert_limit",
        "payout_per_share", "filled_at",
    },
}


def attest_database_schema(path: str | Path) -> dict:
    """Read-only compatibility check against the current production schema contract."""
    target = Path(path).expanduser().resolve()
    base = {
        "version": DATABASE_SCHEMA_VERSION,
        "path": str(target),
        "exists": target.is_file(),
        "compatible": False,
        "missing_tables": [],
        "missing_columns": {},
    }
    if not target.is_file():
        base["reason"] = "database file missing"
        return base

    try:
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=5.0)
        try:
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            missing_tables = sorted(set(REQUIRED_TABLE_COLUMNS) - tables)
            missing_columns: dict[str, list[str]] = {}
            for table, required in REQUIRED_TABLE_COLUMNS.items():
                if table not in tables:
                    continue
                columns = {
                    str(row[1])
                    for row in connection.execute(f'PRAGMA table_info("{table}")')
                }
                missing = sorted(required - columns)
                if missing:
                    missing_columns[table] = missing
        finally:
            connection.close()
    except sqlite3.Error as exc:
        base["reason"] = f"schema inspection failed: {type(exc).__name__}: {exc}"
        return base

    compatible = not missing_tables and not missing_columns
    base.update(
        {
            "compatible": compatible,
            "missing_tables": missing_tables,
            "missing_columns": missing_columns,
            "reason": (
                "database satisfies the current production schema contract"
                if compatible
                else "database is missing required production schema elements"
            ),
        }
    )
    return base


def require_database_schema(path: str | Path) -> dict:
    """Return the attestation or abort startup for an incompatible database.

    Production schema creation/migrations run before this guard. Reaching this
    function with missing tables/columns therefore means the running database does
    not match the code's persistence contract and must not be treated as healthy.
    """
    attestation = attest_database_schema(path)
    if attestation.get("compatible") is True:
        return attestation

    missing_tables = list(attestation.get("missing_tables") or [])
    missing_columns = dict(attestation.get("missing_columns") or {})
    details: list[str] = []
    if missing_tables:
        details.append(f"missing tables={missing_tables}")
    if missing_columns:
        details.append(f"missing columns={missing_columns}")
    if not details:
        details.append(str(attestation.get("reason") or "unknown schema incompatibility"))
    raise RuntimeError(
        f"production database schema incompatible with {DATABASE_SCHEMA_VERSION}: "
        + "; ".join(details)
    )
