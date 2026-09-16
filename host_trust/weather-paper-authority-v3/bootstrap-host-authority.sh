#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\n' 'REFUSED: this application repository cannot bootstrap, approve, install, or update the host authority.' >&2
printf '%s\n' 'Obtain the authority implementation, trust anchor, host policy, and release approvals through an independently reviewed operator-controlled provisioning process.' >&2
printf '%s\n' 'A digest generated from this candidate is not evidence of independent provenance. See docs/PRODUCTION_HOST_TRUST.md.' >&2
exit 40
