#!/usr/bin/env python3
from __future__ import annotations

"""Run the weather PAPER public-provider connectivity gate on the target host."""

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

# Deployment shell scripts invoke this file by absolute path and may run from any CWD.
# Put the repository root on sys.path explicitly so the application package is always
# importable without relying on PYTHONPATH or an editable package install.
_SCRIPT_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from polymarket_scanner.weather_only_network_preflight import (  # noqa: E402
    WeatherNetworkPreflightError,
    check_weather_paper_network,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        report = asyncio.run(check_weather_paper_network())
        payload = report.as_dict()
        payload["acceptance"] = (
            "PASS_REQUIRED_WEATHER_NETWORK" if report.required_passed
            else "FAIL_REQUIRED_WEATHER_NETWORK"
        )
        exit_code = 0 if report.required_passed else 2
    except (WeatherNetworkPreflightError, OSError, RuntimeError) as exc:
        payload = {
            "acceptance": "FAIL_REQUIRED_WEATHER_NETWORK",
            "error": getattr(exc, "code", str(exc)),
        }
        exit_code = 2

    text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        os.chmod(output, 0o600)
    sys.stdout.write(text)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
