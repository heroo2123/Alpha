#!/usr/bin/env bash
set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/env -i PATH=/usr/bin:/bin HOME="${HOME}" LANG=C.UTF-8 GIT_CONFIG_NOSYSTEM=1 \
  /bin/bash --noprofile --norc "${SCRIPT_DIR}/snapshot-all-paper-rollback.sh" "$@"
