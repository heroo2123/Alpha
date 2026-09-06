from __future__ import annotations

import asyncio
import json
import logging
from typing import Iterable

import httpx

from .config import settings
from .models import Book, Market

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
log = logging.getLogger("polybot.polymarket")


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

    async def close(self) -> None:
        await self.http.aclose()

    async def _event_page(self, offset: int, *, tag_slug: str | None = None) -> list[dict]:
        """Fetch one Gamma event page with compatibility and transient-failure retries.

        Gamma discovery is a non-trading, idempotent GET. A single network/5xx/429
        hiccup must not take the scanner down, so retry with bounded exponential
        backoff before surfacing the failure to the caller.
        """
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

    async def active_markets(self) -> list[Market]:
        markets: list[Market] = []
        seen: set[str] = set()
        offset = 0
        page_size = max(1, min(int(settings.gamma_page_size), 100))
        page_concurrency = max(1, min(int(settings.gamma_page_concurrency), 16))

        # Broad liquid universe, capped for the small free-tier VM. Gamma offsets
        # are independent, so fetch several pages in parallel rather than waiting
        # on dozens of sequential round trips every two minutes.
        exhausted = False
        while len(markets) < settings.max_events and not exhausted:
            offsets = [offset + i * page_size for i in range(page_concurrency)]
            pages = await asyncio.gather(*(self._event_page(x) for x in offsets))
            for events in pages:
                if not events:
                    exhausted = True
                    break
                self._append_events(markets, events, seen)
                if len(markets) >= settings.max_events:
                    break
                if len(events) < page_size:
                    exhausted = True
                    break
            offset += page_size * page_concurrency

        markets = markets[: settings.max_events]
        seen = {m.id for m in markets}

        # Weather is a priority strategy family and must never disappear merely
        # because the broad market cap filled first. Fetch its smaller tag-specific
        # pagination in parallel too, with a hard 20-page safety cap.
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

        return markets

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
