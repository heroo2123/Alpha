from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from .execution_certificate import EXECUTION_CERTIFICATE_VERSION
from .store import STRUCTURAL_DETECTORS
from .trade_only import TRADE_READY_VERSION

STRUCTURAL_ENTRY_SOURCE = "USER_REPORTED_PER_LEG_EXECUTION_V1"
STRUCTURAL_RESOLVED_STATUS = "RESOLVED"


def _d(value: object, *, name: str) -> Decimal:
    if value is None or isinstance(value, bool):
        raise ValueError(f"{name} is missing/invalid")
    try:
        out = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not out.is_finite():
        raise ValueError(f"{name} must be finite")
    return out


def _utc_text(value: datetime | None = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("execution time must be timezone-aware")
    return current.astimezone(timezone.utc).isoformat()


def ensure_structural_fill_schema(store) -> None:
    """Create the prospective per-leg ledger without rewriting legacy rows."""
    with store._lock, store._conn() as c:
        c.executescript(
            """
            CREATE TABLE IF NOT EXISTS manual_structural_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                signal_id INTEGER NOT NULL UNIQUE,
                detector TEXT NOT NULL,
                shares REAL NOT NULL,
                total_cash_cost REAL NOT NULL,
                bundle_cost REAL NOT NULL,
                entry_source TEXT NOT NULL,
                certificate_version TEXT NOT NULL,
                trade_ready_version TEXT NOT NULL,
                within_cert_limits INTEGER NOT NULL,
                within_cert_capacity INTEGER NOT NULL,
                execution_at TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'OPEN',
                total_payout_per_bundle REAL,
                pnl REAL,
                resolved_at TEXT,
                FOREIGN KEY(signal_id) REFERENCES signals(id)
            );
            CREATE INDEX IF NOT EXISTS idx_manual_structural_status
                ON manual_structural_trades(status);
            CREATE TABLE IF NOT EXISTS manual_structural_legs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id INTEGER NOT NULL,
                leg_index INTEGER NOT NULL,
                market_id TEXT NOT NULL,
                condition_id TEXT NOT NULL,
                token_id TEXT NOT NULL,
                outcome TEXT NOT NULL,
                shares REAL NOT NULL,
                avg_price REAL NOT NULL,
                fee_usd REAL NOT NULL,
                cash_cost REAL NOT NULL,
                certified_limit REAL NOT NULL,
                certified_safe_depth REAL NOT NULL,
                within_cert_limit INTEGER NOT NULL,
                payout_per_share REAL,
                filled_at TEXT NOT NULL,
                FOREIGN KEY(trade_id) REFERENCES manual_structural_trades(id),
                UNIQUE(trade_id, leg_index),
                UNIQUE(trade_id, token_id)
            );
            CREATE INDEX IF NOT EXISTS idx_manual_structural_leg_trade
                ON manual_structural_legs(trade_id);
            """
        )


def _signal_execution_context(row: sqlite3.Row | dict) -> tuple[str, dict, list[dict]]:
    detector = str(row["detector"] or "")
    if detector not in STRUCTURAL_DETECTORS:
        raise ValueError("per-leg structural fills are only for structural detectors")
    if str(row["confidence"] or "") != "ACTIONABLE":
        raise ValueError("unknown/non-actionable structural alert id")
    if str(row["status"] or "") != "OPEN" or row["resolved_at"] is not None:
        raise ValueError("cannot record structural fills after the source alert is no longer open")

    try:
        metadata = json.loads(row["metadata"] or "{}")
    except Exception as exc:
        raise ValueError("stored alert metadata is unreadable") from exc
    if not isinstance(metadata, dict):
        raise ValueError("stored alert metadata is malformed")
    if metadata.get("trade_ready") is not True or metadata.get("trade_ready_version") != TRADE_READY_VERSION:
        raise ValueError("structural alert does not carry the current TRADE NOW readiness version")

    cert = metadata.get("execution_certificate")
    if not isinstance(cert, dict) or cert.get("version") != EXECUTION_CERTIFICATE_VERSION:
        raise ValueError("structural alert does not carry the current exact execution certificate")
    legs = cert.get("legs")
    if not isinstance(legs, list) or len(legs) < 2 or any(not isinstance(x, dict) for x in legs):
        raise ValueError("stored execution certificate has no exact structural leg vector")

    try:
        signal_tokens = [str(x) for x in json.loads(row["token_ids"] or "[]")]
    except Exception as exc:
        raise ValueError("stored purchased token vector is unreadable") from exc
    cert_tokens = [str(x.get("token_id") or "") for x in legs]
    if signal_tokens != cert_tokens or not all(cert_tokens) or len(set(cert_tokens)) != len(cert_tokens):
        raise ValueError("stored certificate leg vector does not match purchased tokens")
    return detector, cert, legs


def record_structural_fills(
    store,
    signal_id: int,
    fills: list[dict[str, Any]],
    *,
    execution_at: datetime | None = None,
) -> dict:
    """Atomically record every required structural leg from user-reported fills.

    ``fills`` identifies legs by the 1-based leg number printed in TRADE NOW. Token,
    market and outcome identities are taken only from the persisted v5 certificate.
    Every expected leg must appear exactly once and all legs must have exactly the
    same filled share count. Total cash cost and bundle cost are derived from actual
    average fill prices plus actual fees; alert prices are never used as realized P&L.

    Limit/capacity conformance is recorded but a nonconforming real fill is not
    discarded. It remains personal execution history while being excluded from the
    strategy-conforming evidence totals.
    """
    if not isinstance(signal_id, int) or signal_id <= 0:
        raise ValueError("alert id must be a positive integer")
    if not isinstance(fills, list) or not fills:
        raise ValueError("per-leg fills are required")
    execution_text = _utc_text(execution_at)
    recorded_text = _utc_text()

    ensure_structural_fill_schema(store)
    with store._lock, store._conn() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            row = c.execute("SELECT * FROM signals WHERE id=?", (signal_id,)).fetchone()
            if row is None:
                raise ValueError("unknown/non-actionable structural alert id")
            detector, cert, expected_legs = _signal_execution_context(row)

            existing = c.execute(
                "SELECT id FROM manual_structural_trades WHERE signal_id=?",
                (signal_id,),
            ).fetchone()
            if existing:
                raise ValueError("per-leg structural execution is already recorded for this alert")

            by_index: dict[int, dict[str, Any]] = {}
            for fill in fills:
                if not isinstance(fill, dict):
                    raise ValueError("each fill must be an object")
                try:
                    index = int(fill.get("leg"))
                except (TypeError, ValueError):
                    raise ValueError("each fill needs a valid 1-based leg number")
                if index in by_index:
                    raise ValueError("each structural leg may be recorded only once")
                by_index[index] = fill

            required = set(range(1, len(expected_legs) + 1))
            if set(by_index) != required:
                raise ValueError(f"exactly legs 1..{len(expected_legs)} must be supplied")

            normalized: list[dict[str, Any]] = []
            common_shares: Decimal | None = None
            total_cash = Decimal("0")
            within_limits = True
            for index, cert_leg in enumerate(expected_legs, start=1):
                fill = by_index[index]
                shares = _d(fill.get("shares"), name=f"leg {index} shares")
                avg_price = _d(fill.get("avg_price"), name=f"leg {index} average price")
                fee = _d(fill.get("fee_usd", 0), name=f"leg {index} fee")
                if shares <= 0:
                    raise ValueError("filled shares must be positive")
                if avg_price <= 0 or avg_price >= 1:
                    raise ValueError("actual average fill price must be between 0 and 1")
                if fee < 0:
                    raise ValueError("actual fee cannot be negative")
                if common_shares is None:
                    common_shares = shares
                elif shares != common_shares:
                    raise ValueError("every structural leg must have exactly the same filled share count")

                limit = _d(cert_leg.get("safe_limit"), name=f"leg {index} certified limit")
                safe_depth = _d(cert_leg.get("safe_depth_to_limit"), name=f"leg {index} certified safe depth")
                if limit <= 0 or limit >= 1 or safe_depth <= 0:
                    raise ValueError("stored structural certificate limit/depth is invalid")
                within = avg_price <= limit
                within_limits = within_limits and within
                cash = shares * avg_price + fee
                total_cash += cash
                normalized.append({
                    "leg_index": index,
                    "market_id": str(cert_leg.get("market_id") or ""),
                    "condition_id": str(cert_leg.get("condition_id") or ""),
                    "token_id": str(cert_leg.get("token_id") or ""),
                    "outcome": str(cert_leg.get("outcome") or ""),
                    "shares": shares,
                    "avg_price": avg_price,
                    "fee_usd": fee,
                    "cash_cost": cash,
                    "certified_limit": limit,
                    "certified_safe_depth": safe_depth,
                    "within_cert_limit": within,
                })

            assert common_shares is not None
            if total_cash <= 0:
                raise ValueError("derived structural cash cost is invalid")
            safe_common = _d(cert.get("safe_common_shares"), name="certificate safe common shares")
            within_capacity = common_shares <= safe_common
            bundle_cost = total_cash / common_shares

            cur = c.execute(
                """
                INSERT INTO manual_structural_trades(
                    signal_id,detector,shares,total_cash_cost,bundle_cost,entry_source,
                    certificate_version,trade_ready_version,within_cert_limits,
                    within_cert_capacity,execution_at,recorded_at,status
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?, 'OPEN')
                """,
                (
                    signal_id,
                    detector,
                    float(common_shares),
                    float(total_cash),
                    float(bundle_cost),
                    STRUCTURAL_ENTRY_SOURCE,
                    str(cert.get("version")),
                    TRADE_READY_VERSION,
                    int(within_limits),
                    int(within_capacity),
                    execution_text,
                    recorded_text,
                ),
            )
            trade_id = int(cur.lastrowid)
            for leg in normalized:
                if not all((leg["market_id"], leg["condition_id"], leg["token_id"], leg["outcome"])):
                    raise ValueError("stored structural leg identity is incomplete")
                c.execute(
                    """
                    INSERT INTO manual_structural_legs(
                        trade_id,leg_index,market_id,condition_id,token_id,outcome,
                        shares,avg_price,fee_usd,cash_cost,certified_limit,
                        certified_safe_depth,within_cert_limit,payout_per_share,filled_at
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,?)
                    """,
                    (
                        trade_id,
                        leg["leg_index"],
                        leg["market_id"],
                        leg["condition_id"],
                        leg["token_id"],
                        leg["outcome"],
                        float(leg["shares"]),
                        float(leg["avg_price"]),
                        float(leg["fee_usd"]),
                        float(leg["cash_cost"]),
                        float(leg["certified_limit"]),
                        float(leg["certified_safe_depth"]),
                        int(leg["within_cert_limit"]),
                        execution_text,
                    ),
                )
            c.commit()
        except Exception:
            c.rollback()
            raise

    return {
        "id": trade_id,
        "signal_id": signal_id,
        "detector": detector,
        "shares": float(common_shares),
        "total_cash_cost": float(total_cash),
        "bundle_cost": float(bundle_cost),
        "within_cert_limits": bool(within_limits),
        "within_cert_capacity": bool(within_capacity),
        "entry_source": STRUCTURAL_ENTRY_SOURCE,
        "status": "OPEN",
        "legs": [
            {
                "leg": leg["leg_index"],
                "token_id": leg["token_id"],
                "market_id": leg["market_id"],
                "outcome": leg["outcome"],
                "shares": float(leg["shares"]),
                "avg_price": float(leg["avg_price"]),
                "fee_usd": float(leg["fee_usd"]),
            }
            for leg in normalized
        ],
    }


def open_structural_trades(store, after_id: int = 0) -> list[dict]:
    ensure_structural_fill_schema(store)
    with store._conn() as c:
        trades = [dict(row) for row in c.execute(
            "SELECT * FROM manual_structural_trades WHERE status='OPEN' AND id>? ORDER BY id LIMIT 64", (after_id,)
        )]
        for trade in trades:
            trade["legs"] = [dict(row) for row in c.execute(
                "SELECT * FROM manual_structural_legs WHERE trade_id=? ORDER BY leg_index",
                (trade["id"],),
            )]
    return trades


def resolve_structural_trade(store, trade_id: int, payouts_by_token: dict[str, float]) -> dict | None:
    """Settle one complete structural execution from exact final token payouts."""
    ensure_structural_fill_schema(store)
    if not isinstance(payouts_by_token, dict) or not payouts_by_token:
        raise ValueError("exact token payouts are required")

    normalized: dict[str, Decimal] = {}
    for token, raw in payouts_by_token.items():
        payout = _d(raw, name=f"payout for token {token}")
        if payout < 0 or payout > 1:
            raise ValueError("token payout must be between 0 and 1")
        normalized[str(token)] = payout

    with store._lock, store._conn() as c:
        c.execute("BEGIN IMMEDIATE")
        try:
            trade = c.execute(
                "SELECT * FROM manual_structural_trades WHERE id=?",
                (trade_id,),
            ).fetchone()
            if trade is None or str(trade["status"] or "") != "OPEN":
                c.rollback()
                return None
            legs = c.execute(
                "SELECT * FROM manual_structural_legs WHERE trade_id=? ORDER BY leg_index",
                (trade_id,),
            ).fetchall()
            tokens = [str(row["token_id"]) for row in legs]
            if not legs or set(tokens) != set(normalized) or len(tokens) != len(normalized):
                raise ValueError("settlement payout vector does not exactly match recorded structural legs")

            total_payout = sum((normalized[token] for token in tokens), Decimal("0"))
            shares = _d(trade["shares"], name="recorded shares")
            cash = _d(trade["total_cash_cost"], name="recorded total cash cost")
            pnl = shares * total_payout - cash
            now = _utc_text()
            for row in legs:
                token = str(row["token_id"])
                c.execute(
                    "UPDATE manual_structural_legs SET payout_per_share=? WHERE id=?",
                    (float(normalized[token]), int(row["id"])),
                )
            c.execute(
                """
                UPDATE manual_structural_trades
                SET status=?,total_payout_per_bundle=?,pnl=?,resolved_at=?
                WHERE id=?
                """,
                (STRUCTURAL_RESOLVED_STATUS, float(total_payout), float(pnl), now, trade_id),
            )
            c.commit()
        except Exception:
            c.rollback()
            raise

    return {
        "id": trade_id,
        "status": STRUCTURAL_RESOLVED_STATUS,
        "total_payout_per_bundle": float(total_payout),
        "pnl": float(pnl),
    }


def structural_manual_stats(store) -> dict:
    """Audit prospective per-leg structural executions separately from legacy `/took`."""
    ensure_structural_fill_schema(store)
    with store._conn() as c:
        rows = [dict(row) for row in c.execute(
            "SELECT * FROM manual_structural_trades ORDER BY id"
        )]
    conforming = [
        row for row in rows
        if bool(row.get("within_cert_limits")) and bool(row.get("within_cert_capacity"))
    ]
    resolved = [row for row in conforming if row.get("status") == STRUCTURAL_RESOLVED_STATUS]
    return {
        "total_recorded": len(rows),
        "conforming_total": len(conforming),
        "open": sum(row.get("status") == "OPEN" for row in conforming),
        "resolved": len(resolved),
        "cash_cost": float(sum(float(row.get("total_cash_cost") or 0) for row in conforming)),
        "pnl": float(sum(float(row.get("pnl") or 0) for row in resolved)),
        "nonconforming_excluded": len(rows) - len(conforming),
        "evidence_class": STRUCTURAL_ENTRY_SOURCE,
        "exchange_verified": False,
    }
