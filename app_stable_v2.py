from __future__ import annotations

"""Lower-load production runtime for the GCP e2-micro.

This module builds on app_stable but cuts the remaining sources of avoidable load
on the tiny shared-core VM:

* only ~800 priority CLOB tokens are kept on market WebSockets (normally about two
  workers at 400 tokens/connection);
* complete-universe discovery requests only SELL/best-ask prices from CLOB /prices.
  All actionable detectors need asks; full depth is still fetched with /books by
  app.confirm_actionable immediately before an ACTIONABLE alert is persisted;
* a partial HTTP sweep can never replace/timestamp the last complete transport sweep;
* request/response application bytes and quote-omission classes are measured for
  future VM egress/cost attestation without pretending they equal cloud billing;
* the fuzzy duplicate-market research WATCH is disabled by default in production.
  It is a low-confidence O(n^2)-style similarity scan and is not allowed to starve
  the time-sensitive weather/sports/crypto/actionable lanes on an e2-micro.

Gamma still discovers the complete market universe and the compact ask snapshot
attempts every discovered token. Sports and crypto retain their dedicated live feeds.
The process watchdog from app_stable remains active.
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
PRICE_DISCOVERY_DIAGNOSTICS_VERSION = "clob_sell_sweep_v3_transport_accounting"

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
_last_price_fetch_diagnostics: dict = {
    "version": PRICE_DISCOVERY_DIAGNOSTICS_VERSION,
    "transport_complete": False,
    "reason": "no price discovery attempt yet",
}


def _duplicate_divergence_production(markets):
    if not ENABLE_DUPLICATE_DIVERGENCE_WATCH:
        return []
    return _original_duplicate_divergence(markets)


# evaluate_signals resolves duplicate_divergence from evaluator.py globals at call
# time, so this removes only that research WATCH from the production hot path.
evaluator_module.duplicate_divergence = _duplicate_divergence_production


def _request_body_bytes(body: list[dict[str, str]]) -> int:
    """Measure compact JSON payload bytes at the application layer, not wire billing."""
    return len(json.dumps(body, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _project_request_gib_per_day(request_bytes: int) -> float:
    if request_bytes <= 0:
        return 0.0
    sweeps_per_day = 86400.0 / max(1.0, float(stable.TOP_PRICE_REFRESH_SECONDS))
    return request_bytes * sweeps_per_day / float(1024 ** 3)


async def _fetch_ask_prices(tokens: list[str]) -> dict[str, Book]:
    """Fetch best asks for every discovered CLOB token using bounded concurrency.

    A sweep can contain legitimate unquotable tokens, but it is transport-complete
    only when every HTTP chunk completed successfully. The caller must not advance
    the authoritative snapshot timestamp after any failed chunk. Counts distinguish
    response omission, missing SELL quotes and invalid SELL values so lack of market
    liquidity is not confused with network failure.
    """
    global _last_price_fetch_diagnostics
    unique_tokens = list(dict.fromkeys(str(token) for token in tokens if str(token)))
    if not unique_tokens:
        _last_price_fetch_diagnostics = {
            "version": PRICE_DISCOVERY_DIAGNOSTICS_VERSION,
            "transport_complete": False,
            "reason": "no target tokens",
            "requested_tokens": 0,
            "chunk_count": 0,
            "failed_chunks": 0,
            "usable_tokens": 0,
            "request_body_bytes": 0,
            "response_body_bytes": 0,
            "projected_request_gib_per_day": 0.0,
        }
        return {}

    chunks = [
        unique_tokens[i:i + stable.TOP_PRICE_CHUNK_TOKENS]
        for i in range(0, len(unique_tokens), stable.TOP_PRICE_CHUNK_TOKENS)
    ]
    sem = asyncio.Semaphore(stable.TOP_PRICE_CONCURRENCY)

    async def one(chunk: list[str]) -> tuple[dict[str, Book], dict]:
        body = [{"token_id": token, "side": "SELL"} for token in chunk]
        body_bytes = _request_body_bytes(body)
        stats = {
            "requested_tokens": len(chunk),
            "request_attempts": 0,
            "request_body_bytes": 0,
            "response_body_bytes": 0,
            "response_entries": 0,
            "response_missing_tokens": 0,
            "missing_sell_tokens": 0,
            "invalid_sell_tokens": 0,
            "usable_tokens": 0,
            "success": False,
            "error": None,
        }
        async with sem:
            for attempt in range(3):
                try:
                    stats["request_attempts"] += 1
                    stats["request_body_bytes"] += body_bytes
                    response = await stable.base.poly.http.post(f"{CLOB}/prices", json=body)
                    try:
                        stats["response_body_bytes"] += len(response.content)
                    except Exception:
                        try:
                            stats["response_body_bytes"] += len(str(response.text).encode("utf-8"))
                        except Exception:
                            pass
                    if response.status_code == 429 and attempt < 2:
                        await asyncio.sleep(0.75 * (attempt + 1))
                        continue
                    response.raise_for_status()

                    # JSON decoding tens of thousands of token-price rows repeatedly
                    # is real work on an e2-micro. Do it off the asyncio event loop so
                    # scanner/WS/health tasks continue to get scheduled.
                    text = response.text
                    payload = await asyncio.to_thread(json.loads, text)
                    if not isinstance(payload, dict):
                        raise ValueError("CLOB /prices response was not a JSON object")
                    received_at = time.time()

                    out: dict[str, Book] = {}
                    for token in chunk:
                        if token not in payload:
                            stats["response_missing_tokens"] += 1
                            continue
                        stats["response_entries"] += 1
                        entry = payload.get(token)
                        if not isinstance(entry, dict):
                            stats["invalid_sell_tokens"] += 1
                            continue
                        raw_ask = entry.get("SELL")
                        if raw_ask is None:
                            stats["missing_sell_tokens"] += 1
                            continue
                        try:
                            ask = float(raw_ask)
                        except (TypeError, ValueError):
                            stats["invalid_sell_tokens"] += 1
                            continue
                        if not math.isfinite(ask) or ask <= 0.0 or ask >= 1.0:
                            stats["invalid_sell_tokens"] += 1
                            continue
                        out[token] = Book(
                            token_id=token,
                            bids=[],
                            asks=[(ask, 0.0)],
                            timestamp="price-discovery",
                            received_at=received_at,
                            source="clob_rest_prices",
                        )
                    stats["usable_tokens"] = len(out)
                    stats["success"] = True
                    return out, stats
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    stats["error"] = f"{type(exc).__name__}: {exc}"
                    if attempt < 2:
                        await asyncio.sleep(0.25 * (attempt + 1))
                        continue
                    log.debug("ask-price chunk failed (%d tokens): %r", len(chunk), exc)
                    return {}, stats
        return {}, stats

    pieces = await asyncio.gather(*(one(chunk) for chunk in chunks), return_exceptions=True)
    merged: dict[str, Book] = {}
    chunk_stats: list[dict] = []
    for index, piece in enumerate(pieces):
        if isinstance(piece, tuple) and len(piece) == 2 and isinstance(piece[0], dict) and isinstance(piece[1], dict):
            books, stats = piece
            merged.update(books)
            chunk_stats.append(stats)
        else:
            error = piece if isinstance(piece, BaseException) else RuntimeError("invalid chunk result")
            chunk_stats.append({
                "requested_tokens": len(chunks[index]),
                "request_attempts": 0,
                "request_body_bytes": 0,
                "response_body_bytes": 0,
                "response_entries": 0,
                "response_missing_tokens": len(chunks[index]),
                "missing_sell_tokens": 0,
                "invalid_sell_tokens": 0,
                "usable_tokens": 0,
                "success": False,
                "error": f"{type(error).__name__}: {error}",
            })
        # Yield between merge steps on the shared-core VM.
        await asyncio.sleep(0)

    failed_chunks = sum(not bool(row.get("success")) for row in chunk_stats)
    request_bytes = sum(int(row.get("request_body_bytes") or 0) for row in chunk_stats)
    response_bytes = sum(int(row.get("response_body_bytes") or 0) for row in chunk_stats)
    response_entries = sum(int(row.get("response_entries") or 0) for row in chunk_stats)
    response_missing = sum(int(row.get("response_missing_tokens") or 0) for row in chunk_stats)
    missing_sell = sum(int(row.get("missing_sell_tokens") or 0) for row in chunk_stats)
    invalid_sell = sum(int(row.get("invalid_sell_tokens") or 0) for row in chunk_stats)
    transport_complete = failed_chunks == 0
    _last_price_fetch_diagnostics = {
        "version": PRICE_DISCOVERY_DIAGNOSTICS_VERSION,
        "transport_complete": transport_complete,
        "reason": (
            "all CLOB /prices chunks completed"
            if transport_complete
            else f"{failed_chunks} of {len(chunks)} CLOB /prices chunks failed"
        ),
        "requested_tokens": len(unique_tokens),
        "chunk_count": len(chunks),
        "failed_chunks": failed_chunks,
        "response_entries": response_entries,
        "response_missing_tokens": response_missing,
        "missing_sell_tokens": missing_sell,
        "invalid_sell_tokens": invalid_sell,
        "usable_tokens": len(merged),
        "usable_coverage_ratio": len(merged) / len(unique_tokens),
        "response_entry_ratio": response_entries / len(unique_tokens),
        "request_body_bytes": request_bytes,
        "response_body_bytes": response_bytes,
        "projected_request_gib_per_day": _project_request_gib_per_day(request_bytes),
        "application_byte_scope": (
            "JSON request bodies and decoded response bodies only; not TCP/TLS overhead or cloud billing"
        ),
        "chunk_errors": [str(row.get("error")) for row in chunk_stats if row.get("error")][:8],
    }
    return merged


def _record_price_attempt(diagnostics: dict) -> None:
    state = stable.base.state
    state["price_discovery_last_attempt"] = dict(diagnostics)
    state["price_discovery_diagnostics_version"] = PRICE_DISCOVERY_DIAGNOSTICS_VERSION
    state["price_discovery_request_bytes_total"] = int(
        state.get("price_discovery_request_bytes_total") or 0
    ) + int(diagnostics.get("request_body_bytes") or 0)
    state["price_discovery_response_bytes_total"] = int(
        state.get("price_discovery_response_bytes_total") or 0
    ) + int(diagnostics.get("response_body_bytes") or 0)


def _accept_price_sweep(refreshed: dict[str, Book], diagnostics: dict, *, now: float | None = None) -> bool:
    """Advance authority only for a transport-complete non-empty sweep."""
    global _last_price_fetch_diagnostics
    _last_price_fetch_diagnostics = dict(diagnostics)
    _record_price_attempt(diagnostics)
    if not diagnostics.get("transport_complete"):
        stable._price_refresh_error = str(diagnostics.get("reason") or "partial CLOB /prices sweep")
        stable.base.state["price_snapshot_error"] = stable._price_refresh_error
        stable.base.state["price_snapshot_last_attempt_transport_complete"] = False
        return False
    if not refreshed:
        stable._price_refresh_error = "CLOB /prices transport completed but returned no usable token prices"
        stable.base.state["price_snapshot_error"] = stable._price_refresh_error
        stable.base.state["price_snapshot_last_attempt_transport_complete"] = True
        return False

    accepted_at = time.time() if now is None else float(now)
    stable._price_books = dict(refreshed)
    stable._price_snapshot_at = accepted_at
    stable._price_refresh_error = None
    stable.base.state["price_snapshot_tokens"] = len(refreshed)
    stable.base.state["price_snapshot_at"] = accepted_at
    stable.base.state["price_snapshot_error"] = None
    stable.base.state["price_snapshot_authoritative_transport_complete"] = True
    stable.base.state["price_snapshot_last_attempt_transport_complete"] = True
    stable.base.state["price_snapshot_response_missing_tokens"] = int(diagnostics.get("response_missing_tokens") or 0)
    stable.base.state["price_snapshot_missing_sell_tokens"] = int(diagnostics.get("missing_sell_tokens") or 0)
    stable.base.state["price_snapshot_invalid_sell_tokens"] = int(diagnostics.get("invalid_sell_tokens") or 0)
    stable.base.state["price_snapshot_usable_coverage_ratio"] = float(diagnostics.get("usable_coverage_ratio") or 0.0)
    return True


async def _top_price_loop_v2() -> None:
    """Refresh whole-universe ask discovery without accepting partial HTTP sweeps."""
    global _last_price_fetch_diagnostics
    while True:
        try:
            tokens = list(stable._full_tokens)
            if not tokens:
                await asyncio.sleep(1.0)
                continue
            started = time.monotonic()
            refreshed = await _fetch_ask_prices(tokens)
            diagnostics = dict(_last_price_fetch_diagnostics)
            stable.base.state["price_snapshot_seconds"] = round(time.monotonic() - started, 3)
            if _accept_price_sweep(refreshed, diagnostics):
                log.info(
                    "whole-market ask snapshot: %d/%d usable tokens, response-missing=%d, "
                    "no-ask=%d, invalid=%d in %.2fs; request-body≈%.3f MiB; websocket hot set=%d",
                    len(refreshed),
                    len(tokens),
                    int(diagnostics.get("response_missing_tokens") or 0),
                    int(diagnostics.get("missing_sell_tokens") or 0),
                    int(diagnostics.get("invalid_sell_tokens") or 0),
                    time.monotonic() - started,
                    float(diagnostics.get("request_body_bytes") or 0) / float(1024 ** 2),
                    len(stable._priority_tokens),
                )
            else:
                log.warning(
                    "whole-market ask snapshot not advanced: %s; retaining last complete snapshot",
                    stable._price_refresh_error,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stable._price_refresh_error = repr(exc)
            stable.base.state["price_snapshot_error"] = stable._price_refresh_error
            stable.base.state["price_snapshot_last_attempt_transport_complete"] = False
            log.warning("whole-market ask-price refresh failed: %r", exc)
        await asyncio.sleep(stable.TOP_PRICE_REFRESH_SECONDS)


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


# app_stable._stable_startup resolves these globals at runtime, so replacing them
# here changes the production discovery transport/acceptance without duplicating the
# rest of the scanner lifecycle.
stable._fetch_top_prices = _fetch_ask_prices
stable._top_price_loop = _top_price_loop_v2
# app_stable installed its original merged-snapshot function on the stream object at
# import time. Replace that bound hook too, not only the module global.
stable._merged_book_snapshot = _merged_book_snapshot_v2
stable.base.market_stream.snapshot = _merged_book_snapshot_v2


async def _mark_runtime_v2() -> None:
    stable.base.state["runtime_mode"] = "bounded_ws_plus_full_ask_discovery_v3_complete_sweeps"
    stable.base.state["stream_priority_limit"] = stable.WS_PRIORITY_TOKEN_LIMIT
    stable.base.state["price_snapshot_side"] = "SELL/best-ask"
    stable.base.state["price_snapshot_target_seconds"] = stable.TOP_PRICE_REFRESH_SECONDS
    stable.base.state["price_discovery_diagnostics_version"] = PRICE_DISCOVERY_DIAGNOSTICS_VERSION
    stable.base.state["price_snapshot_authoritative_transport_complete"] = False
    stable.base.state["ws_book_max_stale_seconds"] = WS_BOOK_MAX_STALE_SECONDS
    stable.base.state["duplicate_divergence_watch_enabled"] = ENABLE_DUPLICATE_DIVERGENCE_WATCH


# Registered after app_stable's startup handler, purely to make the active runtime
# unambiguous in the health snapshot/debug logs.
app.add_event_handler("startup", _mark_runtime_v2)
