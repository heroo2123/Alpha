#!/usr/bin/env python3
from __future__ import annotations

"""Strict additive attestation for final V9 all-PAPER runtime and code-loading boundary."""

import importlib.util
import json
import os
import shlex
import subprocess
from dataclasses import replace
from pathlib import Path

FINAL_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final_v9"
DOTENV_ENV_LINE = "Environment=ALPHA_DISABLE_DOTENV=1"
HOST_GATE = "/usr/local/libexec/polymarket-weather-paper/release-gate.py"
FORBIDDEN_NETWORK_ENVIRONMENT = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY", "http_proxy", "https_proxy", "all_proxy", "no_proxy", "SSL_CERT_FILE", "SSL_CERT_DIR",
)
FORBIDDEN_CODE_ENVIRONMENT = (
    "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP", "PYTHONINSPECT", "LD_PRELOAD", "LD_LIBRARY_PATH", "BASH_ENV", "ENV", "CDPATH",
)
_EXTRA_WRITER_MARKERS = (
    "weather_only_live_paper_all_signals_final_v5.py", "weather_only_live_paper_all_signals_final_v6.py",
    "weather_only_live_paper_all_signals_final_v7.py", "weather_only_live_paper_all_signals_final_v8.py", "weather_only_live_paper_all_signals_final_v9.py",
)


def _load_base():
    path = Path(__file__).with_name("attest-all-paper-runtime.py")
    spec = importlib.util.spec_from_file_location("_all_paper_attest_base", path)
    if spec is None or spec.loader is None: raise RuntimeError("ALL_PAPER_BASE_ATTESTER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def _proc_environment(pid: int) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in Path(f"/proc/{int(pid)}/environ").read_bytes().split(b"\0"):
        if item and b"=" in item:
            key,value=item.split(b"=",1); out[key.decode("utf-8","strict")]=value.decode("utf-8","strict")
    return out


def _directives(text: str, name: str) -> list[str]:
    prefix=name+"="; return [line.strip().split("=",1)[1] for line in text.splitlines() if line.strip().startswith(prefix)]


def _strict_actual_unit(text: str, app: Path, release: str) -> Path:
    python=(app/".releases"/release/"venv"/"bin"/"python").resolve()
    starts=_directives(text,"ExecStart")
    if len(starts)!=1: raise RuntimeError("ALL_PAPER_UNIT_EXECSTART_NOT_UNIQUE")
    tokens=shlex.split(starts[0])
    if len(tokens)<5 or os.path.realpath(tokens[0])!=str(python) or tokens[1:5] != ["-E","-s","-m",FINAL_MODULE]:
        raise RuntimeError("ALL_PAPER_UNIT_RELEASE_INTERPRETER_OR_FLAGS_INVALID")
    pre=_directives(text,"ExecStartPre")
    if len(pre)!=1: raise RuntimeError("ALL_PAPER_UNIT_HOST_GATE_NOT_UNIQUE")
    p=shlex.split(pre[0])
    if len(p)<3 or p[:3] != ["/usr/bin/python3",HOST_GATE,"verify-checkout"]:
        raise RuntimeError("ALL_PAPER_UNIT_HOST_GATE_INVALID")
    envlines=_directives(text,"Environment")
    if "PYTHONNOUSERSITE=1" not in envlines: raise RuntimeError("ALL_PAPER_UNIT_NOUSERSITE_MISSING")
    unset=set(" ".join(_directives(text,"UnsetEnvironment")).split())
    missing=sorted(set(FORBIDDEN_NETWORK_ENVIRONMENT+FORBIDDEN_CODE_ENVIRONMENT)-unset)
    if missing: raise RuntimeError("ALL_PAPER_UNIT_ENV_UNSET_INCOMPLETE:"+",".join(missing))
    return python


def _probe(python: Path, app: Path, env: dict[str,str]) -> dict:
    code=r'''import importlib,json,os,site,sys
mods=["polymarket_scanner","polymarket_scanner.weather_only_live_paper_all_signals_final_v9"]
loc={n:os.path.realpath(importlib.import_module(n).__file__) for n in mods}
print(json.dumps({"executable":os.path.realpath(sys.executable),"prefix":os.path.realpath(sys.prefix),"base_prefix":os.path.realpath(sys.base_prefix),"sys_path":[os.path.realpath(p or os.getcwd()) for p in sys.path],"enable_user_site":site.ENABLE_USER_SITE,"module_locations":loc},sort_keys=True))'''
    clean={k:v for k,v in env.items() if k not in set(FORBIDDEN_NETWORK_ENVIRONMENT+FORBIDDEN_CODE_ENVIRONMENT)}
    clean["PYTHONNOUSERSITE"]="1"; clean["ALPHA_DISABLE_DOTENV"]="1"
    cp=subprocess.run([str(python),"-E","-s","-c",code],cwd=app,env=clean,text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
    if cp.returncode: raise RuntimeError("ALL_PAPER_PYTHON_PROBE_FAILED")
    data=json.loads(cp.stdout)
    if data.get("enable_user_site") is not False: raise RuntimeError("ALL_PAPER_USER_SITE_ENABLED")
    if os.path.realpath(str(data.get("executable")))!=str(python): raise RuntimeError("ALL_PAPER_PROBE_INTERPRETER_MISMATCH")
    app_real=str(app.resolve()); venv_real=str(python.parent.parent.resolve())
    for name,path in data.get("module_locations",{}).items():
        if not (path==app_real or path.startswith(app_real+os.sep)): raise RuntimeError("ALL_PAPER_MODULE_OUTSIDE_APP:"+name)
    for path in data.get("sys_path",[]):
        if "/.local/" in path or ("site-packages" in path and not path.startswith(venv_real)):
            raise RuntimeError("ALL_PAPER_FOREIGN_SYS_PATH")
    return data


def _normalize_facts(facts, app: Path, release_file: Path):
    text=facts.unit_text.replace(" -E -s -m "," -m ")
    lines=[]
    for line in text.splitlines():
        if line.startswith("ExecStartPre="):
            line=f"ExecStartPre=/bin/bash {app}/deploy/verify-runtime-release.sh {app} {release_file}"
        lines.append(line)
    def norm(row):
        row=tuple(row)
        return (row[0],*row[3:]) if len(row)>=4 and row[1:3]==("-E","-s") else row
    return replace(facts,unit_text="\n".join(lines)+"\n",process_argv=norm(facts.process_argv),matching_weather_process_argvs=tuple(norm(r) for r in facts.matching_weather_process_argvs))


def main() -> int:
    base=_load_base(); base.FINAL_ALL_PAPER_MODULE=FINAL_MODULE
    base.KNOWN_WEATHER_WRITER_MARKERS=tuple(dict.fromkeys((*base.KNOWN_WEATHER_WRITER_MARKERS,*_EXTRA_WRITER_MARKERS)))
    original_collect=base.collect_facts; original_attest=base.attest_weather_runtime
    probe_result: dict={}

    def collect_facts(*,unit_name,app_dir,release_file,db_path):
        nonlocal probe_result
        app=Path(app_dir).expanduser().resolve(); release_path=Path(release_file).expanduser().resolve(); release=release_path.read_text().strip().lower()
        if (app/".env").exists() or (app/".env").is_symlink(): raise RuntimeError("ALL_PAPER_IMPLICIT_DOTENV_PRESENT")
        facts=original_collect(unit_name=unit_name,app_dir=app,release_file=release_path,db_path=db_path)
        python=_strict_actual_unit(facts.unit_text,app,release)
        if facts.active:
            if facts.main_pid is None: raise RuntimeError("ACTIVE_ALL_PAPER_SERVICE_MAINPID_MISSING")
            env=_proc_environment(int(facts.main_pid))
            if env.get("ALPHA_DISABLE_DOTENV")!="1" or env.get("PYTHONNOUSERSITE")!="1": raise RuntimeError("ALL_PAPER_PROCESS_REQUIRED_ENV_MISSING")
            leaked=sorted(name for name in FORBIDDEN_NETWORK_ENVIRONMENT+FORBIDDEN_CODE_ENVIRONMENT if name in env)
            if leaked: raise RuntimeError("ALL_PAPER_PROCESS_FORBIDDEN_ENVIRONMENT_LEAK:"+",".join(leaked))
            if os.path.realpath(facts.process_executable or "")!=str(python): raise RuntimeError("ALL_PAPER_PROCESS_RELEASE_INTERPRETER_MISMATCH")
            actual=tuple(facts.process_argv)
            if len(actual)<5 or actual[1:5] != ("-E","-s","-m",FINAL_MODULE): raise RuntimeError("ALL_PAPER_PROCESS_FLAGS_OR_ENTRYPOINT_INVALID")
            probe_result=_probe(python,app,env)
        return _normalize_facts(facts,app,release_path)

    def attest_wrapper(facts,**kwargs):
        app=Path(kwargs["expected_app_dir"]).resolve(); release=str(kwargs["expected_release_sha"]).lower()
        kwargs["expected_python"]=app/".releases"/release/"venv"/"bin"/"python"
        return original_attest(facts,**kwargs)

    base.collect_facts=collect_facts; base.attest_weather_runtime=attest_wrapper
    return int(base.main())

if __name__=="__main__": raise SystemExit(main())
