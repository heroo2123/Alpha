#!/usr/bin/env bash
set -Eeuo pipefail

# Enable boot persistence only after active exact-SHA final acceptance and fresh source proof.
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
ATTESTATION_OUT="${CONFIG_DIR}/all-paper-persistence-attestation.json"
CYCLE_OUT="${CONFIG_DIR}/all-paper-persistence-cycle.json"
THREE_LAYER_OUT="${CONFIG_DIR}/all-paper-persistence-three-layer.json"
FRESH_CAPTURE_OUT="${CONFIG_DIR}/all-paper-persistence-fresh-capture.json"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "usage: $0 <exact-approved-release-sha>"
[[ -d "${APP_DIR}/.git" ]] || fail "isolated checkout missing"
[[ -f "${RELEASE_FILE}" ]] || fail "release marker missing"
[[ -f "${START_EPOCH_FILE}" && ! -L "${START_EPOCH_FILE}" ]] || fail "final start boundary missing/invalid"
[[ ! -e "${APP_DIR}/.env" ]] || fail "ignored .env exists in candidate checkout"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')" == "${EXPECTED_SHA}" ]] || fail "checkout no longer matches approved candidate"
[[ "$(tr -d '[:space:]' < "${RELEASE_FILE}")" == "${EXPECTED_SHA}" ]] || fail "release marker no longer matches approved candidate"
systemctl is-active --quiet "${UNIT}" 2>/dev/null || fail "accepted final service is not active"
! systemctl is-enabled --quiet "${UNIT}" 2>/dev/null || fail "service already enabled before persistence approval"

START_ACCEPTANCE_EPOCH="$(tr -d '[:space:]' < "${START_EPOCH_FILE}")"
"${APP_DIR}/.venv/bin/python" - "${START_ACCEPTANCE_EPOCH}" <<'PY'
import math, sys
try: value=float(sys.argv[1])
except Exception: raise SystemExit("invalid final start boundary")
if not math.isfinite(value) or value <= 0: raise SystemExit("invalid final start boundary")
PY

bash "${APP_DIR}/deploy/check-weather-paper-service-isolation.sh" --require-disabled
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" \
  --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" \
  --status "${STATUS_PATH}" --require-active --output "${ATTESTATION_OUT}"
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/verify-all-paper-first-cycle-v2.py" \
  --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" \
  --timeout-seconds 120 --max-age-seconds 600 --output "${CYCLE_OUT}"
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/verify-three-layer-validation-status.py" \
  --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" \
  --timeout-seconds 60 --max-age-seconds 600 --output "${THREE_LAYER_OUT}"
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/verify-three-layer-fresh-capture.py" \
  --status "${STATUS_PATH}" --db "${DB_PATH}" --release-sha "${EXPECTED_SHA}" \
  --not-before "${START_ACCEPTANCE_EPOCH}" --timeout-seconds 60 --max-age-seconds 600 \
  --output "${FRESH_CAPTURE_OUT}"

bash "${APP_DIR}/deploy/setup-weather-paper-backup-service.sh"
persistence_attempted=0
rollback_persistence(){
  code=$?
  if (( code != 0 )) && (( persistence_attempted == 1 )); then
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
systemctl is-enabled --quiet "${UNIT}" || fail "final service was not enabled"
systemctl is-active --quiet "${UNIT}" || fail "final service stopped while enabling persistence"
systemctl is-enabled --quiet "${BACKUP_TIMER}" || fail "backup timer was not enabled"
systemctl is-active --quiet "${BACKUP_TIMER}" || fail "backup timer is not active"

"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" \
  --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" \
  --status "${STATUS_PATH}" --require-active >/dev/null
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/verify-all-paper-first-cycle-v2.py" \
  --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" \
  --timeout-seconds 60 --max-age-seconds 600 >/dev/null

trap - EXIT
printf '\nPASS: final all-weather PAPER bot is enabled for restart persistence.\n'
printf 'A durable post-start WRH+NWS+GEFS capture and exact final runtime were proven first.\n'
printf 'Operator invalidation sync and deterministic configuration remain mandatory.\n'
printf 'Daily verified paper-ledger backups are enabled.\n'
printf 'Release: %s\n' "${EXPECTED_SHA}"
printf 'Real-money, wallet/signing, automatic-order and result-lag authority remain disabled.\n'
