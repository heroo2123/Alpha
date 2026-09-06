from __future__ import annotations

import logging
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

log = logging.getLogger("polybot.evaluator")


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

    The scanner follows ~13k markets. Keeping regex/grouping/comparison work on
    the uvicorn event loop can starve Telegram polling and WebSocket heartbeats
    for tens of seconds on an e2-micro. This worker owns all synchronous detector
    evaluation; async REST confirmation still happens on the main loop afterward.
    """
    _apply_live_bbo(markets, books)
    signals: list[Signal] = []

    if fast_market:
        signals.extend(_safe("binary_buy_both", hardened_binary_buy_both, markets, books))
        signals.extend(_safe("neg_risk_underround", hardened_neg_risk_underround, markets, books))
        signals.extend(_safe("nested_threshold_arb", hardened_nested_threshold_arbitrage, markets, books))

    if weather_cache and (fast_market or weather_refreshed):
        signals.extend(_safe("weather_late_lock", weather_late_lock, weather_markets, books, weather_cache))

    if fast_market or sports_trigger:
        signals.extend(_safe("sports_result_lag", sports_result_lag, markets, books, sports_cache))

    if fast_market or crypto_trigger:
        signals.extend(_safe("crypto_resolution_lag", crypto_resolution_lag, markets, books, crypto_stream))

    if fast_market or macro_refreshed:
        signals.extend(_safe("official_macro_release_lag", official_macro_release_lag, markets, books, macro))

    if run_watch:
        signals.extend(_safe("crypto_crossfeed_divergence", crypto_crossfeed_divergence, markets, crypto_stream))
        signals.extend(_safe("duplicate_divergence", duplicate_divergence, list(markets)))
        signals.extend(_safe("wide_spread", wide_spread_watch, list(markets)))

    signals.sort(key=lambda signal: (signal.confidence != "ACTIONABLE", -(signal.edge or 0)))
    return signals
