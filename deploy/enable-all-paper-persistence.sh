#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"; CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"; DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"; STATUS_PATH="${WEATHER_PAPER_STATUS_PATH:-/var/lib/polymarket-weather-paper/status.json}"; RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"; START_FILE="${CONFIG_DIR}/all-paper-final-start.epoch"; UNIT=polymarket-weather-paper.service; SHA="${1:-}"; GEN="${2:-}"; AUTH=/usr/local/libexec/polymarket-weather-paper-v2/authority.py
fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ "${SHA}" =~ ^[0-9a-f]{40}$ && "${GEN}" =~ ^[0-9a-f]{64}$ ]] || fail "usage: $0 <sha> <generation>"
PY="${APP_DIR}/.releases/${SHA}/venv/bin/python"; [[ -x "${PY}" && -f "${START_FILE}" ]] || fail "candidate/start evidence missing"
/usr/bin/python3 "${AUTH}" verify-generation --generation-id "${GEN}" --app-dir "${APP_DIR}" --candidate-sha "${SHA}"
/usr/bin/python3 "${AUTH}" verify-checkout --generation-id "${GEN}" --app-dir "${APP_DIR}" --candidate-sha "${SHA}" --release-file "${RELEASE_FILE}"
systemctl is-active --quiet "${UNIT}" || fail "accepted service inactive"; ! systemctl is-enabled --quiet "${UNIT}" || fail "already enabled"
EPOCH="$(tr -d '[:space:]' <"${START_FILE}")"; "${PY}" -E -s - "${EPOCH}" <<'PY2'
import math,sys
v=float(sys.argv[1]); assert math.isfinite(v) and v>0
PY2
"${PY}" -E -s "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" --status "${STATUS_PATH}" --expected-release-sha "${SHA}" --generation-id "${GEN}" --require-active --output "${CONFIG_DIR}/all-paper-persistence-attestation.json"
"${PY}" -E -s "${APP_DIR}/deploy/verify-all-paper-first-cycle-v2.py" --status "${STATUS_PATH}" --release-sha "${SHA}" --not-before "${EPOCH}" --timeout-seconds 120 --max-age-seconds 900 --output "${CONFIG_DIR}/all-paper-persistence-cycle.json"
"${PY}" -E -s "${APP_DIR}/deploy/verify-operator-sync-complete.py" --db "${DB_PATH}" --output "${CONFIG_DIR}/all-paper-persistence-operator-sync.json"
/usr/bin/python3 "${AUTH}" verify-candidate-environment --generation-id "${GEN}" --app-dir "${APP_DIR}" --candidate-sha "${SHA}" --environment-manifest "${APP_DIR}/.releases/${SHA}/environment-manifest.json"
bash "${APP_DIR}/deploy/setup-weather-paper-backup-service.sh"; sudo systemctl start polymarket-weather-paper-backup.service
sudo systemctl enable "${UNIT}"; sudo systemctl enable --now polymarket-weather-paper-backup.timer
systemctl is-enabled --quiet "${UNIT}" && systemctl is-active --quiet "${UNIT}" || fail "persistence activation failed"
printf 'PASS: V10 PAPER persistence enabled for exact release/generation.\n'
