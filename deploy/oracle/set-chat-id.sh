#!/usr/bin/env bash
set -Eeuo pipefail
APP_NAME="polymarket-edge-scanner"
CONFIG_DIR="${HOME}/.${APP_NAME}"
ENV_FILE="${CONFIG_DIR}/bot.env"
SERVICE_NAME="${APP_NAME}.service"
COMMAND_SERVICE="polymarket-edge-command.service"
CHAT_ID="${1:-}"

if [[ -z "${CHAT_ID}" ]]; then
  echo "Usage: $0 YOUR_TELEGRAM_CHAT_ID" >&2
  exit 1
fi
[[ -f "${ENV_FILE}" ]] || { echo "Missing ${ENV_FILE}; run install.sh first." >&2; exit 1; }

if grep -q '^TELEGRAM_CHAT_ID=' "${ENV_FILE}"; then
  sed -i "s/^TELEGRAM_CHAT_ID=.*/TELEGRAM_CHAT_ID=${CHAT_ID}/" "${ENV_FILE}"
else
  printf '\nTELEGRAM_CHAT_ID=%s\n' "${CHAT_ID}" >> "${ENV_FILE}"
fi
chmod 600 "${ENV_FILE}"

sudo systemctl restart "${SERVICE_NAME}"
if sudo systemctl cat "${COMMAND_SERVICE}" >/dev/null 2>&1; then
  sudo systemctl restart "${COMMAND_SERVICE}"
fi
sleep 2
sudo systemctl --no-pager --full status "${SERVICE_NAME}"
if sudo systemctl cat "${COMMAND_SERVICE}" >/dev/null 2>&1; then
  sudo systemctl --no-pager --full status "${COMMAND_SERVICE}"
fi
echo "Chat ID saved. Send /help to your Telegram bot to test it."
