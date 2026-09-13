#!/usr/bin/env bash
set -Eeuo pipefail

# Prepare an isolated weather-paper checkout at one explicitly approved immutable SHA.
# This command never starts/enables a service and never changes the legacy scanner
# checkout or legacy release.sha marker.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
REPOSITORY_URL="${ALPHA_WEATHER_REPOSITORY_URL:-https://github.com/heroo2123/Alpha.git}"
SOURCE_REF="${ALPHA_WEATHER_SOURCE_REF:-${2:-weather-live-paper-corrective-2026-09-13}}"
RELEASE_SHA="${1:-}"
UNIT="polymarket-weather-paper.service"
FINAL_MODULE="polymarket_scanner.weather_only_live_paper_final"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ "${RELEASE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] \
  || fail "usage: $0 <exact-release-sha> [source-branch]"
git check-ref-format --branch "${SOURCE_REF}" >/dev/null 2>&1 \
  || fail "invalid source branch/ref"

if systemctl is-active --quiet "${UNIT}" 2>/dev/null; then
  fail "${UNIT} is active; stop it explicitly before preparing another candidate"
fi
if pgrep -af 'polymarket_scanner\.weather_only_live_paper|weather_only_live_paper(_v[234]|_corrective|_final)?\.py' >/dev/null 2>&1; then
  fail "a weather-paper process is already running outside the stopped service"
fi

if [[ ! -d "${APP_DIR}/.git" ]]; then
  [[ ! -e "${APP_DIR}" ]] || fail "weather app path exists but is not a git checkout: ${APP_DIR}"
  mkdir -p "$(dirname "${APP_DIR}")"
  git clone --no-checkout "${REPOSITORY_URL}" "${APP_DIR}"
fi

[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] \
  || fail "weather-paper checkout differs from its authorized commit"

git -C "${APP_DIR}" remote get-url origin >/dev/null 2>&1 \
  || fail "weather-paper checkout has no origin remote"
git -C "${APP_DIR}" fetch --prune origin "${SOURCE_REF}"
git -C "${APP_DIR}" cat-file -e "${RELEASE_SHA}^{commit}" 2>/dev/null \
  || fail "requested weather-paper commit is not present after fetch"
git -C "${APP_DIR}" merge-base --is-ancestor "${RELEASE_SHA}" FETCH_HEAD \
  || fail "requested commit is not part of the explicitly selected source branch"
git -C "${APP_DIR}" checkout --detach "${RELEASE_SHA}"
ACTUAL_SHA="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
[[ "${ACTUAL_SHA}" == "${RELEASE_SHA,,}" ]] || fail "detached checkout did not land on requested SHA"

for required in \
  deploy/verify-runtime-release.sh \
  deploy/render-weather-paper-unit.py \
  deploy/check-weather-paper-network.py \
  deploy/check-weather-paper-service-isolation.sh \
  deploy/pre-release-weather-paper-backup.sh \
  deploy/setup-weather-paper-backup-service.sh \
  deploy/preflight-weather-paper-deployment.sh \
  deploy/start-weather-paper-candidate.sh \
  deploy/verify-weather-paper-first-cycle.py \
  deploy/enable-weather-paper-persistence.sh \
  polymarket_scanner/weather_only_live_paper_corrective.py \
  polymarket_scanner/weather_only_live_paper_final.py \
  polymarket_scanner/weather_only_paper_recovery.py \
  polymarket_scanner/weather_only_paper_recovery_final.py \
  polymarket_scanner/weather_only_runtime_attestation.py \
  polymarket_scanner/weather_only_deployment_acceptance.py \
  polymarket_scanner/weather_only_network_preflight.py \
  polymarket_scanner/weather_only_paper_backup.py
 do
  [[ -f "${APP_DIR}/${required}" ]] || fail "candidate lacks required weather-paper file: ${required}"
done

grep -qF "${FINAL_MODULE}" "${APP_DIR}/deploy/render-weather-paper-unit.py" \
  || fail "candidate renderer does not point to final guarded weather-paper entrypoint"
grep -qF 'weather-paper-release.sha' "${APP_DIR}/deploy/render-weather-paper-unit.py" \
  || fail "candidate does not use an isolated weather-paper release marker"

if [[ ! -x "${APP_DIR}/.venv/bin/python" ]]; then
  python3 -m venv "${APP_DIR}/.venv"
fi
"${APP_DIR}/.venv/bin/python" -m pip install -r "${APP_DIR}/requirements.txt"
"${APP_DIR}/.venv/bin/python" -m pip check

# Verify every pinned runtime requirement exactly, not merely satisfiable ranges.
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
print("Weather-paper runtime dependency pins match exactly.")
PY

# Import the exact deployable module before publishing the release marker. This is a
# no-start smoke test and catches missing internal files/imports before any service
# installation or live-paper process is attempted.
PYTHONPATH="${APP_DIR}" "${APP_DIR}/.venv/bin/python" -c \
  "import ${FINAL_MODULE}; print('Final weather-paper runtime import passed.')"

mkdir -p "${CONFIG_DIR}"
umask 077
TMP_MARKER="$(mktemp "${CONFIG_DIR}/.weather-paper-release.XXXXXX")"
trap 'rm -f "${TMP_MARKER}"' EXIT
printf '%s\n' "${ACTUAL_SHA}" > "${TMP_MARKER}"
chmod 600 "${TMP_MARKER}"
mv -f "${TMP_MARKER}" "${RELEASE_FILE}"
trap - EXIT

bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"
printf '\nWeather PAPER candidate prepared but NOT started or enabled.\n'
printf 'Isolated app: %s\n' "${APP_DIR}"
printf 'Release: %s\n' "${ACTUAL_SHA}"
printf 'Release marker: %s\n' "${RELEASE_FILE}"
printf 'Legacy scanner checkout/release marker were not changed.\n'
