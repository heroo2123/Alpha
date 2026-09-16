#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"; STATUS_PATH="${WEATHER_PAPER_STATUS_PATH:-/var/lib/polymarket-weather-paper/status.json}"
UNIT="polymarket-weather-paper.service"; RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"; GENERATION_FILE="${CONFIG_DIR}/weather-paper-cutover-generation.id"
START_EPOCH_FILE="${CONFIG_DIR}/all-paper-final-start.epoch"; ATTESTATION_OUT="${CONFIG_DIR}/all-paper-active-attestation.json"; FIRST_CYCLE_OUT="${CONFIG_DIR}/all-paper-first-cycle-acceptance.json"; THREE_LAYER_OUT="${CONFIG_DIR}/all-paper-three-layer-runtime-acceptance.json"; OPERATOR_SYNC_OUT="${CONFIG_DIR}/all-paper-operator-sync-acceptance.json"
GATE="/usr/local/libexec/polymarket-weather-paper/release-gate.py"; HOST_RECOVERY="/usr/local/libexec/polymarket-weather-paper/restore-rollback.sh"
EXPECTED_SHA="${1:-}"; EXPECTED_GENERATION="${2:-}"
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ && "${EXPECTED_GENERATION}" =~ ^[0-9a-f]{32}$ ]] || fail "usage: $0 <exact-approved-release-sha> <exact-generation-id>"
[[ -x "${GATE}" && -x "${HOST_RECOVERY}" ]] || fail "independent host authority missing"
[[ -d "${APP_DIR}/.git" && -f "${RELEASE_FILE}" && -f "${GENERATION_FILE}" ]] || fail "candidate release/generation evidence missing"
RELEASE_VENV="${APP_DIR}/.releases/${EXPECTED_SHA}/venv"; [[ -x "${RELEASE_VENV}/bin/python" ]] || fail "exact candidate release venv missing"
[[ "$(tr -d '[:space:]' < "${GENERATION_FILE}")" == "${EXPECTED_GENERATION}" ]] || fail "generation marker mismatch"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${EXPECTED_SHA}" ]] || fail "checkout is not the explicitly approved candidate"
[[ "$(tr -d '[:space:]' < "${RELEASE_FILE}")" == "${EXPECTED_SHA}" ]] || fail "release marker is not the explicitly approved candidate"
[[ ! -e "${APP_DIR}/.env" ]] || fail "ignored .env exists"
/usr/bin/python3 "${GATE}" verify-generation --generation-id "${EXPECTED_GENERATION}" --sha "${EXPECTED_SHA}"
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-file "${GENERATION_FILE}"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then fail "service already active"; fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then fail "service already enabled"; fi

start_attempted=0
rollback_on_error(){
 code=$?; trap - EXIT
 if (( code != 0 && start_attempted == 1 )); then
   rm -f "${START_EPOCH_FILE}" >/dev/null 2>&1 || true
   sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
   sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true
   printf 'Acceptance failed; restoring exact immutable predecessor generation %s...\n' "${EXPECTED_GENERATION}" >&2
   sudo "${HOST_RECOVERY}" --generation-id "${EXPECTED_GENERATION}" || printf 'HOST ROLLBACK FAILED: candidate contained; manual recovery required.\n' >&2
 fi
 exit "${code}"
}
trap rollback_on_error EXIT

bash "${APP_DIR}/deploy/preflight-all-paper-deployment.sh" "${EXPECTED_SHA}" "${EXPECTED_GENERATION}"
START_ACCEPTANCE_EPOCH="$("${RELEASE_VENV}/bin/python" -E -s -c 'import time; print(f"{time.time():.9f}")')"
mkdir -p "${CONFIG_DIR}"; umask 077; printf '%s\n' "${START_ACCEPTANCE_EPOCH}" > "${START_EPOCH_FILE}"; chmod 600 "${START_EPOCH_FILE}"
start_attempted=1; sudo systemctl start "${UNIT}"
for _ in $(seq 1 20); do systemctl is-active --quiet "${UNIT}" 2>/dev/null && break; sleep 1; done
systemctl is-active --quiet "${UNIT}" 2>/dev/null || fail "all-PAPER service did not become active"
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-file "${GENERATION_FILE}"
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" --status "${STATUS_PATH}" --require-active --output "${ATTESTATION_OUT}"
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/verify-all-paper-first-cycle-v2.py" --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" --timeout-seconds 900 --max-age-seconds 900 --output "${FIRST_CYCLE_OUT}"
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/verify-operator-sync-complete.py" --db "${DB_PATH}" --output "${OPERATOR_SYNC_OUT}"
"${RELEASE_VENV}/bin/python" -E -s "${APP_DIR}/deploy/verify-three-layer-validation-status.py" --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" --timeout-seconds 60 --max-age-seconds 900 --output "${THREE_LAYER_OUT}"
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-file "${GENERATION_FILE}"
! systemctl is-enabled --quiet "${UNIT}" 2>/dev/null || fail "service became persistent before approval"
[[ "$(tr -d '[:space:]' < "${START_EPOCH_FILE}")" == "${START_ACCEPTANCE_EPOCH}" ]] || fail "start boundary changed"
trap - EXIT
printf 'PASS: host-approved V9 candidate active and accepted against generation %s; boot persistence remains disabled.\n' "${EXPECTED_GENERATION}"
