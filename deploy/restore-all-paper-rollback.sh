#!/usr/bin/env bash
set -Eeuo pipefail

# Restore the exact pre-cutover weather PAPER checkout/unit/ledger/state captured by
# snapshot-all-paper-rollback.sh. If any identity evidence is missing, leave the
# candidate contained rather than guessing.
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
ROLLBACK_DB_PRESENT="${ROLLBACK_DIR}/previous-db-present"
ROLLBACK_DB="${ROLLBACK_DIR}/previous-weather-paper.sqlite3"
ROLLBACK_DB_MANIFEST="${ROLLBACK_DIR}/previous-weather-paper.sqlite3.json"
ATTESTATION_OUT="${CONFIG_DIR}/rollback-restored-weather-paper-attestation.json"

fail(){ printf 'ROLLBACK ERROR: %s\n' "$*" >&2; exit 1; }

# First contain the failed candidate regardless of whether restoration succeeds.
sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true

[[ -d "${APP_DIR}/.git" ]] || fail "missing weather-paper checkout"
[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "rollback virtualenv missing"
for required in "${ROLLBACK_SHA}" "${ROLLBACK_UNIT}" "${ROLLBACK_ACTIVE}" "${ROLLBACK_ENABLED}" "${ROLLBACK_DB_PRESENT}"; do
  [[ -f "${required}" ]] || fail "missing rollback snapshot: ${required}"
done

PREVIOUS_SHA="$(tr -d '[:space:]' < "${ROLLBACK_SHA}")"
PREVIOUS_ACTIVE="$(tr -d '[:space:]' < "${ROLLBACK_ACTIVE}")"
PREVIOUS_ENABLED="$(tr -d '[:space:]' < "${ROLLBACK_ENABLED}")"
PREVIOUS_DB_PRESENT="$(tr -d '[:space:]' < "${ROLLBACK_DB_PRESENT}")"
[[ "${PREVIOUS_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "rollback SHA invalid"
[[ "${PREVIOUS_ACTIVE}" =~ ^[01]$ ]] || fail "rollback active state invalid"
[[ "${PREVIOUS_ENABLED}" =~ ^[01]$ ]] || fail "rollback enabled state invalid"
[[ "${PREVIOUS_DB_PRESENT}" =~ ^[01]$ ]] || fail "rollback DB state invalid"
if [[ "${PREVIOUS_DB_PRESENT}" == "1" ]]; then
  [[ -f "${ROLLBACK_DB}" && -f "${ROLLBACK_DB_MANIFEST}" ]] \
    || fail "rollback database evidence missing"
fi
git -C "${APP_DIR}" cat-file -e "${PREVIOUS_SHA}^{commit}" 2>/dev/null \
  || fail "rollback commit is no longer present locally"

# Preserve a best-effort forensic copy of candidate-mutated state before replacing it.
if [[ -f "${DB_PATH}" ]]; then
  FORENSIC_DB="${ROLLBACK_DIR}/failed-candidate-weather-paper-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
  PYTHONPATH="${APP_DIR}${PYTHONPATH:+:${PYTHONPATH}}" \
  "${APP_DIR}/.venv/bin/python" - "${DB_PATH}" "${FORENSIC_DB}" <<'PY' || true
import os, sqlite3, sys
from pathlib import Path
source, destination = map(lambda x: Path(x).resolve(), sys.argv[1:])
tmp = destination.with_name("." + destination.name + f".tmp-{os.getpid()}")
try:
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=5.0)
    dst = sqlite3.connect(tmp, timeout=5.0)
    try:
        src.backup(dst, pages=256, sleep=0.01)
    finally:
        dst.close(); src.close()
    os.replace(tmp, destination)
    os.chmod(destination, 0o600)
finally:
    tmp.unlink(missing_ok=True)
PY
fi

# Restore database state BEFORE allowing the previous process to restart. SQLite WAL
# and SHM files from the failed candidate must never accompany the restored main DB.
mkdir -p "$(dirname "${DB_PATH}")"
if [[ "${PREVIOUS_DB_PRESENT}" == "1" ]]; then
  PYTHONPATH="${APP_DIR}${PYTHONPATH:+:${PYTHONPATH}}" \
  "${APP_DIR}/.venv/bin/python" - "${ROLLBACK_DB}" "${DB_PATH}" <<'PY'
import os, sqlite3, sys
from pathlib import Path
from polymarket_scanner.weather_only_paper_backup import (
    verify_weather_paper_database,
    verify_weather_paper_restore,
)
source = Path(sys.argv[1]).resolve()
target = Path(sys.argv[2]).resolve()
verified = verify_weather_paper_restore(source)
if verified.get("restore_verified") is not True:
    raise SystemExit("rollback source restore verification failed")
source_profile = verify_weather_paper_database(source)
tmp = target.with_name("." + target.name + f".rollback-{os.getpid()}")
try:
    tmp.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=5.0)
    dst = sqlite3.connect(tmp, timeout=5.0)
    try:
        src.backup(dst, pages=256, sleep=0.01)
    finally:
        dst.close(); src.close()
    copied = verify_weather_paper_database(tmp)
    if copied["schema_sha256"] != source_profile["schema_sha256"]:
        raise SystemExit("restored rollback DB schema mismatch")
    if copied["logical_tables"] != source_profile["logical_tables"]:
        raise SystemExit("restored rollback DB logical mismatch")
    Path(str(target) + "-wal").unlink(missing_ok=True)
    Path(str(target) + "-shm").unlink(missing_ok=True)
    os.replace(tmp, target)
    os.chmod(target, 0o600)
finally:
    tmp.unlink(missing_ok=True)
PY
else
  rm -f "${DB_PATH}" "${DB_PATH}-wal" "${DB_PATH}-shm"
fi

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
# dependency set required by the restored release before allowing the old service to run.
if [[ -f "${APP_DIR}/requirements-runtime-hashed.txt" ]]; then
  "${APP_DIR}/.venv/bin/python" -m pip install --require-hashes -r "${APP_DIR}/requirements-runtime-hashed.txt"
else
  "${APP_DIR}/.venv/bin/python" -m pip install -r "${APP_DIR}/requirements.txt"
fi
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

printf 'PASS: previous weather PAPER release and ledger restored after failed candidate acceptance.\n'
printf 'Release: %s\n' "${PREVIOUS_SHA}"
printf 'Active restored: %s | enabled restored: %s | DB restored: %s\n' \
  "${PREVIOUS_ACTIVE}" "${PREVIOUS_ENABLED}" "${PREVIOUS_DB_PRESENT}"
