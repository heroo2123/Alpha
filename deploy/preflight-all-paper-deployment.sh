#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
NETWORK_OUT="${CONFIG_DIR}/all-paper-network-preflight.json"
ATTESTATION_OUT="${CONFIG_DIR}/all-paper-predeploy-attestation.json"
AUTH=/usr/local/libexec/polymarket-weather-paper-v3/authority.py
UNIT=polymarket-weather-paper.service
SHA="${1:-}"; GEN="${2:-}"
RUNTIME_ROOT=/var/lib/polymarket-weather-paper-runtime
SRC="${RUNTIME_ROOT}/releases/${SHA}/source"
PY="${RUNTIME_ROOT}/releases/${SHA}/venv/bin/python"
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${SHA}" =~ ^[0-9a-f]{40}$ && "${GEN}" =~ ^[0-9a-f]{64}$ ]] || fail "usage: $0 <sha> <generation>"
[[ -x "${AUTH}" && -x "${PY}" && -d "${SRC}" ]] || fail "immutable runtime missing"
systemctl is-active --quiet "${UNIT}" 2>/dev/null && fail "preflight requires stopped candidate"
systemctl is-enabled --quiet "${UNIT}" 2>/dev/null && fail "preflight requires disabled persistence"
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 /usr/bin/python3 "${AUTH}" verify-generation --generation-id "${GEN}" --candidate-sha "${SHA}"
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 /usr/bin/python3 "${AUTH}" verify-runtime-files --generation-id "${GEN}" --candidate-sha "${SHA}"
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 /usr/bin/python3 "${AUTH}" verify-checkout --generation-id "${GEN}" --candidate-sha "${SHA}"
bash "${SRC}/deploy/setup-all-paper-service.sh" "${SHA}" "${GEN}"
env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 PYTHONNOUSERSITE=1 ALPHA_DISABLE_DOTENV=1 \
  "${PY}" -I -s -E "${SRC}/deploy/check-weather-paper-network.py" --output "${NETWORK_OUT}"
bash "${SRC}/deploy/pre-release-weather-paper-backup.sh"
"${PY}" -I -s -E "${SRC}/deploy/attest-all-paper-runtime-v2.py" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" --expected-release-sha "${SHA}" --generation-id "${GEN}" --output "${ATTESTATION_OUT}"
sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 /usr/bin/python3 "${AUTH}" verify-runtime-files --generation-id "${GEN}" --candidate-sha "${SHA}"
printf 'PASS: immutable V10 preflight passed; service remains STOPPED/DISABLED.\n'
