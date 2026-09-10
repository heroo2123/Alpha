"""Single-process, streaming Gamma builder. No feeds, CLOB calls, or Telegram."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import fields
from functools import cache
import hashlib
import json
import logging
import os
from pathlib import Path
import subprocess
import time
import uuid

import httpx

from .models import Market
from .polymarket import GAMMA, PolymarketClient, _compact_event_payload
from .production_universe import (
    PRODUCTION_UNIVERSE_FILTER_VERSION, _flag, _neg_risk_precertifiable_parent,
    detector_filter_reasons,
)
from .universe_snapshot import (
    BUILD_DEADLINE_SECONDS, BUILD_INTERVAL_SECONDS, SnapshotError, SnapshotWriter,
    GAMMA_PAGE_SIZE, atomic_json, builder_lock, read_json, snapshot_directory,
)
from .runtime_resources import runtime_resource_snapshot
from .universe_failures import failure_record
from .builder_runtime import PinnedProducer, cgroup_diagnostics

log = logging.getLogger("polybot.universe_builder")
MAX_PAGE_BYTES = 16 * 1024 * 1024
MIN_REQUEST_INTERVAL_SECONDS = 0.05  # <=20 requests/s, one request in flight.
_MATERIALIZER = object.__new__(PolymarketClient)
_MARKET_FIELDS = tuple(field.name for field in fields(Market))


@cache
def release_sha() -> str:
    root = Path(__file__).resolve().parents[1]
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True, timeout=3).strip()


async def fetch_page(client: httpx.AsyncClient, cursor: str | None, *, diagnostics: dict | None = None) -> tuple[list[dict], str | None, float, str]:
    params = {"active": "true", "closed": "false", "limit": GAMMA_PAGE_SIZE}
    if cursor is not None:
        params["after_cursor"] = cursor
    # One request at a time. A failed attempt abandons this generation; the next
    # scheduled build retries from the beginning, with the prior generation intact.
    network_started = time.monotonic()
    async with client.stream("GET", f"{GAMMA}/events/keyset", params=params) as response:
        response.raise_for_status()
        body = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
            observed = len(body) + len(chunk)
            if observed > MAX_PAGE_BYTES:
                raise SnapshotError("PAGE_BYTES_CAP", observed_bytes=observed, limit_bytes=MAX_PAGE_BYTES)
            body.extend(chunk)
    receipt = time.time()
    network_seconds = time.monotonic() - network_started
    decode_started = time.monotonic()
    size = len(body)
    digest = hashlib.sha256(body).hexdigest()
    payload = json.loads(body)
    del body  # Do not retain raw response buffers alongside the decoded graph.
    if diagnostics is not None:
        diagnostics.update(page_bytes=size, network_seconds=network_seconds,
                           json_seconds=time.monotonic() - decode_started)
    if not isinstance(payload, dict) or not isinstance(payload.get("events"), list):
        raise SnapshotError("SOURCE_ENVELOPE")
    events = payload["events"]
    if len(events) > GAMMA_PAGE_SIZE:
        raise SnapshotError("SOURCE_PAGE_COUNT")
    # Official gamma-openapi.yaml, KeysetEventsResponse: the terminal short
    # page OMITs next_cursor. A full page must carry continuation; treating its
    # missing/null cursor as exhaustion would silently truncate discovery.
    next_cursor = payload.get("next_cursor")
    if next_cursor is None and len(events) == GAMMA_PAGE_SIZE:
        raise SnapshotError("CURSOR_MISSING")
    if next_cursor is not None and (not isinstance(next_cursor, str) or not next_cursor.strip() or len(next_cursor) > 4096):
        raise SnapshotError("CURSOR_INVALID")
    if next_cursor is not None and not events:
        raise SnapshotError("CURSOR_EMPTY_PAGE")
    return events, next_cursor, receipt, digest


def append_events(writer: SnapshotWriter, events: list[dict], receipt: float) -> None:
    # The existing materializer is a pure transformation. Never instantiate its
    # network client or retain a full-universe list in this process.
    for event in events:
        if not isinstance(event, dict) or not str(event.get("id") or "").strip() or not isinstance(event.get("markets"), list):
            raise SnapshotError("SOURCE_EVENT")
        event_id = str(event["id"])
        neg_parent = _neg_risk_precertifiable_parent(event)
        selected, selected_reasons = [], {}
        for raw in event["markets"]:
            if not isinstance(raw, dict) or not str(raw.get("id") or "").strip():
                raise SnapshotError("SOURCE_MARKET")
            if any(not isinstance(raw.get(key), bool) for key in ("active", "closed")):
                raise SnapshotError("SOURCE_OPEN_STATE")
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
                selected.append(raw)  # References, not copies of every Gamma field.
                selected_reasons[str(raw["id"])] = reasons
        if not selected:
            continue
        subset_event = dict(event, markets=selected)
        # NEVER use filtered membership as parent exhaustiveness evidence.
        original_parent = _compact_event_payload(event)
        writer.event(event_id, original_parent)
        materialized: list[Market] = []
        _MATERIALIZER._append_events(materialized, [subset_event], original_parent=original_parent)
        if len(materialized) != len(selected):
            raise SnapshotError("MATERIALIZATION_MISMATCH")
        selected_by_id = {str(raw["id"]): raw for raw in selected}
        for market in materialized:
            market.raw["_event"] = original_parent
            market.raw["_gamma_received_at"] = receipt
            market.raw["_gamma_updated_at"] = selected_by_id[market.id].get("updatedAt")
            market.raw["_filter_reasons"] = selected_reasons[market.id]
            writer.market({name: getattr(market, name) for name in _MARKET_FIELDS})


def builder_status(directory: Path, writer: SnapshotWriter | None, state: str, *, attempt: dict, **extra) -> dict:
    resources = runtime_resource_snapshot(disk_path=directory)
    generation_path = (writer.path if writer.path.exists() else directory / writer.name) if writer else None
    value = {
        "version": "builder_diagnostics_v2_closed_failure_codes", "state": state, **attempt,
        "build_id": writer.manifest["generation_id"] if writer else attempt["attempt_id"], "pid": os.getpid(),
        "updated_at": time.time(), "elapsed_seconds": time.time() - attempt["started_at"],
        "keyset_pages": writer.pages if writer else 0,
        "discovered_market_count": writer.discovered if writer else 0,
        "materialized_market_count": writer.materialized if writer else 0,
        "inventory_market_count": writer.inventory_count if writer else 0,
        "generation_file_bytes": generation_path.stat().st_size if generation_path and generation_path.exists() else None,
        "decoded_payload_bytes": writer.decoded_bytes if writer else 0,
        "parent_payload_bytes": writer.parent_bytes if writer else 0,
        "market_payload_bytes": writer.market_bytes if writer else 0,
        "max_parent_bytes": writer.max_parent_bytes if writer else 0,
        "max_market_bytes": writer.max_market_bytes if writer else 0, **extra,
        "process_rss_bytes": resources["process_rss_bytes"],
        "process_swap_bytes": resources["process_swap_bytes"],
        "disk_free_bytes": resources["disk_free_bytes"],
        "process_cpu_seconds": time.process_time(), "cgroup": cgroup_diagnostics(),
    }
    atomic_json(directory / "builder-status.json", value)
    return value


def record_failure(directory: Path, writer, attempt: dict, phase: str, exc: BaseException, **metrics) -> None:
    failure = {**failure_record(exc), "failed_stage": phase, **metrics}
    # This journal fallback remains useful if ENOSPC prevents status persistence.
    log.error("universe_failure %s", json.dumps({"attempt_id": attempt["attempt_id"], **failure}, sort_keys=True))
    try:
        value = builder_status(directory, writer, "FAILED", attempt=attempt, **failure)
        try:
            history = read_json(directory / "builder-failures.json")
        except (OSError, ValueError, SnapshotError):
            history = {"total": 0, "failures": []}
        # Bounded across failed attempts AND daemon restarts. Never overwrite an
        # earlier failure with a later status from a different build.
        entry = {key: value.get(key) for key in ("attempt_id", "build_id", "producer_sha", "updated_at",
                 "elapsed_seconds", "keyset_pages", "discovered_market_count", "materialized_market_count",
                 "generation_file_bytes", "max_parent_bytes", "max_market_bytes", "failure_code", "failure_metrics", "failed_stage",
                 "max_page_bytes", "decoded_payload_bytes", "network_total_seconds", "json_total_seconds",
                 "transform_total_seconds", "process_rss_bytes", "process_swap_bytes", "process_cpu_seconds", "cgroup")}
        atomic_json(directory / "builder-failures.json", {"total": int(history["total"]) + 1,
                    "failures": [*history["failures"][-15:], entry]})
    except Exception as diagnostic_error:
        log.error("universe_diagnostics_write_failure %s", json.dumps(failure_record(diagnostic_error)))


async def build_once(directory: Path, client: httpx.AsyncClient, *, producer_sha: str, release_check=None) -> dict:
    """Caller owns builder_lock for this operation and any subsequent cadence."""
    writer = None
    attempt = {"attempt_id": uuid.uuid4().hex, "producer_sha": producer_sha, "started_at": time.time()}
    phase = "INITIALIZING"
    started = time.monotonic()
    last_status = started
    diagnostics = {"max_page_bytes": 0, "network_total_seconds": 0., "json_total_seconds": 0.,
                   "transform_total_seconds": 0., "page_max_seconds": 0., "slow_pages": 0}
    try:
        if release_check is not None:
            release_check()
        writer = SnapshotWriter(directory, producer_sha=producer_sha, filter_version=PRODUCTION_UNIVERSE_FILTER_VERSION)
        builder_status(directory, writer, "BUILDING", attempt=attempt, phase=phase, **diagnostics)
        async with asyncio.timeout(BUILD_DEADLINE_SECONDS):
            cursor = None
            while True:
                page_started = time.monotonic()
                phase = "FETCH_AND_PARSE"
                page_info = {}
                events, next_cursor, receipt, digest = await fetch_page(client, cursor, diagnostics=page_info)
                request_seconds = time.monotonic() - page_started
                diagnostics["max_page_bytes"] = max(diagnostics["max_page_bytes"], page_info.get("page_bytes", 0))
                diagnostics["network_total_seconds"] += page_info.get("network_seconds", request_seconds)
                diagnostics["json_total_seconds"] += page_info.get("json_seconds", 0.)
                diagnostics["page_max_seconds"] = max(diagnostics["page_max_seconds"], request_seconds)
                diagnostics["slow_pages"] += int(request_seconds >= 2.)
                phase = "MATERIALIZE"
                transform_started = time.monotonic()
                append_events(writer, events, receipt)
                event_count = len(events)
                del events  # Critical: release this graph BEFORE fetching the next.
                phase = "PAGE_COMMIT"
                writer.page(cursor_in=cursor, cursor_out=next_cursor, receipt=receipt,
                            seconds=request_seconds, response_sha256=digest, events=event_count)
                diagnostics["transform_total_seconds"] += time.monotonic() - transform_started
                diagnostics["page_average_seconds"] = (diagnostics["network_total_seconds"] + diagnostics["json_total_seconds"]) / writer.pages
                if time.monotonic() - started >= BUILD_DEADLINE_SECONDS:
                    raise SnapshotError("BUILD_DEADLINE")
                # Status itself needs fsync. Bound its frequency instead of issuing
                # thousands of synchronous metadata writes on the VM's disk.
                if time.monotonic() - last_status >= 5:
                    builder_status(directory, writer, "BUILDING", attempt=attempt, phase=phase, **diagnostics)
                    last_status = time.monotonic()
                if next_cursor is None:
                    break
                cursor = next_cursor
                await asyncio.sleep(max(0., MIN_REQUEST_INTERVAL_SECONDS - (time.monotonic() - page_started)))
            phase = "PUBLISH"
            result = writer.publish(before_publish=release_check)
            builder_status(directory, writer, "PUBLISHED", attempt=attempt, phase="IDLE",
                           accepted_generation=result["generation_id"], **diagnostics)
            return result
    except BaseException as exc:
        record_failure(directory, writer, attempt, phase, exc, **diagnostics)
        raise
    finally:
        if writer is not None:
            writer.abort()


async def run(directory: Path, *, once: bool, ipv6: bool) -> None:
    with builder_lock(directory):
        # Attest once per immutable process, not once per generation. Later cheap
        # checks detect source/marker changes without scheduling a git subprocess.
        try:
            producer = PinnedProducer.capture()
        except BaseException as exc:
            record_failure(directory, None, {"attempt_id": uuid.uuid4().hex, "producer_sha": None,
                           "started_at": time.time()}, "RELEASE_STARTUP", exc)
            raise
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
                    manifest = await build_once(directory, client, producer_sha=producer.sha, release_check=producer.check)
                    log.info("universe published: %s markets=%s subset=%s seconds=%.3f", manifest["generation_id"],
                             manifest["discovered_market_count"], manifest["materialized_market_count"], manifest["build_seconds"])
                except Exception:
                    if once:
                        raise
                if once:
                    return
                # No overlap, backlog, or burst of catch-up builds after an overrun.
                next_start = max(time.monotonic() + 60.0, start + BUILD_INTERVAL_SECONDS)
                while time.monotonic() < next_start:
                    await asyncio.sleep(min(30.0, next_start - time.monotonic()))
                    status = read_json(directory / "builder-status.json")
                    status["heartbeat_at"] = time.time()
                    atomic_json(directory / "builder-status.json", status)


def configure_logging() -> None:
    from .safe_logging import install_secret_safe_logging
    install_secret_safe_logging()
    logging.basicConfig(level=logging.INFO)
    # HTTP access/debug messages can contain opaque query cursors or remote
    # exception text. Only our closed diagnostics belong in this daemon's logs.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).disabled = True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--ipv6", action="store_true", help="bind native IPv6 on the production VM")
    parser.add_argument("--directory", type=Path, default=snapshot_directory())
    args = parser.parse_args()
    configure_logging()
    try:
        asyncio.run(run(args.directory, once=args.once, ipv6=args.ipv6))
    except BaseException as exc:
        log.error("universe_exit %s", json.dumps(failure_record(exc)))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
