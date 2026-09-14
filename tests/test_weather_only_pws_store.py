from __future__ import annotations

import time

from polymarket_scanner.weather_only_pws import (
    PWS_STATUS_UNCONFIGURED,
    _build_snapshot,
    build_pws_diagnostic,
)
from polymarket_scanner.weather_only_pws_store import SameDayPWSDiagnosticStore


def _record():
    now = time.time()
    snapshot = _build_snapshot(
        status=PWS_STATUS_UNCONFIGURED,
        configured=False,
        latitude=40.7769,
        longitude=-73.8740,
        unit="F",
        received_at=now - 1.0,
        max_distance_km=15.0,
        max_age_seconds=900.0,
        max_future_skew_seconds=120.0,
    )
    return build_pws_diagnostic(
        event_id="event-pws",
        station="KLGA",
        target_date="2026-09-14",
        unit="F",
        as_of=now,
        official_observations=[
            {
                "station": "KLGA",
                "unit": "F",
                "observed_at": now - 60.0,
                "value": 81.0,
            }
        ],
        pws_snapshot=snapshot,
    )


def test_pws_store_is_immutable_deduplicated_and_nonfinancial(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    store = SameDayPWSDiagnosticStore(db)
    record = _record()

    row_id = store.save(record)
    assert row_id is not None
    assert store.save(record) is None

    loaded = store.diagnostic_json(record.diagnostic_sha256)
    assert loaded is not None
    assert loaded["diagnostic_sha256"] == record.diagnostic_sha256
    assert loaded["predictive_only"] is True
    assert loaded["may_replace_official_observation"] is False
    assert loaded["may_reweight_probability"] is False
    assert loaded["included_in_validated_pnl"] is False
    assert loaded["same_day_delivery_enabled"] is False
    assert loaded["financial_authority"] is False

    summary = store.summary()
    assert summary["total"] == 1
    assert summary["unconfigured"] == 1
    assert summary["available"] == 0
    assert summary["contradictions"] == 0
    assert summary["predictive_only"] is True
    assert summary["may_replace_official_observation"] is False
    assert summary["may_reweight_probability"] is False
    assert summary["included_in_validated_pnl"] is False
    assert summary["same_day_delivery_enabled"] is False
    assert summary["financial_authority"] is False


def test_pws_store_recent_keeps_status_and_contradiction_fields(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    store = SameDayPWSDiagnosticStore(db)
    record = _record()
    store.save(record)

    recent = store.recent(10)
    assert len(recent) == 1
    assert recent[0]["event_id"] == "event-pws"
    assert recent[0]["status"] == PWS_STATUS_UNCONFIGURED
    assert recent[0]["contradiction"] == 0
