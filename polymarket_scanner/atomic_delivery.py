from __future__ import annotations

import json
import sqlite3
import time

from .models import Signal


def persist_trade_now_intent(store, signal: Signal, priority: int = 0) -> int | None:
    """Insert a certified signal and its durable Telegram intent atomically.

    This function is deliberately narrow: it accepts only a signal that already
    carries the production trade-ready certificate. The transaction either commits
    both rows or neither row. A database/outbox failure therefore cannot leave a
    fingerprinted signal behind with no possible delivery intent.

    The command worker still revalidates the market and certificate immediately
    before sending. Persistence here is durable intent, not permission to trade.
    """
    if signal.confidence != "ACTIONABLE" or signal.metadata.get("trade_ready") is not True:
        raise ValueError("only a freshly trade-ready ACTIONABLE may create a TRADE NOW intent")
    if int(priority) != 0:
        raise ValueError("TRADE NOW intents must use priority 0")

    with store._lock, store._conn() as connection:  # owned Store transaction boundary
        try:
            connection.execute("BEGIN IMMEDIATE")
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
            # A duplicate fingerprint is normal deduplication. Critically, no
            # half-created signal/outbox state is committed.
            return None
        except Exception:
            connection.rollback()
            raise
