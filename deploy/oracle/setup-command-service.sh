#!/usr/bin/env bash
set -Eeuo pipefail
echo 'Legacy service setup retired. Use deploy/setup-shadow-services.sh; service starts are explicit.' >&2
exit 2
