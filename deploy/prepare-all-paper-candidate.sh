#!/usr/bin/env bash
set -Eeuo pipefail

# Prepare one immutable final all-weather PAPER candidate. Never start/enable it.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
REPOSITORY_URL="${ALPHA_WEATHER_REPOSITORY_URL:-https://github.com/heroo2123/Alpha.git}"
SOURCE_REF="${ALPHA_WEATHER_SOURCE_REF:-${2:-weather-all-paper-corrective-v7-2026-09-15}}"
RELEASE_SHA="${1:-}"
UNIT="polymarket-weather-paper.service"
FINAL_MODULE="polymarket_scanner.weather_only_live_paper_all_signals_final"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${RELEASE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] || fail "usage: $0 <exact-release-sha> [source-branch]"
git check-ref-format --branch "${SOURCE_REF}" >/dev/null 2>&1 || fail "invalid source branch/ref"
if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then
  fail "${UNIT} is active; stop it explicitly before preparing another candidate"
fi
if systemctl is-enabled --quiet "${UNIT}" 2>/dev/null; then
  fail "${UNIT} is enabled; disable it explicitly before preparing another candidate"
fi
if pgrep -af 'polymarket_scanner\.weather_only_live_paper|weather_only_live_paper.*\.py' >/dev/null 2>&1; then
  fail "a weather-paper process is already running outside the stopped service"
fi

FRESH_CLONE=0
if [[ ! -d "${APP_DIR}/.git" ]]; then
  [[ ! -e "${APP_DIR}" ]] || fail "weather app path exists but is not a git checkout: ${APP_DIR}"
  mkdir -p "$(dirname "${APP_DIR}")"
  git clone --no-checkout "${REPOSITORY_URL}" "${APP_DIR}"
  FRESH_CLONE=1
fi
if [[ "${FRESH_CLONE}" -eq 0 ]]; then
  [[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "weather-paper checkout differs from its authorized commit"
fi

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
  deploy/verify-three-layer-validation-status.py \
  deploy/verify-three-layer-fresh-capture.py \
  polymarket_scanner/weather_only_live_paper_all_signals_final.py \
  polymarket_scanner/weather_only_live_paper_all_signals_v8.py \
  polymarket_scanner/weather_only_live_paper_all_signals_v7.py \
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
