from __future__ import annotations

import asyncio
import inspect
import json

import httpx
import pytest

import polymarket_scanner.weather_only_clob as clob_module
from polymarket_scanner.weather_only_clob import WeatherCLOBClient, WeatherCLOBError


def _market_info_payload() -> dict:
    return {
        "t": [
            {"t": "yes-token", "o": "Yes"},
            {"t": "no-token", "o": "No"},
        ],
        "mos": 5,
        "mts": 0.01,
        "mbf": 0,
        "tbf": 0,
        "rfqe": False,
        "itode": False,
    }


def _book_payload(token: str) -> dict:
    return {
        "asset_id": token,
        "bids": [{"price": "0.20", "size": "10"}],
        "asks": [{"price": "0.40", "size": "10"}],
        "last_trade_price": "0.30",
        "timestamp": "1234567890",
        "hash": "hash",
    }


def test_resilience_is_inside_attested_class_source_and_keepalive_spans_w7_cadence():
    source = inspect.getsource(WeatherCLOBClient)
    assert "CLOB_TRANSIENT_MAX_ATTEMPTS" in source
    assert "CLOB_KEEPALIVE_EXPIRY_SECONDS" in source
    assert clob_module.CLOB_KEEPALIVE_EXPIRY_SECONDS > 30.0
    assert clob_module.CLOB_MAX_KEEPALIVE_CONNECTIONS == clob_module.CLOB_MAX_CONNECTIONS


def test_market_info_retries_transport_then_succeeds(monkeypatch):
    monkeypatch.setattr(clob_module, "CLOB_TRANSIENT_RETRY_DELAY_SECONDS", 0.0)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 3:
            raise httpx.ConnectError("transient", request=request)
        return httpx.Response(200, json=_market_info_payload())

    async def run():
        client = WeatherCLOBClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.market_info("condition-1")
        finally:
            await client.close()

    info = asyncio.run(run())
    assert calls == 3
    assert info.condition_id == "condition-1"
    assert info.fee_rate == 0.0


def test_books_retries_timeout_then_succeeds(monkeypatch):
    monkeypatch.setattr(clob_module, "CLOB_TRANSIENT_RETRY_DELAY_SECONDS", 0.0)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("transient", request=request)
        wanted = [row["token_id"] for row in json.loads(request.content)]
        return httpx.Response(200, json=[_book_payload(token) for token in wanted])

    async def run():
        client = WeatherCLOBClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.books(["token-1"])
        finally:
            await client.close()

    books = asyncio.run(run())
    assert calls == 2
    assert books["token-1"].best_ask == 0.40


def test_transport_retry_exhaustion_remains_fail_closed(monkeypatch):
    monkeypatch.setattr(clob_module, "CLOB_TRANSIENT_RETRY_DELAY_SECONDS", 0.0)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ConnectError("persistent", request=request)

    async def run():
        client = WeatherCLOBClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.market_info("condition-1")
        finally:
            await client.close()

    with pytest.raises(WeatherCLOBError) as raised:
        asyncio.run(run())
    assert raised.value.code == "CLOB_TRANSPORT"
    assert calls == clob_module.CLOB_TRANSIENT_MAX_ATTEMPTS


def test_http_status_is_not_retried(monkeypatch):
    monkeypatch.setattr(clob_module, "CLOB_TRANSIENT_RETRY_DELAY_SECONDS", 0.0)
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(503, json={"error": "unavailable"})

    async def run():
        client = WeatherCLOBClient()
        await client.http.aclose()
        client.http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        try:
            return await client.market_info("condition-1")
        finally:
            await client.close()

    with pytest.raises(WeatherCLOBError) as raised:
        asyncio.run(run())
    assert raised.value.code == "CLOB_HTTP_STATUS"
    assert calls == 1
