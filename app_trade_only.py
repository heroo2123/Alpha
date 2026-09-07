from __future__ import annotations

"""Production runtime: silent research, Telegram reserved for explicitly promoted TRADE NOW only.

Builds on the stable v2 scanner. Detector/research behavior and storage stay intact;
only delivery policy changes. WATCH/experimental signals remain stored/scored in the
background. During the P0 containment phase the promoted registry is intentionally
empty, so no financial alert may be queued for Telegram.
"""

import asyncio

import app as base
import app_stable_v2 as stable_v2
from polymarket_scanner.atomic_delivery import persist_trade_now_intent
from polymarket_scanner.settlement import selected_token_payout
from polymarket_scanner.sports_v3 import quarantine_pre_v3_sports_history
from polymarket_scanner.trade_only import is_trade_ready, mark_trade_readiness, promoted_detectors

app = stable_v2.app

_original_confirm_actionable = base.confirm_actionable
_original_save_signal = base.store.save_signal


async def _trade_only_confirm(signal):
    confirmed = await _original_confirm_actionable(signal)
    if confirmed is None:
        return None
    mark_trade_readiness(confirmed)
    return confirmed


def _trade_only_save_signal(signal):
    """Persist promoted financial intent atomically; keep research storage generic."""
    if is_trade_ready(signal):
        return persist_trade_now_intent(base.store, signal, priority=0)
    return _original_save_signal(signal)


def _trade_only_enqueue(_signal_id, signal):
    # TRADE NOW signals were already inserted into telegram_outbox in the same
    # SQLite transaction as their signal row. Never create an intermediate volatile
    # alert queue hop. Silent research remains stored but is not delivered.
    return is_trade_ready(signal)


async def _silent_scanner_push(*_args, **_kwargs):
    return None


async def _payout_aware_settlement() -> None:
    rows = [
        row for row in await asyncio.to_thread(base.store.open_directional)
        if row["detector"] not in {"binary_buy_both", "neg_risk_underround", "nested_threshold_arb"}
    ]
    if not rows:
        return

    sem = asyncio.Semaphore(8)

    async def fetch(row: dict):
        async with sem:
            return row, await base.poly.market_by_id(str(row["market_id"]))

    results = await asyncio.gather(*(fetch(row) for row in rows), return_exceptions=True)
    for result in results:
        if not isinstance(result, tuple):
            continue
        row, market = result
        payout = selected_token_payout(row, market)
        if payout is None:
            continue
        try:
            await asyncio.to_thread(
                base.store.resolve_payout,
                int(row["id"]),
                payout,
                base.settings.paper_stake_usd,
            )
        except Exception as exc:
            base.log.warning("payout-aware settlement failed for %s: %r", row.get("id"), exc)


base.confirm_actionable = _trade_only_confirm
base.store.save_signal = _trade_only_save_signal
base.enqueue_alert = _trade_only_enqueue
base.settle_open_paper_trades = _payout_aware_settlement
base.tg.send = _silent_scanner_push


async def _mark_trade_only_runtime() -> None:
    quarantined = await asyncio.to_thread(quarantine_pre_v3_sports_history, base.settings.db_path)
    promoted = promoted_detectors()
    base.state["telegram_delivery_mode"] = "TRADE_NOW_ONLY"
    base.state["delivery_persistence_mode"] = "ATOMIC_SIGNAL_OUTBOX_V1"
    base.state["silent_research_enabled"] = True
    base.state["trade_now_promoted_detectors"] = list(promoted)
    base.state["trade_now_promotion_count"] = len(promoted)
    base.state["p0_containment"] = len(promoted) == 0
    base.state["settlement_mode"] = "EXACT_TOKEN_PAYOUT_V1"
    base.state["manual_accounting_mode"] = "USER_REPORTED_ACTUAL_COST_V1"
    base.state["sports_detector_version"] = "home_away_v3_match_moneyline_only"
    base.state["sports_pre_v3_quarantined_now"] = quarantined


app.add_event_handler("startup", _mark_trade_only_runtime)
