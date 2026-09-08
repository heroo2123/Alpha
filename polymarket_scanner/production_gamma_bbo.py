from __future__ import annotations

"""Production discovery transport for the detector-eligible universe.

Gamma BBO is discovery/screening evidence only. It is never execution authority:
ACTIONABLE candidates continue through the existing REST full-book confirmation path
before persistence, and TRADE NOW delivery still rebuilds execution authority again.

This module removes the recurring whole-universe CLOB /prices sweep. Instead, every
accepted Gamma universe refresh seeds a compact YES/NO screening snapshot. The
bounded WebSocket hot set can override it when fresher, while exact candidate
confirmation remains unchanged.
"""

import asyncio
import math
import os
import time
from collections.abc import Callable, Iterable

import httpx

from .config import settings
from .models import Book, Market
from .polymarket import GAMMA, UniverseIncompleteError
from .production_universe import ProductionPolymarketClient

GAMMA_SCREENING_VERSION = "gamma_bbo_screening_v2_partial_sides_exact_candidate_rest"
PRODUCTION_REFRESH_TRANSPORT_VERSION = "dedicated_gamma_ipv6_keepalive_v1"
RUNTIME_MODE = "complete_gamma_filtered_universe_plus_gamma_bbo_screening"

# The complete Gamma keyset walk now takes several minutes on the e2-micro. Keep one
# refresh owner, give startup enough time to finish a full walk, and leave a bounded
# last-known-good window for transient Gamma failures. These are production-runtime
# floors; an operator may choose larger values through the dedicated environment
# variables, but not smaller ones accidentally.
PRODUCTION_UNIVERSE_REFRESH_SECONDS = max(
    600,
    int(os.getenv("PRODUCTION_UNIVERSE_REFRESH_SECONDS", "600")),
)
PRODUCTION_UNIVERSE_MAX_STALE_SECONDS = max(
    1800,
    int(os.getenv("PRODUCTION_UNIVERSE_MAX_STALE_SECONDS", "1800")),
)
PRODUCTION_WATCHDOG_STARTUP_GRACE_SECONDS = max(
    900.0,
    float(os.getenv("PRODUCTION_WATCHDOG_STARTUP_GRACE_SECONDS", "900")),
)
PRODUCTION_GAMMA_SLOW_PAGE_SECONDS = max(
    2.0,
    float(os.getenv("PRODUCTION_GAMMA_SLOW_PAGE_SECONDS", "5")),
)


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


class ScannerOwnedProductionPolymarketClient(ProductionPolymarketClient):
    """Production client whose full-universe refresh is owned only by scanner_loop.

    Gamma discovery has its own IPv6-bound HTTP pool. The base client also services
    CLOB confirmation and settlement requests; sharing that pool allowed unrelated
    market traffic to evict the sequential Gamma keepalive connection on the IPv6-only
    e2-micro. A dedicated Gamma pool keeps discovery transport independent while CLOB
    REST remains the final execution authority.

    Authority telemetry is also snapshot-consistent: counters exposed at the top
    level always describe the last *accepted complete* universe. Partial counters from
    an in-progress traversal are published only under ``refresh_progress`` and can
    never masquerade as properties of the accepted snapshot.
    """

    def __init__(
        self,
        *,
        sports_slug_provider: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        super().__init__(sports_slug_provider=sports_slug_provider)

        # The production VM is intentionally IPv6-only. Binding the discovery pool
        # to :: makes IPv4 candidates fail locally instead of consuming most of the
        # 20-second request timeout before an IPv6 connection is attempted.
        transport = httpx.AsyncHTTPTransport(
            local_address="::",
            retries=0,
            limits=httpx.Limits(
                max_connections=4,
                max_keepalive_connections=2,
                keepalive_expiry=120.0,
            ),
        )
        self._gamma_http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            transport=transport,
            headers={"User-Agent": "polymarket-edge-scanner/0.2 (+github)"},
        )

        self._full_fetch_in_progress = False
        self._refresh_started_at: float | None = None
        self._refresh_page_elapsed_total = 0.0
        self._refresh_page_elapsed_max = 0.0
        self._refresh_slow_pages = 0

        self._accepted_discovered_market_count = 0
        self._accepted_materialized_market_count = 0
        self._accepted_keyset_page_count = 0
        self._accepted_sports_slug_snapshot_count = 0
        self._accepted_full_fetch_seconds: float | None = None
        self._accepted_page_elapsed_average: float | None = None
        self._accepted_page_elapsed_max: float | None = None
        self._accepted_slow_pages = 0
        self._last_attempt_full_fetch_seconds: float | None = None

    async def close(self) -> None:
        await self._gamma_http.aclose()
        await super().close()

    async def _event_keyset_page(
        self,
        after_cursor: str | None,
        *,
        tag_slug: str | None = None,
    ) -> tuple[list[dict], str | None]:
        """Fetch one validated keyset page on the dedicated Gamma IPv6 pool."""
        page_size = max(1, min(int(settings.gamma_page_size), 100))
        params: dict[str, object] = {
            "active": "true",
            "closed": "false",
            "limit": page_size,
        }
        if after_cursor:
            params["after_cursor"] = after_cursor
        if tag_slug:
            params["tag_slug"] = tag_slug

        started = time.monotonic()
        try:
            attempts = 4
            for attempt in range(attempts):
                try:
                    response = await self._gamma_http.get(
                        f"{GAMMA}/events/keyset",
                        params=params,
                    )
                    if response.status_code == 429 or response.status_code >= 500:
                        if attempt < attempts - 1:
                            delay = min(4.0, 0.5 * (2 ** attempt))
                            await asyncio.sleep(delay)
                            continue

                    response.raise_for_status()
                    payload = response.json()
                    if not isinstance(payload, dict):
                        raise UniverseIncompleteError(
                            "Gamma keyset response is not an object"
                        )
                    events = payload.get("events")
                    if not isinstance(events, list):
                        raise UniverseIncompleteError(
                            "Gamma keyset response is missing an events list"
                        )

                    raw_cursor = payload.get("next_cursor")
                    if raw_cursor is not None and not isinstance(raw_cursor, str):
                        raise UniverseIncompleteError(
                            "Gamma keyset next_cursor is malformed"
                        )
                    next_cursor = raw_cursor.strip() if isinstance(raw_cursor, str) else ""
                    next_cursor = next_cursor or None
                    if next_cursor is not None and not events:
                        raise UniverseIncompleteError(
                            "Gamma keyset returned an empty events page with a continuation cursor"
                        )
                    return events, next_cursor
                except httpx.RequestError:
                    if attempt >= attempts - 1:
                        raise
                    delay = min(4.0, 0.5 * (2 ** attempt))
                    await asyncio.sleep(delay)

            raise UniverseIncompleteError(
                "Gamma keyset retries exhausted without a usable page"
            )
        finally:
            elapsed = time.monotonic() - started
            self._refresh_page_elapsed_total += elapsed
            self._refresh_page_elapsed_max = max(
                self._refresh_page_elapsed_max,
                elapsed,
            )
            if elapsed >= PRODUCTION_GAMMA_SLOW_PAGE_SECONDS:
                self._refresh_slow_pages += 1

    def _reset_refresh_progress(self) -> None:
        self._refresh_page_elapsed_total = 0.0
        self._refresh_page_elapsed_max = 0.0
        self._refresh_slow_pages = 0

    def _accept_refresh_telemetry(self, elapsed: float) -> None:
        pages = int(self._keyset_page_count)
        self._accepted_discovered_market_count = int(self._discovered_market_count)
        self._accepted_materialized_market_count = int(self._materialized_market_count)
        self._accepted_keyset_page_count = pages
        self._accepted_sports_slug_snapshot_count = len(
            getattr(self, "_sports_slug_snapshot", set())
        )
        self._accepted_full_fetch_seconds = round(elapsed, 3)
        self._accepted_page_elapsed_average = (
            round(self._refresh_page_elapsed_total / pages, 3)
            if pages > 0
            else None
        )
        self._accepted_page_elapsed_max = round(
            self._refresh_page_elapsed_max,
            3,
        )
        self._accepted_slow_pages = int(self._refresh_slow_pages)

    async def active_markets(self) -> list[Market]:
        self._active_last_attempt_at = time.time()
        self._refresh_started_at = self._active_last_attempt_at
        self._full_fetch_in_progress = True
        self._reset_refresh_progress()
        started = time.monotonic()
        succeeded = False
        try:
            refreshed = await self._fetch_active_markets()
            self._accept_active_snapshot(refreshed)
            elapsed = time.monotonic() - started
            self._accept_refresh_telemetry(elapsed)
            succeeded = True
            return list(self._active_cache)
        except Exception as exc:
            self._active_last_error = repr(exc)
            raise
        finally:
            elapsed = time.monotonic() - started
            self._last_attempt_full_fetch_seconds = round(elapsed, 3)
            self._full_fetch_in_progress = False
            self._refresh_started_at = None
            if not succeeded:
                # The accepted counters above deliberately remain untouched.
                pass

    def universe_status(self, *, now: float | None = None) -> dict:
        status = super().universe_status(now=now)
        current = time.time() if now is None else float(now)

        status.update({
            "refresh_owner": "scanner_loop_only",
            "refresh_transport": PRODUCTION_REFRESH_TRANSPORT_VERSION,
            "refresh_in_progress": bool(self._full_fetch_in_progress),
            "discovered_market_count": self._accepted_discovered_market_count,
            "materialized_market_count": self._accepted_materialized_market_count,
            "keyset_pages": self._accepted_keyset_page_count,
            "sports_slug_snapshot_count": self._accepted_sports_slug_snapshot_count,
            "last_full_fetch_seconds": self._accepted_full_fetch_seconds,
            "last_attempt_full_fetch_seconds": self._last_attempt_full_fetch_seconds,
            "accepted_page_average_seconds": self._accepted_page_elapsed_average,
            "accepted_page_max_seconds": self._accepted_page_elapsed_max,
            "accepted_slow_pages": self._accepted_slow_pages,
        })

        if self._full_fetch_in_progress:
            started_at = self._refresh_started_at
            pages = int(self._keyset_page_count)
            status["refresh_progress"] = {
                "started_at": started_at,
                "elapsed_seconds": (
                    current - started_at if started_at is not None else None
                ),
                "discovered_market_count": int(self._discovered_market_count),
                "materialized_market_count": int(self._materialized_market_count),
                "keyset_pages": pages,
                "page_average_seconds": (
                    self._refresh_page_elapsed_total / pages if pages > 0 else None
                ),
                "page_max_seconds": self._refresh_page_elapsed_max,
                "slow_pages": int(self._refresh_slow_pages),
            }
        else:
            status["refresh_progress"] = None

        return status


def gamma_screening_books(markets: list[Market], *, received_at: float | None = None) -> dict[str, Book]:
    """Build ask-usable binary screening books from Gamma's embedded YES BBO.

    Gamma can expose only one side of the YES top-of-book. Preserve whatever side is
    actually usable instead of requiring both bid and ask:

    * YES ask is directly usable to screen YES buys.
    * YES bid implies the complementary NO ask as ``1 - YES bid``.
    * When both sides exist, retain both bids as well for spread/anomaly screening.

    A crossed Gamma BBO is deliberately *not* rejected here: ``YES ask < YES bid`` is
    exactly the discovery anomaly that can imply a binary buy-both underround. Gamma
    remains screening evidence only; every ACTIONABLE candidate is independently
    rebuilt from current CLOB full books before persistence/delivery.

    Synthetic level size is 1 share solely so discovery detectors that require a
    visible top level can emit a candidate for exact REST confirmation. No Gamma size
    is ever treated as certified executable capacity.
    """
    receipt = time.time() if received_at is None else float(received_at)
    books: dict[str, Book] = {}

    for market in markets:
        tokens = _strict_yes_no_tokens(market)
        if tokens is None:
            continue

        bid = _valid_price(market.best_bid)
        ask = _valid_price(market.best_ask)
        if bid is None and ask is None:
            continue

        yes_token, no_token = tokens

        # Only insert a token into the discovery map when an executable ask can be
        # screened for that token. This keeps len(_price_books) aligned with ask-side
        # discovery coverage used by health telemetry.
        if ask is not None:
            yes_bids = [(bid, 1.0)] if bid is not None else []
            books[yes_token] = Book(
                token_id=yes_token,
                bids=yes_bids,
                asks=[(ask, 1.0)],
                timestamp="price-discovery",
                received_at=receipt,
                source="gamma_bbo_screening",
            )

        if bid is not None:
            no_ask = _valid_price(1.0 - bid)
            if no_ask is not None:
                no_bid = _valid_price(1.0 - ask) if ask is not None else None
                books[no_token] = Book(
                    token_id=no_token,
                    bids=[(no_bid, 1.0)] if no_bid is not None else [],
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

    # Tune the large-universe runtime before scanner startup. The outer scanner loop
    # is the only Gamma refresh owner; the client below performs one real full walk
    # per scanner-scheduled refresh instead of adding its own background cache task.
    base.settings.universe_refresh_seconds = max(
        int(base.settings.universe_refresh_seconds),
        PRODUCTION_UNIVERSE_REFRESH_SECONDS,
    )
    base.settings.universe_max_stale_seconds = max(
        int(base.settings.universe_max_stale_seconds),
        PRODUCTION_UNIVERSE_MAX_STALE_SECONDS,
    )
    stable.WATCHDOG_STARTUP_GRACE_SECONDS = max(
        float(stable.WATCHDOG_STARTUP_GRACE_SECONDS),
        PRODUCTION_WATCHDOG_STARTUP_GRACE_SECONDS,
    )

    old_poly = base.poly
    production_poly = ScannerOwnedProductionPolymarketClient(
        sports_slug_provider=lambda: base.sports_stream.snapshot().keys(),
    )
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
        base.state["price_discovery_source"] = (
            "Gamma embedded ask-side screening; exact CLOB books required for candidates"
        )
        return tokens

    async def _gamma_refresh_clock_loop() -> None:
        # Gamma screening is refreshed by the universe loader itself. Keep the
        # startup task alive because app_stable's lifecycle expects a price task,
        # but perform zero whole-universe CLOB requests here.
        while True:
            await stable.asyncio.sleep(60.0)

    async def _close_replaced_client() -> None:
        try:
            await old_poly.close()
        except Exception:
            pass

    async def _mark_gamma_runtime() -> None:
        base.state["runtime_mode"] = RUNTIME_MODE
        base.state["price_discovery_diagnostics_version"] = GAMMA_SCREENING_VERSION
        base.state["price_discovery_source"] = (
            "Gamma embedded ask-side screening; exact CLOB books required for candidates"
        )
        base.state["price_snapshot_target_seconds"] = stable.TOP_PRICE_REFRESH_SECONDS
        base.state["price_snapshot_authoritative_transport_complete"] = bool(
            stable._price_snapshot_at is not None
        )
        base.state["universe_refresh_owner"] = "scanner_loop_only"
        base.state["universe_refresh_seconds"] = int(base.settings.universe_refresh_seconds)
        base.state["universe_max_stale_seconds"] = int(base.settings.universe_max_stale_seconds)
        base.state["watchdog_startup_grace_seconds"] = float(
            stable.WATCHDOG_STARTUP_GRACE_SECONDS
        )
        base.state["gamma_refresh_transport"] = PRODUCTION_REFRESH_TRANSPORT_VERSION

    base._all_tokens = _all_tokens_with_gamma_screening
    stable._top_price_loop = _gamma_refresh_clock_loop
    stable.TOP_PRICE_REFRESH_SECONDS = max(
        60.0,
        float(base.settings.universe_refresh_seconds),
    )

    base.state["runtime_mode"] = RUNTIME_MODE
    base.state["price_discovery_diagnostics_version"] = GAMMA_SCREENING_VERSION
    base.state["universe_refresh_owner"] = "scanner_loop_only"
    base.state["gamma_refresh_transport"] = PRODUCTION_REFRESH_TRANSPORT_VERSION

    stable.app.router.on_startup.insert(0, _close_replaced_client)
    stable.app.add_event_handler("startup", _mark_gamma_runtime)
