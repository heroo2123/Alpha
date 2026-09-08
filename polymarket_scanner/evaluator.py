from __future__ import annotations

import logging
import time
from collections.abc import Callable

from .config import settings
from .crypto_v3 import (
    crypto_crossfeed_divergence_v3 as crypto_crossfeed_divergence,
    crypto_resolution_lag_v3 as crypto_resolution_lag,
)
from .detectors import duplicate_divergence, weather_late_lock, wide_spread_watch
from .detectors_v02 import official_macro_release_lag
from .hardening import (
    hardened_binary_buy_both,
    hardened_neg_risk_underround,
    hardened_nested_threshold_arbitrage,
)
from .macro import MacroClient
from .models import Book, Market, Signal
from .sports_v3 import sports_result_lag_v3 as sports_result_lag
from .streams import CryptoRTDS
from .weather_contracts import (
    WEATHER_CONTRACT_ADAPTER,
    WEATHER_FRIEND_MODEL_VERSION,
    WEATHER_LATE_MODEL_VERSION,
    settlement_safe_weather_cache,
    settlement_safe_weather_markets,
)
from .weather_friend import friend_style_weather_lock

log = logging.getLogger("polybot.evaluator")

_last_structural_at = 0.0
_last_expensive_watch_at = 0.0
_last_weather_fast_at = 0.0
_last_crypto_resolution_at = 0.0
_current_detector: str | None = None
_current_detector_started = 0.0
_last_detector: str | None = None
_last_detector_seconds: float | None = None


def detector_runtime_status() -> dict:
    now = time.monotonic()
    return {
        "current_detector": _current_detector,
        "current_detector_seconds": round(now - _current_detector_started, 3) if _current_detector and _current_detector_started else None,
        "last_detector": _last_detector,
        "last_detector_seconds": _last_detector_seconds,
    }


def _safe(name: str, fn: Callable, *args) -> list[Signal]:
    global _current_detector, _current_detector_started, _last_detector, _last_detector_seconds
    _current_detector = name
    _current_detector_started = time.monotonic()
    try:
        return list(fn(*args))
    except Exception as exc:
        log.warning("detector %s failed in worker: %r", name, exc)
        return []
    finally:
        elapsed = time.monotonic() - _current_detector_started
        _last_detector = name
        _last_detector_seconds = round(elapsed, 3)
        _current_detector = None
        _current_detector_started = 0.0


def _apply_live_bbo(markets: list[Market], books: dict[str, Book]) -> None:
    for market in markets:
        book = books.get(market.yes_token or "")
        if book:
            market.best_bid = book.best_bid
            market.best_ask = book.best_ask


def _version_weather_signals(signals: list[Signal], model_version: str) -> list[Signal]:
    """Stamp prospective weather evidence without overstating observation authority.

    The contract adapter can prove which settlement source the market names, but the
    current temperature observations still come from AviationWeather METAR. That is
    a useful official proxy and may be highly correlated with WRH, but it has not
    been proven identical to the rule-selected WRH display population, precision,
    revision state or fallback branch. Keep those two authorities separate so proxy
    observations cannot silently become clean empirical calibration evidence.
    """
    for signal in signals:
        signal.metadata["weather_model_version"] = model_version
        signal.metadata["weather_contract_adapter"] = WEATHER_CONTRACT_ADAPTER
        signal.metadata["weather_contract_temporal_safe"] = True
        signal.metadata["weather_contract_source_verified"] = bool(
            signal.metadata.get("settlement_source_verified") is True
        )
        signal.metadata["weather_observation_adapter"] = "AVIATION_WEATHER_METAR_PROXY_V1"
        signal.metadata["weather_observation_source_kind"] = "official_proxy_not_settlement_table"
        signal.metadata["weather_observation_settlement_authority"] = False
        signal.metadata["weather_calibration_eligible_observations"] = False
    return signals


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
    global _last_structural_at, _last_expensive_watch_at, _last_weather_fast_at, _last_crypto_resolution_at

    now = time.monotonic()
    structural_due = bool(
        fast_market
        and (_last_structural_at <= 0 or now - _last_structural_at >= settings.structural_scan_min_interval_seconds)
    )
    watch_due = bool(
        run_watch
        and (_last_expensive_watch_at <= 0 or now - _last_expensive_watch_at >= settings.expensive_watch_min_interval_seconds)
    )
    weather_due = bool(
        weather_refreshed
        or (
            fast_market
            and (_last_weather_fast_at <= 0 or now - _last_weather_fast_at >= settings.weather_fast_scan_min_interval_seconds)
        )
    )
    crypto_due = bool(
        crypto_trigger
        and (_last_crypto_resolution_at <= 0 or now - _last_crypto_resolution_at >= settings.crypto_resolution_scan_min_interval_seconds)
    )

    _apply_live_bbo(markets, books)
    signals: list[Signal] = []

    if structural_due:
        _last_structural_at = now
        signals.extend(_safe("binary_buy_both", hardened_binary_buy_both, markets, books))
        signals.extend(_safe("neg_risk_underround", hardened_neg_risk_underround, markets, books))
        signals.extend(_safe("nested_threshold_arb", hardened_nested_threshold_arbitrage, markets, books))

    if weather_cache and weather_due:
        _last_weather_fast_at = now
        # The strict boundary owns source host/station, explicit bucket units,
        # required market date, causal observation timestamps and forecast freshness.
        # Legacy detector math sees only these sanitized copies. Contract authority
        # is deliberately kept separate from the still-proxy AWC observation feed.
        certified_weather = settlement_safe_weather_markets(weather_markets)
        certified_cache = settlement_safe_weather_cache(weather_cache)
        late = _safe("weather_late_lock", weather_late_lock, certified_weather, books, certified_cache)
        friend = _safe("weather_friend_lock", friend_style_weather_lock, certified_weather, books, certified_cache)
        signals.extend(_version_weather_signals(late, WEATHER_LATE_MODEL_VERSION))
        signals.extend(_version_weather_signals(friend, WEATHER_FRIEND_MODEL_VERSION))

    # Compatibility symbol name retained for runtime-responsiveness monkeypatch tests;
    # the function bound to it is the fail-closed v3 match-moneyline adapter.
    if sports_trigger:
        signals.extend(_safe("sports_result_lag_v3", sports_result_lag, markets, books, sports_cache))

    if crypto_due:
        _last_crypto_resolution_at = now
        signals.extend(_safe("crypto_resolution_lag", crypto_resolution_lag, markets, books, crypto_stream))

    if macro_refreshed or structural_due:
        signals.extend(_safe("official_macro_release_lag", official_macro_release_lag, markets, books, macro))

    if watch_due:
        _last_expensive_watch_at = now
        signals.extend(_safe("crypto_crossfeed_divergence", crypto_crossfeed_divergence, markets, crypto_stream))
        signals.extend(_safe("duplicate_divergence", duplicate_divergence, list(markets)))
        signals.extend(_safe("wide_spread", wide_spread_watch, list(markets)))

    signals.sort(key=lambda signal: (signal.confidence != "ACTIONABLE", -(signal.edge or 0)))
    return signals
