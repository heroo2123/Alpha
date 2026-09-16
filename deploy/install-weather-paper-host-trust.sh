#!/usr/bin/env bash
set -Eeuo pipefail

# ONE-TIME HOST BOOTSTRAP ONLY. Ordinary candidate deployment MUST NOT call this.
# Authority implementation is sourced from an operator-controlled directory outside
# the candidate checkout and pinned by a caller-supplied bundle digest. Candidate Git
# objects are data only and are never used as the source of privileged authority code.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
LIBEXEC="/usr/local/libexec/polymarket-weather-paper"
ETC_DIR="/etc/polymarket-weather-paper"
AUTHORITY_MANIFEST="${ETC_DIR}/host-authority.json"
SOURCE=""
EXPECTED_BUNDLE=""
BOOTSTRAP=0

fail(){ printf 'HOST TRUST BOOTSTRAP ERROR: %s\n' "$*" >&2; exit 1; }
while (( $# )); do
  case "$1" in
    --authority-source) SOURCE="${2:-}"; shift 2;;
    --bundle-sha256) EXPECTED_BUNDLE="${2:-}"; shift 2;;
    --bootstrap) BOOTSTRAP=1; shift;;
    *) fail "unknown argument: $1";;
  esac
done
[[ "${BOOTSTRAP}" == 1 ]] || fail "explicit --bootstrap required; candidate deployment cannot install host authority"
[[ "${SOURCE}" == /* && -d "${SOURCE}" && ! -L "${SOURCE}" ]] || fail "external authority source directory required"
[[ "${EXPECTED_BUNDLE}" =~ ^[0-9a-f]{64}$ ]] || fail "exact external bundle SHA-256 required"
SOURCE_REAL="$(realpath -e "${SOURCE}")"
APP_REAL="$(realpath -m "${APP_DIR}")"
case "${SOURCE_REAL}/" in "${APP_REAL}/"*) fail "authority source may not reside in candidate application tree";; esac

required=(release-gate.py snapshot-rollback.sh restore-rollback.sh weather-paper-venv-snapshot.py authority-template.json)
for name in "${required[@]}"; do
  [[ -f "${SOURCE_REAL}/${name}" && ! -L "${SOURCE_REAL}/${name}" ]] || fail "authority bundle missing regular file: ${name}"
done
BUNDLE_ACTUAL="$(/usr/bin/python3 - "${SOURCE_REAL}" "${required[@]}" <<'PY'
import hashlib, sys
from pathlib import Path
root=Path(sys.argv[1]); h=hashlib.sha256()
for name in sorted(sys.argv[2:]):
    data=(root/name).read_bytes()
    h.update(name.encode()+b'\0'+len(data).to_bytes(8,'big')+data)
print(h.hexdigest())
PY
)"
[[ "${BUNDLE_ACTUAL}" == "${EXPECTED_BUNDLE}" ]] || fail "external authority bundle digest mismatch"

# Existing authority is immutable through this interface. An upgrade is a separate
# host-administration operation, not a candidate deployment operation.
if [[ -e "${AUTHORITY_MANIFEST}" || -e "${LIBEXEC}/release-gate.py" ]]; then
  fail "host authority already installed; bootstrap refuses replacement"
fi

TMP="$(mktemp -d)"; trap 'rm -rf "${TMP}"' EXIT
for name in release-gate.py snapshot-rollback.sh restore-rollback.sh weather-paper-venv-snapshot.py; do
  cp -- "${SOURCE_REAL}/${name}" "${TMP}/${name}"
done
/usr/bin/python3 - "${SOURCE_REAL}/authority-template.json" "${TMP}" "${EXPECTED_BUNDLE}" > "${TMP}/host-authority.json" <<'PY'
import hashlib,json,sys
from pathlib import Path
template=json.loads(Path(sys.argv[1]).read_text())
root=Path(sys.argv[2]); bundle=sys.argv[3]
if template.get('version') != 'weather-paper-host-release-authority-v2-independent':
    raise SystemExit('authority template version invalid')
protected=template.get('protected_candidate_blobs')
if not isinstance(protected,dict) or not protected:
    raise SystemExit('protected candidate blobs missing')
files={}
for name in ('release-gate.py','snapshot-rollback.sh','restore-rollback.sh','weather-paper-venv-snapshot.py'):
    files[name]=hashlib.sha256((root/name).read_bytes()).hexdigest()
print(json.dumps({'version':template['version'],'bundle_sha256':bundle,'libexec':'/usr/local/libexec/polymarket-weather-paper','files':files,'protected_candidate_blobs':protected},sort_keys=True,indent=2))
PY

sudo install -d -o root -g root -m 0755 "${LIBEXEC}" "${ETC_DIR}"
sudo install -o root -g root -m 0555 "${TMP}/release-gate.py" "${LIBEXEC}/release-gate.py"
sudo install -o root -g root -m 0555 "${TMP}/snapshot-rollback.sh" "${LIBEXEC}/snapshot-rollback.sh"
sudo install -o root -g root -m 0555 "${TMP}/restore-rollback.sh" "${LIBEXEC}/restore-rollback.sh"
sudo install -o root -g root -m 0555 "${TMP}/weather-paper-venv-snapshot.py" "${LIBEXEC}/weather-paper-venv-snapshot.py"
sudo install -o root -g root -m 0444 "${TMP}/host-authority.json" "${AUTHORITY_MANIFEST}"
/usr/bin/python3 "${LIBEXEC}/release-gate.py" verify-authority
printf 'PASS: independent host authority bootstrapped from external bundle %s.\n' "${EXPECTED_BUNDLE}"
