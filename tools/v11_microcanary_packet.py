"""Generate an offline funding decision inventory; never enables execution."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import stat
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from polymarket_scanner.v11.microcanary_prep import (
    CanaryRefusal, credential_file_present, funding_decision_packet,
)

INPUT_BYTES_LIMIT = 65536


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise CanaryRefusal("DUPLICATE_INPUT_KEY")
        result[key] = value
    return result


def _read_bounded(path: Path) -> bytes:
    """Read at most one byte past the cap from the checked file descriptor."""
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) |
                     getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise CanaryRefusal("REQUEST_PATH_INVALID") from None
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise CanaryRefusal("REQUEST_NOT_REGULAR_FILE")
        if info.st_size > INPUT_BYTES_LIMIT:
            raise CanaryRefusal("INPUT_BYTES_BOUND")
        with os.fdopen(fd, "rb") as stream:
            fd = -1
            raw = stream.read(INPUT_BYTES_LIMIT + 1)
        if len(raw) > INPUT_BYTES_LIMIT:
            raise CanaryRefusal("INPUT_BYTES_BOUND")
        return raw
    finally:
        if fd != -1:
            os.close(fd)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path, help="JSON with optional scope, evidence, credential_path")
    args = parser.parse_args()
    try:
        raw = _read_bounded(args.request)
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
    except (CanaryRefusal, OSError, ValueError, TypeError, RecursionError) as exc:
        print(str(exc) if isinstance(exc, CanaryRefusal) else "PACKET_INPUT_INVALID", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
