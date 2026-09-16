#!/usr/bin/env bash
set -Eeuo pipefail

# ONE-TIME HOST BOOTSTRAP ONLY. Ordinary candidate deployment MUST NOT call this.
# Authority implementation comes from an operator-controlled external directory and
# is digest-pinned independently of the application candidate. Candidate Git objects
# are data only and never supply privileged authority implementation.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
UNIT="polymarket-weather-paper.service"
ROLLBACK_DIR="/var/lib/polymarket-weather-paper-rollback"
LIBEXEC="/usr/local/libexec/polymarket-weather-paper"
ETC_DIR="/etc/polymarket-weather-paper"
HOST_PATHS="${ETC_DIR}/host-paths.conf"
AUTHORITY_MANIFEST="${ETC_DIR}/host-authority.json"
SOURCE=""; EXPECTED_BUNDLE=""; BOOTSTRAP=0
DEPLOY_USER="$(id -un)"; DEPLOY_UID="$(id -u)"; DEPLOY_GID="$(id -g)"

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
[[ "${APP_DIR}" == /* && "${CONFIG_DIR}" == /* && "${DB_PATH}" == /* && "${ROLLBACK_DIR}" == /* ]] || fail "host paths must be absolute"
[[ "${DEPLOY_USER}" =~ ^[A-Za-z0-9_.-]+$ && "${DEPLOY_UID}" =~ ^[0-9]+$ && "${DEPLOY_GID}" =~ ^[0-9]+$ ]] || fail "deploy identity invalid"
[[ "${SOURCE}" == /* && -d "${SOURCE}" && ! -L "${SOURCE}" ]] || fail "external authority source directory required"
[[ "${EXPECTED_BUNDLE}" =~ ^[0-9a-f]{64}$ ]] || fail "exact external bundle SHA-256 required"
SOURCE_REAL="$(realpath -e "${SOURCE}")"; APP_REAL="$(realpath -m "${APP_DIR}")"
case "${SOURCE_REAL}/" in "${APP_REAL}/"*) fail "authority source may not reside in candidate application tree";; esac

required=(release-gate.py snapshot-rollback.sh restore-rollback.sh weather-paper-venv-snapshot.py authority-template.json)
for name in "${required[@]}"; do [[ -f "${SOURCE_REAL}/${name}" && ! -L "${SOURCE_REAL}/${name}" ]] || fail "authority bundle missing regular file: ${name}"; done
BUNDLE_ACTUAL="$(/usr/bin/python3 - "${SOURCE_REAL}" "${required[@]}" <<'PY'
import hashlib,sys
from pathlib import Path
root=Path(sys.argv[1]); h=hashlib.sha256()
for name in sorted(sys.argv[2:]):
 data=(root/name).read_bytes(); h.update(name.encode()+b'\0'+len(data).to_bytes(8,'big')+data)
print(h.hexdigest())
PY
)"
[[ "${BUNDLE_ACTUAL}" == "${EXPECTED_BUNDLE}" ]] || fail "external authority bundle digest mismatch"

# Existing implementation is immutable through this interface. Host-path data is also
# bootstrap-only: candidate deployment cannot silently repoint authority at a new app,
# database, service identity, deploy principal, or rollback custody directory.
if [[ -e "${AUTHORITY_MANIFEST}" || -e "${LIBEXEC}/release-gate.py" || -e "${HOST_PATHS}" ]]; then fail "host authority already installed; bootstrap refuses replacement"; fi

TMP="$(mktemp -d)"; trap 'rm -rf "${TMP}"' EXIT
for name in release-gate.py snapshot-rollback.sh restore-rollback.sh weather-paper-venv-snapshot.py; do cp -- "${SOURCE_REAL}/${name}" "${TMP}/${name}"; done
/usr/bin/python3 - "${SOURCE_REAL}/authority-template.json" "${TMP}" "${EXPECTED_BUNDLE}" > "${TMP}/host-authority.json" <<'PY'
import hashlib,json,sys
from pathlib import Path
template=json.loads(Path(sys.argv[1]).read_text()); root=Path(sys.argv[2]); bundle=sys.argv[3]
if template.get('version')!='weather-paper-host-release-authority-v2-independent': raise SystemExit('authority template version invalid')
protected=template.get('protected_candidate_blobs')
if not isinstance(protected,dict) or not protected: raise SystemExit('protected candidate blobs missing')
files={name:hashlib.sha256((root/name).read_bytes()).hexdigest() for name in ('release-gate.py','snapshot-rollback.sh','restore-rollback.sh','weather-paper-venv-snapshot.py')}
print(json.dumps({'version':template['version'],'bundle_sha256':bundle,'libexec':'/usr/local/libexec/polymarket-weather-paper','files':files,'protected_candidate_blobs':protected},sort_keys=True,indent=2))
PY
printf 'APP_DIR=%q\nCONFIG_DIR=%q\nDB_PATH=%q\nUNIT=%q\nROLLBACK_DIR=%q\nDEPLOY_USER=%q\nDEPLOY_UID=%q\nDEPLOY_GID=%q\n' \
  "${APP_DIR}" "${CONFIG_DIR}" "${DB_PATH}" "${UNIT}" "${ROLLBACK_DIR}" "${DEPLOY_USER}" "${DEPLOY_UID}" "${DEPLOY_GID}" > "${TMP}/host-paths.conf"

sudo install -d -o root -g root -m 0755 "${LIBEXEC}" "${ETC_DIR}"
sudo install -d -o root -g "${DEPLOY_GID}" -m 0750 "${ROLLBACK_DIR}" "${ROLLBACK_DIR}/generations"
sudo install -o root -g root -m 0555 "${TMP}/release-gate.py" "${LIBEXEC}/release-gate.py"
sudo install -o root -g root -m 0555 "${TMP}/snapshot-rollback.sh" "${LIBEXEC}/snapshot-rollback.sh"
sudo install -o root -g root -m 0555 "${TMP}/restore-rollback.sh" "${LIBEXEC}/restore-rollback.sh"
sudo install -o root -g root -m 0555 "${TMP}/weather-paper-venv-snapshot.py" "${LIBEXEC}/weather-paper-venv-snapshot.py"
sudo install -o root -g root -m 0444 "${TMP}/host-authority.json" "${AUTHORITY_MANIFEST}"
sudo install -o root -g root -m 0444 "${TMP}/host-paths.conf" "${HOST_PATHS}"
/usr/bin/python3 "${LIBEXEC}/release-gate.py" verify-authority
printf 'PASS: independent host authority bootstrapped from external bundle %s.\n' "${EXPECTED_BUNDLE}"
