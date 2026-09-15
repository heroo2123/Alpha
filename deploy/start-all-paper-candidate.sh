#!/usr/bin/env bash
set -Eeuo pipefail

# Explicit active-but-nonpersistent acceptance for one immutable final all-PAPER
# candidate. Any failure restores the exact v2 source/unit/ledger/venv generation.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
STATUS_PATH="${WEATHER_PAPER_STATUS_PATH:-/var/lib/polymarket-weather-paper/status.json}"
UNIT="polymarket-weather-paper.service"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
START_EPOCH_FILE="${CONFIG_DIR}/all-paper-final-start.epoch"
ATTESTATION_OUT="${CONFIG_DIR}/all-paper-active-attestation.json"
FIRST_CYCLE_OUT="${CONFIG_DIR}/all-paper-first-cycle-acceptance.json"
THREE_LAYER_OUT="${CONFIG_DIR}/all-paper-three-layer-runtime-acceptance.json"
ROLLBACK_SCRIPT="${APP_DIR}/deploy/restore-all-paper-rollback.sh"
ROLLBACK_DIR="${CONFIG_DIR}/all-paper-rollback"
EXPECTED_SHA="${1:-}"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "usage: $0 <exact-40-char-lowercase-release-sha>"
[[ -d "${APP_DIR}/.git" ]] || fail "missing isolated weather-paper checkout"
[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "missing weather-paper virtualenv"
[[ -f "${RELEASE_FILE}" ]] || fail "missing release marker"
[[ -f "${ROLLBACK_SCRIPT}" ]] || fail "missing rollback restore script"
for snapshot in \
  previous-release.sha "${UNIT}" previous-active previous-enabled previous-db-present \
  previous-db.sha256 previous-venv.tar previous-venv.json previous-venv-release.sha \
  snapshot-generation-v2
do
  [[ -f "${ROLLBACK_DIR}/${snapshot}" ]] || fail "missing v2 rollback snapshot: ${snapshot}"
done
[[ "$(tr -d '[:space:]' < "${ROLLBACK_DIR}/snapshot-generation-v2")" == 'all-paper-rollback-v2-exact-venv' ]] \
  || fail "rollback generation is not exact-venv v2"
[[ "$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-venv-release.sha")" == \
   "$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-release.sha")" ]] \
  || fail "rollback virtualenv release mismatch"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')" == "${EXPECTED_SHA}" ]] || fail "checkout is not approved candidate"
[[ "$(tr -d '[:space:]' < "${RELEASE_FILE}")" == "${EXPECTED_SHA}" ]] || fail "release marker is not approved candidate"
[[ ! -e "${APP_DIR}/.env" ]] || fail "ignored .env exists in candidate checkout"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} already active"; fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} already enabled"; fi

rollback_on_error(){
  code=$?
  if (( code != 0 )); then
    printf 'All-PAPER acceptance failed; restoring exact pre-cutover generation...\n' >&2
    rm -f "${START_EPOCH_FILE}" >/dev/null 2>&1 || true
    if ! bash "${ROLLBACK_SCRIPT}"; then
      printf 'ROLLBACK FAILED: candidate contained but previous service was not fully restored.\n' >&2
      sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
      sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true
    fi
  fi
  exit "${code}"
}
trap rollback_on_error EXIT

bash "${APP_DIR}/deploy/preflight-all-paper-deployment.sh"

START_ACCEPTANCE_EPOCH="$("${APP_DIR}/.venv/bin/python" -c 'import time; print(f"{time.time():.9f}")')"
mkdir -p "${CONFIG_DIR}"
umask 077
TMP_START="$(mktemp "${CONFIG_DIR}/.all-paper-final-start.XXXXXX")"
printf '%s\n' "${START_ACCEPTANCE_EPOCH}" > "${TMP_START}"
chmod 600 "${TMP_START}"
mv -f "${TMP_START}" "${START_EPOCH_FILE}"

sudo systemctl start "${UNIT}"
for _ in $(seq 1 20); do
  if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then break; fi
  sleep 1
done
systemctl is-active --quiet "${UNIT}" 2>/dev/null || fail "all-PAPER service did not become active"

"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" \
  --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" \
  --status "${STATUS_PATH}" --require-active --output "${ATTESTATION_OUT}"
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/verify-all-paper-first-cycle-v2.py" \
  --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" \
  --not-before "${START_ACCEPTANCE_EPOCH}" --timeout-seconds 600 --max-age-seconds 600 \
  --output "${FIRST_CYCLE_OUT}"
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/verify-three-layer-validation-status.py" \
  --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" \
  --not-before "${START_ACCEPTANCE_EPOCH}" --timeout-seconds 60 --max-age-seconds 600 \
  --output "${THREE_LAYER_OUT}"

[[ "$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')" == "${EXPECTED_SHA}" ]] || fail "checkout changed during acceptance"
[[ "$(tr -d '[:space:]' < "${RELEASE_FILE}")" == "${EXPECTED_SHA}" ]] || fail "release marker changed during acceptance"
! systemctl is-enabled --quiet "${UNIT}" 2>/dev/null || fail "service became persistent before approval"
[[ "$(tr -d '[:space:]' < "${START_EPOCH_FILE}")" == "${START_ACCEPTANCE_EPOCH}" ]] || fail "start boundary changed"

trap - EXIT
printf '\nPASS: final all-PAPER candidate is active and accepted but remains DISABLED for boot persistence.\n'
printf 'Release: %s\n' "${EXPECTED_SHA}"
printf 'Operator invalidation, retry, config and exact-rollback gates are proven.\n'
printf 'Persistence still requires a fresh post-start WRH+NWS+GEFS capture and repeated final attestation.\n'
printf 'Real orders, wallet/signing authority and result-lag delivery remain disabled.\n'
