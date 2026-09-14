#!/usr/bin/env bash
set -Eeuo pipefail

# Install the reviewed weather-only LIVE PAPER unit.  This script never starts or
# enables the service and never touches the legacy scanner units.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
CURRENT_USER="$(id -un)"
BOT_ENV="${CONFIG_DIR}/bot.env"
PAPER_ENV="${CONFIG_DIR}/weather-paper.env"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"

[[ -f "${BOT_ENV}" ]] || { echo 'Existing bot.env is required for Telegram credentials' >&2; exit 1; }
bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"

umask 077
TMP_ENV="$(mktemp)"
UNIT_DIR="$(mktemp -d)"
TMP_UNIT="${UNIT_DIR}/polymarket-weather-paper.service"
trap 'rm -f "${TMP_ENV}"; rm -rf "${UNIT_DIR}"' EXIT

# Extract an executable allowlist rather than grepping the whole legacy environment.
# The optional SYNOPTIC_PWS_TOKEN is read-only diagnostic authority; wallet, exchange,
# cloud and unrelated secrets never enter the weather-paper environment.
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/extract-weather-paper-env.py" \
  --source "${BOT_ENV}" --output "${TMP_ENV}"
install -m 0600 "${TMP_ENV}" "${PAPER_ENV}"

"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/render-weather-paper-unit.py" \
  --app-dir "${APP_DIR}" --config-dir "${CONFIG_DIR}" --user "${CURRENT_USER}" --output "${TMP_UNIT}"
systemd-analyze verify "${TMP_UNIT}"
sudo install -m 0644 "${TMP_UNIT}" /etc/systemd/system/polymarket-weather-paper.service
sudo systemctl daemon-reload

echo 'Weather LIVE PAPER unit installed. It was NOT started or enabled.'
echo 'Weather code/release marker are isolated from the legacy scanner release.'
echo 'Only Telegram credentials and optional SYNOPTIC_PWS_TOKEN were copied to weather-paper.env.'
