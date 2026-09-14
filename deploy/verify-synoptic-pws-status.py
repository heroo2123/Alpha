#!/usr/bin/env python3
from __future__ import annotations

"""Wait for and verify fresh configured silent Synoptic/CWOP PWS runtime status."""

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

from polymarket_scanner.weather_only_live_paper_synoptic import (  # noqa: E402
    SYNOPTIC_PWS_RUNTIME_VERSION,
)
from polymarket_scanner.weather_only_synoptic_pws import (  # noqa: E402
    SYNOPTIC_CWOP_NETWORK_ID,
)


class SynopticStatusError(RuntimeError):
    pass


def _sha(value: object) -> str | None:
    text = str(value or "").strip().lower()
    if len(text) != 40 or any(char not in "0123456789abcdef" for char in text):
        return None
    return text


def _read_status(path: Path) -> object:
    if path.is_symlink() or not path.is_file():
        raise SynopticStatusError("SYNOPTIC_STATUS_FILE_INVALID")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise SynopticStatusError("SYNOPTIC_STATUS_JSON_INVALID") from None


def _wait_reason(
    status: object,
    *,
    expected_release_sha: str,
    not_before: float,
    max_age_seconds: float,
    now: float | None = None,
) -> str | None:
    """Wait only for old/intermediate snapshots from the exact candidate.

    The inherited final service atomically publishes its status before the Synoptic
    wrapper adds provider fields. That intermediate file must not cause a false failed
    deployment. Wrong-release or already-claimed Synoptic snapshots never get waited
    past; strict verification handles them immediately.
    """
    if not isinstance(status, dict):
        return None
    finished = status.get("finished_at")
    if not isinstance(finished, (int, float)) or isinstance(finished, bool):
        return None
    finished = float(finished)
    if not math.isfinite(finished):
        return None
    if finished < float(not_before):
        return "SYNOPTIC_STATUS_PREDATES_START"
    observed = _sha(status.get("release_sha"))
    expected = _sha(expected_release_sha)
    if expected is None or observed != expected:
        return None
    current = float(time.time() if now is None else now)
    if current - finished > float(max_age_seconds):
        return "SYNOPTIC_STATUS_STALE"
    if status.get("synoptic_pws_runtime_version") in (None, ""):
        return "SYNOPTIC_STATUS_INTERMEDIATE_WRAPPER"
    return None


def verify(status: object, *, expected_release_sha: str | None = None) -> dict:
    if not isinstance(status, dict):
        raise SynopticStatusError("SYNOPTIC_STATUS_TYPE_INVALID")
    if expected_release_sha is not None:
        expected = _sha(expected_release_sha)
        observed = _sha(status.get("release_sha"))
        if expected is None or observed != expected:
            raise SynopticStatusError("SYNOPTIC_RELEASE_SHA_MISMATCH")
    if status.get("synoptic_pws_runtime_version") != SYNOPTIC_PWS_RUNTIME_VERSION:
        raise SynopticStatusError("SYNOPTIC_RUNTIME_VERSION_MISSING")
    if status.get("pws_provider") != "SYNOPTIC_CWOP":
        raise SynopticStatusError("SYNOPTIC_PROVIDER_NOT_ACTIVE")
    if str(status.get("pws_network_id") or "") != SYNOPTIC_CWOP_NETWORK_ID:
        raise SynopticStatusError("SYNOPTIC_NETWORK_ID_MISMATCH")
    if status.get("pws_configured") is not True:
        raise SynopticStatusError("SYNOPTIC_PWS_NOT_CONFIGURED")
    if status.get("pws_predictive_only") is not True:
        raise SynopticStatusError("SYNOPTIC_PWS_PREDICTIVE_BOUNDARY_MISSING")
    for key, code in (
        ("pws_may_replace_official_observation", "SYNOPTIC_PWS_OFFICIAL_REPLACEMENT_NOT_FALSE"),
        ("pws_may_reweight_probability", "SYNOPTIC_PWS_REWEIGHT_NOT_FALSE"),
        ("same_day_delivery_enabled", "SYNOPTIC_SAME_DAY_DELIVERY_NOT_FALSE"),
        ("financial_authority", "SYNOPTIC_FINANCIAL_AUTHORITY_NOT_FALSE"),
        ("automatic_order_placement", "SYNOPTIC_ORDER_PLACEMENT_NOT_FALSE"),
    ):
        if status.get(key) is not False:
            raise SynopticStatusError(code)
    return {
        "acceptance": "PASS_SYNOPTIC_PWS_RUNTIME_STATUS",
        "runtime_version": SYNOPTIC_PWS_RUNTIME_VERSION,
        "provider": "SYNOPTIC_CWOP",
        "network_id": SYNOPTIC_CWOP_NETWORK_ID,
        "configured": True,
        "predictive_only": True,
        "same_day_delivery_enabled": False,
        "financial_authority": False,
        "automatic_order_placement": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--status", type=Path, required=True)
    parser.add_argument("--release-sha")
    parser.add_argument("--not-before", type=float)
    parser.add_argument("--timeout-seconds", type=float, default=60.0)
    parser.add_argument("--max-age-seconds", type=float, default=600.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    wait_mode = args.release_sha is not None or args.not_before is not None
    if wait_mode and (args.release_sha is None or args.not_before is None):
        print("FAIL_SYNOPTIC_PWS_RUNTIME_STATUS:WAIT_ARGS_INCOMPLETE", file=sys.stderr)
        return 2
    if args.timeout_seconds <= 0.0 or args.timeout_seconds > 600.0:
        print("FAIL_SYNOPTIC_PWS_RUNTIME_STATUS:TIMEOUT_INVALID", file=sys.stderr)
        return 2
    if args.max_age_seconds <= 0.0 or args.max_age_seconds > 1800.0:
        print("FAIL_SYNOPTIC_PWS_RUNTIME_STATUS:MAX_AGE_INVALID", file=sys.stderr)
        return 2

    path = args.status.expanduser().resolve()
    deadline = time.monotonic() + (args.timeout_seconds if wait_mode else 0.0)
    last_error = "SYNOPTIC_STATUS_NOT_YET_AVAILABLE"
    while True:
        try:
            status = _read_status(path)
            if wait_mode:
                reason = _wait_reason(
                    status,
                    expected_release_sha=args.release_sha,
                    not_before=args.not_before,
                    max_age_seconds=args.max_age_seconds,
                )
                if reason is not None:
                    last_error = reason
                    if time.monotonic() >= deadline:
                        raise SynopticStatusError(f"SYNOPTIC_STATUS_TIMEOUT:{last_error}")
                    time.sleep(0.25)
                    continue
            payload = verify(status, expected_release_sha=args.release_sha)
            code = 0
            break
        except SynopticStatusError as exc:
            last_error = str(exc)
            if wait_mode and exc.args and exc.args[0] in {
                "SYNOPTIC_STATUS_FILE_INVALID",
                "SYNOPTIC_STATUS_JSON_INVALID",
            } and time.monotonic() < deadline:
                time.sleep(0.25)
                continue
            payload = {
                "acceptance": "FAIL_SYNOPTIC_PWS_RUNTIME_STATUS",
                "error": last_error,
            }
            code = 2
            break

    text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        os.chmod(output, 0o600)
    print(text, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
