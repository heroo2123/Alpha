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
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
STATUS_PATH="${WEATHER_PAPER_STATUS_PATH:-/var/lib/polymarket-weather-paper/status.json}"
UNIT="polymarket-weather-paper.service"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
START_EPOCH_FILE="${CONFIG_DIR}/all-paper-final-start.epoch"
ATTESTATION_OUT="${CONFIG_DIR}/all-paper-active-attestation.json"
FIRST_CYCLE_OUT="${CONFIG_DIR}/all-paper-first-cycle-acceptance.json"
THREE_LAYER_OUT="${CONFIG_DIR}/all-paper-three-layer-runtime-acceptance.json"
OPERATOR_SYNC_OUT="${CONFIG_DIR}/all-paper-operator-sync-acceptance.json"
AUTHORITY="/usr/local/libexec/polymarket-weather-paper/v2/authority.py"
RELEASES_ROOT="/var/lib/polymarket-weather-paper-releases"
EXPECTED_SHA="${1:-}"
GENERATION_ID="${2:-}"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "usage: $0 <exact-approved-release-sha> <generation-id>"
[[ "${GENERATION_ID}" =~ ^gen-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{12}-[0-9a-f]{12}$ ]] || fail "exact immutable generation ID required"
PYTHON="${RELEASES_ROOT}/${EXPECTED_SHA}/venv/bin/python"
EVIDENCE="${RELEASES_ROOT}/${EXPECTED_SHA}/release-evidence.json"
[[ -f "${AUTHORITY}" && ! -L "${AUTHORITY}" ]] || fail "independent host authority v2 missing"
[[ -d "${APP_DIR}/.git" && -x "${PYTHON}" && -f "${RELEASE_FILE}" && -f "${EVIDENCE}" ]] || fail "candidate checkout/runtime missing"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${EXPECTED_SHA}" ]] || fail "checkout is not candidate"
[[ "$(tr -d '[:space:]' < "${RELEASE_FILE}")" == "${EXPECTED_SHA}" ]] || fail "release marker is not candidate"
[[ ! -e "${APP_DIR}/.env" ]] || fail "ignored .env exists"

/usr/bin/python3 "${AUTHORITY}" verify-authority
/usr/bin/python3 "${AUTHORITY}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
/usr/bin/python3 "${AUTHORITY}" verify-generation \
  --generation-id "${GENERATION_ID}" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" \
  --candidate-sha "${EXPECTED_SHA}" --phase candidate
/usr/bin/python3 "${AUTHORITY}" verify-runtime --app-dir "${APP_DIR}" --sha "${EXPECTED_SHA}" \
  --generation-id "${GENERATION_ID}" --evidence "${EVIDENCE}"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then fail "service already active"; fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then fail "service already enabled"; fi

rollback_on_error(){
  code=$?
  if (( code != 0 )); then
    rm -f "${START_EPOCH_FILE}" >/dev/null 2>&1 || true
    printf 'Acceptance failed; invoking independent rollback generation %s...\n' "${GENERATION_ID}" >&2
    if ! sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 \
      /usr/bin/python3 "${AUTHORITY}" recover --generation-id "${GENERATION_ID}"; then
      sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
      sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true
      printf 'HOST ROLLBACK FAILED: candidate contained; manual recovery required.\n' >&2
    fi
  fi
  exit "${code}"
}
trap rollback_on_error EXIT

bash "${APP_DIR}/deploy/preflight-all-paper-deployment.sh" "${EXPECTED_SHA}" "${GENERATION_ID}"
START_ACCEPTANCE_EPOCH="$(env -i PATH=/usr/bin:/bin HOME=/nonexistent PYTHONNOUSERSITE=1 \
  "${PYTHON}" -I -s -c 'import time; print(f"{time.time():.9f}")')"
mkdir -p "${CONFIG_DIR}"; umask 077
printf '%s\n' "${START_ACCEPTANCE_EPOCH}" > "${START_EPOCH_FILE}"; chmod 600 "${START_EPOCH_FILE}"

# This script is an operator deployment action. Corrective tests only inspect it; this
# engineering task never invokes it and therefore never starts the VM service.
sudo systemctl start "${UNIT}"
for _ in $(seq 1 20); do systemctl is-active --quiet "${UNIT}" 2>/dev/null && break; sleep 1; done
systemctl is-active --quiet "${UNIT}" 2>/dev/null || fail "all-PAPER service did not become active"
/usr/bin/python3 "${AUTHORITY}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
/usr/bin/python3 "${AUTHORITY}" verify-generation \
  --generation-id "${GENERATION_ID}" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" \
  --candidate-sha "${EXPECTED_SHA}" --phase candidate
/usr/bin/python3 "${AUTHORITY}" verify-runtime --app-dir "${APP_DIR}" --sha "${EXPECTED_SHA}" \
  --generation-id "${GENERATION_ID}" --evidence "${EVIDENCE}"

"${PYTHON}" -I -s "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" \
  --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-id "${GENERATION_ID}" \
  --db "${DB_PATH}" --status "${STATUS_PATH}" --require-active --output "${ATTESTATION_OUT}"
"${PYTHON}" -I -s "${APP_DIR}/deploy/verify-all-paper-first-cycle-v2.py" \
  --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" \
  --timeout-seconds 900 --max-age-seconds 900 --output "${FIRST_CYCLE_OUT}"
"${PYTHON}" -I -s "${APP_DIR}/deploy/verify-operator-sync-complete.py" \
  --db "${DB_PATH}" --output "${OPERATOR_SYNC_OUT}"
"${PYTHON}" -I -s "${APP_DIR}/deploy/verify-three-layer-validation-status.py" \
  --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" \
  --timeout-seconds 60 --max-age-seconds 900 --output "${THREE_LAYER_OUT}"

/usr/bin/python3 "${AUTHORITY}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
/usr/bin/python3 "${AUTHORITY}" verify-generation \
  --generation-id "${GENERATION_ID}" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" \
  --candidate-sha "${EXPECTED_SHA}" --phase candidate
! systemctl is-enabled --quiet "${UNIT}" 2>/dev/null || fail "service became persistent before approval"
[[ "$(tr -d '[:space:]' < "${START_EPOCH_FILE}")" == "${START_ACCEPTANCE_EPOCH}" ]] || fail "start boundary changed"
trap - EXIT
printf 'PASS: host-approved V9 candidate active and accepted for immutable generation %s; boot persistence remains disabled.\n' "${GENERATION_ID}"
