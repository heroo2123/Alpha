from __future__ import annotations

"""Maker-specific PAPER lifecycle and settlement accounting.

The ordinary weather position table assumes an immediate simulated taker fill.  Maker
orders are different: they can rest, partially fill over time, then cancel/expire while
retaining a real simulated inventory quantity.  Reusing the taker position table would
silently over/under-count those fills.

This layer therefore keeps maker lifecycle state in the existing hash-chained maker
store and records final exact-token payout as another immutable research event.  A
process restart cancels all still-resting virtual orders because prospective WebSocket
coverage was interrupted; already simulated shares remain eligible for later PAPER
settlement.  No actual order/fill or financial authority is introduced.
"""

import json
import math
import time
from dataclasses import replace
from pathlib import Path

from .weather_only_maker_shadow import (
    CANCELLED,
    PARTIALLY_SIMULATED,
    RESTING,
    SIMULATED_FILLED,
    VirtualMakerOrder,
)
from .weather_only_maker_store import WeatherMakerShadowStore, WeatherMakerStoreError


MAKER_PAPER_ACCOUNTING_VERSION = "weather_maker_paper_accounting_v1_hash_chain_exact_fill_quantity"
MAKER_SETTLEMENT_EVENT = "PAPER_SETTLED"


class MakerPaperAccountingError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise MakerPaperAccountingError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise MakerPaperAccountingError(code) from None
    if not math.isfinite(number):
        raise MakerPaperAccountingError(code)
    return number


class MakerPaperAccountingStore(WeatherMakerShadowStore):
    def _order_ids(self) -> list[str]:
        rows = self.db.execute(
            "SELECT order_id FROM weather_maker_shadow_orders ORDER BY order_id"
        ).fetchall()
        return [str(row[0]) for row in rows]

    def orders(self) -> list[VirtualMakerOrder]:
        return [self.load_order(order_id) for order_id in self._order_ids()]

    def active_orders(self) -> list[VirtualMakerOrder]:
        return [
            order for order in self.orders()
            if order.status in {RESTING, PARTIALLY_SIMULATED}
        ]

    def active_token_ids(self) -> set[str]:
        return {order.token_id for order in self.active_orders()}

    def _has_event(self, order_id: str, event_type: str) -> bool:
        row = self.db.execute(
            "SELECT 1 FROM weather_maker_shadow_events WHERE order_id=? AND event_type=? LIMIT 1",
            (str(order_id), str(event_type).upper()),
        ).fetchone()
        return row is not None

    def cancel_open_after_restart(self, *, recorded_at: float | None = None) -> int:
        at = time.time() if recorded_at is None else _finite(
            recorded_at, "MAKER_ACCOUNTING_RESTART_TIME_INVALID"
        )
        if at < 0.0:
            raise MakerPaperAccountingError("MAKER_ACCOUNTING_RESTART_TIME_INVALID")
        changed = 0
        for previous in self.active_orders():
            updated = replace(previous, status=CANCELLED)
            self.update_order(
                previous,
                updated,
                event_type="PROCESS_RESTART_COVERAGE_GAP",
                payload={
                    "version": MAKER_PAPER_ACCOUNTING_VERSION,
                    "reason": "PROSPECTIVE_MARKET_WS_CONTINUITY_LOST",
                    "simulated_filled_shares_preserved": previous.simulated_filled_shares,
                    "actual_order_placed": False,
                    "actual_fill_authority": False,
                    "financial_authority": False,
                },
                recorded_at=at,
            )
            changed += 1
        return changed

    def unsettled_filled_orders(self) -> list[VirtualMakerOrder]:
        return [
            order for order in self.orders()
            if order.simulated_filled_shares > 0.0
            and not self._has_event(order.order_id, MAKER_SETTLEMENT_EVENT)
        ]

    def record_settlement(
        self,
        order: VirtualMakerOrder,
        *,
        payout_per_share: float,
        evidence: dict,
        settled_at: float | None = None,
    ) -> dict:
        if not isinstance(order, VirtualMakerOrder):
            raise MakerPaperAccountingError("MAKER_ACCOUNTING_ORDER_INVALID")
        if order.simulated_filled_shares <= 0.0:
            raise MakerPaperAccountingError("MAKER_ACCOUNTING_NO_SIMULATED_FILL")
        payout = _finite(payout_per_share, "MAKER_ACCOUNTING_PAYOUT_INVALID")
        if not 0.0 <= payout <= 1.0:
            raise MakerPaperAccountingError("MAKER_ACCOUNTING_PAYOUT_INVALID")
        if not isinstance(evidence, dict):
            raise MakerPaperAccountingError("MAKER_ACCOUNTING_EVIDENCE_INVALID")
        if self._has_event(order.order_id, MAKER_SETTLEMENT_EVENT):
            existing = self.settlement(order.order_id)
            if existing is None:
                raise MakerPaperAccountingError("MAKER_ACCOUNTING_SETTLEMENT_EVENT_INVALID")
            return existing

        at = time.time() if settled_at is None else _finite(
            settled_at, "MAKER_ACCOUNTING_SETTLED_AT_INVALID"
        )
        shares = float(order.simulated_filled_shares)
        capital = shares * float(order.bid_price)
        proceeds = shares * payout
        pnl = proceeds - capital
        roi = pnl / capital if capital > 0.0 else None
        payload = {
            "version": MAKER_PAPER_ACCOUNTING_VERSION,
            "order_id": order.order_id,
            "event_id": order.event_id,
            "market_id": order.market_id,
            "condition_id": order.condition_id,
            "token_id": order.token_id,
            "outcome": order.outcome,
            "simulated_filled_shares": shares,
            "entry_price_per_share": float(order.bid_price),
            "simulated_capital_used": capital,
            "payout_per_share": payout,
            "paper_proceeds": proceeds,
            "paper_pnl": pnl,
            "paper_roi": roi,
            "settled_at": at,
            "evidence": dict(evidence),
            "simulated_fill_only": True,
            "actual_order_placed": False,
            "actual_fill_authority": False,
            "financial_authority": False,
        }
        self.append_research_event(
            order.order_id,
            event_type=MAKER_SETTLEMENT_EVENT,
            payload=payload,
            recorded_at=at,
        )
        return payload

    def settlement(self, order_id: str) -> dict | None:
        row = self.db.execute(
            """
            SELECT payload_json FROM weather_maker_shadow_events
            WHERE order_id=? AND event_type=? ORDER BY seq DESC LIMIT 1
            """,
            (str(order_id), MAKER_SETTLEMENT_EVENT),
        ).fetchone()
        if row is None:
            return None
        try:
            payload = json.loads(str(row[0]))
        except json.JSONDecodeError:
            raise WeatherMakerStoreError("MAKER_STORE_EVENT_PAYLOAD_JSON_INVALID") from None
        if not isinstance(payload, dict):
            raise WeatherMakerStoreError("MAKER_STORE_EVENT_PAYLOAD_JSON_INVALID")
        return payload

    def summary(self) -> dict:
        orders = self.orders()
        statuses: dict[str, int] = {}
        total_simulated_shares = 0.0
        for order in orders:
            statuses[order.status] = statuses.get(order.status, 0) + 1
            total_simulated_shares += float(order.simulated_filled_shares)
        settled = sum(
            1 for order in orders if self._has_event(order.order_id, MAKER_SETTLEMENT_EVENT)
        )
        return {
            "version": MAKER_PAPER_ACCOUNTING_VERSION,
            "orders_total": len(orders),
            "active_orders": sum(
                count for status, count in statuses.items()
                if status in {RESTING, PARTIALLY_SIMULATED}
            ),
            "simulated_filled_orders": sum(
                1 for order in orders if order.simulated_filled_shares > 0.0
            ),
            "settled_orders": settled,
            "total_simulated_filled_shares": total_simulated_shares,
            "status_counts": statuses,
            "actual_order_placed": False,
            "actual_fill_authority": False,
            "financial_authority": False,
        }
