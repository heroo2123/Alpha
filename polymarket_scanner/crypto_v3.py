from __future__ import annotations

import math
import re
import time

from .config import settings
from .detectors import market_url
from .detectors_v02 import ASSETS, _asset, _end, _meta, _topic, threshold
from .models import Book, Market, Signal
from .polymarket import taker_fee_per_share
from .streams import CryptoRTDS, PriceTick

CRYPTO_FEED_VERSION = "rtds_v3_connected_causal"
CROSSFEED_MAX_AGE_SECONDS = 20.0
CROSSFEED_MAX_SKEW_SECONDS = 5.0
SOURCE_FUTURE_TOLERANCE_SECONDS = 2.0


def _connected(rtds: CryptoRTDS) -> bool:
    return bool(getattr(rtds, "connected", False))


def _tick_time_valid(tick: PriceTick | None, *, now_ts: float) -> bool:
    if tick is None:
        return False
    try:
        ts = float(tick.ts)
        price = float(tick.price)
    except (TypeError, ValueError, OverflowError):
        return False
    return (
        math.isfinite(ts)
        and math.isfinite(price)
        and price > 0.0
        and ts <= now_ts + SOURCE_FUTURE_TOLERANCE_SECONDS
    )


def _boundary_tick(
    rtds: CryptoRTDS,
    topic: str,
    symbol: str,
    target_ts: float,
    now_ts: float,
) -> PriceTick | None:
    tick = rtds.nearest(topic, symbol, target_ts, settings.crypto_boundary_tolerance_seconds)
    if not _tick_time_valid(tick, now_ts=now_ts):
        return None
    assert tick is not None
    if abs(float(tick.ts) - float(target_ts)) > float(settings.crypto_boundary_tolerance_seconds):
        return None
    return tick


def crypto_resolution_lag_v3(
    markets: list[Market],
    books: dict[str, Book],
    rtds: CryptoRTDS,
    now_ts: float | None = None,
) -> list[Signal]:
    """Known crypto-boundary research with explicit feed authority.

    Captured boundary ticks are useful evidence only while the RTDS transport is
    currently connected and the source timestamps themselves are causal and within
    the configured market-boundary tolerance. A disconnected process does not turn
    an old in-memory tick into a new result-lag candidate.
    """
    current = time.time() if now_ts is None else float(now_ts)
    if not _connected(rtds):
        return []

    out: list[Signal] = []
    for m in markets:
        asset = _asset(m)
        topic = _topic(m)
        if not asset or not topic:
            continue
        _, symbol, _ = asset
        winner = None
        detail = ""
        evidence: dict = {"crypto_feed_version": CRYPTO_FEED_VERSION, "reference_topic": topic}

        slug_match = re.search(r"(?:btc|eth|sol|xrp)-updown-(5m|15m|4h)-(\d+)", m.event_slug.lower())
        if slug_match:
            duration = {"5m": 300, "15m": 900, "4h": 14400}[slug_match.group(1)]
            start_ts = float(slug_match.group(2))
            end_ts = start_ts + duration
            if current < end_ts + 1:
                continue
            start_tick = _boundary_tick(rtds, topic, symbol, start_ts, current)
            end_tick = _boundary_tick(rtds, topic, symbol, end_ts, current)
            if not start_tick or not end_tick:
                continue
            if float(start_tick.ts) > float(end_tick.ts) or math.isclose(start_tick.price, end_tick.price):
                continue
            winner = "Up" if end_tick.price > start_tick.price else "Down"
            detail = (
                f"start {start_tick.price:,.4f} @ {start_tick.ts:.3f}, "
                f"end {end_tick.price:,.4f} @ {end_tick.ts:.3f} ({topic})"
            )
            evidence.update({
                "reference_start_target_ts": start_ts,
                "reference_start_tick_ts": float(start_tick.ts),
                "reference_end_target_ts": end_ts,
                "reference_end_tick_ts": float(end_tick.ts),
            })
        else:
            parsed = threshold(m.question)
            end_ts = _end(m.end_date)
            if not parsed or end_ts is None or current < end_ts + 1:
                continue
            tick = _boundary_tick(rtds, topic, symbol, end_ts, current)
            if not tick:
                continue
            truth = tick.price >= parsed[1] if parsed[0] == "above" else tick.price <= parsed[1]
            winner = "Yes" if truth else "No"
            detail = (
                f"reference {tick.price:,.4f} @ {tick.ts:.3f} "
                f"vs {parsed[0]} {parsed[1]:,.4f} ({topic})"
            )
            evidence.update({
                "reference_target_ts": end_ts,
                "reference_tick_ts": float(tick.ts),
            })

        token = m.token_for_outcome(winner)
        book = books.get(token or "")
        if not token or not book or book.best_ask is None or book.best_ask > settings.known_outcome_max_ask:
            continue
        ask = float(book.best_ask)
        cost = ask + taker_fee_per_share(ask)
        edge = 1.0 - cost
        if edge < settings.actionable_min_edge:
            continue

        meta = _meta(
            m,
            winner,
            ask,
            "Verify the market Rules use the same source and boundary time before trading.",
            {"fingerprint_key": f"{m.id}:{winner}:{CRYPTO_FEED_VERSION}", **evidence},
        )
        meta["action_steps"].insert(1, f"Confirm Rules/source/time; captured {detail}.")
        out.append(Signal(
            "crypto_resolution_lag",
            "ACTIONABLE",
            m.event_id,
            m.id,
            "Crypto resolution value captured",
            f"Outcome {winner}; {detail}; ask {ask:.3f}; post-fee edge {edge:.2%}.",
            market_url(m),
            edge,
            cost,
            1.0,
            [token],
            meta,
        ))
    return out


def _fresh_latest(tick: PriceTick | None, *, now_ts: float) -> PriceTick | None:
    if not _tick_time_valid(tick, now_ts=now_ts):
        return None
    assert tick is not None
    age = now_ts - float(tick.ts)
    if age < -SOURCE_FUTURE_TOLERANCE_SECONDS or age > CROSSFEED_MAX_AGE_SECONDS:
        return None
    return tick


def crypto_crossfeed_divergence_v3(
    markets: list[Market],
    rtds: CryptoRTDS,
    now_ts: float | None = None,
) -> list[Signal]:
    """Research-only divergence using two simultaneously fresh reference feeds."""
    current = time.time() if now_ts is None else float(now_ts)
    if not _connected(rtds):
        return []

    out: list[Signal] = []
    seen: set[str] = set()
    for m in markets:
        asset = _asset(m)
        if not asset or asset[0] in seen:
            continue
        chainlink = _fresh_latest(rtds.latest("crypto_prices_chainlink", asset[1]), now_ts=current)
        binance = _fresh_latest(
            rtds.latest("crypto_prices", asset[2])
            or rtds.latest("crypto_prices", asset[2].replace("usdt", "/usdt")),
            now_ts=current,
        )
        if not chainlink or not binance:
            continue
        if abs(float(chainlink.ts) - float(binance.ts)) > CROSSFEED_MAX_SKEW_SECONDS:
            continue

        bps = abs(binance.price - chainlink.price) / chainlink.price * 10000
        if bps < settings.crypto_crossfeed_watch_bps:
            continue
        seen.add(asset[0])
        out.append(Signal(
            "crypto_crossfeed_divergence",
            "WATCH",
            m.event_id,
            m.id,
            f"{asset[0].upper()} reference feeds diverged",
            (
                f"Chainlink {chainlink.price:,.4f} @ {chainlink.ts:.3f} vs "
                f"Binance-backed {binance.price:,.4f} @ {binance.ts:.3f}: {bps:.1f} bps. "
                "Read Rules to identify the source that actually settles the contract."
            ),
            market_url(m),
            None,
            None,
            None,
            [],
            {
                "fingerprint_key": f"{asset[0]}:{int(bps/5)}:{CRYPTO_FEED_VERSION}",
                "crypto_feed_version": CRYPTO_FEED_VERSION,
                "chainlink_tick_ts": float(chainlink.ts),
                "binance_tick_ts": float(binance.ts),
                "tick_skew_seconds": abs(float(chainlink.ts) - float(binance.ts)),
                "links": [{"label": "OPEN MARKET", "url": market_url(m)}],
                "action_steps": [
                    "Open the market and read its resolution source.",
                    "Judge the contract only against that source; do not trade merely because feeds differ.",
                ],
                "risk_note": "Cross-feed divergence is a discovery signal, not an arbitrage.",
            },
        ))
    return out
