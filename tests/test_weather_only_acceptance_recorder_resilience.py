from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import polymarket_scanner.weather_only_acceptance_recorder as recorder
from polymarket_scanner.weather_only_acceptance_bundle import validate_weather_w7_acceptance_bundle
from polymarket_scanner.weather_only_acceptance_recorder import WeatherW7RecorderSession
from polymarket_scanner.weather_only_wrh import WRHSourceError
from test_weather_only_acceptance_recorder import (
    PID,
    SHA,
    _db,
    _proc,
    _runtime_payload,
)
from test_weather_only_acceptance_recorder_source import (
    _OrderedFakeCLOB,
    _event_for_target,
    _finality_snapshots,
)


class _SequenceWRH:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def fetch_snapshot(self, *, station, target_date):
        assert station == "KLGA"
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        assert outcome.target_date == target_date
        return SimpleNamespace(snapshot=outcome)


def _session(tmp_path, wrh):
    target, before, _ = _finality_snapshots()
    event = _event_for_target(target)
    db = tmp_path / "signals.db"
    _db(db)
    proc = tmp_path / "proc"
    _proc(proc)
    report = tmp_path / "runtime.json"
    report.write_text(json.dumps(_runtime_payload(time.time())), encoding="utf-8")
    return WeatherW7RecorderSession(
        release_sha=SHA,
        scanner_process_id=PID,
        database_path=db,
        runtime_report_path=report,
        probe_event=event,
        clob=_OrderedFakeCLOB(),
        wrh=wrh,
        proc_root=proc,
    ), before


def test_w7_source_poll_retries_transient_backend_http_error_without_false_unhealthy_sample(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder, "SOURCE_POLL_RETRY_DELAY_SECONDS", 0.0)
    target, before, _ = _finality_snapshots()
    wrh = _SequenceWRH((
        WRHSourceError("WRH_LIVE_BACKEND_HTTP_ERROR"),
        before,
    ))
    session, _ = _session(tmp_path, wrh)

    sample = asyncio.run(session.record_sample(poll_source=True))

    assert target == session.compiled.target_date
    assert wrh.calls == 2
    assert sample.cycle_ok is True
    assert sample.source_update_confirmation_seconds is None
    assert sample.source_update_evidence_sha256 is None
    assert session.source_poll_failure_codes == []
    assert session.previous_wrh_snapshot == before
    assert len(session.samples) == 1
    assert len(session.incremental_measurements) == 1


def test_w7_source_poll_exhaustion_records_fail_closed_sample_and_preserves_bundle(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder, "SOURCE_POLL_RETRY_DELAY_SECONDS", 0.0)
    wrh = _SequenceWRH((
        WRHSourceError("WRH_LIVE_BACKEND_HTTP_ERROR"),
        WRHSourceError("WRH_LIVE_BACKEND_HTTP_ERROR"),
        WRHSourceError("WRH_LIVE_BACKEND_HTTP_ERROR"),
    ))
    session, _ = _session(tmp_path, wrh)

    sample = asyncio.run(session.record_sample(poll_source=True))

    assert wrh.calls == recorder.SOURCE_POLL_MAX_ATTEMPTS == 3
    assert sample.cycle_ok is False
    assert sample.source_update_confirmation_seconds is None
    assert sample.source_update_evidence_sha256 is None
    assert session.source_poll_failure_codes == ["WRH_LIVE_BACKEND_HTTP_ERROR"]
    assert session.previous_wrh_snapshot is None
    assert len(session.samples) == 1
    assert len(session.incremental_measurements) == 1

    bundle = session.finalize()
    report = validate_weather_w7_acceptance_bundle(bundle, expected_release_sha=SHA)
    assert report.passed is False
    assert "CYCLE_UNHEALTHY:0" in report.reasons


def test_w7_source_integrity_error_is_not_retried_and_is_fail_closed(tmp_path, monkeypatch):
    monkeypatch.setattr(recorder, "SOURCE_POLL_RETRY_DELAY_SECONDS", 0.0)
    wrh = _SequenceWRH((WRHSourceError("WRH_LIVE_VIEWER_SCRIPT_SHA_MISMATCH"),))
    session, _ = _session(tmp_path, wrh)

    sample = asyncio.run(session.record_sample(poll_source=True))

    assert wrh.calls == 1
    assert sample.cycle_ok is False
    assert session.source_poll_failure_codes == ["WRH_LIVE_VIEWER_SCRIPT_SHA_MISMATCH"]
    assert session.previous_wrh_snapshot is None
