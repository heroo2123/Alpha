#!/usr/bin/env bash
# RETIRED_PRODUCTION_DEPLOYMENT_ENTRYPOINT: historical body below is unreachable.
printf '%s\n' 'REFUSED: retired PAPER deployment path; use the independently provisioned host protocol in deploy/production-host-control.sh and docs/PRODUCTION_HOST_TRUST.md' >&2
exit 40

set -Eeuo pipefail
AUTH=/usr/local/libexec/polymarket-weather-paper-v3/authority.py
GEN="${1:-}"
[[ "${GEN}" =~ ^[0-9a-f]{64}$ ]] || { echo "usage: $0 <cutover-generation-id>" >&2; exit 2; }
[[ -f "${AUTH}" ]] || { echo "INDEPENDENT HOST AUTHORITY V3 NOT INSTALLED" >&2; exit 3; }
exec sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 GIT_CONFIG_NOSYSTEM=1 \
  /usr/bin/python3 "${AUTH}" recover --generation-id "${GEN}"
