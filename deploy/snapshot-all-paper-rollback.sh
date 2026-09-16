#!/usr/bin/env bash
# RETIRED_PRODUCTION_DEPLOYMENT_ENTRYPOINT: historical body below is unreachable.
printf '%s\n' 'REFUSED: retired PAPER deployment path; use the independently provisioned host protocol in deploy/production-host-control.sh and docs/PRODUCTION_HOST_TRUST.md' >&2
exit 40

set -Eeuo pipefail
AUTH=/usr/local/libexec/polymarket-weather-paper-v3/authority.py
CANDIDATE_SHA="${1:-}"
[[ "${CANDIDATE_SHA}" =~ ^[0-9a-f]{40}$ ]] || { echo "usage: $0 <candidate-sha>" >&2; exit 2; }
[[ -x "${AUTH}" ]] || { echo "independently pinned host authority v3 is not installed" >&2; exit 3; }
# The independent authority derives predecessor identity and every privileged path
# exclusively from its root-owned policy anchor. The candidate supplies only the SHA.
exec sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 \
  /usr/bin/python3 "${AUTH}" create-cutover --candidate-sha "${CANDIDATE_SHA}"
