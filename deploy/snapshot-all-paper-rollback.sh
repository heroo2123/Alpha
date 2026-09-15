#!/usr/bin/env bash
set -Eeuo pipefail

# Capture the exact known-good weather PAPER release before any cutover mutation.
# This script is read-only with respect to the running service: it never stops,
# disables, starts, enables, or rewrites production.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
UNIT="polymarket-weather-paper.service"
UNIT_FILE="/etc/systemd/system/${UNIT}"
RELEASE_FILE="${CONFIG_DIR}/weather-paper-release.sha"
ROLLBACK_DIR="${CONFIG_DIR}/all-paper-rollback"
ROLLBACK_SHA="${ROLLBACK_DIR}/previous-release.sha"
ROLLBACK_UNIT="${ROLLBACK_DIR}/${UNIT}"
ROLLBACK_ACTIVE="${ROLLBACK_DIR}/previous-active"
ROLLBACK_ENABLED="${ROLLBACK_DIR}/previous-enabled"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ -d "${APP_DIR}/.git" ]] || fail "missing weather-paper checkout"
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
cleanup(){ rm -f "${TMP_SHA}" "${TMP_UNIT}" "${TMP_ACTIVE}" "${TMP_ENABLED}"; }
trap cleanup EXIT
printf '%s\n' "${HEAD_SHA}" > "${TMP_SHA}"
cat "${UNIT_FILE}" > "${TMP_UNIT}"
printf '%s\n' "${ACTIVE}" > "${TMP_ACTIVE}"
printf '%s\n' "${ENABLED}" > "${TMP_ENABLED}"
chmod 600 "${TMP_SHA}" "${TMP_UNIT}" "${TMP_ACTIVE}" "${TMP_ENABLED}"
mv -f "${TMP_SHA}" "${ROLLBACK_SHA}"
mv -f "${TMP_UNIT}" "${ROLLBACK_UNIT}"
mv -f "${TMP_ACTIVE}" "${ROLLBACK_ACTIVE}"
mv -f "${TMP_ENABLED}" "${ROLLBACK_ENABLED}"
trap - EXIT

printf 'Rollback snapshot captured without changing production.\n'
printf 'Release: %s\n' "${HEAD_SHA}"
printf 'Was active: %s | was enabled: %s\n' "${ACTIVE}" "${ENABLED}"
