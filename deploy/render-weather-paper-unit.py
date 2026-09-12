"""Render the weather-only live-paper service; never install or start it."""

from __future__ import annotations

import argparse
import re
from pathlib import Path


def render(app_dir: Path, config_dir: Path, user: str) -> str:
    for value in (str(app_dir), str(config_dir), user):
        if not re.fullmatch(r"[/A-Za-z0-9_.-]+", value):
            raise ValueError("service paths/user must be absolute, whitespace-free safe names")
    if not app_dir.is_absolute() or not config_dir.is_absolute():
        raise ValueError("service directories must be absolute")
    python = f"{app_dir}/.venv/bin/python"
    verifier = f"/bin/bash {app_dir}/deploy/verify-runtime-release.sh {app_dir} {config_dir}/release.sha"
    state = "/var/lib/polymarket-weather-paper"
    return f"""[Unit]
Description=Polymarket weather-only LIVE PAPER research signals
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3

[Service]
Type=simple
User={user}
WorkingDirectory={app_dir}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile={config_dir}/weather-paper.env
ExecStartPre={verifier}
ExecStart={python} -m polymarket_scanner.weather_only_live_paper_human --db {state}/weather-paper.sqlite --status {state}/status.json --release-file {config_dir}/release.sha --interval-seconds 180 --forecast-cache-seconds 900 --forecast-raw-gap-min 0.08 --max-forecast-events 6
Restart=on-failure
RestartSec=15
TimeoutStopSec=20
MemoryHigh=280M
MemoryMax=350M
MemorySwapMax=0
TasksMax=48
Nice=5
CPUWeight=70
IOWeight=50
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
ReadWritePaths={state}
UMask=0077

[Install]
WantedBy=multi-user.target
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--config-dir", type=Path, required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(args.app_dir, args.config_dir, args.user), encoding="utf-8")


if __name__ == "__main__":
    main()
