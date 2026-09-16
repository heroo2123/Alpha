#!/usr/bin/env bash
set -Eeuo pipefail
AUTH=/usr/local/libexec/polymarket-weather-paper-v3/authority.py
UNIT=polymarket-weather-paper.service
SHA="${1:-}"; GEN="${2:-}"
fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ "${SHA}" =~ ^[0-9a-f]{40}$ && "${GEN}" =~ ^[0-9a-f]{64}$ ]] || fail "usage: $0 <candidate-sha> <cutover-generation-id>"
[[ -f "${AUTH}" ]] || fail "independent host authority v3 not installed"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null || systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then
  fail "candidate preparation requires stopped and disabled service"
fi
host(){ sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 GIT_CONFIG_NOSYSTEM=1 /usr/bin/python3 "${AUTH}" "$@"; }
host authority-info >/dev/null
host verify-generation --generation-id "${GEN}" --candidate-sha "${SHA}"
MUTATED=0
rollback(){ code=$?; if (( code != 0 && MUTATED == 1 )); then host recover --generation-id "${GEN}" || true; fi; exit "$code"; }
trap rollback EXIT
# The trusted authority, not candidate code, archives the exact candidate Git object,
# creates the clean root-owned venv, validates the lock/inventory, and renders/installs
# the root-owned unit. Candidate checkout mutation happens only after sealing succeeds.
MUTATED=1
host prepare-candidate --generation-id "${GEN}" --candidate-sha "${SHA}"
host verify-runtime-files --generation-id "${GEN}" --candidate-sha "${SHA}"
host activate-checkout --generation-id "${GEN}" --candidate-sha "${SHA}"
host verify-checkout --generation-id "${GEN}" --candidate-sha "${SHA}"
host verify-runtime-files --generation-id "${GEN}" --candidate-sha "${SHA}"
MUTATED=0; trap - EXIT
printf 'PASS: immutable V10 candidate prepared by independent authority. Release=%s Generation=%s\n' "${SHA}" "${GEN}"
