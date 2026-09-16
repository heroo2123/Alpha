#!/usr/bin/env bash
set -Eeuo pipefail

# Final all-PAPER candidate preparation. Independent host authority must already exist.
# Candidate deployment cannot install/replace release, snapshot, recovery or trust-policy code.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
GENERATION_FILE="${CONFIG_DIR}/weather-paper-cutover-generation.id"
SOURCE_REF="${ALPHA_WEATHER_SOURCE_REF:-${2:-weather-stage1-findings1-4-corrective-2026-09-16}}"
RELEASE_SHA="${1:-}"
UNIT="polymarket-weather-paper.service"
LIBEXEC="/usr/local/libexec/polymarket-weather-paper"
GATE="${LIBEXEC}/release-gate.py"
HOST_SNAPSHOT="${LIBEXEC}/snapshot-rollback.sh"
HOST_RECOVERY="${LIBEXEC}/restore-rollback.sh"
HASH_LOCK="requirements-runtime-hashed.txt"
FINAL_MODULE="polymarket_scanner.weather_only_live_paper_all_signals_final_v9"
fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "${RELEASE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] || fail "usage: $0 <approved-release-sha> [source-ref]"
RELEASE_SHA="${RELEASE_SHA,,}"
[[ -x "${GATE}" && -x "${HOST_SNAPSHOT}" && -x "${HOST_RECOVERY}" ]] || fail "independent host trust bootstrap missing"
/usr/bin/python3 "${GATE}" verify-authority
[[ -d "${APP_DIR}/.git" && -f "${RELEASE_FILE}" ]] || fail "known-good predecessor checkout/release marker required"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} must be stopped before candidate mutation"; fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} must be disabled before candidate mutation"; fi
if pgrep -af 'polymarket_scanner\.weather_only_live_paper|weather_only_live_paper.*\.py' >/dev/null 2>&1; then fail "weather PAPER writer still running"; fi
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "predecessor checkout dirty"
CURRENT_SHA="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
[[ "$(tr -d '[:space:]' < "${RELEASE_FILE}")" == "${CURRENT_SHA}" ]] || fail "predecessor release marker mismatch"

git check-ref-format --branch "${SOURCE_REF}" >/dev/null 2>&1 || fail "invalid source ref"
git -C "${APP_DIR}" remote get-url origin >/dev/null 2>&1 || fail "origin remote missing"
git -C "${APP_DIR}" fetch --prune origin "${SOURCE_REF}"
git -C "${APP_DIR}" cat-file -e "${RELEASE_SHA}^{commit}" 2>/dev/null || fail "candidate commit unavailable"
git -C "${APP_DIR}" merge-base --is-ancestor "${RELEASE_SHA}" FETCH_HEAD || fail "candidate is not part of selected source ref"
/usr/bin/python3 "${GATE}" verify-object --app-dir "${APP_DIR}" --sha "${CURRENT_SHA}"
/usr/bin/python3 "${GATE}" verify-object --app-dir "${APP_DIR}" --sha "${RELEASE_SHA}"

# Snapshot A before any worktree or environment mutation. Snapshot authority refuses
# PREDECESSOR_SHA == candidate and refuses a second generation for this cutover.
GENERATION_ID="$(sudo "${HOST_SNAPSHOT}" --candidate-sha "${RELEASE_SHA}" | tail -n1 | tr -d '[:space:]')"
[[ "${GENERATION_ID}" =~ ^[0-9a-f]{32}$ ]] || fail "immutable host generation not returned"
/usr/bin/python3 "${GATE}" verify-generation --generation-id "${GENERATION_ID}" --sha "${RELEASE_SHA}"

PREPARE_MUTATED=0
rollback_on_error(){
  code=$?
  trap - EXIT
  if (( code != 0 && PREPARE_MUTATED == 1 )); then
    printf 'Candidate preparation failed; restoring exact predecessor generation %s...\n' "${GENERATION_ID}" >&2
    sudo "${HOST_RECOVERY}" --generation-id "${GENERATION_ID}" || {
      sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
      sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true
      printf 'HOST RECOVERY FAILED: service contained; manual recovery required.\n' >&2
    }
  fi
  exit "${code}"
}
trap rollback_on_error EXIT

git -C "${APP_DIR}" checkout --detach "${RELEASE_SHA}"
PREPARE_MUTATED=1
[[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${RELEASE_SHA}" ]] || fail "candidate checkout mismatch"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "candidate checkout dirty"
[[ ! -e "${APP_DIR}/.env" ]] || fail "ignored .env present"
/usr/bin/python3 "${GATE}" verify-object --app-dir "${APP_DIR}" --sha "${RELEASE_SHA}"

for required in deploy/render-all-paper-unit.py deploy/attest-all-paper-runtime-v2.py deploy/weather-paper-release-venv.py \
  deploy/verify-all-paper-first-cycle-v2.py deploy/preflight-all-paper-deployment.sh \
  polymarket_scanner/weather_only_live_paper_all_signals_final_v9.py \
  polymarket_scanner/weather_only_all_paper_deployment_acceptance_v2.py "${HASH_LOCK}"; do
  [[ -f "${APP_DIR}/${required}" ]] || fail "candidate missing required file: ${required}"
done
grep -qF "${FINAL_MODULE}" "${APP_DIR}/deploy/render-all-paper-unit.py" || fail "renderer is not final-v9"
for protected in PYTHONPATH PYTHONHOME PYTHONUSERBASE PYTHONSTARTUP PYTHONINSPECT LD_PRELOAD LD_LIBRARY_PATH; do
  grep -qF "${protected}" "${APP_DIR}/deploy/render-all-paper-unit.py" || fail "renderer missing loader isolation: ${protected}"
done

# Never mutate predecessor .venv. Candidate gets an empty SHA-specific environment.
RELEASE_ROOT="${APP_DIR}/.releases/${RELEASE_SHA}"
RELEASE_VENV="${RELEASE_ROOT}/venv"
VENV_MANIFEST="${RELEASE_ROOT}/venv-manifest.json"
[[ ! -e "${RELEASE_ROOT}" ]] || fail "release-specific directory already exists; refusing reuse"
mkdir -p "${RELEASE_ROOT}"
python3 -E -s "${APP_DIR}/deploy/weather-paper-release-venv.py" build --venv "${RELEASE_VENV}" --lock "${APP_DIR}/${HASH_LOCK}" --manifest "${VENV_MANIFEST}"
python3 -E -s "${APP_DIR}/deploy/weather-paper-release-venv.py" verify --venv "${RELEASE_VENV}" --lock "${APP_DIR}/${HASH_LOCK}" --manifest "${VENV_MANIFEST}"
(
  cd "${APP_DIR}"
  env -i HOME="${CONFIG_DIR}" PATH="${RELEASE_VENV}/bin:/usr/bin:/bin" PYTHONNOUSERSITE=1 ALPHA_DISABLE_DOTENV=1 \
    "${RELEASE_VENV}/bin/python" -E -s - <<'PY'
from polymarket_scanner.weather_only_live_paper_all_signals_final_v7 import assert_attested_all_paper_configuration
from polymarket_scanner.weather_only_live_paper_all_signals_final_v8 import assert_network_environment_isolated
from polymarket_scanner import weather_only_live_paper_all_signals_final_v9
assert_attested_all_paper_configuration(); assert_network_environment_isolated()
print('Final V9 isolated configuration/network import guards passed.')
PY
) || fail "candidate isolated runtime import/configuration failed"

mkdir -p "${CONFIG_DIR}"; umask 077
TMP_GEN="$(mktemp "${CONFIG_DIR}/.weather-paper-generation.XXXXXX")"; TMP_REL="$(mktemp "${CONFIG_DIR}/.weather-paper-release.XXXXXX")"
trap 'rm -f "${TMP_GEN}" "${TMP_REL}"' RETURN
printf '%s\n' "${GENERATION_ID}" > "${TMP_GEN}"; printf '%s\n' "${RELEASE_SHA}" > "${TMP_REL}"; chmod 600 "${TMP_GEN}" "${TMP_REL}"
mv -f "${TMP_GEN}" "${GENERATION_FILE}"; mv -f "${TMP_REL}" "${RELEASE_FILE}"
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-file "${GENERATION_FILE}"

PREPARE_MUTATED=0; trap - EXIT
printf 'PASS: host-approved V9 candidate prepared in fresh release venv; generation=%s. Stopped/disabled.\n' "${GENERATION_ID}"
