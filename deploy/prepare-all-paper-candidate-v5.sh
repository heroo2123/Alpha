#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
AUTH=/usr/local/libexec/polymarket-weather-paper-v3/authority.py
UNIT=polymarket-weather-paper.service
SHA="${1:-}"; GEN="${2:-}"; SOURCE_REF="${3:-weather-all-paper-post-final-review-corrective-2026-09-16}"
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${SHA}" =~ ^[0-9a-f]{40}$ && "${GEN}" =~ ^[0-9a-f]{64}$ ]] || fail "usage: $0 <candidate-sha> <cutover-generation> [source-ref]"
[[ -x "${AUTH}" && -d "${APP_DIR}/.git" ]] || fail "host authority/app checkout missing"
systemctl is-active --quiet "${UNIT}" 2>/dev/null && fail "candidate preparation requires stopped service"
systemctl is-enabled --quiet "${UNIT}" 2>/dev/null && fail "candidate preparation requires disabled persistence"
/usr/bin/git -C "${APP_DIR}" fetch --no-tags origin "${SOURCE_REF}"
[[ "$(/usr/bin/git -C "${APP_DIR}" rev-parse "${SHA}^{commit}")" == "${SHA}" ]] || fail "candidate object unavailable"
if ! /usr/bin/git -C "${APP_DIR}" merge-base --is-ancestor "${SHA}" FETCH_HEAD; then
  fail "candidate SHA is not contained in requested source ref"
fi
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 \
  /usr/bin/python3 "${AUTH}" verify-generation --generation-id "${GEN}" --candidate-sha "${SHA}"
# The authority builds source+venv into a root-owned release and installs the unit it
# rendered from independently pinned policy. Candidate code cannot choose those paths
# or mutate the executable release afterward.
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 \
  /usr/bin/python3 "${AUTH}" prepare-candidate --generation-id "${GEN}" --candidate-sha "${SHA}"
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 \
  /usr/bin/python3 "${AUTH}" activate-checkout --generation-id "${GEN}" --candidate-sha "${SHA}"
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 \
  /usr/bin/python3 "${AUTH}" verify-runtime-files --generation-id "${GEN}" --candidate-sha "${SHA}"
printf 'PASS: immutable V10 candidate prepared. SHA=%s generation=%s; service remains STOPPED/DISABLED.\n' "${SHA}" "${GEN}"
