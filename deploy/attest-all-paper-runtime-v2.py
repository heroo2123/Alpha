#!/usr/bin/env python3
from __future__ import annotations

"""Additive V10 runtime attestation for the independently prepared immutable release."""

import argparse
import json
import os
import subprocess
from pathlib import Path

FINAL_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final_v10"
AUTH = "/usr/local/libexec/polymarket-weather-paper-v3/authority.py"
RUNTIME_ROOT = Path("/var/lib/polymarket-weather-paper-runtime")
FORBIDDEN = {
    "HTTP_PROXY","HTTPS_PROXY","ALL_PROXY","NO_PROXY","http_proxy","https_proxy",
    "all_proxy","no_proxy","SSL_CERT_FILE","SSL_CERT_DIR","PYTHONPATH","PYTHONHOME",
    "PYTHONUSERBASE","PYTHONSTARTUP","PYTHONINSPECT","PYTHONWARNINGS",
    "PYTHONBREAKPOINT","LD_PRELOAD","LD_LIBRARY_PATH","BASH_ENV","ENV","CDPATH",
    "GIT_DIR","GIT_WORK_TREE","GIT_CONFIG_GLOBAL","GIT_CONFIG_SYSTEM",
}
ALLOWED = {
    "PATH","HOME","LANG","PYTHONUNBUFFERED","PYTHONNOUSERSITE",
    "PYTHONDONTWRITEBYTECODE","ALPHA_DISABLE_DOTENV","ALPHA_RELEASE_SHA",
    "ALPHA_CUTOVER_GENERATION","ALPHA_RUNTIME_SOURCE","TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
}


def run(args: list[str], allow_nonzero: bool = False) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode and not allow_nonzero:
        raise RuntimeError("ATTEST_COMMAND_FAILED:" + Path(args[0]).name)
    return result


def proc_env(pid: int) -> dict[str, str]:
    out = {}
    for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if b"=" in item:
            k, v = item.split(b"=", 1)
            out[k.decode("utf-8")] = v.decode("utf-8")
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--app-dir", type=Path, required=True)
    p.add_argument("--release-file", type=Path, required=True)
    p.add_argument("--db", type=Path, required=True)
    p.add_argument("--status", type=Path)
    p.add_argument("--unit", default="polymarket-weather-paper.service")
    p.add_argument("--require-active", action="store_true")
    p.add_argument("--output", type=Path)
    p.add_argument("--expected-release-sha", required=True)
    p.add_argument("--generation-id", required=True)
    a = p.parse_args()
    sha = a.expected_release_sha.strip().lower()
    gen = a.generation_id.strip().lower()
    source = (RUNTIME_ROOT / "releases" / sha / "source").resolve()
    python = (RUNTIME_ROOT / "releases" / sha / "venv/bin/python").resolve()
    payload: dict[str, object]
    try:
        if len(sha) != 40 or len(gen) != 64:
            raise RuntimeError("ATTEST_IDENTITY_INVALID")
        if run(["git","-C",str(a.app_dir),"rev-parse","HEAD"]).stdout.strip().lower() != sha:
            raise RuntimeError("ATTEST_STAGING_CHECKOUT_MISMATCH")
        if a.release_file.read_text(encoding="utf-8").strip().lower() != sha:
            raise RuntimeError("ATTEST_RELEASE_MARKER_MISMATCH")
        unit = run(["systemctl","cat",a.unit]).stdout
        expected_exec = f"{python} -I -s -E -m {FINAL_MODULE}"
        if expected_exec not in unit or f"WorkingDirectory={source}" not in unit:
            raise RuntimeError("ATTEST_UNIT_RUNTIME_IDENTITY_MISMATCH")
        if f"ALPHA_CUTOVER_GENERATION={gen}" not in unit or f"ALPHA_RELEASE_SHA={sha}" not in unit:
            raise RuntimeError("ATTEST_UNIT_GENERATION_IDENTITY_MISMATCH")
        active = run(["systemctl","is-active","--quiet",a.unit], allow_nonzero=True).returncode == 0
        if a.require_active and not active:
            raise RuntimeError("ATTEST_ACTIVE_REQUIRED")
        process = None
        if active:
            raw = run(["systemctl","show",a.unit,"--property","MainPID","--value"]).stdout.strip()
            pid = int(raw)
            if pid <= 1:
                raise RuntimeError("ATTEST_PID_INVALID")
            exe = Path(os.readlink(f"/proc/{pid}/exe")).resolve()
            cwd = Path(os.readlink(f"/proc/{pid}/cwd")).resolve()
            argv = [x.decode("utf-8") for x in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if x]
            env = proc_env(pid)
            if exe != python or cwd != source:
                raise RuntimeError("ATTEST_PROCESS_PATH_MISMATCH")
            if not argv or Path(argv[0]).resolve() != python or "-I" not in argv or "-s" not in argv or "-E" not in argv or FINAL_MODULE not in argv:
                raise RuntimeError("ATTEST_PROCESS_ARGV_MISMATCH")
            leaked = set(env) & FORBIDDEN
            unexpected = set(env) - ALLOWED
            if leaked:
                raise RuntimeError("ATTEST_FORBIDDEN_ENV:" + ",".join(sorted(leaked)))
            if unexpected:
                raise RuntimeError("ATTEST_UNEXPECTED_ENV:" + ",".join(sorted(unexpected)))
            if env.get("PYTHONNOUSERSITE") != "1" or env.get("ALPHA_RELEASE_SHA") != sha or env.get("ALPHA_CUTOVER_GENERATION") != gen or env.get("ALPHA_RUNTIME_SOURCE") != str(source):
                raise RuntimeError("ATTEST_PROCESS_ENV_IDENTITY_MISMATCH")
            process = {"pid":pid,"executable":str(exe),"cwd":str(cwd),"environment_keys":sorted(env)}
        status = {}
        if a.status and a.status.exists():
            status = json.loads(a.status.read_text(encoding="utf-8"))
            if status.get("runtime_python_executable") != str(python):
                raise RuntimeError("ATTEST_STATUS_PYTHON_MISMATCH")
            if status.get("runtime_source_path") != str(source):
                raise RuntimeError("ATTEST_STATUS_SOURCE_MISMATCH")
            if status.get("python_user_site_disabled") is not True or status.get("application_modules_from_approved_source") is not True:
                raise RuntimeError("ATTEST_STATUS_IMPORT_BOUNDARY_UNPROVEN")
            allowed_roots = (str(source), str((RUNTIME_ROOT/"releases"/sha/"venv").resolve()), "/usr/lib", "/usr/local/lib")
            for item in status.get("runtime_sys_path") or []:
                if not item:
                    continue
                rp = str(Path(item).resolve())
                if not any(rp == root or rp.startswith(root.rstrip("/") + "/") for root in allowed_roots):
                    raise RuntimeError("ATTEST_SYS_PATH_UNAPPROVED:" + rp)
        payload = {
            "acceptance":"PASS_ALL_PAPER_RUNTIME_V10_ATTESTATION",
            "release_sha":sha,"generation_id":gen,"final_module":FINAL_MODULE,
            "immutable_runtime_source":str(source),"immutable_runtime_python":str(python),
            "active":active,"process":process,"forbidden_environment_absent":True,
            "financial_authority":False,"automatic_order_placement":False,
            "wallet_or_order_api_loaded":False,
        }
        code = 0
    except Exception as exc:
        payload = {"acceptance":"FAIL_ALL_PAPER_RUNTIME_V10_ATTESTATION","error":getattr(exc,"code",str(exc))}
        code = 2
    text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if a.output:
        a.output.parent.mkdir(parents=True, exist_ok=True)
        a.output.write_text(text, encoding="utf-8")
        os.chmod(a.output, 0o600)
    print(text, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
