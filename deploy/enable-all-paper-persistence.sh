#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"; CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"; STATUS_PATH="${WEATHER_PAPER_STATUS_PATH:-/var/lib/polymarket-weather-paper/status.json}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"; GENERATION_FILE="${CONFIG_DIR}/weather-paper-cutover-generation.id"; START_EPOCH_FILE="${CONFIG_DIR}/all-paper-final-start.epoch"
UNIT="polymarket-weather-paper.service"; BACKUP_UNIT="polymarket-weather-paper-backup.service"; BACKUP_TIMER="polymarket-weather-paper-backup.timer"
EXPECTED_SHA="${1:-}"; EXPECTED_GENERATION="${2:-}"; LIBEXEC="/usr/local/libexec/polymarket-weather-paper"; GATE="${LIBEXEC}/release-gate.py"
ATTESTATION_OUT="${CONFIG_DIR}/all-paper-persistence-attestation.json"; CYCLE_OUT="${CONFIG_DIR}/all-paper-persistence-cycle.json"; THREE_LAYER_OUT="${CONFIG_DIR}/all-paper-persistence-three-layer.json"; FRESH_CAPTURE_OUT="${CONFIG_DIR}/all-paper-persistence-fresh-capture.json"; OPERATOR_SYNC_OUT="${CONFIG_DIR}/all-paper-persistence-operator-sync.json"
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ && "${EXPECTED_GENERATION}" =~ ^[0-9a-f]{32}$ ]] || fail "usage: $0 <exact-approved-release-sha> <exact-generation-id>"
[[ -x "${GATE}" ]] || fail "host release authority missing"
[[ -d "${APP_DIR}/.git" && -f "${RELEASE_FILE}" && -f "${GENERATION_FILE}" && -f "${START_EPOCH_FILE}" && ! -L "${START_EPOCH_FILE}" ]] || fail "candidate/start/generation evidence missing"
[[ ! -e "${APP_DIR}/.env" ]] || fail "ignored .env exists"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${EXPECTED_SHA}" ]] || fail "checkout mismatch"; [[ "$(tr -d '[:space:]' < "${RELEASE_FILE}")" == "${EXPECTED_SHA}" ]] || fail "release marker mismatch"; [[ "$(tr -d '[:space:]' < "${GENERATION_FILE}")" == "${EXPECTED_GENERATION}" ]] || fail "generation marker mismatch"
RELEASE_VENV="${APP_DIR}/.releases/${EXPECTED_SHA}/venv"; VENV_MANIFEST="${APP_DIR}/.releases/${EXPECTED_SHA}/venv-manifest.json"; [[ -x "${RELEASE_VENV}/bin/python" && -f "${VENV_MANIFEST}" ]] || fail "exact release venv/evidence missing"
/usr/bin/python3 "${GATE}" verify-generation --generation-id "${EXPECTED_GENERATION}" --sha "${EXPECTED_SHA}"; /usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-file "${GENERATION_FILE}"
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/weather-paper-release-venv.py" verify --venv "${RELEASE_VENV}" --lock "${APP_DIR}/requirements-runtime-hashed.txt" --manifest "${VENV_MANIFEST}" >/dev/null
systemctl is-active --quiet "${UNIT}" 2>/dev/null || fail "accepted service is not active"; ! systemctl is-enabled --quiet "${UNIT}" 2>/dev/null || fail "service already enabled"
START_ACCEPTANCE_EPOCH="$(tr -d '[:space:]' < "${START_EPOCH_FILE}")"
"${RELEASE_VENV}/bin/python" -E -s - "${START_ACCEPTANCE_EPOCH}" <<'PY'
import math,sys
try: value=float(sys.argv[1])
except Exception: raise SystemExit('invalid final start boundary')
if not math.isfinite(value) or value<=0: raise SystemExit('invalid final start boundary')
PY
bash "${APP_DIR}/deploy/check-weather-paper-service-isolation.sh" --require-disabled
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" --status "${STATUS_PATH}" --require-active --output "${ATTESTATION_OUT}"
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/verify-all-paper-first-cycle-v2.py" --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" --timeout-seconds 120 --max-age-seconds 900 --output "${CYCLE_OUT}"
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/verify-operator-sync-complete.py" --db "${DB_PATH}" --output "${OPERATOR_SYNC_OUT}"
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/verify-three-layer-validation-status.py" --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" --timeout-seconds 60 --max-age-seconds 900 --output "${THREE_LAYER_OUT}"
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/verify-three-layer-fresh-capture.py" --status "${STATUS_PATH}" --db "${DB_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" --timeout-seconds 60 --max-age-seconds 900 --output "${FRESH_CAPTURE_OUT}"
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-file "${GENERATION_FILE}"
bash "${APP_DIR}/deploy/setup-weather-paper-backup-service.sh"
persistence_attempted=0
rollback_persistence(){ code=$?; if (( code!=0 && persistence_attempted==1 )); then sudo systemctl disable --now "${BACKUP_TIMER}" >/dev/null 2>&1||true; sudo systemctl disable "${UNIT}" >/dev/null 2>&1||true; fi; exit "${code}"; }
trap rollback_persistence EXIT
sudo systemctl start "${BACKUP_UNIT}"; systemctl is-failed --quiet "${BACKUP_UNIT}" 2>/dev/null && fail "initial verified paper backup failed"; persistence_attempted=1
sudo systemctl enable "${UNIT}"; sudo systemctl enable --now "${BACKUP_TIMER}"
systemctl is-enabled --quiet "${UNIT}"||fail "service was not enabled"; systemctl is-active --quiet "${UNIT}"||fail "service stopped while enabling persistence"; systemctl is-enabled --quiet "${BACKUP_TIMER}"||fail "backup timer not enabled"; systemctl is-active --quiet "${BACKUP_TIMER}"||fail "backup timer not active"
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-file "${GENERATION_FILE}"; "${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/verify-operator-sync-complete.py" --db "${DB_PATH}" >/dev/null
trap - EXIT; printf 'PASS: host-approved V9 PAPER bot enabled for restart persistence. Release=%s generation=%s\n' "${EXPECTED_SHA}" "${EXPECTED_GENERATION}"
