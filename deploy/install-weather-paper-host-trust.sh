#!/usr/bin/env bash
set -Eeuo pipefail

# One-time host bootstrap performed BEFORE candidate checkout. It establishes a
# root-owned trust boundary independent of the candidate working tree. Every host
# authority tool is materialized from the exact reviewed candidate Git object; the
# mutable directory containing this bootstrap is never the source of installed tools.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
UNIT="polymarket-weather-paper.service"
LIBEXEC="/usr/local/libexec/polymarket-weather-paper"
ETC_DIR="/etc/polymarket-weather-paper"
MANIFEST="${ETC_DIR}/approved-releases.json"
HOST_PATHS="${ETC_DIR}/host-paths.conf"
DROPIN_DIR="/etc/systemd/system/${UNIT}.d"
DROPIN="${DROPIN_DIR}/10-release-authority.conf"
CANDIDATE_SHA="${1:-}"

fail(){ printf 'HOST TRUST INSTALL ERROR: %s\n' "$*" >&2; exit 1; }
[[ "${CANDIDATE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] || fail "usage: $0 <reviewed-candidate-sha>"
CANDIDATE_SHA="${CANDIDATE_SHA,,}"
[[ -d "${APP_DIR}/.git" && -f "${RELEASE_FILE}" ]] || fail "current known-good checkout/release marker missing"
for value in "${APP_DIR}" "${CONFIG_DIR}" "${DB_PATH}"; do
  [[ "${value}" == /* && "${value}" != *$'\n'* && "${value}" != *$'\r'* ]] || fail "host path must be absolute and single-line"
done
[[ "${UNIT}" == "polymarket-weather-paper.service" ]] || fail "unexpected service identity"

CURRENT_SHA="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
CURRENT_MARKER="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${CURRENT_SHA}" == "${CURRENT_MARKER}" && "${CURRENT_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "current release identity mismatch"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] || fail "current known-good checkout is dirty"
git -C "${APP_DIR}" cat-file -e "${CANDIDATE_SHA}^{commit}" 2>/dev/null || fail "candidate commit object must be fetched before host approval"
CURRENT_TREE="$(git -C "${APP_DIR}" rev-parse "${CURRENT_SHA}^{tree}" | tr -d '[:space:]')"
CANDIDATE_TREE="$(git -C "${APP_DIR}" rev-parse "${CANDIDATE_SHA}^{tree}" | tr -d '[:space:]')"

# Fail closed if the bootstrap being invoked is not itself the exact reviewed blob.
# This catches accidental/local edits before any root-owned authority is installed.
EXPECTED_BOOTSTRAP_BLOB="$(git -C "${APP_DIR}" rev-parse "${CANDIDATE_SHA}:deploy/install-weather-paper-host-trust.sh")"
ACTUAL_BOOTSTRAP_BLOB="$(git -C "${APP_DIR}" hash-object "${BASH_SOURCE[0]}")"
[[ "${ACTUAL_BOOTSTRAP_BLOB}" == "${EXPECTED_BOOTSTRAP_BLOB}" ]] || fail "bootstrap script does not match reviewed candidate object"

TMP_BUNDLE="$(mktemp -d)"
cleanup(){ rm -rf "${TMP_BUNDLE}"; }
trap cleanup EXIT
for name in weather-paper-host-release-gate.py weather-paper-venv-snapshot.py weather-paper-host-snapshot.sh weather-paper-host-recovery.sh; do
  candidate_path="deploy/${name}"
  git -C "${APP_DIR}" show "${CANDIDATE_SHA}:${candidate_path}" > "${TMP_BUNDLE}/${name}" \
    || fail "reviewed candidate missing host tool: ${candidate_path}"
  EXPECTED_BLOB="$(git -C "${APP_DIR}" rev-parse "${CANDIDATE_SHA}:${candidate_path}")"
  ACTUAL_BLOB="$(git -C "${APP_DIR}" hash-object "${TMP_BUNDLE}/${name}")"
  [[ "${ACTUAL_BLOB}" == "${EXPECTED_BLOB}" ]] || fail "materialized host tool blob mismatch: ${name}"
done

sudo install -d -o root -g root -m 0755 "${LIBEXEC}" "${ETC_DIR}" "${DROPIN_DIR}"
sudo install -o root -g root -m 0555 "${TMP_BUNDLE}/weather-paper-host-release-gate.py" "${LIBEXEC}/release-gate.py"
sudo install -o root -g root -m 0555 "${TMP_BUNDLE}/weather-paper-venv-snapshot.py" "${LIBEXEC}/weather-paper-venv-snapshot.py"
sudo install -o root -g root -m 0555 "${TMP_BUNDLE}/weather-paper-host-snapshot.sh" "${LIBEXEC}/snapshot-rollback.sh"
sudo install -o root -g root -m 0555 "${TMP_BUNDLE}/weather-paper-host-recovery.sh" "${LIBEXEC}/restore-rollback.sh"

# Freeze every filesystem/service path used by rollback outside candidate control.
# %q makes the root-owned file safe to source even if a legitimate path contains
# shell metacharacters; the values were also required to be absolute single lines.
TMP_PATHS="$(mktemp)"
printf 'APP_DIR=%q\nCONFIG_DIR=%q\nDB_PATH=%q\nUNIT=%q\n' \
  "${APP_DIR}" "${CONFIG_DIR}" "${DB_PATH}" "${UNIT}" > "${TMP_PATHS}"
sudo install -o root -g root -m 0444 "${TMP_PATHS}" "${HOST_PATHS}"
rm -f "${TMP_PATHS}"

# Approval is intentionally NOT cumulative. At any cutover exactly the currently
# running known-good release and the one reviewed candidate are startable. Re-running
# bootstrap therefore revokes stale historical candidate approvals.
TMP_MANIFEST="$(mktemp)"
/usr/bin/python3 - "${CURRENT_SHA}" "${CURRENT_TREE}" "${CANDIDATE_SHA}" "${CANDIDATE_TREE}" > "${TMP_MANIFEST}" <<'PY'
import json, sys
current_sha, current_tree, candidate_sha, candidate_tree = [value.lower() for value in sys.argv[1:]]
approved = {current_sha: current_tree, candidate_sha: candidate_tree}
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
UnsetEnvironment=HTTP_PROXY HTTPS_PROXY ALL_PROXY NO_PROXY http_proxy https_proxy all_proxy no_proxy SSL_CERT_FILE SSL_CERT_DIR BASH_ENV ENV CDPATH GIT_DIR GIT_WORK_TREE GIT_CONFIG_GLOBAL GIT_CONFIG_SYSTEM
EOF
sudo install -o root -g root -m 0644 "${TMP_DROPIN}" "${DROPIN}"
rm -f "${TMP_DROPIN}"
sudo systemctl daemon-reload

/usr/bin/python3 "${LIBEXEC}/release-gate.py" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
/usr/bin/python3 "${LIBEXEC}/release-gate.py" verify-object --app-dir "${APP_DIR}" --sha "${CANDIDATE_SHA}"
trap - EXIT
cleanup
printf 'PASS: host trust authority installed from exact reviewed Git object. Current=%s Candidate=%s\n' "${CURRENT_SHA}" "${CANDIDATE_SHA}"
