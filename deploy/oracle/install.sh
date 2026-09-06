#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="https://github.com/heroo2123/Alpha.git"
APP_NAME="polymarket-edge-scanner"
APP_DIR="${HOME}/${APP_NAME}"
DATA_DIR="${HOME}/.${APP_NAME}/data"
CONFIG_DIR="${HOME}/.${APP_NAME}"
ENV_FILE="${CONFIG_DIR}/bot.env"
SERVICE_NAME="${APP_NAME}.service"
COMMAND_SERVICE="polymarket-edge-command.service"
CURRENT_USER="$(id -un)"

say() { printf '\n\033[1;36m%s\033[0m\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

if [[ "${EUID}" -eq 0 ]]; then
  fail "Run this installer as the normal SSH user, not as root. It will use sudo when needed."
fi

say "Installing system packages"
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates python3 python3-venv python3-pip
elif command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y git curl ca-certificates python3 python3-pip
else
  fail "Unsupported Linux image. Use Canonical Ubuntu."
fi

python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required.")
print("Python", sys.version.split()[0], "OK")
PY

say "Installing ${APP_NAME} from GitHub"
if [[ -d "${APP_DIR}/.git" ]]; then
  git -C "${APP_DIR}" fetch origin main
  git -C "${APP_DIR}" reset --hard origin/main
else
  rm -rf "${APP_DIR}"
  git clone --depth 1 --branch main "${REPO_URL}" "${APP_DIR}"
fi
chmod +x "${APP_DIR}/deploy/oracle/"*.sh 2>/dev/null || true

python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

mkdir -p "${DATA_DIR}" "${CONFIG_DIR}"
chmod 700 "${CONFIG_DIR}" "${DATA_DIR}"

say "Telegram configuration"
printf 'Paste the Telegram bot token from @BotFather (input is hidden): ' >/dev/tty
IFS= read -r -s TELEGRAM_BOT_TOKEN </dev/tty
printf '\n' >/dev/tty
[[ -n "${TELEGRAM_BOT_TOKEN}" ]] || fail "Telegram bot token cannot be empty."

printf 'Telegram chat ID (leave blank if you still need /whoami): ' >/dev/tty
IFS= read -r TELEGRAM_CHAT_ID </dev/tty

cat > "${ENV_FILE}" <<EOF
TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}
TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}
DB_PATH=${DATA_DIR}/signals.db
SCAN_INTERVAL_SECONDS=15
UNIVERSE_REFRESH_SECONDS=120
WEATHER_REFRESH_SECONDS=60
ACTIONABLE_MIN_EDGE=0.025
PAPER_STAKE_USD=100
MARKET_WS_ENABLED=true
SPORTS_WS_ENABLED=true
CRYPTO_RTDS_ENABLED=true
EOF
chmod 600 "${ENV_FILE}"
unset TELEGRAM_BOT_TOKEN

say "Creating scanner systemd service"
TMP_SERVICE="$(mktemp)"
cat > "${TMP_SERVICE}" <<EOF
[Unit]
Description=Polymarket Edge Scanner
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
Environment=TELEGRAM_COMMANDS_IN_APP=false
ExecStart=${APP_DIR}/.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
sudo install -m 0644 "${TMP_SERVICE}" "/etc/systemd/system/${SERVICE_NAME}"
rm -f "${TMP_SERVICE}"

say "Creating isolated Telegram command service"
TMP_COMMAND="$(mktemp)"
cat > "${TMP_COMMAND}" <<EOF
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
sudo install -m 0644 "${TMP_COMMAND}" "/etc/systemd/system/${COMMAND_SERVICE}"
rm -f "${TMP_COMMAND}"

sudo systemctl daemon-reload
# Command worker is the only getUpdates consumer. Start it before the scanner.
sudo systemctl enable --now "${COMMAND_SERVICE}"
sudo systemctl enable --now "${SERVICE_NAME}"

sleep 3
if sudo systemctl is-active --quiet "${SERVICE_NAME}" && sudo systemctl is-active --quiet "${COMMAND_SERVICE}"; then
  say "SUCCESS: scanner and Telegram command worker are running 24/7"
else
  sudo systemctl status "${SERVICE_NAME}" --no-pager || true
  sudo systemctl status "${COMMAND_SERVICE}" --no-pager || true
  fail "One of the services did not start."
fi

printf '\nUseful commands:\n'
printf '  Scanner:  sudo systemctl status %s\n' "${SERVICE_NAME}"
printf '  Commands: sudo systemctl status %s\n' "${COMMAND_SERVICE}"
printf '  Scanner logs:  sudo journalctl -u %s -f\n' "${SERVICE_NAME}"
printf '  Command logs:  sudo journalctl -u %s -f\n' "${COMMAND_SERVICE}"
printf '  Health:  curl http://127.0.0.1:8000/health\n'
printf '  Update:  %s/deploy/oracle/update.sh\n' "${APP_DIR}"

if [[ -z "${TELEGRAM_CHAT_ID}" ]]; then
  printf '\nNEXT: open your Telegram bot and send /whoami. Then run:\n'
  printf '  %s/deploy/oracle/set-chat-id.sh YOUR_CHAT_ID\n' "${APP_DIR}"
else
  printf '\nTelegram chat ID was configured. Send /help to the bot to test it.\n'
fi
