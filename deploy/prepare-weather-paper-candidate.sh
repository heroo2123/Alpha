#!/usr/bin/env bash
set -Eeuo pipefail

# Prepare one approved immutable candidate without starting/enabling it. Host authority
# must already be installed independently; this script cannot install or replace it.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
GENERATION_FILE="${CONFIG_DIR}/weather-paper-cutover-generation.id"
REPOSITORY_URL="${ALPHA_WEATHER_REPOSITORY_URL:-https://github.com/heroo2123/Alpha.git}"
SOURCE_REF="${ALPHA_WEATHER_SOURCE_REF:-${2:-weather-stage1-findings1-4-corrective-2026-09-16}}"
RELEASE_SHA="${1:-}"
UNIT="polymarket-weather-paper.service"
FINAL_MODULE="polymarket_scanner.weather_only_live_paper_three_layer_validation"
HOST_GATE="/usr/local/libexec/polymarket-weather-paper/release-gate.py"
HOST_SNAPSHOT="/usr/local/libexec/polymarket-weather-paper/snapshot-rollback.sh"
LOCK_NAME="requirements-runtime-hashed.txt"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${RELEASE_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "usage: $0 <exact-release-sha> [source-branch]"
git check-ref-format --branch "${SOURCE_REF}" >/dev/null 2>&1 || fail "invalid source branch/ref"
[[ -x "${HOST_GATE}" && -x "${HOST_SNAPSHOT}" ]] || fail "independent host authority not installed"
/usr/bin/python3 "${HOST_GATE}" verify-authority

if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} is active; stop explicitly before preparation"; fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} is enabled; disable explicitly before preparation"; fi
if pgrep -af 'polymarket_scanner\.weather_only_live_paper|weather_only_live_paper(_v[234]|_corrective|_final|_three_layer_validation)?\.py' >/dev/null 2>&1; then fail "weather-paper process already running"; fi
[[ -d "${APP_DIR}/.git" ]] || fail "known-good predecessor checkout is required before cutover"
[[ -f "${RELEASE_FILE}" ]] || fail "known-good predecessor release marker missing"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "predecessor checkout dirty"

git -C "${APP_DIR}" remote get-url origin >/dev/null 2>&1 || fail "checkout has no origin"
git -C "${APP_DIR}" fetch --prune origin "${SOURCE_REF}"
git -C "${APP_DIR}" cat-file -e "${RELEASE_SHA}^{commit}" 2>/dev/null || fail "candidate object absent after fetch"
git -C "${APP_DIR}" merge-base --is-ancestor "${RELEASE_SHA}" FETCH_HEAD || fail "candidate not on selected source ref"
/usr/bin/python3 "${HOST_GATE}" verify-object --app-dir "${APP_DIR}" --sha "${RELEASE_SHA}"

# CRITICAL ORDER: capture immutable predecessor generation before checkout mutation.
GENERATION_ID="$(sudo "${HOST_SNAPSHOT}" --candidate-sha "${RELEASE_SHA}" | tail -n1 | tr -d '[:space:]')"
[[ "${GENERATION_ID}" =~ ^[0-9a-f]{32}$ ]] || fail "host snapshot did not return exact generation ID"
/usr/bin/python3 "${HOST_GATE}" verify-generation --generation-id "${GENERATION_ID}" --sha "${RELEASE_SHA}"

git -C "${APP_DIR}" checkout --detach "${RELEASE_SHA}"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${RELEASE_SHA}" ]] || fail "candidate checkout mismatch"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "candidate checkout dirty"

for required in deploy/verify-runtime-release.sh deploy/render-weather-paper-unit.py deploy/start-weather-paper-candidate.sh deploy/attest-weather-paper-runtime.py deploy/weather-paper-release-venv.py "${LOCK_NAME}" polymarket_scanner/weather_only_live_paper_three_layer_validation.py polymarket_scanner/weather_only_runtime_attestation.py; do
  [[ -f "${APP_DIR}/${required}" ]] || fail "candidate lacks required file: ${required}"
done
grep -qF "${FINAL_MODULE}" "${APP_DIR}/deploy/render-weather-paper-unit.py" || fail "renderer entrypoint mismatch"

RELEASE_ROOT="${APP_DIR}/.releases/${RELEASE_SHA}"
RELEASE_VENV="${RELEASE_ROOT}/venv"
VENV_MANIFEST="${RELEASE_ROOT}/venv-manifest.json"
[[ ! -e "${RELEASE_ROOT}" ]] || fail "release-specific directory already exists; refusing reuse"
mkdir -p "${RELEASE_ROOT}"
python3 -E -s "${APP_DIR}/deploy/weather-paper-release-venv.py" build --venv "${RELEASE_VENV}" --lock "${APP_DIR}/${LOCK_NAME}" --manifest "${VENV_MANIFEST}"
python3 -E -s "${APP_DIR}/deploy/weather-paper-release-venv.py" verify --venv "${RELEASE_VENV}" --lock "${APP_DIR}/${LOCK_NAME}" --manifest "${VENV_MANIFEST}"

# Import from the exact application working directory with no inherited loader state.
# This mirrors the systemd WorkingDirectory without using PYTHONPATH.
(
  cd "${APP_DIR}"
  env -i HOME="${CONFIG_DIR}" PATH="${RELEASE_VENV}/bin:/usr/bin:/bin" PYTHONNOUSERSITE=1 \
    "${RELEASE_VENV}/bin/python" -E -s -c "import ${FINAL_MODULE}; print('runtime import passed')"
) || fail "candidate runtime import failed"

mkdir -p "${CONFIG_DIR}"; umask 077
TMP_GEN="$(mktemp "${CONFIG_DIR}/.weather-paper-generation.XXXXXX")"
TMP_REL="$(mktemp "${CONFIG_DIR}/.weather-paper-release.XXXXXX")"
trap 'rm -f "${TMP_GEN}" "${TMP_REL}"' EXIT
printf '%s\n' "${GENERATION_ID}" > "${TMP_GEN}"
printf '%s\n' "${RELEASE_SHA}" > "${TMP_REL}"
chmod 600 "${TMP_GEN}" "${TMP_REL}"
mv -f "${TMP_GEN}" "${GENERATION_FILE}"
mv -f "${TMP_REL}" "${RELEASE_FILE}"
trap - EXIT
/usr/bin/python3 "${HOST_GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --generation-file "${GENERATION_FILE}"
printf 'PASS: candidate %s prepared in fresh release venv; immutable predecessor generation=%s. NOT started/enabled.\n' "${RELEASE_SHA}" "${GENERATION_ID}"
