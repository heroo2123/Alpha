#!/usr/bin/env bash
set -Eeuo pipefail
APP_NAME="polymarket-edge-scanner"
APP_DIR="${HOME}/${APP_NAME}"
SERVICE_NAME="${APP_NAME}.service"

[[ -d "${APP_DIR}/.git" ]] || { echo "App not found at ${APP_DIR}" >&2; exit 1; }

echo "Updating from GitHub main..."
git -C "${APP_DIR}" fetch origin main
git -C "${APP_DIR}" reset --hard origin/main
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"
sudo systemctl restart "${SERVICE_NAME}"
sleep 2
sudo systemctl --no-pager --full status "${SERVICE_NAME}"
