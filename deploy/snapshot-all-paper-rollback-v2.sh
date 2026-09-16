#!/usr/bin/env bash
set -Eeuo pipefail
HOST_SNAPSHOT="/usr/local/libexec/polymarket-weather-paper/snapshot-rollback.sh"
[[ -f "${HOST_SNAPSHOT}" ]] || { echo 'HOST ROLLBACK SNAPSHOT AUTHORITY NOT INSTALLED' >&2; exit 2; }
# Snapshot custody is privileged: the root-owned authority writes only into the
# root-owned rollback directory pinned in /etc/polymarket-weather-paper/host-paths.conf.
# Keep HOME nonexistent so root's personal Git configuration cannot influence the
# host-authority snapshot path or repository verification.
exec sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent GIT_CONFIG_NOSYSTEM=1 \
  /bin/bash --noprofile --norc "${HOST_SNAPSHOT}" "$@"
