#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
STATUS_PATH="${WEATHER_PAPER_STATUS_PATH:-/var/lib/polymarket-weather-paper/status.json}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
START_FILE="${CONFIG_DIR}/all-paper-final-start.epoch"
AUTH=/usr/local/libexec/polymarket-weather-paper-v3/authority.py
UNIT=polymarket-weather-paper.service
SHA="${1:-}"; GEN="${2:-}"
RUNTIME_ROOT=/var/lib/polymarket-weather-paper-runtime
SRC="${RUNTIME_ROOT}/releases/${SHA}/source"; PY="${RUNTIME_ROOT}/releases/${SHA}/venv/bin/python"
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${SHA}" =~ ^[0-9a-f]{40}$ && "${GEN}" =~ ^[0-9a-f]{64}$ ]] || fail "usage: $0 <sha> <generation>"
[[ -x "${AUTH}" && -x "${PY}" ]] || fail "authority/runtime missing"
rollback(){
  code=$?
  if (( code != 0 )); then
    rm -f "${START_FILE}"
    sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 /usr/bin/python3 "${AUTH}" recover --generation-id "${GEN}" || {
      sudo systemctl stop "${UNIT}" || true
      sudo systemctl disable "${UNIT}" || true
    }
  fi
  exit "${code}"
}
trap rollback EXIT
bash "${SRC}/deploy/preflight-all-paper-deployment.sh" "${SHA}" "${GEN}"
EPOCH="$(env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 "${PY}" -I -s -E -c 'import time; print(f"{time.time():.9f}")')"
umask 077; printf '%s\n' "${EPOCH}" >"${START_FILE}"
sudo systemctl start "${UNIT}"
for _ in $(seq 1 20); do systemctl is-active --quiet "${UNIT}" && break; sleep 1; done
systemctl is-active --quiet "${UNIT}" || fail "service failed to start"
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 /usr/bin/python3 "${AUTH}" verify-process --generation-id "${GEN}" --candidate-sha "${SHA}"
"${PY}" -I -s -E "${SRC}/deploy/attest-all-paper-runtime-v2.py" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" --status "${STATUS_PATH}" --expected-release-sha "${SHA}" --generation-id "${GEN}" --require-active --output "${CONFIG_DIR}/all-paper-active-attestation.json"
"${PY}" -I -s -E "${SRC}/deploy/verify-all-paper-first-cycle-v2.py" --status "${STATUS_PATH}" --release-sha "${SHA}" --not-before "${EPOCH}" --timeout-seconds 900 --max-age-seconds 900 --output "${CONFIG_DIR}/all-paper-first-cycle-acceptance.json"
"${PY}" -I -s -E "${SRC}/deploy/verify-operator-sync-complete.py" --db "${DB_PATH}" --output "${CONFIG_DIR}/all-paper-operator-sync-acceptance.json"
"${PY}" -I -s -E "${SRC}/deploy/verify-three-layer-validation-status.py" --status "${STATUS_PATH}" --release-sha "${SHA}" --not-before "${EPOCH}" --timeout-seconds 60 --max-age-seconds 900 --output "${CONFIG_DIR}/all-paper-three-layer-runtime-acceptance.json"
! systemctl is-enabled --quiet "${UNIT}" || fail "persistence enabled before approval"
trap - EXIT
printf 'PASS: immutable V10 candidate active and accepted; persistence disabled. Generation=%s\n' "${GEN}"
