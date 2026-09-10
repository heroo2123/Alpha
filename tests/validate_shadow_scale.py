"""Offline capacity gate; synthetic records, not Polymarket trading evidence.

Run separately so RSS includes neither pytest's imports nor a prior benchmark.
The child builder and parent reader overlap on the same host. This does not claim
to reproduce shared-core e2-micro scheduling or actual internet/feed latency.
"""
import argparse
import asyncio
import json
import multiprocessing
import itertools
import httpx
from pathlib import Path
import resource
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from polymarket_scanner import universe_builder as builder
from polymarket_scanner.production_universe import PRODUCTION_UNIVERSE_FILTER_VERSION
from polymarket_scanner.universe_reader import UniverseReader
from polymarket_scanner.universe_snapshot import SnapshotWriter, builder_lock, GAMMA_PAGE_SIZE, MAX_FILE_BYTES
from gamma_payload_fixture import source_events, EVENTS, INVENTORY, ACTIVE, SELECTED

SHA = "a" * 40


def raw_market(mid, selected=True):
    return {"id": str(mid), "active": True, "closed": False, "enableOrderBook": True,
            "acceptingOrders": True, "conditionId": "c" + str(mid), "outcomes": ["Yes", "No"],
            "clobTokenIds": ["y" + str(mid), "n" + str(mid)],
            "question": "Will BTC be above $100?" if selected else "Unrelated fixture?",
            "bestBid": .4, "bestAsk": .42, "description": "Synthetic fixture. " * 48,
            "resolutionSource": "fixture", "slug": "fixture-" + str(mid)}


def build_child(directory, result_queue):
    try:
        page_number = total_source_bytes = largest_page = 0
        events = source_events()
        def respond(request):
            nonlocal page_number, total_source_bytes, largest_page
            assert int(request.url.params["limit"]) == GAMMA_PAGE_SIZE
            assert request.url.params.get("after_cursor") == (str(page_number) if page_number else None)
            page = list(itertools.islice(events, GAMMA_PAGE_SIZE))
            page_number += 1
            payload = {"events": page}
            if len(page) == GAMMA_PAGE_SIZE:
                payload["next_cursor"] = str(page_number)
            body = json.dumps(payload).encode()
            total_source_bytes += len(body)
            largest_page = max(largest_page, len(body))
            return httpx.Response(200, content=body)
        async def build():
            # Exercise the REAL bounded HTTP/JSON parser, not a patched fetch_page.
            async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
                with builder_lock(Path(directory)):
                    return await builder.build_once(Path(directory), client, producer_sha=SHA)
        manifest = asyncio.run(build())
        files = list(Path(directory).glob("g-*.sqlite"))
        manifest.update(source_raw_total_bytes=total_source_bytes, source_max_page_bytes=largest_page,
                        snapshot_file_bytes=max(path.stat().st_size for path in files))
        result_queue.put({"manifest": manifest, "peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024})
    except BaseException as exc:
        result_queue.put({"error": type(exc).__name__ + ": " + str(exc)})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        with builder_lock(directory):
            writer = SnapshotWriter(directory, producer_sha=SHA, filter_version=PRODUCTION_UNIVERSE_FILTER_VERSION)
            try:
                builder.append_events(writer, [{"id": "seed", "markets": [raw_market("seed")]}], time.time())
                writer.page(cursor_in=None, cursor_out=None, receipt=time.time(), seconds=.01, response_sha256="b" * 64, events=1)
                writer.publish()
            finally:
                writer.abort()
        reader = UniverseReader(directory, producer_sha=SHA, priority=lambda row: row.id,
                                weather_universe=lambda rows: ([], []))
        reader.accept(reader.poll())
        first_id = reader.accepted.manifest["generation_id"]
        ctx = multiprocessing.get_context("spawn")
        queue = ctx.Queue(maxsize=1)
        process = ctx.Process(target=build_child, args=(str(directory), queue))
        process.start()
        heartbeat = 0
        maximum_tick = 0
        start = time.monotonic()
        while process.is_alive():
            tick = time.monotonic()
            assert reader.universe_status()["safe_for_detection"]
            assert reader.accepted.manifest["generation_id"] == first_id
            # Exercise existing quote access while the builder consumes CPU/disk.
            reader.screening_status()
            heartbeat += 1
            maximum_tick = max(maximum_tick, time.monotonic() - tick)
            if time.monotonic() - start > 180:
                process.terminate()
                process.join(5)
                raise AssertionError("synthetic builder exceeded 180-second validation budget")
            process.join(.05)
        assert process.exitcode == 0
        result = queue.get(timeout=2)
        assert "error" not in result, result
        generation = reader.poll()
        assert generation is not None
        reader.accept(generation)
        manifest = generation.manifest
        assert manifest["keyset_pages"] == EVENTS // GAMMA_PAGE_SIZE + 1
        assert manifest["inventory_market_count"] == INVENTORY
        assert result["manifest"]["source_raw_total_bytes"] > 800 * 1024 * 1024
        assert result["manifest"]["source_max_page_bytes"] > 5.4 * 1024 * 1024
        assert result["manifest"]["snapshot_file_bytes"] < MAX_FILE_BYTES
        assert manifest["discovered_market_count"] == ACTIVE
        assert manifest["materialized_market_count"] == SELECTED
        assert len(generation.tokens) == 27000 and len(generation.priority_tokens) == 800
        assert result["peak_rss_bytes"] < 160 * 1024 * 1024, result
        reader_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
        assert reader_rss < 256 * 1024 * 1024
        assert maximum_tick < 1 and heartbeat >= 2
        result.update(scope="SYNTHETIC_OFFLINE_CAPACITY_NOT_E2_MICRO_LIVE_VALIDATION", passed=True,
                      reader_peak_rss_bytes=reader_rss, reader_heartbeats_during_build=heartbeat,
                      maximum_reader_tick_seconds=maximum_tick, tokens=27000, hot_tokens=800,
                      generation_files=len(list(directory.glob("g-*.sqlite"))))
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
