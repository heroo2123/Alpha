#!/usr/bin/env bash
set -Eeuo pipefail

# Prepare one immutable final all-weather PAPER candidate. Never start/enable it.
# A complete pre-cutover rollback snapshot (source, unit, service state and SQLite
# ledger) is mandatory before the known-good checkout can be replaced. Once checkout
# mutation begins, every preparation failure automatically restores that snapshot.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
SOURCE_REF="${ALPHA_WEATHER_SOURCE_REF:-${2:-weather-all-paper-independent-review-corrective-v2-2026-09-15}}"
RELEASE_SHA="${1:-}"
UNIT="polymarket-weather-paper.service"
FINAL_MODULE="polymarket_scanner.weather_only_live_paper_all_signals_final_v2"
ROLLBACK_DIR="${CONFIG_DIR}/all-paper-rollback"
ROLLBACK_SHA="${ROLLBACK_DIR}/previous-release.sha"
ROLLBACK_UNIT="${ROLLBACK_DIR}/${UNIT}"
ROLLBACK_ACTIVE="${ROLLBACK_DIR}/previous-active"
ROLLBACK_ENABLED="${ROLLBACK_DIR}/previous-enabled"
ROLLBACK_DB_PRESENT="${ROLLBACK_DIR}/previous-db-present"
ROLLBACK_DB="${ROLLBACK_DIR}/previous-weather-paper.sqlite3"
ROLLBACK_DB_MANIFEST="${ROLLBACK_DIR}/previous-weather-paper.sqlite3.json"
HASH_LOCK="requirements-runtime-hashed.txt"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${RELEASE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] || fail "usage: $0 <exact-release-sha> [source-branch]"
git check-ref-format --branch "${SOURCE_REF}" >/dev/null 2>&1 || fail "invalid source branch/ref"
[[ -d "${APP_DIR}/.git" ]] || fail "existing weather-paper checkout required for rollback-safe cutover"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then
  fail "${UNIT} is active; snapshot it first, then stop it explicitly before candidate preparation"
fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then
  fail "${UNIT} is enabled; snapshot it first, then disable it explicitly before candidate preparation"
fi
if pgrep -af 'polymarket_scanner\.weather_only_live_paper|weather_only_live_paper.*\.py' >/dev/null 2>&1; then
  fail "a weather-paper process is already running outside the stopped service"
fi

# The snapshot must have been captured before production was stopped/disabled.
for required_snapshot in \
  "${ROLLBACK_SHA}" "${ROLLBACK_UNIT}" "${ROLLBACK_ACTIVE}" \
  "${ROLLBACK_ENABLED}" "${ROLLBACK_DB_PRESENT}"
do
  [[ -f "${required_snapshot}" ]] || fail "missing pre-cutover rollback snapshot: ${required_snapshot}"
done
PREVIOUS_SHA="$(tr -d '[:space:]' < "${ROLLBACK_SHA}")"
PREVIOUS_ACTIVE="$(tr -d '[:space:]' < "${ROLLBACK_ACTIVE}")"
PREVIOUS_ENABLED="$(tr -d '[:space:]' < "${ROLLBACK_ENABLED}")"
PREVIOUS_DB_PRESENT="$(tr -d '[:space:]' < "${ROLLBACK_DB_PRESENT}")"
[[ "${PREVIOUS_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "rollback snapshot SHA invalid"
[[ "${PREVIOUS_ACTIVE}" =~ ^[01]$ ]] || fail "rollback active-state snapshot invalid"
[[ "${PREVIOUS_ENABLED}" =~ ^[01]$ ]] || fail "rollback enabled-state snapshot invalid"
[[ "${PREVIOUS_DB_PRESENT}" =~ ^[01]$ ]] || fail "rollback database-state snapshot invalid"
if [[ "${PREVIOUS_DB_PRESENT}" == "1" ]]; then
  [[ -f "${ROLLBACK_DB}" && -f "${ROLLBACK_DB_MANIFEST}" ]] \
    || fail "pre-cutover rollback database snapshot is incomplete"
fi
CURRENT_HEAD="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
CURRENT_MARKER="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${CURRENT_HEAD}" == "${PREVIOUS_SHA}" ]] || fail "rollback snapshot does not match current checkout"
[[ "${CURRENT_MARKER}" == "${PREVIOUS_SHA}" ]] || fail "rollback snapshot does not match current release marker"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "weather-paper checkout differs from its authorized commit"

PREPARE_MUTATED=0
rollback_prepare_on_error(){
  code=$?
  if (( code != 0 && PREPARE_MUTATED == 1 )); then
    printf 'Candidate preparation failed after checkout mutation; restoring full pre-cutover release and ledger...\n' >&2
    if [[ -f "${APP_DIR}/deploy/restore-all-paper-rollback.sh" ]]; then
      if ! bash "${APP_DIR}/deploy/restore-all-paper-rollback.sh"; then
        printf 'PREPARATION ROLLBACK FAILED: candidate remains contained; previous runtime requires manual recovery.\n' >&2
        sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
        sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true
      fi
    else
      printf 'PREPARATION ROLLBACK FAILED: restore script unavailable after checkout mutation.\n' >&2
      sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
      sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true
    fi
  fi
  exit "${code}"
}
trap rollback_prepare_on_error EXIT

git -C "${APP_DIR}" remote get-url origin >/dev/null 2>&1 || fail "weather-paper checkout has no origin remote"
git -C "${APP_DIR}" fetch --prune origin "${SOURCE_REF}"
git -C "${APP_DIR}" cat-file -e "${RELEASE_SHA}^{commit}" 2>/dev/null || fail "requested commit is not present after fetch"
git -C "${APP_DIR}" merge-base --is-ancestor "${RELEASE_SHA}" FETCH_HEAD || fail "requested commit is not part of the explicitly selected source branch"
git -C "${APP_DIR}" checkout --detach "${RELEASE_SHA}"
PREPARE_MUTATED=1
ACTUAL_SHA="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
[[ "${ACTUAL_SHA}" == "${RELEASE_SHA,,}" ]] || fail "detached checkout did not land on requested SHA"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "prepared checkout is not clean"

for required in \
  deploy/verify-runtime-release.sh \
  deploy/render-all-paper-unit.py \
  deploy/attest-all-paper-runtime.py \
  deploy/verify-all-paper-first-cycle.py \
  deploy/check-weather-paper-network.py \
  deploy/check-weather-paper-service-isolation.sh \
  deploy/pre-release-weather-paper-backup.sh \
  deploy/setup-all-paper-service.sh \
  deploy/preflight-all-paper-deployment.sh \
  deploy/start-all-paper-candidate.sh \
  deploy/enable-all-paper-persistence.sh \
  deploy/snapshot-all-paper-rollback.sh \
  deploy/restore-all-paper-rollback.sh \
  deploy/verify-three-layer-validation-status.py \
  deploy/verify-three-layer-fresh-capture.py \
  polymarket_scanner/weather_only_live_paper_all_signals_final_v2.py \
  polymarket_scanner/weather_only_live_paper_all_signals_final.py \
  polymarket_scanner/weather_only_live_paper_all_signals_v8.py \
  polymarket_scanner/weather_only_live_paper_all_signals_v7.py \
  polymarket_scanner/weather_only_independent_review_corrective_v2.py \
  polymarket_scanner/weather_only_independent_review_corrective.py \
  polymarket_scanner/weather_only_paper_post_receipt.py \
  polymarket_scanner/weather_only_maker_paper_accounting_v5.py \
  polymarket_scanner/weather_only_all_paper_deployment_acceptance.py \
  "${HASH_LOCK}"
 do
  [[ -f "${APP_DIR}/${required}" ]] || fail "candidate lacks required all-PAPER file: ${required}"
done

grep -qF "${FINAL_MODULE}" "${APP_DIR}/deploy/render-all-paper-unit.py" || fail "all-PAPER renderer does not point to final entrypoint"
grep -qF 'weather-paper-release.sha' "${APP_DIR}/deploy/render-all-paper-unit.py" || fail "all-PAPER candidate does not use isolated release marker"

if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  python3 -m venv "${APP_DIR}/.venv"
fi
"${APP_DIR}/.venv/bin/python" -m pip install --require-hashes -r "${APP_DIR}/${HASH_LOCK}"
"${APP_DIR}/.venv/bin/python" -m pip check
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
PYTHONPATH="${APP_DIR}" "${APP_DIR}/.venv/bin/python" -c "import ${FINAL_MODULE}; print('Final all-PAPER runtime import passed.')"

mkdir -p "${CONFIG_DIR}"
umask 077
TMP_MARKER="$(mktemp "${CONFIG_DIR}/.weather-paper-release.XXXXXX")"
printf '%s\n' "${ACTUAL_SHA}" > "${TMP_MARKER}"
chmod 600 "${TMP_MARKER}"
mv -f "${TMP_MARKER}" "${RELEASE_FILE}"
bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"

# Preparation completed successfully. Keep the candidate stopped/disabled and leave
# acceptance/cutover as a separate explicit operation.
PREPARE_MUTATED=0
trap - EXIT
printf '\nFinal all-PAPER candidate prepared but NOT started or enabled.\n'
printf 'Release: %s\n' "${ACTUAL_SHA}"
printf 'Source ref: %s\n' "${SOURCE_REF}"
printf 'Rollback release preserved: %s (was active=%s enabled=%s db=%s)\n' \
  "${PREVIOUS_SHA}" "${PREVIOUS_ACTIVE}" "${PREVIOUS_ENABLED}" "${PREVIOUS_DB_PRESENT}"
