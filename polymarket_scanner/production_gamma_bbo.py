from __future__ import annotations

"""Production discovery transport for the detector-eligible universe.

Gamma BBO is discovery/screening evidence only.  It is never execution authority:
ACTIONABLE candidates continue through the existing REST full-book confirmation path
before persistence, and TRADE NOW delivery still rebuilds execution authority again.

This module removes the recurring whole-universe CLOB /prices sweep.  Instead, every
accepted Gamma universe refresh seeds a compact YES/NO BBO snapshot.  The bounded
WebSocket hot set can override it when fresher, while exact candidate confirmation
remains unchanged.
"""

import math
import os
import time

from .models import Book, Market
from .production_universe import ProductionPolymarketClient

GAMMA_SCREENING_VERSION = "gamma_bbo_screening_v1_exact_candidate_rest"


def _valid_price(value: object) -> float | None:
    try:
        price = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return price if math.isfinite(price) and 0.0 < price < 1.0 else None


def _strict_yes_no_tokens(market: Market) -> tuple[str, str] | None:
    if len(market.outcomes) != 2 or len(market.token_ids) != 2:
        return None
    labels = [str(x).strip().lower() for x in market.outcomes]
    tokens = [str(x).strip() for x in market.token_ids]
    if sorted(labels) != ["no", "yes"] or len(set(tokens)) != 2 or not all(tokens):
        return None
    return tokens[labels.index("yes")], tokens[labels.index("no")]


def gamma_screening_books(markets: list[Market], *, received_at: float | None = None) -> dict[str, Book]:
    """Build binary complement BBO books from Gamma's embedded YES best bid/ask.

    The synthetic level size is 1 share solely so single-leg discovery detectors
    that require a non-zero visible top level can emit a candidate for subsequent
    exact REST confirmation.  Structural hardening already defers discovery-size
    gating for ``timestamp='price-discovery'`` rows.  No Gamma size is ever treated
    as certified executable capacity.
    """
    receipt = time.time() if received_at is None else float(received_at)
    books: dict[str, Book] = {}
    for market in markets:
        tokens = _strict_yes_no_tokens(market)
        if tokens is None:
            continue
        bid = _valid_price(market.best_bid)
        ask = _valid_price(market.best_ask)
        if bid is None or ask is None or ask < bid:
            continue
        yes_token, no_token = tokens
        no_bid = _valid_price(1.0 - ask)
        no_ask = _valid_price(1.0 - bid)
        if no_bid is None or no_ask is None or no_ask < no_bid:
            continue

        books[yes_token] = Book(
            token_id=yes_token,
            bids=[(bid, 1.0)],
            asks=[(ask, 1.0)],
            timestamp="price-discovery",
            received_at=receipt,
            source="gamma_bbo_screening",
        )
        books[no_token] = Book(
            token_id=no_token,
            bids=[(no_bid, 1.0)],
            asks=[(no_ask, 1.0)],
            timestamp="price-discovery",
            received_at=receipt,
            source="gamma_bbo_screening",
        )
    return books


def install_production_gamma_runtime(stable_v2_module) -> None:
    """Patch the already-imported stable runtime before FastAPI startup executes."""
    stable = stable_v2_module.stable
    base = stable.base

    if os.getenv("ENABLE_DUPLICATE_DIVERGENCE_WATCH", "0").strip().lower() in {
        "1", "true", "yes", "on"
    }:
        raise RuntimeError(
            "compact production universe is incompatible with duplicate-divergence WATCH; "
            "disable that research lane before startup"
        )

    old_poly = base.poly
    production_poly = ProductionPolymarketClient()
    base.poly = production_poly

    original_all_tokens = base._all_tokens

    def _all_tokens_with_gamma_screening(markets: list[Market]) -> list[str]:
        tokens = original_all_tokens(markets)
        now = time.time()
        screening = gamma_screening_books(markets, received_at=now)
        stable._price_books = screening
        stable._price_snapshot_at = now
        stable._price_refresh_error = None
        base.state["price_snapshot_tokens"] = len(screening)
        base.state["price_snapshot_at"] = now
        base.state["price_snapshot_seconds"] = 0.0
        base.state["price_snapshot_error"] = None
        base.state["price_snapshot_authoritative_transport_complete"] = True
        base.state["price_snapshot_last_attempt_transport_complete"] = True
        base.state["price_snapshot_usable_coverage_ratio"] = (
            len(screening) / len(tokens) if tokens else 0.0
        )
        base.state["price_discovery_diagnostics_version"] = GAMMA_SCREENING_VERSION
        base.state["price_discovery_source"] = "Gamma embedded BBO screening; exact CLOB books required for candidates"
        return tokens

    async def _gamma_refresh_clock_loop() -> None:
        # Gamma screening is refreshed by the universe loader itself.  Keep the
        # startup task alive because app_stable's lifecycle expects a price task,
        # but perform zero whole-universe CLOB requests here.
        while True:
            await stable.asyncio.sleep(60.0)

    async def _close_replaced_client() -> None:
        try:
            await old_poly.close()
        except Exception:
            pass

    # app.py calls base._all_tokens after every accepted universe refresh.
    base._all_tokens = _all_tokens_with_gamma_screening
    # app_stable._stable_startup resolves this symbol from app_stable at runtime.
    stable._top_price_loop = _gamma_refresh_clock_loop
    # app_trade_only computes price-discovery staleness from this target interval.
    # Tie it to Gamma refresh cadence instead of the obsolete 20-second /prices loop.
    stable.TOP_PRICE_REFRESH_SECONDS = max(
        60.0,
        float(getattr(base.settings, "universe_refresh_seconds", 120)),
    )

    base.state["runtime_mode"] = "complete_gamma_filtered_universe_plus_gamma_bbo_screening"
    base.state["price_discovery_diagnostics_version"] = GAMMA_SCREENING_VERSION

    # Close the unused base HTTP client before scanner startup.  Startup handlers
    # run sequentially, and inserting at index 0 makes this happen before app.py's
    # scanner task is created.
    stable.app.router.on_startup.insert(0, _close_replaced_client)
