#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${1:-}"
RELEASE_FILE="${2:-}"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -n "${APP_DIR}" && -d "${APP_DIR}/.git" ]] || fail "runtime release check: invalid app checkout"
[[ -n "${RELEASE_FILE}" && -f "${RELEASE_FILE}" ]] || fail "runtime release check: immutable release marker missing"

EXPECTED_SHA="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
ACTUAL_SHA="$(git -C "${APP_DIR}" rev-parse HEAD)"
[[ "${EXPECTED_SHA}" =~ ^[0-9a-f]{40}$ ]] || fail "runtime release check: release marker malformed"
[[ "${ACTUAL_SHA}" == "${EXPECTED_SHA}" ]] \
  || fail "runtime release check: checkout ${ACTUAL_SHA} does not match authorized ${EXPECTED_SHA}"

# A modified tracked file OR an unexpected untracked Python/importable file can alter
# the executed application without changing HEAD. Git-ignored runtime artifacts such
# as .venv remain ignored, but every visible untracked path fails the release gate.
[[ -z "$(git -C "${APP_DIR}" status --porcelain --untracked-files=all)" ]] \
  || fail "runtime release check: tracked working tree differs or untracked import surface exists"

printf 'Runtime release attested: %s\n' "${ACTUAL_SHA}"
