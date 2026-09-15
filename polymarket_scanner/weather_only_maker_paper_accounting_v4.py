from __future__ import annotations

"""Crash-safe at-most-once claim for maker PAPER settlement notifications.

The settlement event and PAPER P&L are already durable before Telegram notification.
This layer addresses the external-side-effect crash window: a process could previously
send a result successfully and die before recording the local SENT event, causing a
duplicate result after restart.

A notification is now claimed durably as SENDING in the same SQLite transaction that
selects it.  SENDING is terminal for automatic retry.  This deliberately prefers a
possibly missed informational result message over a duplicate after an unknowable
crash/transport boundary.  Settlement/P&L state is unaffected and remains queryable.

No order, wallet, signing, cancellation, actual-fill or financial authority exists.
"""

import json
import time

from .weather_only_maker_paper_accounting import MAKER_SETTLEMENT_EVENT
from .weather_only_maker_paper_accounting_v3 import (
    MAKER_PAPER_ACCOUNTING_V3_VERSION,
    MakerPaperAccountingStoreV3,
)
from .weather_only_maker_store import WeatherMakerStoreError, _sha_text


MAKER_PAPER_ACCOUNTING_V4_VERSION = (
    "weather_maker_paper_accounting_v4_atomic_at_most_once_notification_claim"
)
MAKER_SETTLEMENT_NOTIFY_SENDING = "PAPER_SETTLEMENT_TELEGRAM_SENDING"


class MakerPaperAccountingStoreV4(MakerPaperAccountingStoreV3):
    """Atomically claim unnotified settlements before Telegram side effects."""

    def claim_pending_settlement_notifications(
        self,
        *,
        sent_event_type: str,
        uncertain_event_type: str,
        limit: int = 50,
        claimed_at: float | None = None,
    ) -> list[tuple[str, dict]]:
        sent = str(sent_event_type or "").strip().upper()
        uncertain = str(uncertain_event_type or "").strip().upper()
        if not sent or not uncertain or sent == uncertain:
            raise WeatherMakerStoreError("MAKER_STORE_NOTIFICATION_EVENT_TYPE_INVALID")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise WeatherMakerStoreError("MAKER_STORE_NOTIFICATION_LIMIT_INVALID")
        at = time.time() if claimed_at is None else float(claimed_at)
        if not (at >= 0.0):
            raise WeatherMakerStoreError("MAKER_STORE_NOTIFICATION_CLAIM_TIME_INVALID")

        terminal = (MAKER_SETTLEMENT_NOTIFY_SENDING, sent, uncertain)
        with self._db_lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                rows = self.db.execute(
                    """
                    SELECT s.order_id,s.payload_json,s.payload_sha256,o.state_sha256
                    FROM weather_maker_shadow_events s
                    JOIN weather_maker_shadow_orders o ON o.order_id=s.order_id
                    WHERE s.event_type=?
                      AND NOT EXISTS (
                        SELECT 1 FROM weather_maker_shadow_events n
                        WHERE n.order_id=s.order_id
                          AND n.event_type IN (?,?,?)
                      )
                    ORDER BY s.seq
                    LIMIT ?
                    """,
                    (MAKER_SETTLEMENT_EVENT, *terminal, int(limit)),
                ).fetchall()

                claimed: list[tuple[str, dict]] = []
                seen: set[str] = set()
                for row in rows:
                    order_id = str(row["order_id"])
                    if order_id in seen:
                        raise WeatherMakerStoreError(
                            "MAKER_STORE_MULTIPLE_SETTLEMENT_EVENTS"
                        )
                    seen.add(order_id)
                    payload_text = str(row["payload_json"])
                    if _sha_text(payload_text) != str(row["payload_sha256"]):
                        raise WeatherMakerStoreError(
                            "MAKER_STORE_EVENT_PAYLOAD_DIGEST_MISMATCH"
                        )
                    try:
                        payload = json.loads(payload_text)
                    except json.JSONDecodeError:
                        raise WeatherMakerStoreError(
                            "MAKER_STORE_EVENT_PAYLOAD_JSON_INVALID"
                        ) from None
                    if not isinstance(payload, dict):
                        raise WeatherMakerStoreError(
                            "MAKER_STORE_EVENT_PAYLOAD_JSON_INVALID"
                        )
                    state_sha = str(row["state_sha256"])
                    if len(state_sha) != 64:
                        raise WeatherMakerStoreError(
                            "MAKER_STORE_STATE_DIGEST_MISMATCH"
                        )
                    self._append_event(
                        order_id,
                        MAKER_SETTLEMENT_NOTIFY_SENDING,
                        {
                            "version": MAKER_PAPER_ACCOUNTING_V4_VERSION,
                            "claimed_at": at,
                            "retry_policy": "AT_MOST_ONCE_AFTER_DURABLE_CLAIM",
                            "financial_authority": False,
                        },
                        state_sha,
                        at,
                    )
                    claimed.append((order_id, payload))
                self.db.execute("COMMIT")
                return claimed
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise

    def summary(self) -> dict:
        value = dict(super().summary())
        with self._db_lock:
            sending = int(
                self.db.execute(
                    "SELECT COUNT(*) FROM weather_maker_shadow_events WHERE event_type=?",
                    (MAKER_SETTLEMENT_NOTIFY_SENDING,),
                ).fetchone()[0]
            )
        value.update(
            {
                "version": MAKER_PAPER_ACCOUNTING_V4_VERSION,
                "base_accounting_version": MAKER_PAPER_ACCOUNTING_V3_VERSION,
                "settlement_notification_claims": sending,
                "settlement_notification_retry_policy": (
                    "AT_MOST_ONCE_AFTER_DURABLE_CLAIM"
                ),
                "actual_order_placed": False,
                "actual_fill_authority": False,
                "financial_authority": False,
            }
        )
        return value
