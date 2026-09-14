from __future__ import annotations

import time

import pytest

from polymarket_scanner.weather_only_pws import (
    DEFAULT_PWS_COLLECTION_DEADLINE_SECONDS,
    DEFAULT_PWS_IDENTITY_LOCATION_TOLERANCE_KM,
    DEFAULT_PWS_MAX_AGE_SECONDS,
    DEFAULT_PWS_MAX_DISTANCE_KM,
    DEFAULT_PWS_MAX_FUTURE_SKEW_SECONDS,
    DEFAULT_PWS_MAX_RESPONSE_BYTES,
    DEFAULT_PWS_REQUEST_DEADLINE_SECONDS,
    PWS_STATUS_NO_FRESH_QC,
    PWS_STATUS_UNCONFIGURED,
    _build_snapshot,
    build_pws_diagnostic,
)
from polymarket_scanner.weather_only_pws_store import (
    PWSDiagnosticStoreError,
    SameDayPWSDiagnosticStore,
)
from polymarket_scanner.weather_only_same_day_capture_store import SameDayCaptureStore
from test_weather_only_same_day_capture import _capture


def _snapshot(now, *, status=PWS_STATUS_UNCONFIGURED, configured=False):
    return _build_snapshot(
        status=status,
        configured=configured,
        latitude=40.7769,
        longitude=-73.8740,
        unit="F",
        received_at=now - 1.0,
        max_distance_km=DEFAULT_PWS_MAX_DISTANCE_KM,
        identity_location_tolerance_km=DEFAULT_PWS_IDENTITY_LOCATION_TOLERANCE_KM,
        max_age_seconds=DEFAULT_PWS_MAX_AGE_SECONDS,
        max_future_skew_seconds=DEFAULT_PWS_MAX_FUTURE_SKEW_SECONDS,
        max_response_bytes=DEFAULT_PWS_MAX_RESPONSE_BYTES,
        request_deadline_seconds=DEFAULT_PWS_REQUEST_DEADLINE_SECONDS,
        collection_deadline_seconds=DEFAULT_PWS_COLLECTION_DEADLINE_SECONDS,
    )


def _record(capture, *, status=PWS_STATUS_UNCONFIGURED, configured=False):
    now = max(time.time(), float(capture.as_of) + 1.0)
    return build_pws_diagnostic(
        event_id=capture.event_id,
        station=capture.station,
        target_date=capture.target_date,
        unit=capture.unit,
        as_of=now,
        official_observations=capture.official_observations,
        pws_snapshot=_snapshot(now, status=status, configured=configured),
    )


def test_official_capture_and_pws_pending_intent_are_one_transaction(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    capture_store = SameDayCaptureStore(db)
    pws_store = SameDayPWSDiagnosticStore(db)
    capture = _capture()

    assert capture_store.save(capture) is not None
    link = pws_store.link_for_capture(capture.capture_sha256)
    assert link is not None
    assert link["state"] == "PENDING"
    assert link["event_id"] == capture.event_id
    assert link["financial_authority"] == 0


def test_unconfigured_diagnostic_is_durable_failed_link_not_false_no_data(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    capture_store = SameDayCaptureStore(db)
    pws_store = SameDayPWSDiagnosticStore(db)
    capture = _capture()
    assert capture_store.save(capture) is not None

    record = _record(capture)
    row_id = pws_store.finalize(capture.capture_sha256, record)
    assert row_id is not None

    link = pws_store.link_for_capture(capture.capture_sha256)
    assert link is not None
    assert link["state"] == "FAILED"
    assert link["failure_code"] == PWS_STATUS_UNCONFIGURED
    assert link["diagnostic_sha256"] == record.diagnostic_sha256
    loaded = pws_store.diagnostic_json(record.diagnostic_sha256)
    assert loaded is not None
    assert loaded["predictive_only"] is True
    assert loaded["may_replace_official_observation"] is False
    assert loaded["may_reweight_probability"] is False
    assert loaded["included_in_validated_pnl"] is False
    assert loaded["same_day_delivery_enabled"] is False
    assert loaded["financial_authority"] is False


def test_true_no_fresh_qc_result_is_unavailable_not_failed(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    capture_store = SameDayCaptureStore(db)
    pws_store = SameDayPWSDiagnosticStore(db)
    capture = _capture()
    assert capture_store.save(capture) is not None

    record = _record(capture, status=PWS_STATUS_NO_FRESH_QC, configured=True)
    pws_store.finalize(capture.capture_sha256, record)
    link = pws_store.link_for_capture(capture.capture_sha256)
    assert link is not None
    assert link["state"] == "UNAVAILABLE"
    assert link["failure_code"] is None


def test_restart_marks_pending_as_interrupted_and_never_backfills_later_sample(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    capture_store = SameDayCaptureStore(db)
    capture = _capture()
    assert capture_store.save(capture) is not None

    restarted = SameDayPWSDiagnosticStore(db)
    assert restarted.mark_interrupted_pending(completed_at=float(capture.as_of) + 10.0) == 1
    link = restarted.link_for_capture(capture.capture_sha256)
    assert link is not None
    assert link["state"] == "INTERRUPTED"
    assert link["failure_code"] == "PWS_COLLECTION_INTERRUPTED_BEFORE_OUTCOME"

    with pytest.raises(PWSDiagnosticStoreError) as raised:
        restarted.finalize(capture.capture_sha256, _record(capture))
    assert raised.value.code == "PWS_STORE_CAPTURE_LINK_NOT_PENDING"
    assert restarted.summary()["total"] == 0
    assert restarted.summary()["capture_links_interrupted"] == 1


def test_explicit_pws_failure_is_durable_and_nonfinancial(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    capture_store = SameDayCaptureStore(db)
    pws_store = SameDayPWSDiagnosticStore(db)
    capture = _capture()
    assert capture_store.save(capture) is not None

    pws_store.record_failure(capture.capture_sha256, "PWS_TIMEOUT")
    link = pws_store.link_for_capture(capture.capture_sha256)
    assert link is not None
    assert link["state"] == "FAILED"
    assert link["failure_code"] == "PWS_TIMEOUT"
    assert link["included_in_validated_pnl"] == 0
    assert link["same_day_delivery_enabled"] == 0
    assert link["financial_authority"] == 0
