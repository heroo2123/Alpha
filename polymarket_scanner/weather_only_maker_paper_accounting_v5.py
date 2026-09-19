from __future__ import annotations

"""Atomic maker activation boundary for PAPER-only prospective orders.

Telegram receipt is durable before activation. Order creation, ORDER_CREATED audit
event, Telegram linkage and signal transition to MAKER_RESTING are then committed in
one SQLite transaction. A restart can therefore distinguish a fully activated virtual
order from a receipt that never reached durable activation. The latter is never
reconstructed because prospective public-WebSocket continuity was lost.
"""

import json
import math
import time

from .weather_only_maker_paper_accounting_v4 import (
    MAKER_PAPER_ACCOUNTING_V4_VERSION,
    MakerPaperAccountingStoreV4,
)
from .weather_only_maker_shadow import VirtualMakerOrder
from .weather_only_maker_store import (
    WEATHER_MAKER_STORE_VERSION,
    WeatherMakerStoreError,
    _order_text_and_sha,
    _timestamp,
)


MAKER_PAPER_ACCOUNTING_V5_VERSION = (
    "weather_maker_paper_accounting_v5_atomic_activation_signal_link"
)
MAKER_RESTART_ORPHAN_STATUS = "MAKER_NOT_ACTIVATED_RESTART_COVERAGE_LOST"


def _positive_int(value: object, code: str) -> int:
    if isinstance(value, bool):
        raise WeatherMakerStoreError(code)
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherMakerStoreError(code) from None
    if number <= 0:
        raise WeatherMakerStoreError(code)
    return number


def _nonnegative_time(value: object, code: str) -> float:
    if isinstance(value, bool):
        raise WeatherMakerStoreError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherMakerStoreError(code) from None
    if not math.isfinite(number) or number < 0.0:
        raise WeatherMakerStoreError(code)
    return number


class MakerPaperAccountingStoreV5(MakerPaperAccountingStoreV4):
    """V4 settlement claims plus crash-consistent virtual-order activation."""

    def activate_after_telegram(
        self,
        order: VirtualMakerOrder,
        *,
        signal_id: int,
        telegram_message_id: int,
        telegram_sent_at: float,
        post_delivery_exact_finished_at: float,
        recorded_at: float | None = None,
    ) -> VirtualMakerOrder:
        state_text, state_sha = _order_text_and_sha(order)
        sid = _positive_int(signal_id, "MAKER_ACTIVATION_SIGNAL_ID_INVALID")
        message_id = _positive_int(
            telegram_message_id, "MAKER_ACTIVATION_TELEGRAM_RECEIPT_INVALID"
        )
        sent_at = _nonnegative_time(
            telegram_sent_at, "MAKER_ACTIVATION_TELEGRAM_TIME_INVALID"
        )
        exact_finished = _nonnegative_time(
            post_delivery_exact_finished_at, "MAKER_ACTIVATION_EXACT_TIME_INVALID"
        )
        at = _timestamp(recorded_at)
        if exact_finished + 1e-9 < sent_at or at + 1e-9 < exact_finished:
            raise WeatherMakerStoreError("MAKER_ACTIVATION_TIME_ORDER_INVALID")

        with self._db_lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                signal = self.db.execute(
                    "SELECT lane,payload_json,telegram_message_id,telegram_sent_at,status "
                    "FROM weather_paper_signals WHERE id=?",
                    (sid,),
                ).fetchone()
                if signal is None:
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_SIGNAL_NOT_FOUND")
                if str(signal["lane"] or "") != "weather_maker_virtual_bid":
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_SIGNAL_LANE_MISMATCH")
                if signal["telegram_message_id"] is None or signal["telegram_sent_at"] is None:
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_TELEGRAM_RECEIPT_MISSING")
                if int(signal["telegram_message_id"]) != message_id:
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_TELEGRAM_RECEIPT_MISMATCH")
                if abs(float(signal["telegram_sent_at"]) - sent_at) > 1e-6:
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_TELEGRAM_TIME_MISMATCH")
                try:
                    payload = json.loads(str(signal["payload_json"]))
                except json.JSONDecodeError:
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_SIGNAL_PAYLOAD_INVALID") from None
                if not isinstance(payload, dict):
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_SIGNAL_PAYLOAD_INVALID")
                if str(payload.get("order_id") or "") != order.order_id:
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_ORDER_ID_MISMATCH")
                if str(payload.get("token_id") or "") != order.token_id:
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_TOKEN_ID_MISMATCH")

                existing = self.db.execute(
                    "SELECT state_sha256 FROM weather_maker_shadow_orders WHERE order_id=?",
                    (order.order_id,),
                ).fetchone()
                if existing is not None:
                    linked = self.db.execute(
                        "SELECT 1 FROM weather_maker_shadow_events "
                        "WHERE order_id=? AND event_type='TELEGRAM_SIGNAL_LINKED' LIMIT 1",
                        (order.order_id,),
                    ).fetchone()
                    if str(existing["state_sha256"]) != state_sha or linked is None:
                        raise WeatherMakerStoreError("MAKER_ACTIVATION_PARTIAL_STATE")
                    self.db.execute(
                        "UPDATE weather_paper_signals SET status='MAKER_RESTING' WHERE id=?",
                        (sid,),
                    )
                    self.db.execute("COMMIT")
                    return self.load_order(order.order_id)

                self.db.execute(
                    """INSERT INTO weather_maker_shadow_orders
                       (order_id,store_version,state_json,state_sha256,updated_at,
                        actual_order_placed,actual_fill_authority,financial_authority)
                       VALUES (?,?,?,?,?,0,0,0)""",
                    (order.order_id, WEATHER_MAKER_STORE_VERSION, state_text, state_sha, at),
                )
                self._append_event(
                    order.order_id,
                    "ORDER_CREATED",
                    {
                        "state_sha256": state_sha,
                        "accounting_version": MAKER_PAPER_ACCOUNTING_V5_VERSION,
                        "financial_authority": False,
                    },
                    state_sha,
                    at,
                )
                self._append_event(
                    order.order_id,
                    "TELEGRAM_SIGNAL_LINKED",
                    {
                        "signal_id": sid,
                        "telegram_message_id": message_id,
                        "telegram_sent_at": sent_at,
                        "post_delivery_exact_finished_at": exact_finished,
                        "activation_atomic": True,
                        "actual_order_placed": False,
                        "actual_fill_authority": False,
                        "financial_authority": False,
                    },
                    state_sha,
                    at,
                )
                cur = self.db.execute(
                    "UPDATE weather_paper_signals SET status='MAKER_RESTING' WHERE id=?",
                    (sid,),
                )
                if cur.rowcount != 1:
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_SIGNAL_STATUS_LOST")
                self.db.execute("COMMIT")
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
        return order

    def reconcile_pending_delivery_without_receipt_after_restart(
        self, *, recorded_at: float | None = None
    ) -> int:
        """Conservatively terminalize ambiguous maker sends lacking a durable receipt.

        A row can reach PENDING_DELIVERY immediately before Telegram I/O. If the
        runtime later restarts without a saved Telegram receipt, we cannot prove
        whether the remote side accepted the message. Never retry or leave the row
        indefinitely pending: mark it DELIVERY_UNCERTAIN and preserve it for audit.
        """
        _nonnegative_time(
            time.time() if recorded_at is None else recorded_at,
            "MAKER_PENDING_RESTART_TIME_INVALID",
        )
        with self._db_lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                rows = self.db.execute(
                    "SELECT id FROM weather_paper_signals "
                    "WHERE lane='weather_maker_virtual_bid' "
                    "AND status='PENDING_DELIVERY' "
                    "AND telegram_message_id IS NULL "
                    "ORDER BY id"
                ).fetchall()
                for row in rows:
                    self.db.execute(
                        "UPDATE weather_paper_signals "
                        "SET status='DELIVERY_UNCERTAIN' WHERE id=?",
                        (int(row["id"]),),
                    )
                self.db.execute("COMMIT")
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
        return len(rows)

    def unactivated_receipt_signal_ids(self) -> list[int]:
        """Detect delivered maker receipts lacking a durable activated virtual order."""
        terminal = {
            "MAKER_RESTING",
            "MAKER_NOT_ACTIVATED",
            MAKER_RESTART_ORPHAN_STATUS,
            "DELIVERY_UNCERTAIN",
            "DELIVERY_FAILED",
            "EXPIRED",
        }
        out: list[int] = []
        with self._db_lock:
            rows = self.db.execute(
                "SELECT id,payload_json,status FROM weather_paper_signals "
                "WHERE lane='weather_maker_virtual_bid' "
                "AND telegram_message_id IS NOT NULL ORDER BY id"
            ).fetchall()
            for row in rows:
                if str(row["status"] or "") in terminal:
                    continue
                try:
                    payload = json.loads(str(row["payload_json"]))
                except json.JSONDecodeError:
                    raise WeatherMakerStoreError(
                        "MAKER_ACTIVATION_SIGNAL_PAYLOAD_INVALID"
                    ) from None
                if not isinstance(payload, dict):
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_SIGNAL_PAYLOAD_INVALID")
                order_id = str(payload.get("order_id") or "").strip()
                if not order_id:
                    raise WeatherMakerStoreError("MAKER_ACTIVATION_ORDER_ID_MISSING")
                existing = self.db.execute(
                    "SELECT 1 FROM weather_maker_shadow_orders WHERE order_id=?",
                    (order_id,),
                ).fetchone()
                if existing is None:
                    out.append(int(row["id"]))
        return out

    def reconcile_unactivated_receipts_after_restart(
        self, *, recorded_at: float | None = None
    ) -> int:
        """Never reconstruct a maker order after prospective WS continuity was lost."""
        at = time.time() if recorded_at is None else _nonnegative_time(
            recorded_at, "MAKER_ACTIVATION_RESTART_TIME_INVALID"
        )
        changed = 0
        with self._db_lock:
            try:
                self.db.execute("BEGIN IMMEDIATE")
                rows = self.db.execute(
                    "SELECT id,payload_json,status FROM weather_paper_signals "
                    "WHERE lane='weather_maker_virtual_bid' "
                    "AND telegram_message_id IS NOT NULL"
                ).fetchall()
                for row in rows:
                    status = str(row["status"] or "")
                    if status in {
                        "MAKER_RESTING",
                        "MAKER_NOT_ACTIVATED",
                        MAKER_RESTART_ORPHAN_STATUS,
                        "DELIVERY_UNCERTAIN",
                        "DELIVERY_FAILED",
                        "EXPIRED",
                    }:
                        continue
                    try:
                        payload = json.loads(str(row["payload_json"]))
                    except json.JSONDecodeError:
                        raise WeatherMakerStoreError(
                            "MAKER_ACTIVATION_SIGNAL_PAYLOAD_INVALID"
                        ) from None
                    if not isinstance(payload, dict):
                        raise WeatherMakerStoreError("MAKER_ACTIVATION_SIGNAL_PAYLOAD_INVALID")
                    order_id = str(payload.get("order_id") or "").strip()
                    if not order_id:
                        raise WeatherMakerStoreError("MAKER_ACTIVATION_ORDER_ID_MISSING")
                    existing = self.db.execute(
                        "SELECT 1 FROM weather_maker_shadow_orders WHERE order_id=?",
                        (order_id,),
                    ).fetchone()
                    if existing is not None:
                        continue
                    self.db.execute(
                        "UPDATE weather_paper_signals SET status=? WHERE id=?",
                        (MAKER_RESTART_ORPHAN_STATUS, int(row["id"])),
                    )
                    changed += 1
                self.db.execute("COMMIT")
            except Exception:
                if self.db.in_transaction:
                    self.db.execute("ROLLBACK")
                raise
        return changed

    def summary(self) -> dict:
        value = dict(super().summary())
        with self._db_lock:
            table = self.db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='weather_paper_signals'"
            ).fetchone()
            orphaned = 0
            if table is not None:
                orphaned = int(
                    self.db.execute(
                        "SELECT COUNT(*) FROM weather_paper_signals WHERE status=?",
                        (MAKER_RESTART_ORPHAN_STATUS,),
                    ).fetchone()[0]
                )
        value.update(
            {
                "version": MAKER_PAPER_ACCOUNTING_V5_VERSION,
                "base_accounting_version": MAKER_PAPER_ACCOUNTING_V4_VERSION,
                "atomic_activation_signal_link": True,
                "restart_unactivated_receipts": orphaned,
                "actual_order_placed": False,
                "actual_fill_authority": False,
                "financial_authority": False,
            }
        )
        return value
