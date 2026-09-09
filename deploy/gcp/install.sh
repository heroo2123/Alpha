#!/usr/bin/env bash
set -Eeuo pipefail
# Retired: provisioning, WARP installation, secret prompts and automatic restarts.
echo 'Use deploy/prepare-shadow-release.sh with an authorized SHA, then deploy/setup-shadow-services.sh.' >&2
echo 'See docs/SILENT_SHADOW_HANDOFF.md. This compatibility entrypoint makes no changes.' >&2
exit 2
