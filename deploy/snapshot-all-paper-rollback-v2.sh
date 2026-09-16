#!/usr/bin/env bash
set -Eeuo pipefail

# Backward-compatible operator wrapper for independent host-trust v2 snapshot.
CANDIDATE_SHA="${1:-}"
AUTHORITY="/usr/local/libexec/polymarket-weather-paper/v2/authority.py"
[[ "${CANDIDATE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] || {
  echo 'usage: snapshot-all-paper-rollback-v2.sh <exact-candidate-sha>' >&2; exit 2;
}
[[ -f "${AUTHORITY}" && ! -L "${AUTHORITY}" ]] || {
  echo 'INDEPENDENT HOST AUTHORITY V2 NOT INSTALLED' >&2; exit 2;
}
exec sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 \
  /usr/bin/python3 "${AUTHORITY}" snapshot --candidate-sha "${CANDIDATE_SHA,,}"
