#!/usr/bin/env python3
from __future__ import annotations

"""Compatibility shim only; this candidate does not implement release authority.

The executable release gate is the independently provisioned, root-pinned host-trust
v2 component at /usr/local/libexec/polymarket-weather-paper/v2/authority.py.
Ordinary candidate deployment never installs this repository file as privileged code.
"""

import os
import sys

HOST_AUTHORITY = "/usr/local/libexec/polymarket-weather-paper/v2/authority.py"


def main() -> int:
    if not os.path.isfile(HOST_AUTHORITY):
        print("FAIL_HOST_AUTHORITY_V2_NOT_PROVISIONED", file=sys.stderr)
        return 2
    os.execv("/usr/bin/python3", ["/usr/bin/python3", HOST_AUTHORITY, *sys.argv[1:]])
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
