"""CI mirror of the independently pinned V10 unit template; never installs or starts it."""
from __future__ import annotations
import argparse,re
from pathlib import Path

FINAL_MODULE="polymarket_scanner.weather_only_live_paper_all_signals_final_v10"
RUNTIME_ROOT=Path("/var/lib/polymarket-weather-paper-runtime")
AUTH="/usr/local/libexec/polymarket-weather-paper-v3/authority.py"
FORBIDDEN=(
"ALL_PROXY","BASH_ENV","CDPATH","ENV","GIT_CONFIG_GLOBAL","GIT_CONFIG_SYSTEM","GIT_DIR","GIT_WORK_TREE",
"HTTP_PROXY","HTTPS_PROXY","LD_LIBRARY_PATH","LD_PRELOAD","NO_PROXY","PYTHONBREAKPOINT","PYTHONHOME",
"PYTHONINSPECT","PYTHONPATH","PYTHONSTARTUP","PYTHONUSERBASE","PYTHONWARNINGS","SSL_CERT_DIR","SSL_CERT_FILE",
"all_proxy","http_proxy","https_proxy","no_proxy")

def render(app_dir:Path,config_dir:Path,user:str,release_sha:str,generation_id:str)->str:
    for value in (str(app_dir),str(config_dir),user,release_sha,generation_id):
        if not re.fullmatch(r"[/A-Za-z0-9_.-]+",value):
            raise ValueError("unsafe service identity")
    if not app_dir.is_absolute() or not config_dir.is_absolute() or not re.fullmatch(r"[0-9a-f]{40}",release_sha) or not re.fullmatch(r"[0-9a-f]{64}",generation_id):
        raise ValueError("invalid service identity")
    root=RUNTIME_ROOT/"releases"/release_sha; source=root/"source"; python=root/"venv/bin/python"
    state="/var/lib/polymarket-weather-paper"; release_file=config_dir/"weather-paper-release.sha"; env_file=config_dir/"weather-paper.env"; unset=" ".join(FORBIDDEN)
    return f"""[Unit]
Description=Polymarket final all-weather PAPER research runtime V10
Wants=network-online.target
After=network-online.target
StartLimitIntervalSec=600
StartLimitBurst=3

[Service]
Type=simple
User={user}
WorkingDirectory={source}
Environment=PYTHONUNBUFFERED=1
Environment=PYTHONNOUSERSITE=1
Environment=PYTHONDONTWRITEBYTECODE=1
Environment=ALPHA_DISABLE_DOTENV=1
Environment=ALPHA_RELEASE_SHA={release_sha}
Environment=ALPHA_CUTOVER_GENERATION={generation_id}
Environment=ALPHA_RUNTIME_SOURCE={source}
UnsetEnvironment={unset}
EnvironmentFile={env_file}
ExecStartPre=+/usr/bin/python3 {AUTH} verify-runtime-files --generation-id {generation_id} --candidate-sha {release_sha}
ExecStart=/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 PYTHONUNBUFFERED=1 PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 ALPHA_DISABLE_DOTENV=1 ALPHA_RELEASE_SHA={release_sha} ALPHA_CUTOVER_GENERATION={generation_id} ALPHA_RUNTIME_SOURCE={source} TELEGRAM_BOT_TOKEN=${{TELEGRAM_BOT_TOKEN}} TELEGRAM_CHAT_ID=${{TELEGRAM_CHAT_ID}} {python} -I -s -E -m {FINAL_MODULE} --db {state}/weather-paper.sqlite --status {state}/status.json --release-file {release_file} --interval-seconds 180 --forecast-cache-seconds 900 --forecast-raw-gap-min 0.08 --max-forecast-events 6 --paper-stake-usd 10
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

def main():
    p=argparse.ArgumentParser(); p.add_argument("--app-dir",type=Path,required=True); p.add_argument("--config-dir",type=Path,required=True); p.add_argument("--user",required=True); p.add_argument("--release-sha",required=True); p.add_argument("--generation-id",required=True); p.add_argument("--output",type=Path,required=True); a=p.parse_args()
    a.output.parent.mkdir(parents=True,exist_ok=True); a.output.write_text(render(a.app_dir,a.config_dir,a.user,a.release_sha,a.generation_id),encoding="utf-8")
if __name__=="__main__": main()
