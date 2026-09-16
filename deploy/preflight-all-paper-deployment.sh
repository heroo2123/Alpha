#!/usr/bin/env bash
set -Eeuo pipefail

PATH=/usr/bin:/bin
export PATH
unset BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM || true
unset PYTHONPATH PYTHONHOME PYTHONUSERBASE PYTHONSTARTUP PYTHONINSPECT LD_PRELOAD LD_LIBRARY_PATH || true
unset HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy SSL_CERT_FILE SSL_CERT_DIR || true
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1

APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
UNIT="polymarket-weather-paper.service"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
NETWORK_OUT="${CONFIG_DIR}/all-paper-network-preflight.json"
ATTESTATION_OUT="${CONFIG_DIR}/all-paper-predeploy-attestation.json"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
AUTHORITY="/usr/local/libexec/polymarket-weather-paper/v2/authority.py"
RELEASES_ROOT="/var/lib/polymarket-weather-paper-releases"
EXPECTED_SHA="${1:-}"
GENERATION_ID="${2:-}"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "usage: $0 <exact-approved-release-sha> <generation-id>"
[[ "${GENERATION_ID}" =~ ^gen-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{12}-[0-9a-f]{12}$ ]] || fail "exact immutable generation ID required"
VENV="${RELEASES_ROOT}/${EXPECTED_SHA}/venv"
PYTHON="${VENV}/bin/python"
EVIDENCE="${RELEASES_ROOT}/${EXPECTED_SHA}/release-evidence.json"
[[ -d "${APP_DIR}/.git" && -x "${PYTHON}" && -f "${RELEASE_FILE}" && -f "${EVIDENCE}" ]] || fail "candidate app/release evidence missing"
[[ -f "${AUTHORITY}" && ! -L "${AUTHORITY}" ]] || fail "independent host authority v2 missing"
[[ ! -e "${APP_DIR}/.env" ]] || fail "ignored .env exists in attested app directory"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} is active; preflight requires stopped candidate"; fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} is enabled; candidate staging requires disabled persistence"; fi

/usr/bin/python3 "${AUTHORITY}" verify-authority
/usr/bin/python3 "${AUTHORITY}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
/usr/bin/python3 "${AUTHORITY}" verify-generation \
  --generation-id "${GENERATION_ID}" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" \
  --candidate-sha "${EXPECTED_SHA}" --phase candidate
/usr/bin/python3 "${AUTHORITY}" verify-runtime --app-dir "${APP_DIR}" --sha "${EXPECTED_SHA}" \
  --generation-id "${GENERATION_ID}" --evidence "${EVIDENCE}"
bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"
bash "${APP_DIR}/deploy/check-weather-paper-service-isolation.sh" --require-disabled

env -i PATH=/usr/bin:/bin HOME=/nonexistent PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  "${PYTHON}" -I -s "${APP_DIR}/deploy/check-weather-paper-network.py" --output "${NETWORK_OUT}"
bash "${APP_DIR}/deploy/pre-release-weather-paper-backup.sh"
bash "${APP_DIR}/deploy/setup-all-paper-service.sh" "${EXPECTED_SHA}" "${GENERATION_ID}"

env -i PATH=/usr/bin:/bin HOME=/nonexistent PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  "${PYTHON}" -I -s "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" \
  --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-id "${GENERATION_ID}" \
  --db "${DB_PATH}" --output "${ATTESTATION_OUT}"

/usr/bin/python3 "${AUTHORITY}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
/usr/bin/python3 "${AUTHORITY}" verify-generation \
  --generation-id "${GENERATION_ID}" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" \
  --candidate-sha "${EXPECTED_SHA}" --phase candidate
/usr/bin/python3 "${AUTHORITY}" verify-runtime --app-dir "${APP_DIR}" --sha "${EXPECTED_SHA}" \
  --generation-id "${GENERATION_ID}" --evidence "${EVIDENCE}"

printf 'PASS: host-approved V9 preflight passed for immutable generation %s; service remains STOPPED and DISABLED.\n' "${GENERATION_ID}"
printf 'Network report: %s\nAttestation: %s\n' "${NETWORK_OUT}" "${ATTESTATION_OUT}"
