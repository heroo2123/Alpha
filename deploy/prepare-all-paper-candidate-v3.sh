#!/usr/bin/env bash
set -Eeuo pipefail

# Final candidate preparation. Trust decisions and rollback verification are made by
# root-owned host tools installed before cutover; candidate code never verifies its
# own approval or recovery authority.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
SOURCE_REF="${ALPHA_WEATHER_SOURCE_REF:-${2:-weather-all-paper-final-review-fixes-2026-09-16}}"
RELEASE_SHA="${1:-}"
UNIT="polymarket-weather-paper.service"
ROLLBACK_DIR="/var/lib/polymarket-weather-paper-rollback"
LIBEXEC="/usr/local/libexec/polymarket-weather-paper"
GATE="${LIBEXEC}/release-gate.py"
HOST_VENV="${LIBEXEC}/weather-paper-venv-snapshot.py"
HOST_RECOVERY="${LIBEXEC}/restore-rollback.sh"
HOST_PATHS="/etc/polymarket-weather-paper/host-paths.conf"
HASH_LOCK="requirements-runtime-hashed.txt"
FINAL_MODULE="polymarket_scanner.weather_only_live_paper_all_signals_final_v9"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${RELEASE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] || fail "usage: $0 <approved-release-sha> [source-ref]"
[[ -d "${APP_DIR}/.git" && -f "${RELEASE_FILE}" ]] || fail "known-good checkout/release marker required"
for host_tool in "${GATE}" "${HOST_VENV}" "${HOST_RECOVERY}" "${HOST_PATHS}"; do [[ -f "${host_tool}" ]] || fail "host trust bootstrap missing: ${host_tool}"; done
[[ -d "${ROLLBACK_DIR}" && ! -L "${ROLLBACK_DIR}" ]] || fail "root rollback custody directory missing"
[[ "$(stat -c '%u' "${ROLLBACK_DIR}")" == "0" ]] || fail "rollback custody directory is not root owned"
ROLLBACK_MODE="$(stat -c '%a' "${ROLLBACK_DIR}")"
(( (8#${ROLLBACK_MODE} & 8#22) == 0 )) || fail "rollback custody directory writable by nonroot"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} must be stopped before candidate mutation"; fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} must be disabled before candidate mutation"; fi
if pgrep -af 'polymarket_scanner\.weather_only_live_paper|weather_only_live_paper.*\.py' >/dev/null 2>&1; then fail "weather PAPER writer still running"; fi

/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
git check-ref-format --branch "${SOURCE_REF}" >/dev/null 2>&1 || fail "invalid source ref"
git -C "${APP_DIR}" remote get-url origin >/dev/null 2>&1 || fail "origin remote missing"
git -C "${APP_DIR}" fetch --prune origin "${SOURCE_REF}"
git -C "${APP_DIR}" cat-file -e "${RELEASE_SHA}^{commit}" 2>/dev/null || fail "candidate commit unavailable"
git -C "${APP_DIR}" merge-base --is-ancestor "${RELEASE_SHA}" FETCH_HEAD || fail "candidate is not part of selected source ref"
/usr/bin/python3 "${GATE}" verify-object --app-dir "${APP_DIR}" --sha "${RELEASE_SHA,,}"

for required in previous-release.sha previous-tree.sha "${UNIT}" previous-active previous-enabled \
  previous-db-present previous-db.sha256 previous-venv.tar previous-venv.json \
  previous-venv-release.sha rollback-manifest-v4.json snapshot-generation-v4; do
  [[ -f "${ROLLBACK_DIR}/${required}" && ! -L "${ROLLBACK_DIR}/${required}" ]] || fail "missing root-custodied rollback snapshot: ${required}"
  [[ "$(stat -c '%u' "${ROLLBACK_DIR}/${required}")" == "0" ]] || fail "rollback artifact not root owned: ${required}"
  mode="$(stat -c '%a' "${ROLLBACK_DIR}/${required}")"
  (( (8#${mode} & 8#22) == 0 )) || fail "rollback artifact writable by nonroot: ${required}"
done
[[ "$(tr -d '[:space:]' < "${ROLLBACK_DIR}/snapshot-generation-v4")" == 'all-paper-rollback-v4-root-custody-hash-bound' ]] || fail "rollback generation not root-custody v4"
CURRENT_SHA="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
[[ "$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-release.sha")" == "${CURRENT_SHA}" ]] || fail "rollback generation not for current release"
[[ "$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-venv-release.sha")" == "${CURRENT_SHA}" ]] || fail "rollback venv not for current release"
/usr/bin/python3 "${HOST_VENV}" verify \
  --venv "${APP_DIR}/.venv" --archive "${ROLLBACK_DIR}/previous-venv.tar" \
  --manifest "${ROLLBACK_DIR}/previous-venv.json"
/usr/bin/python3 "${HOST_VENV}" verify-tree \
  --venv "${APP_DIR}/.venv" --manifest "${ROLLBACK_DIR}/previous-venv.json"

PREPARE_MUTATED=0
rollback_on_error(){
  code=$?
  if (( code != 0 && PREPARE_MUTATED == 1 )); then
    printf 'Candidate preparation failed; invoking host-owned recovery...\n' >&2
    /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 \
      /bin/bash --noprofile --norc "${HOST_RECOVERY}" || {
      sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
      sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true
      printf 'HOST RECOVERY FAILED: service contained; manual recovery required.\n' >&2
    }
  fi
  exit "${code}"
}
trap rollback_on_error EXIT

git -C "${APP_DIR}" checkout --detach "${RELEASE_SHA,,}"
PREPARE_MUTATED=1
[[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${RELEASE_SHA,,}" ]] || fail "candidate checkout mismatch"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "candidate checkout dirty"
[[ ! -e "${APP_DIR}/.env" ]] || fail "ignored .env present"
/usr/bin/python3 "${GATE}" verify-object --app-dir "${APP_DIR}" --sha "${RELEASE_SHA,,}"

for required in deploy/render-all-paper-unit.py deploy/attest-all-paper-runtime-v2.py \
  deploy/verify-all-paper-first-cycle-v2.py deploy/preflight-all-paper-deployment.sh \
  polymarket_scanner/weather_only_live_paper_all_signals_final_v9.py \
  polymarket_scanner/weather_only_all_paper_deployment_acceptance_v2.py "${HASH_LOCK}"; do
  [[ -f "${APP_DIR}/${required}" ]] || fail "candidate missing required file: ${required}"
done
grep -qF "${FINAL_MODULE}" "${APP_DIR}/deploy/render-all-paper-unit.py" || fail "renderer is not final-v9"
grep -qF 'UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY' "${APP_DIR}/deploy/render-all-paper-unit.py" || fail "renderer does not isolate proxy environment"

"${APP_DIR}/.venv/bin/python" -m pip install --require-hashes -r "${APP_DIR}/${HASH_LOCK}"
"${APP_DIR}/.venv/bin/python" -m pip check
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u NO_PROXY -u http_proxy -u https_proxy -u all_proxy -u no_proxy -u SSL_CERT_FILE -u SSL_CERT_DIR \
  PYTHONPATH="${APP_DIR}" ALPHA_DISABLE_DOTENV=1 "${APP_DIR}/.venv/bin/python" - <<'PY'
from polymarket_scanner.weather_only_live_paper_all_signals_final_v7 import assert_attested_all_paper_configuration
from polymarket_scanner.weather_only_live_paper_all_signals_final_v8 import assert_network_environment_isolated
assert_attested_all_paper_configuration(); assert_network_environment_isolated()
print('Final V9 inherited configuration/network import guards passed.')
PY

mkdir -p "${CONFIG_DIR}"; umask 077
TMP_MARKER="$(mktemp "${CONFIG_DIR}/.weather-paper-release.XXXXXX")"
printf '%s\n' "${RELEASE_SHA,,}" > "${TMP_MARKER}"; chmod 600 "${TMP_MARKER}"; mv -f "${TMP_MARKER}" "${RELEASE_FILE}"
bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"

PREPARE_MUTATED=0
trap - EXIT
printf 'PASS: host-approved V9 candidate prepared, stopped and disabled. Release=%s\n' "${RELEASE_SHA,,}"
