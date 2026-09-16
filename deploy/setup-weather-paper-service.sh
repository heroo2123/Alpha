#!/usr/bin/env bash
# RETIRED_PRODUCTION_DEPLOYMENT_ENTRYPOINT: historical body below is unreachable.
printf '%s\n' 'REFUSED: retired PAPER deployment path; use the independently provisioned host protocol in deploy/production-host-control.sh and docs/PRODUCTION_HOST_TRUST.md' >&2
exit 40

set -Eeuo pipefail

# Install the reviewed weather-only LIVE PAPER unit. Never start or enable it.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
CURRENT_USER="$(id -un)"
BOT_ENV="${CONFIG_DIR}/bot.env"
PAPER_ENV="${CONFIG_DIR}/weather-paper.env"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
GENERATION_FILE="${CONFIG_DIR}/weather-paper-cutover-generation.id"
HOST_GATE="/usr/local/libexec/polymarket-weather-paper/release-gate.py"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ -f "${BOT_ENV}" ]] || fail "Existing bot.env is required for Telegram credentials"
[[ -f "${RELEASE_FILE}" && -f "${GENERATION_FILE}" ]] || fail "release/generation marker missing"
RELEASE_SHA="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${RELEASE_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "release marker invalid"
RELEASE_VENV="${APP_DIR}/.releases/${RELEASE_SHA}/venv"
[[ -x "${RELEASE_VENV}/bin/python" ]] || fail "exact release virtualenv missing"
[[ -x "${HOST_GATE}" ]] || fail "independent host release authority missing"
/usr/bin/python3 "${HOST_GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-file "${GENERATION_FILE}"

umask 077
TMP_ENV="$(mktemp)"
UNIT_DIR="$(mktemp -d)"
TMP_UNIT="${UNIT_DIR}/polymarket-weather-paper.service"
trap 'rm -f "${TMP_ENV}"; rm -rf "${UNIT_DIR}"' EXIT
# Paper service receives ONLY Telegram delivery credentials. Never copy wallet,
# exchange, cloud, database, Python loader, linker, or proxy configuration.
grep -E '^[[:space:]]*(TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID)=' "${BOT_ENV}" > "${TMP_ENV}" || true
[[ "$(grep -c -E '^[[:space:]]*TELEGRAM_BOT_TOKEN=' "${TMP_ENV}" || true)" -eq 1 ]] || fail "TELEGRAM_BOT_TOKEN missing/duplicated in bot.env"
[[ "$(grep -c -E '^[[:space:]]*TELEGRAM_CHAT_ID=' "${TMP_ENV}" || true)" -eq 1 ]] || fail "TELEGRAM_CHAT_ID missing/duplicated in bot.env"
if grep -Eq '^[[:space:]]*(PYTHONPATH|PYTHONHOME|PYTHONUSERBASE|PYTHONSTARTUP|PYTHONINSPECT|LD_PRELOAD|LD_LIBRARY_PATH)=' "${TMP_ENV}"; then
  fail "code-loading environment leaked into PAPER service env"
fi
install -m 0600 "${TMP_ENV}" "${PAPER_ENV}"

"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/render-weather-paper-unit.py" \
  --app-dir "${APP_DIR}" --config-dir "${CONFIG_DIR}" --user "${CURRENT_USER}" \
  --release-sha "${RELEASE_SHA}" --output "${TMP_UNIT}"
systemd-analyze verify "${TMP_UNIT}"
sudo install -o root -g root -m 0644 "${TMP_UNIT}" /etc/systemd/system/polymarket-weather-paper.service
sudo systemctl daemon-reload

echo 'Weather LIVE PAPER unit installed. It was NOT started or enabled.'
echo 'Interpreter is bound to the exact release-specific venv.'
echo 'Only TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID were copied to weather-paper.env.'
