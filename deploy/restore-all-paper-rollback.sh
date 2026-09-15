#!/usr/bin/env bash
set -Eeuo pipefail

# Restore the exact pre-cutover weather PAPER checkout/unit/state captured by
# snapshot-all-paper-rollback.sh.  This is intended only for failed candidate
# acceptance; if any identity evidence is missing, leave the candidate stopped.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
UNIT="polymarket-weather-paper.service"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
ROLLBACK_DIR="${CONFIG_DIR}/all-paper-rollback"
ROLLBACK_SHA="${ROLLBACK_DIR}/previous-release.sha"
ROLLBACK_UNIT="${ROLLBACK_DIR}/${UNIT}"
ROLLBACK_ACTIVE="${ROLLBACK_DIR}/previous-active"
ROLLBACK_ENABLED="${ROLLBACK_DIR}/previous-enabled"
ATTESTATION_OUT="${CONFIG_DIR}/rollback-restored-weather-paper-attestation.json"

fail(){ printf 'ROLLBACK ERROR: %s\n' "$*" >&2; exit 1; }

# First contain the failed candidate regardless of whether restoration succeeds.
sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true

[[ -d "${APP_DIR}/.git" ]] || fail "missing weather-paper checkout"
[[ -f "${ROLLBACK_SHA}" ]] || fail "missing rollback release snapshot"
[[ -f "${ROLLBACK_UNIT}" ]] || fail "missing rollback unit snapshot"
[[ -f "${ROLLBACK_ACTIVE}" ]] || fail "missing rollback active-state snapshot"
[[ -f "${ROLLBACK_ENABLED}" ]] || fail "missing rollback enabled-state snapshot"

PREVIOUS_SHA="$(tr -d '[:space:]' < "${ROLLBACK_SHA}")"
PREVIOUS_ACTIVE="$(tr -d '[:space:]' < "${ROLLBACK_ACTIVE}")"
PREVIOUS_ENABLED="$(tr -d '[:space:]' < "${ROLLBACK_ENABLED}")"
[[ "${PREVIOUS_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "rollback SHA invalid"
[[ "${PREVIOUS_ACTIVE}" =~ ^[01]$ ]] || fail "rollback active state invalid"
[[ "${PREVIOUS_ENABLED}" =~ ^[01]$ ]] || fail "rollback enabled state invalid"
git -C "${APP_DIR}" cat-file -e "${PREVIOUS_SHA}^{commit}" 2>/dev/null \
  || fail "rollback commit is no longer present locally"

# Restore immutable source identity and release marker before reinstalling the old unit.
git -C "${APP_DIR}" checkout --detach "${PREVIOUS_SHA}"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')" == "${PREVIOUS_SHA}" ]] \
  || fail "failed to restore rollback checkout"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] \
  || fail "rollback checkout is dirty"
mkdir -p "${CONFIG_DIR}"
umask 077
TMP_MARKER="$(mktemp "${CONFIG_DIR}/.weather-paper-release.rollback.XXXXXX")"
printf '%s\n' "${PREVIOUS_SHA}" > "${TMP_MARKER}"
chmod 600 "${TMP_MARKER}"
mv -f "${TMP_MARKER}" "${RELEASE_FILE}"

# Candidate preparation may have changed the shared virtualenv. Reinstall the exact
# dependency pins from the restored release before allowing the old service to run.
[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "rollback virtualenv missing"
"${APP_DIR}/.venv/bin/python" -m pip install -r "${APP_DIR}/requirements.txt"
"${APP_DIR}/.venv/bin/python" -m pip check

sudo install -m 0644 "${ROLLBACK_UNIT}" "/etc/systemd/system/${UNIT}"
sudo systemctl daemon-reload

if [[ "${PREVIOUS_ENABLED}" == "1" ]]; then
  sudo systemctl enable "${UNIT}" >/dev/null
else
  sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true
fi
if [[ "${PREVIOUS_ACTIVE}" == "1" ]]; then
  sudo systemctl start "${UNIT}"
  for _ in $(seq 1 20); do
    systemctl is-active --quiet "${UNIT}" 2>/dev/null && break
    sleep 1
  done
  systemctl is-active --quiet "${UNIT}" 2>/dev/null \
    || fail "restored weather PAPER service did not become active"
else
  sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
fi

[[ "$(tr -d '[:space:]' < "${RELEASE_FILE}")" == "${PREVIOUS_SHA}" ]] \
  || fail "rollback release marker mismatch"
if [[ "${PREVIOUS_ENABLED}" == "1" ]]; then
  systemctl is-enabled --quiet "${UNIT}" 2>/dev/null || fail "rollback enable state not restored"
else
  ! systemctl is-enabled --quiet "${UNIT}" 2>/dev/null || fail "rollback disable state not restored"
fi

if [[ "${PREVIOUS_ACTIVE}" == "1" && -f "${APP_DIR}/deploy/attest-weather-paper-runtime.py" ]]; then
  "${APP_DIR}/.venv/bin/python" "${APP_DIR}/deploy/attest-weather-paper-runtime.py" \
    --app-dir "${APP_DIR}" \
    --release-file "${RELEASE_FILE}" \
    --db "${DB_PATH}" \
    --require-active \
    --output "${ATTESTATION_OUT}"
fi

printf 'PASS: previous weather PAPER release restored after failed candidate acceptance.\n'
printf 'Release: %s\n' "${PREVIOUS_SHA}"
printf 'Active restored: %s | enabled restored: %s\n' "${PREVIOUS_ACTIVE}" "${PREVIOUS_ENABLED}"
