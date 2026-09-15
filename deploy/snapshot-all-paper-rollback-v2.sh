#!/usr/bin/env bash
set -Eeuo pipefail

# Add an exact virtualenv generation to the existing verified source/unit/SQLite
# rollback snapshot. Run this candidate script (and its two companion files) from a
# temporary directory before stopping the known-good service.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
ROLLBACK_DIR="${CONFIG_DIR}/all-paper-rollback"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BASE_SNAPSHOT="${ALL_PAPER_BASE_SNAPSHOT_SCRIPT:-${SCRIPT_DIR}/snapshot-all-paper-rollback.sh}"
VENV_HELPER="${ALL_PAPER_VENV_HELPER:-${SCRIPT_DIR}/weather-paper-venv-snapshot.py}"
VENV_ARCHIVE="${ROLLBACK_DIR}/previous-venv.tar"
VENV_MANIFEST="${ROLLBACK_DIR}/previous-venv.json"
VENV_RELEASE="${ROLLBACK_DIR}/previous-venv-release.sha"
DB_SHA="${ROLLBACK_DIR}/previous-db.sha256"
GENERATION="${ROLLBACK_DIR}/snapshot-generation-v2"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ -f "${BASE_SNAPSHOT}" ]] || fail "missing candidate base rollback snapshot script"
[[ -f "${VENV_HELPER}" ]] || fail "missing candidate virtualenv snapshot helper"
[[ -d "${APP_DIR}/.git" ]] || fail "missing weather-paper checkout"
[[ -d "${APP_DIR}/.venv" && ! -L "${APP_DIR}/.venv" ]] || fail "current .venv root invalid"

mkdir -p "${ROLLBACK_DIR}"
chmod 700 "${ROLLBACK_DIR}"
# A failed refresh must never leave a previous generation marker authorizing a mixed
# old/new rollback set. The marker is republished only after every new component and
# the current venv tree have been verified.
rm -f "${GENERATION}"

# The mature snapshot records checkout/unit/service state and a verified online SQLite
# backup. It is read-only with respect to production.
bash "${BASE_SNAPSHOT}"

PREVIOUS_SHA="$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-release.sha")"
[[ "${PREVIOUS_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "base rollback SHA invalid"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')" == "${PREVIOUS_SHA}" ]] \
  || fail "checkout changed during rollback snapshot"

/usr/bin/python3 "${VENV_HELPER}" snapshot \
  --venv "${APP_DIR}/.venv" \
  --archive "${VENV_ARCHIVE}" \
  --manifest "${VENV_MANIFEST}"
/usr/bin/python3 "${VENV_HELPER}" verify-tree \
  --venv "${APP_DIR}/.venv" \
  --manifest "${VENV_MANIFEST}"

umask 077
TMP_RELEASE="$(mktemp "${ROLLBACK_DIR}/.previous-venv-release.XXXXXX")"
TMP_GENERATION="$(mktemp "${ROLLBACK_DIR}/.snapshot-generation-v2.XXXXXX")"
TMP_DB_SHA="$(mktemp "${ROLLBACK_DIR}/.previous-db-sha.XXXXXX")"
cleanup(){ rm -f "${TMP_RELEASE}" "${TMP_GENERATION}" "${TMP_DB_SHA}"; }
trap cleanup EXIT
printf '%s\n' "${PREVIOUS_SHA}" > "${TMP_RELEASE}"
printf '%s\n' 'all-paper-rollback-v2-exact-venv' > "${TMP_GENERATION}"
if [[ "$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-db-present")" == "1" ]]; then
  sha256sum "${ROLLBACK_DIR}/previous-weather-paper.sqlite3" | awk '{print $1}' > "${TMP_DB_SHA}"
else
  printf '%s\n' 'ABSENT' > "${TMP_DB_SHA}"
fi
chmod 600 "${TMP_RELEASE}" "${TMP_GENERATION}" "${TMP_DB_SHA}"
mv -f "${TMP_RELEASE}" "${VENV_RELEASE}"
mv -f "${TMP_DB_SHA}" "${DB_SHA}"
# Verify archive integrity once more immediately before publishing generation validity.
/usr/bin/python3 "${VENV_HELPER}" verify \
  --venv "${APP_DIR}/.venv" \
  --archive "${VENV_ARCHIVE}" \
  --manifest "${VENV_MANIFEST}"
/usr/bin/python3 "${VENV_HELPER}" verify-tree \
  --venv "${APP_DIR}/.venv" \
  --manifest "${VENV_MANIFEST}"
mv -f "${TMP_GENERATION}" "${GENERATION}"
trap - EXIT

printf 'PASS: rollback generation includes exact source, unit, ledger and current virtualenv evidence.\n'
printf 'Release: %s\n' "${PREVIOUS_SHA}"
