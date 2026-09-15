#!/usr/bin/env bash
set -Eeuo pipefail

# Prepare and verify the final operator-synchronized all-weather PAPER release without
# starting or enabling it.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
UNIT="polymarket-weather-paper.service"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
NETWORK_OUT="${CONFIG_DIR}/all-paper-network-preflight.json"
ATTESTATION_OUT="${CONFIG_DIR}/all-paper-predeploy-attestation.json"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ -d "${APP_DIR}" ]] || fail "missing weather-paper app directory: ${APP_DIR}"
[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "missing weather-paper app virtualenv"
[[ -f "${RELEASE_FILE}" ]] || fail "missing weather-paper release marker: ${RELEASE_FILE}"
[[ ! -e "${APP_DIR}/.env" ]] || fail "ignored .env exists in attested app directory"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then
  fail "${UNIT} is active; preflight refuses to replace an active unit"
fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then
  fail "${UNIT} is enabled; candidate staging requires disabled persistence"
fi

bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"
bash "${APP_DIR}/deploy/check-weather-paper-service-isolation.sh" --require-disabled
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/check-weather-paper-network.py" --output "${NETWORK_OUT}"
bash "${APP_DIR}/deploy/pre-release-weather-paper-backup.sh"
bash "${APP_DIR}/deploy/setup-all-paper-service.sh"
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" \
  --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" \
  --output "${ATTESTATION_OUT}"

printf '\nFinal all-PAPER pre-deployment gate passed.\n'
printf 'Legacy scanner/research services are inactive and disabled.\n'
printf 'Public providers are reachable and the existing PAPER ledger has a verified backup.\n'
printf 'The final-v7 service is installed but remains STOPPED and DISABLED.\n'
printf 'Dotenv/config determinism is independently attested.\n'
printf 'Network report: %s\n' "${NETWORK_OUT}"
printf 'Attestation: %s\n' "${ATTESTATION_OUT}"
