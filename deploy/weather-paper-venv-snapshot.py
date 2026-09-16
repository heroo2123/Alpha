#!/usr/bin/env python3
from __future__ import annotations

"""Compatibility shim; predecessor venv trust lives in host authority v2.

The corrective architecture does not archive/reconstruct the predecessor environment.
The independent host authority records an exact predecessor venv tree manifest inside
the immutable cutover generation and verifies that same environment in place at
recovery. Candidate B is built in a separate SHA-scoped venv.
"""

import os
import sys

HOST_AUTHORITY = "/usr/local/libexec/polymarket-weather-paper/v2/authority.py"


def main() -> int:
    print(
        "weather-paper-venv-snapshot.py is superseded by independently provisioned "
        "host authority v2; candidate code cannot snapshot/restore trusted venvs.",
        file=sys.stderr,
    )
    if not os.path.isfile(HOST_AUTHORITY):
        return 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
