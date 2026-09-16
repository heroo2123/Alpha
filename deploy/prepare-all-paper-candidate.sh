#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
exec /bin/bash "${APP_DIR}/deploy/prepare-all-paper-candidate-v5.sh" "$@"
