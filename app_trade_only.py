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
from polymarket_scanner.runtime_manifest import build_runtime_manifest
from polymarket_scanner.runtime_resources import runtime_resource_snapshot
from polymarket_scanner.schema_contract import require_database_schema
from polymarket_scanner.settlement import exact_token_payout, selected_token_payout
from polymarket_scanner.shadow_telemetry import (
    SHADOW_MAX_ROWS,
    SHADOW_RETENTION_DAYS,
    SHADOW_SAMPLE_SECONDS,
    SHADOW_TELEMETRY_VERSION,
    maybe_record_shadow_health_state,
)
from polymarket_scanner.sports_v3 import SPORTS_MAPPING_VERSION, quarantine_pre_v3_sports_history
from polymarket_scanner.trade_only import (
    TRADE_READY_VERSION,
    is_trade_ready,
    mark_trade_readiness,
    promoted_detectors,
)
from polymarket_scanner.universe_reader import UniverseReader
from polymarket_scanner.universe_snapshot import RUNTIME_MODE, snapshot_directory
from polymarket_scanner.universe_builder import release_sha
from polymarket_scanner.shadow_execution import record_shadow_execution

app = stable_v2.app

# Canonical production input. This process never performs a Gamma universe crawl.
base.universe_source = UniverseReader(snapshot_directory(), producer_sha=release_sha(),
    priority=stable_v2.stable._market_priority, weather_universe=base._weather_universe)
base.poly.universe_status = base.universe_source.universe_status


async def _forbidden_scanner_crawl():
    raise RuntimeError("production universe discovery belongs to universe_builder")


base.poly.active_markets = _forbidden_scanner_crawl


def _accept_snapshot(prepared):
    base.universe_source.accept(prepared)
    stable = stable_v2.stable
    stable._full_tokens = tuple(prepared.tokens)
    stable._priority_tokens = prepared.priority_tokens
    stable._price_books = prepared.screening
    stable._price_snapshot_at = prepared.manifest["first_page_received_at"]
    stable._price_refresh_error = None
    base.poly._active_cache = prepared.markets
    base.state["stream_priority_tokens"] = len(prepared.priority_tokens)
    base.state["accepted_generation_id"] = prepared.manifest["generation_id"]
    base.state["runtime_mode"] = RUNTIME_MODE


base.accept_snapshot = _accept_snapshot

_original_confirm_actionable = base.confirm_actionable
_original_save_signal = base.store.save_signal
_original_set_state = base.store.set_state
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
    if base.universe_source is not None:
        return base.universe_source.screening_status()
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
    snapshot["universe_authority"] = base.poly.universe_status()
    snapshot["universe_safe_for_detection"] = snapshot["universe_authority"]["safe_for_detection"]
    snapshot["price_discovery_authority"] = _price_discovery_status()
    snapshot["operational_readiness"] = (
        "SCANNING_SILENT_SHADOW" if snapshot["universe_safe_for_detection"]
        and not snapshot["price_discovery_authority"]["stale"] else "WAITING_OR_DEGRADED_FAIL_CLOSED"
    )
    snapshot["feed_progress"] = feed_progress_snapshot(
        base.market_stream,
        base.sports_stream,
        base.crypto_stream,
    )
    snapshot["runtime_resources"] = runtime_resource_snapshot(db_path=base.settings.db_path)
    return snapshot


def _trade_only_set_state(key: str, value: str) -> None:
    """Persist normal state, then sample only the production health record.

    ``health_snapshot_loop`` already calls Store.set_state from ``asyncio.to_thread``.
    The latest-state write is allowed to finish and release the Store lock first;
    only then does the secret-free one-minute sampler perform its separate SQLite
    transaction. Shadow-evidence failures never rewrite the latest health record.
    """
    _original_set_state(key, value)
    if key != "scanner_health_snapshot":
        return
    try:
        sample = maybe_record_shadow_health_state(base.store, value)
    except Exception as exc:
        base.state["shadow_telemetry_error"] = f"{type(exc).__name__}: {exc}"
        return
    if sample is not None:
        base.state["shadow_telemetry"] = sample
        base.state["shadow_telemetry_error"] = None


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
    if not base.state.get("production_runtime_authority_complete"):
        base.state["detector_suppression_reason"] = "release/dependency/schema authority unavailable"
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
    for signal in signals:
        signal.metadata["universe_generation_id"] = universe.get("generation_id")
        signal.metadata["universe_filter_version"] = universe.get("filter_version")
        signal.metadata["screening_coverage_ratio"] = price["usable_coverage_ratio"]
    return signals


async def _trade_only_confirm(signal):
    # A candidate can sit in the post-processing queue after discovery authority
    # changes. Recheck the universe before any ACTIONABLE confirmation/persistence.
    universe = base.poly.universe_status()
    if not universe.get("safe_for_detection"):
        return None
    if signal.metadata.get("universe_generation_id") != universe.get("generation_id"):
        base.state["candidate_generation_mismatch_total"] = int(base.state.get("candidate_generation_mismatch_total") or 0) + 1
        return None
    from polymarket_scanner.backpressure import CANDIDATE_MAX_AGE_SECONDS
    age = time.time() - signal.created_at.timestamp()
    if not 0 <= age <= CANDIDATE_MAX_AGE_SECONDS:
        base.state["candidate_expired_total"] = int(base.state.get("candidate_expired_total") or 0) + 1
        return None
    await record_shadow_execution(signal, base.poly)
    confirmed = await _original_confirm_actionable(signal)
    current_universe = base.poly.universe_status()
    if not current_universe.get("safe_for_detection"):
        return None
    if signal.metadata.get("universe_generation_id") != current_universe.get("generation_id"):
        base.state["candidate_generation_mismatch_total"] = int(base.state.get("candidate_generation_mismatch_total") or 0) + 1
        return None
    if confirmed is None:
        signal.confidence = "WATCH"
        signal.metadata["confirmation_rejected"] = True
        mark_trade_readiness(signal)
        return signal  # Retain rejected execution observations in silent evidence.
    mark_trade_readiness(confirmed)
    return confirmed


def _trade_only_save_signal(signal):
    """Persist silent evidence; this release cannot create financial intent."""
    # Separate containment latch: even a mistakenly changed promotion registry
    # cannot make this silent-shadow release enqueue a financial instruction.
    signal.metadata["trade_ready"] = False
    signal.metadata["delivery_permission"] = "SILENT_SHADOW_DISABLED"
    return _original_save_signal(signal)


def _trade_only_enqueue(_signal_id, signal):
    # Future financial delivery has a separate tested atomic intent implementation.
    # Silent research never enters either delivery queue.
    return False


def _bounded_queue_detector_output(signals):
    """Coalesce all pending candidate batches into one bounded latest-state batch.

    Count, bytes and expiry bound all candidate classes. Overflow is explicit
    censored evidence, never a claim that every opportunity was processed.
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
        now=time.time(),
    )
    base.state["candidate_backpressure"] = stats
    base.state["candidate_overflow_total"] = int(base.state.get("candidate_overflow_total") or 0) + stats["overflow_dropped"]
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


_directional_settlement_cursor = 0
_structural_settlement_cursor = 0


async def _settle_directional_signals() -> None:
    global _directional_settlement_cursor
    rows, _directional_settlement_cursor = await asyncio.to_thread(
        base.store.open_directional_page, _directional_settlement_cursor
    )
    if not rows:
        return

    sem = asyncio.Semaphore(4)

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
    global _structural_settlement_cursor
    trades = await asyncio.to_thread(open_structural_trades, base.store, _structural_settlement_cursor)
    _structural_settlement_cursor = int(trades[-1]["id"]) if trades else 0
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
base.store.set_state = _trade_only_set_state
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
    db_schema = await asyncio.to_thread(require_database_schema, base.settings.db_path)
    from polymarket_scanner.structural_quarantine import quarantine_structural_history
    base.state["structural_claims_quarantined_now"] = await asyncio.to_thread(quarantine_structural_history, base.settings.db_path)
    quarantined = await asyncio.to_thread(quarantine_pre_v3_sports_history, base.settings.db_path)
    db_health = await asyncio.to_thread(database_health, base.settings.db_path)
    promoted = promoted_detectors()
    if promoted:
        raise RuntimeError("silent-shadow release requires exactly zero detector promotions")
    if base.settings.telegram_commands_in_app:
        raise RuntimeError("canonical shadow scanner requires external command-worker ownership")
    runtime_manifest = await asyncio.to_thread(
        build_runtime_manifest,
        promoted_detectors=promoted,
        trade_ready_version=TRADE_READY_VERSION,
    )
    runtime_manifest["database_schema"] = db_schema
    runtime_manifest["production_runtime_authority_complete"] = bool(
        runtime_manifest.get("production_runtime_authority_complete")
        and db_schema.get("compatible") is True
    )
    if not runtime_manifest["production_runtime_authority_complete"]:
        raise RuntimeError("canonical shadow runtime requires release, dependency, preflight and schema attestation")
    runtime_manifest["runtime_authority_scope"] = (
        str(runtime_manifest.get("runtime_authority_scope") or "")
        + "; DATABASE_SCHEMA_CONTRACT_REQUIRED_AT_STARTUP"
    )
    base.state["runtime_mode"] = RUNTIME_MODE
    base.state["telegram_delivery_mode"] = "SILENT_SHADOW_FINANCIAL_DELIVERY_DISABLED"
    base.state["delivery_persistence_mode"] = "SILENT_EVIDENCE_ONLY_NO_FINANCIAL_INTENT"
    base.state["silent_research_enabled"] = True
    base.state["trade_now_promoted_detectors"] = list(promoted)
    base.state["trade_now_promotion_count"] = len(promoted)
    base.state["p0_containment"] = len(promoted) == 0
    base.state["settlement_mode"] = "EXACT_TOKEN_PAYOUT_V2_DIRECTIONAL_PLUS_PER_LEG_STRUCTURAL"
    base.state["manual_accounting_mode"] = (
        "DIRECTIONAL_USER_REPORTED_ACTUAL_COST_V2_PLUS_STRUCTURAL_PER_LEG_V1"
    )
    base.state["structural_fill_evidence_exchange_verified"] = False
    base.state["sports_detector_version"] = SPORTS_MAPPING_VERSION
    base.state["sports_pre_v3_quarantined_now"] = quarantined
    base.state["runtime_manifest"] = runtime_manifest
    base.state["production_release_attested"] = runtime_manifest["production_release_attested"]
    base.state["production_runtime_authority_complete"] = runtime_manifest[
        "production_runtime_authority_complete"
    ]
    base.state["runtime_policy_sha256"] = runtime_manifest["nonsecret_safety_policy_sha256"]
    base.state["database_schema"] = db_schema
    base.state["shadow_telemetry"] = {
        "version": SHADOW_TELEMETRY_VERSION,
        "sample_interval_seconds": SHADOW_SAMPLE_SECONDS,
        "retention_days": SHADOW_RETENTION_DAYS,
        "max_rows": SHADOW_MAX_ROWS,
        "status": "ARMED_AFTER_SCHEMA_GATE",
    }
    base.state["shadow_telemetry_error"] = None
    base.state["universe_authority"] = base.poly.universe_status()
    base.state["universe_safe_for_detection"] = False
    base.state["price_discovery_authority"] = _price_discovery_status()
    base.state["signal_queue_max_batches"] = SIGNAL_QUEUE_MAX_BATCHES
    base.state["signal_watch_retention"] = RESEARCH_WATCH_RETENTION
    base.state["signal_backpressure_mode"] = "COALESCE_WITH_COUNT_BYTE_AGE_BOUNDS_AND_VISIBLE_OVERFLOW"
    base.state["sqlite_runtime"] = db_runtime
    base.state["sqlite_health"] = db_health


app.add_event_handler("startup", _mark_trade_only_runtime)
