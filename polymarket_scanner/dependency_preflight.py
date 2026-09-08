from __future__ import annotations

"""Network dependency preflight for an authorized production/shadow host.

This module does not prove reachability merely by existing in CI. It is designed to
be executed on the actual VM before a shadow/release run. Required trading
infrastructure and optional research feeds are reported separately so an optional
adapter cannot make the core scanner look healthy or unhealthy by accident.

Every live probe records elapsed time and the CLI can atomically persist a secret-free
JSON evidence file. Deployment callers may bind that evidence to the exact immutable
release SHA. That evidence is useful for release/shadow attestation, but it is still
only evidence for the host and time at which the command actually ran.
"""

import argparse
import asyncio
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable

import httpx
import websockets

from .polymarket import CLOB, GAMMA
from .streams import MARKET_WS, RTDS_WS, SPORTS_WS
from .weather import AWC

PREFLIGHT_VERSION = "dependency_preflight_v5_gamma_keyset_continuation_release_bound"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True, slots=True)
class ProbeResult:
    name: str
    endpoint: str
    required: bool
    ok: bool
    detail: str
    elapsed_ms: float | None = None


def summarize_preflight(results: list[ProbeResult]) -> dict:
    required = [row for row in results if row.required]
    optional = [row for row in results if not row.required]
    required_failed = [row.name for row in required if not row.ok]
    optional_failed = [row.name for row in optional if not row.ok]
    required_latencies = [
        float(row.elapsed_ms)
        for row in required
        if row.elapsed_ms is not None and row.elapsed_ms >= 0
    ]
    optional_latencies = [
        float(row.elapsed_ms)
        for row in optional
        if row.elapsed_ms is not None and row.elapsed_ms >= 0
    ]
    return {
        "version": PREFLIGHT_VERSION,
        "ok": not required_failed,
        "required_total": len(required),
        "required_failed": required_failed,
        "required_max_elapsed_ms": max(required_latencies) if required_latencies else None,
        "optional_total": len(optional),
        "optional_failed": optional_failed,
        "optional_max_elapsed_ms": max(optional_latencies) if optional_latencies else None,
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
    started = time.perf_counter()
    try:
        response = await client.get(endpoint)
        status = int(response.status_code)
        ok = 200 <= status < 300 if require_2xx else 100 <= status < 500
        detail = f"HTTP {status}"
    except Exception as exc:
        ok = False
        detail = f"{type(exc).__name__}: {exc}"
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return ProbeResult(name, endpoint, required, ok, detail, elapsed_ms)


def _validate_gamma_keyset_payload(payload: object, *, label: str) -> tuple[list, str | None]:
    if not isinstance(payload, dict):
        raise ValueError(f"{label} keyset envelope is not an object")
    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError(f"{label} keyset envelope missing events list")
    cursor = payload.get("next_cursor")
    if cursor is not None and not isinstance(cursor, str):
        raise ValueError(f"{label} next_cursor malformed")
    normalized = cursor.strip() if isinstance(cursor, str) else ""
    next_cursor = normalized or None
    if next_cursor is not None and not events:
        raise ValueError(f"{label} keyset returned empty events with continuation cursor")
    return events, next_cursor


async def _gamma_keyset_probe(client: httpx.AsyncClient) -> ProbeResult:
    """Verify the real two-page Gamma keyset contract used for full-universe discovery."""
    endpoint = f"{GAMMA}/events/keyset?limit=1&active=true&closed=false"
    started = time.perf_counter()
    ok = False
    detail = "uninitialized"
    try:
        first = await client.get(endpoint)
        first_status = int(first.status_code)
        first.raise_for_status()
        _events, cursor = _validate_gamma_keyset_payload(first.json(), label="first")
        if cursor is None:
            detail = f"HTTP {first_status}; keyset continuation cursor missing"
        else:
            second = await client.get(
                f"{GAMMA}/events/keyset",
                params={
                    "limit": 1,
                    "active": "true",
                    "closed": "false",
                    "after_cursor": cursor,
                },
            )
            second_status = int(second.status_code)
            second.raise_for_status()
            _second_events, second_cursor = _validate_gamma_keyset_payload(
                second.json(), label="continuation"
            )
            if second_cursor == cursor:
                detail = (
                    f"HTTP {first_status}/{second_status}; keyset continuation repeated cursor"
                )
            else:
                ok = True
                detail = (
                    f"HTTP {first_status}/{second_status}; Gamma keyset continuation contract valid"
                )
    except Exception as exc:
        detail = f"{type(exc).__name__}: {exc}"
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return ProbeResult("polymarket_gamma", endpoint, True, ok, detail, elapsed_ms)


async def _ws_probe(
    name: str,
    endpoint: str,
    *,
    required: bool,
    connect: Callable[..., Awaitable] = websockets.connect,
) -> ProbeResult:
    started = time.perf_counter()
    try:
        ws = await connect(endpoint, open_timeout=6, close_timeout=2)
        try:
            await ws.close()
        finally:
            pass
        ok = True
        detail = "WebSocket handshake succeeded"
    except Exception as exc:
        ok = False
        detail = f"{type(exc).__name__}: {exc}"
    elapsed_ms = round((time.perf_counter() - started) * 1000.0, 3)
    return ProbeResult(name, endpoint, required, ok, detail, elapsed_ms)


async def run_dependency_preflight(
    *,
    include_optional: bool = True,
    client: httpx.AsyncClient | None = None,
    ws_connect: Callable[..., Awaitable] = websockets.connect,
) -> dict:
    owned = client is None
    http = client or httpx.AsyncClient(timeout=6.0, headers={"User-Agent": "polymarket-edge-scanner-preflight/5"})
    try:
        tasks = [
            _gamma_keyset_probe(http),
            _http_probe(http, "polymarket_clob", f"{CLOB}/time", required=True),
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
        summary = summarize_preflight(results)
        summary["measured_at"] = datetime.now(timezone.utc).isoformat()
        return summary
    finally:
        if owned:
            await http.aclose()


def bind_release_sha(summary: dict, release_sha: str | None) -> dict:
    bound = dict(summary)
    if release_sha is None:
        bound["release_sha"] = None
        return bound
    normalized = str(release_sha).strip().lower()
    if not _SHA_RE.fullmatch(normalized):
        raise ValueError("release SHA must be exactly 40 hexadecimal characters")
    bound["release_sha"] = normalized
    return bound


def write_preflight_evidence(summary: dict, output: str | Path) -> Path:
    path = Path(output).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(summary, indent=2, sort_keys=True, allow_nan=False) + "\n"
    tmp = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(path)
        os.chmod(path, 0o600)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
    return path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe Alpha production dependencies from this host")
    parser.add_argument("--required-only", action="store_true", help="skip optional research feeds")
    parser.add_argument("--output", help="atomically persist the JSON evidence to this path")
    parser.add_argument("--release-sha", help="bind persisted/printed evidence to this immutable release SHA")
    args = parser.parse_args(argv)
    summary = asyncio.run(run_dependency_preflight(include_optional=not args.required_only))
    try:
        summary = bind_release_sha(summary, args.release_sha)
    except ValueError as exc:
        parser.error(str(exc))
    rendered = json.dumps(summary, indent=2, sort_keys=True, allow_nan=False)
    print(rendered)
    if args.output:
        write_preflight_evidence(summary, args.output)
    return 0 if summary["ok"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
