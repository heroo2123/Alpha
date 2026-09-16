from __future__ import annotations

"""Operator-state synchronization for delivered PAPER signals.

A Telegram alert is delivered before the final causal post-receipt validation.  This
module keeps the human-visible message synchronized with the durable signal state:

* terminal post-receipt states schedule an idempotent edit of the original message;
* identical Telegram edits may be retried safely after ambiguous transport outcomes;
* dedupe is released only after the invalidation edit is durably confirmed;
* retries are bounded and cooled down so a permanently bad thesis cannot spam; and
* /recent exposes the real signal state, terminal reason and Telegram-sync state.

No order, wallet, signing, cancellation or financial authority is introduced.
"""

import asyncio
import hashlib
import html
import json
import time

import httpx

from .weather_only_independent_review_corrective import IndependentReviewAllPaperCommandController
from .weather_only_independent_review_corrective_v3 import IndependentReviewPostReceiptStoreV3
from .weather_only_live_paper import WeatherLivePaperError
from .weather_only_paper_corrective import CorrectivePaperTelegram, DeliveryUncertain
from .weather_only_paper_positions import WeatherPaperPositionError, _payload


OPERATOR_STATE_CORRECTIVE_VERSION = (
    "weather_operator_state_v1_visible_terminal_sync_bounded_retry"
)
OPERATOR_SYNC_PENDING = "PENDING"
OPERATOR_SYNC_APPLIED = "APPLIED"
OPERATOR_SYNC_FAILED = "FAILED"
TERMINAL_VISIBLE_STATUSES = {
    "POST_RECEIPT_NOT_ACTIONABLE",
    "ACTIONABILITY_UNPROVEN",
    "PAPER_ACCOUNTING_ERROR",
    "EXPIRED",
    "MAKER_NOT_ACTIVATED",
    "MAKER_NOT_ACTIVATED_RESTART_COVERAGE_LOST",
}
RETRYABLE_AFTER_VISIBLE_INVALIDATION = {
    "POST_RECEIPT_NOT_ACTIONABLE",
    "ACTIONABILITY_UNPROVEN",
    "PAPER_ACCOUNTING_ERROR",
    "EXPIRED",
}
MAX_RETRIES_PER_BASE_FINGERPRINT = 3
RETRY_COOLDOWN_SECONDS = 180.0


def _sha(value: object) -> str:
    raw = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


class OperatorStateTelegram(CorrectivePaperTelegram):
    """Corrective Telegram transport with idempotent message editing."""

    async def edit_html(self, message_id: int, text: str) -> None:
        if isinstance(message_id, bool) or not isinstance(message_id, int) or message_id <= 0:
            raise WeatherLivePaperError("PAPER_TELEGRAM_EDIT_MESSAGE_ID_INVALID")
        if len(text) > 3900:
            raise WeatherLivePaperError("PAPER_TELEGRAM_EDIT_MESSAGE_TOO_LONG")
        payload: dict[str, object] = {
            "chat_id": self.chat_id,
            "message_id": int(message_id),
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
            "reply_markup": {"inline_keyboard": []},
        }
        endpoint = f"https://api.telegram.org/bot{self.token}/editMessageText"
        for attempt in range(3):
            try:
                response = await self.http.post(endpoint, json=payload)
            except httpx.RequestError:
                if attempt >= 2:
                    raise DeliveryUncertain("PAPER_TELEGRAM_EDIT_UNCERTAIN") from None
                await asyncio.sleep(0.5 * (attempt + 1))
                continue

            if response.status_code == 429:
                try:
                    retry_after = float(
                        (response.json().get("parameters") or {}).get("retry_after", 1.0)
                    )
                except Exception:
                    retry_after = 1.0
                if attempt >= 2:
                    raise WeatherLivePaperError("PAPER_TELEGRAM_EDIT_RATE_LIMITED")
                await asyncio.sleep(min(15.0, max(1.0, retry_after)))
                continue

            if response.status_code >= 500:
                if attempt >= 2:
                    raise DeliveryUncertain("PAPER_TELEGRAM_EDIT_SERVER_UNCERTAIN")
                await asyncio.sleep(0.5 * (attempt + 1))
                continue

            if response.status_code >= 400:
                description = ""
                try:
                    body = response.json()
                    description = str(body.get("description") or "").lower()
                except Exception:
                    description = ""
                if response.status_code == 400 and "message is not modified" in description:
                    return
                raise WeatherLivePaperError(
                    f"PAPER_TELEGRAM_EDIT_HTTP_{response.status_code}"
                )

            try:
                body = response.json()
            except ValueError:
                if attempt >= 2:
                    raise DeliveryUncertain(
                        "PAPER_TELEGRAM_EDIT_RESPONSE_UNCERTAIN"
                    ) from None
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            if not isinstance(body, dict) or body.get("ok") is not True:
                if attempt >= 2:
                    raise DeliveryUncertain("PAPER_TELEGRAM_EDIT_RECEIPT_UNCERTAIN")
                await asyncio.sleep(0.5 * (attempt + 1))
                continue
            return

        raise WeatherLivePaperError("PAPER_TELEGRAM_EDIT_RETRY_EXHAUSTED")


class OperatorStatePostReceiptStore(IndependentReviewPostReceiptStoreV3):
    """Durable operator-sync and bounded retry semantics for delivered V5 signals."""

    def __init__(self, path) -> None:
        super().__init__(path)
        self._operator_state_migrate()

    def _operator_state_migrate(self) -> None:
        with self._conn() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS weather_paper_operator_sync (
                    signal_id INTEGER PRIMARY KEY,
                    version TEXT NOT NULL,
                    terminal_status TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    telegram_message_id INTEGER NOT NULL,
                    base_fingerprint TEXT NOT NULL,
                    message_sha256 TEXT NOT NULL,
                    state TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    applied_at REAL,
                    fingerprint_released INTEGER NOT NULL DEFAULT 0
                        CHECK(fingerprint_released IN (0,1)),
                    FOREIGN KEY(signal_id) REFERENCES weather_paper_signals(id)
                );
                CREATE TABLE IF NOT EXISTS weather_paper_retry_guard (
                    base_fingerprint TEXT PRIMARY KEY,
                    invalidations INTEGER NOT NULL,
                    next_retry_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                );
                """
            )

    @staticmethod
    def _validate_terminal_identity(
        signal: dict,
        *,
        decision_id: str,
        event_id: str,
        market_id: str | None,
        side: str | None,
    ) -> None:
        payload = _payload(signal.get("payload_json"))
        if str(payload.get("decision_id") or "") != str(decision_id):
            raise WeatherPaperPositionError("V5_TERMINAL_DECISION_IDENTITY_MISMATCH")
        if str(signal.get("event_id") or "") != str(event_id):
            raise WeatherPaperPositionError("V5_TERMINAL_EVENT_IDENTITY_MISMATCH")
        if str(signal.get("market_id") or "") != str(market_id or ""):
            raise WeatherPaperPositionError("V5_TERMINAL_MARKET_IDENTITY_MISMATCH")
        if str(signal.get("side") or "").upper() != str(side or "").upper():
            raise WeatherPaperPositionError("V5_TERMINAL_SIDE_IDENTITY_MISMATCH")

    def mark_post_receipt_not_actionable(
        self,
        signal_id: int,
        *,
        decision_id: str,
        event_id: str,
        market_id: str | None,
        side: str | None,
        reason: str,
        recorded_at: float | None = None,
    ) -> None:
        sid = int(signal_id)
        with self._conn() as db:
            row = db.execute(
                "SELECT * FROM weather_paper_signals WHERE id=?", (sid,)
            ).fetchone()
        if row is None:
            raise WeatherPaperPositionError("V5_SIGNAL_NOT_FOUND")
        self._validate_terminal_identity(
            dict(row),
            decision_id=str(decision_id),
            event_id=str(event_id),
            market_id=market_id,
            side=side,
        )
        super().mark_post_receipt_not_actionable(
            sid,
            decision_id=decision_id,
            event_id=event_id,
            market_id=market_id,
            side=side,
            reason=reason,
            recorded_at=recorded_at,
        )
        self.ensure_operator_sync_records(signal_ids=[sid])

    def _latest_reason(self, db, signal: dict) -> str:
        payload = _payload(signal.get("payload_json"))
        decision_id = str(payload.get("decision_id") or "").strip()
        if decision_id:
            row = db.execute(
                "SELECT reason,outcome FROM weather_paper_decisions "
                "WHERE decision_id=? ORDER BY id DESC LIMIT 1",
                (decision_id,),
            ).fetchone()
            if row is not None:
                reason = str(row["reason"] or "").strip()
                if reason:
                    return reason
                outcome = str(row["outcome"] or "").strip()
                if outcome:
                    return outcome
        return str(signal.get("status") or "UNKNOWN_TERMINAL_STATE")

    def _sync_message(self, signal: dict, reason: str) -> str:
        payload = _payload(signal.get("payload_json"))
        title = str(payload.get("event_title") or signal.get("event_id") or "Weather signal")
        lane = str(signal.get("lane") or "unknown")
        side = str(signal.get("side") or "BASKET")
        status = str(signal.get("status") or signal.get("signal_status") or "INVALIDATED")
        return "\n".join(
            [
                "⛔ <b>INVALIDATED — DO NOT ACT</b>",
                f"<b>{html.escape(title[:180])}</b>",
                f"Lane: <code>{html.escape(lane)}</code>",
                f"Former side: <b>{html.escape(side)}</b>",
                "",
                "The earlier PAPER alert did not survive its required post-receipt validation.",
                f"Final state: <b>{html.escape(status)}</b>",
                f"Reason: <code>{html.escape(str(reason)[:800])}</code>",
                "",
                "🚫 <b>Do not place a trade from the earlier alert.</b>",
                "No validated PAPER position was opened from that alert.",
                "A later alert is a separate decision and must be evaluated on its own.",
            ]
        )

    def ensure_operator_sync_records(self, *, signal_ids: list[int] | None = None) -> int:
        wanted = None if signal_ids is None else {int(value) for value in signal_ids}
        created = 0
        with self._conn() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = [
                dict(row)
                for row in db.execute(
                    "SELECT * FROM weather_paper_signals "
                    "WHERE telegram_message_id IS NOT NULL ORDER BY id"
                )
            ]
            for signal in rows:
                sid = int(signal["id"])
                if wanted is not None and sid not in wanted:
                    continue
                status = str(signal.get("status") or "")
                if status not in TERMINAL_VISIBLE_STATUSES:
                    continue
                existing = db.execute(
                    "SELECT 1 FROM weather_paper_operator_sync WHERE signal_id=?",
                    (sid,),
                ).fetchone()
                if existing is not None:
                    continue
                reason = self._latest_reason(db, signal)
                text = self._sync_message(signal, reason)
                now = time.time()
                db.execute(
                    """
                    INSERT INTO weather_paper_operator_sync(
                        signal_id,version,terminal_status,reason,telegram_message_id,
                        base_fingerprint,message_sha256,state,attempts,last_error,
                        created_at,updated_at,applied_at,fingerprint_released
                    ) VALUES(?,?,?,?,?,?,?,?,0,NULL,?,?,NULL,0)
                    """,
                    (
                        sid,
                        OPERATOR_STATE_CORRECTIVE_VERSION,
                        status,
                        reason,
                        int(signal["telegram_message_id"]),
                        str(signal["fingerprint"]),
                        _sha(text),
                        OPERATOR_SYNC_PENDING,
                        now,
                        now,
                    ),
                )
                created += 1
            db.execute("COMMIT")
        return created

    def pending_operator_sync(self, limit: int = 50, *, signal_id: int | None = None) -> list[dict]:
        count = max(1, min(200, int(limit)))
        with self._conn() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT o.*,s.lane,s.event_id,s.market_id,s.side,s.payload_json,
                           s.status AS signal_status
                    FROM weather_paper_operator_sync o
                    JOIN weather_paper_signals s ON s.id=o.signal_id
                    WHERE o.state<>? AND (? IS NULL OR o.signal_id=?)
                    ORDER BY o.signal_id LIMIT ?
                    """,
                    (OPERATOR_SYNC_APPLIED, signal_id, signal_id, count),
                )
            ]
        for row in rows:
            row["message_text"] = self._sync_message(row, str(row.get("reason") or ""))
        return rows

    def mark_operator_sync_failed(self, signal_id: int, error: str) -> None:
        now = time.time()
        with self._conn() as db:
            cur = db.execute(
                """
                UPDATE weather_paper_operator_sync
                SET state=?,attempts=attempts+1,last_error=?,updated_at=?
                WHERE signal_id=?
                """,
                (OPERATOR_SYNC_FAILED, str(error)[:500], now, int(signal_id)),
            )
            if cur.rowcount != 1:
                raise WeatherPaperPositionError("OPERATOR_SYNC_SIGNAL_NOT_FOUND")

    def mark_operator_sync_applied(self, signal_id: int) -> None:
        sid = int(signal_id)
        now = time.time()
        with self._conn() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
                sync = db.execute(
                    "SELECT * FROM weather_paper_operator_sync WHERE signal_id=?",
                    (sid,),
                ).fetchone()
                signal = db.execute(
                    "SELECT fingerprint,status FROM weather_paper_signals WHERE id=?",
                    (sid,),
                ).fetchone()
                if sync is None or signal is None:
                    raise WeatherPaperPositionError("OPERATOR_SYNC_SIGNAL_NOT_FOUND")

                status = str(signal["status"] or "")
                released = int(sync["fingerprint_released"] or 0)
                base = str(sync["base_fingerprint"])
                if (
                    released == 0
                    and status in RETRYABLE_AFTER_VISIBLE_INVALIDATION
                    and str(signal["fingerprint"]) == base
                ):
                    guard = db.execute(
                        "SELECT invalidations FROM weather_paper_retry_guard "
                        "WHERE base_fingerprint=?",
                        (base,),
                    ).fetchone()
                    invalidations = int(guard["invalidations"]) if guard else 0
                    if invalidations < MAX_RETRIES_PER_BASE_FINGERPRINT:
                        tombstone = "terminal:" + str(sid) + ":" + _sha(
                            {
                                "base": base,
                                "signal_id": sid,
                                "status": status,
                                "applied_at": now,
                            }
                        )
                        db.execute(
                            "UPDATE weather_paper_signals SET fingerprint=? WHERE id=?",
                            (tombstone, sid),
                        )
                        db.execute(
                            """
                            INSERT INTO weather_paper_retry_guard(
                                base_fingerprint,invalidations,next_retry_at,updated_at
                            ) VALUES(?,?,?,?)
                            ON CONFLICT(base_fingerprint) DO UPDATE SET
                                invalidations=excluded.invalidations,
                                next_retry_at=excluded.next_retry_at,
                                updated_at=excluded.updated_at
                            """,
                            (
                                base,
                                invalidations + 1,
                                now + RETRY_COOLDOWN_SECONDS,
                                now,
                            ),
                        )
                        released = 1

                db.execute(
                    """
                    UPDATE weather_paper_operator_sync
                    SET state=?,attempts=attempts+1,last_error=NULL,updated_at=?,
                        applied_at=?,fingerprint_released=?
                    WHERE signal_id=?
                    """,
                    (OPERATOR_SYNC_APPLIED, now, now, released, sid),
                )
                db.execute("COMMIT")
            except Exception:
                if db.in_transaction:
                    db.execute("ROLLBACK")
                raise

    def save_signal(self, **kwargs):
        base = str(kwargs.get("fingerprint") or "").strip()
        if base:
            now = time.time()
            with self._conn() as db:
                guard = db.execute(
                    "SELECT invalidations,next_retry_at FROM weather_paper_retry_guard "
                    "WHERE base_fingerprint=?",
                    (base,),
                ).fetchone()
            if guard is not None:
                invalidations = int(guard["invalidations"])
                retry_at = float(guard["next_retry_at"])
                if invalidations >= MAX_RETRIES_PER_BASE_FINGERPRINT or now < retry_at:
                    return None
        return super().save_signal(**kwargs)

    def recent_signals(self, limit: int = 10) -> list[dict]:
        count = max(1, min(100, int(limit)))
        out: list[dict] = []
        with self._conn() as db:
            rows = [
                dict(row)
                for row in db.execute(
                    """
                    SELECT s.id,s.lane,s.event_id,s.market_id,s.side,s.entry_cost,s.raw_gap,
                           s.created_at,s.telegram_message_id,s.status AS signal_status,
                           s.payload_json,p.id AS position_id,p.status AS position_status,
                           o.state AS operator_sync_state,o.reason AS operator_sync_reason
                    FROM weather_paper_signals s
                    LEFT JOIN weather_paper_positions p ON p.signal_id=s.id
                    LEFT JOIN weather_paper_operator_sync o ON o.signal_id=s.id
                    ORDER BY s.id DESC LIMIT ?
                    """,
                    (count,),
                )
            ]
            for row in rows:
                reason = str(row.get("operator_sync_reason") or "").strip()
                if not reason:
                    reason = self._latest_reason(db, row)
                row["terminal_reason"] = reason
                row.pop("payload_json", None)
                out.append(row)
        return out

    def operator_sync_summary(self) -> dict:
        with self._conn() as db:
            total = int(
                db.execute("SELECT COUNT(*) FROM weather_paper_operator_sync").fetchone()[0]
            )
            applied = int(
                db.execute(
                    "SELECT COUNT(*) FROM weather_paper_operator_sync WHERE state=?",
                    (OPERATOR_SYNC_APPLIED,),
                ).fetchone()[0]
            )
            failed = int(
                db.execute(
                    "SELECT COUNT(*) FROM weather_paper_operator_sync WHERE state=?",
                    (OPERATOR_SYNC_FAILED,),
                ).fetchone()[0]
            )
        return {
            "version": OPERATOR_STATE_CORRECTIVE_VERSION,
            "total": total,
            "applied": applied,
            "unconfirmed": total - applied,
            "failed": failed,
            "healthy": total == applied,
        }


class OperatorStateCommandController(IndependentReviewAllPaperCommandController):
    """Truthful command facade for terminal/retracted PAPER alerts."""

    def _read_status(self) -> dict:
        status = dict(super()._read_status())
        summary = self.store.operator_sync_summary()
        status["operator_message_sync"] = summary
        if not summary["healthy"]:
            status["cycle_ok"] = False
            status["operator_all_lanes_healthy"] = False
        return status

    def _recent_text(self) -> str:
        rows = self.store.recent_signals(10)
        lines = ["🧾 <b>RECENT WEATHER SIGNALS</b>"]
        if not rows:
            lines.append("No signals stored yet.")
            return "\n".join(lines)
        for row in rows:
            signal_status = str(row.get("signal_status") or "UNKNOWN")
            position_status = str(row.get("position_status") or "")
            if position_status:
                state = f"PAPER POSITION {position_status}"
            elif signal_status in TERMINAL_VISIBLE_STATUSES:
                state = f"⛔ INVALIDATED — {signal_status}"
            elif signal_status == "DELIVERY_UNCERTAIN":
                state = "⚠️ DELIVERY UNCERTAIN"
            else:
                state = signal_status
            lines.append(
                f"#{int(row['id'])} {html.escape(str(row.get('side') or 'BASKET'))} "
                f"@ {html.escape(str(row.get('entry_cost') if row.get('entry_cost') is not None else 'n/a'))} — "
                f"<b>{html.escape(state)}</b>"
            )
            reason = str(row.get("terminal_reason") or "").strip()
            if signal_status in TERMINAL_VISIBLE_STATUSES and reason:
                lines.append(f"  Reason: <code>{html.escape(reason[:500])}</code>")
            sync = str(row.get("operator_sync_state") or "")
            if signal_status in TERMINAL_VISIBLE_STATUSES and sync != OPERATOR_SYNC_APPLIED:
                lines.append("  ⚠️ <b>Telegram invalidation is not yet confirmed.</b>")
        return "\n".join(lines)
