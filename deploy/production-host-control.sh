#!/usr/bin/env bash
# Candidate-side protocol client only. No authority source/digest/bootstrap input.
set -Eeuo pipefail
AUTH=/usr/local/libexec/polymarket-weather-paper-v3/authority.py
fail(){ printf 'PRODUCTION HOST CONTROL: %s\n' "$*" >&2; exit 2; }
[[ -f "${AUTH}" && ! -L "${AUTH}" ]] || fail 'independently provisioned host authority missing; see docs/PRODUCTION_HOST_TRUST.md'
COMMAND="${1:-}"
shift || true
case "${COMMAND}" in
  authority-info|create-cutover|prepare-candidate|activate-checkout|verify-generation|verify-runtime-files|verify-checkout|verify-process|finalize|recover) ;;
  *) fail 'expected a documented authority operation; this client cannot install/update authority or start services';;
esac
exec sudo /usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 \
  /usr/bin/python3 -I -s -E "${AUTH}" "${COMMAND}" "$@"
