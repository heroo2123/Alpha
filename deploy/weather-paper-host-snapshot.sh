#!/usr/bin/env bash
set -Eeuo pipefail

# Reference copy for the independently installed host snapshot authority.
PATH=/usr/bin:/bin; export PATH
unset PYTHONPATH PYTHONHOME PYTHONUSERBASE PYTHONSTARTUP PYTHONINSPECT LD_PRELOAD LD_LIBRARY_PATH BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM || true
export PYTHONNOUSERSITE=1
LIBEXEC="/usr/local/libexec/polymarket-weather-paper"
HOST_PATHS="/etc/polymarket-weather-paper/host-paths.conf"
GATE="${LIBEXEC}/release-gate.py"
AUTHORITY="/etc/polymarket-weather-paper/host-authority.json"

fail(){ printf 'HOST SNAPSHOT ERROR: %s\n' "$*" >&2; exit 1; }
CANDIDATE_SHA=""
while (( $# )); do case "$1" in --candidate-sha) CANDIDATE_SHA="${2:-}"; shift 2;; *) fail "unknown argument $1";; esac; done
[[ "${EUID}" == 0 ]] || fail "snapshot authority must run as root"
[[ "${CANDIDATE_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "--candidate-sha required"
/usr/bin/python3 "${GATE}" verify-authority --authority-manifest "${AUTHORITY}"
[[ -f "${HOST_PATHS}" && ! -L "${HOST_PATHS}" && "$(stat -c '%u' "${HOST_PATHS}")" == 0 ]] || fail "root-owned host paths missing"
(( (8#$(stat -c '%a' "${HOST_PATHS}") & 8#22) == 0 )) || fail "host paths writable by nonroot"
# shellcheck disable=SC1090
source "${HOST_PATHS}"
[[ "${APP_DIR:-}" == /* && "${CONFIG_DIR:-}" == /* && "${DB_PATH:-}" == /* && "${ROLLBACK_DIR:-}" == /* ]] || fail "pinned paths invalid"
[[ "${UNIT:-}" == "polymarket-weather-paper.service" ]] || fail "service identity invalid"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
UNIT_FILE="/etc/systemd/system/${UNIT}"
GENERATIONS="${ROLLBACK_DIR}/generations"
[[ -d "${APP_DIR}/.git" && -f "${RELEASE_FILE}" && -f "${UNIT_FILE}" ]] || fail "predecessor evidence missing"
PREDECESSOR_SHA="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
PREDECESSOR_TREE="$(git -C "${APP_DIR}" rev-parse HEAD^{tree} | tr -d '[:space:]')"
RELEASE_MARKER="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${PREDECESSOR_SHA}" == "${RELEASE_MARKER}" ]] || fail "predecessor release marker mismatch"
[[ "${PREDECESSOR_SHA}" != "${CANDIDATE_SHA}" ]] || fail "current SHA equals candidate; predecessor generation creation forbidden"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "predecessor checkout dirty"
git -C "${APP_DIR}" cat-file -e "${CANDIDATE_SHA}^{commit}" 2>/dev/null || fail "candidate object missing"
CANDIDATE_TREE="$(git -C "${APP_DIR}" rev-parse "${CANDIDATE_SHA}^{tree}" | tr -d '[:space:]')"
/usr/bin/python3 "${GATE}" verify-object --app-dir "${APP_DIR}" --sha "${PREDECESSOR_SHA}"
/usr/bin/python3 "${GATE}" verify-object --app-dir "${APP_DIR}" --sha "${CANDIDATE_SHA}"

# Prevent a second snapshot from rebinding the same cutover after candidate mutation or
# from replacing an existing immutable predecessor generation.
mkdir -p "${GENERATIONS}"
chown root:"${DEPLOY_GID}" "${ROLLBACK_DIR}" "${GENERATIONS}"
chmod 0750 "${ROLLBACK_DIR}" "${GENERATIONS}"
if find "${GENERATIONS}" -mindepth 2 -maxdepth 2 -name generation.json -type f -print0 2>/dev/null | \
   xargs -0 -r grep -lF '"candidate_sha": "'"${CANDIDATE_SHA}"'"' | grep -q .; then
  fail "cutover generation for candidate already exists"
fi
CREATED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
GENERATION_ID="$(/usr/bin/python3 - "${PREDECESSOR_SHA}" "${CANDIDATE_SHA}" "${CREATED_AT}" <<'PY'
import hashlib,sys
print(hashlib.sha256("\0".join(sys.argv[1:]).encode()).hexdigest()[:32])
PY
)"
GEN="${GENERATIONS}/${GENERATION_ID}"
[[ ! -e "${GEN}" ]] || fail "generation ID collision"
install -d -o root -g "${DEPLOY_GID}" -m 0750 "${GEN}"
ACTIVE=0; ENABLED=0
systemctl is-active --quiet "${UNIT}" 2>/dev/null && ACTIVE=1 || true
systemctl is-enabled --quiet "${UNIT}" 2>/dev/null && ENABLED=1 || true
install -o root -g "${DEPLOY_GID}" -m 0440 "${UNIT_FILE}" "${GEN}/${UNIT}"
printf '%s\n' "${RELEASE_MARKER}" > "${GEN}/predecessor-release-marker"

# Identify the predecessor interpreter from the captured unit; support both legacy
# APP_DIR/.venv and release-specific APP_DIR/.releases/<sha>/venv without rebuilding.
PREDECESSOR_PYTHON="$(sed -n 's/^ExecStart=\([^ ]*\/bin\/python\) .*/\1/p' "${UNIT_FILE}" | head -n1)"
[[ "${PREDECESSOR_PYTHON}" == "${APP_DIR}/.venv/bin/python" || "${PREDECESSOR_PYTHON}" == "${APP_DIR}/.releases/${PREDECESSOR_SHA}/venv/bin/python" ]] || fail "predecessor interpreter path invalid"
PREDECESSOR_VENV="${PREDECESSOR_PYTHON%/bin/python}"
[[ -x "${PREDECESSOR_PYTHON}" && -f "${PREDECESSOR_VENV}/pyvenv.cfg" ]] || fail "predecessor venv missing"
VENV_PARENT="$(dirname "${PREDECESSOR_VENV}")"; VENV_NAME="$(basename "${PREDECESSOR_VENV}")"
tar --numeric-owner --format=posix -C "${VENV_PARENT}" -cf "${GEN}/predecessor-venv.tar" "${VENV_NAME}"
/usr/bin/python3 - "${PREDECESSOR_VENV}" > "${GEN}/predecessor-venv-manifest.json" <<'PY'
import hashlib,json,os,stat,sys
from pathlib import Path
root=Path(sys.argv[1]).resolve(); rows=[]
for p in sorted([root,*root.rglob('*')], key=lambda x:str(x.relative_to(root.parent))):
 st=p.lstat(); rel=str(p.relative_to(root.parent))
 row={'path':rel,'mode':stat.S_IMODE(st.st_mode),'uid':st.st_uid,'gid':st.st_gid}
 if p.is_symlink(): row.update(kind='symlink',target=os.readlink(p))
 elif p.is_dir(): row['kind']='dir'
 elif p.is_file():
  h=hashlib.sha256(p.read_bytes()).hexdigest(); row.update(kind='file',size=st.st_size,sha256=h)
 else: raise SystemExit('unsupported venv node')
 rows.append(row)
raw=json.dumps(rows,sort_keys=True,separators=(',',':')).encode()
print(json.dumps({'version':'weather-paper-exact-venv-v3','root':str(root),'tree_sha256':hashlib.sha256(raw).hexdigest(),'entries':rows},sort_keys=True,indent=2))
PY

if [[ -f "${DB_PATH}" ]]; then
  /usr/bin/python3 - "${DB_PATH}" "${GEN}/predecessor-db.sqlite" <<'PY'
import os,sqlite3,sys
from pathlib import Path
s=Path(sys.argv[1]); t=Path(sys.argv[2]); tmp=t.with_name('.'+t.name+'.tmp')
src=sqlite3.connect(f'file:{s}?mode=ro',uri=True); dst=sqlite3.connect(tmp)
try: src.backup(dst)
finally: dst.close(); src.close()
os.replace(tmp,t)
PY
  DB_PRESENT=1; DB_DIGEST="$(sha256sum "${GEN}/predecessor-db.sqlite" | awk '{print $1}')"
else DB_PRESENT=0; DB_DIGEST=ABSENT; fi
UNIT_DIGEST="$(sha256sum "${GEN}/${UNIT}" | awk '{print $1}')"
VENV_DIGEST="$(sha256sum "${GEN}/predecessor-venv.tar" | awk '{print $1}')"
VENV_MANIFEST_DIGEST="$(sha256sum "${GEN}/predecessor-venv-manifest.json" | awk '{print $1}')"

/usr/bin/python3 - "${GENERATION_ID}" "${PREDECESSOR_SHA}" "${PREDECESSOR_TREE}" "${CANDIDATE_SHA}" "${CANDIDATE_TREE}" "${UNIT_DIGEST}" "${DB_DIGEST}" "${VENV_DIGEST}" "${VENV_MANIFEST_DIGEST}" "${ACTIVE}" "${ENABLED}" "${RELEASE_MARKER}" "${DEPLOY_USER}" "${APP_DIR}" "${CREATED_AT}" "${PREDECESSOR_VENV}" "${DB_PRESENT}" > "${GEN}/generation.json" <<'PY'
import json,sys
(k,gid,p_sha,p_tree,c_sha,c_tree,unit_d,db_d,venv_d,vm_d,active,enabled,marker,user,app,created,venv,db_present)=(None,*sys.argv[1:])
print(json.dumps({'version':'weather-paper-cutover-generation-v1','generation_id':gid,'predecessor_sha':p_sha,'predecessor_tree':p_tree,'candidate_sha':c_sha,'candidate_tree':c_tree,'predecessor_unit_digest':unit_d,'predecessor_db_digest':db_d,'predecessor_venv_digest':venv_d,'venv_manifest_digest':vm_d,'active':active=='1','enabled':enabled=='1','release_marker':marker,'deploy_user':user,'app_dir':app,'creation_timestamp':created,'predecessor_venv':venv,'db_present':db_present=='1'},sort_keys=True,indent=2))
PY
for path in "${GEN}"/*; do chown root:"${DEPLOY_GID}" "${path}"; chmod 0440 "${path}"; done
chmod 0550 "${GEN}"
# Publish only the generation ID. Never use a mutable 'latest rollback' payload.
printf '%s\n' "${GENERATION_ID}"
