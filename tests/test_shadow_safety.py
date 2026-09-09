import asyncio
from datetime import datetime, timedelta, timezone
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

import command_worker_trade_only as command
from polymarket_scanner.backpressure import coalesce_signal_batches, CANDIDATE_MAX_COUNT, CANDIDATE_MAX_BYTES
from polymarket_scanner.models import Signal
from polymarket_scanner.shadow_execution import record_shadow_execution
from polymarket_scanner.store import Store
from polymarket_scanner.structural_quarantine import quarantine_structural_history
from polymarket_scanner.trade_only import promoted_detectors, is_trade_ready


def signal(key="m", detector="binary_buy_both"):
    return Signal(detector, "ACTIONABLE", "e", key, "fixture", "fixture", "https://example.com",
                  .1, .9, 1, ["yes", "no"], {"fingerprint_key": key})


def test_real_registry_is_exactly_empty():
    assert promoted_detectors() == ()


def test_shadow_execution_exercises_exact_books_without_promotion():
    from test_execution_certificate import _Poly, _info, _books, _raw_market
    poly = _Poly(_info(), _books())
    calls = []
    async def current(mid):
        calls.append(mid)
        return dict(_raw_market(), active=True, closed=False, acceptingOrders=True, enableOrderBook=True)
    poly.market_by_id = current
    row = signal("m1")
    asyncio.run(record_shadow_execution(row, poly))
    evidence = row.metadata["shadow_execution"]
    assert evidence["status"] == "EXACT_EXECUTION_PREVIEW_ONLY"
    assert evidence["certificate"]["legs"][0]["token_id"] == "yes"
    assert calls == ["m1"]
    assert evidence["delivery_permission"] is False
    assert "execution_certificate" not in row.metadata
    assert is_trade_ready(row) is False
    assert promoted_detectors() == ()


def test_shadow_execution_preserves_rejected_candidate_evidence():
    async def closed(mid):
        return {"closed": True}
    row = signal()
    asyncio.run(record_shadow_execution(row, SimpleNamespace(market_by_id=closed)))
    assert row.metadata["shadow_execution"]["status"] == "REJECTED"
    assert is_trade_ready(row) is False


def test_silent_shadow_latch_blocks_alert_http_even_if_certificate_exists(monkeypatch):
    monkeypatch.setattr(command, "_silent_shadow_process", True)
    async def forbidden(*args, **kwargs):
        raise AssertionError("financial network boundary crossed")
    tg = SimpleNamespace(token="fixture", _trade_alert_not_after_epoch=1e30)
    with pytest.raises(command.DeliveryRejected, match="disabled"):
        asyncio.run(command._safe_post_message(tg, SimpleNamespace(post=forbidden), "fixture", "text", lane="alert"))


def test_candidate_count_bytes_and_age_are_bounded_and_drops_visible():
    rows = [signal(str(i)) for i in range(CANDIDATE_MAX_COUNT + 7)]
    batch, stats = coalesce_signal_batches([rows])
    assert len(batch) == CANDIDATE_MAX_COUNT
    assert stats["overflow_dropped"] == 7 and stats["evidence_complete"] is False
    huge = signal("large")
    huge.detail = "x" * (CANDIDATE_MAX_BYTES + 1)
    batch, stats = coalesce_signal_batches([[huge]])
    assert batch == [] and stats["overflow_dropped"] == 1
    stale = signal("old")
    stale.created_at = datetime.now(timezone.utc) - timedelta(seconds=61)
    future = signal("future")
    future.created_at = datetime.now(timezone.utc) + timedelta(seconds=5)
    batch, stats = coalesce_signal_batches([[stale, future]], now=datetime.now(timezone.utc).timestamp())
    assert batch == [] and stats["expired_dropped"] == 2


def test_structural_quarantine_preserves_user_fill_records(tmp_path):
    db = tmp_path / "signals.db"
    store = Store(str(db))
    row = signal(detector="nested_threshold_arb")
    row.metadata["certification_status"] = "NESTED_PAYOFF_PROOF_V2"
    signal_id = store.save_signal(row)
    with store._conn() as connection:
        connection.execute("INSERT INTO manual_trades(signal_id,stake,entry_cost,created_at) VALUES (?,?,?,?)",
                           (signal_id, 10, .9, row.created_at.isoformat()))
        before = [tuple(r) for r in connection.execute("SELECT * FROM manual_trades")]
    assert quarantine_structural_history(str(db)) == 1
    assert quarantine_structural_history(str(db)) == 0
    with store._conn() as connection:
        assert [tuple(r) for r in connection.execute("SELECT * FROM manual_trades")] == before
        evidence = connection.execute("SELECT confidence,edge,theoretical_payout,metadata FROM signals").fetchone()
    assert tuple(evidence[:3]) == ("LEGACY_THEORETICAL", None, None)
    assert json.loads(evidence[3])["semantic_evidence_valid"] is False


def test_settlement_paging_advances_past_unscoreable_research(tmp_path):
    store = Store(str(tmp_path / "signals.db"))
    for i in range(130):
        row = signal(str(i), detector="unrelated")
        row.confidence = "WATCH"
        store.save_signal(row)
    row = signal("valid", detector="macro_fixture")
    expected = store.save_signal(row)
    rows, cursor = store.open_directional_page()
    assert rows == [] and cursor == 128
    rows, cursor = store.open_directional_page(cursor)
    assert [r["id"] for r in rows] == [expected]
    assert store.open_directional_page(cursor) == ([], 0)


def test_watchdog_accepts_responsive_waiting_scanner(monkeypatch):
    import app_stable as stable
    class StopTest(Exception):
        pass
    times = iter([1000., 2000.])
    sleeps = iter([None])
    def sleep(seconds):
        try:
            next(sleeps)
        except StopIteration:
            raise StopTest
    monkeypatch.setattr(stable.time, "time", lambda: next(times))
    monkeypatch.setattr(stable.time, "sleep", sleep)
    monkeypatch.setattr(stable.base, "universe_source", object())
    monkeypatch.setattr(stable.base, "state", {"waiting_for_universe": True, "scanner_heartbeat": 1999., "last_scan": None})
    monkeypatch.setattr(stable.os, "_exit", lambda code: pytest.fail("healthy waiting scanner was killed"))
    with pytest.raises(StopTest):
        stable._watchdog_main()


def test_actual_scanner_accepts_generations_and_research_never_enqueues(tmp_path):
    # Process isolation avoids leaking the production hook installation into tests
    # of legacy modules. All transports below are local fixtures.
    program = r'''
import asyncio, os, sys
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0, "tests")
from test_universe_snapshots import publish, reader, event, raw_market
from test_shadow_safety import signal
from polymarket_scanner.config import settings
settings.db_path = sys.argv[1] + "/signals.db"
settings.telegram_commands_in_app = False
import app_trade_only as runtime
base = runtime.base
directory = Path(sys.argv[1]) / "universe"
publish(directory)
base.universe_source = reader(directory)
base.poly.universe_status = base.universe_source.universe_status
base.state["production_runtime_authority_complete"] = True
base.settings.scan_interval_seconds = .01
async def quiet(*args): return None
base.macro = SimpleNamespace(enabled=False, status=lambda: {}, close=quiet)
async def feeds(*args):
    await asyncio.sleep(.01)
    return {"fallback"}
base.market_stream.configure = quiet
base._wait_for_feeds = feeds
base.settle_open_paper_trades = quiet
seen = []
def evaluate(markets, books, *args, **kwargs):
    accepted = base.universe_source.accepted
    assert markets is accepted.markets
    assert set(books) == set(accepted.screening)
    seen.append(markets[0].id)
    return []
base.evaluate_signals = evaluate
async def run():
    task = asyncio.create_task(base.scanner_loop())
    try:
        async with asyncio.timeout(12):
            while "m" not in seen: await asyncio.sleep(.02)
            publish(directory, events=[event(raw_market("new"))])
            while "new" not in seen: await asyncio.sleep(.02)
        assert seen.count("m") > 1
        row = signal()
        row.confidence = "WATCH"
        row.metadata["trade_ready"] = True
        signal_id = runtime._trade_only_save_signal(row)
        assert signal_id is not None and runtime._trade_only_enqueue(signal_id, row) is False
        with base.store._conn() as db:
            assert db.execute("SELECT count(*) FROM telegram_outbox").fetchone()[0] == 0
        try:
            await base.poly.active_markets()
            raise AssertionError("scanner Gamma crawl was allowed")
        except RuntimeError:
            pass
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await base.poly.close()
        await base.tg.close()
        await base.weather.close()
        await base.macro.close()
asyncio.run(run())
print("actual scanner handoff and silent persistence passed")
'''
    result = subprocess.run([sys.executable, "-c", program, str(tmp_path)], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "handoff and silent persistence passed" in result.stdout
