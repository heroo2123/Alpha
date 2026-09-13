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
