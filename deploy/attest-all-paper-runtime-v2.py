#!/usr/bin/env python3
from __future__ import annotations

"""Strict runtime/code-loading attestation for final V9 all-PAPER."""

import argparse
import dataclasses
import importlib.util
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path

FINAL_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final_v9"
RELEASES_ROOT = Path("/var/lib/polymarket-weather-paper-releases")
FORBIDDEN_CODE_ENVIRONMENT = (
    "PYTHONPATH",
    "PYTHONHOME",
    "PYTHONUSERBASE",
    "PYTHONSTARTUP",
    "PYTHONINSPECT",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
)
FORBIDDEN_NETWORK_ENVIRONMENT = (
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
)
REQUIRED_PROCESS_ENVIRONMENT = {
    "PATH": "/usr/bin:/bin",
    "HOME": "/nonexistent",
    "PYTHONUNBUFFERED": "1",
    "ALPHA_DISABLE_DOTENV": "1",
    "PYTHONNOUSERSITE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
    "LANG": "C.UTF-8",
    "LC_ALL": "C.UTF-8",
}
ALLOWED_PROCESS_ENVIRONMENT_KEYS = set(REQUIRED_PROCESS_ENVIRONMENT) | {
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "LC_CTYPE",
}
_EXTRA_WRITER_MARKERS = (
    "weather_only_live_paper_all_signals_final_v8.py",
    "weather_only_live_paper_all_signals_final_v9.py",
)


def _load_base():
    path = Path(__file__).with_name("attest-all-paper-runtime.py")
    spec = importlib.util.spec_from_file_location("_all_paper_attest_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ALL_PAPER_BASE_ATTESTER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _proc_environment(pid: int) -> dict[str, str]:
    raw = Path(f"/proc/{int(pid)}/environ").read_bytes()
    out: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if not item:
            continue
        text = item.decode("utf-8", errors="strict")
        if "=" in text:
            key, value = text.split("=", 1)
            out[key] = value
    return out


def _execstart(unit_text: str) -> tuple[str, ...]:
    rows = [
        line.strip().split("=", 1)[1]
        for line in unit_text.splitlines()
        if line.strip().startswith("ExecStart=")
    ]
    if len(rows) != 1:
        raise RuntimeError("ALL_PAPER_UNIT_EXECSTART_NOT_UNIQUE")
    try:
        return tuple(shlex.split(rows[0]))
    except ValueError:
        raise RuntimeError("ALL_PAPER_UNIT_EXECSTART_INVALID") from None


def _validate_unit_environment_and_exec(
    unit_text: str,
    *,
    expected_python: Path,
    release_sha: str,
    generation_id: str,
    app_dir: Path,
    release_file: Path,
    evidence: Path,
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    unset_names: set[str] = set()
    for line in unit_text.splitlines():
        row = line.strip()
        if row.startswith("UnsetEnvironment="):
            unset_names.update(row.split("=", 1)[1].split())
    missing = sorted(
        (set(FORBIDDEN_CODE_ENVIRONMENT) | set(FORBIDDEN_NETWORK_ENVIRONMENT))
        - unset_names
    )
    if missing:
        raise RuntimeError("ALL_PAPER_UNIT_UNSET_ENVIRONMENT_INCOMPLETE:" + ",".join(missing))
    for required in (
        "Environment=PYTHONNOUSERSITE=1",
        "Environment=PYTHONDONTWRITEBYTECODE=1",
        "Environment=ALPHA_DISABLE_DOTENV=1",
    ):
        if unit_text.count(required) != 1:
            raise RuntimeError("ALL_PAPER_UNIT_REQUIRED_ENVIRONMENT_INVALID:" + required)

    tokens = _execstart(unit_text)
    if tokens[:2] != ("/usr/bin/env", "-i"):
        raise RuntimeError("ALL_PAPER_UNIT_CLEAN_ENV_EXEC_MISSING")
    python_text = str(expected_python)
    try:
        python_index = tokens.index(python_text)
    except ValueError:
        raise RuntimeError("ALL_PAPER_UNIT_SHA_SCOPED_INTERPRETER_MISSING") from None
    expected_assignments = {
        "PATH=/usr/bin:/bin",
        "HOME=/nonexistent",
        "PYTHONUNBUFFERED=1",
        "ALPHA_DISABLE_DOTENV=1",
        "PYTHONNOUSERSITE=1",
        "PYTHONDONTWRITEBYTECODE=1",
        "LANG=C.UTF-8",
        "LC_ALL=C.UTF-8",
        "TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN}",
        "TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID}",
    }
    assignments = set(tokens[2:python_index])
    if assignments != expected_assignments:
        raise RuntimeError("ALL_PAPER_UNIT_CLEAN_ENV_ALLOWLIST_MISMATCH")
    if tokens[python_index + 1 : python_index + 5] != ("-I", "-s", "-m", FINAL_MODULE):
        raise RuntimeError("ALL_PAPER_UNIT_ISOLATED_PYTHON_FLAGS_INVALID")

    prestarts = [
        line.strip().split("=", 1)[1]
        for line in unit_text.splitlines()
        if line.strip().startswith("ExecStartPre=")
    ]
    joined = "\n".join(prestarts)
    required_fragments = (
        "/usr/local/libexec/polymarket-weather-paper/v2/authority.py verify-checkout",
        f"verify-generation --generation-id {generation_id}",
        f"--candidate-sha {release_sha} --phase candidate",
        f"verify-runtime --app-dir {app_dir}",
        f"--sha {release_sha} --generation-id {generation_id} --evidence {evidence}",
        f"/bin/bash {app_dir}/deploy/verify-runtime-release.sh {app_dir} {release_file}",
    )
    if any(fragment not in joined for fragment in required_fragments):
        raise RuntimeError("ALL_PAPER_UNIT_HOST_AUTHORITY_PRESTART_INCOMPLETE")
    return tokens, tuple(prestarts)


def _validate_process_environment(pid: int) -> dict:
    environment = _proc_environment(pid)
    for key, expected in REQUIRED_PROCESS_ENVIRONMENT.items():
        if environment.get(key) != expected:
            raise RuntimeError(f"ALL_PAPER_PROCESS_REQUIRED_ENV_INVALID:{key}")
    for key in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        if not environment.get(key):
            raise RuntimeError(f"ALL_PAPER_PROCESS_TELEGRAM_ENV_MISSING:{key}")
    leaked = sorted(
        key
        for key in (*FORBIDDEN_CODE_ENVIRONMENT, *FORBIDDEN_NETWORK_ENVIRONMENT)
        if key in environment
    )
    if leaked:
        raise RuntimeError("ALL_PAPER_PROCESS_FORBIDDEN_ENVIRONMENT_LEAK:" + ",".join(leaked))
    unexpected = sorted(set(environment) - ALLOWED_PROCESS_ENVIRONMENT_KEYS)
    if unexpected:
        raise RuntimeError("ALL_PAPER_PROCESS_ENVIRONMENT_NOT_ALLOWLISTED:" + ",".join(unexpected))
    return {
        "keys": sorted(environment),
        "telegram_values_redacted": True,
        "forbidden_code_loading_present": False,
        "allowlisted": True,
    }


def _probe_interpreter(expected_python: Path, app_dir: Path) -> dict:
    code = r'''
import importlib.util,json,site,sys
mods=["polymarket_scanner", "polymarket_scanner.weather_only_live_paper_all_signals_final_v9"]
origins={}
for name in mods:
    spec=importlib.util.find_spec(name)
    origins[name]=None if spec is None else spec.origin
print(json.dumps({"executable":sys.executable,"prefix":sys.prefix,"base_prefix":sys.base_prefix,"enable_user_site":site.ENABLE_USER_SITE,"sys_path":sys.path,"origins":origins},sort_keys=True))
'''
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": "/nonexistent",
        "PYTHONNOUSERSITE": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
    }
    proc = subprocess.run(
        [str(expected_python), "-I", "-s", "-c", code],
        cwd=app_dir,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError("ALL_PAPER_INTERPRETER_PROBE_FAILED")
    data = json.loads(proc.stdout)
    if Path(str(data.get("executable") or "")).absolute() != expected_python.absolute():
        raise RuntimeError("ALL_PAPER_PROBE_EXECUTABLE_MISMATCH")
    venv_root = expected_python.parent.parent.resolve()
    if Path(str(data.get("prefix") or "")).resolve() != venv_root:
        raise RuntimeError("ALL_PAPER_PROBE_VENV_PREFIX_MISMATCH")
    if data.get("enable_user_site") is not False:
        raise RuntimeError("ALL_PAPER_PROBE_USER_SITE_ENABLED")
    base_prefix = Path(str(data.get("base_prefix") or "")).resolve()
    if base_prefix == venv_root:
        raise RuntimeError("ALL_PAPER_PROBE_NOT_A_VIRTUALENV")
    for value in data.get("sys_path", []):
        if not value:
            raise RuntimeError("ALL_PAPER_PROBE_EMPTY_SYS_PATH_ENTRY")
        path = Path(str(value)).absolute()
        allowed = False
        for root in (app_dir.resolve(), venv_root, base_prefix):
            try:
                path.relative_to(root)
                allowed = True
                break
            except ValueError:
                pass
        if not allowed:
            raise RuntimeError("ALL_PAPER_PROBE_SYS_PATH_OUTSIDE_ALLOWED_ROOTS:" + str(path))
    for name, raw in dict(data.get("origins") or {}).items():
        if not raw:
            raise RuntimeError("ALL_PAPER_PROBE_MODULE_ORIGIN_MISSING:" + name)
        origin = Path(str(raw)).resolve()
        try:
            origin.relative_to(app_dir.resolve())
        except ValueError:
            raise RuntimeError("ALL_PAPER_PROBE_MODULE_ORIGIN_OUTSIDE_CANDIDATE:" + name) from None
    return data


def _normalized_facts(base, facts, raw_tokens: tuple[str, ...], expected_python: Path, app_dir: Path, release_file: Path):
    python_index = raw_tokens.index(str(expected_python))
    normalized_exec = (str(expected_python), "-m", FINAL_MODULE, *raw_tokens[python_index + 5 :])
    lines: list[str] = []
    for line in facts.unit_text.splitlines():
        stripped = line.strip()
        if stripped.startswith("ExecStartPre="):
            continue
        if stripped.startswith("ExecStart="):
            lines.append(
                f"ExecStartPre=/bin/bash {app_dir}/deploy/verify-runtime-release.sh {app_dir} {release_file}"
            )
            lines.append("ExecStart=" + " ".join(shlex.quote(part) for part in normalized_exec))
        else:
            lines.append(line)
    normalized_argv = facts.process_argv
    if facts.active:
        argv = tuple(facts.process_argv)
        if len(argv) < 5 or argv[:5] != (str(expected_python), "-I", "-s", "-m", FINAL_MODULE):
            raise RuntimeError("ALL_PAPER_PROCESS_ISOLATED_ARGV_INVALID")
        normalized_argv = (str(expected_python), "-m", FINAL_MODULE, *argv[5:])
    return dataclasses.replace(facts, unit_text="\n".join(lines) + "\n", process_argv=normalized_argv)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--release-file", type=Path, required=True)
    parser.add_argument("--generation-id", required=True)
    parser.add_argument("--unit", default="polymarket-weather-paper.service")
    parser.add_argument("--db", type=Path, default=Path("/var/lib/polymarket-weather-paper/weather-paper.sqlite"))
    parser.add_argument("--status", type=Path, default=Path("/var/lib/polymarket-weather-paper/status.json"))
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument("--require-active", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    base = _load_base()
    base.FINAL_ALL_PAPER_MODULE = FINAL_MODULE
    base.KNOWN_WEATHER_WRITER_MARKERS = tuple(
        dict.fromkeys((*base.KNOWN_WEATHER_WRITER_MARKERS, *_EXTRA_WRITER_MARKERS))
    )
    app_dir = args.app_dir.expanduser().resolve()
    release_file = args.release_file.expanduser().resolve()
    release_sha = release_file.read_text(encoding="utf-8").strip().lower()
    evidence_path = RELEASES_ROOT / release_sha / "release-evidence.json"
    environment_file = (
        args.environment_file.expanduser().resolve()
        if args.environment_file is not None
        else release_file.parent / "weather-paper.env"
    )
    try:
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        if evidence.get("release_sha") != release_sha or evidence.get("generation_id") != args.generation_id:
            raise RuntimeError("ALL_PAPER_RELEASE_EVIDENCE_IDENTITY_MISMATCH")
        if evidence.get("runtime_module") != FINAL_MODULE:
            raise RuntimeError("ALL_PAPER_RELEASE_EVIDENCE_MODULE_MISMATCH")
        expected_python = Path(str(evidence.get("interpreter_path") or ""))
        expected_venv = RELEASES_ROOT / release_sha / "venv"
        if expected_python != expected_venv / "bin/python":
            raise RuntimeError("ALL_PAPER_RELEASE_EVIDENCE_INTERPRETER_MISMATCH")

        environment_attestation = base._attest_environment_file(environment_file)
        facts = base.collect_facts(
            unit_name=args.unit,
            app_dir=app_dir,
            release_file=release_file,
            db_path=args.db.expanduser().resolve(),
        )
        raw_tokens, _ = _validate_unit_environment_and_exec(
            facts.unit_text,
            expected_python=expected_python,
            release_sha=release_sha,
            generation_id=args.generation_id,
            app_dir=app_dir,
            release_file=release_file,
            evidence=evidence_path,
        )
        process_environment_attestation = None
        if facts.active:
            if facts.main_pid is None:
                raise RuntimeError("ACTIVE_ALL_PAPER_SERVICE_MAINPID_MISSING")
            process_environment_attestation = _validate_process_environment(int(facts.main_pid))
        probe = _probe_interpreter(expected_python, app_dir)
        normalized = _normalized_facts(base, facts, raw_tokens, expected_python, app_dir, release_file)
        dotenv_attestation = base._attest_dotenv_boundary(app_dir, facts.unit_text, facts.main_pid)
        attestation = base.attest_weather_runtime(
            normalized,
            expected_app_dir=app_dir,
            expected_python=expected_python,
            expected_db_path=args.db.expanduser().resolve(),
            expected_status_path=args.status,
            expected_release_file=release_file,
            expected_environment_file=environment_file,
            expected_release_sha=release_sha,
            expected_module=FINAL_MODULE,
        )
        payload = {
            "facts": facts.as_dict(),
            "attestation": attestation.as_dict(),
            "environment_attestation": environment_attestation,
            "dotenv_attestation": dotenv_attestation,
            "code_loading_attestation": {
                "generation_id": args.generation_id,
                "release_evidence": str(evidence_path),
                "expected_python": str(expected_python),
                "expected_venv": str(expected_venv),
                "process_environment": process_environment_attestation,
                "interpreter_probe": probe,
                "python_isolated_mode": True,
                "python_user_site_disabled": True,
                "application_module_origins_inside_candidate": True,
            },
        }
        if args.require_active and not attestation.deployment_proven:
            payload["acceptance"] = "FAIL_ACTIVE_ALL_PAPER_RUNTIME_NOT_PROVEN"
            exit_code = 2
        elif attestation.deployment_proven:
            payload["acceptance"] = "PASS_CANONICAL_ALL_PAPER_ACTIVE_RUNTIME_ATTESTED"
            exit_code = 0
        else:
            payload["acceptance"] = "PASS_ALL_PAPER_UNIT_INACTIVE_NO_DEPLOYMENT_CLAIM"
            exit_code = 0
    except Exception as exc:
        payload = {
            "acceptance": "FAIL_ALL_PAPER_RUNTIME_ATTESTATION",
            "error": getattr(exc, "code", str(exc)),
        }
        exit_code = 2

    text = json.dumps(payload, sort_keys=True, indent=2) + "\n"
    if args.output is not None:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
        os.chmod(output, 0o600)
    sys.stdout.write(text)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
