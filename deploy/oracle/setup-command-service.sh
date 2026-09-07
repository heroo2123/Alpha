#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="polymarket-edge-scanner"
APP_DIR="${HOME}/${APP_NAME}"
CONFIG_DIR="${HOME}/.${APP_NAME}"
ENV_FILE="${CONFIG_DIR}/bot.env"
RELEASE_FILE="${CONFIG_DIR}/release.sha"
SCANNER_SERVICE="${APP_NAME}.service"
COMMAND_SERVICE="polymarket-edge-command.service"
CURRENT_USER="$(id -un)"

[[ -d "${APP_DIR}" ]] || { echo "Missing app directory: ${APP_DIR}" >&2; exit 1; }
[[ -f "${ENV_FILE}" ]] || { echo "Missing env file: ${ENV_FILE}" >&2; exit 1; }
[[ -f "${RELEASE_FILE}" ]] || { echo "Missing immutable release marker: ${RELEASE_FILE}" >&2; exit 1; }
[[ -x "${APP_DIR}/.venv/bin/python" ]] || { echo "Missing virtualenv: ${APP_DIR}/.venv" >&2; exit 1; }
[[ -f "${APP_DIR}/deploy/verify-runtime-release.sh" ]] || { echo "Missing runtime release verifier" >&2; exit 1; }

bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"

"${APP_DIR}/.venv/bin/python" - <<'PY'
from polymarket_scanner.trade_only import promoted_detectors
assert promoted_detectors() == (), f"P0 containment violated: promoted detectors={promoted_detectors()}"
print("P0 containment verified: 0 promoted TRADE NOW detectors")
PY

# Force both safety-critical scanner policy and the single getUpdates owner through
# a systemd drop-in, even if an older base installer wrote a legacy ExecStart.
sudo mkdir -p "/etc/systemd/system/${SCANNER_SERVICE}.d"
TMP_DROPIN="$(mktemp)"
cat > "${TMP_DROPIN}" <<EOF
[Service]
Environment=TELEGRAM_COMMANDS_IN_APP=false
ExecStartPre=
ExecStartPre=/bin/bash ${APP_DIR}/deploy/verify-runtime-release.sh ${APP_DIR} ${RELEASE_FILE}
ExecStart=
ExecStart=${APP_DIR}/.venv/bin/uvicorn app_trade_only:app --host 127.0.0.1 --port 8000
EOF
sudo install -m 0644 "${TMP_DROPIN}" "/etc/systemd/system/${SCANNER_SERVICE}.d/trade-only-policy.conf"
rm -f "${TMP_DROPIN}"
# Remove the older command-only drop-in if present; the canonical file above now
# owns the command setting and production entrypoint.
sudo rm -f "/etc/systemd/system/${SCANNER_SERVICE}.d/telegram-command-worker.conf"

TMP_SERVICE="$(mktemp)"
cat > "${TMP_SERVICE}" <<EOF
[Unit]
Description=Polymarket Edge Telegram Command Worker (trade-only policy)
Wants=network-online.target
After=network-online.target ${SCANNER_SERVICE}

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStartPre=/bin/bash ${APP_DIR}/deploy/verify-runtime-release.sh ${APP_DIR} ${RELEASE_FILE}
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/command_worker_trade_only.py
Restart=always
RestartSec=3
TimeoutStopSec=15
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
sudo install -m 0644 "${TMP_SERVICE}" "/etc/systemd/system/${COMMAND_SERVICE}"
rm -f "${TMP_SERVICE}"

sudo systemctl daemon-reload
# Stop the scanner first so there can never be two simultaneous getUpdates consumers
# during migration. Restart both processes so new code and policy are guaranteed live.
sudo systemctl stop "${SCANNER_SERVICE}" || true
sudo systemctl enable "${COMMAND_SERVICE}" "${SCANNER_SERVICE}" >/dev/null
sudo systemctl restart "${SCANNER_SERVICE}"
sleep 2
sudo systemctl restart "${COMMAND_SERVICE}"
sleep 3

echo
printf 'Command service: '
sudo systemctl is-active "${COMMAND_SERVICE}" || true
printf 'Scanner service: '
sudo systemctl is-active "${SCANNER_SERVICE}" || true

echo "Scanner ExecStart:"
sudo systemctl show -p ExecStart "${SCANNER_SERVICE}"
echo "Scanner ExecStartPre:"
sudo systemctl show -p ExecStartPre "${SCANNER_SERVICE}"
echo "Command ExecStart:"
sudo systemctl show -p ExecStart "${COMMAND_SERVICE}"
echo "Command ExecStartPre:"
sudo systemctl show -p ExecStartPre "${COMMAND_SERVICE}"
echo "Promoted TRADE NOW detectors: 0 (P0 containment)"
echo "Authorized immutable release: $(tr -d '[:space:]' < "${RELEASE_FILE}")"

echo
echo "Send /status now. The command worker is a separate OS process from the scanner."
echo "Command logs: sudo journalctl -u ${COMMAND_SERVICE} -n 50 --no-pager"
echo "Scanner logs: sudo journalctl -u ${SCANNER_SERVICE} -n 50 --no-pager"
