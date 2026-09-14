#!/usr/bin/env python3
from __future__ import annotations

"""Verify the active final PAPER cycle is using configured silent Synoptic/CWOP PWS."""

import argparse
import json
import os
import sys
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


def verify(status: object) -> dict:
    if not isinstance(status, dict):
        raise SynopticStatusError("SYNOPTIC_STATUS_TYPE_INVALID")
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
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        path = args.status.expanduser().resolve()
        if path.is_symlink() or not path.is_file():
            raise SynopticStatusError("SYNOPTIC_STATUS_FILE_INVALID")
        status = json.loads(path.read_text(encoding="utf-8"))
        payload = verify(status)
        code = 0
    except (OSError, UnicodeError, json.JSONDecodeError, SynopticStatusError) as exc:
        payload = {
            "acceptance": "FAIL_SYNOPTIC_PWS_RUNTIME_STATUS",
            "error": str(exc),
        }
        code = 2
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
