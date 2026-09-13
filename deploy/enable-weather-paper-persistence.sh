#!/usr/bin/env bash
set -Eeuo pipefail

# Enable boot persistence only after the exact candidate is already active, attested,
# and has produced a fresh healthy PAPER-only cycle. This script does not start a new
# candidate and never enables/disables legacy scanner services.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
STATUS_PATH="${WEATHER_PAPER_STATUS_PATH:-/var/lib/polymarket-weather-paper/status.json}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
UNIT="polymarket-weather-paper.service"
BACKUP_UNIT="polymarket-weather-paper-backup.service"
BACKUP_TIMER="polymarket-weather-paper-backup.timer"
EXPECTED_SHA="${1:-}"
ATTESTATION_OUT="${CONFIG_DIR}/weather-paper-persistence-attestation.json"
CYCLE_OUT="${CONFIG_DIR}/weather-paper-persistence-cycle.json"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "usage: $0 <exact-approved-release-sha>"
[[ -d "${APP_DIR}/.git" ]] || fail "isolated weather-paper checkout missing"
[[ -f "${RELEASE_FILE}" ]] || fail "weather-paper release marker missing"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')" == "${EXPECTED_SHA}" ]] \
  || fail "weather-paper checkout no longer matches approved candidate"
[[ "$(tr -d '[:space:]' < "${RELEASE_FILE}")" == "${EXPECTED_SHA}" ]] \
  || fail "weather-paper release marker no longer matches approved candidate"
systemctl is-active --quiet "${UNIT}" 2>/dev/null || fail "accepted weather PAPER service is not active"

# Before granting boot persistence, prove the superseded stack will not reappear after
# reboot and contend for the small VM or Telegram updates. This check is read-only and
# deliberately refuses to disable anything on the user's behalf.
bash "${APP_DIR}/deploy/check-weather-paper-service-isolation.sh" --require-disabled

"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/attest-weather-paper-runtime.py" \
  --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" \
  --status "${STATUS_PATH}" --require-active --output "${ATTESTATION_OUT}"

# Require a currently fresh safe cycle again; do not rely solely on an old receipt.
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/verify-weather-paper-first-cycle.py" \
  --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before 0 \
  --timeout-seconds 120 --max-age-seconds 600 --output "${CYCLE_OUT}"

bash "${APP_DIR}/deploy/setup-weather-paper-backup-service.sh"

changed=0
rollback_persistence(){
  code=$?
  if (( code != 0 )) && (( changed == 1 )); then
    sudo systemctl disable --now "${BACKUP_TIMER}" >/dev/null 2>&1 || true
    sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true
  fi
  exit "${code}"
}
trap rollback_persistence EXIT

# Prove the backup job can actually run on the live paper ledger before scheduling it.
sudo systemctl start "${BACKUP_UNIT}"
systemctl is-failed --quiet "${BACKUP_UNIT}" 2>/dev/null && fail "initial verified paper backup failed"

sudo systemctl enable "${UNIT}"
changed=1
sudo systemctl enable --now "${BACKUP_TIMER}"
systemctl is-enabled --quiet "${UNIT}" || fail "weather PAPER service was not enabled"
systemctl is-active --quiet "${UNIT}" || fail "weather PAPER service stopped during persistence enable"
systemctl is-enabled --quiet "${BACKUP_TIMER}" || fail "weather PAPER backup timer was not enabled"
systemctl is-active --quiet "${BACKUP_TIMER}" || fail "weather PAPER backup timer is not active"

# Final identity check after persistence changes; no restart is performed.
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/attest-weather-paper-runtime.py" \
  --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" \
  --status "${STATUS_PATH}" --require-active >/dev/null

trap - EXIT
printf '\nPASS: weather PAPER bot is enabled for 24/7 restart persistence.\n'
printf 'Daily verified paper-ledger backups are enabled.\n'
printf 'Release: %s\n' "${EXPECTED_SHA}"
printf 'Superseded scanner/research services remain outside this persistence path.\n'
printf 'Same-day research/trading authority was not changed by this action.\n'
printf 'Real-money trading remains outside this deployment.\n'
