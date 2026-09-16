from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from polymarket_scanner.weather_only_trade_feed import WeatherTradeFeedError
from polymarket_scanner.weather_only_trade_feed_public_v2 import (
    PUBLIC_DATA_TRADES_URL,
    PublicWeatherDataAPITradeFeedClient,
    parse_public_data_trade_page,
)


CONDITION = "0x" + "1" * 64
TOKEN = "123456789"
TX = "0x" + "2" * 64
WALLET = "0x" + "3" * 40
NOW = 1_789_480_100.0


def _row(**updates):
    value = {
        "conditionId": CONDITION,
        "asset": TOKEN,
        "transactionHash": TX,
        "proxyWallet": WALLET,
        "side": "SELL",
        "size": 3.0,
        "price": 0.42,
        "timestamp": 1_789_480_000,
        "outcome": "Yes",
        "outcomeIndex": 0,
        "title": "Weather market",
        "slug": "weather-market",
        "eventSlug": "weather-event",
    }
    value.update(updates)
    return value


def test_public_trade_feed_uses_current_endpoint_no_auth_and_current_filters(monkeypatch):
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url.copy_with(query=None))
        seen["authorization"] = request.headers.get("Authorization")
        seen["query"] = dict(request.url.params)
        return httpx.Response(
            200,
            json=[_row()],
            headers={"Content-Type": "application/json", "Content-Encoding": "identity"},
            request=request,
        )

    monkeypatch.setattr(time, "time", lambda: NOW)
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = PublicWeatherDataAPITradeFeedClient(http=http)
    try:
        page = asyncio.run(client.page(condition_ids=(CONDITION,), limit=50, offset=0))
    finally:
        asyncio.run(http.aclose())

    assert seen["url"] == PUBLIC_DATA_TRADES_URL
    assert seen["authorization"] is None
    assert seen["query"]["market"] == CONDITION
    assert seen["query"]["takerOnly"] == "true"
    assert seen["query"]["filterType"] == "TOKENS"
    assert seen["query"]["filterAmount"] == "0.01"
    assert seen["query"]["limit"] == "50"
    assert seen["query"]["offset"] == "0"
    assert len(page.trades) == 1
    assert page.trades[0].aggressor_side == "SELL"
    assert page.trades[0].token_id == TOKEN
    assert page.trades[0].source == "POLYMARKET_DATA_API_PUBLIC_TAKER_ONLY"
    assert page.next_offset is None
    assert page.actual_fill_authority is False
    assert page.financial_authority is False


def test_public_trade_feed_uses_offset_pagination_not_cursor(monkeypatch):
    seen = {}

    async def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(
            200,
            json=[],
            headers={"Content-Type": "application/json", "Content-Encoding": "identity"},
            request=request,
        )

    monkeypatch.setattr(time, "time", lambda: NOW)
    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport)
    client = PublicWeatherDataAPITradeFeedClient(http=http)
    try:
        page = asyncio.run(client.page(condition_ids=(CONDITION,), offset=200, limit=200))
    finally:
        asyncio.run(http.aclose())

    assert seen["market"] == CONDITION
    assert seen["takerOnly"] == "true"
    assert seen["offset"] == "200"
    assert seen["limit"] == "200"
    assert "cursor" not in seen
    assert "condition" not in seen
    assert page.requested_offset == 200
    assert page.next_offset is None
    assert page.trades == ()


def test_full_page_exposes_bounded_next_offset():
    rows = [
        _row(
            transactionHash="0x" + f"{index + 10:064x}",
            proxyWallet="0x" + f"{index + 10:040x}",
        )
        for index in range(2)
    ]
    page = parse_public_data_trade_page(
        rows,
        condition_ids=(CONDITION,),
        requested_offset=100,
        requested_limit=2,
        fetched_at=NOW,
    )
    assert page.next_offset == 102
    assert len({trade.trade_id for trade in page.trades}) == 2


def test_duplicate_public_row_fails_closed():
    row = _row()
    with pytest.raises(WeatherTradeFeedError) as exc:
        parse_public_data_trade_page(
            [row, dict(row)],
            condition_ids=(CONDITION,),
            requested_offset=0,
            requested_limit=10,
            fetched_at=NOW,
        )
    assert exc.value.code == "TRADE_FEED_SURROGATE_ID_COLLISION"


def test_wrong_market_row_fails_closed():
    with pytest.raises(WeatherTradeFeedError) as exc:
        parse_public_data_trade_page(
            [_row(conditionId="0x" + "9" * 64)],
            condition_ids=(CONDITION,),
            requested_offset=0,
            requested_limit=10,
            fetched_at=NOW,
        )
    assert exc.value.code == "TRADE_FEED_CONDITION_IDENTITY_MISMATCH"


def test_future_trade_timestamp_fails_closed():
    with pytest.raises(WeatherTradeFeedError) as exc:
        parse_public_data_trade_page(
            [_row(timestamp=int(NOW + 1))],
            condition_ids=(CONDITION,),
            requested_offset=0,
            requested_limit=10,
            fetched_at=NOW,
        )
    assert exc.value.code == "TRADE_FEED_TIMESTAMP_INVALID"


def test_response_larger_than_requested_limit_fails_closed():
    with pytest.raises(WeatherTradeFeedError) as exc:
        parse_public_data_trade_page(
            [_row(), _row(transactionHash="0x" + "4" * 64)],
            condition_ids=(CONDITION,),
            requested_offset=0,
            requested_limit=1,
            fetched_at=NOW,
        )
    assert exc.value.code == "TRADE_FEED_PAGE_EXCEEDS_LIMIT"
