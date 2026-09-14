from __future__ import annotations

from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"{label}: expected one match, found {count}")
    return text.replace(old, new, 1)


# 1) Remove accidental duplicate strict-v6 grammar blocks left by an obsolete
# self-mutating review workflow. Keep exactly the last canonical block before the
# public question-supported helper.
strict_path = Path("polymarket_scanner/weather_only_contract_strict.py")
strict = strict_path.read_text(encoding="utf-8")
marker = "_CURRENT_TITLE_RE = re.compile("
first = strict.index(marker)
question_supported = strict.index("def _question_supported(", first)
last = strict.rfind(marker, first, question_supported)
if last != first:
    canonical = strict[last:question_supported]
    strict = strict[:first] + canonical + strict[question_supported:]
if strict.count(marker) != 1:
    raise SystemExit(f"strict grammar dedupe failed: {strict.count(marker)} copies remain")
strict_path.write_text(strict, encoding="utf-8")


# 2) Make the NWS geographic-capability cache a true fixed TTL. A cache hit may move
# the item to the end for bounded-LRU order, but must not renew its observation time.
guarded_path = Path("polymarket_scanner/weather_only_three_layer_guarded.py")
guarded = guarded_path.read_text(encoding="utf-8")
# This logic lives in the runtime module, not the guarded transport; leave guarded
# untouched here. The assertion below catches accidental future relocation.
guarded_path.write_text(guarded, encoding="utf-8")

runtime_path = Path("polymarket_scanner/weather_only_live_paper_three_layer_validation.py")
runtime = runtime_path.read_text(encoding="utf-8")
runtime = replace_once(
    runtime,
    "                cache.pop(key, None)\n                cache[key] = (now, value)\n                return value\n",
    "                cache.pop(key, None)\n                # Move to the LRU tail without extending the provider observation TTL.\n                cache[key] = (float(cached[0]), value)\n                return value\n",
    "fixed capability TTL",
)
runtime = replace_once(
    runtime,
    "THREE_LAYER_SELECTION_UNIVERSE_CAP = 12\n",
    "# Live census measured 22 same-day NWS-supported contracts. Keep material headroom\n"
    "# while retaining a hard fail-closed bound for the e2-micro research lane.\n"
    "THREE_LAYER_SELECTION_UNIVERSE_CAP = 32\n",
    "selection universe cap",
)
runtime = replace_once(
    runtime,
    "THREE_LAYER_CAPTURE_JSON_BYTES_CAP = 512 * 1024 * 1024\n",
    "# Captures are stored as lossless zlib BLOBs. Live probes measured ~18-21 KiB\n"
    "# compressed per capture versus ~138-182 KiB raw. 640 MiB leaves headroom above\n"
    "# the conservative 32*24*31 sample-max projection while remaining disk-bounded.\n"
    "THREE_LAYER_CAPTURE_JSON_BYTES_CAP = 640 * 1024 * 1024\n",
    "capture byte cap",
)
runtime_path.write_text(runtime, encoding="utf-8")


# 3) Store the immutable canonical capture JSON losslessly compressed. SQLite is
# dynamically typed, so legacy TEXT rows remain readable while new rows use BLOB.
store_path = Path("polymarket_scanner/weather_only_same_day_capture_store.py")
store = store_path.read_text(encoding="utf-8")
store = replace_once(store, "import json\nimport math\n", "import hashlib\nimport json\nimport math\n", "hashlib import")
store = replace_once(store, "import time\n", "import time\nimport zlib\n", "zlib import")
store = replace_once(
    store,
    'SAME_DAY_CAPTURE_STORE_VERSION = "weather_same_day_capture_store_v3_attempt_audit_capacity"\n'
    'SAME_DAY_ATTEMPT_VERSION = "weather_same_day_capture_attempt_v1"\n',
    'SAME_DAY_CAPTURE_STORE_VERSION = "weather_same_day_capture_store_v4_lossless_zlib_blob"\n'
    'SAME_DAY_ATTEMPT_VERSION = "weather_same_day_capture_attempt_v1"\n'
    'SAME_DAY_CAPTURE_STORAGE_CODEC = "ZLIB_JSON_UTF8_V1"\n'
    'SAME_DAY_CAPTURE_LEGACY_STORAGE_CODEC = "LEGACY_TEXT_JSON_UTF8_V1"\n'
    'SAME_DAY_CAPTURE_UNCOMPRESSED_BYTES_CAP = 16 * 1024 * 1024\n'
    'SAME_DAY_CAPTURE_ZLIB_LEVEL = 9\n',
    "store version/constants",
)
helper_anchor = "\n\nclass SameDayCaptureStore:\n"
helpers = r'''

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


def _encode_capture_blob(payload_text: str) -> bytes:
    raw = str(payload_text).encode("utf-8")
    if len(raw) > SAME_DAY_CAPTURE_UNCOMPRESSED_BYTES_CAP:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_UNCOMPRESSED_RECORD_CAP_EXHAUSTED")
    try:
        encoded = zlib.compress(raw, SAME_DAY_CAPTURE_ZLIB_LEVEL)
    except (zlib.error, MemoryError):
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_COMPRESSION_FAILED") from None
    if not encoded:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_COMPRESSION_FAILED")
    return encoded


def _decompress_capture_blob(value: bytes | bytearray | memoryview) -> bytes:
    compressed = bytes(value)
    if not compressed:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_COMPRESSED_CAPTURE")
    decoder = zlib.decompressobj()
    try:
        raw = decoder.decompress(compressed, SAME_DAY_CAPTURE_UNCOMPRESSED_BYTES_CAP + 1)
    except (zlib.error, MemoryError):
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_COMPRESSED_CAPTURE") from None
    if len(raw) > SAME_DAY_CAPTURE_UNCOMPRESSED_BYTES_CAP or decoder.unconsumed_tail:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_DECOMPRESSED_RECORD_CAP_EXHAUSTED")
    if not decoder.eof or decoder.unused_data:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_COMPRESSED_CAPTURE")
    try:
        tail = decoder.flush()
    except (zlib.error, MemoryError):
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_COMPRESSED_CAPTURE") from None
    if len(raw) + len(tail) > SAME_DAY_CAPTURE_UNCOMPRESSED_BYTES_CAP:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_DECOMPRESSED_RECORD_CAP_EXHAUSTED")
    return raw + tail


def _decode_capture_json(value: object) -> dict:
    if isinstance(value, str):
        raw = value.encode("utf-8")
        if len(raw) > SAME_DAY_CAPTURE_UNCOMPRESSED_BYTES_CAP:
            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_DECOMPRESSED_RECORD_CAP_EXHAUSTED")
    elif isinstance(value, (bytes, bytearray, memoryview)):
        raw = _decompress_capture_blob(value)
    else:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_CAPTURE_JSON")
    try:
        decoded = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_CAPTURE_JSON") from None
    if not isinstance(decoded, dict):
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_CAPTURE_JSON")
    return decoded


def _verify_decoded_capture_digest(value: dict, expected_digest: str) -> None:
    digest = str(expected_digest or "").strip().lower()
    if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_CAPTURE_DIGEST")
    if str(value.get("capture_sha256") or "").strip().lower() != digest:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_CAPTURE_DIGEST")
    payload = dict(value)
    payload.pop("capture_sha256", None)
    actual = hashlib.sha256(_canonical_json_bytes(payload)).hexdigest()
    if actual != digest:
        raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_CAPTURE_DIGEST")
'''
if helper_anchor not in store:
    raise SystemExit("store helper anchor missing")
store = store.replace(helper_anchor, helpers + helper_anchor, 1)

store = replace_once(
    store,
    "        except (TypeError, ValueError):\n            raise SameDayCaptureStoreError(\"SAME_DAY_CAPTURE_STORE_JSON_INVALID\") from None\n        with self._conn() as db:\n",
    "        except (TypeError, ValueError):\n            raise SameDayCaptureStoreError(\"SAME_DAY_CAPTURE_STORE_JSON_INVALID\") from None\n        stored_payload = _encode_capture_blob(payload)\n        with self._conn() as db:\n",
    "encode capture before transaction",
)
store = replace_once(
    store,
    "                and current_bytes + len(payload) > self.max_capture_json_bytes\n",
    "                and current_bytes + len(stored_payload) > self.max_capture_json_bytes\n",
    "stored-byte capacity",
)
store = replace_once(
    store,
    "                        payload,\n                    ),\n",
    "                        sqlite3.Binary(stored_payload),\n                    ),\n",
    "insert compressed payload",
)
old_capture_json = '''    def capture_json(self, capture_sha256: str) -> dict | None:\n        digest = str(capture_sha256 or "").strip().lower()\n        with self._conn() as db:\n            row = db.execute(\n                "SELECT capture_json FROM weather_same_day_captures WHERE capture_sha256=?",\n                (digest,),\n            ).fetchone()\n        if row is None:\n            return None\n        try:\n            value = json.loads(str(row["capture_json"]))\n        except Exception as exc:\n            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_CAPTURE_JSON") from exc\n        if not isinstance(value, dict):\n            raise SameDayCaptureStoreError("SAME_DAY_CAPTURE_STORE_CORRUPT_CAPTURE_JSON")\n        return value\n'''
new_capture_json = '''    def capture_json(self, capture_sha256: str) -> dict | None:\n        digest = str(capture_sha256 or "").strip().lower()\n        with self._conn() as db:\n            row = db.execute(\n                "SELECT capture_sha256,capture_json FROM weather_same_day_captures WHERE capture_sha256=?",\n                (digest,),\n            ).fetchone()\n        if row is None:\n            return None\n        value = _decode_capture_json(row["capture_json"])\n        _verify_decoded_capture_digest(value, str(row["capture_sha256"]))\n        return value\n'''
store = replace_once(store, old_capture_json, new_capture_json, "capture_json decoder")
store = replace_once(
    store,
    '            "capture_json_bytes": capture_json_bytes,\n'
    '            "max_capture_rows": self.max_capture_rows,\n',
    '            "capture_json_bytes": capture_json_bytes,\n'
    '            "capture_storage_codec": SAME_DAY_CAPTURE_STORAGE_CODEC,\n'
    '            "legacy_capture_storage_codec_supported": SAME_DAY_CAPTURE_LEGACY_STORAGE_CODEC,\n'
    '            "capture_uncompressed_record_bytes_cap": SAME_DAY_CAPTURE_UNCOMPRESSED_BYTES_CAP,\n'
    '            "max_capture_rows": self.max_capture_rows,\n',
    "summary codec metadata",
)
store_path.write_text(store, encoding="utf-8")


# 4) Permanent adversarial tests for compression/backward compatibility/corruption.
compression_test = Path("tests/test_weather_only_same_day_capture_store_compression.py")
if compression_test.exists():
    raise SystemExit(f"refusing to overwrite {compression_test}")
compression_test.write_text(r'''from __future__ import annotations

import json
import sqlite3
import zlib

import pytest

import polymarket_scanner.weather_only_same_day_capture_store as store_module
from polymarket_scanner.weather_only_same_day_capture_store import (
    SAME_DAY_CAPTURE_STORAGE_CODEC,
    SameDayCaptureStore,
    SameDayCaptureStoreError,
)
from test_weather_only_same_day_capture import _capture


def _canonical(record) -> str:
    return json.dumps(
        record.as_dict(), sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    )


def test_new_capture_is_losslessly_compressed_blob_and_round_trips(tmp_path):
    store = SameDayCaptureStore(tmp_path / "weather-paper.sqlite")
    record = _capture()
    raw = _canonical(record).encode("utf-8")
    assert store.save(record) == 1
    with store._conn() as db:
        row = db.execute(
            "SELECT typeof(capture_json) AS kind,LENGTH(capture_json) AS bytes "
            "FROM weather_same_day_captures"
        ).fetchone()
    assert row["kind"] == "blob"
    assert 0 < int(row["bytes"]) < len(raw)
    assert store.capture_json(record.capture_sha256) == record.as_dict()
    summary = store.summary()
    assert summary["capture_storage_codec"] == SAME_DAY_CAPTURE_STORAGE_CODEC
    assert summary["capture_json_bytes"] == int(row["bytes"])


def test_legacy_plain_text_capture_remains_readable(tmp_path):
    store = SameDayCaptureStore(tmp_path / "weather-paper.sqlite")
    record = _capture()
    assert store.save(record) == 1
    legacy = _canonical(record)
    with store._conn() as db:
        db.execute("UPDATE weather_same_day_captures SET capture_json=?", (legacy,))
    assert store.capture_json(record.capture_sha256) == record.as_dict()


def test_corrupt_compressed_blob_fails_closed(tmp_path):
    store = SameDayCaptureStore(tmp_path / "weather-paper.sqlite")
    record = _capture()
    assert store.save(record) == 1
    with store._conn() as db:
        db.execute(
            "UPDATE weather_same_day_captures SET capture_json=?",
            (sqlite3.Binary(b"not-a-zlib-stream"),),
        )
    with pytest.raises(SameDayCaptureStoreError, match="CORRUPT_COMPRESSED_CAPTURE"):
        store.capture_json(record.capture_sha256)


def test_compressed_blob_with_trailing_payload_fails_closed(tmp_path):
    store = SameDayCaptureStore(tmp_path / "weather-paper.sqlite")
    record = _capture()
    assert store.save(record) == 1
    blob = zlib.compress(_canonical(record).encode("utf-8"), 9) + b"TRAILING"
    with store._conn() as db:
        db.execute(
            "UPDATE weather_same_day_captures SET capture_json=?",
            (sqlite3.Binary(blob),),
        )
    with pytest.raises(SameDayCaptureStoreError, match="CORRUPT_COMPRESSED_CAPTURE"):
        store.capture_json(record.capture_sha256)


def test_decompression_bomb_is_bounded_before_json_parse(tmp_path, monkeypatch):
    store = SameDayCaptureStore(tmp_path / "weather-paper.sqlite")
    record = _capture()
    assert store.save(record) == 1
    monkeypatch.setattr(store_module, "SAME_DAY_CAPTURE_UNCOMPRESSED_BYTES_CAP", 128)
    blob = zlib.compress(b"A" * 4096, 9)
    with store._conn() as db:
        db.execute(
            "UPDATE weather_same_day_captures SET capture_json=?",
            (sqlite3.Binary(blob),),
        )
    with pytest.raises(SameDayCaptureStoreError, match="DECOMPRESSED_RECORD_CAP_EXHAUSTED"):
        store.capture_json(record.capture_sha256)


def test_valid_json_with_tampered_content_fails_digest_check(tmp_path):
    store = SameDayCaptureStore(tmp_path / "weather-paper.sqlite")
    record = _capture()
    assert store.save(record) == 1
    payload = record.as_dict()
    payload["station"] = "KZZZ"
    blob = zlib.compress(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode(),
        9,
    )
    with store._conn() as db:
        db.execute(
            "UPDATE weather_same_day_captures SET capture_json=?",
            (sqlite3.Binary(blob),),
        )
    with pytest.raises(SameDayCaptureStoreError, match="CORRUPT_CAPTURE_DIGEST"):
        store.capture_json(record.capture_sha256)


def test_global_byte_cap_counts_actual_compressed_bytes(tmp_path):
    record = _capture()
    first = SameDayCaptureStore(tmp_path / "measure.sqlite")
    assert first.save(record) == 1
    with first._conn() as db:
        stored = int(db.execute("SELECT LENGTH(capture_json) FROM weather_same_day_captures").fetchone()[0])
    assert stored > 1

    capped = SameDayCaptureStore(
        tmp_path / "capped.sqlite",
        max_capture_rows=10,
        max_capture_json_bytes=stored - 1,
        max_attempt_rows=10,
    )
    with pytest.raises(SameDayCaptureStoreError, match="CAPTURE_BYTE_CAP_EXHAUSTED"):
        capped.save(record)
''', encoding="utf-8")


# 5) Permanent regression proving cache hits do not renew the capability TTL.
eligibility_test = Path("tests/test_weather_only_three_layer_layer2_eligibility.py")
eligibility = eligibility_test.read_text(encoding="utf-8")
if "test_layer2_capability_cache_hit_does_not_extend_fixed_ttl" in eligibility:
    raise SystemExit("cache TTL regression already exists")
eligibility += r'''


def test_layer2_capability_cache_hit_does_not_extend_fixed_ttl(monkeypatch):
    clock = [1000.0]
    calls = 0

    async def point_supported(**_kwargs):
        nonlocal calls
        calls += 1
        return True

    service = object.__new__(runtime_module.ThreeLayerValidationWeatherLivePaperService)
    service._three_layer_nws_support_cache = {}
    service._station_cache_now = lambda: clock[0]
    service._same_day_nws = NS(point_supported=point_supported)
    metadata = NS(latitude=40.12345, longitude=-73.98765)

    async def scenario():
        assert await service._nws_point_supported_for_metadata(metadata) is True
        clock[0] = 1000.0 + runtime_module.THREE_LAYER_NWS_SUPPORT_CACHE_TTL_SECONDS - 1.0
        assert await service._nws_point_supported_for_metadata(metadata) is True
        # This must expire relative to the original provider observation, not relative
        # to the preceding cache hit.
        clock[0] = 1000.0 + runtime_module.THREE_LAYER_NWS_SUPPORT_CACHE_TTL_SECONDS + 1.0
        assert await service._nws_point_supported_for_metadata(metadata) is True

    asyncio.run(scenario())
    assert calls == 2
'''
eligibility_test.write_text(eligibility, encoding="utf-8")

print("THREE_LAYER_STORAGE_HARDENING_APPLIED")
