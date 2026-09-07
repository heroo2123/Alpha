#!/usr/bin/env bash
set -Eeuo pipefail

# Pin a checked-out Alpha repository to an explicitly authorized immutable commit.
# Production deploy/update scripts call this before installing dependencies or
# touching systemd. Merely being the tip of mutable `main` is never authorization.

APP_DIR="${1:-}"
RELEASE_SHA="${2:-}"
RELEASE_FILE="${3:-}"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -n "${APP_DIR}" && -d "${APP_DIR}/.git" ]] || fail "release-pin: repository path is missing or not a git checkout"
[[ "${RELEASE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] || fail "release-pin: release SHA must be exactly 40 hexadecimal characters"
[[ -n "${RELEASE_FILE}" ]] || fail "release-pin: release marker path is required"

# Fetch the canonical branch only to prove that the requested immutable commit is
# part of its history. The deploy target is the SHA itself, never origin/main.
git -C "${APP_DIR}" fetch --prune origin main
git -C "${APP_DIR}" cat-file -e "${RELEASE_SHA}^{commit}" 2>/dev/null \
  || fail "release-pin: requested commit is not present after fetching origin/main"
git -C "${APP_DIR}" merge-base --is-ancestor "${RELEASE_SHA}" origin/main \
  || fail "release-pin: requested commit is not an ancestor of origin/main"

git -C "${APP_DIR}" reset --hard "${RELEASE_SHA}"
ACTUAL_SHA="$(git -C "${APP_DIR}" rev-parse HEAD)"
[[ "${ACTUAL_SHA}" == "${RELEASE_SHA,,}" ]] \
  || fail "release-pin: checkout attestation failed (${ACTUAL_SHA} != ${RELEASE_SHA,,})"

# Only commits carrying the fail-closed production runtime are eligible. This also
# prevents accidentally pinning a pre-hardening historical commit simply because it
# is an ancestor of main.
[[ -f "${APP_DIR}/app_trade_only.py" ]] || fail "release-pin: pinned commit lacks app_trade_only.py"
[[ -f "${APP_DIR}/command_worker_trade_only.py" ]] || fail "release-pin: pinned commit lacks command_worker_trade_only.py"

mkdir -p "$(dirname "${RELEASE_FILE}")"
printf '%s\n' "${ACTUAL_SHA}" > "${RELEASE_FILE}"
chmod 600 "${RELEASE_FILE}"
printf 'Immutable release pinned: %s\n' "${ACTUAL_SHA}"
