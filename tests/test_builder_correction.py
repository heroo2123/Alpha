"""Regressions for the failed 1971089 live acceptance, with no live services."""
import asyncio
import json
import logging
from pathlib import Path
import sqlite3
import subprocess
import time
import weakref
import zlib

import httpx
import pytest

from polymarket_scanner import universe_builder as builder
from polymarket_scanner import universe_snapshot as snapshots
from polymarket_scanner.builder_runtime import PinnedProducer, _signature, cgroup_diagnostics
from polymarket_scanner.universe_failures import MESSAGES, SnapshotError, failure_record
from polymarket_scanner.universe_ready import ready_snapshot
from test_universe_snapshots import SHA, event, publish, raw_market, reader


def test_failure_vocabulary_cannot_include_remote_text_or_unbounded_metrics():
    with pytest.raises(ValueError, match="unknown internal"):
        SnapshotError("https://remote.invalid/secret?token=secret")
    error = SnapshotError("PAGE_BYTES_CAP", observed_bytes=20_789_112, limit_bytes=16_777_216,
                          url="secret", count=float("inf"), page=-1, children=True)
    assert failure_record(error)["failure_metrics"] == {"observed_bytes": 20_789_112, "limit_bytes": 16_777_216}
    assert all(SnapshotError(code).code == code for code in MESSAGES)


def test_builder_suppresses_transport_access_logs(monkeypatch):
    for name in ("httpx", "httpcore"):
        monkeypatch.setattr(logging.getLogger(name), "disabled", False)
    builder.configure_logging()
    assert all(logging.getLogger(name).disabled for name in ("httpx", "httpcore"))


def test_sqlite_initialization_failure_closes_and_removes_private_file(tmp_path, monkeypatch):
    class BrokenDatabase:
        closed = False
        def executescript(self, sql):
            raise sqlite3.DatabaseError("injected initialization failure")
        def close(self):
            self.closed = True
    db = BrokenDatabase()
    def connect(path):
        Path(path).touch()
        return db
    monkeypatch.setattr(snapshots.sqlite3, "connect", connect)
    with pytest.raises(sqlite3.DatabaseError):
        snapshots.SnapshotWriter(tmp_path, producer_sha=SHA, filter_version="fixture")
    assert db.closed and not list(tmp_path.glob("*.building"))


@pytest.mark.parametrize("exc,code", [
    (subprocess.TimeoutExpired(["git", "secret"], 3, output="secret"), "RELEASE_LOOKUP_TIMEOUT"),
    (httpx.ReadTimeout("secret"), "HTTP_TIMEOUT"),
    (sqlite3.DatabaseError("secret"), "SQLITE_INTEGRITY"),
    (RuntimeError("secret"), "INTERNAL_EXCEPTION"),
    (TimeoutError("secret"), "BUILD_DEADLINE"),
])
def test_failure_classification_never_serializes_exception_text(exc, code):
    record = failure_record(exc)
    assert record["failure_code"] == code
    assert "secret" not in json.dumps(record)


def test_http_failure_diagnostics_are_safe_and_preserved_across_attempts(tmp_path, caplog):
    publish(tmp_path)
    accepted = snapshots.current_pointer(tmp_path)
    async def run():
        def respond(request):
            raise httpx.HTTPStatusError("secret body", request=httpx.Request("GET", "https://secret.invalid/?token=secret"),
                                       response=httpx.Response(429))
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            for _ in range(2):
                with pytest.raises(httpx.HTTPStatusError):
                    await builder.build_once(tmp_path, client, producer_sha=SHA)
    asyncio.run(run())
    history = json.loads((tmp_path / "builder-failures.json").read_text())
    assert history["total"] == 2
    assert len({entry["attempt_id"] for entry in history["failures"]}) == 2
    assert all(entry["failure_code"] == "HTTP_STATUS" and entry["failure_metrics"] == {"http_status": 429}
               for entry in history["failures"])
    assert snapshots.current_pointer(tmp_path) == accepted
    assert "secret" not in caplog.text + (tmp_path / "builder-status.json").read_text() + json.dumps(history)


def test_constructor_failure_records_new_attempt_and_bounded_history(tmp_path, monkeypatch):
    def fail(*args, **kwargs):
        raise SnapshotError("GENERATION_FILE_CAP", observed_bytes=300_000_000, limit_bytes=snapshots.MAX_FILE_BYTES)
    monkeypatch.setattr(builder, "SnapshotWriter", fail)
    async def run():
        for _ in range(18):
            with pytest.raises(SnapshotError):
                await builder.build_once(tmp_path, None, producer_sha=SHA)
    asyncio.run(run())
    status = json.loads((tmp_path / "builder-status.json").read_text())
    history = json.loads((tmp_path / "builder-failures.json").read_text())
    assert status["failed_stage"] == "INITIALIZING" and status["keyset_pages"] == 0
    assert status["failure_code"] == "GENERATION_FILE_CAP"
    assert history["total"] == 18 and len(history["failures"]) == 16
    assert not (tmp_path / "current.json").exists()


def test_response_limit_failure_is_identified_before_json_or_event_count():
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(
                200, content=b"x" * (builder.MAX_PAGE_BYTES + 1)))) as client:
            with pytest.raises(SnapshotError) as raised:
                await builder.fetch_page(client, None)
            assert raised.value.code == "PAGE_BYTES_CAP"
            assert raised.value.metrics["observed_bytes"] > builder.MAX_PAGE_BYTES
    asyncio.run(run())


def test_page_object_graph_is_released_before_next_fetch(tmp_path, monkeypatch):
    class Page(list):
        pass
    previous = None
    count = 0
    async def fetch(client, cursor, **kwargs):
        nonlocal previous, count
        assert previous is None or previous() is None
        count += 1
        result = Page([event(raw_market(str(count)), eid=str(count))])
        previous = weakref.ref(result)
        return result, "second" if count == 1 else None, time.time(), "b" * 64
    monkeypatch.setattr(builder, "fetch_page", fetch)
    asyncio.run(builder.build_once(tmp_path, None, producer_sha=SHA))
    assert count == 2 and previous() is None


def test_parent_compression_is_lossless_shared_and_separate_from_market_row_cap(tmp_path):
    # A complete original parent can legitimately be larger than one market row.
    closed = [raw_market(f"closed-{i}", active=False, closed=True) for i in range(3200)]
    original = event(raw_market("a"), raw_market("b"), *closed, negRisk=True, negRiskMarketID="p")
    publish(tmp_path, events=[original])
    pointer = snapshots.current_pointer(tmp_path)
    with sqlite3.connect(tmp_path / pointer["file"]) as db:
        assert db.execute("SELECT count(*) FROM events").fetchone()[0] == 1
        blob, length = db.execute("SELECT payload,raw_bytes FROM events").fetchone()
        assert snapshots.MAX_ROW_BYTES < length < snapshots.MAX_PARENT_BYTES
        assert len(blob) < length / 2
    generation = reader(tmp_path).poll()
    parent = generation.markets[0].raw["_event"]
    assert len(parent["markets"]) == 3202
    assert generation.markets[1].raw["_event"] is parent
    assert snapshots.decode_payload(blob, length, parent=True) == parent


@pytest.mark.parametrize("mutation", ["length", "trailing", "truncated", "expansion"])
def test_compressed_payload_corruption_and_expansion_are_rejected(mutation):
    raw = snapshots.encoded({"question": "fixture"})
    blob, length = zlib.compress(raw), len(raw)
    if mutation == "length":
        length += 1
    elif mutation == "trailing":
        blob += zlib.compress(b"extra")
    elif mutation == "truncated":
        blob = blob[:-2]
    else:
        blob = zlib.compress(b"x" * 1_000_000)
    with pytest.raises(SnapshotError) as raised:
        snapshots.decode_payload(blob, length)
    assert raised.value.code == "PAYLOAD_INVALID"


@pytest.mark.parametrize("cap,code", [("MAX_ROW_BYTES", "ROW_SIZE_CAP"), ("MAX_PARENT_BYTES", "PARENT_SIZE_CAP"),
                                     ("MAX_DECODED_BYTES", "DECODED_SIZE_CAP"), ("MAX_FILE_BYTES", "GENERATION_FILE_CAP"),
                                     ("INVENTORY_CAP", "INVENTORY_CAP")])
def test_distinct_capacity_failures_preserve_accepted_generation(tmp_path, monkeypatch, cap, code):
    publish(tmp_path)
    accepted = snapshots.current_pointer(tmp_path)
    monkeypatch.setattr(snapshots, cap, 1)
    with pytest.raises(SnapshotError) as raised:
        publish(tmp_path, events=[event(raw_market("a"), raw_market("b"))])
    assert raised.value.code == code
    assert snapshots.current_pointer(tmp_path) == accepted


def make_guard(tmp_path):
    marker, head, source = (tmp_path / name for name in ("release.sha", "HEAD", "source.py"))
    marker.write_text(SHA)
    head.write_text(SHA)
    source.write_text("immutable source")
    return PinnedProducer(SHA, marker, head, {source: _signature(source)}), source


def test_attested_release_needs_no_git_between_generations(tmp_path, monkeypatch):
    guard, _ = make_guard(tmp_path)
    def no_git(*args, **kwargs):
        raise AssertionError("git must not run after startup authority")
    monkeypatch.setattr(subprocess, "check_output", no_git)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"events": [event(raw_market())]}))) as client:
            for _ in range(2):
                manifest = await builder.build_once(tmp_path, client, producer_sha=guard.sha, release_check=guard.check)
                assert manifest["producer_sha"] == SHA
    asyncio.run(run())
    assert snapshots.current_pointer(tmp_path)["sequence"] == 2


@pytest.mark.parametrize("change", ["source", "marker", "head"])
def test_release_change_during_build_blocks_publication(tmp_path, change):
    publish(tmp_path)
    accepted = snapshots.current_pointer(tmp_path)
    guard, source = make_guard(tmp_path)
    async def run():
        def respond(request):
            {"source": source, "marker": guard.marker, "head": guard.head}[change].write_text("changed")
            return httpx.Response(200, json={"events": [event(raw_market())]})
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with pytest.raises(SnapshotError) as raised:
                await builder.build_once(tmp_path, client, producer_sha=SHA, release_check=guard.check)
            assert raised.value.code == "RELEASE_MISMATCH"
    asyncio.run(run())
    assert snapshots.current_pointer(tmp_path) == accepted
    assert json.loads((tmp_path / "builder-status.json").read_text())["failed_stage"] == "PUBLISH"


def test_cgroup_diagnostics_distinguish_reclaim_from_oom_and_host_memory(tmp_path):
    proc, cgroup = tmp_path / "proc", tmp_path / "cgroup"
    (proc / "self").mkdir(parents=True)
    (proc / "self/cgroup").write_text("0::/alpha/builder\n")
    unit = cgroup / "alpha/builder"
    unit.mkdir(parents=True)
    for name, value in {"memory.current": "1000", "memory.high": "2000", "memory.max": "3000",
                        "memory.events": "high 17\nmax 0\noom 0\noom_kill 0\n",
                        "memory.stat": "anon 700\nfile 300\npgscan 200\npgsteal 150\n",
                        "cpu.stat": "usage_usec 1000000\nthrottled_usec 2000\n",
                        "io.pressure": "some avg10=1.00 avg60=2.00 avg300=3.00 total=100\n"}.items():
        (unit / name).write_text(value)
    result = cgroup_diagnostics(proc_root=proc, cgroup_root=cgroup)
    assert result["available"] and result["memory.events"]["high"] == 17
    assert result["memory.events"]["oom_kill"] == 0 and result["memory.stat"]["pgscan"] == 200
    assert result["memory.peak"] is None  # Unavailable is not zero.
    assert result["io.pressure"] == {"some": {"avg10": 1., "total": 100.}}


def test_bootstrap_requires_fresh_complete_generation_and_measured_coverage(tmp_path):
    consumer = reader(tmp_path)
    assert ready_snapshot(consumer) is None
    publish(tmp_path, events=[event(raw_market(bestBid=None, bestAsk=None))])
    assert ready_snapshot(consumer) is None
    publish(tmp_path)
    assert ready_snapshot(consumer)["ready"] is True
    assert ready_snapshot(consumer)["screening_coverage"] == 1.
    stale = tmp_path / "stale"
    start = time.time() - 1900
    publish(stale, started=start, finish=start + 10)
    assert ready_snapshot(reader(stale)) is None
