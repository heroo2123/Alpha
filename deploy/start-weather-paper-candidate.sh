#!/usr/bin/env bash
set -Eeuo pipefail

# Explicit PAPER-only start gate. This script is never invoked by installation or CI.
# It verifies one exact commit, reruns the stopped-service preflight/backup, starts only
# the canonical weather-paper unit, and stops it again automatically if active runtime
# attestation does not prove the expected process identity.
APP_DIR="${ALPHA_APP_DIR:-${HOME}/polymarket-edge-scanner}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
UNIT="polymarket-weather-paper.service"
RELEASE_FILE="${CONFIG_DIR}/release.sha"
ATTESTATION_OUT="${CONFIG_DIR}/weather-paper-active-attestation.json"
EXPECTED_SHA="${1:-}"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]] \
  || fail "usage: $0 <exact-40-char-lowercase-release-sha>"
[[ -d "${APP_DIR}/.git" ]] || fail "missing git checkout: ${APP_DIR}"
[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "missing app virtualenv"
[[ -f "${RELEASE_FILE}" ]] || fail "missing release marker: ${RELEASE_FILE}"

HEAD_SHA="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
MARKER_SHA="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${HEAD_SHA}" == "${EXPECTED_SHA}" ]] || fail "checkout is not the explicitly approved candidate"
[[ "${MARKER_SHA}" == "${EXPECTED_SHA}" ]] || fail "release marker is not the explicitly approved candidate"

if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then
  fail "${UNIT} is already active; refusing an ambiguous/repeated start"
fi

# Preflight refuses orphan weather processes, verifies the release, creates a verified
# restorable paper-ledger backup when one exists, and installs the canonical unit while
# leaving it stopped.
bash "${APP_DIR}/deploy/preflight-weather-paper-deployment.sh"

started=0
rollback_on_error(){
  code=$?
  if (( code != 0 )) && (( started == 1 )); then
    printf 'Start acceptance failed; stopping weather PAPER service...\n' >&2
    sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
  fi
  exit "${code}"
}
trap rollback_on_error EXIT

sudo systemctl start "${UNIT}"
started=1

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

# Recheck release identity after the process exists, so a checkout/marker race cannot
# turn the preflighted commit into a different running tree.
HEAD_AFTER="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
MARKER_AFTER="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${HEAD_AFTER}" == "${EXPECTED_SHA}" ]] || fail "checkout changed during start acceptance"
[[ "${MARKER_AFTER}" == "${EXPECTED_SHA}" ]] || fail "release marker changed during start acceptance"

trap - EXIT
printf '\nPASS: canonical weather PAPER candidate is active and attested.\n'
printf 'Release: %s\n' "${EXPECTED_SHA}"
printf 'Attestation: %s\n' "${ATTESTATION_OUT}"
printf 'The service was started but NOT enabled for boot persistence.\n'
printf 'Real-money trading authority is not granted by this script.\n'
