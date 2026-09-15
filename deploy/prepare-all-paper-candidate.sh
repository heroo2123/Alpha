#!/usr/bin/env bash
set -Eeuo pipefail

# Prepare one immutable final all-weather PAPER candidate. Never start/enable it.
# A pre-cutover rollback snapshot is mandatory before the known-good checkout can be
# replaced, so failed live acceptance can restore the previous service automatically.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
REPOSITORY_URL="${ALPHA_WEATHER_REPOSITORY_URL:-https://github.com/heroo2123/Alpha.git}"
SOURCE_REF="${ALPHA_WEATHER_SOURCE_REF:-${2:-weather-all-paper-corrective-v7-2026-09-15}}"
RELEASE_SHA="${1:-}"
UNIT="polymarket-weather-paper.service"
FINAL_MODULE="polymarket_scanner.weather_only_live_paper_all_signals_final"
ROLLBACK_DIR="${CONFIG_DIR}/all-paper-rollback"
ROLLBACK_SHA="${ROLLBACK_DIR}/previous-release.sha"
ROLLBACK_UNIT="${ROLLBACK_DIR}/${UNIT}"
ROLLBACK_ACTIVE="${ROLLBACK_DIR}/previous-active"
ROLLBACK_ENABLED="${ROLLBACK_DIR}/previous-enabled"

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
for required_snapshot in "${ROLLBACK_SHA}" "${ROLLBACK_UNIT}" "${ROLLBACK_ACTIVE}" "${ROLLBACK_ENABLED}"; do
  [[ -f "${required_snapshot}" ]] || fail "missing pre-cutover rollback snapshot: ${required_snapshot}"
done
PREVIOUS_SHA="$(tr -d '[:space:]' < "${ROLLBACK_SHA}")"
PREVIOUS_ACTIVE="$(tr -d '[:space:]' < "${ROLLBACK_ACTIVE}")"
PREVIOUS_ENABLED="$(tr -d '[:space:]' < "${ROLLBACK_ENABLED}")"
[[ "${PREVIOUS_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "rollback snapshot SHA invalid"
[[ "${PREVIOUS_ACTIVE}" =~ ^[01]$ ]] || fail "rollback active-state snapshot invalid"
[[ "${PREVIOUS_ENABLED}" =~ ^[01]$ ]] || fail "rollback enabled-state snapshot invalid"
CURRENT_HEAD="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
CURRENT_MARKER="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${CURRENT_HEAD}" == "${PREVIOUS_SHA}" ]] || fail "rollback snapshot does not match current checkout"
[[ "${CURRENT_MARKER}" == "${PREVIOUS_SHA}" ]] || fail "rollback snapshot does not match current release marker"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "weather-paper checkout differs from its authorized commit"

git -C "${APP_DIR}" remote get-url origin >/dev/null 2>&1 || fail "weather-paper checkout has no origin remote"
git -C "${APP_DIR}" fetch --prune origin "${SOURCE_REF}"
git -C "${APP_DIR}" cat-file -e "${RELEASE_SHA}^{commit}" 2>/dev/null || fail "requested commit is not present after fetch"
git -C "${APP_DIR}" merge-base --is-ancestor "${RELEASE_SHA}" FETCH_HEAD || fail "requested commit is not part of the explicitly selected source branch"
git -C "${APP_DIR}" checkout --detach "${RELEASE_SHA}"
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
  polymarket_scanner/weather_only_live_paper_all_signals_final.py \
  polymarket_scanner/weather_only_live_paper_all_signals_v8.py \
  polymarket_scanner/weather_only_live_paper_all_signals_v7.py \
  polymarket_scanner/weather_only_independent_review_corrective.py \
  polymarket_scanner/weather_only_paper_post_receipt.py \
  polymarket_scanner/weather_only_maker_paper_accounting_v5.py \
  polymarket_scanner/weather_only_all_paper_deployment_acceptance.py
 do
  [[ -f "${APP_DIR}/${required}" ]] || fail "candidate lacks required all-PAPER file: ${required}"
done

grep -qF "${FINAL_MODULE}" "${APP_DIR}/deploy/render-all-paper-unit.py" || fail "all-PAPER renderer does not point to final entrypoint"
grep -qF 'weather-paper-release.sha' "${APP_DIR}/deploy/render-all-paper-unit.py" || fail "all-PAPER candidate does not use isolated release marker"

if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  python3 -m venv "${APP_DIR}/.venv"
fi
"${APP_DIR}/.venv/bin/python" -m pip install -r "${APP_DIR}/requirements.txt"
"${APP_DIR}/.venv/bin/python" -m pip check
PYTHONPATH="${APP_DIR}" "${APP_DIR}/.venv/bin/python" - "${APP_DIR}/requirements.txt" <<'PY'
from importlib.metadata import version
from pathlib import Path
import sys
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    row = raw.strip()
    if not row or row.startswith("#") or "==" not in row:
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
trap 'rm -f "${TMP_MARKER}"' EXIT
printf '%s\n' "${ACTUAL_SHA}" > "${TMP_MARKER}"
chmod 600 "${TMP_MARKER}"
mv -f "${TMP_MARKER}" "${RELEASE_FILE}"
trap - EXIT
bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"

printf '\nFinal all-PAPER candidate prepared but NOT started or enabled.\n'
printf 'Release: %s\n' "${ACTUAL_SHA}"
printf 'Source ref: %s\n' "${SOURCE_REF}"
printf 'Rollback release preserved: %s (was active=%s enabled=%s)\n' "${PREVIOUS_SHA}" "${PREVIOUS_ACTIVE}" "${PREVIOUS_ENABLED}"
