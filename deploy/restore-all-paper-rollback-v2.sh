#!/usr/bin/env bash
set -Eeuo pipefail

# Restore the exact pre-cutover source/unit/ledger/service state AND virtualenv tree.
# This path deliberately uses /usr/bin/python3 for recovery so a candidate-mutated
# virtualenv is never trusted to restore itself.
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
ROLLBACK_DB_SHA="${ROLLBACK_DIR}/previous-db.sha256"
VENV_ARCHIVE="${ROLLBACK_DIR}/previous-venv.tar"
VENV_MANIFEST="${ROLLBACK_DIR}/previous-venv.json"
VENV_RELEASE="${ROLLBACK_DIR}/previous-venv-release.sha"
GENERATION="${ROLLBACK_DIR}/snapshot-generation-v2"
ATTESTATION_OUT="${CONFIG_DIR}/rollback-restored-weather-paper-attestation.json"

fail(){ printf 'ROLLBACK ERROR: %s\n' "$*" >&2; exit 1; }

sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true

[[ -d "${APP_DIR}/.git" ]] || fail "missing weather-paper checkout"
for required in \
  "${ROLLBACK_SHA}" "${ROLLBACK_UNIT}" "${ROLLBACK_ACTIVE}" "${ROLLBACK_ENABLED}" \
  "${ROLLBACK_DB_PRESENT}" "${ROLLBACK_DB_SHA}" "${VENV_ARCHIVE}" "${VENV_MANIFEST}" \
  "${VENV_RELEASE}" "${GENERATION}"
do
  [[ -f "${required}" ]] || fail "missing rollback snapshot: ${required}"
done
[[ "$(tr -d '[:space:]' < "${GENERATION}")" == 'all-paper-rollback-v2-exact-venv' ]] \
  || fail "rollback generation is not exact-venv v2"

PREVIOUS_SHA="$(tr -d '[:space:]' < "${ROLLBACK_SHA}")"
PREVIOUS_ACTIVE="$(tr -d '[:space:]' < "${ROLLBACK_ACTIVE}")"
PREVIOUS_ENABLED="$(tr -d '[:space:]' < "${ROLLBACK_ENABLED}")"
PREVIOUS_DB_PRESENT="$(tr -d '[:space:]' < "${ROLLBACK_DB_PRESENT}")"
[[ "${PREVIOUS_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "rollback SHA invalid"
[[ "$(tr -d '[:space:]' < "${VENV_RELEASE}")" == "${PREVIOUS_SHA}" ]] \
  || fail "virtualenv snapshot belongs to a different release"
[[ "${PREVIOUS_ACTIVE}" =~ ^[01]$ && "${PREVIOUS_ENABLED}" =~ ^[01]$ ]] \
  || fail "rollback service state invalid"
[[ "${PREVIOUS_DB_PRESENT}" =~ ^[01]$ ]] || fail "rollback DB state invalid"
git -C "${APP_DIR}" cat-file -e "${PREVIOUS_SHA}^{commit}" 2>/dev/null \
  || fail "rollback commit is no longer present locally"

# Preserve the candidate copy of the standalone venv verifier before checkout changes
# the repository back to the old release.
HELPER_SOURCE="${APP_DIR}/deploy/weather-paper-venv-snapshot.py"
[[ -f "${HELPER_SOURCE}" ]] || fail "candidate venv restore helper missing"
TMP_HELPER="$(mktemp /tmp/weather-paper-venv-restore.XXXXXX.py)"
cp "${HELPER_SOURCE}" "${TMP_HELPER}"
chmod 600 "${TMP_HELPER}"
trap 'rm -f "${TMP_HELPER}"' EXIT
/usr/bin/python3 "${TMP_HELPER}" verify \
  --venv "${APP_DIR}/.venv" --archive "${VENV_ARCHIVE}" --manifest "${VENV_MANIFEST}"

if [[ "${PREVIOUS_DB_PRESENT}" == "1" ]]; then
  [[ -f "${ROLLBACK_DB}" && -f "${ROLLBACK_DB_MANIFEST}" ]] \
    || fail "rollback database evidence missing"
  EXPECTED_DB_SHA="$(tr -d '[:space:]' < "${ROLLBACK_DB_SHA}")"
  [[ "${EXPECTED_DB_SHA}" =~ ^[0-9a-f]{64}$ ]] || fail "rollback DB SHA invalid"
  [[ "$(sha256sum "${ROLLBACK_DB}" | awk '{print $1}')" == "${EXPECTED_DB_SHA}" ]] \
    || fail "rollback DB SHA mismatch"
else
  [[ "$(tr -d '[:space:]' < "${ROLLBACK_DB_SHA}")" == 'ABSENT' ]] \
    || fail "rollback DB absent marker mismatch"
fi

# Best-effort forensic copy of the failed candidate ledger before restoring it.
if [[ -f "${DB_PATH}" ]]; then
  FORENSIC_DB="${ROLLBACK_DIR}/failed-candidate-weather-paper-$(date -u +%Y%m%dT%H%M%SZ).sqlite3"
  /usr/bin/python3 - "${DB_PATH}" "${FORENSIC_DB}" <<'PY' || true
import os, sqlite3, sys
from pathlib import Path
source, destination = map(lambda value: Path(value).resolve(), sys.argv[1:])
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

mkdir -p "$(dirname "${DB_PATH}")"
if [[ "${PREVIOUS_DB_PRESENT}" == "1" ]]; then
  /usr/bin/python3 - "${ROLLBACK_DB}" "${DB_PATH}" <<'PY'
import hashlib, os, sqlite3, sys
from pathlib import Path
source, target = map(lambda value: Path(value).resolve(), sys.argv[1:])
tmp = target.with_name("." + target.name + f".rollback-{os.getpid()}")
def logical_digest(path: Path) -> str:
    db = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=5.0)
    try:
        if db.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
            raise SystemExit("rollback database quick_check failed")
        h = hashlib.sha256()
        for line in db.iterdump():
            h.update(line.encode("utf-8")); h.update(b"\n")
        return h.hexdigest()
    finally:
        db.close()
try:
    source_digest = logical_digest(source)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=5.0)
    dst = sqlite3.connect(tmp, timeout=5.0)
    try:
        src.backup(dst, pages=256, sleep=0.01)
    finally:
        dst.close(); src.close()
    if logical_digest(tmp) != source_digest:
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

# Restore source/release identity while the service is still contained.
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

# Restore the exact snapshotted virtualenv instead of pip-reconstructing it.
/usr/bin/python3 "${TMP_HELPER}" restore \
  --venv "${APP_DIR}/.venv" --archive "${VENV_ARCHIVE}" --manifest "${VENV_MANIFEST}"
/usr/bin/python3 "${TMP_HELPER}" verify-tree \
  --venv "${APP_DIR}/.venv" --manifest "${VENV_MANIFEST}"
PYTHONDONTWRITEBYTECODE=1 "${APP_DIR}/.venv/bin/python" -m pip check

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
  PYTHONDONTWRITEBYTECODE=1 "${APP_DIR}/.venv/bin/python" \
    "${APP_DIR}/deploy/attest-weather-paper-runtime.py" \
    --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}" --db "${DB_PATH}" \
    --require-active --output "${ATTESTATION_OUT}"
fi

trap - EXIT
rm -f "${TMP_HELPER}"
printf 'PASS: exact pre-cutover release, ledger, service state and virtualenv restored.\n'
printf 'Release: %s\n' "${PREVIOUS_SHA}"
printf 'Active restored: %s | enabled restored: %s | DB restored: %s\n' \
  "${PREVIOUS_ACTIVE}" "${PREVIOUS_ENABLED}" "${PREVIOUS_DB_PRESENT}"
