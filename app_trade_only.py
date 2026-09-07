from __future__ import annotations

"""Production runtime: silent research, Telegram reserved for explicitly promoted TRADE NOW only.

Builds on the stable v2 scanner. Detector/research behavior and storage stay intact;
only delivery policy changes. WATCH/experimental signals remain stored/scored in the
background. During the P0 containment phase the promoted registry is intentionally
empty, so no financial alert may be queued for Telegram.

Production additionally treats market-data authority as a detector gate: an old
local market list may remain in memory for diagnostics, but a Gamma universe that is
truncated or older than the hard stale limit cannot generate new signals.
"""

import asyncio
import time

import app as base
import app_stable_v2 as stable_v2
from polymarket_scanner.atomic_delivery import persist_trade_now_intent
from polymarket_scanner.backpressure import (
    RESEARCH_WATCH_RETENTION,
    SIGNAL_QUEUE_MAX_BATCHES,
    coalesce_signal_batches,
)
from polymarket_scanner.db_ops import configure_database_runtime, database_health
from polymarket_scanner.feed_health import feed_progress_snapshot
from polymarket_scanner.manual_fills import (
    ensure_structural_fill_schema,
    open_structural_trades,
    resolve_structural_trade,
)
from polymarket_scanner.settlement import exact_token_payout, selected_token_payout
from polymarket_scanner.sports_v3 import quarantine_pre_v3_sports_history
from polymarket_scanner.trade_only import is_trade_ready, mark_trade_readiness, promoted_detectors

app = stable_v2.app

_original_confirm_actionable = base.confirm_actionable
_original_save_signal = base.store.save_signal
_original_evaluate_signals = base.evaluate_signals
_original_health_snapshot = base._health_snapshot

# Replace the unbounded base queue before FastAPI startup. base.signal_processing_loop
# resolves this module global at runtime, so the worker automatically consumes the
# bounded queue below without duplicating its processing logic.
base.signal_queue = asyncio.Queue(maxsize=SIGNAL_QUEUE_MAX_BATCHES)

# Whole-universe price snapshots are discovery data, not execution evidence. They
# may be incomplete because some tokens have no usable top ask. Surface that ratio
# explicitly, and suppress broad structural discovery if the snapshot itself stops
# advancing for several target intervals. Exact TRADE NOW execution is separately
# rebuilt from live CLOB books at delivery time.
_BROAD_PRICE_DEPENDENT = {
    "binary_buy_both", "neg_risk_underround", "nested_threshold_arb",
    "duplicate_divergence", "wide_spread",
}


def _price_discovery_status() -> dict:
    stable = stable_v2.stable
    total = len(stable._full_tokens)
    usable = len(stable._price_books)
    snapshot_at = stable._price_snapshot_at
    age = time.time() - float(snapshot_at) if snapshot_at is not None else None
    stale_after = max(90.0, float(stable.TOP_PRICE_REFRESH_SECONDS) * 3.0)
    stale = age is None or age > stale_after
    coverage = (usable / total) if total else 0.0
    return {
        "target_tokens": total,
        "usable_price_tokens": usable,
        "usable_coverage_ratio": coverage,
        "snapshot_at": snapshot_at,
        "snapshot_age_seconds": age,
        "stale_after_seconds": stale_after,
        "stale": stale,
        "last_error": stable._price_refresh_error,
    }


def _trade_health_snapshot() -> dict:
    snapshot = _original_health_snapshot()
    snapshot["feed_progress"] = feed_progress_snapshot(
        base.market_stream,
        base.sports_stream,
        base.crypto_stream,
    )
    return snapshot


def _trade_only_evaluate_signals(*args, **kwargs):
    """Fail closed when production discovery authority is stale/incomplete."""
    universe = base.poly.universe_status()
    price = _price_discovery_status()
    base.state["universe_authority"] = universe
    base.state["universe_safe_for_detection"] = bool(universe.get("safe_for_detection"))
    base.state["price_discovery_authority"] = price

    if not universe.get("safe_for_detection"):
        base.state["detector_suppression_reason"] = (
            "Gamma universe is truncated, missing, or beyond the hard stale limit"
        )
        return []

    signals = _original_evaluate_signals(*args, **kwargs)
    if price["stale"]:
        before = len(signals)
        signals = [signal for signal in signals if signal.detector not in _BROAD_PRICE_DEPENDENT]
        suppressed = before - len(signals)
        base.state["broad_price_signals_suppressed"] = suppressed
        base.state["detector_suppression_reason"] = (
            "whole-universe price discovery is stale; broad price-dependent detectors suppressed"
        )
    else:
        base.state["broad_price_signals_suppressed"] = 0
        base.state["detector_suppression_reason"] = None
    return signals


async def _trade_only_confirm(signal):
    # A candidate can sit in the post-processing queue after discovery authority
    # changes. Recheck the universe before any ACTIONABLE confirmation/persistence.
    universe = base.poly.universe_status()
    if not universe.get("safe_for_detection"):
        return None
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


def _bounded_queue_detector_output(signals):
    """Coalesce all pending candidate batches into one bounded latest-state batch.

    Every unique ACTIONABLE episode survives. Repeated observations are replaced by
    their newest copy, and only WATCH/research rows are capped. This prevents a slow
    SQLite/network phase from turning a burst of scanner wakes into an unbounded
    stale-work queue while preserving future financial-candidate availability.
    """
    if not signals:
        return

    pending_batches = []
    drained = 0
    while True:
        try:
            pending = base.signal_queue.get_nowait()
        except asyncio.QueueEmpty:
            break
        pending_batches.append(pending)
        base.signal_queue.task_done()
        drained += 1

    batch, stats = coalesce_signal_batches(
        [*pending_batches, signals],
        watch_limit=RESEARCH_WATCH_RETENTION,
    )
    if not batch:
        base.state["signal_batches_pending"] = base.signal_queue.qsize()
        return

    try:
        base.signal_queue.put_nowait(batch)
    except asyncio.QueueFull:
        base.state["signal_backpressure_error"] = "bounded signal queue unexpectedly full after coalescing"
        base.log.error(base.state["signal_backpressure_error"])
        return

    base.state["signal_batches_pending"] = base.signal_queue.qsize()
    base.state["signal_queue_max_batches"] = SIGNAL_QUEUE_MAX_BATCHES
    base.state["signal_batches_coalesced_total"] = int(
        base.state.get("signal_batches_coalesced_total") or 0
    ) + drained
    base.state["signal_duplicate_episodes_coalesced_total"] = int(
        base.state.get("signal_duplicate_episodes_coalesced_total") or 0
    ) + int(stats["duplicate_episodes_coalesced"])
    base.state["signal_watch_dropped_backpressure_total"] = int(
        base.state.get("signal_watch_dropped_backpressure_total") or 0
    ) + int(stats["watch_dropped"])
    base.state["signal_queue_last_coalesce"] = stats
    base.state["signal_backpressure_error"] = None


async def _silent_scanner_push(*_args, **_kwargs):
    return None


async def _settle_directional_signals() -> None:
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


async def _settle_structural_manual_fills() -> None:
    """Resolve exact per-leg structural executions only when every token is final."""
    trades = await asyncio.to_thread(open_structural_trades, base.store)
    if not trades:
        return

    market_ids = sorted({
        str(leg.get("market_id") or "")
        for trade in trades
        for leg in (trade.get("legs") or [])
        if str(leg.get("market_id") or "")
    })
    if not market_ids:
        return

    sem = asyncio.Semaphore(8)

    async def fetch(mid: str):
        async with sem:
            return mid, await base.poly.market_by_id(mid)

    results = await asyncio.gather(*(fetch(mid) for mid in market_ids), return_exceptions=True)
    markets = {
        mid: market
        for result in results
        if isinstance(result, tuple)
        for mid, market in [result]
        if isinstance(market, dict)
    }

    for trade in trades:
        payouts: dict[str, float] = {}
        complete = True
        for leg in trade.get("legs") or []:
            token = str(leg.get("token_id") or "")
            market = markets.get(str(leg.get("market_id") or ""))
            payout = exact_token_payout(token, market) if market is not None else None
            if payout is None:
                complete = False
                break
            payouts[token] = payout
        if not complete or not payouts:
            continue
        try:
            resolved = await asyncio.to_thread(
                resolve_structural_trade,
                base.store,
                int(trade["id"]),
                payouts,
            )
            if resolved is not None:
                base.log.info(
                    "resolved structural manual trade %s: payout/bundle=%.6f pnl=%.6f",
                    trade["id"],
                    resolved["total_payout_per_bundle"],
                    resolved["pnl"],
                )
        except Exception as exc:
            base.log.warning("structural per-leg settlement failed for %s: %r", trade.get("id"), exc)


async def _payout_aware_settlement() -> None:
    await _settle_directional_signals()
    await _settle_structural_manual_fills()


base.evaluate_signals = _trade_only_evaluate_signals
base.confirm_actionable = _trade_only_confirm
base.store.save_signal = _trade_only_save_signal
base.enqueue_alert = _trade_only_enqueue
base.queue_detector_output = _bounded_queue_detector_output
base.settle_open_paper_trades = _payout_aware_settlement
base._health_snapshot = _trade_health_snapshot
base.tg.send = _silent_scanner_push


async def _mark_trade_only_runtime() -> None:
    # Configure WAL/quick-check off the event loop before declaring this runtime
    # healthy. WAL mode is persistent for the database file and benefits the separate
    # command-worker process as well.
    db_runtime = await asyncio.to_thread(configure_database_runtime, base.settings.db_path)
    await asyncio.to_thread(ensure_structural_fill_schema, base.store)
    quarantined = await asyncio.to_thread(quarantine_pre_v3_sports_history, base.settings.db_path)
    db_health = await asyncio.to_thread(database_health, base.settings.db_path)
    promoted = promoted_detectors()
    base.state["telegram_delivery_mode"] = "TRADE_NOW_ONLY"
    base.state["delivery_persistence_mode"] = "ATOMIC_SIGNAL_OUTBOX_V1"
    base.state["silent_research_enabled"] = True
    base.state["trade_now_promoted_detectors"] = list(promoted)
    base.state["trade_now_promotion_count"] = len(promoted)
    base.state["p0_containment"] = len(promoted) == 0
    base.state["settlement_mode"] = "EXACT_TOKEN_PAYOUT_V2_DIRECTIONAL_PLUS_PER_LEG_STRUCTURAL"
    base.state["manual_accounting_mode"] = (
        "DIRECTIONAL_USER_REPORTED_ACTUAL_COST_V2_PLUS_STRUCTURAL_PER_LEG_V1"
    )
    base.state["structural_fill_evidence_exchange_verified"] = False
    base.state["sports_detector_version"] = "home_away_v3_match_moneyline_only"
    base.state["sports_pre_v3_quarantined_now"] = quarantined
    base.state["universe_authority"] = base.poly.universe_status()
    base.state["universe_safe_for_detection"] = False
    base.state["price_discovery_authority"] = _price_discovery_status()
    base.state["signal_queue_max_batches"] = SIGNAL_QUEUE_MAX_BATCHES
    base.state["signal_watch_retention"] = RESEARCH_WATCH_RETENTION
    base.state["signal_backpressure_mode"] = "COALESCE_DUPLICATES_KEEP_ALL_UNIQUE_ACTIONABLE_BOUND_WATCH"
    base.state["sqlite_runtime"] = db_runtime
    base.state["sqlite_health"] = db_health


app.add_event_handler("startup", _mark_trade_only_runtime)