#!/usr/bin/env bash
set -Eeuo pipefail

# Read-only guard: the e2-micro must not run the older scanner/research stack beside
# the canonical weather PAPER service. With --require-disabled, also reject units that
# would come back automatically after a reboot. This script never stops/disables them.
REQUIRE_DISABLED=0
if [[ "${1:-}" == "--require-disabled" ]]; then
  REQUIRE_DISABLED=1
elif [[ -n "${1:-}" ]]; then
  echo "usage: $0 [--require-disabled]" >&2
  exit 2
fi

legacy_units=(
  polymarket-edge-scanner.service
  polymarket-edge-command.service
  polymarket-universe-builder.service
  polymarket-weather-shadow.service
  polymarket-weather-calibration.service
)

failed=0
for unit in "${legacy_units[@]}"; do
  if systemctl is-active --quiet "${unit}" 2>/dev/null; then
    printf 'BLOCKED: legacy service is active: %s\n' "${unit}" >&2
    failed=1
  fi
  if (( REQUIRE_DISABLED == 1 )) && systemctl is-enabled --quiet "${unit}" 2>/dev/null; then
    printf 'BLOCKED: legacy service is enabled for persistence: %s\n' "${unit}" >&2
    failed=1
  fi
done

if (( failed != 0 )); then
  echo 'Weather PAPER isolation gate failed. No legacy service was changed automatically.' >&2
  exit 2
fi

if (( REQUIRE_DISABLED == 1 )); then
  echo 'PASS: legacy scanner/research services are inactive and not enabled.'
else
  echo 'PASS: legacy scanner/research services are inactive.'
fi
