from __future__ import annotations

"""Thread-safe read facade for maker PAPER settlement notifications.

The V3 runtime intentionally performs maker SQLite work in ``asyncio.to_thread``.
All database access must therefore remain behind the accounting store's process-local
RLock.  This additive facade moves the last runtime-owned raw SQLite query behind that
lock without changing the hash-chained maker state machine.

Malformed persisted event JSON is an integrity failure, not a silent notification
skip.  No actual order, fill, wallet, signing or financial authority is introduced.
"""

import json

from .weather_only_maker_paper_accounting import (
    MAKER_PAPER_ACCOUNTING_VERSION,
    MakerPaperAccountingStore,
)
from .weather_only_maker_store import WeatherMakerStoreError


MAKER_PAPER_ACCOUNTING_V3_VERSION = (
    "weather_maker_paper_accounting_v3_locked_notification_query"
)


class MakerPaperAccountingStoreV3(MakerPaperAccountingStore):
    """Keep settlement-notification reads inside the serialized store boundary."""

    def pending_notification_payloads(
        self,
        *,
        source_event_type: str,
        terminal_event_types: tuple[str, ...],
        limit: int = 50,
    ) -> list[tuple[str, dict]]:
        source = str(source_event_type or "").strip().upper()
        terminals = tuple(
            str(value or "").strip().upper() for value in terminal_event_types
        )
        if not source or not terminals or any(not value for value in terminals):
            raise WeatherMakerStoreError("MAKER_STORE_NOTIFICATION_EVENT_TYPE_INVALID")
        if len(set(terminals)) != len(terminals):
            raise WeatherMakerStoreError("MAKER_STORE_NOTIFICATION_EVENT_TYPE_DUPLICATE")
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 500:
            raise WeatherMakerStoreError("MAKER_STORE_NOTIFICATION_LIMIT_INVALID")

        placeholders = ",".join("?" for _ in terminals)
        with self._db_lock:
            rows = self.db.execute(
                f"""
                SELECT s.order_id,s.payload_json
                FROM weather_maker_shadow_events s
                WHERE s.event_type=?
                  AND NOT EXISTS (
                    SELECT 1 FROM weather_maker_shadow_events n
                    WHERE n.order_id=s.order_id
                      AND n.event_type IN ({placeholders})
                  )
                ORDER BY s.seq
                LIMIT ?
                """,
                (source, *terminals, int(limit)),
            ).fetchall()
            out: list[tuple[str, dict]] = []
            for row in rows:
                try:
                    payload = json.loads(str(row["payload_json"]))
                except json.JSONDecodeError:
                    raise WeatherMakerStoreError(
                        "MAKER_STORE_EVENT_PAYLOAD_JSON_INVALID"
                    ) from None
                if not isinstance(payload, dict):
                    raise WeatherMakerStoreError(
                        "MAKER_STORE_EVENT_PAYLOAD_JSON_INVALID"
                    )
                out.append((str(row["order_id"]), payload))
            return out

    def summary(self) -> dict:
        value = dict(super().summary())
        value.update(
            {
                "version": MAKER_PAPER_ACCOUNTING_V3_VERSION,
                "base_accounting_version": MAKER_PAPER_ACCOUNTING_VERSION,
                "notification_query_serialized": True,
                "actual_order_placed": False,
                "actual_fill_authority": False,
                "financial_authority": False,
            }
        )
        return value
