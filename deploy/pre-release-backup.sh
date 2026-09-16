#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="polymarket-edge-scanner"
APP_DIR="${1:-${ALPHA_APP_DIR:-${HOME}/${APP_NAME}}}"
DATA_DIR="${2:-${ALPHA_DATA_DIR:-${HOME}/.${APP_NAME}/data}}"
BACKUP_DIR="${3:-${ALPHA_BACKUP_DIR:-${HOME}/.${APP_NAME}/backups}}"
DB_PATH="${DB_PATH:-${DATA_DIR}/signals.db}"
RETENTION_DAYS="${ALPHA_BACKUP_RETENTION_DAYS:-14}"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

if [[ ! -f "${DB_PATH}" ]]; then
  printf 'No existing database at %s; pre-release backup not required.\n' "${DB_PATH}"
  exit 0
fi

[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "missing app virtualenv: ${APP_DIR}/.venv"
[[ "${RETENTION_DAYS}" =~ ^[0-9]+$ ]] && (( RETENTION_DAYS >= 1 )) \
  || fail "ALPHA_BACKUP_RETENTION_DAYS must be a positive integer"

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"

exec 9>"${BACKUP_DIR}/.backup.lockfile"
if ! flock -n 9; then
  fail "another pre-release database backup appears to be running"
fi

printf 'Creating verified pre-release SQLite backup before any release/service change...\n'
RESULT="$("${APP_DIR}/.venv/bin/python" -m polymarket_scanner.db_ops backup \
  --db "${DB_PATH}" \
  --backup-dir "${BACKUP_DIR}" \
  --retention-days "${RETENTION_DAYS}")"
printf '%s\n' "${RESULT}"

"${APP_DIR}/.venv/bin/python" - <<'PY' "${RESULT}"
import json
import sys
payload = json.loads(sys.argv[1])
if payload.get("restore_verified") is not True:
    raise SystemExit("pre-release backup restore verification did not pass")
if not payload.get("sha256"):
    raise SystemExit("pre-release backup hash missing")
print("Pre-release backup verified and restorable:", payload.get("backup"))
PY
