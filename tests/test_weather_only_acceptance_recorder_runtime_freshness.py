from __future__ import annotations

import asyncio
import json
import time

import pytest

from polymarket_scanner.weather_only_acceptance_bundle import validate_weather_w7_acceptance_bundle
from polymarket_scanner.weather_only_acceptance_recorder import (
    MAX_RUNTIME_REPORT_AGE_SECONDS,
    SAMPLE_INTERVAL_SECONDS,
    W7_RUNTIME_REPORT_MAX_AGE_SECONDS,
    WeatherW7RecorderError,
    WeatherW7RecorderSession,
    parse_weather_w7_runtime_report,
)
from polymarket_scanner.weather_only_runtime import DEFAULT_LOOP_INTERVAL_SECONDS
from test_weather_only_acceptance_recorder import (
    PID,
    SHA,
    _FakeCLOB,
    _db,
    _event,
    _proc,
    _runtime_payload,
)


def test_w7_live_runtime_freshness_matches_attested_five_minute_loop_without_weakening_generic_default():
    assert MAX_RUNTIME_REPORT_AGE_SECONDS == 90.0
    assert DEFAULT_LOOP_INTERVAL_SECONDS == 300.0
    assert SAMPLE_INTERVAL_SECONDS == 30.0
    assert W7_RUNTIME_REPORT_MAX_AGE_SECONDS == 360.0

    now = 1_800_000_000.0
    healthy_previous_cycle = _runtime_payload(now - 300.0)
    parsed = parse_weather_w7_runtime_report(
        healthy_previous_cycle,
        observed_at=now,
        max_age_seconds=W7_RUNTIME_REPORT_MAX_AGE_SECONDS,
    )
    assert parsed.cycle_ok is True

    with pytest.raises(WeatherW7RecorderError) as stale:
        parse_weather_w7_runtime_report(
            _runtime_payload(now - 370.0),
            observed_at=now,
            max_age_seconds=W7_RUNTIME_REPORT_MAX_AGE_SECONDS,
        )
    assert stale.value.code == "W7_RECORDER_RUNTIME_REPORT_STALE"


def test_w7_session_runtime_report_stale_after_valid_baseline_becomes_fail_closed_sample_not_crash(tmp_path):
    db = tmp_path / "signals.db"
    _db(db)
    proc = tmp_path / "proc"
    _proc(proc)
    report_path = tmp_path / "runtime.json"
    report_path.write_text(json.dumps(_runtime_payload(time.time())), encoding="utf-8")

    session = WeatherW7RecorderSession(
        release_sha=SHA,
        scanner_process_id=PID,
        database_path=db,
        runtime_report_path=report_path,
        probe_event=_event(),
        clob=_FakeCLOB(),
        proc_root=proc,
    )

    report_path.write_text(
        json.dumps(_runtime_payload(time.time() - 400.0)),
        encoding="utf-8",
    )

    sample = asyncio.run(session.record_sample(poll_source=False))
    assert sample.cycle_ok is False
    assert session.runtime_report_failure_codes == ["W7_RECORDER_RUNTIME_REPORT_STALE"]
    assert sample.weather_event_count == session.last_runtime_observation.weather_event_count
    assert sample.exact_clob_required_for_candidates is True
    assert len(session.samples) == 1
    assert len(session.incremental_measurements) == 1

    bundle = session.finalize()
    acceptance = validate_weather_w7_acceptance_bundle(bundle, expected_release_sha=SHA)
    assert acceptance.passed is False
    assert "CYCLE_UNHEALTHY:0" in acceptance.reasons


def test_w7_session_runtime_invariant_break_after_valid_baseline_also_preserves_fail_artifact(tmp_path):
    db = tmp_path / "signals.db"
    _db(db)
    proc = tmp_path / "proc"
    _proc(proc)
    report_path = tmp_path / "runtime.json"
    report_path.write_text(json.dumps(_runtime_payload(time.time())), encoding="utf-8")

    session = WeatherW7RecorderSession(
        release_sha=SHA,
        scanner_process_id=PID,
        database_path=db,
        runtime_report_path=report_path,
        probe_event=_event(),
        clob=_FakeCLOB(),
        proc_root=proc,
    )

    broken = _runtime_payload(time.time(), financial_delivery=True)
    report_path.write_text(json.dumps(broken), encoding="utf-8")

    sample = asyncio.run(session.record_sample(poll_source=False))
    assert sample.cycle_ok is False
    assert session.runtime_report_failure_codes == [
        "W7_RECORDER_RUNTIME_INVARIANT_BROKEN:financial_delivery"
    ]

    bundle = session.finalize()
    acceptance = validate_weather_w7_acceptance_bundle(bundle, expected_release_sha=SHA)
    assert acceptance.passed is False
    assert "CYCLE_UNHEALTHY:0" in acceptance.reasons
