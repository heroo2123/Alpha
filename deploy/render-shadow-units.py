"""Render shadow/research services and bounded resource slice; never install/start."""
import argparse
from pathlib import Path
import re


def render(app_dir: Path, config_dir: Path, user: str) -> dict[str, str]:
    for value in (str(app_dir), str(config_dir), user):
        if not re.fullmatch(r"[/A-Za-z0-9_.-]+", value):
            raise ValueError("service paths/user must be absolute, whitespace-free safe names")
    if not app_dir.is_absolute() or not config_dir.is_absolute():
        raise ValueError("service directories must be absolute")
    python = f"{app_dir}/.venv/bin/python"
    verifier = f"/bin/bash {app_dir}/deploy/verify-runtime-release.sh {app_dir} {config_dir}/release.sha"
    shared = f"""Type=simple
User={user}
WorkingDirectory={app_dir}
Environment=PYTHONUNBUFFERED=1
Environment=ALPHA_CONFIG_DIR={config_dir}
Environment=UNIVERSE_SNAPSHOT_DIR={config_dir}/universe
ExecStartPre={verifier}
ExecStartPre={python} -m polymarket_scanner.shadow_preflight
Restart=on-failure
RestartSec=15
TimeoutStopSec=20
Slice=polymarket-shadow.slice
MemorySwapMax=0
TasksMax=64
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths={config_dir}
UMask=0077
"""
    def service(description, command, specific):
        return f"""[Unit]
Description={description}
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3

[Service]
{shared}{specific}
ExecStart={command}

[Install]
WantedBy=multi-user.target
"""
    scanner = service("Polymarket silent-shadow scanner", f"{python} -m uvicorn app_trade_only:app --host 127.0.0.1 --port 8000 --workers 1",
        f"EnvironmentFile={config_dir}/bot.env\nEnvironment=TELEGRAM_COMMANDS_IN_APP=false\n"
        "Environment=MARKET_WS_PRIORITY_TOKEN_LIMIT=800\nMemoryHigh=420M\nMemoryMax=480M\nCPUWeight=100\n")
    command = service("Polymarket command worker, financial delivery disabled", f"{python} {app_dir}/command_worker_trade_only.py",
        f"EnvironmentFile={config_dir}/bot.env\nMemoryHigh=80M\nMemoryMax=112M\nCPUWeight=100\n")
    universe = service("Polymarket complete Gamma universe builder", f"{python} -m polymarket_scanner.universe_builder --ipv6",
        "Nice=5\nCPUWeight=50\nIOWeight=50\nIOSchedulingClass=best-effort\nIOSchedulingPriority=6\nMemoryHigh=144M\nMemoryMax=160M\n")
    universe = universe.replace(f"ReadWritePaths={config_dir}", f"ReadWritePaths={config_dir}/universe")

    # Independent public-data research service. It receives no bot.env, Telegram
    # credential or trading/account configuration. Persistent evidence is confined
    # to a systemd-owned state directory. The reviewed entrypoint is the operational
    # wrapper so horizon attestation and explicit collection health cannot be bypassed.
    calibration_state = "/var/lib/polymarket-weather-calibration"
    calibration_db = f"{calibration_state}/weather-calibration.sqlite"
    calibration_preflight = (
        f"{python} -m polymarket_scanner.weather_calibration_service_preflight "
        f"--app-dir {app_dir} --release-file {config_dir}/release.sha --db {calibration_db}"
    )
    calibration = f"""[Unit]
Description=Weather prospective calibration research worker, no financial authority
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3

[Service]
Type=simple
User={user}
WorkingDirectory={app_dir}
Environment=PYTHONUNBUFFERED=1
ExecStartPre={verifier}
ExecStartPre={calibration_preflight}
ExecStart={python} -m polymarket_scanner.weather_only_calibration_worker_runtime --loop --interval-seconds 30 --db {calibration_db} --output {calibration_state}/status.json
Restart=on-failure
RestartSec=30
TimeoutStopSec=20
Slice=polymarket-shadow.slice
MemoryHigh=96M
MemoryMax=128M
MemorySwapMax=0
TasksMax=32
Nice=10
CPUWeight=20
IOWeight=20
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ProtectKernelTunables=true
ProtectKernelModules=true
ProtectControlGroups=true
RestrictSUIDSGID=true
LockPersonality=true
CapabilityBoundingSet=
AmbientCapabilities=
StateDirectory=polymarket-weather-calibration
StateDirectoryMode=0700
ReadWritePaths={calibration_state}
UMask=0077

[Install]
WantedBy=multi-user.target
"""
    return {
        "polymarket-edge-scanner.service": scanner,
        "polymarket-edge-command.service": command,
        "polymarket-universe-builder.service": universe,
        "polymarket-weather-calibration.service": calibration,
        "polymarket-shadow.slice": "[Unit]\nDescription=Bounded Polymarket silent-shadow workload\n\n[Slice]\nMemoryHigh=560M\nMemoryMax=640M\nMemorySwapMax=0\nTasksMax=192\n",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, body in render(args.app_dir, args.config_dir, args.user).items():
        (args.output_dir / name).write_text(body)


if __name__ == "__main__":
    main()
