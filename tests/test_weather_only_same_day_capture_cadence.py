from __future__ import annotations

from pathlib import Path

import pytest

from polymarket_scanner.weather_only_same_day_capture_store import (
    SameDayCaptureStore,
    SameDayCaptureStoreError,
)
from test_weather_only_same_day_capture import _capture


def test_latest_capture_time_is_durable_across_store_instances(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    first = SameDayCaptureStore(db)
    capture = _capture()
    assert first.latest_as_of_for_event(capture.event_id) is None
    assert first.save(capture) is not None
    assert first.latest_as_of_for_event(capture.event_id) == pytest.approx(capture.as_of)

    # A new process/store instance sees the same persisted sampling boundary.
    restarted = SameDayCaptureStore(db)
    assert restarted.latest_as_of_for_event(capture.event_id) == pytest.approx(capture.as_of)
    summary = restarted.summary()
    assert summary["persistent_cadence_supported"] is True
    assert summary["automatic_evidence_pruning"] is False
    assert summary["capture_json_bytes"] > 0
    assert summary["database_file_bytes"] > 0


def test_blank_event_identity_cannot_bypass_persistent_cadence(tmp_path):
    store = SameDayCaptureStore(tmp_path / "weather-paper.sqlite")
    with pytest.raises(SameDayCaptureStoreError, match="SAME_DAY_CAPTURE_STORE_EVENT_ID_INVALID"):
        store.latest_as_of_for_event("   ")



def test_failed_attempt_is_durable_and_survives_restart(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    first = SameDayCaptureStore(db)
    attempt = first.start_attempt(
        event_id="event-x", station="KLGA", target_date="2026-09-14",
        family="DAILY_HIGH", unit="F", attempted_at=1000.0,
    )
    first.finish_attempt(
        attempt, outcome="FAILED", completed_at=1001.0, error_code="SOURCE_TIMEOUT"
    )
    restarted = SameDayCaptureStore(db)
    assert restarted.latest_attempt_at_for_event("event-x") == pytest.approx(1000.0)
    summary = restarted.attempt_summary()
    assert summary["total"] == 1
    assert summary["failed"] == 1
    assert summary["started"] == 0
    assert summary["included_in_validated_pnl"] is False


def test_interrupted_attempt_is_reconciled_as_failed_not_silently_lost(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    first = SameDayCaptureStore(db)
    first.start_attempt(
        event_id="event-x", station="KLGA", target_date="2026-09-14",
        family="DAILY_HIGH", unit="F", attempted_at=1000.0,
    )
    restarted = SameDayCaptureStore(db)
    assert restarted.reconcile_started_attempts(completed_at=1002.0) == 1
    summary = restarted.attempt_summary()
    assert summary["started"] == 0
    assert summary["failed"] == 1


def test_attempt_and_capture_capacity_limits_fail_closed(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    store = SameDayCaptureStore(
        db, max_capture_rows=1, max_capture_json_bytes=1, max_attempt_rows=1
    )
    attempt = store.start_attempt(
        event_id="event-x", station="KLGA", target_date="2026-09-14",
        family="DAILY_HIGH", unit="F", attempted_at=1000.0,
    )
    store.finish_attempt(
        attempt, outcome="FAILED", completed_at=1001.0, error_code="TEST"
    )
    with pytest.raises(SameDayCaptureStoreError, match="ATTEMPT_ROW_CAP_EXHAUSTED"):
        store.start_attempt(
            event_id="event-y", station="KLGA", target_date="2026-09-14",
            family="DAILY_HIGH", unit="F", attempted_at=1002.0,
        )
    with pytest.raises(SameDayCaptureStoreError, match="CAPTURE_BYTE_CAP_EXHAUSTED"):
        store.save(_capture())



def test_started_attempt_with_durable_capture_reconciles_as_saved(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    store = SameDayCaptureStore(db)
    capture = _capture()
    attempt = store.start_attempt(
        event_id=capture.event_id,
        station=capture.station,
        target_date=capture.target_date,
        family=capture.family,
        unit=capture.unit,
        attempted_at=capture.as_of - 1.0,
    )
    assert store.save(capture) is not None
    assert store.reconcile_started_attempts(completed_at=capture.as_of + 1.0) == 1
    summary = store.attempt_summary()
    assert summary["started"] == 0
    assert summary["saved"] == 1
    assert summary["failed"] == 0
