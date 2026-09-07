from __future__ import annotations

import json
import sqlite3
import time
from datetime import datetime, timezone

from .models import Signal


def _episode_key(signal: Signal) -> str:
    raw = signal.metadata.get("fingerprint_key") or signal.market_id or signal.event_id
    return str(raw or "").strip()


def _dedupe_window_seconds(signal: Signal) -> float:
    try:
        raw = float(signal.metadata.get("fingerprint_bucket_seconds", 900))
    except (TypeError, ValueError, OverflowError):
        raw = 900.0
    # A financial episode should never be allowed to use a vanishing cooldown.
    return max(60.0, min(86400.0, raw))


def _parse_created_at(value: object) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(timezone.utc)


def _recent_trade_episode_exists(connection, signal: Signal) -> bool:
    """Rolling dedupe independent of fixed fingerprint bucket boundaries.

    The old fingerprint floor bucket allowed the same opportunity to persist twice
    when two detections only milliseconds apart straddled the bucket boundary. For a
    money alert, inspect recently persisted *trade-ready* episodes with the same
    detector/key inside the requested cooldown before inserting anything.
    """
    key = _episode_key(signal)
    if not key:
        return True  # no stable episode identity: financial persistence fails closed
    candidate_at = signal.created_at.astimezone(timezone.utc)
    window = _dedupe_window_seconds(signal)

    rows = connection.execute(
        """
        SELECT metadata,market_id,event_id,created_at
        FROM signals
        WHERE detector=? AND confidence='ACTIONABLE'
        ORDER BY id DESC LIMIT 200
        """,
        (signal.detector,),
    ).fetchall()
    for row in rows:
        try:
            meta = json.loads(row["metadata"] or "{}")
        except Exception:
            continue
        if not isinstance(meta, dict) or meta.get("trade_ready") is not True:
            continue
        other_key = str(meta.get("fingerprint_key") or row["market_id"] or row["event_id"] or "").strip()
        if other_key != key:
            continue
        other_at = _parse_created_at(row["created_at"])
        if other_at is None:
            # Same financial episode with an unparseable persisted time is unsafe to
            # duplicate automatically; require operator/audit intervention instead.
            return True
        age = (candidate_at - other_at).total_seconds()
        if age < 0 or age < window:
            return True
    return False


def persist_trade_now_intent(store, signal: Signal, priority: int = 0) -> int | None:
    """Insert a certified signal and its durable Telegram intent atomically.

    This function is deliberately narrow: it accepts only a signal that already
    carries the production trade-ready certificate. The transaction either commits
    both rows or neither row. A database/outbox failure therefore cannot leave a
    fingerprinted signal behind with no possible delivery intent.

    The command worker still revalidates the market and certificate immediately
    before sending. Persistence here is durable intent, not permission to trade.
    Rolling episode dedupe is checked in the same IMMEDIATE transaction, avoiding
    the fixed-time-bucket boundary duplicate demonstrated in the adversarial review.
    """
    if signal.confidence != "ACTIONABLE" or signal.metadata.get("trade_ready") is not True:
        raise ValueError("only a freshly trade-ready ACTIONABLE may create a TRADE NOW intent")
    if int(priority) != 0:
        raise ValueError("TRADE NOW intents must use priority 0")
    if signal.created_at.tzinfo is None:
        raise ValueError("TRADE NOW candidate creation time must be timezone-aware")

    with store._lock, store._conn() as connection:  # owned Store transaction boundary
        try:
            connection.execute("BEGIN IMMEDIATE")
            if _recent_trade_episode_exists(connection, signal):
                connection.rollback()
                return None

            cursor = connection.execute(
                """
                INSERT INTO signals(
                    fingerprint,detector,confidence,event_id,market_id,title,detail,url,
                    edge,entry_cost,theoretical_payout,token_ids,metadata,created_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    signal.fingerprint(),
                    signal.detector,
                    signal.confidence,
                    signal.event_id,
                    signal.market_id,
                    signal.title,
                    signal.detail,
                    signal.url,
                    signal.edge,
                    signal.entry_cost,
                    signal.theoretical_payout,
                    json.dumps(signal.token_ids),
                    json.dumps(signal.metadata),
                    signal.created_at.isoformat(),
                ),
            )
            signal_id = int(cursor.lastrowid)
            connection.execute(
                """
                INSERT INTO telegram_outbox(
                    signal_id,priority,status,attempts,next_attempt_at,last_error,
                    created_at,sent_at,claimed_at
                ) VALUES(?,0,'PENDING',0,0,NULL,?,NULL,NULL)
                """,
                (signal_id, time.time()),
            )
            connection.commit()
            return signal_id
        except sqlite3.IntegrityError:
            connection.rollback()
            # A duplicate exact fingerprint is normal deduplication. Critically, no
            # half-created signal/outbox state is committed.
            return None
        except Exception:
            connection.rollback()
            raise
