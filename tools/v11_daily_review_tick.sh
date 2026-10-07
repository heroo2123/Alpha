#!/usr/bin/env bash
set -u
STATE=/var/lib/alpha-v11/daily-review-authority
TMP="$STATE/last-status.$$.tmp"
OUT="$STATE/last-status.json"
RC=0
/usr/bin/python3 /usr/local/libexec/alpha-v11-daily-review-authority.py publish-current   --policy /etc/alpha-v11/daily-review-authority/policy.json >"$TMP" 2>&1 || RC=$?
/bin/chmod 0644 "$TMP"
/bin/mv -f "$TMP" "$OUT"
exit "$RC"
