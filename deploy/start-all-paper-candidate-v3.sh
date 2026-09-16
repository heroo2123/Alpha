#!/usr/bin/env bash
# RETIRED_PRODUCTION_DEPLOYMENT_ENTRYPOINT: historical body below is unreachable.
printf '%s\n' 'REFUSED: retired PAPER deployment path; use the independently provisioned host protocol in deploy/production-host-control.sh and docs/PRODUCTION_HOST_TRUST.md' >&2
exit 40

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
[[ -f "${AUTH}" && -x "${PY}" && -d "${SRC}" ]] || fail "authority/runtime missing"
host(){ sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 GIT_CONFIG_NOSYSTEM=1 /usr/bin/python3 "${AUTH}" "$@"; }
rollback(){
  code=$?
  if (( code != 0 )); then
    rm -f "${START_FILE}"
    host recover --generation-id "${GEN}" || {
      sudo systemctl stop "${UNIT}" || true
      sudo systemctl disable "${UNIT}" || true
    }
  fi
  exit "${code}"
}
trap rollback EXIT
host verify-generation --generation-id "${GEN}" --candidate-sha "${SHA}"
host verify-runtime-files --generation-id "${GEN}" --candidate-sha "${SHA}"
host verify-checkout --generation-id "${GEN}" --candidate-sha "${SHA}"
/bin/bash --noprofile --norc "${SRC}/deploy/preflight-all-paper-deployment.sh" "${SHA}" "${GEN}"
EPOCH="$(date +%s.%N)"; umask 077; mkdir -p "${CONFIG_DIR}"; printf '%s\n' "${EPOCH}" >"${START_FILE}"
sudo systemctl start "${UNIT}"
for _ in $(seq 1 20); do systemctl is-active --quiet "${UNIT}" && break; sleep 1; done
systemctl is-active --quiet "${UNIT}" || fail "service failed to start"
host verify-process --generation-id "${GEN}" --candidate-sha "${SHA}" >/dev/null
/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 ALPHA_DISABLE_DOTENV=1 \
  "${PY}" -I -s -E "${SRC}/deploy/attest-all-paper-runtime-v2.py" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" --status "${STATUS_PATH}" --expected-release-sha "${SHA}" --generation-id "${GEN}" --require-active --output "${CONFIG_DIR}/all-paper-active-attestation.json"
/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  "${PY}" -I -s -E "${SRC}/deploy/verify-all-paper-first-cycle-v2.py" --status "${STATUS_PATH}" --release-sha "${SHA}" --not-before "${EPOCH}" --timeout-seconds 900 --max-age-seconds 900 --output "${CONFIG_DIR}/all-paper-first-cycle-acceptance.json"
/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  "${PY}" -I -s -E "${SRC}/deploy/verify-operator-sync-complete.py" --db "${DB_PATH}" --output "${CONFIG_DIR}/all-paper-operator-sync-acceptance.json"
/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  "${PY}" -I -s -E "${SRC}/deploy/verify-three-layer-validation-status.py" --status "${STATUS_PATH}" --release-sha "${SHA}" --not-before "${EPOCH}" --timeout-seconds 60 --max-age-seconds 900 --output "${CONFIG_DIR}/all-paper-three-layer-runtime-acceptance.json"
/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  "${PY}" -I -s -E "${SRC}/deploy/verify-three-layer-fresh-capture.py" --status "${STATUS_PATH}" --db "${DB_PATH}" --release-sha "${SHA}" --not-before "${EPOCH}" --timeout-seconds 60 --max-age-seconds 900 --output "${CONFIG_DIR}/all-paper-three-layer-fresh-capture.json"
host verify-process --generation-id "${GEN}" --candidate-sha "${SHA}" >/dev/null
! systemctl is-enabled --quiet "${UNIT}" || fail "persistence enabled before approval"
trap - EXIT
printf 'PASS: immutable V10 candidate active and accepted; persistence disabled. Generation=%s\n' "${GEN}"
