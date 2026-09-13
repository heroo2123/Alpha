#!/usr/bin/env bash
set -Eeuo pipefail

# Explicit PAPER-only start gate. This script is never invoked by installation or CI.
# It verifies one exact commit, reruns the stopped-service preflight/backup, starts only
# the canonical weather-paper unit, and stops it again automatically if either active
# runtime identity or the first fresh paper cycle fails acceptance.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
STATUS_PATH="${WEATHER_PAPER_STATUS_PATH:-/var/lib/polymarket-weather-paper/status.json}"
UNIT="polymarket-weather-paper.service"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
ATTESTATION_OUT="${CONFIG_DIR}/weather-paper-active-attestation.json"
FIRST_CYCLE_OUT="${CONFIG_DIR}/weather-paper-first-cycle-acceptance.json"
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

# Preflight refuses orphan weather processes, verifies the isolated release, creates a
# verified restorable paper-ledger backup when one exists, and installs the canonical
# unit while leaving it stopped.
bash "${APP_DIR}/deploy/preflight-weather-paper-deployment.sh"

start_attempted=0
rollback_on_error(){
  code=$?
  if (( code != 0 )) && (( start_attempted == 1 )); then
    printf 'Start acceptance failed; stopping weather PAPER service...\n' >&2
    sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
  fi
  exit "${code}"
}
trap rollback_on_error EXIT

START_ACCEPTANCE_EPOCH="$(date +%s)"
# Mark the attempt before asking systemd to start. If `systemctl start` itself returns
# non-zero after partially launching the unit, the EXIT trap still stops the service.
start_attempted=1
sudo systemctl start "${UNIT}"

# Give systemd a bounded window to launch the process. Do not use `enable`: this is an
# explicit acceptance start, not permission for unattended boot persistence yet.
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

# Deployment is not accepted merely because the process exists. Wait for one status
# cycle produced after this exact start and prove it is healthy, paper-only, and keeps
# the three-layer same-day lane silent/untrusted for trading.
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/verify-weather-paper-first-cycle.py" \
  --status "${STATUS_PATH}" \
  --release-sha "${EXPECTED_SHA}" \
  --not-before "${START_ACCEPTANCE_EPOCH}" \
  --timeout-seconds 600 \
  --max-age-seconds 600 \
  --output "${FIRST_CYCLE_OUT}"

# Recheck release identity after the process AND first cycle exist, so a checkout or
# marker race cannot turn the preflighted commit into a different running tree.
HEAD_AFTER="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
MARKER_AFTER="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${HEAD_AFTER}" == "${EXPECTED_SHA}" ]] || fail "checkout changed during start acceptance"
[[ "${MARKER_AFTER}" == "${EXPECTED_SHA}" ]] || fail "release marker changed during start acceptance"

trap - EXIT
printf '\nPASS: canonical weather PAPER candidate is active, attested, and completed a healthy first cycle.\n'
printf 'Release: %s\n' "${EXPECTED_SHA}"
printf 'Runtime attestation: %s\n' "${ATTESTATION_OUT}"
printf 'First-cycle acceptance: %s\n' "${FIRST_CYCLE_OUT}"
printf 'The service was started but NOT enabled for boot persistence.\n'
printf 'The weather-paper checkout/release marker are isolated from the legacy scanner.\n'
printf 'Real-money trading authority is not granted by this script.\n'
