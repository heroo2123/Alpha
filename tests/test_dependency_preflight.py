import asyncio

import httpx

from polymarket_scanner.dependency_preflight import (
    PREFLIGHT_VERSION,
    ProbeResult,
    run_dependency_preflight,
    summarize_preflight,
)


class FakeWS:
    async def close(self):
        return None


def test_summary_fails_only_for_required_dependencies():
    summary = summarize_preflight([
        ProbeResult("required-ok", "a", True, True, "ok"),
        ProbeResult("optional-bad", "b", False, False, "down"),
    ])
    assert summary["version"] == PREFLIGHT_VERSION
    assert summary["ok"] is True
    assert summary["required_failed"] == []
    assert summary["optional_failed"] == ["optional-bad"]

    failed = summarize_preflight([
        ProbeResult("required-bad", "a", True, False, "down"),
    ])
    assert failed["ok"] is False
    assert failed["required_failed"] == ["required-bad"]


def test_preflight_required_endpoints_and_optional_failures_are_separated():
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "gamma-api.polymarket.com":
            return httpx.Response(200, json=[])
        if host == "clob.polymarket.com":
            return httpx.Response(200, text="123")
        if host == "api.telegram.org":
            # Host reachability only: authentication is deliberately not attempted.
            return httpx.Response(404, text="not found")
        if host == "aviationweather.gov":
            return httpx.Response(503, text="research feed down")
        return httpx.Response(500)

    async def fake_ws(endpoint: str, **_kwargs):
        if "sports-api" in endpoint or "ws-live-data" in endpoint:
            raise OSError("optional websocket unavailable")
        return FakeWS()

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await run_dependency_preflight(client=client, ws_connect=fake_ws)

    summary = asyncio.run(run())
    assert summary["ok"] is True
    assert summary["required_failed"] == []
    assert sorted(summary["optional_failed"]) == [
        "aviationweather_metar_proxy",
        "polymarket_rtds_ws",
        "polymarket_sports_ws",
    ]


def test_required_gamma_or_market_ws_failure_fails_preflight():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "gamma-api.polymarket.com":
            return httpx.Response(503)
        if request.url.host == "clob.polymarket.com":
            return httpx.Response(200)
        if request.url.host == "api.telegram.org":
            return httpx.Response(404)
        return httpx.Response(200)

    async def fake_ws(endpoint: str, **_kwargs):
        if "ws-subscriptions-clob" in endpoint:
            raise OSError("market websocket blocked")
        return FakeWS()

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await run_dependency_preflight(
                include_optional=False,
                client=client,
                ws_connect=fake_ws,
            )

    summary = asyncio.run(run())
    assert summary["ok"] is False
    assert sorted(summary["required_failed"]) == [
        "polymarket_gamma",
        "polymarket_market_ws",
    ]
    assert summary["optional_total"] == 0
