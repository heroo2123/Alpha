from __future__ import annotations

"""Lossless, bounded persistence for silent same-day three-layer evidence.

The base capture store remains the compatibility boundary for existing PAPER data.
This wrapper is used only by the guarded three-layer validation runtime. New capture
rows are stored as zlib-compressed canonical JSON while legacy uncompressed rows stay
readable. Every read revalidates the capture digest and the duplicated SQL identity
columns before returning evidence.

Compression is storage-only. It does not alter the capture payload, authority flags,
research semantics, sampling cadence, attempt audit, or settlement/financial state.
"""

import hashlib
import json
import math
import sqlite3
import zlib
from pathlib import Path

from .weather_only_same_day_capture import SameDayCaptureRecord, verify_same_day_capture_record
from .weather_only_same_day_capture_store import (
    SAME_DAY_ATTEMPT_VERSION,
    SameDayCaptureStore,
    SameDayCaptureStoreError,
    _nonnegative_time,
)


COMPRESSED_CAPTURE_STORE_VERSION = "weather_same_day_capture_store_v4_zlib_integrity"
LEGACY_CAPTURE_ENCODING = "json-utf8-v1"
CURRENT_CAPTURE_ENCODING = "zlib-json-utf8-v1"
MAX_UNCOMPRESSED_CAPTURE_BYTES = 2 * 1024 * 1024
MAX_COMPRESSED_CAPTURE_BYTES = 2 * 1024 * 1024
ZLIB_LEVEL = 9


def _canonical_json_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=True,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_JSON_INVALID") from None


def _digest_payload(value: dict) -> str:
    payload = dict(value)
    payload.pop("capture_sha256", None)
    return hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()


def _valid_digest(value: object) -> str:
    digest = str(value or "").strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CAPTURE_DIGEST_INVALID")
    return digest


def _bounded_decompress(raw: bytes) -> bytes:
    if len(raw) > MAX_COMPRESSED_CAPTURE_BYTES:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_COMPRESSED_CAPTURE_TOO_LARGE")
    decoder = zlib.decompressobj()
    try:
        decoded = decoder.decompress(raw, MAX_UNCOMPRESSED_CAPTURE_BYTES + 1)
    except zlib.error:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_COMPRESSED_CAPTURE_INVALID") from None
    if len(decoded) > MAX_UNCOMPRESSED_CAPTURE_BYTES or decoder.unconsumed_tail:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_DECOMPRESSED_CAPTURE_TOO_LARGE")
    if not decoder.eof:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_COMPRESSED_CAPTURE_TRUNCATED")
    if decoder.unused_data:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_COMPRESSED_CAPTURE_TRAILING_DATA")
    return decoded


def _strict_json_dict(raw: bytes) -> dict:
    if len(raw) > MAX_UNCOMPRESSED_CAPTURE_BYTES:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_DECOMPRESSED_CAPTURE_TOO_LARGE")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError):
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_CAPTURE_JSON") from None
    if not isinstance(value, dict):
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_CAPTURE_JSON")
    return value


class CompressedSameDayCaptureStore(SameDayCaptureStore):
    """Same-day store with bounded lossless compression and read-time integrity."""

    def __init__(
        self,
        path: str | Path,
        *,
        max_capture_rows: int | None = None,
        max_capture_json_bytes: int | None = None,
        max_attempt_rows: int | None = None,
    ) -> None:
        super().__init__(
            path,
            max_capture_rows=max_capture_rows,
            max_capture_json_bytes=max_capture_json_bytes,
            max_attempt_rows=max_attempt_rows,
        )
        self._ensure_compression_schema()

    def _ensure_compression_schema(self) -> None:
        db = self._conn()
        try:
            db.execute("BEGIN IMMEDIATE")
            columns = {
                str(row["name"])
                for row in db.execute("PRAGMA table_info(weather_same_day_captures)").fetchall()
            }
            if "capture_encoding" not in columns:
                db.execute(
                    "ALTER TABLE weather_same_day_captures "
                    "ADD COLUMN capture_encoding TEXT NOT NULL DEFAULT 'json-utf8-v1'"
                )
            if "capture_uncompressed_bytes" not in columns:
                db.execute(
                    "ALTER TABLE weather_same_day_captures "
                    "ADD COLUMN capture_uncompressed_bytes INTEGER"
                )
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def save(self, record: SameDayCaptureRecord) -> int | None:
        try:
            value = verify_same_day_capture_record(record)
        except Exception as exc:
            code = getattr(exc, "code", type(exc).__name__)
            raise SameDayCaptureStoreError(
                f"SAME_DAY_CAPTURE_STORE_RECORD_INVALID:{code}"
            ) from exc

        payload = _canonical_json_bytes(value.as_dict())
        if len(payload) > MAX_UNCOMPRESSED_CAPTURE_BYTES:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CAPTURE_TOO_LARGE")
        compressed = zlib.compress(payload, level=ZLIB_LEVEL)
        if len(compressed) > MAX_COMPRESSED_CAPTURE_BYTES:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_COMPRESSED_CAPTURE_TOO_LARGE")
        try:
            reasons = json.dumps(
                list(value.block_reasons),
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=True,
                allow_nan=False,
            )
        except (TypeError, ValueError):
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_JSON_INVALID") from None

        db = self._conn()
        try:
            # Serialize capacity admission with the insert. Without the immediate
            # write lock two writers could both observe the final free slot/bytes.
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT id FROM weather_same_day_captures WHERE capture_sha256=?",
                (value.capture_sha256,),
            ).fetchone()
            if existing is not None:
                db.rollback()
                return None
            capacity = db.execute(
                "SELECT COUNT(*) AS rows, COALESCE(SUM(LENGTH(capture_json)),0) AS bytes "
                "FROM weather_same_day_captures"
            ).fetchone()
            current_rows = int(capacity["rows"] or 0)
            current_bytes = int(capacity["bytes"] or 0)
            if self.max_capture_rows is not None and current_rows >= self.max_capture_rows:
                raise SameDayCaptureStoreError(
                    "SAME_DAY_CAPTURE_STORE_CAPTURE_ROW_CAP_EXHAUSTED"
                )
            if (
                self.max_capture_json_bytes is not None
                and current_bytes + len(compressed) > self.max_capture_json_bytes
            ):
                raise SameDayCaptureStoreError(
                    "SAME_DAY_CAPTURE_STORE_CAPTURE_BYTE_CAP_EXHAUSTED"
                )
            cur = db.execute(
                """
                INSERT INTO weather_same_day_captures(
                    store_version,capture_version,capture_sha256,event_id,station,
                    target_date,family,unit,as_of,status,block_reasons_json,capture_json,
                    included_in_validated_pnl,same_day_delivery_enabled,financial_authority,
                    capture_encoding,capture_uncompressed_bytes
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,0,0,0,?,?)
                """,
                (
                    COMPRESSED_CAPTURE_STORE_VERSION,
                    value.version,
                    value.capture_sha256,
                    value.event_id,
                    value.station,
                    value.target_date,
                    value.family,
                    value.unit,
                    float(value.as_of),
                    value.status,
                    reasons,
                    sqlite3.Binary(compressed),
                    CURRENT_CAPTURE_ENCODING,
                    len(payload),
                ),
            )
            row_id = int(cur.lastrowid)
            db.commit()
            return row_id
        except SameDayCaptureStoreError:
            db.rollback()
            raise
        except sqlite3.IntegrityError as exc:
            db.rollback()
            raise SameDayCaptureStoreError(
                "SAME_DAY_CAPTURE_STORE_INSERT_INTEGRITY_ERROR"
            ) from exc
        except sqlite3.Error as exc:
            db.rollback()
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_SQLITE_ERROR") from exc
        finally:
            db.close()

    def start_attempt(
        self,
        *,
        event_id: str,
        station: str,
        target_date: str,
        family: str,
        unit: str,
        attempted_at: float,
    ) -> int:
        """Reserve attempt capacity atomically with insertion.

        The validation store can be exercised by concurrent callers during tests or
        future orchestration. The capacity read and insert therefore share one
        ``BEGIN IMMEDIATE`` transaction so two writers cannot both consume the final
        available attempt slot.
        """
        identity = str(event_id or "").strip()
        station_id = str(station or "").strip().upper()
        target = str(target_date or "").strip()
        family_id = str(family or "").strip()
        unit_id = str(unit or "").strip()
        if not all((identity, station_id, target, family_id, unit_id)):
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ATTEMPT_IDENTITY_INVALID")
        started = _nonnegative_time(
            attempted_at, "SAME_DAY_CAPTURE_STORE_ATTEMPT_TIME_INVALID"
        )

        db = self._conn()
        try:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT COUNT(*) AS total FROM weather_same_day_capture_attempts"
            ).fetchone()
            total = int(row["total"] or 0)
            if self.max_attempt_rows is not None and total >= self.max_attempt_rows:
                raise SameDayCaptureStoreError(
                    "SAME_DAY_CAPTURE_STORE_ATTEMPT_ROW_CAP_EXHAUSTED"
                )
            cur = db.execute(
                """
                INSERT INTO weather_same_day_capture_attempts(
                    attempt_version,event_id,station,target_date,family,unit,
                    attempted_at,completed_at,outcome,error_code,capture_sha256,
                    included_in_validated_pnl,same_day_delivery_enabled,financial_authority
                ) VALUES(?,?,?,?,?,?,?,NULL,'STARTED',NULL,NULL,0,0,0)
                """,
                (
                    SAME_DAY_ATTEMPT_VERSION,
                    identity,
                    station_id,
                    target,
                    family_id,
                    unit_id,
                    started,
                ),
            )
            attempt_id = int(cur.lastrowid)
            db.commit()
            return attempt_id
        except SameDayCaptureStoreError:
            db.rollback()
            raise
        except sqlite3.Error as exc:
            db.rollback()
            raise SameDayCaptureStoreError(
                "SAME_DAY_CAPTURE_STORE_ATTEMPT_SQLITE_ERROR"
            ) from exc
        finally:
            db.close()

    def _row_for_digest(self, digest: str) -> sqlite3.Row | None:
        # Metadata, duplicated identity fields and the blob are fetched by one SELECT
        # from one SQLite snapshot. A second connection/read here would create a TOCTOU
        # window in which the row metadata and payload could come from different states.
        with self._conn() as db:
            return db.execute(
                """
                SELECT id,store_version,capture_version,capture_sha256,event_id,station,
                       target_date,family,unit,as_of,status,block_reasons_json,
                       capture_encoding,capture_uncompressed_bytes,capture_json,
                       LENGTH(capture_json) AS stored_bytes
                  FROM weather_same_day_captures
                 WHERE capture_sha256=?
                """,
                (digest,),
            ).fetchone()

    def capture_json(self, capture_sha256: str) -> dict | None:
        digest = _valid_digest(capture_sha256)
        row = self._row_for_digest(digest)
        if row is None:
            return None
        encoding = str(row["capture_encoding"] or "").strip()
        stored_bytes = int(row["stored_bytes"] or 0)
        if stored_bytes <= 0:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CAPTURE_STORAGE_INVALID")
        if encoding == CURRENT_CAPTURE_ENCODING:
            if stored_bytes > MAX_COMPRESSED_CAPTURE_BYTES:
                raise SameDayCaptureStoreError(
                    "SAME_DAY_CAPTURE_STORE_COMPRESSED_CAPTURE_TOO_LARGE"
                )
        elif encoding == LEGACY_CAPTURE_ENCODING:
            if stored_bytes > MAX_UNCOMPRESSED_CAPTURE_BYTES:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CAPTURE_TOO_LARGE")
        else:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ENCODING_UNSUPPORTED")

        stored = row["capture_json"]
        if encoding == CURRENT_CAPTURE_ENCODING:
            if not isinstance(stored, (bytes, bytearray, memoryview)):
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ENCODING_PAYLOAD_MISMATCH")
            raw = _bounded_decompress(bytes(stored))
        else:
            if isinstance(stored, str):
                raw = stored.encode("utf-8")
            elif isinstance(stored, (bytes, bytearray, memoryview)):
                raw = bytes(stored)
            else:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_ENCODING_PAYLOAD_MISMATCH")
            if len(raw) > MAX_UNCOMPRESSED_CAPTURE_BYTES:
                raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CAPTURE_TOO_LARGE")

        expected_uncompressed = row["capture_uncompressed_bytes"]
        if expected_uncompressed is not None:
            if (
                isinstance(expected_uncompressed, bool)
                or not isinstance(expected_uncompressed, int)
                or expected_uncompressed < 0
                or expected_uncompressed != len(raw)
            ):
                raise SameDayCaptureStoreError(
                    "SAME_DAY_CAPTURE_STORE_UNCOMPRESSED_LENGTH_MISMATCH"
                )
        value = _strict_json_dict(raw)
        if value.get("capture_sha256") != digest:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CAPTURE_DIGEST_MISMATCH")
        if _digest_payload(value) != digest:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CAPTURE_DIGEST_MISMATCH")

        reasons = value.get("block_reasons")
        try:
            shadow_reasons = json.loads(str(row["block_reasons_json"]))
        except (TypeError, json.JSONDecodeError):
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_REASONS") from None
        if not isinstance(reasons, list) or reasons != shadow_reasons:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_IDENTITY_MISMATCH")

        identity_pairs = (
            ("version", str(row["capture_version"])),
            ("event_id", str(row["event_id"])),
            ("station", str(row["station"])),
            ("target_date", str(row["target_date"])),
            ("family", str(row["family"])),
            ("unit", str(row["unit"])),
            ("status", str(row["status"])),
        )
        if any(value.get(key) != expected for key, expected in identity_pairs):
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_IDENTITY_MISMATCH")
        as_of = value.get("as_of")
        if isinstance(as_of, bool) or not isinstance(as_of, (int, float)):
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_IDENTITY_MISMATCH")
        as_of_number = float(as_of)
        if not math.isfinite(as_of_number) or as_of_number != float(row["as_of"]):
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_IDENTITY_MISMATCH")
        for authority_key in (
            "included_in_validated_pnl",
            "calibrated_probability",
            "same_day_delivery_enabled",
            "settlement_authority",
            "financial_authority",
        ):
            if value.get(authority_key) is not False:
                raise SameDayCaptureStoreError(
                    "SAME_DAY_CAPTURE_STORE_AUTHORITY_BOUNDARY_BROKEN"
                )
        return value

    def summary(self) -> dict:
        result = dict(super().summary())
        with self._conn() as db:
            row = db.execute(
                """
                SELECT
                    SUM(CASE WHEN capture_encoding=? THEN 1 ELSE 0 END) AS compressed_rows,
                    SUM(CASE WHEN capture_encoding=? THEN 1 ELSE 0 END) AS legacy_rows,
                    SUM(CASE WHEN capture_encoding NOT IN (?,?) THEN 1 ELSE 0 END) AS unknown_rows,
                    COALESCE(SUM(capture_uncompressed_bytes),0) AS known_uncompressed_bytes
                  FROM weather_same_day_captures
                """,
                (
                    CURRENT_CAPTURE_ENCODING,
                    LEGACY_CAPTURE_ENCODING,
                    CURRENT_CAPTURE_ENCODING,
                    LEGACY_CAPTURE_ENCODING,
                ),
            ).fetchone()
        result.update(
            {
                "version": COMPRESSED_CAPTURE_STORE_VERSION,
                "capture_storage_encoding_current": CURRENT_CAPTURE_ENCODING,
                "legacy_capture_encoding": LEGACY_CAPTURE_ENCODING,
                "compressed_rows": int(row["compressed_rows"] or 0),
                "legacy_rows": int(row["legacy_rows"] or 0),
                "unknown_encoding_rows": int(row["unknown_rows"] or 0),
                "known_uncompressed_capture_bytes": int(
                    row["known_uncompressed_bytes"] or 0
                ),
                "capture_storage_bytes": int(result.get("capture_json_bytes") or 0),
                "max_uncompressed_capture_bytes": MAX_UNCOMPRESSED_CAPTURE_BYTES,
                "max_compressed_capture_bytes": MAX_COMPRESSED_CAPTURE_BYTES,
                "lossless_compression": True,
                "read_time_digest_verification": True,
                "read_time_sql_identity_verification": True,
                "read_time_single_snapshot": True,
                "attempt_capacity_admission_atomic": True,
                "bounded_decompression": True,
                "legacy_uncompressed_read_compatible": True,
            }
        )
        return result
