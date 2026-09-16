#!/usr/bin/env bash
# RETIRED_PRODUCTION_DEPLOYMENT_ENTRYPOINT: historical body below is unreachable.
printf '%s\n' 'REFUSED: retired PAPER deployment path; use the independently provisioned host protocol in deploy/production-host-control.sh and docs/PRODUCTION_HOST_TRUST.md' >&2
exit 40

set -Eeuo pipefail

APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
BACKUP_DIR="${WEATHER_PAPER_BACKUP_DIR:-${CONFIG_DIR}/weather-paper-backups}"
# The three-layer research database is deliberately allowed to grow to roughly a
# gigabyte of compressed evidence. Keeping fourteen full daily SQLite copies on the
# small e2-micro disk could therefore consume most of the filesystem. Three verified
# daily generations give rollback depth while keeping the worst-case backup footprint
# bounded enough for the deployment target. Operators may explicitly override this.
RETENTION_DAYS="${WEATHER_PAPER_BACKUP_RETENTION_DAYS:-3}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "missing weather-paper app virtualenv: ${APP_DIR}/.venv"
[[ -f "${RELEASE_FILE}" ]] || fail "missing weather-paper release marker: ${RELEASE_FILE}"
[[ "${RETENTION_DAYS}" =~ ^[0-9]+$ ]] && (( RETENTION_DAYS >= 1 )) \
  || fail "WEATHER_PAPER_BACKUP_RETENTION_DAYS must be a positive integer"

if [[ ! -f "${DB_PATH}" ]]; then
  printf 'No existing weather-paper database at %s; no paper-ledger backup is required.\n' "${DB_PATH}"
  exit 0
fi

mkdir -p "${BACKUP_DIR}"
chmod 700 "${BACKUP_DIR}"
exec 9>"${BACKUP_DIR}/.backup.lockfile"
flock -n 9 || fail "another weather-paper backup is already running"

RELEASE_SHA="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
# The inline interpreter receives the repository root explicitly. The result is the
# same whether this script is called from the app checkout, $HOME, or systemd.
PYTHONPATH="${APP_DIR}${PYTHONPATH:+:${PYTHONPATH}}" \
"${APP_DIR}/.venv/bin/python" - "${DB_PATH}" "${BACKUP_DIR}" "${RELEASE_SHA}" "${RETENTION_DAYS}" <<'PY'
import json
import sys
from polymarket_scanner.weather_only_paper_backup import backup_weather_paper_database

source, backup_dir, release_sha, retention_days = sys.argv[1:]
result = backup_weather_paper_database(
    source,
    backup_dir,
    release_sha=release_sha,
    retention_days=int(retention_days),
)
if result.get("restore_verified") is not True:
    raise SystemExit("weather-paper backup restore verification failed")
print(json.dumps(result, sort_keys=True, indent=2))
PY
