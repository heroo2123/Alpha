from __future__ import annotations

"""Local, read-only preflight for the prospective weather calibration service.

This check is deliberately narrower than the live network preflight. It runs before a
future systemd service start and verifies that the imported package comes from the
release checkout, the immutable release marker is well formed, collection/model/source
policy identities are internally consistent, no financial authority has leaked into
the experiment manifest, and an existing evidence database passes SQLite integrity and
permission checks.

It never creates or migrates a database, never contacts Gamma/GEFS/WRH/CLOB/Telegram,
and never grants trading authority.
"""

import argparse
import inspect
import json
import re
import sqlite3
from pathlib import Path

from . import weather_only_calibration_worker_runtime as runtime_module
from .weather_calibration_experiment import (
    WeatherCalibrationExperimentError,
    build_weather_calibration_experiment_manifest,
    validate_weather_calibration_experiment_manifest,
)


WEATHER_CALIBRATION_SERVICE_PREFLIGHT_VERSION = "weather_calibration_service_preflight_v2_release_package_db_policy_integrity"
_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


class WeatherCalibrationServicePreflightError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _release_sha(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise WeatherCalibrationServicePreflightError("SERVICE_RELEASE_MARKER_INVALID")
    value = path.read_text(encoding="utf-8").strip().lower()
    if not _SHA_RE.fullmatch(value):
        raise WeatherCalibrationServicePreflightError("SERVICE_RELEASE_SHA_INVALID")
    return value


def _require_module_under_app(app_dir: Path) -> str:
    if not app_dir.is_absolute() or not app_dir.is_dir():
        raise WeatherCalibrationServicePreflightError("SERVICE_APP_DIR_INVALID")
    source = inspect.getsourcefile(runtime_module)
    if not source:
        raise WeatherCalibrationServicePreflightError("SERVICE_RUNTIME_SOURCE_UNRESOLVED")
    source_path = Path(source).resolve()
    root = app_dir.resolve()
    try:
        source_path.relative_to(root)
    except ValueError:
        raise WeatherCalibrationServicePreflightError("SERVICE_RUNTIME_OUTSIDE_RELEASE_CHECKOUT") from None
    expected_suffix = Path("polymarket_scanner/weather_only_calibration_worker_runtime.py")
    if source_path.relative_to(root) != expected_suffix:
        raise WeatherCalibrationServicePreflightError("SERVICE_RUNTIME_ENTRYPOINT_IDENTITY_MISMATCH")
    return str(source_path)


def _database_preflight(path: Path) -> dict:
    if not path.is_absolute():
        raise WeatherCalibrationServicePreflightError("SERVICE_DB_PATH_NOT_ABSOLUTE")
    parent = path.parent
    if not parent.is_dir() or parent.is_symlink():
        raise WeatherCalibrationServicePreflightError("SERVICE_DB_PARENT_INVALID")
    if not path.exists():
        return {
            "exists": False,
            "integrity": "NOT_CREATED_YET",
            "read_only_check": True,
        }
    if path.is_symlink() or not path.is_file():
        raise WeatherCalibrationServicePreflightError("SERVICE_DB_FILE_INVALID")
    mode = path.stat().st_mode & 0o777
    if mode & 0o077:
        raise WeatherCalibrationServicePreflightError("SERVICE_DB_PERMISSIONS_TOO_BROAD")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        with sqlite3.connect(uri, uri=True, timeout=5.0) as db:
            integrity = db.execute("PRAGMA integrity_check").fetchone()
            if integrity is None or str(integrity[0]).lower() != "ok":
                raise WeatherCalibrationServicePreflightError("SERVICE_DB_INTEGRITY_FAILED")
            tables = {
                str(row[0])
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
    except WeatherCalibrationServicePreflightError:
        raise
    except sqlite3.Error:
        raise WeatherCalibrationServicePreflightError("SERVICE_DB_READ_FAILED") from None

    required = {"weather_calibration_worker_events", "wrh_collector_captures", "wrh_collector_keys", "wrh_collector_snapshots"}
    if tables and not required.issubset(tables):
        raise WeatherCalibrationServicePreflightError("SERVICE_DB_SCHEMA_PARTIAL")
    return {
        "exists": True,
        "integrity": "ok",
        "mode_octal": format(mode, "04o"),
        "table_count": len(tables),
        "read_only_check": True,
    }


def run_service_preflight(*, app_dir: str | Path, release_file: str | Path, db_path: str | Path) -> dict:
    app = Path(app_dir)
    release = Path(release_file)
    database = Path(db_path)
    release_sha = _release_sha(release)
    runtime_source = _require_module_under_app(app)
    try:
        manifest = validate_weather_calibration_experiment_manifest(
            build_weather_calibration_experiment_manifest()
        )
    except WeatherCalibrationExperimentError as exc:
        raise WeatherCalibrationServicePreflightError(f"SERVICE_EXPERIMENT:{exc.code}") from exc
    db_report = _database_preflight(database)
    return {
        "version": WEATHER_CALIBRATION_SERVICE_PREFLIGHT_VERSION,
        "ok": True,
        "release_sha": release_sha,
        "runtime_source": runtime_source,
        "experiment_manifest_sha256": manifest.manifest_sha256,
        "capture_policy_id": manifest.capture_policy_id,
        "statistical_policy_status": manifest.statistical_policy_status,
        "statistical_policy_id": manifest.statistical_policy_id,
        "statistical_policy_sha256": manifest.statistical_policy_sha256,
        "calibrated_probability_authority": False,
        "database": db_report,
        "network_requests": False,
        "database_mutation": False,
        "telegram_delivery": False,
        "financial_authority": False,
        "financial_delivery": False,
        "automatic_order_placement": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--release-file", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = run_service_preflight(
            app_dir=args.app_dir,
            release_file=args.release_file,
            db_path=args.db,
        )
    except WeatherCalibrationServicePreflightError as exc:
        print(json.dumps({
            "version": WEATHER_CALIBRATION_SERVICE_PREFLIGHT_VERSION,
            "ok": False,
            "error": exc.code,
            "calibrated_probability_authority": False,
            "financial_authority": False,
        }, sort_keys=True))
        raise SystemExit(2)
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
