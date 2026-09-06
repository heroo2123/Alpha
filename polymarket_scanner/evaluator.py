from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .detectors import duplicate_divergence, weather_late_lock, wide_spread_watch
from .detectors_v02 import (
    crypto_crossfeed_divergence,
    crypto_resolution_lag,
    official_macro_release_lag,
    sports_result_lag,
)
from .hardening import (
    hardened_binary_buy_both,
    hardened_neg_risk_underround,
    hardened_nested_threshold_arbitrage,
)
from .macro import MacroClient
from .models import Book, Market, Signal
from .streams import CryptoRTDS
from .config import settings

log = logging.getLogger("polybot.evaluator")

_last_structural_at = 0.0
_last_expensive_watch_at = 0.0


def _safe(name: str, fn: Callable, *args) -> list[Signal]:
    """Keep one detector failure from discarding the rest of a scan pass."""
    try:
        return list(fn(*args))
    except Exception as exc:
        # Crypto RTDS is updated by the asyncio WebSocket task while this worker
        # thread reads it. A rare concurrent-cache race should merely skip that
        # detector for one pass; REST confirmation still protects ACTIONABLE legs.
        log.warning("detector %s failed in worker: %r", name, exc)
        return []


def _apply_live_bbo(markets: list[Market], books: dict[str, Book]) -> None:
    for market in markets:
        book = books.get(market.yes_token or "")
        if book:
            market.best_bid = book.best_bid
            market.best_ask = book.best_ask


def evaluate_signals(
    markets: list[Market],
    books: dict[str, Book],
    weather_markets: list[Market],
    weather_cache: dict[str, list],
    sports_cache: dict[str, dict],
    crypto_stream: CryptoRTDS,
    macro: MacroClient,
    *,
    fast_market: bool,
    weather_refreshed: bool,
    sports_trigger: bool,
    crypto_trigger: bool,
    macro_refreshed: bool,
    run_watch: bool,
) -> list[Signal]:
    """CPU-heavy detector pass intended to run via ``asyncio.to_thread``.

    The scanner follows ~13k markets on a shared-core e2-micro. Market WebSocket
    changes can arrive continuously, so re-running every expensive whole-universe
    structural/grouping detector on every book tick will eventually starve the
    process. Weather/sports/crypto remain event-driven; full structural and WATCH
    passes are deliberately rate-limited by the production CPU budget.
    """
    global _last_structural_at, _last_expensive_watch_at

    now = time.monotonic()
    structural_due = bool(
        fast_market
        and (
            _last_structural_at <= 0
            or now - _last_structural_at >= settings.structural_scan_min_interval_seconds
        )
    )
    watch_due = bool(
        run_watch
        and (
            _last_expensive_watch_at <= 0
            or now - _last_expensive_watch_at >= settings.expensive_watch_min_interval_seconds
        )
    )

    _apply_live_bbo(markets, books)
    signals: list[Signal] = []

    if structural_due:
        _last_structural_at = now
        signals.extend(_safe("binary_buy_both", hardened_binary_buy_both, markets, books))
        signals.extend(_safe("neg_risk_underround", hardened_neg_risk_underround, markets, books))
        signals.extend(_safe("nested_threshold_arb", hardened_nested_threshold_arbitrage, markets, books))

    # Weather remains price-event driven because it only scans the much smaller
    # weather subset and these late-day opportunities can move quickly.
    if weather_cache and (fast_market or weather_refreshed):
        signals.extend(_safe("weather_late_lock", weather_late_lock, weather_markets, books, weather_cache))

    # Known-result/known-boundary feeds remain live. These loops are substantially
    # cheaper than the structural grouping/threshold pass and need prompt repricing.
    if fast_market or sports_trigger:
        signals.extend(_safe("sports_result_lag", sports_result_lag, markets, books, sports_cache))

    if fast_market or crypto_trigger:
        signals.extend(_safe("crypto_resolution_lag", crypto_resolution_lag, markets, books, crypto_stream))

    # Macro releases are rare. Re-check on a fresh official value and on each
    # structural cadence rather than on every market-book tick.
    if macro_refreshed or structural_due:
        signals.extend(_safe("official_macro_release_lag", official_macro_release_lag, markets, books, macro))

    if watch_due:
        _last_expensive_watch_at = now
        signals.extend(_safe("crypto_crossfeed_divergence", crypto_crossfeed_divergence, markets, crypto_stream))
        signals.extend(_safe("duplicate_divergence", duplicate_divergence, list(markets)))
        signals.extend(_safe("wide_spread", wide_spread_watch, list(markets)))

    signals.sort(key=lambda signal: (signal.confidence != "ACTIONABLE", -(signal.edge or 0)))
    return signals
