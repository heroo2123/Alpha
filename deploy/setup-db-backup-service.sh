#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="polymarket-edge-scanner"
APP_DIR="${ALPHA_APP_DIR:-${HOME}/${APP_NAME}}"
CONFIG_DIR="${ALPHA_CONFIG_DIR:-${HOME}/.${APP_NAME}}"
ENV_FILE="${CONFIG_DIR}/bot.env"
RELEASE_FILE="${CONFIG_DIR}/release.sha"
BACKUP_SERVICE="${APP_NAME}-db-backup.service"
BACKUP_TIMER="${APP_NAME}-db-backup.timer"
CURRENT_USER="$(id -un)"

fail(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }

[[ -f "${APP_DIR}/deploy/run-db-backup.sh" ]] || fail "missing backup runner"
[[ -f "${APP_DIR}/deploy/verify-runtime-release.sh" ]] || fail "missing runtime release verifier"
[[ -f "${ENV_FILE}" ]] || fail "missing environment file: ${ENV_FILE}"
[[ -f "${RELEASE_FILE}" ]] || fail "missing immutable release marker: ${RELEASE_FILE}"

bash "${APP_DIR}/deploy/verify-runtime-release.sh" "${APP_DIR}" "${RELEASE_FILE}"

TMP_SERVICE="$(mktemp)"
cat > "${TMP_SERVICE}" <<EOF
[Unit]
Description=Polymarket Edge Scanner verified SQLite backup
After=local-fs.target

[Service]
Type=oneshot
User=${CURRENT_USER}
WorkingDirectory=${APP_DIR}
EnvironmentFile=${ENV_FILE}
Environment=ALPHA_APP_DIR=${APP_DIR}
Environment=ALPHA_DATA_DIR=${CONFIG_DIR}/data
Environment=ALPHA_BACKUP_DIR=${CONFIG_DIR}/backups
Environment=ALPHA_BACKUP_RETENTION_DAYS=14
ExecStartPre=/bin/bash ${APP_DIR}/deploy/verify-runtime-release.sh ${APP_DIR} ${RELEASE_FILE}
ExecStart=/bin/bash ${APP_DIR}/deploy/run-db-backup.sh
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=${CONFIG_DIR}
EOF
sudo install -m 0644 "${TMP_SERVICE}" "/etc/systemd/system/${BACKUP_SERVICE}"
rm -f "${TMP_SERVICE}"

TMP_TIMER="$(mktemp)"
cat > "${TMP_TIMER}" <<EOF
[Unit]
Description=Daily verified Polymarket Edge Scanner SQLite backup

[Timer]
OnCalendar=daily
Persistent=true
RandomizedDelaySec=30m
Unit=${BACKUP_SERVICE}

[Install]
WantedBy=timers.target
EOF
sudo install -m 0644 "${TMP_TIMER}" "/etc/systemd/system/${BACKUP_TIMER}"
rm -f "${TMP_TIMER}"

sudo systemctl daemon-reload
sudo systemctl enable --now "${BACKUP_TIMER}" >/dev/null

# Exercise the complete online-backup + integrity + restore-verification path now.
sudo systemctl start "${BACKUP_SERVICE}"
sudo systemctl is-active --quiet "${BACKUP_TIMER}" || fail "backup timer is not active"

printf 'Backup timer installed: %s\n' "${BACKUP_TIMER}"
sudo systemctl --no-pager list-timers "${BACKUP_TIMER}" || true
