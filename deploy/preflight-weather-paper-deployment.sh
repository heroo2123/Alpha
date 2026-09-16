#!/usr/bin/env bash
# RETIRED_PRODUCTION_DEPLOYMENT_ENTRYPOINT: historical body below is unreachable.
printf '%s\n' 'REFUSED: retired PAPER deployment path; use the independently provisioned host protocol in deploy/production-host-control.sh and docs/PRODUCTION_HOST_TRUST.md' >&2
exit 40

set -Eeuo pipefail

APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
UNIT="polymarket-weather-paper.service"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
GENERATION_FILE="${CONFIG_DIR}/weather-paper-cutover-generation.id"
NETWORK_OUT="${CONFIG_DIR}/weather-paper-network-preflight.json"
ATTESTATION_OUT="${CONFIG_DIR}/weather-paper-predeploy-attestation.json"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
HOST_GATE="/usr/local/libexec/polymarket-weather-paper/release-gate.py"
EXPECTED_SHA="${1:-$(tr -d '[:space:]' < "${RELEASE_FILE}" 2>/dev/null || true)}"
EXPECTED_GENERATION="${2:-$(tr -d '[:space:]' < "${GENERATION_FILE}" 2>/dev/null || true)}"
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ && "${EXPECTED_GENERATION}" =~ ^[0-9a-f]{32}$ ]] || fail "exact release and generation required"
RELEASE_VENV="${APP_DIR}/.releases/${EXPECTED_SHA}/venv"
[[ -x "${RELEASE_VENV}/bin/python" ]] || fail "exact release venv missing"
/usr/bin/python3 "${HOST_GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-file "${GENERATION_FILE}"
/usr/bin/python3 "${HOST_GATE}" verify-generation --generation-id "${EXPECTED_GENERATION}" --sha "${EXPECTED_SHA}"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then fail "active service blocks preflight"; fi
bash "${APP_DIR}/deploy/check-weather-paper-service-isolation.sh" --require-disabled
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/check-weather-paper-network.py" --output "${NETWORK_OUT}"
bash "${APP_DIR}/deploy/pre-release-weather-paper-backup.sh"
bash "${APP_DIR}/deploy/setup-weather-paper-service.sh"
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/attest-weather-paper-runtime.py" \
  --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-id "${EXPECTED_GENERATION}" \
  --db "${DB_PATH}" --output "${ATTESTATION_OUT}"
printf 'PASS: preflight exact release=%s generation=%s; service remains stopped/disabled.\n' "${EXPECTED_SHA}" "${EXPECTED_GENERATION}"
