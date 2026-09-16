#!/usr/bin/env bash
set -Eeuo pipefail

# Installed root-owned outside the candidate checkout. This is the only rollback path
# used by the final deployment scripts. Runtime paths come only from root-owned host
# configuration, never from candidate-controlled environment variables.
PATH=/usr/bin:/bin
export PATH
unset BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM || true
LIBEXEC="/usr/local/libexec/polymarket-weather-paper"
HOST_PATHS="/etc/polymarket-weather-paper/host-paths.conf"
GATE="${LIBEXEC}/release-gate.py"
VENV_HELPER="${LIBEXEC}/weather-paper-venv-snapshot.py"

fail(){ printf 'HOST ROLLBACK ERROR: %s\n' "$*" >&2; exit 1; }
[[ -f "${HOST_PATHS}" && ! -L "${HOST_PATHS}" ]] || fail "root-owned host path configuration missing"
[[ "$(stat -c '%u' "${HOST_PATHS}")" == "0" ]] || fail "host path configuration is not root owned"
HOST_MODE="$(stat -c '%a' "${HOST_PATHS}")"
(( (8#${HOST_MODE} & 8#22) == 0 )) || fail "host path configuration writable by nonroot"
# shellcheck disable=SC1090
source "${HOST_PATHS}"
[[ "${APP_DIR:-}" == /* && "${CONFIG_DIR:-}" == /* && "${DB_PATH:-}" == /* ]] || fail "pinned host paths invalid"
[[ "${UNIT:-}" == "polymarket-weather-paper.service" ]] || fail "pinned service identity invalid"

RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
ROLLBACK_DIR="${CONFIG_DIR}/all-paper-rollback"
GENERATION="${ROLLBACK_DIR}/snapshot-generation-v3"

sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true

for required in previous-release.sha previous-tree.sha "${UNIT}" previous-active previous-enabled \
  previous-db-present previous-db.sha256 previous-venv.tar previous-venv.json \
  previous-venv-release.sha snapshot-generation-v3; do
  [[ -f "${ROLLBACK_DIR}/${required}" ]] || fail "missing rollback artifact: ${required}"
done
[[ "$(tr -d '[:space:]' < "${GENERATION}")" == 'all-paper-rollback-v3-host-authority' ]] \
  || fail "rollback generation not host-authority v3"
[[ -f "${GATE}" && -f "${VENV_HELPER}" ]] || fail "host trust tools missing"

PREVIOUS_SHA="$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-release.sha")"
PREVIOUS_TREE="$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-tree.sha")"
PREVIOUS_ACTIVE="$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-active")"
PREVIOUS_ENABLED="$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-enabled")"
PREVIOUS_DB_PRESENT="$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-db-present")"
[[ "${PREVIOUS_SHA}" =~ ^[0-9a-f]{40}$ && "${PREVIOUS_TREE}" =~ ^[0-9a-f]{40}$ ]] || fail "rollback git identity invalid"
[[ "${PREVIOUS_ACTIVE}" =~ ^[01]$ && "${PREVIOUS_ENABLED}" =~ ^[01]$ && "${PREVIOUS_DB_PRESENT}" =~ ^[01]$ ]] || fail "rollback state invalid"
[[ "$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-venv-release.sha")" == "${PREVIOUS_SHA}" ]] || fail "venv release mismatch"
/usr/bin/python3 "${GATE}" verify-object --app-dir "${APP_DIR}" --sha "${PREVIOUS_SHA}"
[[ "$(git -C "${APP_DIR}" rev-parse "${PREVIOUS_SHA}^{tree}")" == "${PREVIOUS_TREE}" ]] || fail "approved rollback tree mismatch"
/usr/bin/python3 "${VENV_HELPER}" verify \
  --venv "${APP_DIR}/.venv" --archive "${ROLLBACK_DIR}/previous-venv.tar" \
  --manifest "${ROLLBACK_DIR}/previous-venv.json"

# Preserve failed-candidate DB evidence without making recovery depend on it.
if [[ -f "${DB_PATH}" ]]; then
  cp --reflink=auto --preserve=mode,timestamps "${DB_PATH}" \
    "${ROLLBACK_DIR}/failed-candidate-weather-paper-$(date -u +%Y%m%dT%H%M%SZ).sqlite3" 2>/dev/null || true
fi

mkdir -p "$(dirname "${DB_PATH}")"
if [[ "${PREVIOUS_DB_PRESENT}" == "1" ]]; then
  BACKUP_DB="${ROLLBACK_DIR}/previous-weather-paper.sqlite3"
  EXPECTED="$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-db.sha256")"
  [[ "${EXPECTED}" =~ ^[0-9a-f]{64}$ && -f "${BACKUP_DB}" ]] || fail "rollback DB evidence invalid"
  [[ "$(sha256sum "${BACKUP_DB}" | awk '{print $1}')" == "${EXPECTED}" ]] || fail "rollback DB digest mismatch"
  /usr/bin/python3 - "${BACKUP_DB}" "${DB_PATH}" <<'PY'
import os, sqlite3, sys
from pathlib import Path
source = Path(sys.argv[1]).resolve(); target = Path(sys.argv[2]).resolve()
check = sqlite3.connect(f'file:{source}?mode=ro', uri=True, timeout=5.0)
try:
    if check.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
        raise SystemExit('rollback source DB quick_check failed')
finally:
    check.close()
tmp = target.with_name('.' + target.name + f'.restore-{os.getpid()}')
try:
    src = sqlite3.connect(f'file:{source}?mode=ro', uri=True, timeout=5.0)
    dst = sqlite3.connect(tmp, timeout=5.0)
    try: src.backup(dst, pages=256, sleep=0.01)
    finally: dst.close(); src.close()
    verify = sqlite3.connect(f'file:{tmp}?mode=ro', uri=True, timeout=5.0)
    try:
        if verify.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise SystemExit('restored DB quick_check failed')
    finally: verify.close()
    Path(str(target)+'-wal').unlink(missing_ok=True); Path(str(target)+'-shm').unlink(missing_ok=True)
    os.replace(tmp, target); os.chmod(target, 0o600)
finally:
    tmp.unlink(missing_ok=True)
PY
else
  [[ "$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-db.sha256")" == 'ABSENT' ]] || fail "rollback absent DB marker invalid"
  rm -f "${DB_PATH}" "${DB_PATH}-wal" "${DB_PATH}-shm"
fi

git -C "${APP_DIR}" checkout --detach "${PREVIOUS_SHA}"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD)" == "${PREVIOUS_SHA}" ]] || fail "checkout restore failed"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD^{tree})" == "${PREVIOUS_TREE}" ]] || fail "restored checkout tree mismatch"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "restored checkout dirty"
mkdir -p "${CONFIG_DIR}"; umask 077
printf '%s\n' "${PREVIOUS_SHA}" > "${RELEASE_FILE}"; chmod 600 "${RELEASE_FILE}"

/usr/bin/python3 "${VENV_HELPER}" restore \
  --venv "${APP_DIR}/.venv" --archive "${ROLLBACK_DIR}/previous-venv.tar" \
  --manifest "${ROLLBACK_DIR}/previous-venv.json"
/usr/bin/python3 "${VENV_HELPER}" verify-tree \
  --venv "${APP_DIR}/.venv" --manifest "${ROLLBACK_DIR}/previous-venv.json"
"${APP_DIR}/.venv/bin/python" -m pip check

sudo install -m 0644 "${ROLLBACK_DIR}/${UNIT}" "/etc/systemd/system/${UNIT}"
sudo systemctl daemon-reload
if [[ "${PREVIOUS_ENABLED}" == "1" ]]; then sudo systemctl enable "${UNIT}" >/dev/null; else sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true; fi
if [[ "${PREVIOUS_ACTIVE}" == "1" ]]; then
  sudo systemctl start "${UNIT}"
  for _ in $(seq 1 20); do systemctl is-active --quiet "${UNIT}" 2>/dev/null && break; sleep 1; done
  systemctl is-active --quiet "${UNIT}" 2>/dev/null || fail "restored service failed to start"
fi
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
printf 'PASS: host-owned recovery restored approved rollback release %s.\n' "${PREVIOUS_SHA}"
