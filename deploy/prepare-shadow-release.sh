#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${ALPHA_APP_DIR:-${HOME}/polymarket-edge-scanner}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
RELEASE_SHA="${ALPHA_RELEASE_SHA:-${1:-}}"
PREFLIGHT_FILE="${CONFIG_DIR}/dependency-preflight.json"
export ALPHA_CONFIG_DIR="${CONFIG_DIR}"
[[ "${RELEASE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] || { echo 'An authorized immutable SHA is required' >&2; exit 1; }
[[ -f "${CONFIG_DIR}/bot.env" ]] || { echo 'Existing bot.env is required; this script never creates secrets' >&2; exit 1; }
cd "${APP_DIR}"
for unit in polymarket-edge-scanner polymarket-edge-command polymarket-universe-builder; do
  if systemctl is-active --quiet "${unit}.service"; then
    echo "Stop ${unit} explicitly before preparing a release" >&2; exit 1
  fi
done
# Existing database backup/restore verification precedes any checkout/marker change.
set -a
source "${CONFIG_DIR}/bot.env"
set +a
bash deploy/pre-release-backup.sh "${APP_DIR}" "${CONFIG_DIR}/data" "${CONFIG_DIR}/backups"
bash deploy/release-pin.sh "${APP_DIR}" "${RELEASE_SHA}" "${CONFIG_DIR}/release.sha"
"${APP_DIR}/.venv/bin/python" -m pip install -r requirements.txt
"${APP_DIR}/.venv/bin/python" -m polymarket_scanner.dependency_preflight --required-only \
  --release-sha "${RELEASE_SHA,,}" --output "${PREFLIGHT_FILE}"
chmod 600 "${PREFLIGHT_FILE}"
"${APP_DIR}/.venv/bin/python" -m polymarket_scanner.shadow_preflight
"${APP_DIR}/.venv/bin/python" - <<'PY'
from polymarket_scanner.config import settings
from polymarket_scanner.schema_contract import require_database_schema
require_database_schema(settings.db_path)
print("Existing database schema is compatible; accounting schema unchanged.")
PY
echo 'Release prepared. Install definitions with deploy/setup-shadow-services.sh; services remain stopped.'
echo 'Existing verified backup timer remains supported by deploy/setup-db-backup-service.sh.'
