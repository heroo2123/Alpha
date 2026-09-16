#!/usr/bin/env bash
set -Eeuo pipefail

# Installed root-owned under /usr/local/libexec/polymarket-weather-paper/.  This file
# is the independent rollback snapshot authority; candidate checkout scripts are not
# used to create or validate the rollback generation.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
UNIT="polymarket-weather-paper.service"
UNIT_FILE="/etc/systemd/system/${UNIT}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
ROLLBACK_DIR="${CONFIG_DIR}/all-paper-rollback"
LIBEXEC="/usr/local/libexec/polymarket-weather-paper"
GATE="${LIBEXEC}/release-gate.py"
VENV_HELPER="${LIBEXEC}/weather-paper-venv-snapshot.py"
GENERATION="${ROLLBACK_DIR}/snapshot-generation-v3"

fail(){ printf 'HOST SNAPSHOT ERROR: %s\n' "$*" >&2; exit 1; }
[[ -x /usr/bin/python3 && -f "${GATE}" && -f "${VENV_HELPER}" ]] || fail "host trust tools missing"
[[ -d "${APP_DIR}/.git" && -f "${RELEASE_FILE}" && -f "${UNIT_FILE}" ]] || fail "known-good release evidence missing"
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"

SHA="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
TREE="$(git -C "${APP_DIR}" rev-parse HEAD^{tree} | tr -d '[:space:]')"
ACTIVE=0; ENABLED=0
systemctl is-active --quiet "${UNIT}" 2>/dev/null && ACTIVE=1 || true
systemctl is-enabled --quiet "${UNIT}" 2>/dev/null && ENABLED=1 || true

mkdir -p "${ROLLBACK_DIR}"
chmod 700 "${ROLLBACK_DIR}"
umask 077
rm -f "${GENERATION}"

printf '%s\n' "${SHA}" > "${ROLLBACK_DIR}/previous-release.sha"
printf '%s\n' "${TREE}" > "${ROLLBACK_DIR}/previous-tree.sha"
printf '%s\n' "${ACTIVE}" > "${ROLLBACK_DIR}/previous-active"
printf '%s\n' "${ENABLED}" > "${ROLLBACK_DIR}/previous-enabled"
cat "${UNIT_FILE}" > "${ROLLBACK_DIR}/${UNIT}"
chmod 600 "${ROLLBACK_DIR}/previous-release.sha" "${ROLLBACK_DIR}/previous-tree.sha" \
  "${ROLLBACK_DIR}/previous-active" "${ROLLBACK_DIR}/previous-enabled" "${ROLLBACK_DIR}/${UNIT}"

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
    os.replace(tmp, target); os.chmod(target, 0o600)
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
chmod 600 "${ROLLBACK_DIR}/previous-db-present" "${ROLLBACK_DIR}/previous-db.sha256"

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
chmod 600 "${ROLLBACK_DIR}/previous-venv-release.sha"

# Recheck release identity immediately before publishing the generation marker.
/usr/bin/python3 "${GATE}" verify-checkout --app-dir "${APP_DIR}" --release-file "${RELEASE_FILE}"
[[ "$(git -C "${APP_DIR}" rev-parse HEAD^{tree})" == "${TREE}" ]] || fail "checkout tree changed during snapshot"
printf '%s\n' 'all-paper-rollback-v3-host-authority' > "${GENERATION}"
chmod 600 "${GENERATION}"
printf 'PASS: host-owned rollback snapshot captured for %s (%s).\n' "${SHA}" "${TREE}"
