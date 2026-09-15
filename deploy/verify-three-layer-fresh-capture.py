#!/usr/bin/env python3
from __future__ import annotations

"""Require one fresh successful three-layer research capture after candidate start.

This is a persistence gate, not a trading gate. It first reuses the strict current
wrapper-status verifier, then proves from the durable SQLite attempt/capture audit that
at least one SAVED three-layer capture was created after this candidate's start time.
Using the durable audit avoids a narrow timing window where the next cadence-skipped
cycle overwrites ``saved_now`` in status even though a valid post-start capture exists.
"""

import argparse
import importlib.util
import json
import math
import os
import sqlite3
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


def _durable_saved_capture(db_path: Path, *, not_before: float) -> dict:
    target = db_path.expanduser().resolve()
    if target.is_symlink() or not target.is_file():
        raise FreshCaptureError("THREE_LAYER_FRESH_DATABASE_INVALID")
    try:
        db = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=5.0)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA busy_timeout=5000")
        row = db.execute(
            """
            SELECT a.id AS attempt_id,
                   a.event_id AS attempt_event_id,
                   a.station AS attempt_station,
                   a.target_date AS attempt_target_date,
                   a.family AS attempt_family,
                   a.unit AS attempt_unit,
                   a.attempted_at,
                   a.completed_at,
                   a.capture_sha256 AS attempt_capture_sha256,
                   c.id AS capture_id,
                   c.event_id AS capture_event_id,
                   c.station AS capture_station,
                   c.target_date AS capture_target_date,
                   c.family AS capture_family,
                   c.unit AS capture_unit,
                   c.as_of AS capture_as_of,
                   c.capture_sha256 AS capture_sha256
              FROM weather_same_day_capture_attempts AS a
              JOIN weather_same_day_captures AS c
                ON c.capture_sha256=a.capture_sha256
             WHERE a.outcome='SAVED'
               AND a.attempted_at>=?
               AND a.completed_at IS NOT NULL
               AND a.capture_sha256 IS NOT NULL
             ORDER BY a.completed_at DESC, a.id DESC
             LIMIT 1
            """,
            (float(not_before),),
        ).fetchone()
    except sqlite3.Error as exc:
        raise FreshCaptureError("THREE_LAYER_FRESH_DATABASE_QUERY_FAILED") from exc
    finally:
        try:
            db.close()
        except Exception:
            pass

    if row is None:
        raise FreshCaptureError("THREE_LAYER_FRESH_NO_DURABLE_SAVED_CAPTURE_AFTER_START")

    attempted_at = _finite(row["attempted_at"], "THREE_LAYER_FRESH_ATTEMPT_TIME_INVALID")
    completed_at = _finite(row["completed_at"], "THREE_LAYER_FRESH_COMPLETION_TIME_INVALID")
    capture_as_of = _finite(row["capture_as_of"], "THREE_LAYER_FRESH_CAPTURE_TIME_INVALID")
    if attempted_at < not_before or capture_as_of < attempted_at or completed_at < capture_as_of:
        raise FreshCaptureError("THREE_LAYER_FRESH_DURABLE_TIME_ORDER_INVALID")

    attempt_digest = str(row["attempt_capture_sha256"] or "").strip().lower()
    capture_digest = str(row["capture_sha256"] or "").strip().lower()
    if (
        attempt_digest != capture_digest
        or len(capture_digest) != 64
        or any(ch not in "0123456789abcdef" for ch in capture_digest)
    ):
        raise FreshCaptureError("THREE_LAYER_FRESH_DURABLE_DIGEST_MISMATCH")

    identities = (
        ("event_id", row["attempt_event_id"], row["capture_event_id"]),
        ("station", row["attempt_station"], row["capture_station"]),
        ("target_date", row["attempt_target_date"], row["capture_target_date"]),
        ("family", row["attempt_family"], row["capture_family"]),
        ("unit", row["attempt_unit"], row["capture_unit"]),
    )
    if any(str(left or "") != str(right or "") for _name, left, right in identities):
        raise FreshCaptureError("THREE_LAYER_FRESH_DURABLE_IDENTITY_MISMATCH")

    return {
        "attempt_id": int(row["attempt_id"]),
        "capture_id": int(row["capture_id"]),
        "event_id": str(row["capture_event_id"]),
        "station": str(row["capture_station"]),
        "target_date": str(row["capture_target_date"]),
        "family": str(row["capture_family"]),
        "unit": str(row["capture_unit"]),
        "attempted_at": attempted_at,
        "capture_as_of": capture_as_of,
        "completed_at": completed_at,
        "capture_sha256": capture_digest,
    }


def verify_fresh_capture(
    status: object,
    *,
    release_sha: str,
    not_before: float,
    now: float | None = None,
    max_age_seconds: float = 600.0,
    db_path: Path | None = None,
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

    finished = _finite(status.get("finished_at"), "THREE_LAYER_FRESH_FINISHED_AT_INVALID")
    if finished < boundary:
        raise FreshCaptureError("THREE_LAYER_FRESH_STATUS_PREDATES_START")

    if db_path is None:
        # Backward-compatible pure-function path retained for unit fixtures. Deployment
        # never uses this branch: the CLI requires --db and proves durable evidence.
        attempted = lane.get("attempted_now")
        saved = lane.get("saved_now")
        if isinstance(attempted, bool) or not isinstance(attempted, int) or attempted < 1:
            raise FreshCaptureError("THREE_LAYER_FRESH_NO_ATTEMPT_AFTER_START")
        if isinstance(saved, bool) or not isinstance(saved, int) or saved < 1:
            raise FreshCaptureError("THREE_LAYER_FRESH_NO_SAVED_CAPTURE_AFTER_START")
        durable = None
    else:
        durable = _durable_saved_capture(db_path, not_before=boundary)
        attempted = int(lane.get("attempted_now") or 0)
        saved = int(lane.get("saved_now") or 0)

    result = dict(base)
    result.update(
        {
            "acceptance": "PASS_THREE_LAYER_FRESH_CAPTURE_AFTER_START",
            "candidate_started_at": boundary,
            "accepted_status_finished_at": finished,
            "attempted_now": attempted,
            "saved_now": saved,
            "durable_saved_capture": durable,
            "fresh_live_source_capture_proven": True,
            "durable_capture_audit_used": db_path is not None,
        }
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
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
    db_path = args.db.expanduser().resolve()

    waitable = {
        "THREE_LAYER_FRESH_NO_DURABLE_SAVED_CAPTURE_AFTER_START",
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
                db_path=db_path,
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
