#!/usr/bin/env python3
from __future__ import annotations

"""Read-only host collector for strict weather PAPER runtime attestation."""

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

_SCRIPT_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))
from polymarket_scanner.weather_only_runtime_attestation import (  # noqa:E402
    WeatherRuntimeAttestationError, WeatherRuntimeFacts, attest_weather_runtime,
)

FINAL_WEATHER_MODULE = "polymarket_scanner.weather_only_live_paper_three_layer_validation"
HOST_GATE = "/usr/local/libexec/polymarket-weather-paper/release-gate.py"
FORBIDDEN_ENV = {
    "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP", "PYTHONINSPECT",
    "LD_PRELOAD", "LD_LIBRARY_PATH", "BASH_ENV", "ENV", "CDPATH",
}
KNOWN_WEATHER_WRITER_MARKERS = (
    "polymarket_scanner.weather_only_live_paper", "weather_only_live_paper.py",
    "weather_only_live_paper_v2.py", "weather_only_live_paper_v3.py",
    "weather_only_live_paper_v4.py", "weather_only_live_paper_corrective.py",
    "weather_only_live_paper_final.py", "weather_only_live_paper_three_layer_validation.py",
)


def run(args: list[str], *, allow_nonzero: bool = False, **kwargs) -> subprocess.CompletedProcess[str]:
    cp = subprocess.run(args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, **kwargs)
    if cp.returncode and not allow_nonzero:
        raise RuntimeError(f"COMMAND_FAILED:{args[0]}:{cp.returncode}:{cp.stderr.strip()}")
    return cp


def read_sha(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip().split()[0].lower()


def active(unit: str) -> bool:
    return run(["systemctl", "is-active", "--quiet", unit], allow_nonzero=True).returncode == 0


def enabled(unit: str) -> bool:
    return run(["systemctl", "is-enabled", "--quiet", unit], allow_nonzero=True).returncode == 0


def main_pid(unit: str) -> int | None:
    raw = run(["systemctl", "show", unit, "--property", "MainPID", "--value"]).stdout.strip()
    try: value = int(raw)
    except ValueError: return None
    return value if value > 1 else None


def proc_argv(pid: int) -> tuple[str, ...]:
    return tuple(x.decode("utf-8", "strict") for x in Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0") if x)


def proc_environ(pid: int) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if not item: continue
        key, sep, value = item.partition(b"=")
        if not sep: continue
        out[key.decode("utf-8", "strict")] = value.decode("utf-8", "strict")
    return out


def matching_processes(db_path: Path) -> tuple[tuple[str, ...], ...]:
    rows=[]; db=str(db_path.resolve()); own=os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name)==own: continue
        try: argv=proc_argv(int(entry.name))
        except (OSError,UnicodeError): continue
        joined=" ".join(argv)
        if db in joined or any(m in joined for m in KNOWN_WEATHER_WRITER_MARKERS): rows.append(argv)
    return tuple(sorted(rows,key=lambda r:" ".join(r)))


def directives(text: str, name: str) -> list[str]:
    prefix=name+"="
    return [line.strip().split("=",1)[1] for line in text.splitlines() if line.strip().startswith(prefix)]


def strict_unit_checks(text: str, app: Path, config: Path, sha: str, generation: str) -> dict:
    expected_python=str((app/".releases"/sha/"venv"/"bin"/"python").resolve())
    starts=directives(text,"ExecStart")
    if len(starts)!=1: raise RuntimeError("STRICT_UNIT_EXECSTART_NOT_UNIQUE")
    tok=shlex.split(starts[0])
    if len(tok)<6 or os.path.realpath(tok[0])!=expected_python or tok[1:5] != ["-E","-s","-m",FINAL_WEATHER_MODULE]:
        raise RuntimeError("STRICT_UNIT_RELEASE_INTERPRETER_OR_FLAGS_INVALID")
    pres=directives(text,"ExecStartPre")
    if len(pres)!=1: raise RuntimeError("STRICT_UNIT_PRESTART_NOT_UNIQUE")
    pre=shlex.split(pres[0])
    if len(pre)<8 or pre[:2] != ["/usr/bin/python3",HOST_GATE] or pre[2] != "verify-checkout":
        raise RuntimeError("STRICT_UNIT_HOST_GATE_INVALID")
    env_lines=directives(text,"Environment")
    if "PYTHONNOUSERSITE=1" not in env_lines: raise RuntimeError("STRICT_UNIT_NOUSERSITE_MISSING")
    unset=set(" ".join(directives(text,"UnsetEnvironment")).split())
    if not FORBIDDEN_ENV.issubset(unset): raise RuntimeError("STRICT_UNIT_FORBIDDEN_ENV_NOT_UNSET")
    envfiles=directives(text,"EnvironmentFile")
    expected_env=str(config/"weather-paper.env")
    if envfiles != [expected_env]: raise RuntimeError("STRICT_UNIT_ENV_FILE_INVALID")
    return {"expected_python":expected_python,"forbidden_unset":sorted(FORBIDDEN_ENV),"generation_id":generation}


def strict_process_checks(pid: int, argv: tuple[str,...], environ: dict[str,str], app: Path, sha: str) -> dict:
    expected_python=str((app/".releases"/sha/"venv"/"bin"/"python").resolve())
    if os.path.realpath(os.readlink(f"/proc/{pid}/exe")) != expected_python:
        raise RuntimeError("STRICT_PROCESS_INTERPRETER_MISMATCH")
    if len(argv)<6 or os.path.realpath(argv[0])!=expected_python or tuple(argv[1:5]) != ("-E","-s","-m",FINAL_WEATHER_MODULE):
        raise RuntimeError("STRICT_PROCESS_ARGV_INVALID")
    leaked=sorted(k for k in FORBIDDEN_ENV if k in environ)
    if leaked: raise RuntimeError("STRICT_PROCESS_FORBIDDEN_ENV:"+",".join(leaked))
    if environ.get("PYTHONNOUSERSITE") != "1": raise RuntimeError("STRICT_PROCESS_NOUSERSITE_INVALID")
    return {"pid":pid,"interpreter":expected_python,"environment_keys":sorted(environ),"forbidden_environment_absent":True}


def python_probe(python: Path, app: Path, environ: dict[str,str]) -> dict:
    code = r'''import importlib,json,os,site,sys
mods=["polymarket_scanner","polymarket_scanner.weather_only_live_paper_three_layer_validation","polymarket_scanner.weather_only_runtime_attestation"]
locations={}
for name in mods:
 m=importlib.import_module(name); locations[name]=os.path.realpath(getattr(m,"__file__",""))
print(json.dumps({"executable":os.path.realpath(sys.executable),"prefix":os.path.realpath(sys.prefix),"base_prefix":os.path.realpath(sys.base_prefix),"sys_path":[os.path.realpath(p or os.getcwd()) for p in sys.path],"enable_user_site":site.ENABLE_USER_SITE,"module_locations":locations},sort_keys=True))'''
    probe_env={k:v for k,v in environ.items() if k not in FORBIDDEN_ENV}
    probe_env["PYTHONNOUSERSITE"]="1"
    cp=run([str(python),"-E","-s","-c",code], cwd=str(app), env=probe_env)
    data=json.loads(cp.stdout)
    if data.get("enable_user_site") is not False: raise RuntimeError("STRICT_PROBE_USER_SITE_ENABLED")
    if os.path.realpath(str(data.get("executable"))) != os.path.realpath(python): raise RuntimeError("STRICT_PROBE_INTERPRETER_MISMATCH")
    app_real=os.path.realpath(app)
    for name,path in data.get("module_locations",{}).items():
        if not (path==app_real or path.startswith(app_real+os.sep)): raise RuntimeError("STRICT_PROBE_MODULE_OUTSIDE_APP:"+name)
    for p in data.get("sys_path",[]):
        low=p.lower()
        if "/.local/" in low or "site-packages" in low and not p.startswith(os.path.realpath(python.parent.parent)):
            raise RuntimeError("STRICT_PROBE_SYS_PATH_FOREIGN_SITE")
    return data


def legacy_normalized(facts: WeatherRuntimeFacts, app: Path, release_file: Path) -> WeatherRuntimeFacts:
    # Legacy pure checker expects no -E/-s and candidate-local prestart. Strict checks
    # above validate the real unit/process first; normalization keeps its older checks
    # useful for DB/status/entrypoint/hardening without weakening the new invariants.
    text=facts.unit_text
    text=text.replace(" -E -s -m "," -m ")
    lines=[]
    for line in text.splitlines():
        if line.startswith("ExecStartPre="):
            line=f"ExecStartPre=/bin/bash {app}/deploy/verify-runtime-release.sh {app} {release_file}"
        lines.append(line)
    def norm(row: tuple[str,...]) -> tuple[str,...]:
        return (row[0],*row[3:]) if len(row)>=4 and row[1:3]==("-E","-s") else row
    return replace(facts,unit_text="\n".join(lines)+"\n",process_argv=norm(facts.process_argv),matching_weather_process_argvs=tuple(norm(r) for r in facts.matching_weather_process_argvs))


def main() -> int:
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--app-dir",type=Path,required=True); ap.add_argument("--release-file",type=Path,required=True)
    ap.add_argument("--generation-id",required=True); ap.add_argument("--unit",default="polymarket-weather-paper.service")
    ap.add_argument("--db",type=Path,default=Path("/var/lib/polymarket-weather-paper/weather-paper.sqlite"))
    ap.add_argument("--status",type=Path,default=Path("/var/lib/polymarket-weather-paper/status.json")); ap.add_argument("--require-active",action="store_true"); ap.add_argument("--output",type=Path)
    args=ap.parse_args(); app=args.app_dir.resolve(); release=args.release_file.resolve(); config=release.parent; sha=read_sha(release)
    try:
        if not __import__('re').fullmatch(r'[0-9a-f]{40}',sha) or not __import__('re').fullmatch(r'[0-9a-f]{32}',args.generation_id): raise RuntimeError("RELEASE_OR_GENERATION_INVALID")
        run(["/usr/bin/python3",HOST_GATE,"verify-generation","--generation-id",args.generation_id,"--sha",sha])
        is_active=active(args.unit)
        if not is_active and enabled(args.unit): raise RuntimeError("INACTIVE_WEATHER_SERVICE_STILL_ENABLED")
        pid=main_pid(args.unit) if is_active else None
        if is_active and pid is None: raise RuntimeError("ACTIVE_SERVICE_MAINPID_MISSING")
        unit_text=run(["systemctl","cat",args.unit]).stdout
        unit_evidence=strict_unit_checks(unit_text,app,config,sha,args.generation_id)
        env={}; probe={}; process_evidence={}
        if pid:
            argv=proc_argv(pid); env=proc_environ(pid)
            process_evidence=strict_process_checks(pid,argv,env,app,sha)
            probe=python_probe(app/".releases"/sha/"venv"/"bin"/"python",app,env)
            cwd=os.readlink(f"/proc/{pid}/cwd"); exe=os.readlink(f"/proc/{pid}/exe")
        else: argv=(); cwd=None; exe=None
        facts=WeatherRuntimeFacts(unit_name=args.unit,unit_text=unit_text,active=is_active,main_pid=pid,process_cwd=cwd,process_executable=exe,process_argv=argv,repo_head_sha=run(["git","-C",str(app),"rev-parse","HEAD"]).stdout.strip().lower(),release_marker_sha=sha,matching_weather_process_argvs=matching_processes(args.db))
        normalized=legacy_normalized(facts,app,release)
        att=attest_weather_runtime(normalized,expected_app_dir=app,expected_python=app/".releases"/sha/"venv"/"bin"/"python",expected_db_path=args.db.resolve(),expected_status_path=args.status.resolve(),expected_release_file=release,expected_environment_file=config/"weather-paper.env",expected_release_sha=sha,expected_module=FINAL_WEATHER_MODULE)
        payload={"facts":facts.as_dict(),"attestation":att.as_dict(),"strict_unit":unit_evidence,"strict_process":process_evidence,"python_probe":probe,"generation_id":args.generation_id}
        if args.require_active and not att.deployment_proven: payload["acceptance"]="FAIL_ACTIVE_RUNTIME_NOT_PROVEN"; code=2
        else: payload["acceptance"]="PASS_STRICT_ACTIVE_RUNTIME_ATTESTED" if att.deployment_proven else "PASS_STRICT_INSTALLED_UNIT_INACTIVE"; code=0
    except (WeatherRuntimeAttestationError,RuntimeError,OSError,UnicodeError,ValueError,json.JSONDecodeError) as exc:
        payload={"acceptance":"FAIL_RUNTIME_ATTESTATION","error":getattr(exc,"code",str(exc))}; code=2
    text=json.dumps(payload,sort_keys=True,indent=2)+"\n"
    if args.output: args.output.resolve().parent.mkdir(parents=True,exist_ok=True); args.output.resolve().write_text(text,encoding="utf-8"); os.chmod(args.output.resolve(),0o600)
    sys.stdout.write(text); return code

if __name__=="__main__": raise SystemExit(main())
