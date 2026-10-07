"""Generate an offline funding decision inventory; never enables execution."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_scanner.v11.microcanary_prep import (
    CanaryRefusal, credential_file_present, funding_decision_packet,
)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CanaryRefusal("DUPLICATE_INPUT_KEY")
        result[key] = value
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path, help="JSON with optional scope, evidence, credential_path")
    args = parser.parse_args()
    try:
        raw = args.request.read_bytes()
        if len(raw) > 65536:
            raise CanaryRefusal("INPUT_BYTES_BOUND")
        request = json.loads(raw, object_pairs_hook=_unique_pairs)
        if type(request) is not dict or set(request) - {"scope", "evidence", "credential_path"}:
            raise CanaryRefusal("INPUT_SCHEMA")
        credential_path = request.get("credential_path")
        if credential_path is not None and type(credential_path) is not str:
            raise CanaryRefusal("CREDENTIAL_PATH_INVALID")
        report = funding_decision_packet(
            scope=request.get("scope"), evidence=request.get("evidence"),
            credential_present=credential_file_present(credential_path) if credential_path else False,
        )
    except (CanaryRefusal, OSError, ValueError, TypeError) as exc:
        print(str(exc) if isinstance(exc, CanaryRefusal) else "PACKET_INPUT_INVALID", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
