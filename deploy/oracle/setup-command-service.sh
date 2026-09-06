#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="polymarket-edge-scanner"
APP_DIR="${HOME}/${APP_NAME}"
CONFIG_DIR="${HOME}/.${APP_NAME}"
ENV_FILE="${CONFIG_DIR}/bot.env"
SCANNER_SERVICE="${APP_NAME}.service"
COMMAND_SERVICE="polymarket-edge-command.service"
CURRENT_USER="$(id -un)"

[[ -d "${APP_DIR}" ]] || { echo "Missing app directory: ${APP_DIR}" >&2; exit 1; }
[[ -f "${ENV_FILE}" ]] || { echo "Missing env file: ${ENV_FILE}" >&2; exit 1; }
[[ -x "${APP_DIR}/.venv/bin/python" ]] || { echo "Missing virtualenv: ${APP_DIR}/.venv" >&2; exit 1; }

# Scanner keeps Telegram alert sending, but must not call getUpdates anymore.
sudo mkdir -p "/etc/systemd/system/${SCANNER_SERVICE}.d"
TMP_DROPIN="$(mktemp)"
cat > "${TMP_DROPIN}" <<'EOF'
[Service]
Environment=TELEGRAM_COMMANDS_IN_APP=false
EOF
sudo install -m 0644 "${TMP_DROPIN}" "/etc/systemd/system/${SCANNER_SERVICE}.d/telegram-command-worker.conf"
rm -f "${TMP_DROPIN}"

TMP_SERVICE="$(mktemp)"
cat > "${TMP_SERVICE}" <<EOF
[Unit]
Description=Polymarket Edge Telegram Command Worker
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
ExecStart=${APP_DIR}/.venv/bin/python ${APP_DIR}/command_worker.py
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
# during the migration. Start the command owner, then restart the scanner with the
# in-process command loop disabled by the drop-in above.
sudo systemctl stop "${SCANNER_SERVICE}" || true
sudo systemctl enable --now "${COMMAND_SERVICE}"
sleep 2
sudo systemctl restart "${SCANNER_SERVICE}"
sleep 3

echo
printf 'Command service: '
sudo systemctl is-active "${COMMAND_SERVICE}" || true
printf 'Scanner service: '
sudo systemctl is-active "${SCANNER_SERVICE}" || true

echo
echo "Send /status now. The command worker is a separate OS process from the scanner."
echo "Command logs: sudo journalctl -u ${COMMAND_SERVICE} -n 50 --no-pager"
echo "Scanner logs: sudo journalctl -u ${SCANNER_SERVICE} -n 50 --no-pager"
