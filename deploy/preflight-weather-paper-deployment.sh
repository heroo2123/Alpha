#!/usr/bin/env bash
set -Eeuo pipefail

# Prepare and verify a weather-paper release without starting or enabling it.
APP_DIR="${ALPHA_APP_DIR:-${HOME}/polymarket-edge-scanner}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
UNIT="polymarket-weather-paper.service"
RELEASE_FILE="${CONFIG_DIR}/release.sha"
ATTESTATION_OUT="${CONFIG_DIR}/weather-paper-predeploy-attestation.json"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -d "${APP_DIR}" ]] || fail "missing app directory: ${APP_DIR}"
[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "missing app virtualenv"
[[ -f "${RELEASE_FILE}" ]] || fail "missing release marker: ${RELEASE_FILE}"

if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then
  fail "${UNIT} is active; preflight refuses to change an active paper service"
fi

bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"
bash "${APP_DIR}/deploy/pre-release-weather-paper-backup.sh"
bash "${APP_DIR}/deploy/setup-weather-paper-service.sh"

"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/attest-weather-paper-runtime.py" \
  --app-dir "${APP_DIR}" \
  --release-file "${RELEASE_FILE}" \
  --db "${DB_PATH}" \
  --output "${ATTESTATION_OUT}"

printf '\nPre-deployment gate passed.\n'
printf 'The canonical weather PAPER service is installed but remains STOPPED and DISABLED.\n'
printf 'Attestation: %s\n' "${ATTESTATION_OUT}"
printf 'Starting the service requires a separate explicit deployment action.\n'
