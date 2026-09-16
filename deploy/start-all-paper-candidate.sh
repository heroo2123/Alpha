#!/usr/bin/env bash
set -Eeuo pipefail

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
OPERATOR_SYNC_OUT="${CONFIG_DIR}/all-paper-operator-sync-acceptance.json"
ROLLBACK_DIR="/var/lib/polymarket-weather-paper-rollback"
LIBEXEC="/usr/local/libexec/polymarket-weather-paper"
GATE="${LIBEXEC}/release-gate.py"
HOST_RECOVERY="${LIBEXEC}/restore-rollback.sh"
EXPECTED_SHA="${1:-}"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "usage: $0 <exact-approved-release-sha>"
for tool in "${GATE}" "${HOST_RECOVERY}"; do [[ -f "${tool}" ]] || fail "host authority missing: ${tool}"; done
[[ -d "${APP_DIR}/.git" && -x "${APP_DIR}/.venv/bin/python" && -f "${RELEASE_FILE}" ]] || fail "candidate checkout/runtime missing"
[[ -d "${ROLLBACK_DIR}" && ! -L "${ROLLBACK_DIR}" ]] || fail "root rollback custody directory missing"
[[ "$(stat -c '%u' "${ROLLBACK_DIR}")" == "0" ]] || fail "rollback custody directory is not root owned"
ROLLBACK_MODE="$(stat -c '%a' "${ROLLBACK_DIR}")"
(( (8#${ROLLBACK_MODE} & 8#22) == 0 )) || fail "rollback custody directory writable by nonroot"
for snapshot in previous-release.sha previous-tree.sha "${UNIT}" previous-active previous-enabled \
  previous-db-present previous-db.sha256 previous-venv.tar previous-venv.json \
  previous-venv-release.sha rollback-manifest-v4.json snapshot-generation-v4; do
  [[ -f "${ROLLBACK_DIR}/${snapshot}" && ! -L "${ROLLBACK_DIR}/${snapshot}" ]] || fail "missing root-custodied rollback snapshot: ${snapshot}"
  [[ "$(stat -c '%u' "${ROLLBACK_DIR}/${snapshot}")" == "0" ]] || fail "rollback artifact not root owned: ${snapshot}"
  mode="$(stat -c '%a' "${ROLLBACK_DIR}/${snapshot}")"
  (( (8#${mode} & 8#22) == 0 )) || fail "rollback artifact writable by nonroot: ${snapshot}"
done
[[ "$(tr -d '[:space:]' < "${ROLLBACK_DIR}/snapshot-generation-v4")" == 'all-paper-rollback-v4-root-custody-hash-bound' ]] || fail "rollback generation not root-custody v4"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${EXPECTED_SHA}" ]] || fail "checkout is not candidate"
[[ "$(tr -d '[:space:]' < "${RELEASE_FILE}")" == "${EXPECTED_SHA}" ]] || fail "release marker is not candidate"
[[ ! -e "${APP_DIR}/.env" ]] || fail "ignored .env exists"
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then fail "service already active"; fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then fail "service already enabled"; fi

rollback_on_error(){
  code=$?
  if (( code != 0 )); then
    rm -f "${START_EPOCH_FILE}" >/dev/null 2>&1 || true
    printf 'Acceptance failed; invoking host-owned rollback...\n' >&2
    if ! /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 \
      /bin/bash --noprofile --norc "${HOST_RECOVERY}"; then
      sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
      sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true
      printf 'HOST ROLLBACK FAILED: candidate contained; manual recovery required.\n' >&2
    fi
  fi
  exit "${code}"
}
trap rollback_on_error EXIT

bash "${APP_DIR}/deploy/preflight-all-paper-deployment.sh"
START_ACCEPTANCE_EPOCH="$("${APP_DIR}/.venv/bin/python" -c 'import time; print(f"{time.time():.9f}")')"
mkdir -p "${CONFIG_DIR}"; umask 077
printf '%s\n' "${START_ACCEPTANCE_EPOCH}" > "${START_EPOCH_FILE}"; chmod 600 "${START_EPOCH_FILE}"

sudo systemctl start "${UNIT}"
for _ in $(seq 1 20); do systemctl is-active --quiet "${UNIT}" 2>/dev/null && break; sleep 1; done
systemctl is-active --quiet "${UNIT}" 2>/dev/null || fail "all-PAPER service did not become active"
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"

"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/attest-all-paper-runtime-v2.py" \
  --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" \
  --status "${STATUS_PATH}" --require-active --output "${ATTESTATION_OUT}"
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/verify-all-paper-first-cycle-v2.py" \
  --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" \
  --timeout-seconds 900 --max-age-seconds 900 --output "${FIRST_CYCLE_OUT}"
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/verify-operator-sync-complete.py" \
  --db "${DB_PATH}" --output "${OPERATOR_SYNC_OUT}"
"${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/verify-three-layer-validation-status.py" \
  --status "${STATUS_PATH}" --release-sha "${EXPECTED_SHA}" --not-before "${START_ACCEPTANCE_EPOCH}" \
  --timeout-seconds 60 --max-age-seconds 900 --output "${THREE_LAYER_OUT}"

/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
! systemctl is-enabled --quiet "${UNIT}" 2>/dev/null || fail "service became persistent before approval"
[[ "$(tr -d '[:space:]' < "${START_EPOCH_FILE}")" == "${START_ACCEPTANCE_EPOCH}" ]] || fail "start boundary changed"
trap - EXIT
printf 'PASS: host-approved V9 candidate active and accepted; boot persistence remains disabled.\n'
