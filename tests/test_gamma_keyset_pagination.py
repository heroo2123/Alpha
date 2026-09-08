import asyncio

import httpx
import pytest

from polymarket_scanner.config import settings
from polymarket_scanner.polymarket import PolymarketClient, UniverseIncompleteError


def _event(event_id: str, market_ids: list[str]) -> dict:
    return {
        "id": event_id,
        "slug": f"event-{event_id}",
        "title": f"Event {event_id}",
        "markets": [
            {
                "id": market_id,
                "active": True,
                "closed": False,
                "question": f"Question {market_id}",
                "slug": f"market-{market_id}",
                "conditionId": f"condition-{market_id}",
                "outcomes": '["Yes","No"]',
                "clobTokenIds": f'["{market_id}-yes","{market_id}-no"]',
                "outcomePrices": '["0.5","0.5"]',
            }
            for market_id in market_ids
        ],
    }


def test_keyset_page_uses_after_cursor_and_never_offset(monkeypatch):
    requests: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((str(request.url), dict(request.url.params)))
        return httpx.Response(
            200,
            json={"events": [_event("1", ["m1"])], "next_cursor": "next-2"},
        )

    async def run():
        client = PolymarketClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client._event_keyset_page("cursor-1")
        finally:
            await client.close()

    monkeypatch.setattr(settings, "gamma_page_size", 100)
    events, cursor = asyncio.run(run())
    assert len(events) == 1
    assert cursor == "next-2"
    assert len(requests) == 1
    url, params = requests[0]
    assert "/events/keyset" in url
    assert params["after_cursor"] == "cursor-1"
    assert "offset" not in params


def test_full_universe_walks_keyset_to_natural_exhaustion(monkeypatch):
    calls: list[str | None] = []
    pages = {
        None: ([_event("1", ["m1", "m2"])], "c1"),
        "c1": ([_event("2", ["m2", "m3"])], "c2"),
        "c2": ([_event("3", ["m4"])], None),
    }

    async def run():
        client = PolymarketClient()

        async def fake_keyset(after_cursor=None, *, tag_slug=None):
            assert tag_slug is None
            calls.append(after_cursor)
            return pages[after_cursor]

        async def fake_offset(_offset, *, tag_slug=None):
            assert tag_slug == "weather"
            return []

        client._event_keyset_page = fake_keyset
        client._event_page = fake_offset
        try:
            return await client._fetch_active_markets(), client._fetch_complete, client._fetch_reason
        finally:
            await client.close()

    monkeypatch.setattr(settings, "max_events", 100)
    markets, complete, reason = asyncio.run(run())
    assert [m.id for m in markets] == ["m1", "m2", "m3", "m4"]
    assert calls == [None, "c1", "c2"]
    assert complete is True
    assert "keyset pagination exhausted" in reason


def test_repeated_keyset_cursor_fails_closed(monkeypatch):
    async def run():
        client = PolymarketClient()

        async def fake_keyset(after_cursor=None, *, tag_slug=None):
            if after_cursor is None:
                return [_event("1", ["m1"])], "repeat"
            return [_event("2", ["m2"])], "repeat"

        client._event_keyset_page = fake_keyset
        try:
            return await client._fetch_active_markets()
        finally:
            await client.close()

    monkeypatch.setattr(settings, "max_events", 100)
    with pytest.raises(UniverseIncompleteError, match="repeated keyset cursor"):
        asyncio.run(run())


def test_market_cap_still_fails_closed_when_more_keyset_pages_exist(monkeypatch):
    async def run():
        client = PolymarketClient()

        async def fake_keyset(after_cursor=None, *, tag_slug=None):
            return [_event("1", ["m1", "m2"])], "more"

        client._event_keyset_page = fake_keyset
        try:
            return await client._fetch_active_markets()
        finally:
            await client.close()

    monkeypatch.setattr(settings, "max_events", 2)
    with pytest.raises(UniverseIncompleteError, match="configured market cap 2 reached"):
        asyncio.run(run())


def test_malformed_keyset_envelope_fails_closed(monkeypatch):
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[_event("1", ["m1"])])

    async def run():
        client = PolymarketClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client._event_keyset_page(None)
        finally:
            await client.close()

    with pytest.raises(UniverseIncompleteError, match="keyset response is not an object"):
        asyncio.run(run())
