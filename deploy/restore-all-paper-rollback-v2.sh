#!/usr/bin/env bash
set -Eeuo pipefail
HOST_RECOVERY="/usr/local/libexec/polymarket-weather-paper/restore-rollback.sh"
[[ -f "${HOST_RECOVERY}" ]] || { echo 'HOST ROLLBACK AUTHORITY NOT INSTALLED' >&2; exit 2; }
exec bash "${HOST_RECOVERY}" "$@"
