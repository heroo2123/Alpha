from __future__ import annotations

"""Strict read-only reconstruction of authorized prospective weather calibration data.

Persisted ``authorized_json`` and ``settlement_evidence_json`` are audit copies, not
independent authority. This reader opens the collector SQLite database in read-only
mode, rehydrates and revalidates the frozen capture and every exact WRH snapshot,
independently reconstructs the unique valid cutoff bracket, reruns the full WRH
calibration authorization gate, and only then verifies that the stored audit copies
match the recomputed evidence.

One invalid or ambiguous authorized row fails the whole read. Silent row-skipping
would make a calibration dataset depend on post-outcome data quality and could create
selection bias. The returned records remain research calibration samples only and
never gain financial authority.
"""

import argparse
import json
import math
import sqlite3
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .weather_only_calibration_authority import (
    AuthorizedWRHCalibrationSample,
    WeatherCalibrationAuthorityError,
    authorize_wrh_calibration_sample,
)
from .weather_only_calibration_capture import (
    WeatherCalibrationCaptureError,
    WRHExactSettlementEvidence,
    build_wrh_exact_settlement_evidence,
)
from .weather_only_wrh import WRHSourceError, WRHSourceSnapshot
from .weather_only_wrh_collector import (
    CAPTURE_AUTHORIZED,
    _capture_from_dict,
    _snapshot_from_dict,
)


WEATHER_CALIBRATION_READER_VERSION = "weather_calibration_reader_v3_ro_recompute_label_provenance"


class WeatherCalibrationReaderError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def _json_object(value: object, code: str) -> dict:
    if not isinstance(value, str) or not value:
        raise WeatherCalibrationReaderError(code)
    try:
        payload = json.loads(value)
    except json.JSONDecodeError:
        raise WeatherCalibrationReaderError(code) from None
    if not isinstance(payload, dict):
        raise WeatherCalibrationReaderError(code)
    return payload


def _json_equivalent(value: object) -> object:
    """Normalize dataclass audit payloads exactly as canonical JSON persistence does.

    JSON storage intentionally converts tuples to arrays/lists. Comparing a parsed
    persisted envelope directly to an in-memory ``as_dict`` result would therefore
    create false mismatches even when every evidence digest and semantic field is
    identical. A canonical JSON round-trip removes only that representation artifact;
    it does not coerce numbers, drop fields, or bypass any lineage validation.
    """
    return json.loads(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True))


def _finite(value: object, code: str) -> float:
    if value is None or isinstance(value, bool):
        raise WeatherCalibrationReaderError(code)
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        raise WeatherCalibrationReaderError(code) from None
    if not math.isfinite(number) or number < 0.0:
        raise WeatherCalibrationReaderError(code)
    return number


def _read_only_connection(path: Path) -> sqlite3.Connection:
    if not path.exists() or not path.is_file():
        raise WeatherCalibrationReaderError("READER_DB_MISSING")
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    try:
        db = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error:
        raise WeatherCalibrationReaderError("READER_DB_OPEN_FAILED") from None
    db.row_factory = sqlite3.Row
    return db


def _bind_snapshot_row(row: sqlite3.Row) -> WRHSourceSnapshot:
    payload = _json_object(row["snapshot_json"], "READER_SNAPSHOT_JSON_INVALID")
    try:
        snapshot = _snapshot_from_dict(payload)
    except Exception as exc:
        code = getattr(exc, "code", type(exc).__name__)
        raise WeatherCalibrationReaderError(f"READER_SNAPSHOT_INVALID:{code}") from None
    if snapshot.station != str(row["station"]):
        raise WeatherCalibrationReaderError("READER_SNAPSHOT_STATION_METADATA_MISMATCH")
    if snapshot.target_date.isoformat() != str(row["target_date"]):
        raise WeatherCalibrationReaderError("READER_SNAPSHOT_DATE_METADATA_MISMATCH")
    if snapshot.evidence_sha256 != str(row["evidence_sha256"]):
        raise WeatherCalibrationReaderError("READER_SNAPSHOT_SHA_METADATA_MISMATCH")
    received = _finite(row["received_at"], "READER_SNAPSHOT_RECEIVED_AT_INVALID")
    if abs(received - float(snapshot.received_at)) > 1e-9:
        raise WeatherCalibrationReaderError("READER_SNAPSHOT_RECEIPT_METADATA_MISMATCH")
    return snapshot


def _unique_reauthorized_pair(
    capture,
    snapshots: list[WRHSourceSnapshot],
) -> tuple[WRHExactSettlementEvidence, AuthorizedWRHCalibrationSample]:
    successful: list[tuple[WRHExactSettlementEvidence, AuthorizedWRHCalibrationSample]] = []
    ordered = sorted(snapshots, key=lambda row: (float(row.received_at), row.evidence_sha256))
    for current in ordered:
        if current.first_following_row is None:
            continue
        following_timestamp = current.first_following_row.observation_time_local.timestamp()
        before = [
            snapshot
            for snapshot in ordered
            if snapshot.evidence_sha256 != current.evidence_sha256
            and snapshot.first_following_row is None
            and snapshot.received_at < following_timestamp
        ]
        if not before:
            continue
        previous = max(before, key=lambda row: (float(row.received_at), row.evidence_sha256))
        try:
            settlement = build_wrh_exact_settlement_evidence(capture, previous, current)
            authorized = authorize_wrh_calibration_sample(capture, settlement)
        except (WRHSourceError, WeatherCalibrationCaptureError, WeatherCalibrationAuthorityError):
            continue
        successful.append((settlement, authorized))

    if not successful:
        raise WeatherCalibrationReaderError("READER_NO_VALID_FINALITY_PAIR")
    if len(successful) != 1:
        raise WeatherCalibrationReaderError("READER_FINALITY_PAIR_AMBIGUOUS")
    return successful[0]


@dataclass(frozen=True, slots=True)
class ReconstructedCalibrationRecord:
    capture_evidence_sha256: str
    event_id: str
    market_id: str
    station: str
    target_date: str
    family: str
    model_version: str
    predicted_probability: float
    final_payout: float
    label_adapter: str
    source_role: str
    evidence_version: str
    label_authority: bool
    settlement_state_reconstructable: bool
    authority_evidence_sha256: str
    rule_evidence_sha256: str
    prediction_evidence_sha256: str
    finality_evidence_sha256: str
    settlement_bridge_evidence_sha256: str
    label_evidence_sha256: str
    source_recomputed: bool = field(init=False, default=True)
    stored_authorized_json_used_as_authority: bool = field(init=False, default=False)
    calibration_label_authority: bool = field(init=False, default=True)
    financial_authority: bool = field(init=False, default=False)

    def as_dict(self) -> dict:
        return asdict(self)


def _record_from_authorized(capture, authorized: AuthorizedWRHCalibrationSample) -> ReconstructedCalibrationRecord:
    sample = authorized.sample
    return ReconstructedCalibrationRecord(
        capture_evidence_sha256=capture.capture_evidence_sha256,
        event_id=capture.prediction.event_id,
        market_id=capture.prediction.market_id,
        station=capture.prediction.station,
        target_date=capture.prediction.target_date.isoformat(),
        family=capture.prediction.family,
        model_version=capture.prediction.model_version,
        predicted_probability=float(sample.predicted_probability),
        final_payout=float(sample.final_payout),
        label_adapter=str(sample.label_adapter),
        source_role=str(sample.source_role),
        evidence_version=str(sample.evidence_version),
        label_authority=bool(sample.label_authority),
        settlement_state_reconstructable=bool(sample.settlement_state_reconstructable),
        authority_evidence_sha256=authorized.authority_evidence_sha256,
        rule_evidence_sha256=authorized.rule_evidence_sha256,
        prediction_evidence_sha256=authorized.prediction_evidence_sha256,
        finality_evidence_sha256=authorized.finality_evidence_sha256,
        settlement_bridge_evidence_sha256=authorized.settlement_bridge_evidence_sha256,
        label_evidence_sha256=authorized.label_evidence_sha256,
    )


def read_reconstructed_calibration_dataset(path: str | Path) -> dict:
    db_path = Path(path)
    db = _read_only_connection(db_path)
    try:
        try:
            status_rows = db.execute(
                "SELECT status, COUNT(*) AS n FROM wrh_collector_captures GROUP BY status"
            ).fetchall()
            rows = db.execute(
                """
                SELECT capture_evidence_sha256, station, target_date, family,
                       status, capture_json, settlement_evidence_json, authorized_json
                FROM wrh_collector_captures
                WHERE status = ?
                ORDER BY target_date, station, capture_evidence_sha256
                """,
                (CAPTURE_AUTHORIZED,),
            ).fetchall()
        except sqlite3.Error:
            raise WeatherCalibrationReaderError("READER_SCHEMA_INVALID") from None

        records: list[ReconstructedCalibrationRecord] = []
        seen_events: set[str] = set()
        for row in rows:
            capture_payload = _json_object(row["capture_json"], "READER_CAPTURE_JSON_INVALID")
            try:
                capture = _capture_from_dict(capture_payload)
            except Exception as exc:
                code = getattr(exc, "code", type(exc).__name__)
                raise WeatherCalibrationReaderError(f"READER_CAPTURE_INVALID:{code}") from None
            if capture.capture_evidence_sha256 != str(row["capture_evidence_sha256"]):
                raise WeatherCalibrationReaderError("READER_CAPTURE_SHA_METADATA_MISMATCH")
            if capture.rule_evidence.station != str(row["station"]):
                raise WeatherCalibrationReaderError("READER_CAPTURE_STATION_METADATA_MISMATCH")
            if capture.rule_evidence.target_date.isoformat() != str(row["target_date"]):
                raise WeatherCalibrationReaderError("READER_CAPTURE_DATE_METADATA_MISMATCH")
            if capture.rule_evidence.family != str(row["family"]):
                raise WeatherCalibrationReaderError("READER_CAPTURE_FAMILY_METADATA_MISMATCH")
            if capture.prediction.event_id in seen_events:
                raise WeatherCalibrationReaderError("READER_DUPLICATE_EVENT_AUTHORIZATION")
            seen_events.add(capture.prediction.event_id)

            snapshot_rows = db.execute(
                """
                SELECT station, target_date, evidence_sha256, received_at, snapshot_json
                FROM wrh_collector_snapshots
                WHERE station = ? AND target_date = ?
                ORDER BY received_at, evidence_sha256
                """,
                (capture.rule_evidence.station, capture.rule_evidence.target_date.isoformat()),
            ).fetchall()
            snapshots = [_bind_snapshot_row(snapshot_row) for snapshot_row in snapshot_rows]
            settlement, authorized = _unique_reauthorized_pair(capture, snapshots)

            stored_settlement = _json_object(
                row["settlement_evidence_json"],
                "READER_STORED_SETTLEMENT_JSON_INVALID",
            )
            stored_authorized = _json_object(
                row["authorized_json"],
                "READER_STORED_AUTHORIZED_JSON_INVALID",
            )
            if stored_settlement != _json_equivalent(settlement.as_dict()):
                raise WeatherCalibrationReaderError("READER_STORED_SETTLEMENT_MISMATCH")
            if stored_authorized != _json_equivalent(authorized.as_dict()):
                raise WeatherCalibrationReaderError("READER_STORED_AUTHORIZED_MISMATCH")
            records.append(_record_from_authorized(capture, authorized))

        status_counts = Counter({str(row["status"]): int(row["n"]) for row in status_rows})
        return {
            "version": WEATHER_CALIBRATION_READER_VERSION,
            "read_only_database": True,
            "source_recomputed": True,
            "stored_authorized_json_used_as_authority": False,
            "financial_authority": False,
            "status_counts": dict(sorted(status_counts.items())),
            "authorized_row_count": len(rows),
            "reconstructed_record_count": len(records),
            "records": [record.as_dict() for record in records],
        }
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = read_reconstructed_calibration_dataset(args.db)
    payload = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(payload + "\n")
    print(payload)


if __name__ == "__main__":
    main()
