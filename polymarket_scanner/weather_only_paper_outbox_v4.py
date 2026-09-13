from __future__ import annotations

"""Durable Telegram outbox for weather paper v4.

Telegram cannot provide exactly-once delivery.  This module therefore records a
stable logical message and explicit PENDING/SENDING/ACKNOWLEDGED/UNCERTAIN/EXPIRED
states.  An ambiguous transport outcome is never blindly retried as though the
remote side definitely received nothing.
"""

import asyncio
import json
import sqlite3
import time
from pathlib import Path

import httpx


OUTBOX_VERSION = "weather_paper_outbox_v4_uncertain_delivery"
PENDING = "PENDING"
SENDING = "SENDING"
ACKNOWLEDGED = "ACKNOWLEDGED"
UNCERTAIN = "UNCERTAIN"
EXPIRED = "EXPIRED"
VALID_STATES = {PENDING, SENDING, ACKNOWLEDGED, UNCERTAIN, EXPIRED}
SENDING_LEASE_SECONDS = 60.0


class PaperOutboxError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


class WeatherPaperOutboxV4:
    def __init__(self, db_path: str | Path) -> None:
        self.path = Path(db_path)
        if not self.path.is_absolute():
            raise PaperOutboxError("OUTBOX_DB_NOT_ABSOLUTE")
        self._init()

    def _conn(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def _init(self) -> None:
        with self._conn() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS weather_paper_outbox (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    version TEXT NOT NULL,
                    logical_id TEXT NOT NULL UNIQUE,
                    signal_id INTEGER UNIQUE,
                    position_id INTEGER,
                    message_kind TEXT NOT NULL,
                    body_html TEXT NOT NULL,
                    url TEXT,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    not_after REAL,
                    last_attempt_at REAL,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    telegram_message_id INTEGER,
                    acknowledged_at REAL,
                    last_error TEXT,
                    retry_not_before REAL
                );
                CREATE INDEX IF NOT EXISTS idx_weather_paper_outbox_state
                    ON weather_paper_outbox(state,retry_not_before,id);
                """
            )

    def enqueue(
        self,
        *,
        logical_id: str,
        message_kind: str,
        body_html: str,
        signal_id: int | None = None,
        position_id: int | None = None,
        url: str | None = None,
        not_after: float | None = None,
    ) -> dict:
        lid = str(logical_id or "").strip()
        body = str(body_html or "")
        kind = str(message_kind or "").strip()
        if not lid or not kind or not body:
            raise PaperOutboxError("OUTBOX_IDENTITY_INVALID")
        now = time.time()
        if not_after is not None and float(not_after) <= now:
            state = EXPIRED
            error = "EXPIRED_BEFORE_ENQUEUE"
        else:
            state = PENDING
            error = None
        with self._conn() as db:
            try:
                db.execute(
                    """
                    INSERT INTO weather_paper_outbox(
                        version,logical_id,signal_id,position_id,message_kind,body_html,url,state,
                        created_at,not_after,last_error
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (OUTBOX_VERSION,lid,signal_id,position_id,kind,body,url,state,now,not_after,error),
                )
            except sqlite3.IntegrityError:
                pass
            row = db.execute("SELECT * FROM weather_paper_outbox WHERE logical_id=?", (lid,)).fetchone()
        if not row:
            raise PaperOutboxError("OUTBOX_ENQUEUE_FAILED")
        return dict(row)

    def recover_stale_sending(self, *, now: float | None = None) -> int:
        when = time.time() if now is None else float(now)
        cutoff = when - SENDING_LEASE_SECONDS
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT id FROM weather_paper_outbox WHERE state='SENDING' AND COALESCE(last_attempt_at,0)<=?",
                (cutoff,),
            )]
            for row in rows:
                db.execute(
                    "UPDATE weather_paper_outbox SET state='UNCERTAIN',last_error='CRASH_OR_LEASE_EXPIRED_DURING_SEND' WHERE id=?",
                    (int(row["id"]),),
                )
        return len(rows)

    def expire_due(self, *, now: float | None = None) -> int:
        when = time.time() if now is None else float(now)
        with self._conn() as db:
            cur = db.execute(
                """
                UPDATE weather_paper_outbox
                SET state='EXPIRED',last_error=COALESCE(last_error,'DELIVERY_DEADLINE_EXPIRED')
                WHERE state='PENDING' AND not_after IS NOT NULL AND not_after<=?
                """,
                (when,),
            )
            return int(cur.rowcount or 0)

    def claim_next(self, *, now: float | None = None) -> dict | None:
        when = time.time() if now is None else float(now)
        self.recover_stale_sending(now=when)
        self.expire_due(now=when)
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                """
                SELECT * FROM weather_paper_outbox
                WHERE state='PENDING'
                  AND (retry_not_before IS NULL OR retry_not_before<=?)
                  AND (not_after IS NULL OR not_after>?)
                ORDER BY id LIMIT 1
                """,
                (when, when),
            ).fetchone()
            if not row:
                db.commit()
                return None
            oid = int(row["id"])
            db.execute(
                """
                UPDATE weather_paper_outbox
                SET state='SENDING',last_attempt_at=?,attempt_count=attempt_count+1,last_error=NULL
                WHERE id=? AND state='PENDING'
                """,
                (when, oid),
            )
            claimed = db.execute("SELECT * FROM weather_paper_outbox WHERE id=?", (oid,)).fetchone()
            db.commit()
        return dict(claimed) if claimed else None

    def mark_acknowledged(self, outbox_id: int, telegram_message_id: int) -> dict:
        mid = int(telegram_message_id)
        if mid <= 0:
            raise PaperOutboxError("OUTBOX_TELEGRAM_RECEIPT_INVALID")
        now = time.time()
        with self._conn() as db:
            db.execute(
                """
                UPDATE weather_paper_outbox
                SET state='ACKNOWLEDGED',telegram_message_id=?,acknowledged_at=?,last_error=NULL
                WHERE id=? AND state='SENDING'
                """,
                (mid, now, int(outbox_id)),
            )
            row = db.execute("SELECT * FROM weather_paper_outbox WHERE id=?", (int(outbox_id),)).fetchone()
        if not row or row["state"] != ACKNOWLEDGED:
            raise PaperOutboxError("OUTBOX_ACK_STATE_INVALID")
        return dict(row)

    def mark_uncertain(self, outbox_id: int, reason: str) -> None:
        with self._conn() as db:
            db.execute(
                "UPDATE weather_paper_outbox SET state='UNCERTAIN',last_error=? WHERE id=? AND state='SENDING'",
                (str(reason or "AMBIGUOUS_DELIVERY"), int(outbox_id)),
            )

    def mark_expired(self, outbox_id: int, reason: str) -> None:
        with self._conn() as db:
            db.execute(
                "UPDATE weather_paper_outbox SET state='EXPIRED',last_error=? WHERE id=? AND state IN ('PENDING','SENDING')",
                (str(reason or "DELIVERY_EXPIRED"), int(outbox_id)),
            )

    def mark_retryable(self, outbox_id: int, reason: str, retry_after_seconds: float) -> None:
        delay = max(1.0, min(60.0, float(retry_after_seconds)))
        with self._conn() as db:
            row = db.execute("SELECT not_after FROM weather_paper_outbox WHERE id=?", (int(outbox_id),)).fetchone()
            if not row:
                return
            retry_at = time.time() + delay
            if row["not_after"] is not None and retry_at >= float(row["not_after"]):
                db.execute(
                    "UPDATE weather_paper_outbox SET state='EXPIRED',last_error=? WHERE id=?",
                    (f"{reason}:RETRY_WOULD_CROSS_EXPIRY", int(outbox_id)),
                )
            else:
                db.execute(
                    "UPDATE weather_paper_outbox SET state='PENDING',retry_not_before=?,last_error=? WHERE id=? AND state='SENDING'",
                    (retry_at, str(reason), int(outbox_id)),
                )

    def by_signal(self, signal_id: int) -> dict | None:
        with self._conn() as db:
            row = db.execute("SELECT * FROM weather_paper_outbox WHERE signal_id=?", (int(signal_id),)).fetchone()
        return dict(row) if row else None

    def summary(self) -> dict:
        with self._conn() as db:
            rows = [dict(row) for row in db.execute(
                "SELECT state,COUNT(*) AS n FROM weather_paper_outbox GROUP BY state"
            )]
        counts = {state: 0 for state in VALID_STATES}
        for row in rows:
            counts[str(row["state"])] = int(row["n"])
        return {"version": OUTBOX_VERSION, **{key.lower(): value for key, value in counts.items()}}


class WeatherPaperTelegramOutboxSenderV4:
    """One-attempt transport. Ambiguous outcomes become UNCERTAIN, never blind retry."""

    def __init__(self, *, token: str, chat_id: str, outbox: WeatherPaperOutboxV4) -> None:
        self.token = str(token or "").strip()
        self.chat_id = str(chat_id or "").strip()
        if not self.token or not self.chat_id:
            raise PaperOutboxError("OUTBOX_TELEGRAM_NOT_CONFIGURED")
        self.outbox = outbox
        self.http = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=8.0),
            limits=httpx.Limits(max_connections=2, max_keepalive_connections=2, keepalive_expiry=60.0),
            trust_env=False,
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def deliver_one(self) -> dict | None:
        row = await asyncio.to_thread(self.outbox.claim_next)
        if row is None:
            return None
        oid = int(row["id"])
        if row.get("not_after") is not None and time.time() >= float(row["not_after"]):
            await asyncio.to_thread(self.outbox.mark_expired, oid, "DELIVERY_DEADLINE_EXPIRED_BEFORE_HTTP")
            return {**row, "state": EXPIRED}
        payload: dict[str, object] = {
            "chat_id": self.chat_id,
            "text": str(row["body_html"]),
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        url = str(row.get("url") or "")
        if url.startswith("https://"):
            payload["reply_markup"] = {"inline_keyboard": [[{"text": "OPEN POLYMARKET", "url": url}]]}
        endpoint = f"https://api.telegram.org/bot{self.token}/sendMessage"
        try:
            response = await self.http.post(endpoint, json=payload)
        except (httpx.TimeoutException, httpx.RequestError):
            # Request may have reached Telegram. Retrying here could duplicate a
            # visible alert, so preserve the ambiguity instead.
            await asyncio.to_thread(self.outbox.mark_uncertain, oid, "TELEGRAM_TRANSPORT_OUTCOME_UNKNOWN")
            return {**row, "state": UNCERTAIN}

        if response.status_code == 429:
            retry_after = 1.0
            try:
                retry_after = float((response.json().get("parameters") or {}).get("retry_after", 1.0))
            except Exception:
                retry_after = 1.0
            await asyncio.to_thread(self.outbox.mark_retryable, oid, "TELEGRAM_RATE_LIMITED", retry_after)
            return {**row, "state": PENDING}
        if response.status_code >= 500:
            # A server error is not strong enough evidence that a message was never
            # accepted. Keep it auditable instead of risking a duplicate.
            await asyncio.to_thread(self.outbox.mark_uncertain, oid, f"TELEGRAM_HTTP_{response.status_code}_OUTCOME_UNKNOWN")
            return {**row, "state": UNCERTAIN}
        if response.status_code >= 400:
            await asyncio.to_thread(self.outbox.mark_expired, oid, f"TELEGRAM_HTTP_{response.status_code}")
            return {**row, "state": EXPIRED}
        try:
            body = response.json()
        except ValueError:
            await asyncio.to_thread(self.outbox.mark_uncertain, oid, "TELEGRAM_RESPONSE_JSON_UNKNOWN")
            return {**row, "state": UNCERTAIN}
        result = body.get("result") if isinstance(body, dict) and body.get("ok") is True else None
        message_id = result.get("message_id") if isinstance(result, dict) else None
        if type(message_id) is not int:
            await asyncio.to_thread(self.outbox.mark_uncertain, oid, "TELEGRAM_RECEIPT_MISSING")
            return {**row, "state": UNCERTAIN}
        ack = await asyncio.to_thread(self.outbox.mark_acknowledged, oid, message_id)
        return ack

    async def drain(self, *, maximum: int = 50) -> list[dict]:
        rows: list[dict] = []
        for _ in range(max(1, min(200, int(maximum)))):
            row = await self.deliver_one()
            if row is None:
                break
            rows.append(row)
            if row.get("state") == PENDING:
                break
        return rows
