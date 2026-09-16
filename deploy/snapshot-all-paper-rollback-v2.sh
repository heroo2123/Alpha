#!/usr/bin/env bash
set -Eeuo pipefail
HOST_SNAPSHOT="/usr/local/libexec/polymarket-weather-paper/snapshot-rollback.sh"
[[ -f "${HOST_SNAPSHOT}" ]] || { echo 'HOST ROLLBACK SNAPSHOT AUTHORITY NOT INSTALLED' >&2; exit 2; }
exec bash "${HOST_SNAPSHOT}" "$@"
