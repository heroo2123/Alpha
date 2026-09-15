#!/usr/bin/env bash
set -Eeuo pipefail

# Capture the exact known-good weather PAPER release before any cutover mutation.
# This script is read-only with respect to the running service: SQLite's online backup
# API obtains a coherent ledger snapshot without stopping or rewriting production.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
DB_PATH="${WEATHER_PAPER_DB_PATH:-/var/lib/polymarket-weather-paper/weather-paper.sqlite}"
UNIT="polymarket-weather-paper.service"
UNIT_FILE="/etc/systemd/system/${UNIT}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
ROLLBACK_DIR="${CONFIG_DIR}/all-paper-rollback"
ROLLBACK_SHA="${ROLLBACK_DIR}/previous-release.sha"
ROLLBACK_UNIT="${ROLLBACK_DIR}/${UNIT}"
ROLLBACK_ACTIVE="${ROLLBACK_DIR}/previous-active"
ROLLBACK_ENABLED="${ROLLBACK_DIR}/previous-enabled"
ROLLBACK_DB_PRESENT="${ROLLBACK_DIR}/previous-db-present"
ROLLBACK_DB="${ROLLBACK_DIR}/previous-weather-paper.sqlite3"
ROLLBACK_DB_MANIFEST="${ROLLBACK_DIR}/previous-weather-paper.sqlite3.json"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ -d "${APP_DIR}/.git" ]] || fail "missing weather-paper checkout"
[[ -x "${APP_DIR}/.venv/bin/python" ]] || fail "missing weather-paper virtualenv"
[[ -f "${RELEASE_FILE}" ]] || fail "missing current release marker"
[[ -f "${UNIT_FILE}" ]] || fail "missing installed weather PAPER unit"

HEAD_SHA="$(git -C "${APP_DIR}" rev-parse HEAD | tr -d '[:space:]')"
MARKER_SHA="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${HEAD_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "current checkout SHA invalid"
[[ "${MARKER_SHA}" == "${HEAD_SHA}" ]] || fail "current checkout/release marker mismatch"
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] \
  || fail "current weather-paper checkout is dirty"

ACTIVE=0
ENABLED=0
systemctl is-active --quiet "${UNIT}" 2>/dev/null && ACTIVE=1 || true
systemctl is-enabled --quiet "${UNIT}" 2>/dev/null && ENABLED=1 || true

mkdir -p "${ROLLBACK_DIR}"
chmod 700 "${ROLLBACK_DIR}"
umask 077
TMP_SHA="$(mktemp "${ROLLBACK_DIR}/.previous-release.XXXXXX")"
TMP_UNIT="$(mktemp "${ROLLBACK_DIR}/.previous-unit.XXXXXX")"
TMP_ACTIVE="$(mktemp "${ROLLBACK_DIR}/.previous-active.XXXXXX")"
TMP_ENABLED="$(mktemp "${ROLLBACK_DIR}/.previous-enabled.XXXXXX")"
TMP_DB_PRESENT="$(mktemp "${ROLLBACK_DIR}/.previous-db-present.XXXXXX")"
cleanup(){ rm -f "${TMP_SHA}" "${TMP_UNIT}" "${TMP_ACTIVE}" "${TMP_ENABLED}" "${TMP_DB_PRESENT}"; }
trap cleanup EXIT

# Snapshot database state first. If verification fails, publish none of the new
# rollback identity files, leaving the prior rollback generation intact.
if [[ -f "${DB_PATH}" ]]; then
  PYTHONPATH="${APP_DIR}${PYTHONPATH:+:${PYTHONPATH}}" \
  "${APP_DIR}/.venv/bin/python" - "${DB_PATH}" "${ROLLBACK_DB}" "${ROLLBACK_DB_MANIFEST}" <<'PY'
import json
import os
import sqlite3
import sys
from pathlib import Path
from polymarket_scanner.weather_only_paper_backup import (
    verify_weather_paper_database,
    verify_weather_paper_restore,
)

source = Path(sys.argv[1]).resolve()
destination = Path(sys.argv[2]).resolve()
manifest_path = Path(sys.argv[3]).resolve()
temporary = destination.with_name("." + destination.name + f".tmp-{os.getpid()}")
source_profile = verify_weather_paper_database(source)
try:
    temporary.unlink(missing_ok=True)
    src = sqlite3.connect(f"file:{source}?mode=ro", uri=True, timeout=5.0)
    dst = sqlite3.connect(temporary, timeout=5.0)
    try:
        src.execute("PRAGMA busy_timeout=5000")
        dst.execute("PRAGMA busy_timeout=5000")
        src.backup(dst, pages=256, sleep=0.01)
    finally:
        dst.close()
        src.close()
    copied = verify_weather_paper_database(temporary)
    if copied["schema_sha256"] != source_profile["schema_sha256"]:
        raise SystemExit("rollback DB schema mismatch")
    if copied["logical_tables"] != source_profile["logical_tables"]:
        raise SystemExit("rollback DB logical-content mismatch")
    os.replace(temporary, destination)
    os.chmod(destination, 0o600)
    restore = verify_weather_paper_restore(destination)
    if restore.get("restore_verified") is not True:
        raise SystemExit("rollback DB restore verification failed")
    manifest = {
        "source": str(source),
        "backup": str(destination),
        "schema_profile": source_profile["schema_profile"],
        "schema_sha256": source_profile["schema_sha256"],
        "logical_tables": source_profile["logical_tables"],
        "restore_verified": True,
    }
    tmp_manifest = manifest_path.with_name("." + manifest_path.name + f".tmp-{os.getpid()}")
    tmp_manifest.write_text(json.dumps(manifest, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp_manifest, 0o600)
    os.replace(tmp_manifest, manifest_path)
finally:
    temporary.unlink(missing_ok=True)
PY
  printf '1\n' > "${TMP_DB_PRESENT}"
else
  rm -f "${ROLLBACK_DB}" "${ROLLBACK_DB_MANIFEST}"
  printf '0\n' > "${TMP_DB_PRESENT}"
fi

printf '%s\n' "${HEAD_SHA}" > "${TMP_SHA}"
cat "${UNIT_FILE}" > "${TMP_UNIT}"
printf '%s\n' "${ACTIVE}" > "${TMP_ACTIVE}"
printf '%s\n' "${ENABLED}" > "${TMP_ENABLED}"
chmod 600 "${TMP_SHA}" "${TMP_UNIT}" "${TMP_ACTIVE}" "${TMP_ENABLED}" "${TMP_DB_PRESENT}"
mv -f "${TMP_SHA}" "${ROLLBACK_SHA}"
mv -f "${TMP_UNIT}" "${ROLLBACK_UNIT}"
mv -f "${TMP_ACTIVE}" "${ROLLBACK_ACTIVE}"
mv -f "${TMP_ENABLED}" "${ROLLBACK_ENABLED}"
mv -f "${TMP_DB_PRESENT}" "${ROLLBACK_DB_PRESENT}"
trap - EXIT

printf 'Rollback snapshot captured without changing production.\n'
printf 'Release: %s\n' "${HEAD_SHA}"
printf 'Was active: %s | was enabled: %s | DB present: %s\n' "${ACTIVE}" "${ENABLED}" "$(tr -d '[:space:]' < "${ROLLBACK_DB_PRESENT}")"
