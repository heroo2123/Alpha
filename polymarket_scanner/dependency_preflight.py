from __future__ import annotations

"""Network dependency preflight for an authorized production/shadow host.

This module does not prove reachability merely by existing in CI. It is designed to
be executed on the actual VM before a shadow/release run. Required trading
infrastructure and optional research feeds are reported separately so an optional
adapter cannot make the core scanner look healthy or unhealthy by accident.
"""

import argparse
import asyncio
import json
from dataclasses import asdict, dataclass
from typing import Awaitable, Callable

import httpx
import websockets

from .polymarket import CLOB, GAMMA
from .streams import MARKET_WS, RTDS_WS, SPORTS_WS
from .weather import AWC

PREFLIGHT_VERSION = "dependency_preflight_v1"


@dataclass(frozen=True, slots=True)
class ProbeResult:
    name: str
    endpoint: str
    required: bool
    ok: bool
    detail: str


def summarize_preflight(results: list[ProbeResult]) -> dict:
    required = [row for row in results if row.required]
    optional = [row for row in results if not row.required]
    required_failed = [row.name for row in required if not row.ok]
    optional_failed = [row.name for row in optional if not row.ok]
    return {
        "version": PREFLIGHT_VERSION,
        "ok": not required_failed,
        "required_total": len(required),
        "required_failed": required_failed,
        "optional_total": len(optional),
        "optional_failed": optional_failed,
        "results": [asdict(row) for row in results],
    }


async def _http_probe(
    client: httpx.AsyncClient,
    name: str,
    endpoint: str,
    *,
    required: bool,
    require_2xx: bool = True,
) -> ProbeResult:
    try:
        response = await client.get(endpoint)
        status = int(response.status_code)
        ok = 200 <= status < 300 if require_2xx else 100 <= status < 500
        detail = f"HTTP {status}"
    except Exception as exc:
        ok = False
        detail = f"{type(exc).__name__}: {exc}"
    return ProbeResult(name, endpoint, required, ok, detail)


async def _ws_probe(
    name: str,
    endpoint: str,
    *,
    required: bool,
    connect: Callable[..., Awaitable] = websockets.connect,
) -> ProbeResult:
    try:
        ws = await connect(endpoint, open_timeout=6, close_timeout=2)
        try:
            await ws.close()
        finally:
            pass
        return ProbeResult(name, endpoint, required, True, "WebSocket handshake succeeded")
    except Exception as exc:
        return ProbeResult(name, endpoint, required, False, f"{type(exc).__name__}: {exc}")


async def run_dependency_preflight(
    *,
    include_optional: bool = True,
    client: httpx.AsyncClient | None = None,
    ws_connect: Callable[..., Awaitable] = websockets.connect,
) -> dict:
    owned = client is None
    http = client or httpx.AsyncClient(timeout=6.0, headers={"User-Agent": "polymarket-edge-scanner-preflight/1"})
    try:
        tasks = [
            _http_probe(http, "polymarket_gamma", f"{GAMMA}/markets?limit=1&active=true&closed=false", required=True),
            _http_probe(http, "polymarket_clob", f"{CLOB}/time", required=True),
            # Telegram host reachability is required for TRADE NOW delivery. No bot
            # credential is sent; any non-5xx HTTP response proves DNS/TLS/HTTP path.
            _http_probe(http, "telegram_api_host", "https://api.telegram.org", required=True, require_2xx=False),
            _ws_probe("polymarket_market_ws", MARKET_WS, required=True, connect=ws_connect),
        ]
        if include_optional:
            tasks.extend([
                _ws_probe("polymarket_sports_ws", SPORTS_WS, required=False, connect=ws_connect),
                _ws_probe("polymarket_rtds_ws", RTDS_WS, required=False, connect=ws_connect),
                _http_probe(http, "aviationweather_metar_proxy", f"{AWC}?ids=KORD&format=json&hours=1", required=False),
            ])
        results = list(await asyncio.gather(*tasks))
        return summarize_preflight(results)
    finally:
        if owned:
            await http.aclose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Alpha production dependencies from this host")
    parser.add_argument("--required-only", action="store_true", help="skip optional research feeds")
    args = parser.parse_args(argv)
    summary = asyncio.run(run_dependency_preflight(include_optional=not args.required_only))
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
