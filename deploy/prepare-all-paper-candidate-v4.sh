#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
RELEASE_SHA="${1:-}"; GENERATION_ID="${2:-}"; SOURCE_REF="${3:-weather-all-paper-post-final-review-corrective-2026-09-16}"
AUTH=/usr/local/libexec/polymarket-weather-paper-v2/authority.py
LOCK=requirements-runtime-hashed.txt
fail(){ echo "ERROR: $*" >&2; exit 1; }
[[ "${RELEASE_SHA}" =~ ^[0-9a-f]{40}$ && "${GENERATION_ID}" =~ ^[0-9a-f]{64}$ ]] || fail "usage: $0 <candidate-sha> <cutover-generation-id> [source-ref]"
[[ -f "${AUTH}" && -d "${APP_DIR}/.git" && -f "${RELEASE_FILE}" ]] || fail "independent host authority/current release missing"
if systemctl is-active --quiet polymarket-weather-paper.service 2>/dev/null || systemctl is-enabled --quiet polymarket-weather-paper.service 2>/dev/null; then fail "candidate preparation requires stopped and disabled service"; fi
/usr/bin/python3 "${AUTH}" authority-info >/dev/null
/usr/bin/python3 "${AUTH}" verify-generation --generation-id "${GENERATION_ID}" --app-dir "${APP_DIR}" --candidate-sha "${RELEASE_SHA}"
git check-ref-format --branch "${SOURCE_REF}" >/dev/null || fail "invalid source ref"
git -C "${APP_DIR}" fetch --prune origin "${SOURCE_REF}"
git -C "${APP_DIR}" cat-file -e "${RELEASE_SHA}^{commit}" || fail "candidate object missing"
git -C "${APP_DIR}" merge-base --is-ancestor "${RELEASE_SHA}" FETCH_HEAD || fail "candidate not on selected source ref"
MUTATED=0
rollback(){ code=$?; if (( code != 0 && MUTATED == 1 )); then sudo /usr/bin/python3 "${AUTH}" recover --generation-id "${GENERATION_ID}" --deploy-uid "$(id -u)" --deploy-gid "$(id -g)" || true; fi; exit "$code"; }
trap rollback EXIT
git -C "${APP_DIR}" checkout --detach "${RELEASE_SHA}"; MUTATED=1
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "candidate checkout dirty"
[[ ! -e "${APP_DIR}/.env" ]] || fail "implicit .env forbidden"
/usr/bin/python3 "${AUTH}" verify-generation --generation-id "${GENERATION_ID}" --app-dir "${APP_DIR}" --candidate-sha "${RELEASE_SHA}"
/usr/bin/python3 "${APP_DIR}/deploy/release_environment.py" build --app-dir "${APP_DIR}" --release-sha "${RELEASE_SHA}" --lock "${APP_DIR}/${LOCK}" --python /usr/bin/python3
MANIFEST="${APP_DIR}/.releases/${RELEASE_SHA}/environment-manifest.json"
sudo /usr/bin/python3 "${AUTH}" seal-candidate-environment --generation-id "${GENERATION_ID}" --candidate-sha "${RELEASE_SHA}" --app-dir "${APP_DIR}" --environment-manifest "${MANIFEST}"
mkdir -p "${CONFIG_DIR}"; umask 077; printf '%s\n' "${RELEASE_SHA}" > "${RELEASE_FILE}"
/usr/bin/python3 "${AUTH}" verify-checkout --generation-id "${GENERATION_ID}" --app-dir "${APP_DIR}" --candidate-sha "${RELEASE_SHA}" --release-file "${RELEASE_FILE}"
/usr/bin/python3 "${AUTH}" verify-candidate-environment --generation-id "${GENERATION_ID}" --app-dir "${APP_DIR}" --candidate-sha "${RELEASE_SHA}" --environment-manifest "${MANIFEST}"
MUTATED=0; trap - EXIT
printf 'PASS: clean V10 candidate environment prepared. Release=%s Generation=%s\n' "${RELEASE_SHA}" "${GENERATION_ID}"
