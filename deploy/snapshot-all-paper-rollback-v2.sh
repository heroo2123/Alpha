#!/usr/bin/env bash
set -Eeuo pipefail
HOST_SNAPSHOT="/usr/local/libexec/polymarket-weather-paper/snapshot-rollback.sh"
[[ -f "${HOST_SNAPSHOT}" ]] || { echo 'HOST ROLLBACK SNAPSHOT AUTHORITY NOT INSTALLED' >&2; exit 2; }
exec /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 \
  /bin/bash --noprofile --norc "${HOST_SNAPSHOT}" "$@"
