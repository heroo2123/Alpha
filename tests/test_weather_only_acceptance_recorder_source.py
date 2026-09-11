from __future__ import annotations

import asyncio
import json
import time
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from polymarket_scanner.weather_only_acceptance_bundle import validate_weather_w7_acceptance_bundle
from polymarket_scanner.weather_only_acceptance_recorder import WeatherW7RecorderSession
from polymarket_scanner.weather_only_wrh import parse_synoptic_wrh_hourly_snapshot
from test_weather_only_acceptance_recorder import (
    PID,
    SHA,
    _FakeCLOB,
    _db,
    _event,
    _proc,
    _runtime_payload,
)


def _event_for_target(target_date):
    event = deepcopy(_event(hourly=True))
    event["description"] = event["description"].replace(
        "11 Sep '26",
        target_date.strftime("%d %b '%y"),
    )
    event["title"] = f"Highest temperature in NYC on {target_date.strftime('%B %d')}?"
    return event


def _source_payload(target_date, *, following_observed_at: float, include_following: bool):
    target_midday = datetime.combine(
        target_date,
        datetime.min.time(),
        tzinfo=timezone.utc,
    ) + timedelta(hours=12, minutes=51)
    target_late = target_midday + timedelta(hours=6)
    timestamps = [target_midday.isoformat(), target_late.isoformat()]
    temperatures = [70.0, 82.0]
    metars = [
        f"KLGA {target_midday:%d%H%M}Z AUTO ...",
        f"KLGA {target_late:%d%H%M}Z AUTO ...",
    ]
    pressures = [1012.0, 1009.0]
    if include_following:
        following = datetime.fromtimestamp(following_observed_at, tz=timezone.utc)
        timestamps.append(following.isoformat())
        temperatures.append(75.0)
        metars.append(f"KLGA {following:%d%H%M}Z AUTO ...")
        pressures.append(1011.0)
    return {
        "SUMMARY": {"RESPONSE_MESSAGE": "OK"},
        "STATION": [{
            "STID": "KLGA",
            "SHORTNAME": "GLOBAL-METAR",
            "TIMEZONE": "UTC",
            "OBSERVATIONS": {
                "date_time": timestamps,
                "air_temp_set_1": temperatures,
                "metar_set_1": metars,
                "sea_level_pressure_set_1": pressures,
            },
        }],
    }


def _finality_snapshots():
    now = time.time()
    target = datetime.now(timezone.utc).date() - timedelta(days=1)
    following_observed_at = now - 0.40
    before = parse_synoptic_wrh_hourly_snapshot(
        _source_payload(target, following_observed_at=following_observed_at, include_following=False),
        station="KLGA",
        target_date=target,
        query_start_date=target,
        query_end_date=target + timedelta(days=1),
        received_at=now - 0.80,
    )
    after = parse_synoptic_wrh_hourly_snapshot(
        _source_payload(target, following_observed_at=following_observed_at, include_following=True),
        station="KLGA",
        target_date=target,
        query_start_date=target,
        query_end_date=target + timedelta(days=1),
        received_at=now - 0.20,
    )
    assert before.first_following_row is None
    assert after.first_following_row is not None
    assert after.target_high_f == 82
    return target, before, after


class _FakeWRH:
    def __init__(self, snapshots):
        self.snapshots = list(snapshots)
        self.calls = 0

    def fetch_snapshot(self, *, station, target_date):
        assert station == "KLGA"
        snapshot = self.snapshots.pop(0)
        assert snapshot.target_date == target_date
        self.calls += 1
        return SimpleNamespace(snapshot=snapshot)


def test_recorder_source_poll_finality_double_clob_and_latency_receipt_are_one_path(tmp_path):
    target, before, after = _finality_snapshots()
    event = _event_for_target(target)
    db = tmp_path / "signals.db"
    _db(db)
    proc = tmp_path / "proc"
    _proc(proc)
    report = tmp_path / "runtime.json"
    report.write_text(json.dumps(_runtime_payload(time.time())), encoding="utf-8")
    wrh = _FakeWRH((before, after))

    session = WeatherW7RecorderSession(
        release_sha=SHA,
        scanner_process_id=PID,
        database_path=db,
        runtime_report_path=report,
        probe_event=event,
        clob=_FakeCLOB(),
        wrh=wrh,
        proc_root=proc,
    )

    first = asyncio.run(session.record_sample(poll_source=True))
    assert wrh.calls == 1
    assert first.source_update_confirmation_seconds is None
    assert first.source_update_evidence_sha256 is None
    assert len(session.incremental_measurements) == 1
    assert session.source_measurements == []

    # Keep the scanner report causal/fresh for the second sample. The next source
    # fetch contains the first selected following-date row, so the recorder must
    # certify WRH finality, identify the exact winning bucket, perform two fresh CLOB
    # checks, and attach one causal W7 source-latency receipt to this sample.
    report.write_text(json.dumps(_runtime_payload(time.time())), encoding="utf-8")
    second = asyncio.run(session.record_sample(poll_source=True))
    assert wrh.calls == 2
    assert second.source_update_confirmation_seconds is not None
    assert 0.0 <= second.source_update_confirmation_seconds < 5.0
    assert second.source_update_evidence_sha256 is not None
    assert len(second.source_update_evidence_sha256) == 64
    assert len(session.incremental_measurements) == 2
    assert len(session.source_measurements) == 1
    measurement = session.source_measurements[0]
    assert measurement.measurement_evidence_sha256 == second.source_update_evidence_sha256
    assert measurement.source_update_confirmation_seconds == second.source_update_confirmation_seconds
    assert measurement.financial_authority is False
    assert measurement.financial_delivery is False
    assert measurement.automatic_order_placement is False

    bundle = session.finalize()
    manifest = bundle.w7_evidence.measurement_manifest
    assert len(manifest.incremental_measurements) == 2
    assert len(manifest.source_update_measurements) == 1
    assert manifest.source_update_measurements[0] == measurement
    report_result = validate_weather_w7_acceptance_bundle(bundle, expected_release_sha=SHA)
    # Two samples intentionally fail the frozen 45-minute duration/count gate, but
    # source-update evidence itself is genuine, embedded, interval-bound and <5 sec.
    assert report_result.passed is False
    assert report_result.source_update_sample_count == 1
    assert report_result.max_source_update_confirmation_seconds == second.source_update_confirmation_seconds
    assert any(reason.startswith("SAMPLE_COUNT_BELOW_MIN") for reason in report_result.reasons)
