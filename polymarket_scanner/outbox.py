from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from .models import Signal


class TelegramOutbox:
    """Small persistent queue shared by scanner and Telegram command worker.

    The scanner only inserts signal IDs. The standalone command worker owns
    Telegram networking and marks rows delivered. SQLite makes queued alerts
    survive either process restarting.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._init()

    def _conn(self):
        c = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(
                """
                CREATE TABLE IF NOT EXISTS telegram_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER NOT NULL UNIQUE,
                    priority INTEGER NOT NULL DEFAULT 10,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    attempts INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at REAL NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    sent_at REAL,
                    FOREIGN KEY(signal_id) REFERENCES signals(id)
                );
                CREATE INDEX IF NOT EXISTS idx_telegram_outbox_pending
                    ON telegram_outbox(status, priority, next_attempt_at, id);
                """
            )

    def enqueue_signal(self, signal_id: int, priority: int) -> bool:
        with self._lock, self._conn() as c:
            cur = c.execute(
                """
                INSERT OR IGNORE INTO telegram_outbox(
                    signal_id, priority, status, attempts, next_attempt_at, created_at
                ) VALUES(?, ?, 'PENDING', 0, 0, ?)
                """,
                (int(signal_id), int(priority), time.time()),
            )
            return bool(cur.rowcount)

    def pending_count(self) -> int:
        with self._conn() as c:
            return int(
                c.execute(
                    "SELECT COUNT(*) FROM telegram_outbox WHERE status='PENDING'"
                ).fetchone()[0]
            )

    def next_due(self) -> dict | None:
        now = time.time()
        with self._conn() as c:
            row = c.execute(
                """
                SELECT * FROM telegram_outbox
                WHERE status='PENDING' AND next_attempt_at <= ?
                ORDER BY priority ASC, id ASC
                LIMIT 1
                """,
                (now,),
            ).fetchone()
            return dict(row) if row else None

    def mark_sent(self, outbox_id: int) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                """
                UPDATE telegram_outbox
                SET status='SENT', sent_at=?, last_error=NULL
                WHERE id=?
                """,
                (time.time(), int(outbox_id)),
            )

    def mark_failed(self, outbox_id: int, error: str) -> None:
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT attempts FROM telegram_outbox WHERE id=?",
                (int(outbox_id),),
            ).fetchone()
            attempts = int(row["attempts"] if row else 0) + 1
            delay = min(60.0, 2.0 ** min(attempts, 6))
            c.execute(
                """
                UPDATE telegram_outbox
                SET attempts=?, next_attempt_at=?, last_error=?
                WHERE id=?
                """,
                (attempts, time.time() + delay, str(error)[:1000], int(outbox_id)),
            )

    @staticmethod
    def signal_from_row(row: dict) -> Signal:
        token_ids = json.loads(row.get("token_ids") or "[]")
        metadata = json.loads(row.get("metadata") or "{}")
        created = datetime.fromisoformat(str(row["created_at"]))
        return Signal(
            detector=str(row.get("detector") or ""),
            confidence=str(row.get("confidence") or "WATCH"),
            event_id=str(row.get("event_id") or ""),
            market_id=row.get("market_id"),
            title=str(row.get("title") or ""),
            detail=str(row.get("detail") or ""),
            url=str(row.get("url") or ""),
            edge=row.get("edge"),
            entry_cost=row.get("entry_cost"),
            theoretical_payout=row.get("theoretical_payout"),
            token_ids=list(token_ids) if isinstance(token_ids, list) else [],
            metadata=dict(metadata) if isinstance(metadata, dict) else {},
            created_at=created,
        )
