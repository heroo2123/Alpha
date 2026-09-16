#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"; CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"; USER_NOW="$(id -un)"; BOT_ENV="${CONFIG_DIR}/bot.env"; PAPER_ENV="${CONFIG_DIR}/weather-paper.env"; RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"; SHA="${1:-}"; GEN="${2:-}"
[[ "${SHA}" =~ ^[0-9a-f]{40}$ && "${GEN}" =~ ^[0-9a-f]{64}$ ]] || { echo "usage: $0 <sha> <generation>" >&2; exit 2; }
[[ -f "${BOT_ENV}" && "$(tr -d '[:space:]' < "${RELEASE_FILE}")" == "${SHA}" ]] || { echo 'release/bot env missing' >&2; exit 1; }
umask 077; TMP_ENV="$(mktemp)"; UNIT_DIR="$(mktemp -d)"; TMP_UNIT="${UNIT_DIR}/polymarket-weather-paper.service"; trap 'rm -f "${TMP_ENV}"; rm -rf "${UNIT_DIR}"' EXIT
grep -E '^[[:space:]]*(TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID)=' "${BOT_ENV}" >"${TMP_ENV}" || true
[[ "$(grep -c '^TELEGRAM_BOT_TOKEN=' "${TMP_ENV}" || true)" -eq 1 && "$(grep -c '^TELEGRAM_CHAT_ID=' "${TMP_ENV}" || true)" -eq 1 ]] || { echo 'Telegram credentials missing/duplicated' >&2; exit 1; }
install -m 0600 "${TMP_ENV}" "${PAPER_ENV}"
PY="${APP_DIR}/.releases/${SHA}/venv/bin/python"; [[ -x "${PY}" ]] || { echo 'release venv missing' >&2; exit 1; }
"${PY}" -E -s "${APP_DIR}/deploy/render-all-paper-unit.py" --app-dir "${APP_DIR}" --config-dir "${CONFIG_DIR}" --user "${USER_NOW}" --release-sha "${SHA}" --generation-id "${GEN}" --output "${TMP_UNIT}"
systemd-analyze verify "${TMP_UNIT}"; sudo install -m 0644 "${TMP_UNIT}" /etc/systemd/system/polymarket-weather-paper.service; sudo systemctl daemon-reload
echo 'V10 unit installed; NOT started or enabled.'
