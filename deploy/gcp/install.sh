#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="https://github.com/heroo2123/Alpha.git"
APP_NAME="polymarket-edge-scanner"
APP_DIR="${HOME}/${APP_NAME}"
DATA_DIR="${HOME}/.${APP_NAME}/data"
CONFIG_DIR="${HOME}/.${APP_NAME}"
ENV_FILE="${CONFIG_DIR}/bot.env"
RELEASE_FILE="${CONFIG_DIR}/release.sha"
PREFLIGHT_FILE="${CONFIG_DIR}/dependency-preflight.json"
SCANNER_SERVICE="${APP_NAME}.service"
COMMAND_SERVICE="polymarket-edge-command.service"
CURRENT_USER="$(id -un)"
SWAPFILE="/swapfile"
RELEASE_SHA="${ALPHA_RELEASE_SHA:-${1:-}}"

say(){ printf '\n\033[1;36m%s\033[0m\n' "$*"; }
fail(){ printf '\nERROR: %s\n' "$*" >&2; exit 1; }

[[ "${EUID}" -ne 0 ]] || fail "Run as the normal SSH user, not root."
[[ "${RELEASE_SHA}" =~ ^[0-9a-fA-F]{40}$ ]] \
  || fail "Usage: $0 <40-character-authorized-release-SHA> (or set ALPHA_RELEASE_SHA). Never deploy mutable main."

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

say "Installing ${APP_NAME} at immutable release ${RELEASE_SHA}"
if [[ -d "${APP_DIR}/.git" ]]; then
  git -C "${APP_DIR}" fetch --prune origin main
else
  rm -rf "${APP_DIR}"
  git clone --branch main "${REPO_URL}" "${APP_DIR}"
fi
[[ -f "${APP_DIR}/deploy/release-pin.sh" ]] \
  || fail "Current checkout lacks deploy/release-pin.sh; bootstrap from a reviewed release containing the immutable deployment policy."
bash "${APP_DIR}/deploy/release-pin.sh" "${APP_DIR}" "${RELEASE_SHA}" "${RELEASE_FILE}"
[[ -f "${APP_DIR}/deploy/verify-runtime-release.sh" ]] || fail "Pinned release lacks runtime SHA attestation"

python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${APP_DIR}/.venv/bin/pip" install -r "${APP_DIR}/requirements.txt"

# P0 containment is a release invariant: an installer must never silently restore
# an older promoted-detector policy.
"${APP_DIR}/.venv/bin/python" - <<'PY'
from polymarket_scanner.trade_only import promoted_detectors
assert promoted_detectors() == (), f"P0 containment violated: promoted detectors={promoted_detectors()}"
print("P0 containment verified: 0 promoted TRADE NOW detectors")
PY
bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"

mkdir -p "${DATA_DIR}" "${CONFIG_DIR}"
chmod 700 "${DATA_DIR}" "${CONFIG_DIR}"
chmod 600 "${RELEASE_FILE}"

say "Required dependency preflight"
"${APP_DIR}/.venv/bin/python" -m polymarket_scanner.dependency_preflight \
  --required-only --output "${PREFLIGHT_FILE}"
chmod 600 "${PREFLIGHT_FILE}"

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
TELEGRAM_COMMANDS_IN_APP=false
DB_PATH=${DATA_DIR}/signals.db
SCAN_INTERVAL_SECONDS=15
UNIVERSE_REFRESH_SECONDS=120
WEATHER_REFRESH_SECONDS=60
ACTIONABLE_MIN_EDGE=0.025
PAPER_STAKE_USD=100
MARKET_WS_ENABLED=true
SPORTS_WS_ENABLED=true
CRYPTO_RTDS_ENABLED=true
MARKET_WS_PRIORITY_TOKEN_LIMIT=800
TOP_PRICE_REFRESH_SECONDS=45
SCANNER_WATCHDOG_STALE_SECONDS=120
EOF
chmod 600 "${ENV_FILE}"
unset TELEGRAM_BOT_TOKEN

say "Creating canonical trade-only scanner service"
TMP_SCANNER="$(mktemp)"
cat > "${TMP_SCANNER}" <<EOF
[Unit]
Description=Polymarket Edge Scanner (trade-only policy, silent research)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
User=${CURRENT_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=PYTHONUNBUFFERED=1
Environment=TELEGRAM_COMMANDS_IN_APP=false
ExecStartPre=/bin/bash ${APP_DIR}/deploy/verify-runtime-release.sh ${APP_DIR} ${RELEASE_FILE}
ExecStart=${APP_DIR}/.venv/bin/uvicorn app_trade_only:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=5
TimeoutStopSec=30
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
EOF
sudo install -m 0644 "${TMP_SCANNER}" "/etc/systemd/system/${SCANNER_SERVICE}"
rm -f "${TMP_SCANNER}"

say "Creating canonical trade-only Telegram command/delivery service"
TMP_COMMAND="$(mktemp)"
cat > "${TMP_COMMAND}" <<EOF
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
sudo install -m 0644 "${TMP_COMMAND}" "/etc/systemd/system/${COMMAND_SERVICE}"
rm -f "${TMP_COMMAND}"

sudo systemctl daemon-reload
sudo systemctl enable "${SCANNER_SERVICE}" "${COMMAND_SERVICE}" >/dev/null
sudo systemctl restart "${SCANNER_SERVICE}"
sleep 2
sudo systemctl restart "${COMMAND_SERVICE}"
sleep 3

sudo systemctl is-active --quiet "${SCANNER_SERVICE}" || {
  sudo systemctl status "${SCANNER_SERVICE}" --no-pager || true
  fail "Scanner service failed to start."
}
sudo systemctl is-active --quiet "${COMMAND_SERVICE}" || {
  sudo systemctl status "${COMMAND_SERVICE}" --no-pager || true
  fail "Command service failed to start."
}

say "Installing verified daily SQLite backup/restore check"
bash "${APP_DIR}/deploy/setup-db-backup-service.sh"

say "Runtime authority attestation"
ACTUAL_SHA="$(git -C "${APP_DIR}" rev-parse HEAD)"
RECORDED_SHA="$(tr -d '[:space:]' < "${RELEASE_FILE}")"
[[ "${ACTUAL_SHA}" == "${RECORDED_SHA}" && "${ACTUAL_SHA}" == "${RELEASE_SHA,,}" ]] || fail "Release attestation failed after startup"
echo "Immutable release: ${ACTUAL_SHA}"
echo "Required dependency preflight evidence: ${PREFLIGHT_FILE}"
echo "Scanner ExecStart:"
sudo systemctl show -p ExecStart "${SCANNER_SERVICE}"
echo "Scanner ExecStartPre:"
sudo systemctl show -p ExecStartPre "${SCANNER_SERVICE}"
echo "Command ExecStart:"
sudo systemctl show -p ExecStart "${COMMAND_SERVICE}"
echo "Command ExecStartPre:"
sudo systemctl show -p ExecStartPre "${COMMAND_SERVICE}"
echo "Promoted TRADE NOW detectors: 0 (P0 containment)"

echo
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
