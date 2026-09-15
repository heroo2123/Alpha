from __future__ import annotations

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from test_weather_only_three_layer_finalization import _valid_status


RELEASE = "a" * 40
DIGEST = "b" * 64


def _module():
    path = Path("deploy/verify-three-layer-fresh-capture.py")
    spec = importlib.util.spec_from_file_location("fresh_capture_durable_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _audit_db(path: Path, *, attempted_at: float, event_mismatch: bool = False) -> None:
    with sqlite3.connect(path) as db:
        db.executescript(
            """
            CREATE TABLE weather_same_day_captures(
                id INTEGER PRIMARY KEY,
                capture_sha256 TEXT NOT NULL UNIQUE,
                event_id TEXT NOT NULL,
                station TEXT NOT NULL,
                target_date TEXT NOT NULL,
                family TEXT NOT NULL,
                unit TEXT NOT NULL,
                as_of REAL NOT NULL
            );
            CREATE TABLE weather_same_day_capture_attempts(
                id INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL,
                station TEXT NOT NULL,
                target_date TEXT NOT NULL,
                family TEXT NOT NULL,
                unit TEXT NOT NULL,
                attempted_at REAL NOT NULL,
                completed_at REAL,
                outcome TEXT NOT NULL,
                capture_sha256 TEXT
            );
            """
        )
        db.execute(
            """
            INSERT INTO weather_same_day_captures(
                id,capture_sha256,event_id,station,target_date,family,unit,as_of
            ) VALUES(1,?,?,?,?,?,?,?)
            """,
            (DIGEST, "event-1", "KLGA", "2026-09-15", "DAILY_HIGH", "F", attempted_at + 0.4),
        )
        db.execute(
            """
            INSERT INTO weather_same_day_capture_attempts(
                id,event_id,station,target_date,family,unit,attempted_at,
                completed_at,outcome,capture_sha256
            ) VALUES(1,?,?,?,?,?,?,?,?,?)
            """,
            (
                "different-event" if event_mismatch else "event-1",
                "KLGA",
                "2026-09-15",
                "DAILY_HIGH",
                "F",
                attempted_at,
                attempted_at + 0.5,
                "SAVED",
                DIGEST,
            ),
        )


def _cadence_status():
    status = _valid_status()
    lane = status["same_day_three_layer"]
    lane["attempted_now"] = 0
    lane["saved_now"] = 0
    lane["duplicates_now"] = 0
    lane["cadence_skipped_now"] = 4
    lane["blocked_now"] = 0
    lane["ready_uncalibrated_now"] = 0
    return status


def test_durable_saved_capture_survives_later_cadence_skipped_status_cycle(tmp_path):
    db = tmp_path / "weather.sqlite"
    _audit_db(db, attempted_at=1000.1)
    module = _module()
    result = module.verify_fresh_capture(
        _cadence_status(),
        release_sha=RELEASE,
        not_before=1000.0,
        now=1001.0,
        max_age_seconds=600.0,
        db_path=db,
    )
    assert result["acceptance"] == "PASS_THREE_LAYER_FRESH_CAPTURE_AFTER_START"
    assert result["durable_capture_audit_used"] is True
    assert result["durable_saved_capture"]["capture_sha256"] == DIGEST
    assert result["attempted_now"] == 0
    assert result["saved_now"] == 0


def test_durable_capture_from_before_candidate_start_cannot_grant_persistence(tmp_path):
    db = tmp_path / "weather.sqlite"
    _audit_db(db, attempted_at=999.0)
    module = _module()
    with pytest.raises(Exception, match="THREE_LAYER_FRESH_NO_DURABLE_SAVED_CAPTURE_AFTER_START"):
        module.verify_fresh_capture(
            _cadence_status(),
            release_sha=RELEASE,
            not_before=1000.0,
            now=1001.0,
            max_age_seconds=600.0,
            db_path=db,
        )


def test_durable_attempt_capture_identity_mismatch_fails_closed(tmp_path):
    db = tmp_path / "weather.sqlite"
    _audit_db(db, attempted_at=1000.1, event_mismatch=True)
    module = _module()
    with pytest.raises(Exception, match="THREE_LAYER_FRESH_DURABLE_IDENTITY_MISMATCH"):
        module.verify_fresh_capture(
            _cadence_status(),
            release_sha=RELEASE,
            not_before=1000.0,
            now=1001.0,
            max_age_seconds=600.0,
            db_path=db,
        )
