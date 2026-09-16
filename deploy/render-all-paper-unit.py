"""Render the isolated final all-weather PAPER service; never start it."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

WEATHER_RELEASE_MARKER = "weather-paper-release.sha"
ALL_PAPER_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final_v9"
HOST_AUTHORITY = "/usr/local/libexec/polymarket-weather-paper/v2/authority.py"
RELEASES_ROOT = "/var/lib/polymarket-weather-paper-releases"
FORBIDDEN_INHERITED_ENVIRONMENT = (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "NO_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
    "no_proxy",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONUSERBASE",
    "PYTHONSTARTUP",
    "PYTHONINSPECT",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "BASH_ENV",
    "ENV",
    "CDPATH",
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_CONFIG_GLOBAL",
    "GIT_CONFIG_SYSTEM",
)
GENERATION_RE = re.compile(
    r"gen-[0-9]{8}T[0-9]{6}Z-[0-9a-f]{12}-[0-9a-f]{12}-[0-9a-f]{12}"
)


def render(
    app_dir: Path,
    config_dir: Path,
    user: str,
    release_sha: str,
    generation_id: str,
) -> str:
    for value in (str(app_dir), str(config_dir), user):
        if not re.fullmatch(r"[/A-Za-z0-9_.-]+", value):
            raise ValueError("service paths/user must be absolute, whitespace-free safe names")
    if not app_dir.is_absolute() or not config_dir.is_absolute():
        raise ValueError("service directories must be absolute")
    if not re.fullmatch(r"[0-9a-f]{40}", release_sha):
        raise ValueError("release SHA must be exact lowercase 40-hex")
    if not GENERATION_RE.fullmatch(generation_id):
        raise ValueError("exact immutable generation ID required")

    release_root = f"{RELEASES_ROOT}/{release_sha}"
    python = f"{release_root}/venv/bin/python"
    evidence = f"{release_root}/release-evidence.json"
    release_file = f"{config_dir}/{WEATHER_RELEASE_MARKER}"
    candidate_verifier = (
        f"/bin/bash {app_dir}/deploy/verify-runtime-release.sh {app_dir} {release_file}"
    )
    host_checkout = (
        f"/usr/bin/python3 {HOST_AUTHORITY} verify-checkout "
        f"--app-dir {app_dir} --release-file {release_file}"
    )
    host_generation = (
        f"/usr/bin/python3 {HOST_AUTHORITY} verify-generation "
        f"--generation-id {generation_id} --app-dir {app_dir} "
        f"--release-file {release_file} --candidate-sha {release_sha} --phase candidate"
    )
    host_runtime = (
        f"/usr/bin/python3 {HOST_AUTHORITY} verify-runtime --app-dir {app_dir} "
        f"--sha {release_sha} --generation-id {generation_id} --evidence {evidence}"
    )
    unset_environment = " ".join(FORBIDDEN_INHERITED_ENVIRONMENT)
    state = "/var/lib/polymarket-weather-paper"
    clean_exec = (
        "/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent "
        "PYTHONUNBUFFERED=1 ALPHA_DISABLE_DOTENV=1 PYTHONNOUSERSITE=1 "
        "PYTHONDONTWRITEBYTECODE=1 LANG=C.UTF-8 LC_ALL=C.UTF-8 "
        "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN} "
        "TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID} "
        f"{python} -I -s -m {ALL_PAPER_MODULE} "
        f"--db {state}/weather-paper.sqlite --status {state}/status.json "
        f"--release-file {release_file} --interval-seconds 180 "
        "--forecast-cache-seconds 900 --forecast-raw-gap-min 0.08 "
        "--max-forecast-events 6 --paper-stake-usd 10"
    )
    return f"""[Unit]
Description=Polymarket final all-weather PAPER research runtime
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3

[Service]
Type=simple
User={user}
WorkingDirectory={app_dir}
Environment=PYTHONUNBUFFERED=1
Environment=ALPHA_DISABLE_DOTENV=1
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONDONTWRITEBYTECODE=1
UnsetEnvironment={unset_environment}
EnvironmentFile={config_dir}/weather-paper.env
ExecStartPre={host_checkout}
ExecStartPre={host_generation}
ExecStartPre={host_runtime}
ExecStartPre={candidate_verifier}
ExecStart={clean_exec}
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
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        render(
            args.app_dir,
            args.config_dir,
            args.user,
            args.release_sha,
            args.generation_id,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
