from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from polymarket_scanner.weather_calibration_service_preflight import (
    WeatherCalibrationServicePreflightError,
    run_service_preflight,
)


ROOT = Path(__file__).resolve().parents[1]
GOOD_SHA = "a" * 40


def _release_file(tmp_path: Path, value: str = GOOD_SHA) -> Path:
    path = tmp_path / "release.sha"
    path.write_text(value + "\n", encoding="utf-8")
    return path


def test_service_preflight_is_local_read_only_and_policy_unfrozen(tmp_path):
    report = run_service_preflight(
        app_dir=ROOT,
        release_file=_release_file(tmp_path),
        db_path=tmp_path / "weather-calibration.sqlite",
    )
    assert report["ok"] is True
    assert report["release_sha"] == GOOD_SHA
    assert report["runtime_source"].endswith("polymarket_scanner/weather_only_calibration_worker_runtime.py")
    assert len(report["experiment_manifest_sha256"]) == 64
    assert report["statistical_policy_status"].startswith("UNFROZEN")
    assert report["statistical_policy_id"] is None
    assert report["database"]["exists"] is False
    assert report["network_requests"] is False
    assert report["database_mutation"] is False
    assert report["telegram_delivery"] is False
    assert report["financial_authority"] is False
    assert report["financial_delivery"] is False
    assert report["automatic_order_placement"] is False


def test_service_preflight_rejects_malformed_release_marker(tmp_path):
    with pytest.raises(WeatherCalibrationServicePreflightError) as raised:
        run_service_preflight(
            app_dir=ROOT,
            release_file=_release_file(tmp_path, "not-a-sha"),
            db_path=tmp_path / "weather-calibration.sqlite",
        )
    assert raised.value.code == "SERVICE_RELEASE_SHA_INVALID"


def test_service_preflight_rejects_runtime_imported_outside_declared_checkout(tmp_path):
    fake_app = tmp_path / "fake-app"
    fake_app.mkdir()
    with pytest.raises(WeatherCalibrationServicePreflightError) as raised:
        run_service_preflight(
            app_dir=fake_app,
            release_file=_release_file(tmp_path),
            db_path=tmp_path / "weather-calibration.sqlite",
        )
    assert raised.value.code == "SERVICE_RUNTIME_OUTSIDE_RELEASE_CHECKOUT"


def test_service_preflight_rejects_existing_database_with_broad_permissions(tmp_path):
    db_path = tmp_path / "weather-calibration.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE weather_calibration_worker_events (event_id TEXT PRIMARY KEY)")
    db_path.chmod(0o644)
    with pytest.raises(WeatherCalibrationServicePreflightError) as raised:
        run_service_preflight(
            app_dir=ROOT,
            release_file=_release_file(tmp_path),
            db_path=db_path,
        )
    assert raised.value.code == "SERVICE_DB_PERMISSIONS_TOO_BROAD"


def test_service_preflight_rejects_partial_existing_schema(tmp_path):
    db_path = tmp_path / "weather-calibration.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE unrelated (id INTEGER PRIMARY KEY)")
    db_path.chmod(0o600)
    with pytest.raises(WeatherCalibrationServicePreflightError) as raised:
        run_service_preflight(
            app_dir=ROOT,
            release_file=_release_file(tmp_path),
            db_path=db_path,
        )
    assert raised.value.code == "SERVICE_DB_SCHEMA_PARTIAL"


def test_service_preflight_accepts_empty_new_sqlite_file_only_if_integrity_is_clean(tmp_path):
    db_path = tmp_path / "weather-calibration.sqlite"
    with sqlite3.connect(db_path):
        pass
    db_path.chmod(0o600)
    report = run_service_preflight(
        app_dir=ROOT,
        release_file=_release_file(tmp_path),
        db_path=db_path,
    )
    assert report["database"]["exists"] is True
    assert report["database"]["integrity"] == "ok"
    assert report["database"]["table_count"] == 0
    assert report["database_mutation"] is False
