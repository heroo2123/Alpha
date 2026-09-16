"""Render the guarded weather PAPER service; never start it."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

WEATHER_RELEASE_MARKER = "weather-paper-release.sha"
FINAL_WEATHER_MODULE = "polymarket_scanner.weather_only_live_paper_three_layer_validation"
GUARDED_BASE_MODULE = "polymarket_scanner.weather_only_live_paper_final"
FORBIDDEN_INHERITED_ENV = (
    "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP", "PYTHONINSPECT",
    "LD_PRELOAD", "LD_LIBRARY_PATH", "BASH_ENV", "ENV", "CDPATH",
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
)


def _safe(value: str) -> None:
    if not re.fullmatch(r"[/A-Za-z0-9_.-]+", value):
        raise ValueError("service paths/user/SHA must be whitespace-free safe names")


def render(app_dir: Path, config_dir: Path, user: str, release_sha: str) -> str:
    for value in (str(app_dir), str(config_dir), user, release_sha):
        _safe(value)
    if not app_dir.is_absolute() or not config_dir.is_absolute():
        raise ValueError("service directories must be absolute")
    if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        raise ValueError("release_sha must be exact lowercase SHA-1")
    release_root = f"{app_dir}/.releases/{release_sha}"
    python = f"{release_root}/venv/bin/python"
    release_file = f"{config_dir}/{WEATHER_RELEASE_MARKER}"
    verifier = f"/usr/bin/python3 /usr/local/libexec/polymarket-weather-paper/release-gate.py verify-checkout --app-dir {app_dir} --release-file {release_file} --generation-file {config_dir}/weather-paper-cutover-generation.id"
    state = "/var/lib/polymarket-weather-paper"
    unset = " ".join(FORBIDDEN_INHERITED_ENV)
    return f"""[Unit]
Description=Polymarket weather-only LIVE PAPER final runtime with guarded silent three-layer validation
# Guarded future-day base: {GUARDED_BASE_MODULE}
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3

[Service]
Type=simple
User={user}
WorkingDirectory={app_dir}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONNOUSERSITE=1
Environment=HOME={config_dir}
Environment=PATH={release_root}/venv/bin:/usr/bin:/bin
EnvironmentFile={config_dir}/weather-paper.env
UnsetEnvironment={unset}
ExecStartPre={verifier}
ExecStart={python} -E -s -m {FINAL_WEATHER_MODULE} --db {state}/weather-paper.sqlite --status {state}/status.json --release-file {release_file} --interval-seconds 180 --forecast-cache-seconds 900 --forecast-raw-gap-min 0.08 --max-forecast-events 6 --paper-stake-usd 10
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
    parser.add_argument("--release-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(render(args.app_dir, args.config_dir, args.user, args.release_sha), encoding="utf-8")


if __name__ == "__main__":
    main()
