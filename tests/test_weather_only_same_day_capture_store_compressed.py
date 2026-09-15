from __future__ import annotations

import json
import sqlite3
import zlib
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from polymarket_scanner.weather_only_same_day_capture import _capture_payload, _sha
from polymarket_scanner.weather_only_same_day_capture_store import (
    SameDayCaptureStore,
    SameDayCaptureStoreError,
)
from polymarket_scanner.weather_only_same_day_capture_store_compressed import (
    COMPRESSED_CAPTURE_STORE_VERSION,
    CURRENT_CAPTURE_ENCODING,
    LEGACY_CAPTURE_ENCODING,
    MAX_UNCOMPRESSED_CAPTURE_BYTES,
    CompressedSameDayCaptureStore,
)
from test_weather_only_same_day_capture import _capture


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode("utf-8")


def _json_round_trip(value: object):
    return json.loads(_canonical_bytes(value).decode("utf-8"))


def _second_capture():
    first = _capture()
    shell = replace(
        first,
        event_id=first.event_id + "-capacity-race",
        capture_sha256="0" * 64,
    )
    return replace(shell, capture_sha256=_sha(_capture_payload(shell)))


def _stored_blob(db, digest: str) -> bytes:
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT capture_json FROM weather_same_day_captures WHERE capture_sha256=?",
            (digest,),
        ).fetchone()
    assert row is not None
    assert isinstance(row[0], bytes)
    return row[0]


def _replace_blob(db, digest: str, blob: bytes, *, encoding: str = CURRENT_CAPTURE_ENCODING):
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE weather_same_day_captures SET capture_json=?,capture_encoding=? "
            "WHERE capture_sha256=?",
            (sqlite3.Binary(blob), encoding, digest),
        )


def test_new_capture_is_losslessly_compressed_and_digest_verified(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    store = CompressedSameDayCaptureStore(db, max_capture_json_bytes=10 * 1024 * 1024)
    capture = _capture()
    assert store.save(capture) is not None

    restored = store.capture_json(capture.capture_sha256)
    # Persistence is canonical JSON, so tuple-valued in-memory dataclass fields are
    # intentionally represented as JSON arrays on read. Verify the exact JSON value.
    assert restored == _json_round_trip(capture.as_dict())
    summary = store.summary()
    assert summary["version"] == COMPRESSED_CAPTURE_STORE_VERSION
    assert summary["compressed_rows"] == 1
    assert summary["legacy_rows"] == 0
    assert summary["unknown_encoding_rows"] == 0
    assert summary["lossless_compression"] is True
    assert summary["read_time_digest_verification"] is True
    assert summary["read_time_sql_identity_verification"] is True
    assert summary["bounded_decompression"] is True
    assert summary["capture_storage_bytes"] < summary["known_uncompressed_capture_bytes"]


def test_legacy_uncompressed_capture_survives_schema_migration_and_reads_identically(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    capture = _capture()
    legacy = SameDayCaptureStore(db)
    assert legacy.save(capture) is not None

    migrated = CompressedSameDayCaptureStore(db)
    assert migrated.capture_json(capture.capture_sha256) == _json_round_trip(capture.as_dict())
    summary = migrated.summary()
    assert summary["legacy_rows"] == 1
    assert summary["compressed_rows"] == 0
    with sqlite3.connect(db) as conn:
        encoding = conn.execute(
            "SELECT capture_encoding FROM weather_same_day_captures"
        ).fetchone()[0]
    assert encoding == LEGACY_CAPTURE_ENCODING


def test_byte_capacity_counts_compressed_storage_not_uncompressed_payload(tmp_path):
    capture = _capture()
    raw = _canonical_bytes(capture.as_dict())
    compressed = zlib.compress(raw, level=9)
    assert len(compressed) < len(raw)
    db = tmp_path / "weather-paper.sqlite"
    store = CompressedSameDayCaptureStore(
        db,
        max_capture_rows=2,
        max_capture_json_bytes=len(compressed) + 16,
    )
    assert store.save(capture) is not None
    summary = store.summary()
    assert summary["capture_storage_bytes"] == len(compressed)
    assert summary["capture_storage_bytes"] <= len(compressed) + 16


def test_capacity_admission_is_serialized_across_concurrent_writers(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    first_store = CompressedSameDayCaptureStore(db, max_capture_rows=1)
    second_store = CompressedSameDayCaptureStore(db, max_capture_rows=1)
    records = (_capture(), _second_capture())

    def attempt(pair):
        store, record = pair
        try:
            return ("saved", store.save(record))
        except SameDayCaptureStoreError as exc:
            return ("error", exc.code)

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(attempt, zip((first_store, second_store), records)))

    saved = [value for kind, value in results if kind == "saved" and value is not None]
    exhausted = [
        value for kind, value in results
        if kind == "error" and value == "SAME_DAY_CAPTURE_STORE_CAPTURE_ROW_CAP_EXHAUSTED"
    ]
    assert len(saved) == 1
    assert len(exhausted) == 1
    assert CompressedSameDayCaptureStore(db, max_capture_rows=1).summary()["total"] == 1


def test_truncated_compressed_stream_fails_closed(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    store = CompressedSameDayCaptureStore(db)
    capture = _capture()
    store.save(capture)
    blob = _stored_blob(db, capture.capture_sha256)
    _replace_blob(db, capture.capture_sha256, blob[:-2])
    with pytest.raises(SameDayCaptureStoreError, match="COMPRESSED_CAPTURE_TRUNCATED"):
        store.capture_json(capture.capture_sha256)


def test_concatenated_or_trailing_compressed_stream_fails_closed(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    store = CompressedSameDayCaptureStore(db)
    capture = _capture()
    store.save(capture)
    blob = _stored_blob(db, capture.capture_sha256)
    _replace_blob(db, capture.capture_sha256, blob + zlib.compress(b"{}"))
    with pytest.raises(SameDayCaptureStoreError, match="COMPRESSED_CAPTURE_TRAILING_DATA"):
        store.capture_json(capture.capture_sha256)


def test_decompression_bomb_is_bounded_before_json_parsing(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    store = CompressedSameDayCaptureStore(db)
    capture = _capture()
    store.save(capture)
    bomb = zlib.compress(b"A" * (MAX_UNCOMPRESSED_CAPTURE_BYTES + 1), level=9)
    _replace_blob(db, capture.capture_sha256, bomb)
    with pytest.raises(SameDayCaptureStoreError, match="DECOMPRESSED_CAPTURE_TOO_LARGE"):
        store.capture_json(capture.capture_sha256)


def test_valid_zlib_with_tampered_json_fails_digest_verification(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    store = CompressedSameDayCaptureStore(db)
    capture = _capture()
    store.save(capture)
    value = capture.as_dict()
    value["event_id"] = "tampered-event"
    tampered = zlib.compress(_canonical_bytes(value), level=9)
    _replace_blob(db, capture.capture_sha256, tampered)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE weather_same_day_captures SET capture_uncompressed_bytes=? "
            "WHERE capture_sha256=?",
            (len(_canonical_bytes(value)), capture.capture_sha256),
        )
    with pytest.raises(SameDayCaptureStoreError, match="CAPTURE_DIGEST_MISMATCH"):
        store.capture_json(capture.capture_sha256)


def test_duplicated_sql_identity_tampering_is_detected_even_when_blob_is_intact(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    store = CompressedSameDayCaptureStore(db)
    capture = _capture()
    store.save(capture)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "UPDATE weather_same_day_captures SET event_id='wrong-event' "
            "WHERE capture_sha256=?",
            (capture.capture_sha256,),
        )
    with pytest.raises(SameDayCaptureStoreError, match="IDENTITY_MISMATCH"):
        store.capture_json(capture.capture_sha256)


def test_unknown_storage_encoding_is_never_guessed(tmp_path):
    db = tmp_path / "weather-paper.sqlite"
    store = CompressedSameDayCaptureStore(db)
    capture = _capture()
    store.save(capture)
    blob = _stored_blob(db, capture.capture_sha256)
    _replace_blob(db, capture.capture_sha256, blob, encoding="mystery-v99")
    with pytest.raises(SameDayCaptureStoreError, match="ENCODING_UNSUPPORTED"):
        store.capture_json(capture.capture_sha256)


def test_invalid_digest_lookup_is_rejected_before_database_read(tmp_path):
    store = CompressedSameDayCaptureStore(tmp_path / "weather-paper.sqlite")
    with pytest.raises(SameDayCaptureStoreError, match="CAPTURE_DIGEST_INVALID"):
        store.capture_json("not-a-sha")
