#!/usr/bin/env bash
set -Eeuo pipefail
HOST_RECOVERY="/usr/local/libexec/polymarket-weather-paper/restore-rollback.sh"
[[ -f "${HOST_RECOVERY}" ]] || { echo 'HOST ROLLBACK AUTHORITY NOT INSTALLED' >&2; exit 2; }
exec /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 \
  /bin/bash --noprofile --norc "${HOST_RECOVERY}" "$@"
