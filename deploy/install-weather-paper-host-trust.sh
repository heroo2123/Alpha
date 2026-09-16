#!/usr/bin/env bash
set -Eeuo pipefail

# One-time host bootstrap performed BEFORE candidate checkout.  It establishes a
# root-owned trust boundary independent of the candidate working tree.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
UNIT="polymarket-weather-paper.service"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LIBEXEC="/usr/local/libexec/polymarket-weather-paper"
ETC_DIR="/etc/polymarket-weather-paper"
MANIFEST="${ETC_DIR}/approved-releases.json"
DROPIN_DIR="/etc/systemd/system/${UNIT}.d"
DROPIN="${DROPIN_DIR}/10-release-authority.conf"
CANDIDATE_SHA="${1:-}"

fail(){ printf 'HOST TRUST INSTALL ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${CANDIDATE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] || fail "usage: $0 <reviewed-candidate-sha>"
[[ -d "${APP_DIR}/.git" && -f "${RELEASE_FILE}" ]] || fail "current known-good checkout/release marker missing"
for name in weather-paper-host-release-gate.py weather-paper-venv-snapshot.py weather-paper-host-snapshot.sh weather-paper-host-recovery.sh; do
  [[ -f "${SCRIPT_DIR}/${name}" ]] || fail "bootstrap bundle missing ${name}"
done

CURRENT_SHA="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
CURRENT_MARKER="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${CURRENT_SHA}" == "${CURRENT_MARKER}" && "${CURRENT_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "current release identity mismatch"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "current known-good checkout is dirty"
git -C "${APP_DIR}" cat-file -e "${CANDIDATE_SHA}^{commit}" 2>/dev/null || fail "candidate commit object must be fetched before host approval"
CURRENT_TREE="$(git -C "${APP_DIR}" rev-parse "${CURRENT_SHA}^{tree}" | tr -d '[:space:]')"
CANDIDATE_TREE="$(git -C "${APP_DIR}" rev-parse "${CANDIDATE_SHA}^{tree}" | tr -d '[:space:]')"

sudo install -d -o root -g root -m 0755 "${LIBEXEC}" "${ETC_DIR}" "${DROPIN_DIR}"
sudo install -o root -g root -m 0555 "${SCRIPT_DIR}/weather-paper-host-release-gate.py" "${LIBEXEC}/release-gate.py"
sudo install -o root -g root -m 0555 "${SCRIPT_DIR}/weather-paper-venv-snapshot.py" "${LIBEXEC}/weather-paper-venv-snapshot.py"
sudo install -o root -g root -m 0555 "${SCRIPT_DIR}/weather-paper-host-snapshot.sh" "${LIBEXEC}/snapshot-rollback.sh"
sudo install -o root -g root -m 0555 "${SCRIPT_DIR}/weather-paper-host-recovery.sh" "${LIBEXEC}/restore-rollback.sh"

TMP_MANIFEST="$(mktemp)"
/usr/bin/python3 - "${MANIFEST}" "${CURRENT_SHA}" "${CURRENT_TREE}" "${CANDIDATE_SHA,,}" "${CANDIDATE_TREE}" > "${TMP_MANIFEST}" <<'PY'
import json, sys
from pathlib import Path
manifest, current_sha, current_tree, candidate_sha, candidate_tree = sys.argv[1:]
approved = {}
p = Path(manifest)
if p.exists():
    try:
        old = json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        raise SystemExit('existing host approval manifest is unreadable')
    if old.get('version') != 'weather-paper-host-release-authority-v1':
        raise SystemExit('existing host approval manifest version invalid')
    for row in old.get('approved') or []:
        if isinstance(row, dict):
            approved[str(row.get('sha') or '').lower()] = str(row.get('tree') or '').lower()
approved[current_sha.lower()] = current_tree.lower()
approved[candidate_sha.lower()] = candidate_tree.lower()
print(json.dumps({
    'version': 'weather-paper-host-release-authority-v1',
    'approved': [{'sha': sha, 'tree': tree} for sha, tree in sorted(approved.items())],
}, sort_keys=True, indent=2))
PY
sudo install -o root -g root -m 0444 "${TMP_MANIFEST}" "${MANIFEST}"
rm -f "${TMP_MANIFEST}"

TMP_DROPIN="$(mktemp)"
cat > "${TMP_DROPIN}" <<EOF
[Service]
ExecStartPre=/usr/bin/python3 ${LIBEXEC}/release-gate.py verify-checkout --app-dir ${APP_DIR} --release-file ${RELEASE_FILE}
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy SSL_CERT_FILE SSL_CERT_DIR
EOF
sudo install -o root -g root -m 0644 "${TMP_DROPIN}" "${DROPIN}"
rm -f "${TMP_DROPIN}"
sudo systemctl daemon-reload

/usr/bin/python3 "${LIBEXEC}/release-gate.py" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
/usr/bin/python3 "${LIBEXEC}/release-gate.py" verify-object --app-dir "${APP_DIR}" --sha "${CANDIDATE_SHA,,}"
printf 'PASS: host trust authority installed. Current=%s Candidate=%s\n' "${CURRENT_SHA}" "${CANDIDATE_SHA,,}"
