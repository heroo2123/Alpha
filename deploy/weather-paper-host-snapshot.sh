#!/usr/bin/env bash
set -Eeuo pipefail

# Compatibility entrypoint only. Snapshot authority is implemented by the separately
# provisioned/pinned host-trust v2 package, never by the candidate checkout.
PATH=/usr/bin:/bin
export PATH
unset BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM || true
AUTHORITY="/usr/local/libexec/polymarket-weather-paper/v2/authority.py"
CANDIDATE_SHA="${1:-}"
[[ "${CANDIDATE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] || {
  echo "usage: $0 <exact-candidate-sha>" >&2; exit 2;
}
[[ -f "${AUTHORITY}" && ! -L "${AUTHORITY}" ]] || {
  echo "independent host authority v2 is not provisioned" >&2; exit 2;
}
exec sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 \
  /usr/bin/python3 "${AUTHORITY}" snapshot --candidate-sha "${CANDIDATE_SHA,,}"
