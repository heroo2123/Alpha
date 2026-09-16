#!/usr/bin/env bash
set -Eeuo pipefail

# Install the reviewed all-weather PAPER unit. Never start or enable it.
PATH=/usr/bin:/bin
export PATH
unset BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM || true
unset PYTHONPATH PYTHONHOME PYTHONUSERBASE PYTHONSTARTUP PYTHONINSPECT LD_PRELOAD LD_LIBRARY_PATH || true
export PYTHONNOUSERSITE=1

APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
CURRENT_USER="$(id -un)"
BOT_ENV="${CONFIG_DIR}/bot.env"
PAPER_ENV="${CONFIG_DIR}/weather-paper.env"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
RELEASE_SHA="${1:-}"
GENERATION_ID="${2:-}"
RELEASES_ROOT="/var/lib/polymarket-weather-paper-releases"

[[ "${RELEASE_SHA}" =~ ^[0-9a-f]{40}$ ]] || { echo 'exact lowercase release SHA required' >&2; exit 2; }
[[ "${GENERATION_ID}" =~ ^gen-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{12}-[0-9a-f]{12}$ ]] || { echo 'exact immutable generation ID required' >&2; exit 2; }
[[ "$(tr -d '[:space:]' < "${RELEASE_FILE}")" == "${RELEASE_SHA}" ]] || { echo 'release marker mismatch' >&2; exit 1; }
[[ -f "${BOT_ENV}" ]] || { echo 'Existing bot.env is required for Telegram credentials' >&2; exit 1; }
[[ -x "${RELEASES_ROOT}/${RELEASE_SHA}/venv/bin/python" ]] || { echo 'SHA-scoped release interpreter missing' >&2; exit 1; }
bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"

umask 077
TMP_ENV="$(mktemp)"
UNIT_DIR="$(mktemp -d)"
TMP_UNIT="${UNIT_DIR}/polymarket-weather-paper.service"
trap 'rm -f "${TMP_ENV}"; rm -rf "${UNIT_DIR}"' EXIT
# PAPER service receives only Telegram delivery credentials. Never copy wallet,
# exchange, cloud, database, signing or trading credentials into this environment.
grep -E '^[[:space:]]*(TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID)=' "${BOT_ENV}" > "${TMP_ENV}" || true
[[ "$(grep -c -E '^[[:space:]]*TELEGRAM_BOT_TOKEN=' "${TMP_ENV}" || true)" -eq 1 ]] || {
  echo 'TELEGRAM_BOT_TOKEN missing/duplicated in bot.env' >&2; exit 1;
}
[[ "$(grep -c -E '^[[:space:]]*TELEGRAM_CHAT_ID=' "${TMP_ENV}" || true)" -eq 1 ]] || {
  echo 'TELEGRAM_CHAT_ID missing/duplicated in bot.env' >&2; exit 1;
}
install -m 0600 "${TMP_ENV}" "${PAPER_ENV}"

"${RELEASES_ROOT}/${RELEASE_SHA}/venv/bin/python" -I -s "${APP_DIR}/deploy/render-all-paper-unit.py" \
  --app-dir "${APP_DIR}" --config-dir "${CONFIG_DIR}" --user "${CURRENT_USER}" \
  --release-sha "${RELEASE_SHA}" --generation-id "${GENERATION_ID}" --output "${TMP_UNIT}"
systemd-analyze verify "${TMP_UNIT}"
sudo install -o root -g root -m 0644 "${TMP_UNIT}" /etc/systemd/system/polymarket-weather-paper.service
sudo systemctl daemon-reload

echo 'All-weather PAPER unit installed. It was NOT started or enabled.'
echo 'ExecStart is bound to the exact SHA-scoped interpreter and immutable generation.'
echo 'Only TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are read from weather-paper.env.'
