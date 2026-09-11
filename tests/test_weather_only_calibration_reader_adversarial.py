from __future__ import annotations

import json
import sqlite3

import pytest

from polymarket_scanner.weather_only_calibration_reader import (
    WeatherCalibrationReaderError,
    read_reconstructed_calibration_dataset,
)

# Reuse the canonical synthetic prospective capture/source stream from the primary
# reader test. Pytest places the test directory on sys.path in the repository CI.
from test_weather_only_calibration_reader import (  # noqa: E402
    FOLLOWING,
    _authorized_db,
    _snapshot,
)


def _capture_digest(db_path) -> str:
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "SELECT capture_evidence_sha256 FROM wrh_collector_captures WHERE status = 'AUTHORIZED'"
        ).fetchone()
    assert row is not None
    return str(row[0])


def test_reader_rejects_capture_metadata_tampering(tmp_path):
    db_path = tmp_path / "capture-metadata.sqlite"
    _authorized_db(db_path)
    digest = _capture_digest(db_path)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "UPDATE wrh_collector_captures SET station = 'KJFK' WHERE capture_evidence_sha256 = ?",
            (digest,),
        )
    with pytest.raises(WeatherCalibrationReaderError) as raised:
        read_reconstructed_calibration_dataset(db_path)
    assert raised.value.code == "READER_CAPTURE_STATION_METADATA_MISMATCH"


def test_reader_rejects_snapshot_digest_metadata_tampering(tmp_path):
    db_path = tmp_path / "snapshot-digest.sqlite"
    _authorized_db(db_path)
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "SELECT rowid FROM wrh_collector_snapshots ORDER BY received_at LIMIT 1"
        ).fetchone()
        assert row is not None
        db.execute(
            "UPDATE wrh_collector_snapshots SET evidence_sha256 = ? WHERE rowid = ?",
            ("f" * 64, int(row[0])),
        )
    with pytest.raises(WeatherCalibrationReaderError) as raised:
        read_reconstructed_calibration_dataset(db_path)
    assert raised.value.code == "READER_SNAPSHOT_SHA_METADATA_MISMATCH"


def test_reader_rejects_snapshot_payload_tampering_even_if_sql_metadata_is_unchanged(tmp_path):
    db_path = tmp_path / "snapshot-payload.sqlite"
    _authorized_db(db_path)
    with sqlite3.connect(db_path) as db:
        row = db.execute(
            "SELECT rowid, snapshot_json FROM wrh_collector_snapshots ORDER BY received_at LIMIT 1"
        ).fetchone()
        assert row is not None
        payload = json.loads(row[1])
        payload["target_display_temperatures_f"][0] = 999.0
        db.execute(
            "UPDATE wrh_collector_snapshots SET snapshot_json = ? WHERE rowid = ?",
            (json.dumps(payload, sort_keys=True, separators=(",", ":")), int(row[0])),
        )
    with pytest.raises(WeatherCalibrationReaderError) as raised:
        read_reconstructed_calibration_dataset(db_path)
    assert raised.value.code.startswith("READER_SNAPSHOT_INVALID:")


def test_reader_rejects_authorized_row_when_pre_cutoff_snapshot_is_missing(tmp_path):
    db_path = tmp_path / "missing-pre.sqlite"
    _authorized_db(db_path)
    with sqlite3.connect(db_path) as db:
        db.execute(
            "DELETE FROM wrh_collector_snapshots WHERE received_at < ?",
            (FOLLOWING,),
        )
    with pytest.raises(WeatherCalibrationReaderError) as raised:
        read_reconstructed_calibration_dataset(db_path)
    assert raised.value.code == "READER_NO_VALID_FINALITY_PAIR"


def test_reader_rejects_ambiguous_multiple_valid_post_cutoff_transitions(tmp_path):
    db_path = tmp_path / "ambiguous.sqlite"
    _authorized_db(db_path)
    extra = _snapshot(True, FOLLOWING + 40)
    payload = json.dumps(extra.as_dict(), sort_keys=True, separators=(",", ":"))
    with sqlite3.connect(db_path) as db:
        db.execute(
            """
            INSERT INTO wrh_collector_snapshots
                (station, target_date, evidence_sha256, received_at, snapshot_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                extra.station,
                extra.target_date.isoformat(),
                extra.evidence_sha256,
                extra.received_at,
                payload,
            ),
        )
    with pytest.raises(WeatherCalibrationReaderError) as raised:
        read_reconstructed_calibration_dataset(db_path)
    assert raised.value.code == "READER_FINALITY_PAIR_AMBIGUOUS"


def test_reader_never_mutates_database_when_reconstruction_fails(tmp_path):
    db_path = tmp_path / "readonly-failure.sqlite"
    _authorized_db(db_path)
    with sqlite3.connect(db_path) as db:
        db.execute("DELETE FROM wrh_collector_snapshots WHERE received_at < ?", (FOLLOWING,))
        before = db.total_changes
        row_count_before = db.execute("SELECT COUNT(*) FROM wrh_collector_captures").fetchone()[0]

    with pytest.raises(WeatherCalibrationReaderError):
        read_reconstructed_calibration_dataset(db_path)

    with sqlite3.connect(db_path) as db:
        row_count_after = db.execute("SELECT COUNT(*) FROM wrh_collector_captures").fetchone()[0]
        assert row_count_after == row_count_before
        # The reader opens its own mode=ro connection; this write connection's local
        # total_changes is irrelevant, so verify durable content rather than counters.
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
