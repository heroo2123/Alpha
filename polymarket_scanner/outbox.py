from __future__ import annotations

import json
import sqlite3
import threading
import time
from datetime import datetime
from pathlib import Path

from .config import settings
from .models import Signal


class TelegramOutbox:
    """Persistent scanner -> Telegram queue with fail-closed delivery states.

    Production delivery is deliberately not an at-least-once queue. A money alert
    that may already have reached Telegram must never be retried blindly after a
    worker crash or ambiguous network failure, because a duplicate stale TRADE NOW
    instruction is worse than missing one opportunity. The lifecycle is therefore:

      PENDING -> SENDING -> SENT
                         -> SUPPRESSED   (policy / revalidation failed)
                         -> UNCERTAIN    (delivery may have happened; never auto-retry)
                SENDING -> PENDING      (only for a confirmed no-delivery failure)

    A separate atomic claim prevents two command workers from sending the same row.
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
                    claimed_at REAL,
                    FOREIGN KEY(signal_id) REFERENCES signals(id)
                );
                CREATE INDEX IF NOT EXISTS idx_telegram_outbox_pending
                    ON telegram_outbox(status, priority, next_attempt_at, id);
                """
            )
            # Existing production databases predate the claim column. SQLite has no
            # ADD COLUMN IF NOT EXISTS on all supported versions, so inspect first.
            cols = {str(row[1]) for row in c.execute("PRAGMA table_info(telegram_outbox)")}
            if "claimed_at" not in cols:
                c.execute("ALTER TABLE telegram_outbox ADD COLUMN claimed_at REAL")

    def enqueue_signal(self, signal_id: int, priority: int) -> bool:
        with self._lock, self._conn() as c:
            if int(priority) >= 10:
                pending_watch = int(
                    c.execute(
                        "SELECT COUNT(*) FROM telegram_outbox "
                        "WHERE status IN ('PENDING','SENDING') AND priority>=10"
                    ).fetchone()[0]
                )
                if pending_watch >= int(settings.telegram_watch_backlog_limit):
                    return False
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
                    "SELECT COUNT(*) FROM telegram_outbox WHERE status IN ('PENDING','SENDING')"
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

    def claim(self, outbox_id: int) -> bool:
        """Atomically reserve one due row for exactly one delivery worker."""
        now = time.time()
        with self._lock, self._conn() as c:
            cur = c.execute(
                """
                UPDATE telegram_outbox
                SET status='SENDING', claimed_at=?
                WHERE id=? AND status='PENDING' AND next_attempt_at <= ?
                """,
                (now, int(outbox_id), now),
            )
            return bool(cur.rowcount)

    def recover_abandoned_claims(self, max_age_seconds: float = 60.0) -> int:
        """Quarantine stale in-flight rows instead of duplicating them.

        A second healthy worker must not steal a row actively being delivered by the
        first one, so only claims older than ``max_age_seconds`` are quarantined.
        After a crashed worker, the surviving/restarted process periodically moves
        those stale SENDING rows to UNCERTAIN. No automatic resend is attempted.
        """
        cutoff = time.time() - max(1.0, float(max_age_seconds))
        with self._lock, self._conn() as c:
            cur = c.execute(
                """
                UPDATE telegram_outbox
                SET status='UNCERTAIN', sent_at=NULL,
                    last_error='delivery claim expired without a local Telegram receipt; not retried to avoid duplicate',
                    claimed_at=NULL
                WHERE status='SENDING' AND (claimed_at IS NULL OR claimed_at <= ?)
                """,
                (cutoff,),
            )
            return int(cur.rowcount or 0)

    def mark_sent(self, outbox_id: int) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                """
                UPDATE telegram_outbox
                SET status='SENT', sent_at=?, last_error=NULL, claimed_at=NULL
                WHERE id=? AND status='SENDING'
                """,
                (time.time(), int(outbox_id)),
            )

    def mark_suppressed(self, outbox_id: int, reason: str) -> None:
        with self._lock, self._conn() as c:
            c.execute(
                """
                UPDATE telegram_outbox
                SET status='SUPPRESSED', sent_at=NULL, last_error=?, claimed_at=NULL
                WHERE id=? AND status='SENDING'
                """,
                (str(reason)[:1000], int(outbox_id)),
            )

    def mark_uncertain(self, outbox_id: int, reason: str) -> None:
        """Terminal state for a send whose Telegram receipt is unknown."""
        with self._lock, self._conn() as c:
            c.execute(
                """
                UPDATE telegram_outbox
                SET status='UNCERTAIN', sent_at=NULL, last_error=?, claimed_at=NULL
                WHERE id=? AND status='SENDING'
                """,
                (str(reason)[:1000], int(outbox_id)),
            )

    def mark_failed(self, outbox_id: int, error: str) -> None:
        """Requeue only failures known to have produced no Telegram message."""
        with self._lock, self._conn() as c:
            row = c.execute(
                "SELECT attempts FROM telegram_outbox WHERE id=? AND status='SENDING'",
                (int(outbox_id),),
            ).fetchone()
            if not row:
                return
            attempts = int(row["attempts"] or 0) + 1
            delay = min(60.0, 2.0 ** min(attempts, 6))
            c.execute(
                """
                UPDATE telegram_outbox
                SET status='PENDING', attempts=?, next_attempt_at=?, last_error=?, claimed_at=NULL
                WHERE id=? AND status='SENDING'
                """,
                (attempts, time.time() + delay, str(error)[:1000], int(outbox_id)),
            )

    def status(self, outbox_id: int) -> dict | None:
        with self._conn() as c:
            row = c.execute("SELECT * FROM telegram_outbox WHERE id=?", (int(outbox_id),)).fetchone()
            return dict(row) if row else None

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
