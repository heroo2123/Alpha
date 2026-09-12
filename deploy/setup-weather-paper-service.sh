#!/usr/bin/env bash
set -Eeuo pipefail

# Install the reviewed weather-only LIVE PAPER unit.  This script never starts or
# enables the service and never touches the legacy scanner units.
APP_DIR="${ALPHA_APP_DIR:-${HOME}/polymarket-edge-scanner}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
CURRENT_USER="$(id -un)"
BOT_ENV="${CONFIG_DIR}/bot.env"
PAPER_ENV="${CONFIG_DIR}/weather-paper.env"

[[ -f "${BOT_ENV}" ]] || { echo 'Existing bot.env is required for Telegram credentials' >&2; exit 1; }
bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${CONFIG_DIR}/release.sha"

umask 077
TMP_ENV="$(mktemp)"
UNIT_DIR="$(mktemp -d)"
TMP_UNIT="${UNIT_DIR}/polymarket-weather-paper.service"
trap 'rm -f "${TMP_ENV}"; rm -rf "${UNIT_DIR}"' EXIT
# Paper service receives ONLY Telegram delivery credentials.  Do not copy wallet,
# exchange, cloud, database, or other legacy bot configuration into its environment.
grep -E '^[[:space:]]*(TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID)=' "${BOT_ENV}" > "${TMP_ENV}" || true
[[ "$(grep -c -E '^[[:space:]]*TELEGRAM_BOT_TOKEN=' "${TMP_ENV}" || true)" -eq 1 ]] || {
  echo 'TELEGRAM_BOT_TOKEN missing/duplicated in bot.env' >&2; exit 1;
}
[[ "$(grep -c -E '^[[:space:]]*TELEGRAM_CHAT_ID=' "${TMP_ENV}" || true)" -eq 1 ]] || {
  echo 'TELEGRAM_CHAT_ID missing/duplicated in bot.env' >&2; exit 1;
}
install -m 0600 "${TMP_ENV}" "${PAPER_ENV}"

"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/render-weather-paper-unit.py" \
  --app-dir "${APP_DIR}" --config-dir "${CONFIG_DIR}" --user "${CURRENT_USER}" --output "${TMP_UNIT}"
systemd-analyze verify "${TMP_UNIT}"
sudo install -m 0644 "${TMP_UNIT}" /etc/systemd/system/polymarket-weather-paper.service
sudo systemctl daemon-reload

echo 'Weather LIVE PAPER unit installed. It was NOT started or enabled.'
echo 'Only TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID were copied to weather-paper.env.'
