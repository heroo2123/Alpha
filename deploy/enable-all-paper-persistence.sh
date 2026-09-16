#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
STATUS_PATH="${WEATHER_PAPER_STATUS_PATH:-/var/lib/polymarket-weather-paper/status.json}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
START_FILE="${CONFIG_DIR}/all-paper-final-start.epoch"
AUTH=/usr/local/libexec/polymarket-weather-paper-v3/authority.py
UNIT=polymarket-weather-paper.service
BACKUP_UNIT=polymarket-weather-paper-backup.service
BACKUP_TIMER=polymarket-weather-paper-backup.timer
SHA="${1:-}"; GEN="${2:-}"
RUNTIME_ROOT=/var/lib/polymarket-weather-paper-runtime
SRC="${RUNTIME_ROOT}/releases/${SHA}/source"; PY="${RUNTIME_ROOT}/releases/${SHA}/venv/bin/python"
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${SHA}" =~ ^[0-9a-f]{40}$ && "${GEN}" =~ ^[0-9a-f]{64}$ ]] || fail "usage: $0 <exact-approved-release-sha> <generation-id>"
[[ -x "${AUTH}" && -x "${PY}" && -f "${START_FILE}" ]] || fail "authority/runtime/start evidence missing"
systemctl is-active --quiet "${UNIT}" || fail "accepted service is not active"
! systemctl is-enabled --quiet "${UNIT}" || fail "service already enabled"
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 /usr/bin/python3 "${AUTH}" verify-process --generation-id "${GEN}" --candidate-sha "${SHA}"
START="$(tr -d '[:space:]' <"${START_FILE}")"
"${PY}" -I -s -E "${SRC}/deploy/verify-all-paper-first-cycle-v2.py" --status "${STATUS_PATH}" --release-sha "${SHA}" --not-before "${START}" --timeout-seconds 120 --max-age-seconds 900 --output "${CONFIG_DIR}/all-paper-persistence-cycle.json"
"${PY}" -I -s -E "${SRC}/deploy/verify-operator-sync-complete.py" --db "${DB_PATH}" --output "${CONFIG_DIR}/all-paper-persistence-operator-sync.json"
"${PY}" -I -s -E "${SRC}/deploy/verify-three-layer-validation-status.py" --status "${STATUS_PATH}" --release-sha "${SHA}" --not-before "${START}" --timeout-seconds 60 --max-age-seconds 900 --output "${CONFIG_DIR}/all-paper-persistence-three-layer.json"
"${PY}" -I -s -E "${SRC}/deploy/verify-three-layer-fresh-capture.py" --status "${STATUS_PATH}" --db "${DB_PATH}" --release-sha "${SHA}" --not-before "${START}" --timeout-seconds 60 --max-age-seconds 900 --output "${CONFIG_DIR}/all-paper-persistence-fresh-capture.json"
bash "${SRC}/deploy/setup-weather-paper-backup-service.sh"
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
systemctl is-enabled --quiet "${UNIT}" || fail "service not enabled"
systemctl is-active --quiet "${UNIT}" || fail "service stopped while enabling persistence"
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 /usr/bin/python3 "${AUTH}" verify-process --generation-id "${GEN}" --candidate-sha "${SHA}"
"${PY}" -I -s -E "${SRC}/deploy/verify-operator-sync-complete.py" --db "${DB_PATH}" >/dev/null
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 /usr/bin/python3 "${AUTH}" finalize --generation-id "${GEN}" --candidate-sha "${SHA}"
trap - EXIT
printf 'PASS: immutable V10 PAPER bot enabled for restart persistence. Release=%s generation=%s\n' "${SHA}" "${GEN}"
