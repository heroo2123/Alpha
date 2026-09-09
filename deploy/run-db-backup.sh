#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="polymarket-edge-scanner"
APP_DIR="${ALPHA_APP_DIR:-${HOME}/${APP_NAME}}"
DATA_DIR="${ALPHA_DATA_DIR:-${HOME}/.${APP_NAME}/data}"
DB_PATH="${DB_PATH:-${DATA_DIR}/signals.db}"
BACKUP_DIR="${ALPHA_BACKUP_DIR:-${HOME}/.${APP_NAME}/backups}"
RETENTION_DAYS="${ALPHA_BACKUP_RETENTION_DAYS:-14}"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "missing app virtualenv: ${APP_DIR}/.venv"
[[ -f "${DB_PATH}" ]] || fail "database does not exist: ${DB_PATH}"
[[ "${RETENTION_DAYS}" =~ ^[0-9]+$ ]] && (( RETENTION_DAYS >= 1 )) \
  || fail "ALPHA_BACKUP_RETENTION_DAYS must be a positive integer"

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

# Prevent overlapping timer/manual backups without depending on a long-lived daemon.
exec 9>"${BACKUP_DIR}/.backup.lockfile"
if ! flock -n 9; then
  fail "another database backup appears to be running"
fi

"${APP_DIR}/.venv/bin/python" -m polymarket_scanner.db_ops configure --db "${DB_PATH}" >/dev/null
RESULT="$("${APP_DIR}/.venv/bin/python" -m polymarket_scanner.db_ops backup \
  --db "${DB_PATH}" \
  --backup-dir "${BACKUP_DIR}" \
  --retention-days "${RETENTION_DAYS}")"
printf '%s\n' "${RESULT}"
