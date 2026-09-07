from __future__ import annotations

"""Lower-load production runtime for the GCP e2-micro.

This module builds on app_stable but cuts the remaining sources of avoidable load
on the tiny shared-core VM:

* only ~800 priority CLOB tokens are kept on market WebSockets (normally about two
  workers at 400 tokens/connection);
* complete-universe discovery requests only SELL/best-ask prices from CLOB /prices.
  All actionable detectors need asks; full depth is still fetched with /books by
  app.confirm_actionable immediately before an ACTIONABLE alert is persisted;
* the fuzzy duplicate-market research WATCH is disabled by default in production.
  It is a low-confidence O(n^2)-style similarity scan and is not allowed to starve
  the time-sensitive weather/sports/crypto/actionable lanes on an e2-micro.

Gamma still discovers the complete market universe and the compact ask snapshot
still covers every discovered token. Sports and crypto retain their dedicated live
feeds. The process watchdog from app_stable remains active.
"""

import asyncio
import json
import logging
import math
import os
import time

import app_stable as stable
import polymarket_scanner.evaluator as evaluator_module
from polymarket_scanner.models import Book
from polymarket_scanner.polymarket import CLOB

log = logging.getLogger("polybot.stable_v2")
app = stable.app

# Two market CLOB workers instead of 8 (and instead of the old ~65-70). The full
# universe continues to be refreshed through compact /prices discovery below.
stable.WS_PRIORITY_TOKEN_LIMIT = max(400, int(os.getenv("MARKET_WS_PRIORITY_TOKEN_LIMIT", "800")))

# Ask-only snapshots are much smaller than the v1 BUY+SELL snapshots, so we can
# refresh the complete universe more frequently while using less CPU/network.
stable.TOP_PRICE_REFRESH_SECONDS = max(15.0, float(os.getenv("TOP_PRICE_REFRESH_SECONDS", "20")))
stable.TOP_PRICE_CHUNK_TOKENS = max(50, min(500, int(os.getenv("TOP_PRICE_CHUNK_TOKENS", "500"))))
stable.TOP_PRICE_CONCURRENCY = max(1, min(8, int(os.getenv("TOP_PRICE_CONCURRENCY", "4"))))
WS_BOOK_MAX_STALE_SECONDS = max(
    5.0,
    min(60.0, float(os.getenv("WS_BOOK_MAX_STALE_SECONDS", str(stable.TOP_PRICE_REFRESH_SECONDS * 1.5)))),
)

# SequenceMatcher-based duplicate discovery is useful research, but it compares
# many pairs and has repeatedly coincided with long worker passes on the e2-micro.
# Keep the code available everywhere else, but production disables it unless the
# operator explicitly opts back in.
ENABLE_DUPLICATE_DIVERGENCE_WATCH = os.getenv("ENABLE_DUPLICATE_DIVERGENCE_WATCH", "0").strip().lower() in {
    "1", "true", "yes", "on",
}
_original_duplicate_divergence = evaluator_module.duplicate_divergence


def _duplicate_divergence_production(markets):
    if not ENABLE_DUPLICATE_DIVERGENCE_WATCH:
        return []
    return _original_duplicate_divergence(markets)


# evaluate_signals resolves duplicate_divergence from evaluator.py globals at call
# time, so this removes only that research WATCH from the production hot path.
evaluator_module.duplicate_divergence = _duplicate_divergence_production


async def _fetch_ask_prices(tokens: list[str]) -> dict[str, Book]:
    """Fetch best asks for every discovered CLOB token using bounded concurrency.

    Discovery books intentionally contain no depth. Structural candidates that
    survive discovery are re-fetched with POST /books in confirm_actionable, where
    price, visible size and execution capacity are checked immediately before alert.
    """
    if not tokens:
        return {}

    chunks = [
        tokens[i:i + stable.TOP_PRICE_CHUNK_TOKENS]
        for i in range(0, len(tokens), stable.TOP_PRICE_CHUNK_TOKENS)
    ]
    sem = asyncio.Semaphore(stable.TOP_PRICE_CONCURRENCY)

    async def one(chunk: list[str]) -> dict[str, Book]:
        body = [{"token_id": token, "side": "SELL"} for token in chunk]
        async with sem:
            for attempt in range(3):
                try:
                    response = await stable.base.poly.http.post(f"{CLOB}/prices", json=body)
                    if response.status_code == 429:
                        if attempt < 2:
                            await asyncio.sleep(0.75 * (attempt + 1))
                            continue
                    response.raise_for_status()

                    # JSON decoding tens of thousands of token-price rows repeatedly
                    # is real work on an e2-micro. Do it off the asyncio event loop so
                    # scanner/WS/health tasks continue to get scheduled.
                    text = response.text
                    payload = await asyncio.to_thread(json.loads, text)
                    if not isinstance(payload, dict):
                        return {}
                    received_at = time.time()

                    out: dict[str, Book] = {}
                    for token in chunk:
                        entry = payload.get(token)
                        if not isinstance(entry, dict):
                            continue
                        raw_ask = entry.get("SELL")
                        if raw_ask is None:
                            continue
                        try:
                            ask = float(raw_ask)
                        except (TypeError, ValueError):
                            continue
                        if not math.isfinite(ask) or ask <= 0.0 or ask >= 1.0:
                            continue
                        out[token] = Book(
                            token_id=token,
                            bids=[],
                            asks=[(ask, 0.0)],
                            timestamp="price-discovery",
                            received_at=received_at,
                            source="clob_rest_prices",
                        )
                    return out
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if attempt < 2:
                        await asyncio.sleep(0.25 * (attempt + 1))
                        continue
                    log.debug("ask-price chunk failed (%d tokens): %r", len(chunk), exc)
                    return {}
        return {}

    pieces = await asyncio.gather(*(one(chunk) for chunk in chunks), return_exceptions=True)
    merged: dict[str, Book] = {}
    for piece in pieces:
        if isinstance(piece, dict):
            merged.update(piece)
        # Yield between merge steps on the shared-core VM.
        await asyncio.sleep(0)
    return merged


def _merged_book_snapshot_v2() -> dict[str, Book]:
    """Prefer the freshest independently received source for every hot token.

    The old implementation unconditionally overlaid WebSocket books on the broad
    REST `/prices` snapshot. A disconnected/stalled WS token could therefore replace
    a newer REST price with an old book. WS depth now wins only when it has a local
    receipt timestamp at least as new as the REST discovery row and is itself within
    a bounded freshness window. Otherwise the broad REST ask remains authoritative
    for discovery and actionable candidates are still re-fetched with full REST books.
    """
    now = time.time()
    merged = {token: book.clone() for token, book in stable._price_books.items()}
    ws_books = stable._original_stream_snapshot()
    ws_used = 0
    ws_stale_or_older = 0

    for token, ws_book in ws_books.items():
        ws_received = ws_book.received_at
        if ws_received is None or not math.isfinite(float(ws_received)):
            ws_stale_or_older += 1
            continue
        ws_received = float(ws_received)
        if now - ws_received > WS_BOOK_MAX_STALE_SECONDS:
            ws_stale_or_older += 1
            continue
        rest_book = merged.get(token)
        rest_received = (
            float(rest_book.received_at)
            if rest_book is not None and rest_book.received_at is not None
            else float(stable._price_snapshot_at or 0.0)
        )
        if rest_received > 0.0 and ws_received < rest_received:
            ws_stale_or_older += 1
            continue
        merged[token] = ws_book.clone()
        ws_used += 1

    stable.base.state["ws_books_authoritative"] = ws_used
    stable.base.state["ws_books_stale_or_older"] = ws_stale_or_older
    stable.base.state["ws_book_max_stale_seconds"] = WS_BOOK_MAX_STALE_SECONDS
    stable.base.state["market_ws_last_valid_update"] = stable.base.market_stream.last_valid_update_at
    stable.base.state["market_ws_last_full_book"] = stable.base.market_stream.last_full_book_at
    stable.base.state["market_ws_book_invalidations"] = stable.base.market_stream.invalidated_books
    stable.base.state["market_ws_out_of_order_ignored"] = stable.base.market_stream.out_of_order_ignored
    return merged


# app_stable._top_price_loop resolves this global each cycle, so replacing it here
# changes the production discovery transport without duplicating the scanner logic.
stable._fetch_top_prices = _fetch_ask_prices
# app_stable installed its original merged-snapshot function on the stream object at
# import time. Replace that bound hook too, not only the module global.
stable._merged_book_snapshot = _merged_book_snapshot_v2
stable.base.market_stream.snapshot = _merged_book_snapshot_v2


async def _mark_runtime_v2() -> None:
    stable.base.state["runtime_mode"] = "bounded_ws_plus_full_ask_discovery_v2"
    stable.base.state["stream_priority_limit"] = stable.WS_PRIORITY_TOKEN_LIMIT
    stable.base.state["price_snapshot_side"] = "SELL/best-ask"
    stable.base.state["price_snapshot_target_seconds"] = stable.TOP_PRICE_REFRESH_SECONDS
    stable.base.state["ws_book_max_stale_seconds"] = WS_BOOK_MAX_STALE_SECONDS
    stable.base.state["duplicate_divergence_watch_enabled"] = ENABLE_DUPLICATE_DIVERGENCE_WATCH


# Registered after app_stable's startup handler, purely to make the active runtime
# unambiguous in the health snapshot/debug logs.
app.add_event_handler("startup", _mark_runtime_v2)