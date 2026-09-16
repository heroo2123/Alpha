#!/usr/bin/env bash
set -Eeuo pipefail

# Candidate-side verification ONLY.  Host trust v2 is provisioned independently by
# an administrator from a separately pinned/versioned artifact.  Ordinary candidate
# deployment is never allowed to install, replace, or rewrite release/recovery/
# snapshot authority or its root-owned policy files.
PATH=/usr/bin:/bin
export PATH
unset BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM || true

AUTHORITY="/usr/local/libexec/polymarket-weather-paper/v2/authority.py"
AUTHORITY_MANIFEST="/etc/polymarket-weather-paper/host-authority-v2.json"
APPROVAL_MANIFEST="/etc/polymarket-weather-paper/approved-releases-v2.json"
HOST_PATHS="/etc/polymarket-weather-paper/host-paths-v2.json"

fail(){ printf 'HOST TRUST VERIFY ERROR: %s\n' "$*" >&2; exit 1; }

for path in "${AUTHORITY}" "${AUTHORITY_MANIFEST}" "${APPROVAL_MANIFEST}" "${HOST_PATHS}"; do
  [[ -f "${path}" && ! -L "${path}" ]] || fail "independently provisioned host-trust v2 component missing: ${path}"
  [[ "$(stat -c '%u' "${path}")" == "0" ]] || fail "host-trust component is not root owned: ${path}"
  mode="$(stat -c '%a' "${path}")"
  (( (8#${mode} & 8#22) == 0 )) || fail "host-trust component writable by nonroot: ${path}"
done

/usr/bin/python3 "${AUTHORITY}" verify-authority

cat <<'EOF'
PASS: independently provisioned weather-paper host authority v2 verified.
This candidate did NOT install or replace host release, recovery, snapshot, or trust-policy code.
Candidate approval data must be provisioned independently in approved-releases-v2.json.
EOF
