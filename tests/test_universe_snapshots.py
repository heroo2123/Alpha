import asyncio
import json
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from polymarket_scanner import universe_builder as builder
from polymarket_scanner.models import Market
from polymarket_scanner.production_universe import PRODUCTION_UNIVERSE_FILTER_VERSION
from polymarket_scanner.universe_reader import UniverseReader
from polymarket_scanner.universe_snapshot import (
    SnapshotError, SnapshotWriter, atomic_json, builder_lock, current_pointer,
    file_hash, generation_age, open_generation, GAMMA_PAGE_SIZE,
)

SHA = "a" * 40


def raw_market(mid="m", **extra):
    return {"id": mid, "active": True, "closed": False, "enableOrderBook": True,
            "acceptingOrders": True, "conditionId": "c" + mid,
            "outcomes": ["Yes", "No"], "clobTokenIds": ["y" + mid, "n" + mid],
            "question": "Will BTC be above $100?", "bestBid": .4, "bestAsk": .42,
            "description": "Fixture only", "resolutionSource": "fixture", **extra}


def event(*markets, eid="e", **extra):
    return {"id": eid, "title": "Fixture", "markets": list(markets), **extra}


def publish(directory, *, events=None, started=None, finish=None):
    now = time.time()
    directory.mkdir(exist_ok=True)
    writer = SnapshotWriter(directory, producer_sha=SHA, filter_version=PRODUCTION_UNIVERSE_FILTER_VERSION,
                            started_at=now - 2 if started is None else started)
    try:
        receipt = now - 1 if finish is None else finish
        builder.append_events(writer, events or [event(raw_market())], receipt)
        writer.page(cursor_in=None, cursor_out=None, receipt=receipt, seconds=.1,
                    response_sha256="b" * 64, events=1)
        return writer.publish(finished_at=now if finish is None else finish)
    finally:
        writer.abort()


def reader(directory):
    return UniverseReader(directory, producer_sha=SHA, priority=lambda market: market.id,
                          weather_universe=lambda markets: ([], []))


def test_atomic_acceptance_and_partial_builder_failure(tmp_path):
    first = publish(tmp_path)
    consumer = reader(tmp_path)
    consumer.accept(consumer.poll())
    old = consumer.accepted
    pointer = (tmp_path / "current.json").read_bytes()
    writer = SnapshotWriter(tmp_path, producer_sha=SHA, filter_version=PRODUCTION_UNIVERSE_FILTER_VERSION)
    try:
        builder.append_events(writer, [event(raw_market("new"))], time.time())
        writer.page(cursor_in=None, cursor_out="continue", receipt=time.time(), seconds=.1,
                    response_sha256="b" * 64, events=1)
        with pytest.raises(SnapshotError, match="exhaustion"):
            writer.publish()
        assert consumer.poll() is None
        assert consumer.accepted is old
        assert consumer.universe_status()["generation_id"] == first["generation_id"]
        assert (tmp_path / "current.json").read_bytes() == pointer
    finally:
        writer.abort()
    second = publish(tmp_path, events=[event(raw_market("new"))])
    prepared = consumer.poll()
    assert consumer.accepted is old  # Loading cannot mutate accepted objects.
    consumer.accept(prepared)
    assert consumer.accepted.markets[0].id == "new"
    assert consumer.universe_status()["generation_id"] == second["generation_id"]


def test_builder_lock_excludes_second_owner_and_recovers_after_release(tmp_path):
    with builder_lock(tmp_path):
        with pytest.raises(SnapshotError, match="another"):
            with builder_lock(tmp_path):
                pass
    with builder_lock(tmp_path):
        pass


@pytest.mark.parametrize("fault", ["checksum", "version", "release", "rollback", "partial"])
def test_invalid_generation_never_replaces_accepted(tmp_path, fault):
    publish(tmp_path)
    consumer = reader(tmp_path)
    consumer.accept(consumer.poll())
    prior = consumer.accepted
    first_pointer = current_pointer(tmp_path)
    publish(tmp_path, events=[event(raw_market("new"))])
    ptr = current_pointer(tmp_path)
    path = tmp_path / ptr["file"]
    if fault == "checksum":
        with path.open("ab") as stream:
            stream.write(b"corrupt")
    elif fault == "rollback":
        consumer.accept(consumer.poll())
        prior = consumer.accepted
        atomic_json(tmp_path / "current.json", first_pointer)
    else:
        import sqlite3
        with sqlite3.connect(path) as db:
            manifest = json.loads(db.execute("SELECT value FROM metadata").fetchone()[0])
            if fault == "version":
                manifest["version"] = "incompatible"
            elif fault == "release":
                manifest["producer_sha"] = "c" * 40
            else:
                manifest["complete"] = False
            db.execute("UPDATE metadata SET value=?", (json.dumps(manifest),))
        ptr["sha256"] = file_hash(path)
        atomic_json(tmp_path / "current.json", ptr)
    assert consumer.poll() is None
    assert consumer.accepted is prior
    assert consumer.universe_status()["safe_for_detection"] is True
    assert consumer.last_error


def test_publication_failure_after_rename_preserves_pointer_and_cleans_orphan(tmp_path, monkeypatch):
    publish(tmp_path)
    prior = (tmp_path / "current.json").read_bytes()
    import polymarket_scanner.universe_snapshot as snapshots
    actual_atomic = snapshots.atomic_json

    def fail_pointer(path, value):
        if path.name == "current.json":
            raise OSError("injected disk failure")
        actual_atomic(path, value)

    monkeypatch.setattr(snapshots, "atomic_json", fail_pointer)
    with pytest.raises(OSError):
        publish(tmp_path)
    assert (tmp_path / "current.json").read_bytes() == prior
    assert len(list(tmp_path.glob("g-*.sqlite"))) == 1


def test_staleness_is_from_first_observation_and_quotes_expire_separately(tmp_path):
    start = time.time() - 1100
    manifest = publish(tmp_path, started=start, finish=start + 200)
    consumer = reader(tmp_path)
    prepared = consumer.poll()
    assert prepared is not None
    consumer.accept(prepared)
    assert consumer.universe_status()["safe_for_detection"] is True
    assert consumer.screening_status()["stale"] is True
    assert consumer.universe_status(now=start + 1800)["safe_for_detection"] is False
    assert consumer.universe_status(now=start - 1)["safe_for_detection"] is False
    assert generation_age(manifest, now=start + 1801) == 1801


def test_hard_stale_snapshot_cannot_be_loaded_on_restart(tmp_path):
    start = time.time() - 1900
    # Create a file valid at publication time, then observe it on a later restart.
    publish(tmp_path, started=start, finish=start + 10)
    assert reader(tmp_path).poll() is None


def test_quote_receipts_are_not_replaced_by_acceptance_time(tmp_path):
    manifest = publish(tmp_path)
    consumer = reader(tmp_path)
    prepared = consumer.poll()
    for book in prepared.screening.values():
        assert book.received_at == manifest["first_page_received_at"]
        assert book.source == "gamma_bbo_screening"
    consumer.accept(prepared)
    assert consumer.screening_status()["executable"] is False


def test_original_parent_keeps_excluded_closed_child(tmp_path):
    rows = [raw_market("a"), raw_market("b"), raw_market("other"), raw_market("closed", active=False, closed=True)]
    publish(tmp_path, events=[event(*rows, negRisk=True, negRiskMarketID="parent")])
    prepared = reader(tmp_path).poll()
    assert len(prepared.markets) == 3
    original = prepared.markets[0].raw["_event"]
    assert {child["id"] for child in original["markets"]} == {"a", "b", "other", "closed"}
    assert all(m.raw["_event"] is original for m in prepared.markets)


def test_materialized_cap_aborts_without_publication(tmp_path, monkeypatch):
    import polymarket_scanner.universe_snapshot as snapshots
    monkeypatch.setattr(snapshots, "MATERIALIZED_CAP", 1)
    with pytest.raises(SnapshotError, match="materialized"):
        publish(tmp_path, events=[event(raw_market("a"), raw_market("b"))])
    assert current_pointer(tmp_path) is None


def test_discovery_cap_includes_excluded_markets(tmp_path, monkeypatch):
    import polymarket_scanner.universe_snapshot as snapshots
    monkeypatch.setattr(snapshots, "DISCOVERY_CAP", 1)
    with pytest.raises(SnapshotError, match="discovery"):
        publish(tmp_path, events=[event(raw_market("a"), raw_market("b", question="Unrelated?"))])
    assert current_pointer(tmp_path) is None


def test_conflicting_duplicate_identity_aborts(tmp_path):
    with pytest.raises(SnapshotError, match="conflicting duplicate"):
        publish(tmp_path, events=[event(raw_market()), event(raw_market(conditionId="wrong"))])


def test_generation_retention_is_bounded(tmp_path):
    for _ in range(5):
        publish(tmp_path)
    assert len(list(tmp_path.glob("g-*.sqlite"))) == 3
    assert reader(tmp_path).poll() is not None


@pytest.mark.parametrize("payload", [[], {}, {"events": None}, {"events": [{}] * GAMMA_PAGE_SIZE},
                                     {"events": [{}] * GAMMA_PAGE_SIZE, "next_cursor": None},
                                     {"events": [], "next_cursor": "more"},
                                     {"events": [], "next_cursor": 1}, {"events": [], "next_cursor": ""}])
def test_malformed_keyset_envelopes_never_imply_exhaustion(payload):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload))) as client:
            with pytest.raises(SnapshotError):
                await builder.fetch_page(client, None)
    asyncio.run(run())


@pytest.mark.parametrize("events", [[], [event(raw_market())]])
def test_documented_terminal_short_page_omits_cursor(events):
    # Contract: https://docs.polymarket.com/api-spec/gamma-openapi.yaml
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json={"events": events}))) as client:
            actual, cursor, _, _ = await builder.fetch_page(client, "prior")
            assert actual == events and cursor is None
    asyncio.run(run())


def test_complete_cursor_walk_and_repeated_cursor_failure(tmp_path):
    async def run(repeated):
        calls = []
        def respond(request):
            calls.append(request.url.params.get("after_cursor"))
            index = len(calls)
            payload = {"events": [event(raw_market(str(index)), eid=str(index))]}
            if index == 1 or repeated:
                payload["next_cursor"] = "next"
            return httpx.Response(200, json=payload)
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            with builder_lock(tmp_path):
                if repeated:
                    with pytest.raises(SnapshotError, match="repeated"):
                        await builder.build_once(tmp_path, client, producer_sha=SHA)
                else:
                    manifest = await builder.build_once(tmp_path, client, producer_sha=SHA)
                    assert manifest["complete"] is True
                    assert manifest["keyset_pages"] == 2
        assert calls == [None, "next"]
    asyncio.run(run(False))
    accepted = current_pointer(tmp_path)
    asyncio.run(run(True))
    assert current_pointer(tmp_path) == accepted


def test_slow_builder_does_not_block_reader_or_change_accepted_state(tmp_path, monkeypatch):
    publish(tmp_path)
    consumer = reader(tmp_path)
    consumer.accept(consumer.poll())
    old = consumer.accepted

    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()
        async def stalled(client, cursor, **kwargs):
            entered.set()
            await finish.wait()
            return [event(raw_market("new"))], None, time.time(), "b" * 64
        monkeypatch.setattr(builder, "fetch_page", stalled)
        task = asyncio.create_task(builder.build_once(tmp_path, None, producer_sha=SHA))
        await entered.wait()
        assert await asyncio.to_thread(consumer.poll) is None
        assert consumer.accepted is old
        assert consumer.universe_status()["safe_for_detection"] is True
        finish.set()
        await task
        new = await asyncio.to_thread(consumer.poll)
        assert consumer.accepted is old
        consumer.accept(new)
        assert consumer.accepted.markets[0].id == "new"
    asyncio.run(run())


def test_publication_deadline_includes_reader_validation(tmp_path, monkeypatch):
    import polymarket_scanner.universe_snapshot as snapshots
    publish(tmp_path)
    before = current_pointer(tmp_path)
    writer = SnapshotWriter(tmp_path, producer_sha=SHA, filter_version=PRODUCTION_UNIVERSE_FILTER_VERSION)
    actual_open = snapshots.open_generation
    def slow_validation(*args, **kwargs):
        result = actual_open(*args, **kwargs)
        writer.started_monotonic -= snapshots.BUILD_DEADLINE_SECONDS + 1
        return result
    monkeypatch.setattr(snapshots, "open_generation", slow_validation)
    try:
        builder.append_events(writer, [event(raw_market("new"))], time.time())
        writer.page(cursor_in=None, cursor_out=None, receipt=time.time(), seconds=.1,
                    response_sha256="b" * 64, events=1)
        with pytest.raises(SnapshotError, match="publication exceeded"):
            writer.publish()
    finally:
        writer.abort()
    assert current_pointer(tmp_path) == before
    assert len(list(tmp_path.glob("g-*.sqlite"))) == 1


def test_same_generation_checksum_change_is_reported_without_replacement(tmp_path):
    publish(tmp_path)
    consumer = reader(tmp_path)
    consumer.accept(consumer.poll())
    accepted = consumer.accepted
    pointer = current_pointer(tmp_path)
    pointer["sha256"] = "f" * 64
    atomic_json(tmp_path / "current.json", pointer)
    assert consumer.poll() is None and consumer.last_error
    assert consumer.accepted is accepted


@pytest.mark.parametrize("publish_before_crash", [False, True])
def test_abrupt_builder_death_leaves_only_complete_authority_and_releases_lock(tmp_path, publish_before_crash):
    publish(tmp_path)
    before = current_pointer(tmp_path)
    program = '''
import json, os, sys, time
from pathlib import Path
from polymarket_scanner.universe_snapshot import SnapshotWriter, builder_lock
from polymarket_scanner.universe_builder import append_events
from polymarket_scanner.production_universe import PRODUCTION_UNIVERSE_FILTER_VERSION
directory = Path(sys.argv[1])
with builder_lock(directory):
    writer = SnapshotWriter(directory, producer_sha="a" * 40, filter_version=PRODUCTION_UNIVERSE_FILTER_VERSION)
    append_events(writer, json.loads(sys.argv[2]), time.time())
    writer.page(cursor_in=None, cursor_out=None, receipt=time.time(), seconds=.1, response_sha256="b" * 64, events=1)
    if sys.argv[3] == "True":
        writer.publish()
    os._exit(91)  # No finally, abort, connection close or flock unlock.
'''
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path),
                             json.dumps([event(raw_market("new"))]), str(publish_before_crash)],
                            timeout=10, capture_output=True, text=True)
    assert result.returncode == 91, result.stderr
    consumer = reader(tmp_path)
    prepared = consumer.poll()
    assert prepared is not None and prepared.manifest["complete"] is True
    assert prepared.markets[0].id == ("new" if publish_before_crash else "m")
    assert (current_pointer(tmp_path) == before) is (not publish_before_crash)
    with builder_lock(tmp_path):
        publish(tmp_path, events=[event(raw_market("recovered"))])
    assert consumer.poll().markets[0].id == "recovered"
