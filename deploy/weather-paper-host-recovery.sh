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
[[ "${APP_DIR:-}" == /* && "${CONFIG_DIR:-}" == /* && "${DB_PATH:-}" == /* && "${ROLLBACK_DIR:-}" == /* ]] || fail "pinned host paths invalid"
[[ "${UNIT:-}" == "polymarket-weather-paper.service" ]] || fail "pinned service identity invalid"
[[ "${DEPLOY_USER:-}" =~ ^[A-Za-z0-9_.-]+$ && "${DEPLOY_UID:-}" =~ ^[0-9]+$ && "${DEPLOY_GID:-}" =~ ^[0-9]+$ ]] || fail "pinned deploy identity invalid"
[[ "$(id -u)" == "${DEPLOY_UID}" && "$(id -g)" == "${DEPLOY_GID}" && "$(id -un)" == "${DEPLOY_USER}" ]] || fail "recovery invoked by unexpected deploy identity"

RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
GENERATION="${ROLLBACK_DIR}/snapshot-generation-v4"
ROLLBACK_MANIFEST="${ROLLBACK_DIR}/rollback-manifest-v4.json"

sudo systemctl stop "${UNIT}" >/dev/null 2>&1 || true
sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true

[[ -d "${ROLLBACK_DIR}" && ! -L "${ROLLBACK_DIR}" ]] || fail "rollback custody directory missing"
[[ "$(stat -c '%u' "${ROLLBACK_DIR}")" == "0" ]] || fail "rollback custody directory is not root owned"
ROLLBACK_MODE="$(stat -c '%a' "${ROLLBACK_DIR}")"
(( (8#${ROLLBACK_MODE} & 8#22) == 0 )) || fail "rollback custody directory writable by nonroot"
[[ -f "${GENERATION}" && ! -L "${GENERATION}" && -f "${ROLLBACK_MANIFEST}" && ! -L "${ROLLBACK_MANIFEST}" ]] \
  || fail "root-custody rollback generation/manifest missing"
[[ "$(stat -c '%u' "${GENERATION}")" == "0" && "$(stat -c '%u' "${ROLLBACK_MANIFEST}")" == "0" ]] \
  || fail "rollback generation/manifest not root owned"
for trust_file in "${GENERATION}" "${ROLLBACK_MANIFEST}"; do
  mode="$(stat -c '%a' "${trust_file}")"
  (( (8#${mode} & 8#22) == 0 )) || fail "rollback trust evidence writable by nonroot"
done
[[ "$(tr -d '[:space:]' < "${GENERATION}")" == 'all-paper-rollback-v4-root-custody-hash-bound' ]] \
  || fail "rollback generation not root-custody v4"
[[ -f "${GATE}" && -f "${VENV_HELPER}" ]] || fail "host trust tools missing"

# Validate the aggregate root-owned manifest before reading any rollback payload.
/usr/bin/python3 - "${ROLLBACK_DIR}" "${ROLLBACK_MANIFEST}" "${UNIT}" <<'PY'
from __future__ import annotations
import hashlib, json, stat, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve(); manifest_path = Path(sys.argv[2]).resolve(); unit = sys.argv[3]
try:
    payload = json.loads(manifest_path.read_text(encoding='utf-8'))
except Exception as exc:
    raise SystemExit(f'rollback manifest invalid: {exc}')
if payload.get('version') != 'all-paper-rollback-v4-root-custody-hash-bound':
    raise SystemExit('rollback manifest version invalid')
entries = payload.get('entries')
if not isinstance(entries, list) or not entries:
    raise SystemExit('rollback manifest entries invalid')
expected_base = {
    'previous-release.sha', 'previous-tree.sha', 'previous-active', 'previous-enabled', unit,
    'previous-db-present', 'previous-db.sha256', 'previous-venv.tar', 'previous-venv.json',
    'previous-venv-release.sha',
}
seen = set()
def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()
for row in entries:
    if not isinstance(row, dict):
        raise SystemExit('rollback manifest row invalid')
    name = str(row.get('name') or '')
    if not name or '/' in name or name in seen:
        raise SystemExit('rollback manifest path invalid')
    seen.add(name)
    path = root / name
    st = path.lstat()
    if not stat.S_ISREG(st.st_mode) or path.is_symlink() or st.st_uid != 0 or (st.st_mode & 0o022):
        raise SystemExit(f'rollback artifact custody invalid: {name}')
    if int(row.get('size', -1)) != st.st_size or str(row.get('sha256') or '') != digest(path):
        raise SystemExit(f'rollback artifact digest invalid: {name}')
if not expected_base.issubset(seen):
    raise SystemExit('rollback manifest missing required artifacts')
if 'previous-weather-paper.sqlite3' in seen and not (root / 'previous-weather-paper.sqlite3').is_file():
    raise SystemExit('rollback DB manifest mismatch')
print('PASS_ROOT_CUSTODY_ROLLBACK_MANIFEST')
PY

for required in previous-release.sha previous-tree.sha "${UNIT}" previous-active previous-enabled \
  previous-db-present previous-db.sha256 previous-venv.tar previous-venv.json previous-venv-release.sha; do
  [[ -f "${ROLLBACK_DIR}/${required}" && ! -L "${ROLLBACK_DIR}/${required}" ]] || fail "missing rollback artifact: ${required}"
done

PREVIOUS_SHA="$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-release.sha")"
PREVIOUS_TREE="$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-tree.sha")"
PREVIOUS_ACTIVE="$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-active")"
PREVIOUS_ENABLED="$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-enabled")"
PREVIOUS_DB_PRESENT="$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-db-present")"
[[ "${PREVIOUS_SHA}" =~ ^[0-9a-f]{40}$ && "${PREVIOUS_TREE}" =~ ^[0-9a-f]{40}$ ]] || fail "rollback git identity invalid"
[[ "${PREVIOUS_ACTIVE}" =~ ^[01]$ && "${PREVIOUS_ENABLED}" =~ ^[01]$ && "${PREVIOUS_DB_PRESENT}" =~ ^[01]$ ]] || fail "rollback state invalid"
[[ "$(tr -d '[:space:]' < "${ROLLBACK_DIR}/previous-venv-release.sha")" == "${PREVIOUS_SHA}" ]] || fail "venv release mismatch"
/usr/bin/python3 - "${ROLLBACK_MANIFEST}" "${PREVIOUS_SHA}" "${PREVIOUS_TREE}" <<'PY'
import json, sys
payload = json.load(open(sys.argv[1], encoding='utf-8'))
if payload.get('release_sha') != sys.argv[2] or payload.get('tree_sha') != sys.argv[3]:
    raise SystemExit('rollback manifest release/tree mismatch')
PY
/usr/bin/python3 "${GATE}" verify-object --app-dir "${APP_DIR}" --sha "${PREVIOUS_SHA}"
[[ "$(git -C "${APP_DIR}" rev-parse "${PREVIOUS_SHA}^{tree}")" == "${PREVIOUS_TREE}" ]] || fail "approved rollback tree mismatch"
/usr/bin/python3 "${VENV_HELPER}" verify \
  --venv "${APP_DIR}/.venv" --archive "${ROLLBACK_DIR}/previous-venv.tar" \
  --manifest "${ROLLBACK_DIR}/previous-venv.json"

# Re-validate the captured unit semantics as well as its manifest-bound bytes.
grep -Fxq "User=${DEPLOY_USER}" "${ROLLBACK_DIR}/${UNIT}" || fail "rollback unit user mismatch"
grep -Fxq "WorkingDirectory=${APP_DIR}" "${ROLLBACK_DIR}/${UNIT}" || fail "rollback unit working directory mismatch"
grep -Eq "^ExecStart=${APP_DIR//\//\\/}/\.venv/bin/python -m polymarket_scanner\.weather_only_live_paper[A-Za-z0-9_.-]*( |$)" "${ROLLBACK_DIR}/${UNIT}" \
  || fail "rollback unit execution target invalid"
! grep -Eq '^User=(root|0)$' "${ROLLBACK_DIR}/${UNIT}" || fail "rollback unit unexpectedly privileged"

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

sudo install -o root -g root -m 0644 "${ROLLBACK_DIR}/${UNIT}" "/etc/systemd/system/${UNIT}"
sudo systemctl daemon-reload
if [[ "${PREVIOUS_ENABLED}" == "1" ]]; then sudo systemctl enable "${UNIT}" >/dev/null; else sudo systemctl disable "${UNIT}" >/dev/null 2>&1 || true; fi
if [[ "${PREVIOUS_ACTIVE}" == "1" ]]; then
  sudo systemctl start "${UNIT}"
  for _ in $(seq 1 20); do systemctl is-active --quiet "${UNIT}" 2>/dev/null && break; sleep 1; done
  systemctl is-active --quiet "${UNIT}" 2>/dev/null || fail "restored service failed to start"
fi
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
printf 'PASS: root-custodied recovery restored approved rollback release %s.\n' "${PREVIOUS_SHA}"
