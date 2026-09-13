from __future__ import annotations

import asyncio
from collections import OrderedDict
from pathlib import Path
from types import SimpleNamespace

from polymarket_scanner import weather_only_live_paper_corrective as corrective_runtime
from polymarket_scanner.weather_only_history_bounded import (
    HISTORY_CURSOR_STATE_KEY,
    HISTORY_POLICY_STATE_KEY,
    BoundedForecastHistoryQuarantine,
)
from polymarket_scanner.weather_only_paper_corrective import PAPER_EXECUTION_PROTOCOL_V4
from polymarket_scanner.weather_only_paper_facade import CorrectiveWeatherPaperStore


POLICY = "TEST_PRE_V4_POLICY_V1"
REASON = "TEST_PRE_V4_QUARANTINE"


def _signal(store, index: int, *, current: bool, sent: bool = True) -> int:
    payload = {
        "event_title": f"Weather fixture {index}",
        "station": "KLGA",
        "target_date": "2026-09-20",
        "ask_size": 10.0,
    }
    if current:
        payload["paper_execution_protocol_version"] = PAPER_EXECUTION_PROTOCOL_V4
    signal_id = store.save_signal(
        fingerprint=f"history-{index}",
        lane="weather_forecast_raw_gap",
        evidence_class="RESEARCH_ONLY_UNCALIBRATED_V4" if current else "LEGACY",
        event_id=f"event-{index}",
        market_id=f"market-{index}",
        side="YES",
        token_id=f"token-{index}",
        model_probability=0.5,
        entry_cost=0.25,
        raw_gap=0.25,
        theoretical_payout=1.0,
        created_at=1000.0 + index,
        payload=payload,
    )
    assert signal_id is not None
    if sent:
        store.mark_telegram_sent(signal_id, 10_000 + index, sent_at=1100.0 + index)
    return int(signal_id)


def _runner(store, *, policy=POLICY, batch=2):
    return BoundedForecastHistoryQuarantine(
        store,
        policy_id=policy,
        current_execution_protocol=PAPER_EXECUTION_PROTOCOL_V4,
        quarantine_reason=REASON,
        batch_size=batch,
    )


def test_month_scale_history_work_is_bounded_and_restart_resumes_from_sqlite_cursor(tmp_path: Path):
    db = tmp_path / "paper.sqlite"
    store = CorrectiveWeatherPaperStore(db)
    ids = [_signal(store, i, current=False) for i in range(1, 6)]

    first = _runner(store, batch=2).run_once()
    assert first.scanned_now == 2
    assert first.quarantined_now == 2
    assert first.cursor_after == ids[1]
    assert first.scan_complete_at_call_end is False

    # New helper instance models a process restart: durable cursor resumes at row 3.
    restarted = CorrectiveWeatherPaperStore(db)
    second = _runner(restarted, batch=2).run_once()
    assert second.cursor_before == ids[1]
    assert second.scanned_now == 2
    assert second.quarantined_now == 2
    assert second.cursor_after == ids[3]

    third = _runner(restarted, batch=2).run_once()
    assert third.scanned_now == 1
    assert third.quarantined_now == 1
    assert third.cursor_after == ids[4]
    assert third.scan_complete_at_call_end is True

    fourth = _runner(restarted, batch=2).run_once()
    assert fourth.scanned_now == 0
    assert fourth.quarantined_now == 0
    assert fourth.cursor_before == fourth.cursor_after == ids[4]
    assert fourth.scan_complete_at_call_end is True


def test_undelivered_legacy_row_is_quarantined_before_cursor_can_skip_it(tmp_path: Path):
    store = CorrectiveWeatherPaperStore(tmp_path / "paper.sqlite")
    old_unsent = _signal(store, 1, current=False, sent=False)
    current = _signal(store, 2, current=True, sent=True)
    report = _runner(store, batch=10).run_once()
    assert report.scanned_now == 2
    assert report.quarantined_now == 1
    with store._conn() as db:
        old = db.execute("SELECT status FROM weather_paper_signals WHERE id=?", (old_unsent,)).fetchone()
        new = db.execute("SELECT status FROM weather_paper_signals WHERE id=?", (current,)).fetchone()
    assert old["status"] == "QUARANTINED"
    assert new["status"] != "QUARANTINED"


def test_policy_change_explicitly_resets_cursor_for_revalidation(tmp_path: Path):
    store = CorrectiveWeatherPaperStore(tmp_path / "paper.sqlite")
    ids = [_signal(store, i, current=True) for i in range(1, 4)]
    first = _runner(store, policy="POLICY_A", batch=10).run_once()
    assert first.cursor_after == ids[-1]
    assert store.get_state(HISTORY_POLICY_STATE_KEY) == "POLICY_A"

    changed = _runner(store, policy="POLICY_B", batch=1).run_once()
    assert changed.cursor_before == 0
    assert changed.scanned_now == 1
    assert changed.cursor_after == ids[0]
    assert store.get_state(HISTORY_POLICY_STATE_KEY) == "POLICY_B"
    assert int(store.get_state(HISTORY_CURSOR_STATE_KEY)) == ids[0]


def test_row_failure_does_not_advance_cursor_past_validation_hole(tmp_path: Path, monkeypatch):
    store = CorrectiveWeatherPaperStore(tmp_path / "paper.sqlite")
    ids = [_signal(store, i, current=False) for i in range(1, 4)]
    original = store.quarantine_signal

    def fail_second(signal_id: int, reason: str):
        if int(signal_id) == ids[1]:
            raise RuntimeError("fixture failure")
        return original(signal_id, reason)

    monkeypatch.setattr(store, "quarantine_signal", fail_second)
    report = _runner(store, batch=10).run_once()
    assert report.scanned_now == 1
    assert report.cursor_after == ids[0]
    assert report.errors == (f"HISTORY_SIGNAL_{ids[1]}:RuntimeError",)
    assert int(store.get_state(HISTORY_CURSOR_STATE_KEY)) == ids[0]


class _StationClient:
    def __init__(self):
        self.calls = []

    async def station(self, station: str):
        self.calls.append(station)
        return SimpleNamespace(station=station, timezone="UTC")


def _canonical_shell():
    service = object.__new__(corrective_runtime.WeatherLivePaperCorrectiveService)
    service.station_client = _StationClient()
    service._bounded_station_metadata = OrderedDict()
    return service


def test_canonical_station_cache_is_ttl_and_lru_bounded(monkeypatch):
    monkeypatch.setattr(corrective_runtime, "STATION_METADATA_CACHE_MAX_ENTRIES", 2)
    monkeypatch.setattr(corrective_runtime, "STATION_METADATA_CACHE_TTL_SECONDS", 10.0)
    ticks = iter((0.0, 1.0, 2.0, 3.0, 20.0))
    monkeypatch.setattr(corrective_runtime.time, "monotonic", lambda: next(ticks))
    service = _canonical_shell()

    async def scenario():
        a1 = await service._station_metadata_for_compiled(SimpleNamespace(station_hint="KAAA"))
        await service._station_metadata_for_compiled(SimpleNamespace(station_hint="KBBB"))
        # Touch KAAA after KBBB so KAAA becomes the most recently used entry.
        a2 = await service._station_metadata_for_compiled(SimpleNamespace(station_hint="KAAA"))
        await service._station_metadata_for_compiled(SimpleNamespace(station_hint="KCCC"))
        assert list(service._bounded_station_metadata) == ["KAAA", "KCCC"]
        a3 = await service._station_metadata_for_compiled(SimpleNamespace(station_hint="KAAA"))
        return a1, a2, a3

    a1, a2, a3 = asyncio.run(scenario())
    assert a1 is a2
    assert a3 is not a2  # TTL expiry at t=20 forces fresh metadata.
    assert service.station_client.calls == ["KAAA", "KBBB", "KCCC", "KAAA"]
    assert len(service._bounded_station_metadata) <= 2
