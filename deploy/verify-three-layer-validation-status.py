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
    THREE_LAYER_SELECTION_POLICY,
    THREE_LAYER_SELECTION_UNIVERSE_CAP,
    THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS,
    THREE_LAYER_VALIDATION_RUNTIME_VERSION,
)


EXPECTED_COLLECTION_VERSION = "same_day_three_layer_silent_collection_v3_guarded_rotating"


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
    if lane.get("selection_coverage_complete") is not True:
        raise ThreeLayerStatusError("THREE_LAYER_SELECTION_UNIVERSE_TRUNCATED")
    if lane.get("source_bundle_deadline_seconds") != THREE_LAYER_SOURCE_BUNDLE_DEADLINE_SECONDS:
        raise ThreeLayerStatusError("THREE_LAYER_SOURCE_DEADLINE_MISMATCH")
    if lane.get("theoretical_31_day_row_bound_at_full_daily_eligibility") != THREE_LAYER_31D_CAPTURE_ROW_BOUND:
        raise ThreeLayerStatusError("THREE_LAYER_STORAGE_BOUND_MISMATCH")
    if list(lane.get("errors") or []):
        raise ThreeLayerStatusError("THREE_LAYER_SOURCE_ERRORS_PRESENT")
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
