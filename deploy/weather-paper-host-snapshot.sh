#!/usr/bin/env bash
set -Eeuo pipefail

# Installed root-owned under /usr/local/libexec/polymarket-weather-paper/. This file
# is the independent rollback snapshot authority. Runtime paths come only from the
# root-owned host configuration installed before candidate cutover.
PATH=/usr/bin:/bin
export PATH
unset BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM || true
LIBEXEC="/usr/local/libexec/polymarket-weather-paper"
HOST_PATHS="/etc/polymarket-weather-paper/host-paths.conf"
GATE="${LIBEXEC}/release-gate.py"
VENV_HELPER="${LIBEXEC}/weather-paper-venv-snapshot.py"

fail(){ printf 'HOST SNAPSHOT ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${EUID}" == "0" ]] || fail "snapshot authority must run as root"
[[ -f "${HOST_PATHS}" && ! -L "${HOST_PATHS}" ]] || fail "root-owned host path configuration missing"
[[ "$(stat -c '%u' "${HOST_PATHS}")" == "0" ]] || fail "host path configuration is not root owned"
HOST_MODE="$(stat -c '%a' "${HOST_PATHS}")"
(( (8#${HOST_MODE} & 8#22) == 0 )) || fail "host path configuration writable by nonroot"
# shellcheck disable=SC1090
source "${HOST_PATHS}"
[[ "${APP_DIR:-}" == /* && "${CONFIG_DIR:-}" == /* && "${DB_PATH:-}" == /* && "${ROLLBACK_DIR:-}" == /* ]] || fail "pinned host paths invalid"
[[ "${UNIT:-}" == "polymarket-weather-paper.service" ]] || fail "pinned service identity invalid"
[[ "${DEPLOY_USER:-}" =~ ^[A-Za-z0-9_.-]+$ && "${DEPLOY_UID:-}" =~ ^[0-9]+$ && "${DEPLOY_GID:-}" =~ ^[0-9]+$ ]] || fail "pinned deploy identity invalid"

UNIT_FILE="/etc/systemd/system/${UNIT}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
GENERATION="${ROLLBACK_DIR}/snapshot-generation-v4"
MANIFEST="${ROLLBACK_DIR}/rollback-manifest-v4.json"

[[ -x /usr/bin/python3 && -f "${GATE}" && -f "${VENV_HELPER}" ]] || fail "host trust tools missing"
[[ -d "${APP_DIR}/.git" && -f "${RELEASE_FILE}" && -f "${UNIT_FILE}" ]] || fail "known-good release evidence missing"
[[ "$(stat -c '%u' "${ROLLBACK_DIR}")" == "0" ]] || fail "rollback custody directory is not root owned"
ROLLBACK_MODE="$(stat -c '%a' "${ROLLBACK_DIR}")"
(( (8#${ROLLBACK_MODE} & 8#22) == 0 )) || fail "rollback custody directory writable by nonroot"
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"

# Refuse to snapshot an unexpected privileged/unit identity. The exact unit bytes are
# additionally hash-bound below, but these semantic checks prevent blessing a unit
# that would restore as root or execute outside the approved application checkout.
grep -Fxq "User=${DEPLOY_USER}" "${UNIT_FILE}" || fail "known-good unit user mismatch"
grep -Fxq "WorkingDirectory=${APP_DIR}" "${UNIT_FILE}" || fail "known-good unit working directory mismatch"
grep -Eq "^ExecStart=${APP_DIR//\//\\/}/\.venv/bin/python -m polymarket_scanner\.weather_only_live_paper[A-Za-z0-9_.-]*( |$)" "${UNIT_FILE}" \
  || fail "known-good unit execution target is outside weather PAPER runtime"
! grep -Eq '^User=(root|0)$' "${UNIT_FILE}" || fail "known-good unit unexpectedly privileged"

SHA="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
TREE="$(git -C "${APP_DIR}" rev-parse HEAD^{tree} | tr -d '[:space:]')"
ACTIVE=0; ENABLED=0
systemctl is-active --quiet "${UNIT}" 2>/dev/null && ACTIVE=1 || true
systemctl is-enabled --quiet "${UNIT}" 2>/dev/null && ENABLED=1 || true

umask 027
rm -f "${GENERATION}" "${MANIFEST}"
rm -f "${ROLLBACK_DIR}"/previous-* "${ROLLBACK_DIR}/${UNIT}"

printf '%s\n' "${SHA}" > "${ROLLBACK_DIR}/previous-release.sha"
printf '%s\n' "${TREE}" > "${ROLLBACK_DIR}/previous-tree.sha"
printf '%s\n' "${ACTIVE}" > "${ROLLBACK_DIR}/previous-active"
printf '%s\n' "${ENABLED}" > "${ROLLBACK_DIR}/previous-enabled"
cat "${UNIT_FILE}" > "${ROLLBACK_DIR}/${UNIT}"

if [[ -f "${DB_PATH}" ]]; then
  /usr/bin/python3 - "${DB_PATH}" "${ROLLBACK_DIR}/previous-weather-paper.sqlite3" <<'PY'
import os, sqlite3, sys
from pathlib import Path
source = Path(sys.argv[1]).resolve(); target = Path(sys.argv[2]).resolve()
tmp = target.with_name('.' + target.name + f'.tmp-{os.getpid()}')
try:
    src = sqlite3.connect(f'file:{source}?mode=ro', uri=True, timeout=5.0)
    dst = sqlite3.connect(tmp, timeout=5.0)
    try:
        src.execute('PRAGMA busy_timeout=5000'); dst.execute('PRAGMA busy_timeout=5000')
        src.backup(dst, pages=256, sleep=0.01)
    finally:
        dst.close(); src.close()
    check = sqlite3.connect(f'file:{tmp}?mode=ro', uri=True, timeout=5.0)
    try:
        if check.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
            raise SystemExit('rollback DB quick_check failed')
    finally:
        check.close()
    os.replace(tmp, target)
finally:
    tmp.unlink(missing_ok=True)
PY
  printf '1\n' > "${ROLLBACK_DIR}/previous-db-present"
  sha256sum "${ROLLBACK_DIR}/previous-weather-paper.sqlite3" | awk '{print $1}' > "${ROLLBACK_DIR}/previous-db.sha256"
else
  rm -f "${ROLLBACK_DIR}/previous-weather-paper.sqlite3"
  printf '0\n' > "${ROLLBACK_DIR}/previous-db-present"
  printf 'ABSENT\n' > "${ROLLBACK_DIR}/previous-db.sha256"
fi

/usr/bin/python3 "${VENV_HELPER}" snapshot \
  --venv "${APP_DIR}/.venv" \
  --archive "${ROLLBACK_DIR}/previous-venv.tar" \
  --manifest "${ROLLBACK_DIR}/previous-venv.json"
/usr/bin/python3 "${VENV_HELPER}" verify \
  --venv "${APP_DIR}/.venv" \
  --archive "${ROLLBACK_DIR}/previous-venv.tar" \
  --manifest "${ROLLBACK_DIR}/previous-venv.json"
/usr/bin/python3 "${VENV_HELPER}" verify-tree \
  --venv "${APP_DIR}/.venv" --manifest "${ROLLBACK_DIR}/previous-venv.json"
printf '%s\n' "${SHA}" > "${ROLLBACK_DIR}/previous-venv-release.sha"

# Every rollback payload remains root-owned and only group-readable by the deploy
# user's pinned primary group. No payload or expected digest is writable by nonroot.
for path in "${ROLLBACK_DIR}"/previous-* "${ROLLBACK_DIR}/${UNIT}"; do
  [[ -e "${path}" ]] || continue
  chown root:"${DEPLOY_GID}" "${path}"
  chmod 0640 "${path}"
done

# Publish a root-owned aggregate manifest that binds all payload bytes. Recovery
# verifies this manifest before consuming any rollback artifact.
/usr/bin/python3 - "${ROLLBACK_DIR}" "${UNIT}" "${SHA}" "${TREE}" > "${MANIFEST}" <<'PY'
from __future__ import annotations
import hashlib, json, os, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve(); unit, release, tree = sys.argv[2:5]
names = [
    'previous-release.sha', 'previous-tree.sha', 'previous-active', 'previous-enabled',
    unit, 'previous-db-present', 'previous-db.sha256', 'previous-venv.tar',
    'previous-venv.json', 'previous-venv-release.sha',
]
if (root / 'previous-weather-paper.sqlite3').exists():
    names.append('previous-weather-paper.sqlite3')
def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()
entries = []
for name in names:
    path = root / name
    st = path.lstat()
    if not path.is_file() or path.is_symlink() or st.st_uid != 0 or (st.st_mode & 0o022):
        raise SystemExit(f'rollback artifact custody invalid: {name}')
    entries.append({'name': name, 'sha256': digest(path), 'size': st.st_size, 'mode': oct(st.st_mode & 0o777)})
print(json.dumps({
    'version': 'all-paper-rollback-v4-root-custody-hash-bound',
    'release_sha': release,
    'tree_sha': tree,
    'entries': entries,
}, sort_keys=True, indent=2))
PY
chown root:"${DEPLOY_GID}" "${MANIFEST}"
chmod 0440 "${MANIFEST}"

# Recheck release identity immediately before publishing the generation marker.
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD^{tree})" == "${TREE}" ]] || fail "checkout tree changed during snapshot"
printf '%s\n' 'all-paper-rollback-v4-root-custody-hash-bound' > "${GENERATION}"
chown root:"${DEPLOY_GID}" "${GENERATION}"
chmod 0440 "${GENERATION}"
printf 'PASS: root-custodied, hash-bound rollback snapshot captured for %s (%s).\n' "${SHA}" "${TREE}"
