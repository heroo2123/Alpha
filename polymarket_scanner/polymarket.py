from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Iterable

import httpx

from .config import settings
from .models import Book, Market

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
log = logging.getLogger("polybot.polymarket")


class UniverseIncompleteError(RuntimeError):
    """Gamma pagination could not prove a complete configured universe snapshot."""


def _json_list(value) -> list:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        try:
            data = json.loads(value)
            return data if isinstance(data, list) else []
        except Exception:
            return []
    return []


def _f(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class PolymarketClient:
    def __init__(self) -> None:
        self.http = httpx.AsyncClient(
            timeout=settings.request_timeout,
            limits=httpx.Limits(max_connections=24, max_keepalive_connections=12, keepalive_expiry=20.0),
            headers={"User-Agent": "polymarket-edge-scanner/0.2 (+github)"},
        )
        self._active_cache: list[Market] = []
        self._active_cache_at: float = 0.0
        self._active_refresh_task: asyncio.Task | None = None
        self._active_cache_complete: bool = False
        self._active_cache_reason: str = "uninitialized"
        self._active_last_error: str | None = None
        self._active_last_attempt_at: float = 0.0
        self._active_last_success_at: float = 0.0
        self._fetch_complete: bool = False
        self._fetch_reason: str = "not fetched"

    async def close(self) -> None:
        if self._active_refresh_task is not None:
            self._active_refresh_task.cancel()
            await asyncio.gather(self._active_refresh_task, return_exceptions=True)
            self._active_refresh_task = None
        await self.http.aclose()

    def universe_status(self, *, now: float | None = None) -> dict:
        """Return explicit authority/freshness diagnostics for the cached universe.

        A populated cache is not automatically authoritative. Production may use a
        briefly stale last-known-good snapshot for resilience, but it must suppress
        detector output when the configured hard stale age is exceeded or when the
        fetch hit a configured pagination/cap boundary before exhaustion.
        """
        current = time.time() if now is None else float(now)
        age = current - self._active_cache_at if self._active_cache_at > 0 else None
        max_stale = max(float(settings.universe_refresh_seconds), float(settings.universe_max_stale_seconds))
        hard_stale = age is None or age > max_stale
        refreshing = bool(self._active_refresh_task is not None and not self._active_refresh_task.done())
        safe = bool(self._active_cache) and self._active_cache_complete and not hard_stale
        return {
            "safe_for_detection": safe,
            "market_count": len(self._active_cache),
            "complete": bool(self._active_cache_complete),
            "completeness_reason": self._active_cache_reason,
            "cache_age_seconds": age,
            "max_stale_seconds": max_stale,
            "hard_stale": hard_stale,
            "refresh_in_progress": refreshing,
            "last_attempt_at": self._active_last_attempt_at or None,
            "last_success_at": self._active_last_success_at or None,
            "last_error": self._active_last_error,
        }

    async def _event_page(self, offset: int, *, tag_slug: str | None = None) -> list[dict]:
        """Fetch one Gamma event page with compatibility and transient-failure retries."""
        page_size = max(1, min(int(settings.gamma_page_size), 100))
        base = {
            "active": "true",
            "closed": "false",
            "limit": page_size,
            "offset": offset,
        }
        if tag_slug:
            base["tag_slug"] = tag_slug
        preferred = {**base, "order": "volume", "ascending": "false"}

        attempts = 4
        for attempt in range(attempts):
            try:
                r = await self.http.get(f"{GAMMA}/events", params=preferred)
                if r.status_code == 422:
                    r = await self.http.get(f"{GAMMA}/events", params=base)

                if r.status_code == 429 or r.status_code >= 500:
                    if attempt < attempts - 1:
                        delay = min(4.0, 0.5 * (2 ** attempt))
                        log.warning(
                            "Gamma events page transient HTTP %s offset=%s tag=%s; retrying in %.1fs",
                            r.status_code, offset, tag_slug, delay,
                        )
                        await asyncio.sleep(delay)
                        continue

                r.raise_for_status()
                payload = r.json()
                events = payload.get("events", []) if isinstance(payload, dict) else payload
                return events if isinstance(events, list) else []
            except httpx.RequestError as exc:
                if attempt >= attempts - 1:
                    raise
                delay = min(4.0, 0.5 * (2 ** attempt))
                log.warning(
                    "Gamma events page request failed offset=%s tag=%s attempt=%s/%s: %r; retrying in %.1fs",
                    offset, tag_slug, attempt + 1, attempts, exc, delay,
                )
                await asyncio.sleep(delay)

        return []

    def _append_events(self, out: list[Market], events: list[dict], seen_market_ids: set[str] | None = None) -> None:
        seen = seen_market_ids if seen_market_ids is not None else set()
        for event in events:
            event_id = str(event.get("id", ""))
            event_slug = event.get("slug") or ""
            event_title = event.get("title") or ""
            tags = [str(t.get("slug") or t.get("label") or "") for t in (event.get("tags") or []) if isinstance(t, dict)]
            for raw_market in event.get("markets") or []:
                m = dict(raw_market)
                market_id = str(m.get("id", ""))
                if not market_id or market_id in seen:
                    continue
                if not bool(m.get("active", True)) or bool(m.get("closed", False)):
                    continue
                m["_event"] = event
                outcomes = [str(x) for x in _json_list(m.get("outcomes"))]
                token_ids = [str(x) for x in _json_list(m.get("clobTokenIds"))]
                prices = [_f(x) for x in _json_list(m.get("outcomePrices"))]
                out.append(Market(
                    id=market_id, event_id=event_id,
                    event_slug=event_slug, event_title=event_title,
                    event_neg_risk=bool(event.get("negRisk") or m.get("negRisk")),
                    question=m.get("question") or "", slug=m.get("slug") or "",
                    condition_id=m.get("conditionId") or "", outcomes=outcomes,
                    token_ids=token_ids, outcome_prices=prices,
                    best_bid=_f(m.get("bestBid"), None) if m.get("bestBid") is not None else None,
                    best_ask=_f(m.get("bestAsk"), None) if m.get("bestAsk") is not None else None,
                    liquidity=_f(m.get("liquidityNum") or m.get("liquidity")),
                    volume_24h=_f(m.get("volume24hr") or m.get("volume24hrClob") or m.get("volumeNum")),
                    active=True, closed=False,
                    end_date=m.get("endDate") or m.get("endDateIso"),
                    description=(m.get("description") or event.get("description") or ""),
                    resolution_source=(m.get("resolutionSource") or event.get("resolutionSource") or ""),
                    category=(m.get("category") or event.get("category") or ""),
                    tags=tags, raw=m,
                ))
                seen.add(market_id)

    @staticmethod
    def _pagination_terminal_index(pages: list[list[dict]], page_size: int) -> int | None:
        for i, events in enumerate(pages):
            if len(events) < page_size:
                if any(bool(later) for later in pages[i + 1:]):
                    raise UniverseIncompleteError(
                        "Gamma pagination produced data after a short/empty page; snapshot is internally inconsistent"
                    )
                return i
        return None

    async def _fetch_active_markets(self) -> list[Market]:
        markets: list[Market] = []
        seen: set[str] = set()
        offset = 0
        page_size = max(1, min(int(settings.gamma_page_size), 100))
        page_concurrency = max(1, min(int(settings.gamma_page_concurrency), 16))
        market_cap = max(1, int(settings.max_events))

        self._fetch_complete = False
        self._fetch_reason = "Gamma fetch in progress"
        exhausted = False
        while not exhausted:
            offsets = [offset + i * page_size for i in range(page_concurrency)]
            pages = await asyncio.gather(*(self._event_page(x) for x in offsets))
            terminal = self._pagination_terminal_index(pages, page_size)
            usable_pages = pages if terminal is None else pages[: terminal + 1]

            for events in usable_pages:
                self._append_events(markets, events, seen)
                if len(markets) >= market_cap:
                    self._fetch_reason = (
                        f"configured market cap {market_cap} reached before Gamma pagination exhausted"
                    )
                    raise UniverseIncompleteError(self._fetch_reason)

            if terminal is not None:
                exhausted = True
            offset += page_size * page_concurrency

        # The general query proved exhaustion before the cap. Weather-tag pages are
        # retained only as a compatibility supplement; completeness does not depend
        # on their finite supplement loop because the untagged active query already
        # reached its natural end.
        weather_exhausted = False
        weather_batch = min(4, page_concurrency)
        for first_page in range(0, 20, weather_batch):
            offsets = [(first_page + i) * page_size for i in range(weather_batch) if first_page + i < 20]
            pages = await asyncio.gather(*(self._event_page(x, tag_slug="weather") for x in offsets))
            for events in pages:
                if not events:
                    weather_exhausted = True
                    break
                self._append_events(markets, events, seen)
                if len(events) < page_size:
                    weather_exhausted = True
                    break
            if weather_exhausted:
                break

        self._fetch_complete = True
        self._fetch_reason = "Gamma active-event pagination exhausted before configured market cap"
        return markets

    def _accept_active_snapshot(self, refreshed: list[Market]) -> None:
        if not refreshed:
            raise UniverseIncompleteError("Gamma active universe was empty")
        if not self._fetch_complete:
            raise UniverseIncompleteError(self._fetch_reason or "Gamma universe completeness was not proven")
        now = time.time()
        self._active_cache = refreshed
        self._active_cache_at = now
        self._active_last_success_at = now
        self._active_cache_complete = True
        self._active_cache_reason = self._fetch_reason
        self._active_last_error = None

    async def _refresh_active_cache(self) -> None:
        started = time.time()
        self._active_last_attempt_at = started
        try:
            refreshed = await self._fetch_active_markets()
            self._accept_active_snapshot(refreshed)
            log.info("Gamma background universe refresh completed: %d markets in %.1fs", len(refreshed), time.time() - started)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self._active_last_error = repr(exc)
            log.warning("Gamma background universe refresh failed; retaining current cache only within stale limit: %r", exc)

    async def active_markets(self) -> list[Market]:
        """Return the last complete active universe, with bounded stale reuse.

        Startup performs one complete fetch because there is no cache yet. After
        that, brief Gamma failures may reuse the last complete snapshot while a
        refresh runs. ``universe_status`` independently marks that snapshot unsafe
        after ``universe_max_stale_seconds``; app_trade_only suppresses detector
        output at that point even though the base scanner may keep data for diagnosis.
        """
        if self._active_refresh_task is not None and self._active_refresh_task.done():
            await asyncio.gather(self._active_refresh_task, return_exceptions=True)
            self._active_refresh_task = None

        if not self._active_cache:
            self._active_last_attempt_at = time.time()
            try:
                refreshed = await self._fetch_active_markets()
                self._accept_active_snapshot(refreshed)
            except Exception as exc:
                self._active_last_error = repr(exc)
                raise
            return list(self._active_cache)

        age = time.time() - self._active_cache_at
        if age >= settings.universe_refresh_seconds and self._active_refresh_task is None:
            self._active_refresh_task = asyncio.create_task(self._refresh_active_cache())

        return list(self._active_cache)

    async def book(self, token_id: str) -> Book | None:
        try:
            r = await self.http.get(f"{CLOB}/book", params={"token_id": token_id})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            x = r.json()
            return _book_from_json(token_id, x)
        except Exception:
            return None

    async def books(self, token_ids: Iterable[str]) -> dict[str, Book]:
        ids = list(dict.fromkeys([x for x in token_ids if x]))
        if not ids:
            return {}

        async def chunk_fetch(chunk: list[str]) -> dict[str, Book]:
            try:
                r = await self.http.post(f"{CLOB}/books", json=[{"token_id": t} for t in chunk])
                r.raise_for_status()
                result: dict[str, Book] = {}
                for x in r.json():
                    token = str(x.get("asset_id") or "")
                    if token:
                        result[token] = _book_from_json(token, x)
                return result
            except Exception:
                rows = await asyncio.gather(*(self.book(t) for t in chunk))
                return {t: b for t, b in zip(chunk, rows) if b is not None}

        chunks = [ids[i:i+100] for i in range(0, len(ids), 100)]
        sem = asyncio.Semaphore(4)

        async def guarded(chunk: list[str]):
            async with sem:
                return await chunk_fetch(chunk)

        pieces = await asyncio.gather(*(guarded(c) for c in chunks))
        out: dict[str, Book] = {}
        for piece in pieces:
            out.update(piece)
        return out

    async def market_by_id(self, market_id: str) -> dict | None:
        try:
            r = await self.http.get(f"{GAMMA}/markets/{market_id}")
            r.raise_for_status()
            return r.json()
        except Exception:
            return None


def _book_from_json(token: str, x: dict) -> Book:
    bids = [(_f(i.get("price")), _f(i.get("size"))) for i in x.get("bids", [])]
    asks = [(_f(i.get("price")), _f(i.get("size"))) for i in x.get("asks", [])]
    return Book(token, bids, asks, _f(x.get("last_trade_price"), None), str(x.get("timestamp") or ""))


def taker_fee_per_share(price: float, fee_rate: float = 0.07) -> float:
    """Conservative fee-curve estimate when per-token fee details are unavailable."""
    return fee_rate * price * (1.0 - price)