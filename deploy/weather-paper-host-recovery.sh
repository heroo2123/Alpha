#!/usr/bin/env bash
set -Eeuo pipefail

# Reference copy for independently installed recovery authority. Recovery consumes one
# immutable generation ID and never consults a mutable 'latest rollback' directory.
PATH=/usr/bin:/bin; export PATH
unset PYTHONPATH PYTHONHOME PYTHONUSERBASE PYTHONSTARTUP PYTHONINSPECT LD_PRELOAD LD_LIBRARY_PATH BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM || true
export PYTHONNOUSERSITE=1
LIBEXEC="/usr/local/libexec/polymarket-weather-paper"
HOST_PATHS="/etc/polymarket-weather-paper/host-paths.conf"
GATE="${LIBEXEC}/release-gate.py"
AUTHORITY="/etc/polymarket-weather-paper/host-authority.json"
fail(){ printf 'HOST ROLLBACK ERROR: %s\n' "$*" >&2; exit 1; }
GENERATION_ID=""
while (( $# )); do case "$1" in --generation-id) GENERATION_ID="${2:-}"; shift 2;; *) fail "unknown argument $1";; esac; done
[[ "${EUID}" == 0 ]] || fail "recovery authority must run as root"
[[ "${GENERATION_ID}" =~ ^[0-9a-f]{32}$ ]] || fail "exact --generation-id required"
/usr/bin/python3 "${GATE}" verify-authority --authority-manifest "${AUTHORITY}"
[[ -f "${HOST_PATHS}" && ! -L "${HOST_PATHS}" && "$(stat -c '%u' "${HOST_PATHS}")" == 0 ]] || fail "root-owned host paths missing"
# shellcheck disable=SC1090
source "${HOST_PATHS}"
GEN="${ROLLBACK_DIR}/generations/${GENERATION_ID}"
MANIFEST="${GEN}/generation.json"
[[ -d "${GEN}" && ! -L "${GEN}" && -f "${MANIFEST}" && ! -L "${MANIFEST}" ]] || fail "immutable generation missing"
[[ "$(stat -c '%u' "${MANIFEST}")" == 0 ]] || fail "generation manifest not root owned"
(( (8#$(stat -c '%a' "${MANIFEST}") & 8#22) == 0 )) || fail "generation manifest writable by nonroot"

# Parse only after root-custody checks; validate all bound identities and payload digests.
eval "$(/usr/bin/python3 - "${MANIFEST}" "${GENERATION_ID}" "${APP_DIR}" "${DEPLOY_USER}" <<'PY'
import hashlib,json,shlex,sys
from pathlib import Path
p=Path(sys.argv[1]); gid,app,user=sys.argv[2:]
m=json.loads(p.read_text())
if m.get('version')!='weather-paper-cutover-generation-v1' or m.get('generation_id')!=gid: raise SystemExit('generation identity invalid')
if m.get('app_dir')!=app or m.get('deploy_user')!=user: raise SystemExit('generation host identity invalid')
for k in ('predecessor_sha','predecessor_tree','candidate_sha','candidate_tree'):
 v=str(m.get(k,''));
 if len(v)!=40 or any(c not in '0123456789abcdef' for c in v): raise SystemExit(k+' invalid')
for k in ('predecessor_unit_digest','predecessor_venv_digest','venv_manifest_digest'):
 v=str(m.get(k,''));
 if len(v)!=64 or any(c not in '0123456789abcdef' for c in v): raise SystemExit(k+' invalid')
if m.get('predecessor_db_digest')!='ABSENT':
 v=str(m.get('predecessor_db_digest',''))
 if len(v)!=64 or any(c not in '0123456789abcdef' for c in v): raise SystemExit('db digest invalid')
for key in ('generation_id','predecessor_sha','predecessor_tree','candidate_sha','candidate_tree','release_marker','predecessor_venv'):
 print(key.upper()+'='+shlex.quote(str(m[key])))
print('PREVIOUS_ACTIVE='+('1' if m.get('active') else '0'))
print('PREVIOUS_ENABLED='+('1' if m.get('enabled') else '0'))
print('DB_PRESENT='+('1' if m.get('db_present') else '0'))
print('UNIT_DIGEST='+shlex.quote(str(m['predecessor_unit_digest'])))
print('DB_DIGEST='+shlex.quote(str(m['predecessor_db_digest'])))
print('VENV_DIGEST='+shlex.quote(str(m['predecessor_venv_digest'])))
print('VENV_MANIFEST_DIGEST='+shlex.quote(str(m['venv_manifest_digest'])))
PY
)" || fail "generation manifest validation failed"
[[ "${RELEASE_MARKER}" == "${PREDECESSOR_SHA}" ]] || fail "generation release marker mismatch"
sha256sum -c <(printf '%s  %s\n' "${UNIT_DIGEST}" "${GEN}/${UNIT}") >/dev/null || fail "unit digest mismatch"
sha256sum -c <(printf '%s  %s\n' "${VENV_DIGEST}" "${GEN}/predecessor-venv.tar") >/dev/null || fail "venv archive digest mismatch"
sha256sum -c <(printf '%s  %s\n' "${VENV_MANIFEST_DIGEST}" "${GEN}/predecessor-venv-manifest.json") >/dev/null || fail "venv manifest digest mismatch"
if [[ "${DB_PRESENT}" == 1 ]]; then sha256sum -c <(printf '%s  %s\n' "${DB_DIGEST}" "${GEN}/predecessor-db.sqlite") >/dev/null || fail "DB digest mismatch"; else [[ "${DB_DIGEST}" == ABSENT ]] || fail "absent DB digest invalid"; fi

systemctl stop "${UNIT}" >/dev/null 2>&1 || true
systemctl disable "${UNIT}" >/dev/null 2>&1 || true

# Restore source identity. Worktree corruption is irrelevant: discard it completely.
# If .git remains healthy use the captured predecessor object; otherwise a generation
# may include predecessor.git.bundle (new snapshots do) and recovery reclones from it.
if git -c safe.directory="${APP_DIR}" -C "${APP_DIR}" cat-file -e "${PREDECESSOR_SHA}^{commit}" 2>/dev/null; then
  git -c safe.directory="${APP_DIR}" -C "${APP_DIR}" reset --hard "${PREDECESSOR_SHA}"
  git -c safe.directory="${APP_DIR}" -C "${APP_DIR}" clean -ffdx
  git -c safe.directory="${APP_DIR}" -C "${APP_DIR}" checkout --detach "${PREDECESSOR_SHA}"
elif [[ -f "${GEN}/predecessor.git.bundle" ]]; then
  QUARANTINE="${APP_DIR}.corrupt-${GENERATION_ID}"
  rm -rf "${QUARANTINE}"; mv "${APP_DIR}" "${QUARANTINE}"
  git clone "${GEN}/predecessor.git.bundle" "${APP_DIR}"
  git -C "${APP_DIR}" checkout --detach "${PREDECESSOR_SHA}"
  rm -rf "${QUARANTINE}"
else
  fail "candidate checkout corrupt and generation lacks git bundle"
fi
[[ "$(git -c safe.directory="${APP_DIR}" -C "${APP_DIR}" rev-parse HEAD)" == "${PREDECESSOR_SHA}" ]] || fail "restored SHA mismatch"
[[ "$(git -c safe.directory="${APP_DIR}" -C "${APP_DIR}" rev-parse HEAD^{tree})" == "${PREDECESSOR_TREE}" ]] || fail "restored tree mismatch"

# Restore exact predecessor environment at the path bound in the generation. Never
# reconstruct it from requirements. Candidate release environments are discarded.
VENV_PARENT="$(dirname "${PREDECESSOR_VENV}")"; VENV_NAME="$(basename "${PREDECESSOR_VENV}")"
case "${PREDECESSOR_VENV}" in "${APP_DIR}/.venv"|"${APP_DIR}/.releases/${PREDECESSOR_SHA}/venv") ;; *) fail "bound predecessor venv path invalid";; esac
mkdir -p "${VENV_PARENT}"; rm -rf "${PREDECESSOR_VENV}"
tar --numeric-owner -C "${VENV_PARENT}" -xf "${GEN}/predecessor-venv.tar"
[[ -x "${PREDECESSOR_VENV}/bin/python" && "$(basename "${PREDECESSOR_VENV}")" == "${VENV_NAME}" ]] || fail "restored predecessor venv missing"
/usr/bin/python3 - "${PREDECESSOR_VENV}" "${GEN}/predecessor-venv-manifest.json" <<'PY'
import hashlib,json,os,stat,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve(); expected=json.loads(Path(sys.argv[2]).read_text()); rows=[]
for p in sorted([root,*root.rglob('*')], key=lambda x:str(x.relative_to(root.parent))):
 st=p.lstat(); rel=str(p.relative_to(root.parent)); row={'path':rel,'mode':stat.S_IMODE(st.st_mode),'uid':st.st_uid,'gid':st.st_gid}
 if p.is_symlink(): row.update(kind='symlink',target=os.readlink(p))
 elif p.is_dir(): row['kind']='dir'
 elif p.is_file(): row.update(kind='file',size=st.st_size,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
 else: raise SystemExit('unsupported node')
 rows.append(row)
raw=json.dumps(rows,sort_keys=True,separators=(',',':')).encode()
if hashlib.sha256(raw).hexdigest()!=expected.get('tree_sha256') or rows!=expected.get('entries'): raise SystemExit('restored venv byte tree mismatch')
PY

mkdir -p "$(dirname "${DB_PATH}")"
if [[ "${DB_PRESENT}" == 1 ]]; then install -o "${DEPLOY_UID}" -g "${DEPLOY_GID}" -m 0600 "${GEN}/predecessor-db.sqlite" "${DB_PATH}"; else rm -f "${DB_PATH}" "${DB_PATH}-wal" "${DB_PATH}-shm"; fi
install -o root -g root -m 0644 "${GEN}/${UNIT}" "/etc/systemd/system/${UNIT}"
mkdir -p "${CONFIG_DIR}"; printf '%s\n' "${PREDECESSOR_SHA}" > "${CONFIG_DIR}/weather-paper-release.sha"; chown "${DEPLOY_UID}:${DEPLOY_GID}" "${CONFIG_DIR}/weather-paper-release.sha"; chmod 0600 "${CONFIG_DIR}/weather-paper-release.sha"
rm -f "${CONFIG_DIR}/weather-paper-cutover-generation.id"
chown -R "${DEPLOY_UID}:${DEPLOY_GID}" "${APP_DIR}"
systemctl daemon-reload
if [[ "${PREVIOUS_ENABLED}" == 1 ]]; then systemctl enable "${UNIT}" >/dev/null; fi
if [[ "${PREVIOUS_ACTIVE}" == 1 ]]; then systemctl start "${UNIT}"; fi
printf 'PASS: immutable generation %s restored exact predecessor %s.\n' "${GENERATION_ID}" "${PREDECESSOR_SHA}"
