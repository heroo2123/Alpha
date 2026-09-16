#!/usr/bin/env python3
from __future__ import annotations

"""Wait for and verify the guarded pure three-layer validation wrapper status."""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

_SCRIPT_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from polymarket_scanner.weather_only_live_paper_three_layer_validation import (  # noqa: E402
    THREE_LAYER_31D_CAPTURE_ROW_BOUND,
    THREE_LAYER_ATTEMPT_ROW_CAP,
    THREE_LAYER_CAPTURE_JSON_BYTES_CAP,
    THREE_LAYER_COLLECTION_VERSION,
    THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS,
    THREE_LAYER_ELIGIBILITY_STATION_CAP,
    THREE_LAYER_MAX_EVENTS_PER_CYCLE,
    THREE_LAYER_SELECTION_POLICY,
    THREE_LAYER_SELECTION_UNIVERSE_CAP,
    THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS,
    THREE_LAYER_STATION_METADATA_CONCURRENCY,
    THREE_LAYER_VALIDATION_RUNTIME_VERSION,
)
from polymarket_scanner.weather_only_same_day_capture_store_compressed import (  # noqa: E402
    COMPRESSED_CAPTURE_STORE_VERSION,
    CURRENT_CAPTURE_ENCODING,
    LEGACY_CAPTURE_ENCODING,
    MAX_COMPRESSED_CAPTURE_BYTES,
    MAX_UNCOMPRESSED_CAPTURE_BYTES,
)


EXPECTED_COLLECTION_VERSION = THREE_LAYER_COLLECTION_VERSION


class ThreeLayerStatusError(RuntimeError):
    pass


def _finite(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ThreeLayerStatusError(code)
    number = float(value)
    if not math.isfinite(number):
        raise ThreeLayerStatusError(code)
    return number


def _sha(value: object, code: str) -> str:
    text = str(value or "").strip().lower()
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text):
        raise ThreeLayerStatusError(code)
    return text


def _require_false(payload: dict, key: str, code: str) -> None:
    if payload.get(key) is not False:
        raise ThreeLayerStatusError(code)


def _require_true(payload: dict, key: str, code: str) -> None:
    if payload.get(key) is not True:
        raise ThreeLayerStatusError(code)


def _nonnegative_int(value: object, code: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ThreeLayerStatusError(code)
    return value


def verify(
    status: object,
    *,
    release_sha: str,
    not_before: float,
    now: float | None = None,
    max_age_seconds: float = 600.0,
) -> dict:
    if not isinstance(status, dict):
        raise ThreeLayerStatusError("THREE_LAYER_STATUS_TYPE_INVALID")
    expected_release = _sha(release_sha, "THREE_LAYER_EXPECTED_RELEASE_INVALID")
    if _sha(status.get("release_sha"), "THREE_LAYER_STATUS_RELEASE_INVALID") != expected_release:
        raise ThreeLayerStatusError("THREE_LAYER_STATUS_RELEASE_MISMATCH")
    if status.get("three_layer_validation_runtime_version") != THREE_LAYER_VALIDATION_RUNTIME_VERSION:
        raise ThreeLayerStatusError("THREE_LAYER_WRAPPER_VERSION_MISSING")

    finished = _finite(status.get("finished_at"), "THREE_LAYER_FINISHED_AT_INVALID")
    boundary = _finite(not_before, "THREE_LAYER_NOT_BEFORE_INVALID")
    current = time.time() if now is None else _finite(now, "THREE_LAYER_NOW_INVALID")
    max_age = _finite(max_age_seconds, "THREE_LAYER_MAX_AGE_INVALID")
    if max_age <= 0.0:
        raise ThreeLayerStatusError("THREE_LAYER_MAX_AGE_INVALID")
    if finished < boundary:
        raise ThreeLayerStatusError("THREE_LAYER_STATUS_PREDATES_START")
    if finished > current + 5.0:
        raise ThreeLayerStatusError("THREE_LAYER_STATUS_FROM_FUTURE")
    if current - finished > max_age:
        raise ThreeLayerStatusError("THREE_LAYER_STATUS_STALE")

    _require_false(status, "pws_enabled", "THREE_LAYER_PWS_NOT_DISABLED")
    _require_false(
        status,
        "population_alignment_certified",
        "THREE_LAYER_POPULATION_ALIGNMENT_NOT_FALSE",
    )
    _require_false(status, "same_day_delivery_enabled", "THREE_LAYER_DELIVERY_NOT_FALSE")
    _require_false(status, "financial_delivery", "THREE_LAYER_FINANCIAL_DELIVERY_NOT_FALSE")
    _require_false(status, "financial_authority", "THREE_LAYER_FINANCIAL_AUTHORITY_NOT_FALSE")
    _require_false(status, "automatic_order_placement", "THREE_LAYER_ORDER_PLACEMENT_NOT_FALSE")

    lane = status.get("same_day_three_layer")
    if not isinstance(lane, dict):
        raise ThreeLayerStatusError("THREE_LAYER_LANE_STATUS_MISSING")
    if lane.get("version") != EXPECTED_COLLECTION_VERSION:
        raise ThreeLayerStatusError("THREE_LAYER_COLLECTION_VERSION_MISMATCH")
    if lane.get("enabled") is not True or lane.get("silent_research_only") is not True:
        raise ThreeLayerStatusError("THREE_LAYER_RESEARCH_MODE_INVALID")
    if lane.get("selection_policy") != THREE_LAYER_SELECTION_POLICY:
        raise ThreeLayerStatusError("THREE_LAYER_SELECTION_POLICY_MISMATCH")
    if lane.get("selection_universe_cap") != THREE_LAYER_SELECTION_UNIVERSE_CAP:
        raise ThreeLayerStatusError("THREE_LAYER_SELECTION_CAP_MISMATCH")
    if lane.get("selection_universe_truncated") is not False:
        raise ThreeLayerStatusError("THREE_LAYER_SELECTION_UNIVERSE_TRUNCATED")
    if lane.get("selection_coverage_complete") is not True:
        raise ThreeLayerStatusError("THREE_LAYER_ELIGIBILITY_COVERAGE_INCOMPLETE")
    station_count = _nonnegative_int(
        lane.get("eligibility_station_count"), "THREE_LAYER_STATION_COUNT_INVALID"
    )
    if lane.get("eligibility_station_cap") != THREE_LAYER_ELIGIBILITY_STATION_CAP:
        raise ThreeLayerStatusError("THREE_LAYER_STATION_CAP_MISMATCH")
    if station_count > THREE_LAYER_ELIGIBILITY_STATION_CAP:
        raise ThreeLayerStatusError("THREE_LAYER_STATION_COUNT_EXCEEDS_CAP")
    if lane.get("station_metadata_concurrency") != THREE_LAYER_STATION_METADATA_CONCURRENCY:
        raise ThreeLayerStatusError("THREE_LAYER_STATION_CONCURRENCY_MISMATCH")

    eligible_total = _nonnegative_int(
        lane.get("eligible_events_total"), "THREE_LAYER_ELIGIBLE_TOTAL_INVALID"
    )
    if eligible_total > THREE_LAYER_SELECTION_UNIVERSE_CAP:
        raise ThreeLayerStatusError("THREE_LAYER_ELIGIBLE_TOTAL_EXCEEDS_CAP")
    if lane.get("max_events_per_cycle") != THREE_LAYER_MAX_EVENTS_PER_CYCLE:
        raise ThreeLayerStatusError("THREE_LAYER_MAX_EVENTS_PER_CYCLE_MISMATCH")
    selected_ids = lane.get("selected_event_ids")
    if not isinstance(selected_ids, list) or any(
        not isinstance(value, str) or not value.strip() for value in selected_ids
    ):
        raise ThreeLayerStatusError("THREE_LAYER_SELECTED_IDS_INVALID")
    if len(selected_ids) != len(set(selected_ids)):
        raise ThreeLayerStatusError("THREE_LAYER_SELECTED_IDS_DUPLICATE")
    if len(selected_ids) > THREE_LAYER_MAX_EVENTS_PER_CYCLE or len(selected_ids) > eligible_total:
        raise ThreeLayerStatusError("THREE_LAYER_SELECTED_IDS_COUNT_INVALID")
    selected_count = _nonnegative_int(
        lane.get("eligible_events"), "THREE_LAYER_SELECTED_EVENT_COUNT_INVALID"
    )
    if selected_count != len(selected_ids):
        raise ThreeLayerStatusError("THREE_LAYER_SELECTED_EVENT_COUNT_MISMATCH")
    if lane.get("source_bundle_deadline_seconds") != THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS:
        raise ThreeLayerStatusError("THREE_LAYER_SOURCE_DEADLINE_MISMATCH")
    if lane.get("eligibility_scan_deadline_seconds") != THREE_LAYER_ELIGIBILITY_SCAN_DEADLINE_SECONDS:
        raise ThreeLayerStatusError("THREE_LAYER_ELIGIBILITY_DEADLINE_MISMATCH")
    if lane.get("theoretical_31_day_row_bound_at_full_daily_eligibility") != THREE_LAYER_31D_CAPTURE_ROW_BOUND:
        raise ThreeLayerStatusError("THREE_LAYER_STORAGE_BOUND_MISMATCH")
    if lane.get("capture_json_bytes_cap") != THREE_LAYER_CAPTURE_JSON_BYTES_CAP:
        raise ThreeLayerStatusError("THREE_LAYER_CAPTURE_BYTE_CAP_MISMATCH")
    if lane.get("attempt_row_cap") != THREE_LAYER_ATTEMPT_ROW_CAP:
        raise ThreeLayerStatusError("THREE_LAYER_ATTEMPT_ROW_CAP_MISMATCH")

    store = lane.get("store")
    if not isinstance(store, dict):
        raise ThreeLayerStatusError("THREE_LAYER_STORE_STATUS_MISSING")
    if store.get("version") != COMPRESSED_CAPTURE_STORE_VERSION:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_VERSION_MISMATCH")
    if store.get("max_capture_rows") != THREE_LAYER_31D_CAPTURE_ROW_BOUND:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_ROW_CAP_MISMATCH")
    if store.get("max_capture_json_bytes") != THREE_LAYER_CAPTURE_JSON_BYTES_CAP:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_BYTE_CAP_MISMATCH")
    if store.get("capture_storage_encoding_current") != CURRENT_CAPTURE_ENCODING:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_ENCODING_MISMATCH")
    if store.get("legacy_capture_encoding") != LEGACY_CAPTURE_ENCODING:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_LEGACY_ENCODING_MISMATCH")
    if store.get("max_uncompressed_capture_bytes") != MAX_UNCOMPRESSED_CAPTURE_BYTES:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_UNCOMPRESSED_BOUND_MISMATCH")
    if store.get("max_compressed_capture_bytes") != MAX_COMPRESSED_CAPTURE_BYTES:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_COMPRESSED_BOUND_MISMATCH")
    if _nonnegative_int(
        store.get("unknown_encoding_rows"), "THREE_LAYER_STORE_UNKNOWN_ENCODING_COUNT_INVALID"
    ) != 0:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_UNKNOWN_ENCODING_PRESENT")
    _require_true(store, "lossless_compression", "THREE_LAYER_STORE_COMPRESSION_NOT_PROVEN")
    _require_true(
        store,
        "read_time_digest_verification",
        "THREE_LAYER_STORE_DIGEST_VERIFICATION_NOT_PROVEN",
    )
    _require_true(
        store,
        "read_time_sql_identity_verification",
        "THREE_LAYER_STORE_IDENTITY_VERIFICATION_NOT_PROVEN",
    )
    _require_true(
        store,
        "read_time_single_snapshot",
        "THREE_LAYER_STORE_SINGLE_SNAPSHOT_NOT_PROVEN",
    )
    _require_true(
        store,
        "attempt_capacity_admission_atomic",
        "THREE_LAYER_STORE_ATTEMPT_CAP_ATOMICITY_NOT_PROVEN",
    )
    _require_true(store, "bounded_decompression", "THREE_LAYER_STORE_DECOMPRESSION_NOT_BOUNDED")
    _require_true(
        store,
        "legacy_uncompressed_read_compatible",
        "THREE_LAYER_STORE_LEGACY_COMPATIBILITY_NOT_PROVEN",
    )
    storage_bytes = _nonnegative_int(
        store.get("capture_storage_bytes"), "THREE_LAYER_STORE_STORAGE_BYTES_INVALID"
    )
    if storage_bytes > THREE_LAYER_CAPTURE_JSON_BYTES_CAP:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_STORAGE_BYTES_EXCEED_CAP")

    attempts = store.get("attempts")
    if not isinstance(attempts, dict) or attempts.get("max_attempt_rows") != THREE_LAYER_ATTEMPT_ROW_CAP:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_ATTEMPT_CAP_MISMATCH")
    if store.get("capture_capacity_exhausted") is not False:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_CAPTURE_CAPACITY_NOT_HEALTHY")
    if attempts.get("capacity_exhausted") is not False:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_ATTEMPT_CAPACITY_NOT_HEALTHY")
    if store.get("automatic_evidence_pruning") is not False:
        raise ThreeLayerStatusError("THREE_LAYER_STORE_PRUNING_POLICY_INVALID")
    _require_false(store, "included_in_validated_pnl", "THREE_LAYER_STORE_PNL_NOT_FALSE")
    _require_false(store, "same_day_delivery_enabled", "THREE_LAYER_STORE_DELIVERY_NOT_FALSE")
    _require_false(store, "financial_authority", "THREE_LAYER_STORE_FINANCIAL_NOT_FALSE")
    _require_false(attempts, "included_in_validated_pnl", "THREE_LAYER_ATTEMPT_PNL_NOT_FALSE")
    _require_false(attempts, "same_day_delivery_enabled", "THREE_LAYER_ATTEMPT_DELIVERY_NOT_FALSE")
    _require_false(attempts, "financial_authority", "THREE_LAYER_ATTEMPT_FINANCIAL_NOT_FALSE")
    if _nonnegative_int(attempts.get("started"), "THREE_LAYER_ATTEMPT_STARTED_INVALID") != 0:
        raise ThreeLayerStatusError("THREE_LAYER_ATTEMPT_LEFT_STARTED")
    if lane.get("capture_cadence_persisted_in_sqlite") is not True:
        raise ThreeLayerStatusError("THREE_LAYER_DURABLE_CADENCE_NOT_PROVEN")
    if lane.get("attempt_audit_persisted_in_sqlite") is not True:
        raise ThreeLayerStatusError("THREE_LAYER_DURABLE_ATTEMPT_AUDIT_NOT_PROVEN")
    _nonnegative_int(
        lane.get("attempt_recovery_at_startup"), "THREE_LAYER_ATTEMPT_RECOVERY_INVALID"
    )
    errors = lane.get("errors")
    if not isinstance(errors, list):
        raise ThreeLayerStatusError("THREE_LAYER_ERRORS_TYPE_INVALID")
    if errors:
        raise ThreeLayerStatusError("THREE_LAYER_SOURCE_ERRORS_PRESENT")

    attempted = _nonnegative_int(lane.get("attempted_now"), "THREE_LAYER_ATTEMPTED_NOW_INVALID")
    saved = _nonnegative_int(lane.get("saved_now"), "THREE_LAYER_SAVED_NOW_INVALID")
    duplicates = _nonnegative_int(lane.get("duplicates_now"), "THREE_LAYER_DUPLICATES_NOW_INVALID")
    cadence_skipped = _nonnegative_int(
        lane.get("cadence_skipped_now"), "THREE_LAYER_CADENCE_SKIPPED_INVALID"
    )
    blocked = _nonnegative_int(lane.get("blocked_now"), "THREE_LAYER_BLOCKED_NOW_INVALID")
    ready = _nonnegative_int(
        lane.get("ready_uncalibrated_now"), "THREE_LAYER_READY_NOW_INVALID"
    )
    if attempted != saved + duplicates:
        raise ThreeLayerStatusError("THREE_LAYER_ATTEMPT_ACCOUNTING_MISMATCH")
    if blocked + ready != saved + duplicates:
        raise ThreeLayerStatusError("THREE_LAYER_CAPTURE_STATUS_ACCOUNTING_MISMATCH")
    if selected_count != attempted + cadence_skipped:
        raise ThreeLayerStatusError("THREE_LAYER_SELECTION_ACCOUNTING_MISMATCH")
    _require_false(lane, "population_alignment_certified", "THREE_LAYER_LANE_ALIGNMENT_NOT_FALSE")
    _require_false(lane, "calibrated_probability", "THREE_LAYER_CALIBRATION_NOT_FALSE")
    _require_false(lane, "included_in_validated_pnl", "THREE_LAYER_PNL_NOT_FALSE")
    _require_false(lane, "telegram_delivery", "THREE_LAYER_TELEGRAM_NOT_FALSE")
    _require_false(lane, "financial_authority", "THREE_LAYER_LANE_FINANCIAL_NOT_FALSE")

    return {
        "acceptance": "PASS_THREE_LAYER_VALIDATION_RUNTIME_STATUS",
        "release_sha": expected_release,
        "runtime_version": THREE_LAYER_VALIDATION_RUNTIME_VERSION,
        "collection_version": EXPECTED_COLLECTION_VERSION,
        "selection_policy": THREE_LAYER_SELECTION_POLICY,
        "selection_universe_cap": THREE_LAYER_SELECTION_UNIVERSE_CAP,
        "storage_bound_31d": THREE_LAYER_31D_CAPTURE_ROW_BOUND,
        "storage_encoding": CURRENT_CAPTURE_ENCODING,
        "pws_enabled": False,
        "population_alignment_certified": False,
        "same_day_delivery_enabled": False,
        "financial_authority": False,
        "automatic_order_placement": False,
    }


def _read_status(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise ThreeLayerStatusError("THREE_LAYER_STATUS_FILE_INVALID")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise ThreeLayerStatusError("THREE_LAYER_STATUS_JSON_INVALID") from None


def _is_waitable_intermediate(
    status: object,
    *,
    release_sha: str,
    not_before: float,
) -> bool:
    """Recognize only the inherited final status written just before wrapper finalize."""
    if not isinstance(status, dict):
        return False
    expected = str(release_sha or "").strip().lower()
    observed = str(status.get("release_sha") or "").strip().lower()
    if observed != expected:
        return False
    finished = status.get("finished_at")
    if isinstance(finished, bool) or not isinstance(finished, (int, float)):
        return False
    try:
        fresh = math.isfinite(float(finished)) and float(finished) >= float(not_before)
    except (TypeError, ValueError, OverflowError):
        return False
    return fresh and status.get("three_layer_validation_runtime_version") in (None, "")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--not-before", type=float, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--max-age-seconds", type=float, default=600.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.timeout_seconds <= 0.0 or args.timeout_seconds > 600.0:
        print("FAIL_THREE_LAYER_STATUS:TIMEOUT_INVALID", file=sys.stderr)
        return 2
    deadline = time.monotonic() + args.timeout_seconds
    last = "THREE_LAYER_WRAPPER_STATUS_NOT_YET_AVAILABLE"
    status_path = args.status.expanduser().resolve()

    while time.monotonic() < deadline:
        status: object | None = None
        try:
            status = _read_status(status_path)
            payload = verify(
                status,
                release_sha=args.release_sha,
                not_before=args.not_before,
                max_age_seconds=args.max_age_seconds,
            )
        except ThreeLayerStatusError as exc:
            last = str(exc)
            if _is_waitable_intermediate(
                status,
                release_sha=args.release_sha,
                not_before=args.not_before,
            ):
                time.sleep(0.25)
                continue
            print(f"FAIL_THREE_LAYER_STATUS:{last}", file=sys.stderr)
            return 2

        text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
        if args.output is not None:
            output = args.output.expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
            os.chmod(output, 0o600)
        print(text, end="")
        return 0

    print(f"FAIL_THREE_LAYER_STATUS_TIMEOUT:{last}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())