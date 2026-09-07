from __future__ import annotations

import math
import time

from .crypto_v3 import FEED_PROGRESS_MAX_AGE_SECONDS, SOURCE_FUTURE_TOLERANCE_SECONDS
from .sports_v3 import _sports_source_timestamp


def _age(now: float, value: object) -> float | None:
    try:
        ts = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(ts) or ts <= 0:
        return None
    return max(0.0, now - ts) if ts <= now else -(ts - now)


def feed_progress_snapshot(market_stream, sports_stream, crypto_stream, *, now: float | None = None) -> dict:
    """Describe valid data progress separately from transport heartbeats.

    This function is intentionally diagnostic. Individual detector adapters retain
    their own stricter source/time gates. Health must nevertheless show whether a
    technically connected socket is delivering usable market/result/reference data.
    """
    current = time.time() if now is None else float(now)

    market_valid = getattr(market_stream, "last_valid_update_at", None)
    market_full = getattr(market_stream, "last_full_book_at", None)
    market_books = getattr(market_stream, "books", {})
    market = {
        "connected_workers": int(getattr(market_stream, "connected_workers", 0) or 0),
        "cached_synchronized_books": len(market_books) if isinstance(market_books, dict) else 0,
        "last_transport_message_at": getattr(market_stream, "last_message_at", None),
        "last_valid_book_update_at": market_valid,
        "last_valid_book_update_age_seconds": _age(current, market_valid),
        "last_full_book_at": market_full,
        "last_full_book_age_seconds": _age(current, market_full),
        "books_invalidated_total": int(getattr(market_stream, "invalidated_books", 0) or 0),
        "out_of_order_ignored_total": int(getattr(market_stream, "out_of_order_ignored", 0) or 0),
    }

    sports_cache = getattr(sports_stream, "results", {})
    sports_source_times: list[float] = []
    if isinstance(sports_cache, dict):
        for payload in sports_cache.values():
            if isinstance(payload, dict):
                ts = _sports_source_timestamp(payload)
                if ts is not None and math.isfinite(float(ts)):
                    sports_source_times.append(float(ts))
    sports_latest = max(sports_source_times) if sports_source_times else None
    sports = {
        "connected": bool(getattr(sports_stream, "connected", False)),
        "cached_result_payloads": len(sports_cache) if isinstance(sports_cache, dict) else 0,
        "payloads_with_source_time": len(sports_source_times),
        "last_transport_message_at": getattr(sports_stream, "last_message_at", None),
        "latest_source_timestamp": sports_latest,
        "latest_source_age_seconds": _age(current, sports_latest),
        "last_error": getattr(sports_stream, "last_error", None),
    }

    latest_ticks = getattr(crypto_stream, "latest_ticks", {})
    tick_times: list[float] = []
    fresh = stale = future = invalid = 0
    if isinstance(latest_ticks, dict):
        for tick in latest_ticks.values():
            try:
                ts = float(tick.ts)
                price = float(tick.price)
            except (AttributeError, TypeError, ValueError, OverflowError):
                invalid += 1
                continue
            if not math.isfinite(ts) or not math.isfinite(price) or ts <= 0 or price <= 0:
                invalid += 1
                continue
            tick_times.append(ts)
            age = current - ts
            if age < -SOURCE_FUTURE_TOLERANCE_SECONDS:
                future += 1
            elif age <= FEED_PROGRESS_MAX_AGE_SECONDS:
                fresh += 1
            else:
                stale += 1
    crypto_latest = max(tick_times) if tick_times else None
    crypto = {
        "connected": bool(getattr(crypto_stream, "connected", False)),
        "latest_tick_keys": len(latest_ticks) if isinstance(latest_ticks, dict) else 0,
        "fresh_tick_keys": fresh,
        "stale_tick_keys": stale,
        "future_tick_keys": future,
        "invalid_tick_keys": invalid,
        "last_transport_message_at": getattr(crypto_stream, "last_message_at", None),
        "latest_source_tick_at": crypto_latest,
        "latest_source_tick_age_seconds": _age(current, crypto_latest),
        "freshness_window_seconds": FEED_PROGRESS_MAX_AGE_SECONDS,
    }

    return {
        "market_clob": market,
        "sports": sports,
        "crypto_rtds": crypto,
        "diagnostic_only": True,
    }
