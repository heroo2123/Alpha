#!/usr/bin/env bash
set -Eeuo pipefail

# Explicit PAPER-only start gate for the guarded pure three-layer validation runtime.
# Candidate acceptance never grants boot persistence and rolls back to stopped+disabled
# if runtime identity, first-cycle safety, or wrapper-specific acceptance fails.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
STATUS_PATH="${WEATHER_PAPER_STATUS_PATH:-/var/lib/polymarket-weather-paper/status.json}"
UNIT="polymarket-weather-paper.service"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
START_EPOCH_FILE="${CONFIG_DIR}/weather-paper-three-layer-start.epoch"
ATTESTATION_OUT="${CONFIG_DIR}/weather-paper-active-attestation.json"
FIRST_CYCLE_OUT="${CONFIG_DIR}/weather-paper-first-cycle-acceptance.json"
THREE_LAYER_OUT="${CONFIG_DIR}/weather-paper-three-layer-runtime-acceptance.json"
EXPECTED_SHA="${1:-}"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]] \
  || fail "usage: $0 <exact-40-char-lowercase-release-sha>"
[[ -d "${APP_DIR}/.git" ]] || fail "missing isolated weather-paper git checkout: ${APP_DIR}"
[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "missing weather-paper app virtualenv"
[[ -f "${RELEASE_FILE}" ]] || fail "missing weather-paper release marker: ${RELEASE_FILE}"

HEAD_SHA="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
MARKER_SHA="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${HEAD_SHA}" == "${EXPECTED_SHA}" ]] || fail "checkout is not the explicitly approved candidate"
[[ "${MARKER_SHA}" == "${EXPECTED_SHA}" ]] || fail "release marker is not the explicitly approved candidate"

if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then
  fail "${UNIT} is already active; refusing an ambiguous/repeated start"
fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then
  fail "${UNIT} is already enabled; candidate acceptance requires a non-persistent unit"
fi

# Preflight verifies service isolation, network prerequisites and a restorable ledger
# backup, then installs the candidate unit while leaving it stopped and disabled.
bash "${APP_DIR}/deploy/preflight-weather-paper-deployment.sh"

start_attempted=0
rollback_on_error(){
  code=$?
  if (( code != 0 )) && (( start_attempted == 1 )); then
    printf 'Start acceptance failed; stopping and disabling weather PAPER service...\n' >&2
    sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
    sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true
    rm -f "${START_EPOCH_FILE}" >/dev/null 2>&1 || true
  fi
  exit "${code}"
}
trap rollback_on_error EXIT

# Epoch status timestamps are floating point. Keep the acceptance boundary at matching
# subsecond resolution so a stale same-SHA status written earlier in this wall-clock
# second cannot satisfy the new-start freshness gate. Persist this exact boundary for
# the later boot-persistence gate: 24/7 enablement must prove that at least one actual
# WRH+NWS+GEFS capture was saved after this candidate start.
START_ACCEPTANCE_EPOCH="$("${APP_DIR}/.venv/bin/python" -c 'import time; print(f"{time.time():.9f}")')"
mkdir -p "${CONFIG_DIR}"
umask 077
TMP_START="$(mktemp "${CONFIG_DIR}/.weather-paper-three-layer-start.XXXXXX")"
printf '%s\n' "${START_ACCEPTANCE_EPOCH}" > "${TMP_START}"
chmod 600 "${TMP_START}"
mv -f "${TMP_START}" "${START_EPOCH_FILE}"

start_attempted=1
sudo systemctl start "${UNIT}"

for _ in $(seq 1 20); do
  if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then
    break
  fi
  sleep 1
done
systemctl is-active --quiet "${UNIT}" 2>/dev/null \
  || fail "weather PAPER service did not become active"

"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/attest-weather-paper-runtime.py" \
  --app-dir "${APP_DIR}" \
  --release-file "${RELEASE_FILE}" \
  --db "${DB_PATH}" \
  --require-active \
  --output "${ATTESTATION_OUT}"

# First prove all inherited final PAPER invariants and same-day containment.
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/verify-weather-paper-first-cycle.py" \
  --status "${STATUS_PATH}" \
  --release-sha "${EXPECTED_SHA}" \
  --not-before "${START_ACCEPTANCE_EPOCH}" \
  --timeout-seconds 600 \
  --max-age-seconds 600 \
  --output "${FIRST_CYCLE_OUT}"

# Then prove the final atomic status came from this exact three-layer wrapper, PWS is
# absent, research coverage was not truncated, and every same-day authority stays off.
# A zero-attempt/cadence-skipped first cycle is safe for this provisional active state;
# the separate persistence gate will refuse boot enablement until a real fresh capture
# is saved after START_ACCEPTANCE_EPOCH.
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/verify-three-layer-validation-status.py" \
  --status "${STATUS_PATH}" \
  --release-sha "${EXPECTED_SHA}" \
  --not-before "${START_ACCEPTANCE_EPOCH}" \
  --timeout-seconds 60 \
  --max-age-seconds 600 \
  --output "${THREE_LAYER_OUT}"

HEAD_AFTER="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
MARKER_AFTER="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${HEAD_AFTER}" == "${EXPECTED_SHA}" ]] || fail "checkout changed during start acceptance"
[[ "${MARKER_AFTER}" == "${EXPECTED_SHA}" ]] || fail "release marker changed during start acceptance"
! systemctl is-enabled --quiet "${UNIT}" 2>/dev/null \
  || fail "weather PAPER service became enabled before explicit persistence approval"
[[ -f "${START_EPOCH_FILE}" ]] || fail "candidate start boundary marker disappeared"
[[ "$(tr -d '[:space:]' < "${START_EPOCH_FILE}")" == "${START_ACCEPTANCE_EPOCH}" ]] \
  || fail "candidate start boundary marker changed during acceptance"

trap - EXIT
printf '\nPASS: guarded pure three-layer PAPER validation candidate is active and accepted.\n'
printf 'Release: %s\n' "${EXPECTED_SHA}"
printf 'Runtime attestation: %s\n' "${ATTESTATION_OUT}"
printf 'First-cycle acceptance: %s\n' "${FIRST_CYCLE_OUT}"
printf 'Three-layer runtime acceptance: %s\n' "${THREE_LAYER_OUT}"
printf 'Candidate start boundary: %s\n' "${START_EPOCH_FILE}"
printf 'The service is active but remains DISABLED for boot persistence.\n'
printf 'Persistence still requires a fresh saved three-layer capture after this start.\n'
printf 'Same-day delivery, PWS, financial authority and real orders remain disabled.\n'