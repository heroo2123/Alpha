from __future__ import annotations

"""Cryptographic lineage for the preregistered prospective forecast horizon.

The generic prospective forecast/rule capture proves what was known and when it was
frozen. Calibration for this experiment additionally requires *why that timestamp was
eligible*: exactly one 15-minute window beginning at 17:00 in the settlement station's
local timezone on T-1.

This module binds that operational policy to the capture digest and persisted station
location identity. The evidence is separate from the generic capture so lower-level
collector diagnostics remain reusable. The strict calibration reader requires this
envelope; a direct/manual collector registration without it is not calibration data.
"""

import hashlib
import json
import math
import re
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time as wall_time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from .weather_only_calibration_capture import (
    ProspectiveWeatherCalibrationCapture,
    WeatherCalibrationCaptureError,
    _validate_capture,
)
from .weather_only_wrh_collector import _capture_from_dict


CAPTURE_HORIZON_EVIDENCE_VERSION = "weather_capture_horizon_evidence_v1_station_local_tminus1"
CAPTURE_HORIZON_POLICY_ID = "weather_gefs_station_local_tminus1_1700_window15m_v1"
CAPTURE_HORIZON_LOCAL_HOUR = 17
CAPTURE_HORIZON_LOCAL_MINUTE = 0
CAPTURE_HORIZON_WINDOW_SECONDS = 15 * 60


class WeatherCalibrationHorizonError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _canonical_json(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _hash_payload(value: object) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _sha256(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", text):
        raise WeatherCalibrationHorizonError(code)
    return text


def _timestamp(value: object, code: str) -> float:
    if value is None or isinstance(value, bool) or not isinstance(value, (int, float)):
        raise WeatherCalibrationHorizonError(code)
    number = float(value)
    if not math.isfinite(number) or number < 0.0:
        raise WeatherCalibrationHorizonError(code)
    return number


def _capture_window(target_date: date, timezone_name: str) -> tuple[float, float]:
    if type(target_date) is not date:
        raise WeatherCalibrationHorizonError("HORIZON_TARGET_DATE_INVALID")
    timezone_text = str(timezone_name or "").strip()
    try:
        zone = ZoneInfo(timezone_text)
    except ZoneInfoNotFoundError:
        raise WeatherCalibrationHorizonError("HORIZON_TIMEZONE_INVALID") from None
    start = datetime.combine(
        target_date - timedelta(days=1),
        wall_time(CAPTURE_HORIZON_LOCAL_HOUR, CAPTURE_HORIZON_LOCAL_MINUTE),
        tzinfo=zone,
    )
    end = start + timedelta(seconds=CAPTURE_HORIZON_WINDOW_SECONDS)
    return start.timestamp(), end.timestamp()


@dataclass(frozen=True, slots=True)
class ProspectiveCaptureHorizonEvidence:
    evidence_version: str
    capture_policy_id: str
    capture_evidence_sha256: str
    prediction_evidence_sha256: str
    event_id: str
    market_id: str
    station: str
    target_date: date
    family: str
    model_version: str
    station_timezone: str
    station_metadata_evidence_sha256: str
    captured_at: float
    window_start_at: float
    window_end_at: float
    evidence_sha256: str
    horizon_authority: bool = field(init=False, default=True)
    calibration_label_authority: bool = field(init=False, default=False)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        payload = asdict(self)
        payload["target_date"] = self.target_date.isoformat()
        return payload


def _digest_payload(evidence: ProspectiveCaptureHorizonEvidence) -> dict:
    return {
        "evidence_version": evidence.evidence_version,
        "capture_policy_id": evidence.capture_policy_id,
        "capture_evidence_sha256": evidence.capture_evidence_sha256,
        "prediction_evidence_sha256": evidence.prediction_evidence_sha256,
        "event_id": evidence.event_id,
        "market_id": evidence.market_id,
        "station": evidence.station,
        "target_date": evidence.target_date.isoformat(),
        "family": evidence.family,
        "model_version": evidence.model_version,
        "station_timezone": evidence.station_timezone,
        "station_metadata_evidence_sha256": evidence.station_metadata_evidence_sha256,
        "captured_at": evidence.captured_at,
        "window_start_at": evidence.window_start_at,
        "window_end_at": evidence.window_end_at,
    }


def build_capture_horizon_evidence(
    capture: ProspectiveWeatherCalibrationCapture,
    *,
    station_timezone: str,
    station_metadata_evidence_sha256: str,
    capture_policy_id: str = CAPTURE_HORIZON_POLICY_ID,
) -> ProspectiveCaptureHorizonEvidence:
    try:
        frozen = _validate_capture(capture)
    except WeatherCalibrationCaptureError as exc:
        raise WeatherCalibrationHorizonError(f"HORIZON_CAPTURE_INVALID:{exc.code}") from exc
    if str(capture_policy_id) != CAPTURE_HORIZON_POLICY_ID:
        raise WeatherCalibrationHorizonError("HORIZON_CAPTURE_POLICY_MISMATCH")
    timezone_text = str(station_timezone or "").strip()
    start, end = _capture_window(frozen.prediction.target_date, timezone_text)
    captured = _timestamp(frozen.captured_at, "HORIZON_CAPTURE_TIMESTAMP_INVALID")
    if captured < start or captured >= end:
        raise WeatherCalibrationHorizonError("HORIZON_CAPTURE_OUTSIDE_PREREGISTERED_WINDOW")
    metadata_sha = _sha256(
        station_metadata_evidence_sha256,
        "HORIZON_STATION_METADATA_EVIDENCE_SHA_INVALID",
    )
    shell = ProspectiveCaptureHorizonEvidence(
        evidence_version=CAPTURE_HORIZON_EVIDENCE_VERSION,
        capture_policy_id=CAPTURE_HORIZON_POLICY_ID,
        capture_evidence_sha256=frozen.capture_evidence_sha256,
        prediction_evidence_sha256=frozen.prediction.prediction_evidence_sha256,
        event_id=frozen.prediction.event_id,
        market_id=frozen.prediction.market_id,
        station=frozen.prediction.station.strip().upper(),
        target_date=frozen.prediction.target_date,
        family=frozen.prediction.family,
        model_version=frozen.prediction.model_version,
        station_timezone=timezone_text,
        station_metadata_evidence_sha256=metadata_sha,
        captured_at=captured,
        window_start_at=start,
        window_end_at=end,
        evidence_sha256="0" * 64,
    )
    return ProspectiveCaptureHorizonEvidence(
        **{
            name: getattr(shell, name)
            for name, definition in shell.__dataclass_fields__.items()
            if definition.init and name != "evidence_sha256"
        },
        evidence_sha256=_hash_payload(_digest_payload(shell)),
    )


def validate_capture_horizon_evidence(
    evidence: object,
    *,
    capture: ProspectiveWeatherCalibrationCapture | None = None,
) -> ProspectiveCaptureHorizonEvidence:
    if not isinstance(evidence, ProspectiveCaptureHorizonEvidence):
        raise WeatherCalibrationHorizonError("HORIZON_EVIDENCE_TYPE_INVALID")
    if evidence.evidence_version != CAPTURE_HORIZON_EVIDENCE_VERSION:
        raise WeatherCalibrationHorizonError("HORIZON_EVIDENCE_VERSION_MISMATCH")
    if evidence.capture_policy_id != CAPTURE_HORIZON_POLICY_ID:
        raise WeatherCalibrationHorizonError("HORIZON_CAPTURE_POLICY_MISMATCH")
    if evidence.horizon_authority is not True or evidence.calibration_label_authority is not False:
        raise WeatherCalibrationHorizonError("HORIZON_AUTHORITY_BOUNDARY_INVALID")
    if evidence.financial_authority is not False:
        raise WeatherCalibrationHorizonError("HORIZON_FINANCIAL_AUTHORITY_BOUNDARY_BROKEN")
    _sha256(evidence.capture_evidence_sha256, "HORIZON_CAPTURE_EVIDENCE_SHA_INVALID")
    _sha256(evidence.prediction_evidence_sha256, "HORIZON_PREDICTION_EVIDENCE_SHA_INVALID")
    _sha256(evidence.station_metadata_evidence_sha256, "HORIZON_STATION_METADATA_EVIDENCE_SHA_INVALID")
    start, end = _capture_window(evidence.target_date, evidence.station_timezone)
    if abs(_timestamp(evidence.window_start_at, "HORIZON_WINDOW_START_INVALID") - start) > 1e-9:
        raise WeatherCalibrationHorizonError("HORIZON_WINDOW_START_MISMATCH")
    if abs(_timestamp(evidence.window_end_at, "HORIZON_WINDOW_END_INVALID") - end) > 1e-9:
        raise WeatherCalibrationHorizonError("HORIZON_WINDOW_END_MISMATCH")
    captured = _timestamp(evidence.captured_at, "HORIZON_CAPTURE_TIMESTAMP_INVALID")
    if captured < start or captured >= end:
        raise WeatherCalibrationHorizonError("HORIZON_CAPTURE_OUTSIDE_PREREGISTERED_WINDOW")
    supplied = _sha256(evidence.evidence_sha256, "HORIZON_EVIDENCE_SHA_INVALID")
    if supplied != _hash_payload(_digest_payload(evidence)):
        raise WeatherCalibrationHorizonError("HORIZON_EVIDENCE_DIGEST_MISMATCH")

    if capture is not None:
        try:
            frozen = _validate_capture(capture)
        except WeatherCalibrationCaptureError as exc:
            raise WeatherCalibrationHorizonError(f"HORIZON_CAPTURE_INVALID:{exc.code}") from exc
        expected = (
            frozen.capture_evidence_sha256,
            frozen.prediction.prediction_evidence_sha256,
            frozen.prediction.event_id,
            frozen.prediction.market_id,
            frozen.prediction.station.strip().upper(),
            frozen.prediction.target_date,
            frozen.prediction.family,
            frozen.prediction.model_version,
            float(frozen.captured_at),
        )
        actual = (
            evidence.capture_evidence_sha256,
            evidence.prediction_evidence_sha256,
            evidence.event_id,
            evidence.market_id,
            evidence.station,
            evidence.target_date,
            evidence.family,
            evidence.model_version,
            float(evidence.captured_at),
        )
        if actual != expected:
            raise WeatherCalibrationHorizonError("HORIZON_CAPTURE_IDENTITY_MISMATCH")
    return evidence


def _from_dict(payload: object) -> ProspectiveCaptureHorizonEvidence:
    if not isinstance(payload, dict):
        raise WeatherCalibrationHorizonError("HORIZON_PERSISTED_PAYLOAD_INVALID")
    target = payload.get("target_date")
    if not isinstance(target, str):
        raise WeatherCalibrationHorizonError("HORIZON_PERSISTED_TARGET_DATE_INVALID")
    try:
        target_date = date.fromisoformat(target)
    except ValueError:
        raise WeatherCalibrationHorizonError("HORIZON_PERSISTED_TARGET_DATE_INVALID") from None
    required = {
        name
        for name, definition in ProspectiveCaptureHorizonEvidence.__dataclass_fields__.items()
        if definition.init
    }
    if not required.issubset(payload):
        raise WeatherCalibrationHorizonError("HORIZON_PERSISTED_FIELD_MISSING")
    values: dict[str, Any] = {name: payload[name] for name in required}
    values["target_date"] = target_date
    try:
        return validate_capture_horizon_evidence(ProspectiveCaptureHorizonEvidence(**values))
    except (TypeError, ValueError) as exc:
        raise WeatherCalibrationHorizonError("HORIZON_PERSISTED_PAYLOAD_INVALID") from exc


def ensure_horizon_table(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS weather_calibration_capture_horizon (
            capture_evidence_sha256 TEXT PRIMARY KEY,
            event_id TEXT NOT NULL,
            station TEXT NOT NULL,
            target_date TEXT NOT NULL,
            capture_policy_id TEXT NOT NULL,
            station_timezone TEXT NOT NULL,
            evidence_sha256 TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at REAL NOT NULL
        )
        """
    )


def persist_capture_horizon_evidence(
    db: sqlite3.Connection,
    evidence: ProspectiveCaptureHorizonEvidence,
    *,
    created_at: float,
) -> str:
    valid = validate_capture_horizon_evidence(evidence)
    created = _timestamp(created_at, "HORIZON_CREATED_AT_INVALID")
    payload = _canonical_json(valid.as_dict())
    ensure_horizon_table(db)
    existing = db.execute(
        "SELECT evidence_sha256, evidence_json FROM weather_calibration_capture_horizon WHERE capture_evidence_sha256 = ?",
        (valid.capture_evidence_sha256,),
    ).fetchone()
    if existing is not None:
        if str(existing[0]) != valid.evidence_sha256 or str(existing[1]) != payload:
            raise WeatherCalibrationHorizonError("HORIZON_PERSISTED_COLLISION")
        return "EXISTS"
    db.execute(
        """
        INSERT INTO weather_calibration_capture_horizon (
            capture_evidence_sha256, event_id, station, target_date,
            capture_policy_id, station_timezone, evidence_sha256,
            evidence_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            valid.capture_evidence_sha256,
            valid.event_id,
            valid.station,
            valid.target_date.isoformat(),
            valid.capture_policy_id,
            valid.station_timezone,
            valid.evidence_sha256,
            payload,
            created,
        ),
    )
    return "CREATED"


def attest_registered_worker_horizons(db: sqlite3.Connection, *, created_at: float) -> dict:
    """Create missing horizon attestations only from durable registered worker rows."""
    ensure_horizon_table(db)
    try:
        rows = db.execute(
            """
            SELECT w.event_id, w.capture_policy_id, w.capture_evidence_sha256,
                   w.prediction_evidence_sha256, w.model_version, w.station,
                   w.target_date, w.family, w.captured_at,
                   w.station_metadata_evidence_sha256, w.station_metadata_json,
                   w.status, c.capture_json
            FROM weather_calibration_worker_events AS w
            JOIN wrh_collector_captures AS c
              ON c.capture_evidence_sha256 = w.capture_evidence_sha256
            LEFT JOIN weather_calibration_capture_horizon AS h
              ON h.capture_evidence_sha256 = w.capture_evidence_sha256
            WHERE w.status = 'REGISTERED'
              AND h.capture_evidence_sha256 IS NULL
            ORDER BY w.event_id
            """
        ).fetchall()
    except sqlite3.Error as exc:
        raise WeatherCalibrationHorizonError("HORIZON_WORKER_SCHEMA_INVALID") from exc

    created_count = 0
    errors: dict[str, int] = {}
    for row in rows:
        try:
            capture_payload = json.loads(str(row["capture_json"]))
            capture = _capture_from_dict(capture_payload)
            metadata = json.loads(str(row["station_metadata_json"]))
            if not isinstance(metadata, dict):
                raise WeatherCalibrationHorizonError("HORIZON_STATION_METADATA_JSON_INVALID")
            if str(row["capture_policy_id"]) != CAPTURE_HORIZON_POLICY_ID:
                raise WeatherCalibrationHorizonError("HORIZON_WORKER_CAPTURE_POLICY_MISMATCH")
            if str(metadata.get("station") or "").strip().upper() != str(row["station"]).strip().upper():
                raise WeatherCalibrationHorizonError("HORIZON_WORKER_STATION_METADATA_MISMATCH")
            if str(metadata.get("evidence_sha256") or "") != str(row["station_metadata_evidence_sha256"]):
                raise WeatherCalibrationHorizonError("HORIZON_WORKER_STATION_METADATA_DIGEST_MISMATCH")
            if metadata.get("financial_authority") is not False:
                raise WeatherCalibrationHorizonError("HORIZON_WORKER_STATION_METADATA_AUTHORITY_BROKEN")
            evidence = build_capture_horizon_evidence(
                capture,
                station_timezone=str(metadata.get("timezone") or ""),
                station_metadata_evidence_sha256=str(row["station_metadata_evidence_sha256"]),
                capture_policy_id=str(row["capture_policy_id"]),
            )
            if (
                evidence.event_id != str(row["event_id"])
                or evidence.prediction_evidence_sha256 != str(row["prediction_evidence_sha256"])
                or evidence.model_version != str(row["model_version"])
                or evidence.target_date.isoformat() != str(row["target_date"])
                or evidence.family != str(row["family"])
                or abs(evidence.captured_at - float(row["captured_at"])) > 1e-9
            ):
                raise WeatherCalibrationHorizonError("HORIZON_WORKER_ROW_IDENTITY_MISMATCH")
            persist_capture_horizon_evidence(db, evidence, created_at=created_at)
            created_count += 1
        except (WeatherCalibrationHorizonError, WeatherCalibrationCaptureError, json.JSONDecodeError) as exc:
            code = getattr(exc, "code", type(exc).__name__)
            errors[str(code)] = errors.get(str(code), 0) + 1
    db.commit()
    return {
        "eligible_registered_rows": len(rows),
        "created_horizon_attestations": created_count,
        "errors": dict(sorted(errors.items())),
        "financial_authority": False,
    }


def read_capture_horizon_evidence(
    db: sqlite3.Connection,
    capture: ProspectiveWeatherCalibrationCapture,
) -> ProspectiveCaptureHorizonEvidence:
    try:
        row = db.execute(
            """
            SELECT capture_evidence_sha256, event_id, station, target_date,
                   capture_policy_id, station_timezone, evidence_sha256,
                   evidence_json, created_at
            FROM weather_calibration_capture_horizon
            WHERE capture_evidence_sha256 = ?
            """,
            (capture.capture_evidence_sha256,),
        ).fetchone()
    except sqlite3.Error:
        raise WeatherCalibrationHorizonError("HORIZON_EVIDENCE_TABLE_MISSING") from None
    if row is None:
        raise WeatherCalibrationHorizonError("HORIZON_EVIDENCE_MISSING")
    try:
        payload = json.loads(str(row["evidence_json"]))
    except json.JSONDecodeError:
        raise WeatherCalibrationHorizonError("HORIZON_PERSISTED_JSON_INVALID") from None
    evidence = _from_dict(payload)
    validate_capture_horizon_evidence(evidence, capture=capture)
    if str(row["capture_evidence_sha256"]) != evidence.capture_evidence_sha256:
        raise WeatherCalibrationHorizonError("HORIZON_CAPTURE_SHA_METADATA_MISMATCH")
    if str(row["event_id"]) != evidence.event_id or str(row["station"]) != evidence.station:
        raise WeatherCalibrationHorizonError("HORIZON_IDENTITY_METADATA_MISMATCH")
    if str(row["target_date"]) != evidence.target_date.isoformat():
        raise WeatherCalibrationHorizonError("HORIZON_DATE_METADATA_MISMATCH")
    if str(row["capture_policy_id"]) != evidence.capture_policy_id:
        raise WeatherCalibrationHorizonError("HORIZON_POLICY_METADATA_MISMATCH")
    if str(row["station_timezone"]) != evidence.station_timezone:
        raise WeatherCalibrationHorizonError("HORIZON_TIMEZONE_METADATA_MISMATCH")
    if str(row["evidence_sha256"]) != evidence.evidence_sha256:
        raise WeatherCalibrationHorizonError("HORIZON_SHA_METADATA_MISMATCH")
    _timestamp(row["created_at"], "HORIZON_CREATED_AT_INVALID")
    return evidence
