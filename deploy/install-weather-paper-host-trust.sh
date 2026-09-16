#!/usr/bin/env bash
set -Eeuo pipefail
echo 'REFUSED: candidate repositories cannot install or replace host authority v3. Bootstrap requires an independently obtained authority source, digest, policy, and policy digest.' >&2
exit 40
