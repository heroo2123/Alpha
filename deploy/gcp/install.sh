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
SWAPFILE="/swapfile"

say(){ printf '\n\033[1;36m%s\033[0m\n' "$*"; }
fail(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -ne 0 ]] || fail "Run as the normal SSH user, not root."

say "Installing system packages"
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates python3 python3-venv python3-pip gnupg lsb-release

say "Adding 2 GB swap for the 1 GB e2-micro VM"
if ! swapon --show | grep -q "${SWAPFILE}"; then
  if [[ ! -f "${SWAPFILE}" ]]; then
    sudo fallocate -l 2G "${SWAPFILE}" || sudo dd if=/dev/zero of="${SWAPFILE}" bs=1M count=2048 status=progress
    sudo chmod 600 "${SWAPFILE}"
    sudo mkswap "${SWAPFILE}"
  fi
  sudo swapon "${SWAPFILE}"
fi
if ! grep -qF "${SWAPFILE} none swap sw 0 0" /etc/fstab; then
  echo "${SWAPFILE} none swap sw 0 0" | sudo tee -a /etc/fstab >/dev/null
fi

say "Installing Cloudflare WARP client (connection will be enabled after install)"
curl -fsSL https://pkg.cloudflareclient.com/pubkey.gpg | sudo gpg --yes --dearmor --output /usr/share/keyrings/cloudflare-warp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/cloudflare-warp-archive-keyring.gpg] https://pkg.cloudflareclient.com/ $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/cloudflare-client.list >/dev/null
sudo apt-get update
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y cloudflare-warp

say "Installing ${APP_NAME}"
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
chmod 700 "${DATA_DIR}" "${CONFIG_DIR}"

say "Telegram configuration"
printf 'Paste TELEGRAM_BOT_TOKEN from @BotFather (hidden): ' >/dev/tty
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

say "Creating 24/7 systemd service"
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
sudo systemctl is-active --quiet "${SERVICE_NAME}" || {
  sudo systemctl status "${SERVICE_NAME}" --no-pager || true
  fail "Scanner service failed to start."
}

say "Base installation complete"
echo "Scanner health:"
curl -fsS http://127.0.0.1:8000/health || true
echo
free -h

echo
printf 'NEXT: enable Cloudflare WARP before removing the temporary Google external IPv4:\n'
printf '  warp-cli registration new\n'
printf '  warp-cli connect\n'
printf '  curl -s https://www.cloudflare.com/cdn-cgi/trace | grep warp=\n'
printf 'Do NOT remove Google IPv4 until the last command prints warp=on and the scanner health still works.\n'

if [[ -z "${TELEGRAM_CHAT_ID}" ]]; then
  printf '\nSend /whoami to your Telegram bot, then run:\n  %s/deploy/oracle/set-chat-id.sh YOUR_CHAT_ID\n' "${APP_DIR}"
fi
