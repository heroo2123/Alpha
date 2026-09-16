#!/usr/bin/env bash
set -Eeuo pipefail

# Prepare one exact all-PAPER candidate against one immutable host-created cutover
# generation. Host authority v2 is independently provisioned and never installed by
# this candidate. Candidate dependencies are built from scratch in a SHA-scoped venv;
# the predecessor environment is never pip-mutated or reconstructed.
PATH=/usr/bin:/bin
export PATH
unset BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM || true
unset PYTHONPATH PYTHONHOME PYTHONUSERBASE PYTHONSTARTUP PYTHONINSPECT LD_PRELOAD LD_LIBRARY_PATH || true
export PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1

APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
SOURCE_REF="${ALPHA_WEATHER_SOURCE_REF:-${3:-weather-paper-corrective-stage1-host-trust-2026-09-16}}"
RELEASE_SHA="${1:-}"
GENERATION_ID="${2:-}"
UNIT="polymarket-weather-paper.service"
AUTHORITY="/usr/local/libexec/polymarket-weather-paper/v2/authority.py"
RELEASES_ROOT="/var/lib/polymarket-weather-paper-releases"
HASH_LOCK="requirements-runtime-hashed.txt"
FINAL_MODULE="polymarket_scanner.weather_only_live_paper_all_signals_final_v9"
CURRENT_USER="$(id -un)"
CURRENT_GROUP="$(id -gn)"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${RELEASE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] || fail "usage: $0 <approved-release-sha> <generation-id> [source-ref]"
RELEASE_SHA="${RELEASE_SHA,,}"
[[ "${GENERATION_ID}" =~ ^gen-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{12}-[0-9a-f]{12}$ ]] \
  || fail "exact immutable cutover generation ID required"
[[ -d "${APP_DIR}/.git" && -f "${RELEASE_FILE}" ]] || fail "known-good checkout/release marker required"
[[ -f "${AUTHORITY}" && ! -L "${AUTHORITY}" ]] || fail "independent host authority v2 missing"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} must be stopped before candidate mutation"; fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} must be disabled before candidate mutation"; fi
if pgrep -af 'polymarket_scanner\.weather_only_live_paper|weather_only_live_paper.*\.py' >/dev/null 2>&1; then fail "weather PAPER writer still running"; fi

/usr/bin/python3 "${AUTHORITY}" verify-authority
/usr/bin/python3 "${AUTHORITY}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
/usr/bin/python3 "${AUTHORITY}" verify-generation \
  --generation-id "${GENERATION_ID}" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" \
  --candidate-sha "${RELEASE_SHA}" --phase predecessor

# Branch/ref is transport only. Trust is the exact SHA/tree approved by independent
# host data, so branch movement cannot replace authority or silently change candidate.
git check-ref-format --branch "${SOURCE_REF}" >/dev/null 2>&1 || fail "invalid source ref"
git -C "${APP_DIR}" remote get-url origin >/dev/null 2>&1 || fail "origin remote missing"
git -C "${APP_DIR}" fetch --prune origin "${SOURCE_REF}"
git -C "${APP_DIR}" cat-file -e "${RELEASE_SHA}^{commit}" 2>/dev/null || fail "candidate commit unavailable"
git -C "${APP_DIR}" merge-base --is-ancestor "${RELEASE_SHA}" FETCH_HEAD || fail "candidate is not part of selected source ref"
/usr/bin/python3 "${AUTHORITY}" verify-object --app-dir "${APP_DIR}" --sha "${RELEASE_SHA}"

PREPARE_MUTATED=0
rollback_on_error(){
  code=$?
  if (( code != 0 && PREPARE_MUTATED == 1 )); then
    printf 'Candidate preparation failed; invoking independent recovery for generation %s...\n' "${GENERATION_ID}" >&2
    if ! sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 \
      /usr/bin/python3 "${AUTHORITY}" recover --generation-id "${GENERATION_ID}"; then
      sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
      sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true
      printf 'HOST RECOVERY FAILED: service contained; manual recovery required.\n' >&2
    fi
  fi
  exit "${code}"
}
trap rollback_on_error EXIT

git -C "${APP_DIR}" checkout --detach "${RELEASE_SHA}"
PREPARE_MUTATED=1
[[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${RELEASE_SHA}" ]] || fail "candidate checkout mismatch"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "candidate checkout dirty"
[[ ! -e "${APP_DIR}/.env" ]] || fail "ignored .env present"
/usr/bin/python3 "${AUTHORITY}" verify-object --app-dir "${APP_DIR}" --sha "${RELEASE_SHA}"

for required in \
  deploy/render-all-paper-unit.py deploy/attest-all-paper-runtime-v2.py \
  deploy/verify-all-paper-first-cycle-v2.py deploy/preflight-all-paper-deployment.sh \
  deploy/weather-paper-release-env.py \
  polymarket_scanner/weather_only_live_paper_all_signals_final_v9.py \
  polymarket_scanner/weather_only_all_paper_deployment_acceptance_v2.py "${HASH_LOCK}"; do
  [[ -f "${APP_DIR}/${required}" ]] || fail "candidate missing required file: ${required}"
done

RELEASE_TREE="$(git -C "${APP_DIR}" rev-parse HEAD^{tree} | tr -d '[:space:]')"
RELEASE_ROOT="${RELEASES_ROOT}/${RELEASE_SHA}"
VENV="${RELEASE_ROOT}/venv"
VENV_PYTHON="${VENV}/bin/python"
VENV_MANIFEST="${RELEASE_ROOT}/venv-manifest.json"
RELEASE_EVIDENCE="${RELEASE_ROOT}/release-evidence.json"
[[ ! -e "${RELEASE_ROOT}" ]] || fail "candidate release environment already exists; fresh build required"

# Parent and completed release are root-custodied. Only this new empty release
# directory is temporarily owned by the deploy user while it is constructed.
sudo install -d -o root -g root -m 0755 "${RELEASES_ROOT}"
sudo install -d -o "${CURRENT_USER}" -g "${CURRENT_GROUP}" -m 0755 "${RELEASE_ROOT}"
python3 -m venv --clear "${VENV}"
"${VENV_PYTHON}" -I -s -m pip install --disable-pip-version-check --no-deps --require-hashes \
  -r "${APP_DIR}/${HASH_LOCK}"
"${VENV_PYTHON}" -I -s -m pip check
SITE_PACKAGES="$("${VENV_PYTHON}" -I -s -c 'import site; p=site.getsitepackages(); assert len(p)==1; print(p[0])')"
printf '%s\n' "${APP_DIR}" > "${SITE_PACKAGES}/alpha_weather_candidate.pth"
# Remove venv bootstrap tooling so the final distribution inventory is exactly the
# hash lock: no inherited/user/system package and no unlisted pip/setuptools/wheel.
"${VENV_PYTHON}" -I -s -m pip uninstall -y setuptools wheel >/dev/null 2>&1 || true
"${VENV_PYTHON}" -I -s -m pip uninstall -y pip >/dev/null 2>&1 || true

env -i PATH=/usr/bin:/bin HOME=/nonexistent PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 \
  "${VENV_PYTHON}" -I -s "${APP_DIR}/deploy/weather-paper-release-env.py" manifest \
  --venv "${VENV}" --app-dir "${APP_DIR}" --release-sha "${RELEASE_SHA}" \
  --release-tree "${RELEASE_TREE}" --generation-id "${GENERATION_ID}" \
  --lock "${APP_DIR}/${HASH_LOCK}" --runtime-module "${FINAL_MODULE}" --manifest "${VENV_MANIFEST}"
/usr/bin/python3 "${APP_DIR}/deploy/weather-paper-release-env.py" evidence \
  --manifest "${VENV_MANIFEST}" --output "${RELEASE_EVIDENCE}" --service-identity "${UNIT}"

# Freeze final runtime bytes outside candidate ownership before release publication.
sudo chown -R root:root "${RELEASE_ROOT}"
sudo chmod -R go-w "${RELEASE_ROOT}"
sudo chmod 0444 "${VENV_MANIFEST}" "${RELEASE_EVIDENCE}"

/usr/bin/python3 "${AUTHORITY}" verify-runtime --app-dir "${APP_DIR}" --sha "${RELEASE_SHA}" \
  --generation-id "${GENERATION_ID}" --evidence "${RELEASE_EVIDENCE}"

env -i PATH=/usr/bin:/bin HOME=/nonexistent PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
  LANG=C.UTF-8 LC_ALL=C.UTF-8 ALPHA_DISABLE_DOTENV=1 \
  "${VENV_PYTHON}" -I -s -c \
  "import ${FINAL_MODULE}; print('isolated final V9 runtime import passed')"

mkdir -p "${CONFIG_DIR}"
umask 077
TMP_MARKER="$(mktemp "${CONFIG_DIR}/.weather-paper-release.XXXXXX")"
printf '%s\n' "${RELEASE_SHA}" > "${TMP_MARKER}"
chmod 600 "${TMP_MARKER}"
mv -f "${TMP_MARKER}" "${RELEASE_FILE}"
bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"
/usr/bin/python3 "${AUTHORITY}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
/usr/bin/python3 "${AUTHORITY}" verify-generation \
  --generation-id "${GENERATION_ID}" --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" \
  --candidate-sha "${RELEASE_SHA}" --phase candidate
/usr/bin/python3 "${AUTHORITY}" verify-runtime --app-dir "${APP_DIR}" --sha "${RELEASE_SHA}" \
  --generation-id "${GENERATION_ID}" --evidence "${RELEASE_EVIDENCE}"

PREPARE_MUTATED=0
trap - EXIT
printf 'PASS: candidate prepared with immutable generation %s and fresh SHA-scoped venv. Release=%s\n' \
  "${GENERATION_ID}" "${RELEASE_SHA}"
