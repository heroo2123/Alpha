#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="https://github.com/heroo2123/Alpha.git"
APP_NAME="polymarket-edge-scanner"
APP_DIR="${HOME}/${APP_NAME}"
DATA_DIR="${HOME}/.${APP_NAME}/data"
CONFIG_DIR="${HOME}/.${APP_NAME}"
ENV_FILE="${CONFIG_DIR}/bot.env"
SERVICE_NAME="${APP_NAME}.service"
CURRENT_USER="$(id -un)"

say() { printf '\n\033[1;36m%s\033[0m\n' "$*"; }
fail() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

if [[ "${EUID}" -eq 0 ]]; then
  fail "Run this installer as the normal SSH user (ubuntu), not as root. It will use sudo when needed."
fi

say "Installing system packages"
if command -v apt-get >/dev/null 2>&1; then
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates python3 python3-venv python3-pip
elif command -v dnf >/dev/null 2>&1; then
  sudo dnf install -y git curl ca-certificates python3 python3-pip
else
  fail "Unsupported Linux image. Use Canonical Ubuntu on the Oracle VM."
fi

python3 - <<'PY'
import sys
if sys.version_info < (3, 10):
    raise SystemExit("Python 3.10+ is required. Use the current Canonical Ubuntu image in OCI.")
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

say "Creating systemd 24/7 service"
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
sudo systemctl daemon-reload
sudo systemctl enable --now "${SERVICE_NAME}"

sleep 3
if sudo systemctl is-active --quiet "${SERVICE_NAME}"; then
  say "SUCCESS: scanner is running 24/7"
else
  sudo systemctl status "${SERVICE_NAME}" --no-pager || true
  fail "Service did not start. See logs with: sudo journalctl -u ${SERVICE_NAME} -n 100 --no-pager"
fi

printf '\nUseful commands:\n'
printf '  Status:  sudo systemctl status %s\n' "${SERVICE_NAME}"
printf '  Logs:    sudo journalctl -u %s -f\n' "${SERVICE_NAME}"
printf '  Health:  curl http://127.0.0.1:8000/health\n'
printf '  Update:  %s/deploy/oracle/update.sh\n' "${APP_DIR}"

if [[ -z "${TELEGRAM_CHAT_ID}" ]]; then
  printf '\nNEXT: open your Telegram bot and send /whoami. Then run:\n'
  printf '  %s/deploy/oracle/set-chat-id.sh YOUR_CHAT_ID\n' "${APP_DIR}"
else
  printf '\nTelegram chat ID was configured. Send /help to the bot to test it.\n'
fi
