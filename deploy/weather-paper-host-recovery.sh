#!/usr/bin/env bash
set -Eeuo pipefail

# Compatibility entrypoint only. Recovery authority is implemented by the separately
# provisioned/pinned host-trust v2 package. The exact immutable generation ID is
# mandatory; there is no mutable "latest rollback" namespace.
PATH=/usr/bin:/bin
export PATH
unset BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM || true
AUTHORITY="/usr/local/libexec/polymarket-weather-paper/v2/authority.py"
GENERATION_ID="${1:-}"
[[ "${GENERATION_ID}" =~ ^gen-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{12}-[0-9a-f]{12}$ ]] || {
  echo "usage: $0 <exact-generation-id>" >&2; exit 2;
}
[[ -f "${AUTHORITY}" && ! -L "${AUTHORITY}" ]] || {
  echo "independent host authority v2 is not provisioned" >&2; exit 2;
}
exec sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 \
  /usr/bin/python3 "${AUTHORITY}" recover --generation-id "${GENERATION_ID}"
