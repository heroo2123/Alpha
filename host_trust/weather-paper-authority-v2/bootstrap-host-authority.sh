#!/usr/bin/env bash
set -Eeuo pipefail
printf '%s\n' 'REFUSED: the retired v2 application reference cannot install or replace host authority. Use independently provisioned production host trust.' >&2
exit 40
