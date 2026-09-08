import asyncio
import json
import stat

import httpx
import pytest

from polymarket_scanner.dependency_preflight import (
    PREFLIGHT_VERSION,
    ProbeResult,
    bind_release_sha,
    run_dependency_preflight,
    summarize_preflight,
    write_preflight_evidence,
)


class FakeWS:
    async def close(self):
        return None


def test_summary_fails_only_for_required_dependencies():
    summary = summarize_preflight([
        ProbeResult("required-ok", "a", True, True, "ok", 12.5),
        ProbeResult("optional-bad", "b", False, False, "down", 9.0),
    ])
    assert summary["version"] == PREFLIGHT_VERSION
    assert summary["ok"] is True
    assert summary["required_failed"] == []
    assert summary["optional_failed"] == ["optional-bad"]
    assert summary["required_max_elapsed_ms"] == 12.5
    assert summary["optional_max_elapsed_ms"] == 9.0

    failed = summarize_preflight([
        ProbeResult("required-bad", "a", True, False, "down", 3.0),
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
    assert summary["measured_at"].endswith("+00:00")
    assert all(row["elapsed_ms"] is not None and row["elapsed_ms"] >= 0 for row in summary["results"])


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


def test_release_binding_requires_exact_40_hex_sha():
    sha = "a" * 40
    bound = bind_release_sha({"ok": True}, sha.upper())
    assert bound["release_sha"] == sha
    assert bind_release_sha({"ok": True}, None)["release_sha"] is None
    for bad in ("main", "a" * 39, "g" * 40, ""):
        with pytest.raises(ValueError):
            bind_release_sha({"ok": True}, bad)


def test_preflight_evidence_is_atomic_json_and_owner_only(tmp_path):
    path = tmp_path / "state" / "dependency-preflight.json"
    summary = bind_release_sha(
        {
            "version": PREFLIGHT_VERSION,
            "ok": True,
            "measured_at": "2026-09-08T09:12:00+00:00",
            "required_failed": [],
            "results": [],
        },
        "b" * 40,
    )
    written = write_preflight_evidence(summary, path)
    assert written == path
    assert json.loads(path.read_text()) == summary
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert not list(path.parent.glob(f".{path.name}.tmp-*"))
