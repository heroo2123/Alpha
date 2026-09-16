#!/usr/bin/env bash
set -Eeuo pipefail
# This bootstrap is for an independently downloaded/pinned host-authority bundle.
# DO NOT invoke the copy from an application candidate checkout as trust evidence.
SOURCE="${1:-}"
EXPECTED="${2:-}"
[[ -f "${SOURCE}" && "${EXPECTED}" =~ ^[0-9a-f]{64}$ ]] || { echo "usage: $0 <independently-pinned-authority.py> <expected-sha256>" >&2; exit 2; }
ACTUAL="$(sha256sum "${SOURCE}" | awk '{print $1}')"
[[ "${ACTUAL}" == "${EXPECTED}" ]] || { echo "authority digest mismatch" >&2; exit 3; }
DEST=/usr/local/libexec/polymarket-weather-paper-v2
ETC=/etc/polymarket-weather-paper
sudo install -d -o root -g root -m 0755 "${DEST}" "${ETC}"
sudo install -o root -g root -m 0555 "${SOURCE}" "${DEST}/authority.py"
TMP="$(mktemp)"; trap 'rm -f "${TMP}"' EXIT
python3 - "${EXPECTED}" >"${TMP}" <<'PY2'
import json,sys
print(json.dumps({'version':'weather-paper-host-authority-v2-independent-cutover','authority_sha256':sys.argv[1]},sort_keys=True,indent=2))
PY2
sudo install -o root -g root -m 0444 "${TMP}" "${ETC}/authority-anchor-v2.json"
/usr/bin/python3 "${DEST}/authority.py" authority-info >/dev/null
printf 'PASS: independently pinned host authority v2 installed: %s\n' "${EXPECTED}"
