from __future__ import annotations

import asyncio
import json
from typing import Iterable

import httpx

from .config import settings
from .models import Book, Market

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


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
            headers={"User-Agent": "polymarket-edge-scanner/0.1 (+github)"},
        )

    async def close(self) -> None:
        await self.http.aclose()

    async def active_markets(self) -> list[Market]:
        markets: list[Market] = []
        offset = 0
        while len(markets) < settings.max_events:
            params = {
                "active": "true",
                "closed": "false",
                "limit": settings.gamma_page_size,
                "offset": offset,
                "order": "volume_24hr",
                "ascending": "false",
            }
            r = await self.http.get(f"{GAMMA}/events", params=params)
            r.raise_for_status()
            payload = r.json()
            events = payload.get("events", []) if isinstance(payload, dict) else payload
            if not events:
                break
            for event in events:
                event_id = str(event.get("id", ""))
                event_slug = event.get("slug") or ""
                event_title = event.get("title") or ""
                tags = [str(t.get("slug") or t.get("label") or "") for t in (event.get("tags") or []) if isinstance(t, dict)]
                for m in event.get("markets") or []:
                    outcomes = [str(x) for x in _json_list(m.get("outcomes"))]
                    token_ids = [str(x) for x in _json_list(m.get("clobTokenIds"))]
                    prices = [_f(x) for x in _json_list(m.get("outcomePrices"))]
                    markets.append(
                        Market(
                            id=str(m.get("id", "")),
                            event_id=event_id,
                            event_slug=event_slug,
                            event_title=event_title,
                            event_neg_risk=bool(event.get("negRisk") or m.get("negRisk")),
                            question=m.get("question") or "",
                            slug=m.get("slug") or "",
                            condition_id=m.get("conditionId") or "",
                            outcomes=outcomes,
                            token_ids=token_ids,
                            outcome_prices=prices,
                            best_bid=_f(m.get("bestBid"), None) if m.get("bestBid") is not None else None,
                            best_ask=_f(m.get("bestAsk"), None) if m.get("bestAsk") is not None else None,
                            liquidity=_f(m.get("liquidityNum") or m.get("liquidity")),
                            volume_24h=_f(m.get("volume24hr") or m.get("volume24hrClob") or m.get("volumeNum")),
                            active=bool(m.get("active", True)),
                            closed=bool(m.get("closed", False)),
                            end_date=m.get("endDate") or m.get("endDateIso"),
                            description=(m.get("description") or event.get("description") or ""),
                            resolution_source=(m.get("resolutionSource") or event.get("resolutionSource") or ""),
                            category=(m.get("category") or event.get("category") or ""),
                            tags=tags,
                            raw=m,
                        )
                    )
            if len(events) < settings.gamma_page_size:
                break
            offset += settings.gamma_page_size
        return markets

    async def book(self, token_id: str) -> Book | None:
        try:
            r = await self.http.get(f"{CLOB}/book", params={"token_id": token_id})
            if r.status_code == 404:
                return None
            r.raise_for_status()
            x = r.json()
            bids = [(_f(i.get("price")), _f(i.get("size"))) for i in x.get("bids", [])]
            asks = [(_f(i.get("price")), _f(i.get("size"))) for i in x.get("asks", [])]
            return Book(token_id, bids, asks, _f(x.get("last_trade_price"), None), str(x.get("timestamp") or ""))
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
                    if not token:
                        continue
                    bids = [(_f(i.get("price")), _f(i.get("size"))) for i in x.get("bids", [])]
                    asks = [(_f(i.get("price")), _f(i.get("size"))) for i in x.get("asks", [])]
                    result[token] = Book(token, bids, asks, _f(x.get("last_trade_price"), None), str(x.get("timestamp") or ""))
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


def taker_fee_per_share(price: float, fee_rate: float = 0.07) -> float:
    """Conservative generic fee estimate: use the highest current category rate."""
    return fee_rate * price * (1.0 - price)
