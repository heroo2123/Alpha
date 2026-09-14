#!/usr/bin/env bash
set -Eeuo pipefail

# Prepare and verify a weather-paper release without starting or enabling it.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
UNIT="polymarket-weather-paper.service"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
NETWORK_OUT="${CONFIG_DIR}/weather-paper-network-preflight.json"
SYNOPTIC_OUT="${CONFIG_DIR}/weather-paper-synoptic-preflight.json"
ATTESTATION_OUT="${CONFIG_DIR}/weather-paper-predeploy-attestation.json"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
BOT_ENV="${CONFIG_DIR}/bot.env"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -d "${APP_DIR}" ]] || fail "missing weather-paper app directory: ${APP_DIR}"
[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "missing weather-paper app virtualenv"
[[ -f "${RELEASE_FILE}" ]] || fail "missing weather-paper release marker: ${RELEASE_FILE}"
[[ -f "${BOT_ENV}" ]] || fail "missing bot.env for Synoptic/Telegram credentials"

if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then
  fail "${UNIT} is active; preflight refuses to change an active paper service"
fi

bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"
# The small VM must not share resources or Telegram polling with the superseded stack.
# This is read-only: if anything old is active OR still enabled to return after reboot,
# deployment stops and the operator must decide explicitly what to do with it.
bash "${APP_DIR}/deploy/check-weather-paper-service-isolation.sh" --require-disabled

# Exercise every public provider from the actual target host. This catches IPv4/IPv6
# routing and DNS/TLS/API reachability problems before changing the installed unit or
# starting the bot. It is read-only and sends no Telegram message.
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/check-weather-paper-network.py" \
  --output "${NETWORK_OUT}"

# Independently validate the required Synoptic public token and CWOP endpoint. The
# helper never prints the token and accepts a valid zero-result response as connectivity
# success because a particular reference point need not have a fresh nearby CWOP PWS.
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/check-synoptic-pws.py" \
  --env-file "${BOT_ENV}" --output "${SYNOPTIC_OUT}"

bash "${APP_DIR}/deploy/pre-release-weather-paper-backup.sh"
bash "${APP_DIR}/deploy/setup-weather-paper-service.sh"

"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/attest-weather-paper-runtime.py" \
  --app-dir "${APP_DIR}" \
  --release-file "${RELEASE_FILE}" \
  --db "${DB_PATH}" \
  --output "${ATTESTATION_OUT}"

printf '\nPre-deployment gate passed.\n'
printf 'Legacy scanner/research services are not running and are not enabled beside the weather PAPER bot.\n'
printf 'Required weather/data providers and Synoptic PWS token are reachable from this host.\n'
printf 'The canonical weather PAPER service is installed but remains STOPPED and DISABLED.\n'
printf 'Its code checkout and release marker are isolated from the legacy scanner.\n'
printf 'Network report: %s\n' "${NETWORK_OUT}"
printf 'Synoptic report: %s\n' "${SYNOPTIC_OUT}"
printf 'Attestation: %s\n' "${ATTESTATION_OUT}"
printf 'Starting the service requires a separate explicit deployment action.\n'
