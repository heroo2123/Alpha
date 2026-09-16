from __future__ import annotations

import asyncio

import httpx

import polymarket_scanner.weather_only_trade_feed as trade_feed
from polymarket_scanner.weather_only_trade_feed import WeatherDataAPITradeFeedClient


CONDITION = "0x" + "a" * 64
TOKEN = "123456789012345678901234567890"
TX = "0x" + "b" * 64
WALLET = "0x" + "c" * 40


def test_client_receipt_clock_is_sampled_only_after_http_response(monkeypatch):
    response_returned = {"value": False}
    executed_at = 1_700_000_000
    receipt_at = 1_700_000_010.0

    def handler(request: httpx.Request) -> httpx.Response:
        response_returned["value"] = True
        return httpx.Response(200, json={
            "data": [{
                "condition_id": CONDITION,
                "token_id": TOKEN,
                "transaction_hash": TX,
                "proxy_wallet": WALLET,
                "side": "SELL",
                "size": 1.0,
                "price": 0.24,
                "timestamp": executed_at,
                "outcome": "Yes",
                "outcome_index": 0,
            }],
            "pagination": {
                "has_more": False,
                "limit": 200,
                "offset": 0,
                "next_cursor": None,
            },
        })

    def fake_time() -> float:
        # If page() samples the clock before awaiting the HTTP response, this assertion
        # fails immediately. Receipt evidence is allowed only after the response exists.
        assert response_returned["value"] is True
        return receipt_at

    monkeypatch.setattr(trade_feed.time, "time", fake_time)

    async def scenario():
        http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        client = WeatherDataAPITradeFeedClient("secret", http=http)
        try:
            return await client.page(condition_ids=(CONDITION,))
        finally:
            await http.aclose()

    page = asyncio.run(scenario())
    assert page.fetched_at == receipt_at
    assert page.trades[0].received_at == receipt_at
    assert page.trades[0].executed_at == float(executed_at)
    assert page.actual_fill_authority is False
    assert page.financial_authority is False
