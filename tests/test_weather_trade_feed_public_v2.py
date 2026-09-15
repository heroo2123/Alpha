from __future__ import annotations

import asyncio

import httpx

from polymarket_scanner.weather_only_trade_feed_public_v2 import (
    PublicWeatherDataAPITradeFeedClient,
)


CONDITION = "0x" + "1" * 64
TOKEN = "123456789"
TX = "0x" + "2" * 64
WALLET = "0x" + "3" * 40


def _payload():
    return {
        "data": [
            {
                "condition_id": CONDITION,
                "token_id": TOKEN,
                "transaction_hash": TX,
                "proxy_wallet": WALLET,
                "side": "SELL",
                "size": 3.0,
                "price": 0.42,
                "timestamp": 1789480000,
                "outcome": "Yes",
                "outcome_index": 0,
            }
        ],
        "pagination": {
            "has_more": False,
            "next_cursor": None,
        },
    }


def test_public_v2_trade_feed_uses_no_authorization_header_and_current_filters():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["authorization"] = request.headers.get("Authorization")
        seen["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=_payload(),
            headers={"Content-Type": "application/json", "Content-Encoding": "identity"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = PublicWeatherDataAPITradeFeedClient(http=http)
    try:
        page = asyncio.run(client.page(condition_ids=(CONDITION,), limit=50))
    finally:
        asyncio.run(http.aclose())

    assert seen["authorization"] is None
    assert seen["query"]["condition"] == CONDITION
    assert seen["query"]["taker_only"] == "true"
    assert seen["query"]["filter_type"] == "TOKENS"
    assert seen["query"]["filter_amount"] == "0.01"
    assert seen["query"]["limit"] == "50"
    assert len(page.trades) == 1
    assert page.trades[0].aggressor_side == "SELL"
    assert page.trades[0].token_id == TOKEN


def test_public_v2_trade_feed_replays_same_filters_with_cursor():
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(
            200,
            json={"data": [], "pagination": {"has_more": False, "next_cursor": None}},
            headers={"Content-Type": "application/json", "Content-Encoding": "identity"},
            request=request,
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = PublicWeatherDataAPITradeFeedClient(http=http)
    try:
        page = asyncio.run(
            client.page(condition_ids=(CONDITION,), cursor="opaque-cursor", limit=999)
        )
    finally:
        asyncio.run(http.aclose())

    assert seen["condition"] == CONDITION
    assert seen["taker_only"] == "true"
    assert seen["filter_type"] == "TOKENS"
    assert seen["filter_amount"] == "0.01"
    assert seen["cursor"] == "opaque-cursor"
    assert "limit" not in seen
    assert page.trades == ()
