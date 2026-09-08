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

# Only commits carrying the complete fail-closed production + release-authority
# runtime are eligible. Merely containing app_trade_only.py is not enough. This
# prevents pinning historical revisions that could start without immutable-release,
# dependency-preflight, or runtime-manifest evidence.
[[ -f "${APP_DIR}/app_trade_only.py" ]] || fail "release-pin: pinned commit lacks app_trade_only.py"
[[ -f "${APP_DIR}/command_worker_trade_only.py" ]] || fail "release-pin: pinned commit lacks command_worker_trade_only.py"
[[ -f "${APP_DIR}/deploy/verify-runtime-release.sh" ]] || fail "release-pin: pinned commit predates runtime release attestation"
[[ -f "${APP_DIR}/polymarket_scanner/runtime_manifest.py" ]] || fail "release-pin: pinned commit predates runtime manifest attestation"
[[ -f "${APP_DIR}/polymarket_scanner/dependency_preflight.py" ]] || fail "release-pin: pinned commit predates dependency preflight authority"
[[ -f "${APP_DIR}/deploy/oracle/setup-command-service.sh" ]] || fail "release-pin: pinned commit lacks canonical command-service setup"
[[ -f "${APP_DIR}/deploy/oracle/install.sh" ]] || fail "release-pin: pinned commit lacks Oracle installer"
[[ -f "${APP_DIR}/deploy/oracle/update.sh" ]] || fail "release-pin: pinned commit lacks Oracle updater"
[[ -f "${APP_DIR}/deploy/gcp/install.sh" ]] || fail "release-pin: pinned commit lacks GCP installer"

grep -qF 'verify-runtime-release.sh' "${APP_DIR}/deploy/oracle/setup-command-service.sh" \
  || fail "release-pin: pinned Oracle service setup does not enforce runtime release attestation"
grep -qF 'app_trade_only:app' "${APP_DIR}/deploy/oracle/setup-command-service.sh" \
  || fail "release-pin: pinned Oracle service setup lacks canonical scanner entrypoint"
grep -qF 'command_worker_trade_only.py' "${APP_DIR}/deploy/oracle/setup-command-service.sh" \
  || fail "release-pin: pinned Oracle service setup lacks canonical command worker"
grep -qF 'build_runtime_manifest' "${APP_DIR}/app_trade_only.py" \
  || fail "release-pin: pinned production runtime does not expose runtime manifest attestation"
for script in \
  "${APP_DIR}/deploy/oracle/install.sh" \
  "${APP_DIR}/deploy/oracle/update.sh" \
  "${APP_DIR}/deploy/gcp/install.sh"
do
  grep -qF 'polymarket_scanner.dependency_preflight' "${script}" \
    || fail "release-pin: pinned deploy path lacks dependency preflight gate (${script})"
  grep -qF -- '--required-only' "${script}" \
    || fail "release-pin: pinned deploy path does not fail closed on required dependencies (${script})"
  grep -qF -- '--release-sha' "${script}" \
    || fail "release-pin: pinned deploy preflight is not release-bound (${script})"
done

mkdir -p "$(dirname "${RELEASE_FILE}")"
printf '%s\n' "${ACTUAL_SHA}" > "${RELEASE_FILE}"
chmod 600 "${RELEASE_FILE}"
printf 'Immutable release pinned: %s\n' "${ACTUAL_SHA}"
