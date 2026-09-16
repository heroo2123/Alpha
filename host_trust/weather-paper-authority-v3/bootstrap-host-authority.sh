#!/usr/bin/env bash
set -Eeuo pipefail
# REFERENCE BOOTSTRAP ONLY. The authority source, expected authority digest, policy
# document, and expected policy digest must be obtained/approved independently from
# the application candidate being deployed.
SOURCE="${1:-}"
EXPECTED_AUTH="${2:-}"
POLICY="${3:-}"
EXPECTED_POLICY="${4:-}"
[[ -f "${SOURCE}" && "${EXPECTED_AUTH}" =~ ^[0-9a-f]{64}$ && -f "${POLICY}" && "${EXPECTED_POLICY}" =~ ^[0-9a-f]{64}$ ]] || {
  echo "usage: $0 <independent-authority.py> <authority-sha256> <independent-policy.json> <policy-sha256>" >&2
  exit 2
}
[[ "$(sha256sum "${SOURCE}" | awk '{print $1}')" == "${EXPECTED_AUTH}" ]] || { echo "authority digest mismatch" >&2; exit 3; }
CANON="$(python3 - "${POLICY}" <<'PY'
import hashlib,json,sys
raw=json.load(open(sys.argv[1],encoding='utf-8'))
if not isinstance(raw,dict): raise SystemExit('policy must be object')
data=(json.dumps(raw,sort_keys=True,separators=(',',':'),ensure_ascii=True)+'\n').encode()
print(hashlib.sha256(data).hexdigest())
PY
)"
[[ "${CANON}" == "${EXPECTED_POLICY}" ]] || { echo "policy digest mismatch" >&2; exit 4; }
DEST=/usr/local/libexec/polymarket-weather-paper-v3
ETC=/etc/polymarket-weather-paper
RUNTIME=/var/lib/polymarket-weather-paper-runtime
ROLLBACK=/var/lib/polymarket-weather-paper-rollback/generations-v3
sudo install -d -o root -g root -m 0755 "${DEST}" "${ETC}" "${RUNTIME}" /var/lib/polymarket-weather-paper-rollback
sudo install -d -o root -g root -m 0700 "${ROLLBACK}"
sudo install -o root -g root -m 0555 "${SOURCE}" "${DEST}/authority.py"
TMP="$(mktemp)"; trap 'rm -f "${TMP}"' EXIT
python3 - "${EXPECTED_AUTH}" "${EXPECTED_POLICY}" "${POLICY}" >"${TMP}" <<'PY'
import json,sys
auth,policy_sha,path=sys.argv[1:]
policy=json.load(open(path,encoding='utf-8'))
print(json.dumps({'version':'weather-paper-host-authority-v3-immutable-runtime','authority_sha256':auth,'policy_sha256':policy_sha,'policy':policy},sort_keys=True,indent=2))
PY
sudo install -o root -g root -m 0444 "${TMP}" "${ETC}/authority-anchor-v3.json"
/usr/bin/python3 "${DEST}/authority.py" authority-info >/dev/null
printf 'PASS: independently pinned weather PAPER host authority v3 installed: %s policy=%s\n' "${EXPECTED_AUTH}" "${EXPECTED_POLICY}"
