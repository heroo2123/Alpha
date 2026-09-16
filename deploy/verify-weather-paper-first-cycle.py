#!/usr/bin/env python3
from __future__ import annotations

"""Wait for and verify the first fresh canonical weather PAPER cycle."""

import argparse
import json
import math
import os
import sys
import time
from pathlib import Path

# This verifier is invoked by absolute path from deployment shell code. Make the
# repository root explicit so the application package remains importable regardless
# of the operator's current working directory.
_SCRIPT_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from polymarket_scanner.weather_only_deployment_acceptance import (  # noqa: E402
    WeatherDeploymentAcceptanceError,
    accept_first_weather_paper_cycle,
)


STALE_WAIT_CODES = {
    "DEPLOY_STATUS_PREDATES_START",
    "DEPLOY_STATUS_STALE",
}
INTERMEDIATE_WAIT_CODE = "DEPLOY_STATUS_INTERMEDIATE_FINALIZATION"


def _read_status(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise WeatherDeploymentAcceptanceError("DEPLOY_STATUS_FILE_INVALID")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise WeatherDeploymentAcceptanceError("DEPLOY_STATUS_JSON_INVALID") from None


def _normalized_sha(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if len(text) != 40 or any(ch not in "0123456789abcdef" for ch in text):
        return None
    return text


def _wait_reason_before_strict_acceptance(
    status: object,
    *,
    expected_release_sha: str,
    not_before: float,
) -> str | None:
    """Recognize only snapshots that are safe to wait past during first-cycle startup.

    The layered runtime atomically publishes inherited base/V4/corrective snapshots
    before the final wrapper publishes the canonical final snapshot.  Those transient
    files are not deployment evidence and must never be accepted, but they also must
    not make the guarded start fail before the final wrapper gets a chance to write.

    Old pre-start snapshots are likewise waitable regardless of their release/version.
    Any fresh snapshot that claims to be final is left to the strict acceptance checker
    unchanged so release, identity, and all safety drift still fail closed immediately.
    """
    if not isinstance(status, dict):
        return None

    finished = status.get("finished_at")
    if isinstance(finished, (int, float)) and not isinstance(finished, bool):
        value = float(finished)
        if math.isfinite(value) and value < float(not_before):
            return "DEPLOY_STATUS_PREDATES_START"

    expected = _normalized_sha(expected_release_sha)
    observed = _normalized_sha(status.get("release_sha"))
    if (
        expected is not None
        and observed == expected
        and status.get("final_paper_runtime_version") in (None, "")
    ):
        return INTERMEDIATE_WAIT_CODE
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--not-before", type=float, required=True)
    parser.add_argument("--timeout-seconds", type=float, default=600.0)
    parser.add_argument("--max-age-seconds", type=float, default=600.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.timeout_seconds <= 0.0 or args.timeout_seconds > 1800.0:
        print("FAIL: timeout must be >0 and <=1800 seconds", file=sys.stderr)
        return 2
    deadline = time.monotonic() + args.timeout_seconds
    last_code = "DEPLOY_STATUS_NOT_YET_AVAILABLE"

    while time.monotonic() < deadline:
        try:
            status = _read_status(args.status)
            wait_reason = _wait_reason_before_strict_acceptance(
                status,
                expected_release_sha=args.release_sha,
                not_before=args.not_before,
            )
            if wait_reason is not None:
                last_code = wait_reason
                time.sleep(1.0)
                continue
            acceptance = accept_first_weather_paper_cycle(
                status,
                expected_release_sha=args.release_sha,
                not_before=args.not_before,
                max_age_seconds=args.max_age_seconds,
            )
        except WeatherDeploymentAcceptanceError as exc:
            last_code = exc.code
            # A missing/stale pre-start status is expected while the first live cycle
            # is running. Any fresh final semantic/safety failure is terminal and lets
            # the caller's rollback trap stop the service immediately.
            if exc.code not in STALE_WAIT_CODES and exc.code not in {
                "DEPLOY_STATUS_FILE_INVALID",
                "DEPLOY_STATUS_JSON_INVALID",
            }:
                print(f"FAIL_FIRST_CYCLE:{exc.code}", file=sys.stderr)
                return 2
            time.sleep(1.0)
            continue

        payload = acceptance.as_dict()
        payload["status_path"] = str(args.status.expanduser().resolve())
        text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
        if args.output is not None:
            output = args.output.expanduser().resolve()
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(text, encoding="utf-8")
            os.chmod(output, 0o600)
        sys.stdout.write(text)
        return 0

    print(f"FAIL_FIRST_CYCLE_TIMEOUT:{last_code}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
