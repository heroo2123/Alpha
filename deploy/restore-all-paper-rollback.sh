#!/usr/bin/env bash
set -Eeuo pipefail

# Manual recovery wrapper. Exact immutable generation ID is mandatory; recovery code
# itself is independently provisioned host authority v2, never the candidate checkout.
GENERATION_ID="${1:-}"
AUTHORITY="/usr/local/libexec/polymarket-weather-paper/v2/authority.py"
[[ "${GENERATION_ID}" =~ ^gen-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{12}-[0-9a-f]{12}$ ]] || {
  echo 'usage: restore-all-paper-rollback.sh <exact-generation-id>' >&2; exit 2;
}
[[ -f "${AUTHORITY}" && ! -L "${AUTHORITY}" ]] || {
  echo 'HOST AUTHORITY V2 NOT INSTALLED' >&2; exit 2;
}
exec sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 \
  /usr/bin/python3 "${AUTHORITY}" recover --generation-id "${GENERATION_ID}"
