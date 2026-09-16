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
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
START_EPOCH_FILE="${CONFIG_DIR}/all-paper-final-start.epoch"
UNIT="polymarket-weather-paper.service"
BACKUP_UNIT="polymarket-weather-paper-backup.service"
BACKUP_TIMER="polymarket-weather-paper-backup.timer"
EXPECTED_SHA="${1:-}"
GENERATION_ID="${2:-}"
AUTHORITY="/usr/local/libexec/polymarket-weather-paper/v2/authority.py"
RELEASES_ROOT="/var/lib/polymarket-weather-paper-releases"
ATTESTATION_OUT="${CONFIG_DIR}/all-paper-persistence-attestation.json"
CYCLE_OUT="${CONFIG_DIR}/all-paper-persistence-cycle.json"
THREE_LAYER_OUT="${CONFIG_DIR}/all-paper-persistence-three-layer.json"
FRESH_CAPTURE_OUT="${CONFIG_DIR}/all-paper-persistence-fresh-capture.json"
OPERATOR_SYNC_OUT="${CONFIG_DIR}/all-paper-persistence-operator-sync.json"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "usage: $0 <exact-approved-release-sha> <generation-id>"
[[ "${GENERATION_ID}" =~ ^gen-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{12}-[0-9a-f]{12}$ ]] || fail "exact immutable generation ID required"
PYTHON="${RELEASES_ROOT}/${EXPECTED_SHA}/venv/bin/python"
EVIDENCE="${RELEASES_ROOT}/${EXPECTED_SHA}/release-evidence.json"
[[ -f "${AUTHORITY}" && ! -L "${AUTHORITY}" ]] || fail "independent host authority v2 missing"
[[ -d "${APP_DIR}/.git" && -x "${PYTHON}" && -f "${EVIDENCE}" && -f "${RELEASE_FILE}" && -f "${START_EPOCH_FILE}" && ! -L "${START_EPOCH_FILE}" ]] || fail "candidate/start evidence missing"
[[ ! -e "${APP_DIR}/.env" ]] || fail "ignored .env exists"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${EXPECTED_SHA}" ]] || fail "checkout mismatch"
[[ "$(tr -d '[:space:]' < "${RELEASE_FILE}")" == "${EXPECTED_SHA}" ]] || fail "release marker mismatch"

/usr/bin/python3 "${AUTHORITY}" verify-authority
/usr/bin/python3 "${AUTHORITY}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
/usr/bin/python3 "${AUTHORITY}" verify-generation \
  --generation-id "${GENERATION_ID}" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" \
  --candidate-sha "${EXPECTED_SHA}" --phase candidate
/usr/bin/python3 "${AUTHORITY}" verify-runtime --app-dir "${APP_DIR}" --sha "${EXPECTED_SHA}" \
  --generation-id "${GENERATION_ID}" --evidence "${EVIDENCE}"
systemctl is-active --quiet "${UNIT}" 2>/dev/null || fail "accepted service is not active"
! systemctl is-enabled --quiet "${UNIT}" 2>/dev/null || fail "service already enabled"

START_ACCEPTANCE_EPOCH="$(tr -d '[:space:]' < "${START_EPOCH_FILE}")"
"${PYTHON}" -I -s - "${START_ACCEPTANCE_EPOCH}" <<'PY'
import math, sys
try: value=float(sys.argv[1])
except Exception: raise SystemExit('invalid final start boundary')
if not math.isfinite(value) or value <= 0: raise SystemExit('invalid final start boundary')
PY

bash "${APP_DIR}/deploy/check-weather-paper-service-isolation.sh" --require-disabled
"${PYTHON}" -I -s "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" \
  --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-id "${GENERATION_ID}" \
  --db "${DB_PATH}" --status "${STATUS_PATH}" --require-active --output "${ATTESTATION_OUT}"
"${PYTHON}" -I -s "${APP_DIR}/deploy/verify-all-paper-first-cycle-v2.py" \
  --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" --timeout-seconds 120 --max-age-seconds 900 --output "${CYCLE_OUT}"
"${PYTHON}" -I -s "${APP_DIR}/deploy/verify-operator-sync-complete.py" --db "${DB_PATH}" --output "${OPERATOR_SYNC_OUT}"
"${PYTHON}" -I -s "${APP_DIR}/deploy/verify-three-layer-validation-status.py" \
  --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" --timeout-seconds 60 --max-age-seconds 900 --output "${THREE_LAYER_OUT}"
"${PYTHON}" -I -s "${APP_DIR}/deploy/verify-three-layer-fresh-capture.py" \
  --status "${STATUS_PATH}" --db "${DB_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" --timeout-seconds 60 --max-age-seconds 900 --output "${FRESH_CAPTURE_OUT}"
/usr/bin/python3 "${AUTHORITY}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
/usr/bin/python3 "${AUTHORITY}" verify-generation \
  --generation-id "${GENERATION_ID}" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" \
  --candidate-sha "${EXPECTED_SHA}" --phase candidate
/usr/bin/python3 "${AUTHORITY}" verify-runtime --app-dir "${APP_DIR}" --sha "${EXPECTED_SHA}" \
  --generation-id "${GENERATION_ID}" --evidence "${EVIDENCE}"

bash "${APP_DIR}/deploy/setup-weather-paper-backup-service.sh"
persistence_attempted=0
rollback_persistence(){
  code=$?
  if (( code != 0 && persistence_attempted == 1 )); then
    sudo systemctl disable --now "${BACKUP_TIMER}" >/dev/null 2>&1 || true
    sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true
  fi
  exit "${code}"
}
trap rollback_persistence EXIT
sudo systemctl start "${BACKUP_UNIT}"
systemctl is-failed --quiet "${BACKUP_UNIT}" 2>/dev/null && fail "initial verified paper backup failed"
persistence_attempted=1
sudo systemctl enable "${UNIT}"
sudo systemctl enable --now "${BACKUP_TIMER}"
systemctl is-enabled --quiet "${UNIT}" || fail "service was not enabled"
systemctl is-active --quiet "${UNIT}" || fail "service stopped while enabling persistence"
systemctl is-enabled --quiet "${BACKUP_TIMER}" || fail "backup timer not enabled"
systemctl is-active --quiet "${BACKUP_TIMER}" || fail "backup timer not active"
/usr/bin/python3 "${AUTHORITY}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
/usr/bin/python3 "${AUTHORITY}" verify-generation \
  --generation-id "${GENERATION_ID}" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" \
  --candidate-sha "${EXPECTED_SHA}" --phase candidate
"${PYTHON}" -I -s "${APP_DIR}/deploy/verify-operator-sync-complete.py" --db "${DB_PATH}" >/dev/null
trap - EXIT
printf 'PASS: host-approved V9 PAPER bot enabled for restart persistence for immutable generation %s. Release=%s\n' "${GENERATION_ID}" "${EXPECTED_SHA}"
