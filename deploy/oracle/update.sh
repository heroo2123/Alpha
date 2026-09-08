#!/usr/bin/env bash
set -Eeuo pipefail
APP_NAME="polymarket-edge-scanner"
APP_DIR="${HOME}/${APP_NAME}"
CONFIG_DIR="${HOME}/.${APP_NAME}"
RELEASE_FILE="${CONFIG_DIR}/release.sha"
PREFLIGHT_FILE="${CONFIG_DIR}/dependency-preflight.json"
SERVICE_NAME="${APP_NAME}.service"
COMMAND_SERVICE="polymarket-edge-command.service"
RELEASE_SHA="${ALPHA_RELEASE_SHA:-${1:-}}"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -d "${APP_DIR}/.git" ]] || fail "App not found at ${APP_DIR}"
[[ -f "${APP_DIR}/deploy/release-pin.sh" ]] || fail "Missing immutable release helper; update the checkout manually only after review"
[[ "${RELEASE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] \
  || fail "Usage: $0 <40-character-authorized-release-SHA> (or set ALPHA_RELEASE_SHA)"

mkdir -p "${CONFIG_DIR}"
chmod 700 "${CONFIG_DIR}"

printf 'Updating to immutable release %s...\n' "${RELEASE_SHA}"
bash "${APP_DIR}/deploy/release-pin.sh" "${APP_DIR}" "${RELEASE_SHA}" "${RELEASE_FILE}"

# A pinned historical commit is not deployable merely because it exists on main.
# It must also preserve the current P0 fail-closed production policy.
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
"${APP_DIR}/.venv/bin/python" - <<'PY'
from polymarket_scanner.trade_only import promoted_detectors
assert promoted_detectors() == (), f"P0 containment violated: promoted detectors={promoted_detectors()}"
print("P0 containment verified: 0 promoted TRADE NOW detectors")
PY
bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"

# Prove required network dependencies from the actual host before stopping/restarting
# production services. Optional research feeds are intentionally not release blockers.
printf 'Running required dependency preflight from this host...\n'
"${APP_DIR}/.venv/bin/python" -m polymarket_scanner.dependency_preflight \
  --required-only --output "${PREFLIGHT_FILE}"
chmod 600 "${PREFLIGHT_FILE}"

# Ensure the VM uses the canonical scanner entrypoint, a single Telegram getUpdates
# owner, release-SHA runtime attestation, and verified database backups.
bash "${APP_DIR}/deploy/oracle/setup-command-service.sh"
bash "${APP_DIR}/deploy/setup-db-backup-service.sh"

ACTUAL_SHA="$(git -C "${APP_DIR}" rev-parse HEAD)"
RECORDED_SHA="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${ACTUAL_SHA}" == "${RECORDED_SHA}" && "${ACTUAL_SHA}" == "${RELEASE_SHA,,}" ]] \
  || fail "Post-update release attestation failed"

sleep 2
sudo systemctl --no-pager --full status "${SERVICE_NAME}"
sudo systemctl --no-pager --full status "${COMMAND_SERVICE}"
printf 'Running immutable release: %s\n' "${ACTUAL_SHA}"
printf 'Required dependency preflight evidence: %s\n' "${PREFLIGHT_FILE}"
