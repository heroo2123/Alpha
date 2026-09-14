#!/usr/bin/env python3
from __future__ import annotations

"""Validate Synoptic/CWOP token and connectivity without printing the token."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

_SCRIPT_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from polymarket_scanner.weather_only_pws import (  # noqa: E402
    PWS_STATUS_AVAILABLE,
    PWS_STATUS_NO_FRESH_QC,
)
from polymarket_scanner.weather_only_synoptic_pws_guarded import (  # noqa: E402
    GuardedSynopticCWOPPWSClient,
)


REFERENCE_LATITUDE = 40.7769
REFERENCE_LONGITUDE = -73.8740


def _read_token(path: Path) -> str:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("SYNOPTIC_ENV_FILE_INVALID")
    values: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        stripped = raw.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        if key.strip() == "SYNOPTIC_PWS_TOKEN":
            values.append(value.strip())
    if len(values) != 1 or not values[0]:
        raise RuntimeError("SYNOPTIC_PWS_TOKEN_MISSING_OR_DUPLICATED")
    return values[0]


async def _check(token: str) -> dict:
    client = GuardedSynopticCWOPPWSClient(token=token)
    try:
        snapshot = await client.fetch_snapshot(
            latitude=REFERENCE_LATITUDE,
            longitude=REFERENCE_LONGITUDE,
            unit="F",
        )
    finally:
        await client.close()
    # Zero nearby fresh CWOP stations is not an authentication/network failure. The
    # guarded client distinguishes documented invalid-token/HTTP-error variants from
    # genuine zero-result responses before this acceptance check.
    if snapshot.status not in {PWS_STATUS_AVAILABLE, PWS_STATUS_NO_FRESH_QC}:
        raise RuntimeError(f"SYNOPTIC_PWS_PREFLIGHT_{snapshot.status}")
    return {
        "acceptance": "PASS_SYNOPTIC_PWS_PREFLIGHT",
        "status": snapshot.status,
        "observations": len(snapshot.observations),
        "configured": snapshot.configured,
        "predictive_only": True,
        "settlement_authority": False,
        "financial_authority": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        token = _read_token(args.env_file.expanduser().resolve())
        payload = asyncio.run(_check(token))
        code = 0
    except Exception as exc:
        payload = {
            "acceptance": "FAIL_SYNOPTIC_PWS_PREFLIGHT",
            "error": str(getattr(exc, "code", str(exc))),
        }
        code = 2
    text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        output.chmod(0o600)
    sys.stdout.write(text)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
