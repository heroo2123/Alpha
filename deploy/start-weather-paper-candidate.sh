#!/usr/bin/env bash
set -Eeuo pipefail

# Explicit PAPER-only start gate. Requires the exact immutable cutover generation.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
STATUS_PATH="${WEATHER_PAPER_STATUS_PATH:-/var/lib/polymarket-weather-paper/status.json}"
UNIT="polymarket-weather-paper.service"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
GENERATION_FILE="${CONFIG_DIR}/weather-paper-cutover-generation.id"
START_EPOCH_FILE="${CONFIG_DIR}/weather-paper-three-layer-start.epoch"
ATTESTATION_OUT="${CONFIG_DIR}/weather-paper-active-attestation.json"
FIRST_CYCLE_OUT="${CONFIG_DIR}/weather-paper-first-cycle-acceptance.json"
THREE_LAYER_OUT="${CONFIG_DIR}/weather-paper-three-layer-runtime-acceptance.json"
HOST_GATE="/usr/local/libexec/polymarket-weather-paper/release-gate.py"
HOST_RECOVERY="/usr/local/libexec/polymarket-weather-paper/restore-rollback.sh"
EXPECTED_SHA="${1:-}"
EXPECTED_GENERATION="${2:-}"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ && "${EXPECTED_GENERATION}" =~ ^[0-9a-f]{32}$ ]] || fail "usage: $0 <exact-release-sha> <exact-generation-id>"
[[ -x "${HOST_GATE}" && -x "${HOST_RECOVERY}" ]] || fail "independent host authority missing"
[[ -f "${RELEASE_FILE}" && -f "${GENERATION_FILE}" ]] || fail "release/generation marker missing"
[[ "$(tr -d '[:space:]' < "${GENERATION_FILE}")" == "${EXPECTED_GENERATION}" ]] || fail "cutover generation marker mismatch"
RELEASE_VENV="${APP_DIR}/.releases/${EXPECTED_SHA}/venv"
[[ -x "${RELEASE_VENV}/bin/python" ]] || fail "exact candidate release venv missing"
/usr/bin/python3 "${HOST_GATE}" verify-generation --generation-id "${EXPECTED_GENERATION}" --sha "${EXPECTED_SHA}"
/usr/bin/python3 "${HOST_GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-file "${GENERATION_FILE}"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${EXPECTED_SHA}" ]] || fail "checkout candidate mismatch"
[[ "$(tr -d '[:space:]' < "${RELEASE_FILE}")" == "${EXPECTED_SHA}" ]] || fail "release marker candidate mismatch"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} already active"; fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} already enabled"; fi

# Preflight must use this exact generation/release and installs only a stopped unit.
bash "${APP_DIR}/deploy/preflight-weather-paper-deployment.sh" "${EXPECTED_SHA}" "${EXPECTED_GENERATION}"
start_attempted=0
rollback_on_error(){
  code=$?
  trap - EXIT
  if (( code != 0 )) && (( start_attempted == 1 )); then
    printf 'Candidate acceptance failed; invoking exact immutable predecessor recovery %s...\n' "${EXPECTED_GENERATION}" >&2
    sudo "${HOST_RECOVERY}" --generation-id "${EXPECTED_GENERATION}" || printf 'CRITICAL: predecessor recovery failed\n' >&2
    rm -f "${START_EPOCH_FILE}" >/dev/null 2>&1 || true
  fi
  exit "${code}"
}
trap rollback_on_error EXIT

START_ACCEPTANCE_EPOCH="$("${RELEASE_VENV}/bin/python" -E -s -c 'import time; print(f"{time.time():.9f}")')"
mkdir -p "${CONFIG_DIR}"; umask 077
TMP_START="$(mktemp "${CONFIG_DIR}/.weather-paper-three-layer-start.XXXXXX")"
printf '%s\n' "${START_ACCEPTANCE_EPOCH}" > "${TMP_START}"; chmod 600 "${TMP_START}"; mv -f "${TMP_START}" "${START_EPOCH_FILE}"
start_attempted=1
sudo systemctl start "${UNIT}"
for _ in $(seq 1 20); do systemctl is-active --quiet "${UNIT}" 2>/dev/null && break; sleep 1; done
systemctl is-active --quiet "${UNIT}" 2>/dev/null || fail "weather PAPER service did not become active"

"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/attest-weather-paper-runtime.py" \
  --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-id "${EXPECTED_GENERATION}" \
  --db "${DB_PATH}" --require-active --output "${ATTESTATION_OUT}"
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/verify-weather-paper-first-cycle.py" \
  --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" \
  --timeout-seconds 600 --max-age-seconds 600 --output "${FIRST_CYCLE_OUT}"
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/verify-three-layer-validation-status.py" \
  --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" \
  --timeout-seconds 60 --max-age-seconds 600 --output "${THREE_LAYER_OUT}"
/usr/bin/python3 "${HOST_GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-file "${GENERATION_FILE}"
! systemctl is-enabled --quiet "${UNIT}" 2>/dev/null || fail "service became persistent before explicit approval"
trap - EXIT
printf 'PASS: candidate %s accepted against immutable generation %s; active but DISABLED.\n' "${EXPECTED_SHA}" "${EXPECTED_GENERATION}"
