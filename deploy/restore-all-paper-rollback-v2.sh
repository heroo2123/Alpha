#!/usr/bin/env bash
# RETIRED_PRODUCTION_DEPLOYMENT_ENTRYPOINT: historical body below is unreachable.
printf '%s\n' 'REFUSED: retired PAPER deployment path; use the independently provisioned host protocol in deploy/production-host-control.sh and docs/PRODUCTION_HOST_TRUST.md' >&2
exit 40

set -Eeuo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec /usr/bin/env -i PATH=/usr/bin:/bin HOME="${HOME}" LANG=C.UTF-8 GIT_CONFIG_NOSYSTEM=1 \
  /bin/bash --noprofile --norc "${SCRIPT_DIR}/restore-all-paper-rollback.sh" "$@"
