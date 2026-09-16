#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
BOT_ENV="${CONFIG_DIR}/bot.env"
PAPER_ENV="${CONFIG_DIR}/weather-paper.env"
AUTH=/usr/local/libexec/polymarket-weather-paper-v3/authority.py
UNIT=/etc/systemd/system/polymarket-weather-paper.service
SHA="${1:-}"; GEN="${2:-}"
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${SHA}" =~ ^[0-9a-f]{40}$ && "${GEN}" =~ ^[0-9a-f]{64}$ ]] || fail "usage: $0 <sha> <generation>"
[[ -f "${BOT_ENV}" && -x "${AUTH}" ]] || fail "bot env/host authority missing"
umask 077
TMP="$(mktemp)"; trap 'rm -f "${TMP}"' EXIT
grep -E '^[[:space:]]*(TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID)=' "${BOT_ENV}" >"${TMP}" || true
[[ "$(grep -c '^TELEGRAM_BOT_TOKEN=' "${TMP}" || true)" -eq 1 && "$(grep -c '^TELEGRAM_CHAT_ID=' "${TMP}" || true)" -eq 1 ]] || fail "Telegram credentials missing/duplicated"
install -m 0600 "${TMP}" "${PAPER_ENV}"
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 /usr/bin/python3 "${AUTH}" verify-telegram-env
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 /usr/bin/python3 "${AUTH}" verify-runtime-files --generation-id "${GEN}" --candidate-sha "${SHA}"
systemd-analyze verify "${UNIT}"
printf 'PASS: authority-rendered V10 unit and Telegram-only environment verified; NOT started/enabled.\n'
