#!/usr/bin/env python3
from __future__ import annotations

"""Wait for and verify the first fresh all-weather PAPER V8 cycle."""

import argparse
import json
import os
import sys
import time
from pathlib import Path

_SCRIPT_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from polymarket_scanner.weather_only_all_paper_deployment_acceptance import (  # noqa: E402
    AllPaperDeploymentAcceptanceError,
    accept_first_all_paper_cycle,
)

WAIT_CODES = {
    "ALL_PAPER_STATUS_PREDATES_START",
    "ALL_PAPER_STATUS_STALE",
}


def _read_status(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_STATUS_FILE_INVALID")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise AllPaperDeploymentAcceptanceError("ALL_PAPER_STATUS_JSON_INVALID") from None


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
    last_code = "ALL_PAPER_STATUS_NOT_YET_AVAILABLE"
    while time.monotonic() < deadline:
        try:
            status = _read_status(args.status)
            acceptance = accept_first_all_paper_cycle(
                status,
                expected_release_sha=args.release_sha,
                not_before=args.not_before,
                max_age_seconds=args.max_age_seconds,
            )
        except AllPaperDeploymentAcceptanceError as exc:
            last_code = exc.code
            if exc.code not in WAIT_CODES and exc.code not in {
                "ALL_PAPER_STATUS_FILE_INVALID",
                "ALL_PAPER_STATUS_JSON_INVALID",
                # Parent wrappers atomically publish intermediate status snapshots.
                # Before the V8 wrapper finalizes, the V8 identity field is absent.
                "ALL_PAPER_V8_RUNTIME_VERSION_MISMATCH",
            }:
                print(f"FAIL_ALL_PAPER_FIRST_CYCLE:{exc.code}", file=sys.stderr)
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

    print(f"FAIL_ALL_PAPER_FIRST_CYCLE_TIMEOUT:{last_code}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
