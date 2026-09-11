from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from polymarket_scanner.weather_only_calibration_horizon import (
    CAPTURE_HORIZON_POLICY_ID,
    WeatherCalibrationHorizonError,
    attest_registered_worker_horizons,
    build_capture_horizon_evidence,
    persist_capture_horizon_evidence,
    read_capture_horizon_evidence,
    validate_capture_horizon_evidence,
)

from test_weather_only_calibration_reader import (
    CAPTURED,
    STATION_METADATA_EVIDENCE_SHA256,
    TARGET,
    _capture,
)


def test_horizon_evidence_binds_capture_to_exact_station_local_tminus1_window():
    capture = _capture()
    evidence = build_capture_horizon_evidence(
        capture,
        station_timezone="America/New_York",
        station_metadata_evidence_sha256=STATION_METADATA_EVIDENCE_SHA256,
    )
    expected_start = datetime(2026, 9, 10, 17, 0, tzinfo=ZoneInfo("America/New_York")).timestamp()
    expected_end = datetime(2026, 9, 10, 17, 15, tzinfo=ZoneInfo("America/New_York")).timestamp()
    assert evidence.capture_policy_id == CAPTURE_HORIZON_POLICY_ID
    assert evidence.capture_evidence_sha256 == capture.capture_evidence_sha256
    assert evidence.prediction_evidence_sha256 == capture.prediction.prediction_evidence_sha256
    assert evidence.captured_at == CAPTURED
    assert evidence.window_start_at == expected_start
    assert evidence.window_end_at == expected_end
    assert evidence.horizon_authority is True
    assert evidence.calibration_label_authority is False
    assert evidence.financial_authority is False
    assert validate_capture_horizon_evidence(evidence, capture=capture) == evidence


def test_same_capture_cannot_be_relabelled_as_different_station_timezone():
    capture = _capture()
    with pytest.raises(WeatherCalibrationHorizonError) as raised:
        build_capture_horizon_evidence(
            capture,
            station_timezone="UTC",
            station_metadata_evidence_sha256=STATION_METADATA_EVIDENCE_SHA256,
        )
    assert raised.value.code == "HORIZON_CAPTURE_OUTSIDE_PREREGISTERED_WINDOW"


def test_horizon_persistence_is_idempotent_and_reader_revalidates_payload(tmp_path):
    db_path = tmp_path / "horizon.sqlite"
    capture = _capture()
    evidence = build_capture_horizon_evidence(
        capture,
        station_timezone="America/New_York",
        station_metadata_evidence_sha256=STATION_METADATA_EVIDENCE_SHA256,
    )
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        assert persist_capture_horizon_evidence(db, evidence, created_at=CAPTURED + 1) == "CREATED"
        assert persist_capture_horizon_evidence(db, evidence, created_at=CAPTURED + 2) == "EXISTS"
        db.commit()
        rebuilt = read_capture_horizon_evidence(db, capture)
    assert rebuilt == evidence


def test_registered_worker_attester_builds_horizon_only_from_bound_worker_and_capture_rows(tmp_path):
    db_path = tmp_path / "worker-horizon.sqlite"
    capture = _capture()
    station_metadata = {
        "station": "KLGA",
        "timezone": "America/New_York",
        "evidence_sha256": STATION_METADATA_EVIDENCE_SHA256,
        "financial_authority": False,
    }
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            CREATE TABLE weather_calibration_worker_events (
                event_id TEXT PRIMARY KEY,
                capture_policy_id TEXT NOT NULL,
                capture_evidence_sha256 TEXT NOT NULL,
                prediction_evidence_sha256 TEXT NOT NULL,
                model_version TEXT NOT NULL,
                station TEXT NOT NULL,
                target_date TEXT NOT NULL,
                family TEXT NOT NULL,
                captured_at REAL NOT NULL,
                station_metadata_evidence_sha256 TEXT NOT NULL,
                station_metadata_json TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE wrh_collector_captures (
                capture_evidence_sha256 TEXT PRIMARY KEY,
                capture_json TEXT NOT NULL
            );
            """
        )
        db.execute(
            """
            INSERT INTO weather_calibration_worker_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                capture.prediction.event_id,
                CAPTURE_HORIZON_POLICY_ID,
                capture.capture_evidence_sha256,
                capture.prediction.prediction_evidence_sha256,
                capture.prediction.model_version,
                capture.prediction.station,
                TARGET.isoformat(),
                capture.prediction.family,
                CAPTURED,
                STATION_METADATA_EVIDENCE_SHA256,
                json.dumps(station_metadata, sort_keys=True, separators=(",", ":")),
                "REGISTERED",
            ),
        )
        db.execute(
            "INSERT INTO wrh_collector_captures VALUES (?, ?)",
            (
                capture.capture_evidence_sha256,
                json.dumps(capture.as_dict(), sort_keys=True, separators=(",", ":")),
            ),
        )
        report = attest_registered_worker_horizons(db, created_at=CAPTURED + 2)
        assert report["eligible_registered_rows"] == 1
        assert report["created_horizon_attestations"] == 1
        assert report["errors"] == {}
        rebuilt = read_capture_horizon_evidence(db, capture)
    assert rebuilt.capture_policy_id == CAPTURE_HORIZON_POLICY_ID
    assert rebuilt.station_timezone == "America/New_York"


def test_registered_worker_attester_rejects_policy_drift(tmp_path):
    db_path = tmp_path / "worker-policy-drift.sqlite"
    capture = _capture()
    station_metadata = {
        "station": "KLGA",
        "timezone": "America/New_York",
        "evidence_sha256": STATION_METADATA_EVIDENCE_SHA256,
        "financial_authority": False,
    }
    with sqlite3.connect(db_path) as db:
        db.row_factory = sqlite3.Row
        db.executescript(
            """
            CREATE TABLE weather_calibration_worker_events (
                event_id TEXT PRIMARY KEY, capture_policy_id TEXT NOT NULL,
                capture_evidence_sha256 TEXT NOT NULL, prediction_evidence_sha256 TEXT NOT NULL,
                model_version TEXT NOT NULL, station TEXT NOT NULL, target_date TEXT NOT NULL,
                family TEXT NOT NULL, captured_at REAL NOT NULL,
                station_metadata_evidence_sha256 TEXT NOT NULL, station_metadata_json TEXT NOT NULL,
                status TEXT NOT NULL
            );
            CREATE TABLE wrh_collector_captures (
                capture_evidence_sha256 TEXT PRIMARY KEY, capture_json TEXT NOT NULL
            );
            """
        )
        db.execute(
            "INSERT INTO weather_calibration_worker_events VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                capture.prediction.event_id,
                "drifted-policy-v2",
                capture.capture_evidence_sha256,
                capture.prediction.prediction_evidence_sha256,
                capture.prediction.model_version,
                capture.prediction.station,
                TARGET.isoformat(),
                capture.prediction.family,
                CAPTURED,
                STATION_METADATA_EVIDENCE_SHA256,
                json.dumps(station_metadata, sort_keys=True, separators=(",", ":")),
                "REGISTERED",
            ),
        )
        db.execute(
            "INSERT INTO wrh_collector_captures VALUES (?, ?)",
            (capture.capture_evidence_sha256, json.dumps(capture.as_dict(), sort_keys=True, separators=(",", ":"))),
        )
        report = attest_registered_worker_horizons(db, created_at=CAPTURED + 2)
    assert report["created_horizon_attestations"] == 0
    assert report["errors"] == {"HORIZON_WORKER_CAPTURE_POLICY_MISMATCH": 1}
