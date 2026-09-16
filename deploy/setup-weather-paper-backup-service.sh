#!/usr/bin/env bash
# RETIRED_PRODUCTION_DEPLOYMENT_ENTRYPOINT: historical body below is unreachable.
printf '%s\n' 'REFUSED: retired PAPER deployment path; use the independently provisioned host protocol in deploy/production-host-control.sh and docs/PRODUCTION_HOST_TRUST.md' >&2
exit 40

set -Eeuo pipefail

# Install (but do not enable/start) the verified weather-paper backup timer.
APP_DIR="${ALPHA_WEATHER_APP_DIR:-${HOME}/polymarket-weather-paper-app}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.polymarket-edge-scanner}"
CURRENT_USER="$(id -un)"
STATE_DIR="/var/lib/polymarket-weather-paper"
BACKUP_DIR="${STATE_DIR}/backups"
UNIT_DIR="$(mktemp -d)"
SERVICE_FILE="${UNIT_DIR}/polymarket-weather-paper-backup.service"
TIMER_FILE="${UNIT_DIR}/polymarket-weather-paper-backup.timer"
trap 'rm -rf "${UNIT_DIR}"' EXIT

[[ -x "${APP_DIR}/.venv/bin/python" ]] || { echo 'weather-paper virtualenv missing' >&2; exit 1; }
[[ -f "${CONFIG_DIR}/weather-paper-release.sha" ]] || { echo 'weather-paper release marker missing' >&2; exit 1; }

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=Verified backup of Polymarket weather PAPER ledger
ConditionPathExists=${STATE_DIR}/weather-paper.sqlite

[Service]
Type=oneshot
User=${CURRENT_USER}
WorkingDirectory=${APP_DIR}
Environment=ALPHA_WEATHER_APP_DIR=${APP_DIR}
Environment=ALPHA_CONFIG_DIR=${CONFIG_DIR}
Environment=WEATHER_PAPER_DB_PATH=${STATE_DIR}/weather-paper.sqlite
Environment=WEATHER_PAPER_BACKUP_DIR=${BACKUP_DIR}
# Three retained daily generations bound backup growth on the small VM while the
# primary database may contain up to roughly 1 GiB of compressed three-layer evidence.
Environment=WEATHER_PAPER_BACKUP_RETENTION_DAYS=3
ExecStart=/bin/bash ${APP_DIR}/deploy/pre-release-weather-paper-backup.sh
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=read-only
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
StateDirectory=polymarket-weather-paper
StateDirectoryMode=0700
ReadWritePaths=${STATE_DIR}
UMask=0077
EOF

cat > "${TIMER_FILE}" <<'EOF'
[Unit]
Description=Daily verified backup timer for Polymarket weather PAPER ledger

[Timer]
OnCalendar=daily
RandomizedDelaySec=1800
Persistent=true
AccuracySec=60
Unit=polymarket-weather-paper-backup.service

[Install]
WantedBy=timers.target
EOF

systemd-analyze verify "${SERVICE_FILE}" "${TIMER_FILE}"
sudo install -m 0644 "${SERVICE_FILE}" /etc/systemd/system/polymarket-weather-paper-backup.service
sudo install -m 0644 "${TIMER_FILE}" /etc/systemd/system/polymarket-weather-paper-backup.timer
sudo systemctl daemon-reload

echo 'Weather PAPER backup service/timer installed. They were NOT started or enabled.'
