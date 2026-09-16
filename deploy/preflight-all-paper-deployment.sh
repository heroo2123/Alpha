#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
UNIT="polymarket-weather-paper.service"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
NETWORK_OUT="${CONFIG_DIR}/all-paper-network-preflight.json"
ATTESTATION_OUT="${CONFIG_DIR}/all-paper-predeploy-attestation.json"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
LIBEXEC="/usr/local/libexec/polymarket-weather-paper"
GATE="${LIBEXEC}/release-gate.py"
DROPIN="/etc/systemd/system/${UNIT}.d/10-release-authority.conf"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ -d "${APP_DIR}" && -x "${APP_DIR}/.venv/bin/python" && -f "${RELEASE_FILE}" ]] || fail "candidate app/release evidence missing"
[[ -f "${GATE}" && -f "${DROPIN}" ]] || fail "host release authority/drop-in not installed"
[[ ! -e "${APP_DIR}/.env" ]] || fail "ignored .env exists in attested app directory"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} is active; preflight requires stopped candidate"; fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} is enabled; candidate staging requires disabled persistence"; fi

/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"
bash "${APP_DIR}/deploy/check-weather-paper-service-isolation.sh" --require-disabled
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u NO_PROXY -u http_proxy -u https_proxy -u all_proxy -u no_proxy -u SSL_CERT_FILE -u SSL_CERT_DIR \
  "${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/check-weather-paper-network.py" --output "${NETWORK_OUT}"
bash "${APP_DIR}/deploy/pre-release-weather-paper-backup.sh"
bash "${APP_DIR}/deploy/setup-all-paper-service.sh"
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" \
  --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" --output "${ATTESTATION_OUT}"
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"

printf 'PASS: host-approved V8 preflight passed; service remains STOPPED and DISABLED.\n'
printf 'Network report: %s\nAttestation: %s\n' "${NETWORK_OUT}" "${ATTESTATION_OUT}"
