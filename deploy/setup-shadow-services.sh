#!/usr/bin/env bash
set -Eeuo pipefail

# Install reviewed unit definitions ONLY. Starting/enabling services is a separate,
# explicit deployment step. Does not edit bot.env, networking, swap, or cloud resources.
APP_DIR="${ALPHA_APP_DIR:-${HOME}/polymarket-edge-scanner}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
CURRENT_USER="$(id -un)"
export ALPHA_CONFIG_DIR="${CONFIG_DIR}"
[[ -f "${CONFIG_DIR}/bot.env" ]] || { echo 'Existing bot.env is required' >&2; exit 1; }
bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${CONFIG_DIR}/release.sha"
cd "${APP_DIR}"
"${APP_DIR}/.venv/bin/python" -m polymarket_scanner.shadow_preflight
for unit in polymarket-edge-scanner polymarket-edge-command polymarket-universe-builder polymarket-weather-calibration; do
  if systemctl is-active --quiet "${unit}.service"; then
    echo "Stop ${unit} explicitly before installing its replacement definition" >&2
    exit 1
  fi
done
mkdir -p "${CONFIG_DIR}/universe"
chmod 700 "${CONFIG_DIR}/universe"
UNIT_DIR="$(mktemp -d)"
trap 'rm -rf "${UNIT_DIR}"' EXIT
"${APP_DIR}/.venv/bin/python" deploy/render-shadow-units.py --app-dir "${APP_DIR}" \
  --config-dir "${CONFIG_DIR}" --user "${CURRENT_USER}" --output-dir "${UNIT_DIR}"
systemd-analyze verify "${UNIT_DIR}"/*

# Known historical overrides are retired. Unknown overrides are not silently
# deleted: the migration must inventory them before starting any service.
for name in trade-only-policy.conf telegram-command-worker.conf; do
  sudo rm -f "/etc/systemd/system/polymarket-edge-scanner.service.d/${name}"
done
for unit in polymarket-edge-scanner polymarket-edge-command polymarket-universe-builder polymarket-weather-calibration; do
  if sudo find "/etc/systemd/system/${unit}.service.d" -maxdepth 1 -name '*.conf' -print 2>/dev/null | grep -q .; then
    echo "Unreviewed systemd overrides remain for ${unit}; inspect them before migration" >&2
    exit 1
  fi
done
sudo install -m 0644 "${UNIT_DIR}"/* /etc/systemd/system/
sudo systemctl daemon-reload
echo 'Shadow/research units installed. No services were enabled or started.'
echo 'Weather calibration has no bot.env/Telegram/trading credentials in its reviewed unit.'
echo 'Verify the final systemctl cat/show output before the separately authorized start.'
