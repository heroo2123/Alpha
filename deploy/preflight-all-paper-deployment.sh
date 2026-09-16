#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"; CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"; DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"; RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"; SHA="${1:-}"; GEN="${2:-}"; AUTH=/usr/local/libexec/polymarket-weather-paper-v2/authority.py; UNIT=polymarket-weather-paper.service
fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ "${SHA}" =~ ^[0-9a-f]{40}$ && "${GEN}" =~ ^[0-9a-f]{64}$ ]] || fail "usage: $0 <sha> <generation>"
PY="${APP_DIR}/.releases/${SHA}/venv/bin/python"; MANIFEST="${APP_DIR}/.releases/${SHA}/environment-manifest.json"; [[ -x "${PY}" && -f "${MANIFEST}" && -f "${AUTH}" ]] || fail "release environment/authority missing"
! systemctl is-active --quiet "${UNIT}" 2>/dev/null || fail "service active"; ! systemctl is-enabled --quiet "${UNIT}" 2>/dev/null || fail "service enabled"
/usr/bin/python3 "${AUTH}" verify-generation --generation-id "${GEN}" --app-dir "${APP_DIR}" --candidate-sha "${SHA}"
/usr/bin/python3 "${AUTH}" verify-checkout --generation-id "${GEN}" --app-dir "${APP_DIR}" --candidate-sha "${SHA}" --release-file "${RELEASE_FILE}"
/usr/bin/python3 "${AUTH}" verify-candidate-environment --generation-id "${GEN}" --app-dir "${APP_DIR}" --candidate-sha "${SHA}" --environment-manifest "${MANIFEST}"
[[ ! -e "${APP_DIR}/.env" ]] || fail "implicit .env exists"
/usr/bin/env -i PATH=/usr/bin:/bin HOME="${APP_DIR}" LANG=C.UTF-8 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 ALPHA_DISABLE_DOTENV=1 "${PY}" -E -s "${APP_DIR}/deploy/check-weather-paper-network.py" --output "${CONFIG_DIR}/all-paper-network-preflight.json"
bash "${APP_DIR}/deploy/pre-release-weather-paper-backup.sh"
bash "${APP_DIR}/deploy/setup-all-paper-service.sh" "${SHA}" "${GEN}"
"${PY}" -E -s "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" --expected-release-sha "${SHA}" --generation-id "${GEN}" --output "${CONFIG_DIR}/all-paper-predeploy-attestation.json"
printf 'PASS: V10 preflight passed; service remains stopped/disabled.\n'
