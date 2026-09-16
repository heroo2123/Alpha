#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
UNIT="polymarket-weather-paper.service"; RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"; GENERATION_FILE="${CONFIG_DIR}/weather-paper-cutover-generation.id"
NETWORK_OUT="${CONFIG_DIR}/all-paper-network-preflight.json"; ATTESTATION_OUT="${CONFIG_DIR}/all-paper-predeploy-attestation.json"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"; GATE="/usr/local/libexec/polymarket-weather-paper/release-gate.py"
EXPECTED_SHA="${1:-$(tr -d '[:space:]' < "${RELEASE_FILE}" 2>/dev/null || true)}"; EXPECTED_GENERATION="${2:-$(tr -d '[:space:]' < "${GENERATION_FILE}" 2>/dev/null || true)}"
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ && "${EXPECTED_GENERATION}" =~ ^[0-9a-f]{32}$ ]] || fail "exact release/generation required"
RELEASE_VENV="${APP_DIR}/.releases/${EXPECTED_SHA}/venv"; [[ -x "${RELEASE_VENV}/bin/python" ]] || fail "exact release venv missing"
[[ ! -e "${APP_DIR}/.env" ]] || fail "ignored .env exists in attested app directory"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} is active; preflight requires stopped candidate"; fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} is enabled; candidate staging requires disabled persistence"; fi
/usr/bin/python3 "${GATE}" verify-generation --generation-id "${EXPECTED_GENERATION}" --sha "${EXPECTED_SHA}"
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-file "${GENERATION_FILE}"
bash "${APP_DIR}/deploy/check-weather-paper-service-isolation.sh" --require-disabled
(
 cd "${APP_DIR}"
 env -i HOME="${CONFIG_DIR}" PATH="${RELEASE_VENV}/bin:/usr/bin:/bin" PYTHONNOUSERSITE=1 ALPHA_DISABLE_DOTENV=1 \
   "${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/check-weather-paper-network.py" --output "${NETWORK_OUT}"
)
bash "${APP_DIR}/deploy/pre-release-weather-paper-backup.sh"
bash "${APP_DIR}/deploy/setup-all-paper-service.sh"
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" --output "${ATTESTATION_OUT}"
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-file "${GENERATION_FILE}"
printf 'PASS: host-approved V9 preflight passed for generation %s; service remains STOPPED/DISABLED.\n' "${EXPECTED_GENERATION}"
