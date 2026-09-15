#!/usr/bin/env bash
set -Eeuo pipefail

# Prepare one immutable final all-weather PAPER candidate without starting it.  The
# exact-venv rollback generation must exist before any checkout/dependency mutation.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
SOURCE_REF="${ALPHA_WEATHER_SOURCE_REF:-${2:-weather-all-paper-final-corrective-2026-09-16}}"
RELEASE_SHA="${1:-}"
UNIT="polymarket-weather-paper.service"
FINAL_MODULE="polymarket_scanner.weather_only_live_paper_all_signals_final_v7"
ROLLBACK_DIR="${CONFIG_DIR}/all-paper-rollback"
HASH_LOCK="requirements-runtime-hashed.txt"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${RELEASE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] || fail "usage: $0 <exact-release-sha> [source-branch]"
git check-ref-format --branch "${SOURCE_REF}" >/dev/null 2>&1 || fail "invalid source branch/ref"
[[ -d "${APP_DIR}/.git" ]] || fail "existing weather-paper checkout required"
[[ -f "${RELEASE_FILE}" ]] || fail "existing weather-paper release marker required"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} is active; stop it after taking the v2 rollback snapshot"; fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then fail "${UNIT} is enabled; disable it after taking the v2 rollback snapshot"; fi
if pgrep -af 'polymarket_scanner\.weather_only_live_paper|weather_only_live_paper.*\.py' >/dev/null 2>&1; then
  fail "a weather-paper process is already running outside the stopped service"
fi

for required in \
  previous-release.sha "${UNIT}" previous-active previous-enabled previous-db-present \
  previous-db.sha256 previous-venv.tar previous-venv.json previous-venv-release.sha \
  snapshot-generation-v2
do
  [[ -f "${ROLLBACK_DIR}/${required}" ]] || fail "missing v2 rollback snapshot: ${required}"
done
[[ "$(tr -d '[:space:]' < "${ROLLBACK_DIR}/snapshot-generation-v2")" == 'all-paper-rollback-v2-exact-venv' ]] \
  || fail "rollback generation is not exact-venv v2"
PREVIOUS_SHA="$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-release.sha")"
CURRENT_HEAD="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
CURRENT_MARKER="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${PREVIOUS_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "rollback snapshot SHA invalid"
[[ "${CURRENT_HEAD}" == "${PREVIOUS_SHA}" && "${CURRENT_MARKER}" == "${PREVIOUS_SHA}" ]] \
  || fail "rollback generation does not describe the current known-good release"
[[ "$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-venv-release.sha")" == "${PREVIOUS_SHA}" ]] \
  || fail "rollback virtualenv belongs to another release"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] \
  || fail "weather-paper checkout differs from its authorized commit"

# Fetch is non-mutating to the working tree.  Pull the candidate helper from the exact
# requested commit and verify the rollback archive before candidate checkout begins.
git -C "${APP_DIR}" remote get-url origin >/dev/null 2>&1 || fail "weather-paper checkout has no origin remote"
git -C "${APP_DIR}" fetch --prune origin "${SOURCE_REF}"
git -C "${APP_DIR}" cat-file -e "${RELEASE_SHA}^{commit}" 2>/dev/null || fail "requested commit is not present after fetch"
git -C "${APP_DIR}" merge-base --is-ancestor "${RELEASE_SHA}" FETCH_HEAD || fail "requested commit is not part of selected source ref"
TMP_HELPER="$(mktemp /tmp/weather-paper-venv-prep.XXXXXX.py)"
git -C "${APP_DIR}" show "${RELEASE_SHA}:deploy/weather-paper-venv-snapshot.py" > "${TMP_HELPER}" 
chmod 600 "${TMP_HELPER}"
/usr/bin/python3 "${TMP_HELPER}" verify \
  --venv "${APP_DIR}/.venv" \
  --archive "${ROLLBACK_DIR}/previous-venv.tar" \
  --manifest "${ROLLBACK_DIR}/previous-venv.json"
rm -f "${TMP_HELPER}"

PREPARE_MUTATED=0
rollback_prepare_on_error(){
  code=$?
  if (( code != 0 && PREPARE_MUTATED == 1 )); then
    printf 'Candidate preparation failed after mutation; restoring exact v2 rollback generation...\n' >&2
    if [[ -f "${APP_DIR}/deploy/restore-all-paper-rollback.sh" ]]; then
      bash "${APP_DIR}/deploy/restore-all-paper-rollback.sh" || {
        sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
        sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true
        printf 'PREPARATION ROLLBACK FAILED: candidate contained; manual recovery required.\n' >&2
      }
    fi
  fi
  exit "${code}"
}
trap rollback_prepare_on_error EXIT

git -C "${APP_DIR}" checkout --detach "${RELEASE_SHA}"
PREPARE_MUTATED=1
ACTUAL_SHA="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
[[ "${ACTUAL_SHA}" == "${RELEASE_SHA,,}" ]] || fail "detached checkout did not land on requested SHA"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "prepared checkout is not clean"

for required in \
  deploy/verify-runtime-release.sh deploy/render-all-paper-unit.py \
  deploy/attest-all-paper-runtime-v2.py deploy/verify-all-paper-first-cycle-v2.py \
  deploy/weather-paper-venv-snapshot.py deploy/restore-all-paper-rollback.sh \
  deploy/restore-all-paper-rollback-v2.sh deploy/setup-all-paper-service.sh \
  deploy/preflight-all-paper-deployment.sh deploy/start-all-paper-candidate.sh \
  deploy/enable-all-paper-persistence.sh \
  polymarket_scanner/weather_only_live_paper_all_signals_final_v7.py \
  polymarket_scanner/weather_only_operator_state_corrective.py \
  polymarket_scanner/weather_only_operator_state_corrective_v2.py \
  polymarket_scanner/weather_only_all_paper_deployment_acceptance_v2.py \
  "${HASH_LOCK}"
do
  [[ -f "${APP_DIR}/${required}" ]] || fail "candidate lacks required final corrective file: ${required}"
done
[[ ! -e "${APP_DIR}/.env" ]] || fail "candidate checkout contains ignored .env"
grep -qF "${FINAL_MODULE}" "${APP_DIR}/deploy/render-all-paper-unit.py" || fail "renderer does not point to final-v7 entrypoint"
grep -qF 'Environment=ALPHA_DISABLE_DOTENV=1' "${APP_DIR}/deploy/render-all-paper-unit.py" || fail "renderer does not disable dotenv"

[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "existing virtualenv disappeared before candidate dependency install"
"${APP_DIR}/.venv/bin/python" -m pip install --require-hashes -r "${APP_DIR}/${HASH_LOCK}"
"${APP_DIR}/.venv/bin/python" -m pip check
PYTHONPATH="${APP_DIR}" ALPHA_DISABLE_DOTENV=1 "${APP_DIR}/.venv/bin/python" - <<'PY'
from polymarket_scanner.weather_only_live_paper_all_signals_final_v7 import (
    assert_attested_all_paper_configuration,
)
assert_attested_all_paper_configuration()
print("Final all-PAPER configuration/import guard passed.")
PY
PYTHONPATH="${APP_DIR}" "${APP_DIR}/.venv/bin/python" - "${APP_DIR}/requirements.txt" <<'PY'
from importlib.metadata import version
from pathlib import Path
import sys
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    row = raw.split("#", 1)[0].strip()
    if not row or "==" not in row:
        continue
    name, expected = row.split("==", 1)
    installed = version(name.split("[", 1)[0])
    if installed != expected:
        raise SystemExit(f"dependency pin mismatch: {name} {installed} != {expected}")
print("All-PAPER runtime dependency pins match exactly.")
PY

mkdir -p "${CONFIG_DIR}"
umask 077
TMP_MARKER="$(mktemp "${CONFIG_DIR}/.weather-paper-release.XXXXXX")"
printf '%s\n' "${ACTUAL_SHA}" > "${TMP_MARKER}"
chmod 600 "${TMP_MARKER}"
mv -f "${TMP_MARKER}" "${RELEASE_FILE}"
bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"

PREPARE_MUTATED=0
trap - EXIT
printf '\nFinal operator-synchronized all-PAPER candidate prepared but NOT started or enabled.\n'
printf 'Release: %s\n' "${ACTUAL_SHA}"
printf 'Source ref: %s\n' "${SOURCE_REF}"
printf 'Exact rollback release preserved: %s\n' "${PREVIOUS_SHA}"
