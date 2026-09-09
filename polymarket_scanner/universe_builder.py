"""Single-process, streaming Gamma builder. No feeds, CLOB calls, or Telegram."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import fields
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import time

import httpx

from .models import Market
from .polymarket import GAMMA, PolymarketClient, _compact_event_payload
from .production_universe import (
    PRODUCTION_UNIVERSE_FILTER_VERSION, _flag, _neg_risk_precertifiable_parent,
    detector_filter_reasons,
)
from .universe_snapshot import (
    BUILD_DEADLINE_SECONDS, BUILD_INTERVAL_SECONDS, SnapshotError, SnapshotWriter,
    atomic_json, builder_lock, snapshot_directory,
)
from .runtime_resources import runtime_resource_snapshot

log = logging.getLogger("polybot.universe_builder")
MAX_PAGE_BYTES = 16 * 1024 * 1024


def release_sha() -> str:
    root = Path(__file__).resolve().parents[1]
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, timeout=3).strip()


async def fetch_page(client: httpx.AsyncClient, cursor: str | None) -> tuple[list[dict], str | None, float, str]:
    params = {"active": "true", "closed": "false", "limit": 100}
    if cursor is not None:
        params["after_cursor"] = cursor
    # One request at a time. A failed attempt abandons this generation; the next
    # scheduled build retries from the beginning, with the prior generation intact.
    async with client.stream("GET", f"{GAMMA}/events/keyset", params=params) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes():
            body.extend(chunk)
            if len(body) > MAX_PAGE_BYTES:
                raise SnapshotError("Gamma page exceeds decompressed byte bound")
    receipt = time.time()
    payload = json.loads(body)
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise SnapshotError("malformed Gamma keyset envelope/events")
    events = payload["events"]
    if len(events) > 100:
        raise SnapshotError("Gamma page exceeds requested event bound")
    # Official gamma-openapi.yaml, KeysetEventsResponse: the terminal short
    # page OMITs next_cursor. A full page must carry continuation; treating its
    # missing/null cursor as exhaustion would silently truncate discovery.
    next_cursor = payload.get("next_cursor")
    if next_cursor is None and len(events) == 100:
        raise SnapshotError("full Gamma page missing continuation evidence")
    if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor.strip() or len(next_cursor) > 4096):
        raise SnapshotError("malformed Gamma continuation cursor")
    if next_cursor is not None and not events:
        raise SnapshotError("empty Gamma page with continuation")
    return events, next_cursor, receipt, hashlib.sha256(body).hexdigest()


def append_events(writer: SnapshotWriter, events: list[dict], receipt: float) -> None:
    # The existing materializer is a pure transformation. Never instantiate its
    # network client or retain a full-universe list in this process.
    materializer = object.__new__(PolymarketClient)
    for event in events:
        if not isinstance(event, dict) or not str(event.get("id") or "").strip() or not isinstance(event.get("markets"), list):
            raise SnapshotError("malformed Gamma event or missing child inventory")
        event_id = str(event["id"])
        neg_parent = _neg_risk_precertifiable_parent(event)
        selected = []
        for raw in event["markets"]:
            if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
                raise SnapshotError("malformed Gamma child market")
            if any(not isinstance(raw.get(key), bool) for key in ("active", "closed")):
                raise SnapshotError("missing or ambiguous Gamma child open state")
            active = _flag(raw["active"]) and not _flag(raw["closed"])
            reasons = (["neg_risk_shape_unverified"] if neg_parent else detector_filter_reasons(event, raw)) if active else []
            eligible = bool(reasons)
            # Price changes are permitted across duplicate observations; conflicting
            # contract identity, rules, membership or lifecycle abort the traversal.
            identity = {k: raw.get(k) for k in (
                "conditionId", "clobTokenIds", "outcomes", "question", "description", "resolutionSource",
                "endDate", "active", "closed", "enableOrderBook", "acceptingOrders", "negRiskMarketID",
            )}
            if writer.inventory(str(raw["id"]), event_id, identity, active=active, selected=eligible) and eligible:
                selected.append(dict(raw, _filter_reasons=reasons))
        if not selected:
            continue
        subset_event = dict(event, markets=selected)
        materialized: list[Market] = []
        materializer._append_events(materialized, [subset_event])
        if len(materialized) != len(selected):
            raise SnapshotError("eligible market could not be materialized losslessly")
        # NEVER use the filtered event as parent exhaustiveness evidence.
        original_parent = _compact_event_payload(event)
        selected_by_id = {str(raw["id"]): raw for raw in selected}
        for market in materialized:
            market.raw["_event"] = original_parent
            market.raw["_gamma_received_at"] = receipt
            market.raw["_gamma_updated_at"] = selected_by_id[market.id].get("updatedAt")
            market.raw["_filter_reasons"] = selected_by_id[market.id]["_filter_reasons"]
            writer.market({field.name: getattr(market, field.name) for field in fields(Market)}, original_parent)


def builder_status(directory: Path, writer: SnapshotWriter, state: str, **extra) -> None:
    resources = runtime_resource_snapshot(disk_path=directory)
    atomic_json(directory / "builder-status.json", {
        "state": state, "build_id": writer.manifest["generation_id"], "pid": os.getpid(),
        "producer_sha": writer.manifest["producer_sha"], "updated_at": time.time(),
        "started_at": writer.manifest["started_at"], "elapsed_seconds": time.time() - writer.manifest["started_at"],
        "keyset_pages": writer.pages, "discovered_market_count": writer.discovered,
        "materialized_market_count": writer.materialized, **extra,
        "process_rss_bytes": resources["process_rss_bytes"],
        "process_swap_bytes": resources["process_swap_bytes"],
        "disk_free_bytes": resources["disk_free_bytes"],
    })


async def build_once(directory: Path, client: httpx.AsyncClient, *, producer_sha: str) -> dict:
    """Caller owns builder_lock for this operation and any subsequent cadence."""
    writer = SnapshotWriter(directory, producer_sha=producer_sha, filter_version=PRODUCTION_UNIVERSE_FILTER_VERSION)
    started = time.monotonic()
    page_times: list[float] = []  # <=5000 scalars, not the universe.
    try:
        builder_status(directory, writer, "BUILDING")
        async with asyncio.timeout(BUILD_DEADLINE_SECONDS):
            cursor = None
            while True:
                page_started = time.monotonic()
                events, next_cursor, receipt, digest = await fetch_page(client, cursor)
                request_seconds = time.monotonic() - page_started
                append_events(writer, events, receipt)
                writer.page(cursor_in=cursor, cursor_out=next_cursor, receipt=receipt,
                            seconds=request_seconds, response_sha256=digest, events=len(events))
                page_times.append(request_seconds)
                if time.monotonic() - started >= BUILD_DEADLINE_SECONDS:
                    raise SnapshotError("Gamma build deadline exceeded")
                builder_status(directory, writer, "BUILDING", page_average_seconds=sum(page_times) / len(page_times),
                               page_max_seconds=max(page_times), slow_pages=sum(t >= 2 for t in page_times))
                if next_cursor is None:
                    break
                cursor = next_cursor
                await asyncio.sleep(0)  # Permit deadline/cancellation between bounded pages.
            result = writer.publish()
            builder_status(directory, writer, "PUBLISHED", accepted_generation=result["generation_id"])
            return result
    except BaseException as exc:
        # Never log response bodies, request URLs with queries, or arbitrary HTTP
        # exception representations. Diagnostic category is enough for containment.
        builder_status(directory, writer, "FAILED", error_type=type(exc).__name__)
        raise
    finally:
        writer.abort()


async def run(directory: Path, *, once: bool, ipv6: bool) -> None:
    with builder_lock(directory):
        # A dead builder cannot hold flock. Only its successor may remove private
        # abandoned files; accepted immutable generations remain untouched.
        for path in directory.glob("g-*.sqlite.building*"):
            path.unlink(missing_ok=True)
        from .universe_snapshot import current_pointer
        pointer = current_pointer(directory)
        # Unpublished files left by power loss do not become authority on restart.
        if pointer:
            for path in directory.glob("g-*.sqlite"):
                if path.name > pointer["file"] and path.name != pointer["file"]:
                    path.unlink(missing_ok=True)
        else:
            for path in directory.glob("g-*.sqlite"):
                path.unlink(missing_ok=True)
        transport = httpx.AsyncHTTPTransport(local_address="::" if ipv6 else None,
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1), retries=0)
        async with httpx.AsyncClient(transport=transport, timeout=httpx.Timeout(20.0),
                                    headers={"User-Agent": "Alpha-silent-shadow-universe/1"}) as client:
            while True:
                start = time.monotonic()
                try:
                    manifest = await build_once(directory, client, producer_sha=release_sha())
                    log.info("universe published: %s markets=%s subset=%s seconds=%.3f", manifest["generation_id"],
                             manifest["discovered_market_count"], manifest["materialized_market_count"], manifest["build_seconds"])
                except Exception as exc:
                    log.error("universe build failed: %s; previous generation retained", type(exc).__name__)
                    if once:
                        raise
                if once:
                    return
                # No overlap, backlog, or burst of catch-up builds after an overrun.
                next_start = max(time.monotonic() + 60.0, start + BUILD_INTERVAL_SECONDS)
                while time.monotonic() < next_start:
                    await asyncio.sleep(min(30.0, next_start - time.monotonic()))
                    from .universe_snapshot import read_json
                    status = read_json(directory / "builder-status.json")
                    status["heartbeat_at"] = time.time()
                    atomic_json(directory / "builder-status.json", status)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--ipv6", action="store_true", help="bind native IPv6 on the production VM")
    parser.add_argument("--directory", type=Path, default=snapshot_directory())
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(args.directory, once=args.once, ipv6=args.ipv6))


if __name__ == "__main__":
    main()
