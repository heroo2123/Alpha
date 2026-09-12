from __future__ import annotations

import asyncio
import json
import time

from polymarket_scanner.weather_only_acceptance import (
    MAX_SAMPLE_GAP_SECONDS,
    MIN_DURATION_SECONDS,
    MIN_SAMPLE_COUNT,
    MIN_SOURCE_UPDATE_SAMPLES,
)
import polymarket_scanner.weather_only_acceptance_recorder_bounded as bounded
from polymarket_scanner.weather_only_acceptance_recorder_bounded import (
    W7_SOURCE_POLL_TIMEOUT_CODE,
    W7_SOURCE_POLL_WALL_CLOCK_BUDGET_SECONDS,
    W7_WRH_REQUEST_TIMEOUT_SECONDS,
    WeatherW7BoundedRecorderSession,
)
from polymarket_scanner.weather_only_acceptance_run import WEATHER_W7_RUNNER_VERSION
from test_weather_only_acceptance_recorder import (
    PID,
    SHA,
    _FakeCLOB,
    _db,
    _event,
    _proc,
    _runtime_payload,
)


class _SlowWRH:
    def fetch_snapshot(self, *, station, target_date):
        time.sleep(0.20)
        raise AssertionError("timed-out source worker result must never become evidence")


def _session(tmp_path, *, wrh=None):
    db = tmp_path / "signals.db"
    _db(db)
    proc = tmp_path / "proc"
    _proc(proc)
    report = tmp_path / "runtime.json"
    report.write_text(json.dumps(_runtime_payload(time.time())), encoding="utf-8")
    return WeatherW7BoundedRecorderSession(
        release_sha=SHA,
        scanner_process_id=PID,
        database_path=db,
        runtime_report_path=report,
        probe_event=_event(hourly=True),
        clob=_FakeCLOB(),
        wrh=wrh,
        proc_root=proc,
    )


def test_bounded_adapter_does_not_change_frozen_w7_policy():
    assert MIN_DURATION_SECONDS == 2700
    assert MIN_SAMPLE_COUNT == 80
    assert MAX_SAMPLE_GAP_SECONDS == 60.0
    assert MIN_SOURCE_UPDATE_SAMPLES == 1
    assert W7_SOURCE_POLL_WALL_CLOCK_BUDGET_SECONDS == 20.0
    assert W7_SOURCE_POLL_WALL_CLOCK_BUDGET_SECONDS < 30.0
    assert W7_WRH_REQUEST_TIMEOUT_SECONDS == 3.0
    assert WEATHER_W7_RUNNER_VERSION == (
        "weather_w7_runner_v3_persistent_source_duration_guard_release_bound_frozen_window"
    )


def test_production_owned_wrh_client_is_persistent_and_short_timeout(tmp_path):
    session = _session(tmp_path)
    assert session.wrh._timeout_seconds == W7_WRH_REQUEST_TIMEOUT_SECONDS
    assert session._owned_wrh_http is not None
    assert session.wrh._external_client is session._owned_wrh_http
    asyncio.run(session.close())
    assert session._owned_wrh_http is None


def test_slow_source_poll_times_out_fail_closed_without_crashing_sample(tmp_path, monkeypatch):
    session = _session(tmp_path, wrh=_SlowWRH())
    monkeypatch.setattr(bounded, "W7_SOURCE_POLL_WALL_CLOCK_BUDGET_SECONDS", 0.01)

    started = time.monotonic()
    sample = asyncio.run(session.record_sample(poll_source=True))
    elapsed = time.monotonic() - started

    assert elapsed < 0.5
    assert sample.cycle_ok is False
    assert sample.source_update_confirmation_seconds is None
    assert sample.source_update_evidence_sha256 is None
    assert W7_SOURCE_POLL_TIMEOUT_CODE in session.source_poll_failure_codes
    assert session.source_measurements == []
    assert sample.financial_authority is False
    assert sample.financial_delivery is False
    assert sample.automatic_order_placement is False
    asyncio.run(session.close())


def test_corrected_window_anchors_after_first_completed_sample(tmp_path, monkeypatch):
    session = _session(tmp_path, wrh=_SlowWRH())
    original_record_sample = session.record_sample

    async def record_without_source(*args, **kwargs):
        return await original_record_sample(poll_source=False)

    monkeypatch.setattr(session, "record_sample", record_without_source)
    monkeypatch.setattr(bounded, "SAMPLE_COUNT", 3)
    monkeypatch.setattr(bounded, "SAMPLE_INTERVAL_SECONDS", 0.02)

    asyncio.run(session.run_frozen_window())
    measured = session.samples[-1].observed_at - session.samples[0].observed_at

    assert len(session.samples) == 3
    assert measured >= 0.04
    asyncio.run(session.close())
