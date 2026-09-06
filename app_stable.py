from __future__ import annotations

"""Production wrapper for the small GCP e2-micro deployment.

The base application remains the source of scanner/detector behavior.  This wrapper
changes only market-data transport and resilience:

* Gamma still discovers the complete active universe.
* CLOB /prices supplies lightweight whole-universe top-of-book discovery snapshots.
* WebSockets are reserved for a bounded, time-sensitive subset instead of all ~27k
  outcome tokens.
* Every ACTIONABLE candidate is still REST-confirmed with full order books by
  app.confirm_actionable before it can be persisted/alerted.
* A daemon watchdog lets systemd recover the scanner if the process stops completing
  scans even though the standalone Telegram command service is still alive.
"""

import asyncio
import json
import logging
import math
import os
import re
import threading
import time
from collections.abc import Iterable

import app as base
import polymarket_scanner.hardening as hardening
from polymarket_scanner.hardening import MIN_VISIBLE_NOTIONAL_USD
from polymarket_scanner.models import Book, Market, Signal
from polymarket_scanner.polymarket import CLOB, taker_fee_per_share

log = logging.getLogger("polybot.stable")
app = base.app

# Eight CLOB market-stream workers at the default 400 tokens/worker, rather than
# roughly 65-70 workers for the complete universe.  The complete universe remains
# covered by the compact /prices snapshots below.
WS_PRIORITY_TOKEN_LIMIT = max(800, int(os.getenv("MARKET_WS_PRIORITY_TOKEN_LIMIT", "3200")))
TOP_PRICE_REFRESH_SECONDS = max(20.0, float(os.getenv("TOP_PRICE_REFRESH_SECONDS", "45")))
TOP_PRICE_CHUNK_TOKENS = max(25, min(500, int(os.getenv("TOP_PRICE_CHUNK_TOKENS", "200"))))
TOP_PRICE_CONCURRENCY = max(1, min(12, int(os.getenv("TOP_PRICE_CONCURRENCY", "6"))))
WATCHDOG_STALE_SECONDS = max(60.0, float(os.getenv("SCANNER_WATCHDOG_STALE_SECONDS", "120")))
WATCHDOG_STARTUP_GRACE_SECONDS = max(120.0, float(os.getenv("SCANNER_WATCHDOG_STARTUP_GRACE_SECONDS", "240")))

_full_tokens: tuple[str, ...] = ()
_priority_tokens: tuple[str, ...] = ()
_price_books: dict[str, Book] = {}
_price_snapshot_at: float | None = None
_price_refresh_error: str | None = None
_price_task: asyncio.Task | None = None
_watchdog_started = False

_original_all_tokens = base._all_tokens
_original_stream_configure = base.market_stream.configure
_original_stream_snapshot = base.market_stream.snapshot
_original_confirm_actionable = base.confirm_actionable
_original_visible_notional = hardening._visible_notional

_SPORT_TAGS = {
    "sports", "nba", "nfl", "mlb", "nhl", "wnba", "ncaa", "ncaab", "ncaaf",
    "soccer", "football", "tennis", "ufc", "mma", "boxing", "f1", "formula-1",
    "golf", "cricket", "esports",
}
_CRYPTO_RE = re.compile(r"\b(?:bitcoin|btc|ethereum|eth|solana|sol|xrp)\b", re.I)


def _market_priority(m: Market) -> tuple[int, float, float]:
    text = f"{m.event_title} {m.question} {m.description} {m.resolution_source}".lower()
    tags = {str(x).strip().lower() for x in m.tags}
    category = str(m.category or "").lower()

    if "highest temperature" in text:
        bucket = 0
    elif _CRYPTO_RE.search(text) and (
        "chainlink" in text
        or "30-second" in text
        or "30 second" in text
        or "60-second" in text
        or "60 second" in text
        or "up or down" in text
        or "updown" in str(m.event_slug).lower()
    ):
        bucket = 1
    elif "sport" in category or tags & _SPORT_TAGS:
        bucket = 2
    elif "bls.gov" in text or "bureau of labor statistics" in text:
        bucket = 3
    else:
        bucket = 4
    return bucket, -float(m.volume_24h or 0.0), -float(m.liquidity or 0.0)


def _all_tokens_with_priority(markets: list[Market]) -> list[str]:
    """Return all tokens to the base scanner while recording the WS hot subset."""
    global _full_tokens, _priority_tokens
    all_tokens = _original_all_tokens(markets)
    ordered_markets = sorted(markets, key=_market_priority)
    priority: list[str] = []
    seen: set[str] = set()
    for market in ordered_markets:
        for token in market.token_ids:
            if token and token not in seen:
                priority.append(token)
                seen.add(token)
                if len(priority) >= WS_PRIORITY_TOKEN_LIMIT:
                    break
        if len(priority) >= WS_PRIORITY_TOKEN_LIMIT:
            break

    _full_tokens = tuple(all_tokens)
    _priority_tokens = tuple(priority)
    base.state["stream_priority_tokens"] = len(_priority_tokens)
    base.state["price_snapshot_tokens"] = len(_price_books)
    return all_tokens


async def _configure_priority_stream(_all_token_ids: Iterable[str]) -> None:
    """Subscribe only the bounded hot set; full coverage comes from /prices."""
    await _original_stream_configure(_priority_tokens)
    base.state["stream_priority_tokens"] = len(_priority_tokens)


def _merged_book_snapshot() -> dict[str, Book]:
    # Price discovery is the broad baseline.  Fresh websocket books override it for
    # weather/crypto/sports/high-volume markets in the hot subset.
    merged = dict(_price_books)
    merged.update(_original_stream_snapshot())
    return merged


async def _fetch_top_prices(tokens: list[str]) -> dict[str, Book]:
    """Fetch compact best bid/ask data for the complete token universe.

    Polymarket documents BUY as best bid and SELL as best ask on /prices.  We request
    both for each token.  This is dramatically smaller than full order books and is
    discovery-only: ACTIONABLE alerts are still confirmed using POST /books.
    """
    if not tokens:
        return {}

    sem = asyncio.Semaphore(TOP_PRICE_CONCURRENCY)
    chunks = [tokens[i:i + TOP_PRICE_CHUNK_TOKENS] for i in range(0, len(tokens), TOP_PRICE_CHUNK_TOKENS)]

    async def one(chunk: list[str]) -> dict[str, Book]:
        body: list[dict[str, str]] = []
        for token in chunk:
            body.append({"token_id": token, "side": "BUY"})
            body.append({"token_id": token, "side": "SELL"})

        async with sem:
            for attempt in range(2):
                try:
                    r = await base.poly.http.post(f"{CLOB}/prices", json=body)
                    if r.status_code == 429 and attempt == 0:
                        await asyncio.sleep(1.0)
                        continue
                    r.raise_for_status()
                    payload = r.json()
                    if not isinstance(payload, dict):
                        return {}
                    out: dict[str, Book] = {}
                    for token in chunk:
                        entry = payload.get(token)
                        if not isinstance(entry, dict):
                            continue
                        try:
                            bid = float(entry["BUY"]) if entry.get("BUY") is not None else None
                        except (TypeError, ValueError):
                            bid = None
                        try:
                            ask = float(entry["SELL"]) if entry.get("SELL") is not None else None
                        except (TypeError, ValueError):
                            ask = None
                        if bid is None and ask is None:
                            continue
                        bids = [] if bid is None or not math.isfinite(bid) else [(bid, 0.0)]
                        asks = [] if ask is None or not math.isfinite(ask) else [(ask, 0.0)]
                        out[token] = Book(token, bids, asks, timestamp="price-discovery")
                    return out
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if attempt == 0:
                        await asyncio.sleep(0.25)
                        continue
                    log.debug("top-price chunk failed (%d tokens): %r", len(chunk), exc)
                    return {}
        return {}

    pieces = await asyncio.gather(*(one(chunk) for chunk in chunks), return_exceptions=True)
    merged: dict[str, Book] = {}
    for piece in pieces:
        if isinstance(piece, dict):
            merged.update(piece)
    return merged


async def _top_price_loop() -> None:
    global _price_books, _price_snapshot_at, _price_refresh_error
    while True:
        try:
            tokens = list(_full_tokens)
            if not tokens:
                await asyncio.sleep(1.0)
                continue
            started = time.monotonic()
            refreshed = await _fetch_top_prices(tokens)
            if refreshed:
                _price_books = refreshed
                _price_snapshot_at = time.time()
                _price_refresh_error = None
                base.state["price_snapshot_tokens"] = len(refreshed)
                base.state["price_snapshot_at"] = _price_snapshot_at
                base.state["price_snapshot_seconds"] = round(time.monotonic() - started, 3)
                base.state["price_snapshot_error"] = None
                log.info(
                    "whole-market top-price snapshot: %d/%d tokens in %.2fs; websocket hot set=%d",
                    len(refreshed), len(tokens), time.monotonic() - started, len(_priority_tokens),
                )
            else:
                _price_refresh_error = "CLOB /prices returned no usable token prices"
                base.state["price_snapshot_error"] = _price_refresh_error
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _price_refresh_error = repr(exc)
            base.state["price_snapshot_error"] = _price_refresh_error
            log.warning("whole-market top-price refresh failed: %r", exc)
        await asyncio.sleep(TOP_PRICE_REFRESH_SECONDS)


def _visible_notional_deferred(signal: Signal, books: dict[str, Book]) -> tuple[float, float]:
    """Defer size gating when discovery has price but intentionally no depth.

    Full depth is checked moments later by app.confirm_actionable.  Returning the
    execution floor here prevents a valid candidate from being demoted solely because
    the compact /prices endpoint does not include size.
    """
    rows = [books.get(token) for token in signal.token_ids]
    if rows and all(row is not None for row in rows) and any(row.timestamp == "price-discovery" for row in rows if row):
        return 0.0, MIN_VISIBLE_NOTIONAL_USD
    return _original_visible_notional(signal, books)


async def _confirm_actionable_with_execution_text(signal: Signal) -> Signal | None:
    confirmed = await _original_confirm_actionable(signal)
    if confirmed is None or confirmed.confidence != "ACTIONABLE":
        return confirmed
    if confirmed.detector not in {"binary_buy_both", "neg_risk_underround", "nested_threshold_arb"}:
        return confirmed

    asks = [float(x) for x in confirmed.metadata.get("confirmed_asks") or []]
    sizes = [float(x) for x in confirmed.metadata.get("confirmed_sizes") or []]
    if not asks or not sizes:
        return confirmed
    common = min(sizes)
    fees = sum(taker_fee_per_share(x) for x in asks)
    edge = float(confirmed.edge or 0.0)

    if confirmed.detector == "binary_buy_both" and len(asks) == 2:
        confirmed.metadata["yes_ask"], confirmed.metadata["no_ask"] = asks
        confirmed.detail = (
            f"REST-confirmed now: buy YES {asks[0]:.3f} + NO {asks[1]:.3f}; "
            f"est. fees {fees:.4f}; executable edge {edge:.2%}. "
            f"Common visible size ≈ {common:.1f} shares."
        )
        confirmed.metadata["action_steps"] = [
            "Tap OPEN MARKET below.",
            f"Buy YES at {asks[0]:.3f} or lower AND NO at {asks[1]:.3f} or lower using the SAME share count.",
            f"Do not exceed {common:.2f} shares from this alert; both legs must fill.",
            "If either ask or visible size has worsened, SKIP rather than chase.",
        ]
    elif confirmed.detector == "neg_risk_underround":
        raw = sum(asks)
        legs = list(confirmed.metadata.get("legs") or [])
        for i, ask in enumerate(asks):
            if i < len(legs) and isinstance(legs[i], dict):
                legs[i]["ask"] = ask
        confirmed.metadata["legs"] = legs
        confirmed.detail = (
            f"REST-confirmed now across {len(asks)} legs: combined asks {raw:.3f}, "
            f"est. fees {fees:.4f}, executable edge {edge:.2%}. "
            f"Common visible size ≈ {common:.1f} shares."
        )
        confirmed.metadata["action_steps"] = [
            "Tap OPEN EVENT below.",
            f"Use the SAME share count on every required leg, no more than {common:.2f} shares from this alert.",
            "Confirm every listed leg is still available at or below its alert price before starting.",
            "If even one leg cannot fill, SKIP the entire basket.",
        ]
    elif confirmed.detector == "nested_threshold_arb" and len(asks) == 2:
        confirmed.metadata["yes_ask"], confirmed.metadata["no_ask"] = asks
        confirmed.detail = (
            f"REST-confirmed now: looser YES {asks[0]:.3f} + stricter NO {asks[1]:.3f}; "
            f"est. fees {fees:.4f}; executable edge {edge:.2%}. "
            f"Common visible size ≈ {common:.1f} shares."
        )
        prior = list(confirmed.metadata.get("action_steps") or [])
        first = prior[0] if prior else "Open the LOOSER threshold market."
        second = prior[1] if len(prior) > 1 else "Open the STRICTER threshold market."
        confirmed.metadata["action_steps"] = [
            first,
            second,
            f"Use the SAME share count on both legs, no more than {common:.2f} shares from this alert.",
            "If either ask or visible size has worsened, SKIP.",
        ]
    return confirmed


def _watchdog_main() -> None:
    """Hard recovery path for an event-loop/scanner stall.

    systemd is configured with Restart=always.  If scans stop completing while the
    process remains resident, exiting is safer than leaving a silently dead scanner.
    """
    started = time.time()
    while True:
        time.sleep(10.0)
        now = time.time()
        last_scan = base.state.get("last_scan")
        if last_scan is None:
            if now - started <= WATCHDOG_STARTUP_GRACE_SECONDS:
                continue
            log.critical("scanner watchdog: no first scan after %.0fs; forcing systemd restart", now - started)
            os._exit(70)
        try:
            age = now - float(last_scan)
        except (TypeError, ValueError):
            age = WATCHDOG_STALE_SECONDS + 1.0
        if age > WATCHDOG_STALE_SECONDS:
            log.critical("scanner watchdog: last completed scan %.0fs old; forcing systemd restart", age)
            os._exit(70)


def _start_watchdog_once() -> None:
    global _watchdog_started
    if _watchdog_started:
        return
    _watchdog_started = True
    thread = threading.Thread(target=_watchdog_main, name="scanner-process-watchdog", daemon=True)
    thread.start()


async def _stable_startup() -> None:
    global _price_task
    if _price_task is None:
        _price_task = asyncio.create_task(_top_price_loop())
    _start_watchdog_once()
    base.state["runtime_mode"] = "bounded_ws_plus_full_price_discovery"
    base.state["watchdog_stale_seconds"] = WATCHDOG_STALE_SECONDS


async def _stable_shutdown() -> None:
    global _price_task
    if _price_task is not None:
        _price_task.cancel()
        await asyncio.gather(_price_task, return_exceptions=True)
        _price_task = None


# Patch transport/discovery hooks before FastAPI startup runs.
base._all_tokens = _all_tokens_with_priority
base.market_stream.configure = _configure_priority_stream
base.market_stream.snapshot = _merged_book_snapshot
base.confirm_actionable = _confirm_actionable_with_execution_text
hardening._visible_notional = _visible_notional_deferred

# Base startup was registered when app.py was imported.  Append our data task after
# it; insert our shutdown first so the price task stops before base closes HTTP.
app.add_event_handler("startup", _stable_startup)
app.router.on_shutdown.insert(0, _stable_shutdown)
