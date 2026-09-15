#!/usr/bin/env python3
from __future__ import annotations

"""Require one fresh successful three-layer research capture after candidate start.

This is a persistence gate, not a trading gate. It reuses the strict wrapper-status
verifier and then requires that the same fresh status cycle actually persisted at
least one new same-day capture. Zero-eligible and cadence-skipped cycles remain safe
for provisional runtime acceptance, but they cannot grant 24/7 boot persistence.
"""

import argparse
import importlib.util
import json
import math
import os
import sys
import time
from pathlib import Path

_SCRIPT_ROOT = Path(__file__).resolve().parent.parent
_STATUS_VERIFIER = _SCRIPT_ROOT / "deploy" / "verify-three-layer-validation-status.py"
_spec = importlib.util.spec_from_file_location("three_layer_status_verifier", _STATUS_VERIFIER)
if _spec is None or _spec.loader is None:
    raise RuntimeError("THREE_LAYER_FRESH_CAPTURE_VERIFIER_IMPORT_FAILED")
_status_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_status_module)
ThreeLayerStatusError = _status_module.ThreeLayerStatusError
verify_status = _status_module.verify


class FreshCaptureError(RuntimeError):
    pass


def _finite(value: object, code: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise FreshCaptureError(code)
    number = float(value)
    if not math.isfinite(number):
        raise FreshCaptureError(code)
    return number


def _read_status(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise FreshCaptureError("THREE_LAYER_FRESH_STATUS_FILE_INVALID")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise FreshCaptureError("THREE_LAYER_FRESH_STATUS_JSON_INVALID") from None


def verify_fresh_capture(
    status: object,
    *,
    release_sha: str,
    not_before: float,
    now: float | None = None,
    max_age_seconds: float = 600.0,
) -> dict:
    boundary = _finite(not_before, "THREE_LAYER_FRESH_NOT_BEFORE_INVALID")
    if boundary <= 0.0:
        raise FreshCaptureError("THREE_LAYER_FRESH_NOT_BEFORE_INVALID")
    try:
        base = verify_status(
            status,
            release_sha=release_sha,
            not_before=boundary,
            now=now,
            max_age_seconds=max_age_seconds,
        )
    except ThreeLayerStatusError as exc:
        raise FreshCaptureError(str(exc)) from None
    if not isinstance(status, dict):
        raise FreshCaptureError("THREE_LAYER_FRESH_STATUS_TYPE_INVALID")
    lane = status.get("same_day_three_layer")
    if not isinstance(lane, dict):
        raise FreshCaptureError("THREE_LAYER_FRESH_LANE_MISSING")

    attempted = lane.get("attempted_now")
    saved = lane.get("saved_now")
    if isinstance(attempted, bool) or not isinstance(attempted, int) or attempted < 1:
        raise FreshCaptureError("THREE_LAYER_FRESH_NO_ATTEMPT_AFTER_START")
    if isinstance(saved, bool) or not isinstance(saved, int) or saved < 1:
        raise FreshCaptureError("THREE_LAYER_FRESH_NO_SAVED_CAPTURE_AFTER_START")

    finished = _finite(status.get("finished_at"), "THREE_LAYER_FRESH_FINISHED_AT_INVALID")
    if finished < boundary:
        raise FreshCaptureError("THREE_LAYER_FRESH_STATUS_PREDATES_START")

    result = dict(base)
    result.update(
        {
            "acceptance": "PASS_THREE_LAYER_FRESH_CAPTURE_AFTER_START",
            "candidate_started_at": boundary,
            "accepted_status_finished_at": finished,
            "attempted_now": attempted,
            "saved_now": saved,
            "fresh_live_source_capture_proven": True,
        }
    )
    return result


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
        print("FAIL_THREE_LAYER_FRESH_CAPTURE:TIMEOUT_INVALID", file=sys.stderr)
        return 2
    deadline = time.monotonic() + args.timeout_seconds
    last = "THREE_LAYER_FRESH_CAPTURE_NOT_YET_AVAILABLE"
    status_path = args.status.expanduser().resolve()

    waitable = {
        "THREE_LAYER_FRESH_NO_ATTEMPT_AFTER_START",
        "THREE_LAYER_FRESH_NO_SAVED_CAPTURE_AFTER_START",
        "THREE_LAYER_STATUS_PREDATES_START",
        "THREE_LAYER_STATUS_STALE",
        "THREE_LAYER_WRAPPER_VERSION_MISSING",
    }
    while time.monotonic() < deadline:
        try:
            status = _read_status(status_path)
            payload = verify_fresh_capture(
                status,
                release_sha=args.release_sha,
                not_before=args.not_before,
                max_age_seconds=args.max_age_seconds,
            )
        except FreshCaptureError as exc:
            last = str(exc)
            if last in waitable or last in {
                "THREE_LAYER_FRESH_STATUS_FILE_INVALID",
                "THREE_LAYER_FRESH_STATUS_JSON_INVALID",
            }:
                time.sleep(1.0)
                continue
            print(f"FAIL_THREE_LAYER_FRESH_CAPTURE:{last}", file=sys.stderr)
            return 2

        text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
        if args.output is not None:
            output = args.output.expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
            os.chmod(output, 0o600)
        sys.stdout.write(text)
        return 0

    print(f"FAIL_THREE_LAYER_FRESH_CAPTURE_TIMEOUT:{last}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
