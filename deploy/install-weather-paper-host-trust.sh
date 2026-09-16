#!/usr/bin/env bash
set -Eeuo pipefail
echo "REFUSED: host authority v2 must be installed from an independently pinned bundle; an application candidate cannot install or replace its own release/rollback authority." >&2
exit 64
