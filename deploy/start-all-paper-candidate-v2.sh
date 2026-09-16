#!/usr/bin/env bash
# RETIRED_PRODUCTION_DEPLOYMENT_ENTRYPOINT: historical body below is unreachable.
printf '%s\n' 'REFUSED: retired PAPER deployment path; use the independently provisioned host protocol in deploy/production-host-control.sh and docs/PRODUCTION_HOST_TRUST.md' >&2
exit 40

set -Eeuo pipefail
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"; CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"; DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"; STATUS_PATH="${WEATHER_PAPER_STATUS_PATH:-/var/lib/polymarket-weather-paper/status.json}"; RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"; START_FILE="${CONFIG_DIR}/all-paper-final-start.epoch"; SHA="${1:-}"; GEN="${2:-}"; AUTH=/usr/local/libexec/polymarket-weather-paper-v2/authority.py; UNIT=polymarket-weather-paper.service
fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ "${SHA}" =~ ^[0-9a-f]{40}$ && "${GEN}" =~ ^[0-9a-f]{64}$ ]] || fail "usage: $0 <sha> <generation>"
PY="${APP_DIR}/.releases/${SHA}/venv/bin/python"; [[ -x "${PY}" ]] || fail "release venv missing"
/usr/bin/python3 "${AUTH}" verify-generation --generation-id "${GEN}" --app-dir "${APP_DIR}" --candidate-sha "${SHA}"
rollback(){ c=$?; if ((c!=0)); then rm -f "${START_FILE}"; sudo /usr/bin/python3 "${AUTH}" recover --generation-id "${GEN}" --deploy-uid "$(id -u)" --deploy-gid "$(id -g)" || { sudo systemctl stop "${UNIT}" || true; sudo systemctl disable "${UNIT}" || true; }; fi; exit "$c"; }; trap rollback EXIT
bash "${APP_DIR}/deploy/preflight-all-paper-deployment.sh" "${SHA}" "${GEN}"
EPOCH="$("${PY}" -E -s -c 'import time; print(f"{time.time():.9f}")')"; umask 077; printf '%s\n' "${EPOCH}" >"${START_FILE}"
sudo systemctl start "${UNIT}"; for _ in $(seq 1 20); do systemctl is-active --quiet "${UNIT}" && break; sleep 1; done; systemctl is-active --quiet "${UNIT}" || fail "service failed to start"
/usr/bin/python3 "${AUTH}" verify-checkout --generation-id "${GEN}" --app-dir "${APP_DIR}" --candidate-sha "${SHA}" --release-file "${RELEASE_FILE}"
"${PY}" -E -s "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" --status "${STATUS_PATH}" --expected-release-sha "${SHA}" --generation-id "${GEN}" --require-active --output "${CONFIG_DIR}/all-paper-active-attestation.json"
"${PY}" -E -s "${APP_DIR}/deploy/verify-all-paper-first-cycle-v2.py" --status "${STATUS_PATH}" --release-sha "${SHA}" --not-before "${EPOCH}" --timeout-seconds 900 --max-age-seconds 900 --output "${CONFIG_DIR}/all-paper-first-cycle-acceptance.json"
"${PY}" -E -s "${APP_DIR}/deploy/verify-operator-sync-complete.py" --db "${DB_PATH}" --output "${CONFIG_DIR}/all-paper-operator-sync-acceptance.json"
"${PY}" -E -s "${APP_DIR}/deploy/verify-three-layer-validation-status.py" --status "${STATUS_PATH}" --release-sha "${SHA}" --not-before "${EPOCH}" --timeout-seconds 60 --max-age-seconds 900 --output "${CONFIG_DIR}/all-paper-three-layer-runtime-acceptance.json"
! systemctl is-enabled --quiet "${UNIT}" || fail "persistence enabled before approval"
trap - EXIT; printf 'PASS: V10 candidate active and accepted; persistence disabled. Generation=%s\n' "${GEN}"
