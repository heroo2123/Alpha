#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import site
import subprocess
from pathlib import Path

FINAL_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final_v10"
FORBIDDEN = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
    "PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP",
    "PYTHONINSPECT", "PYTHONWARNINGS", "PYTHONBREAKPOINT",
    "LD_PRELOAD", "LD_LIBRARY_PATH",
)
AUTH = "/usr/local/libexec/polymarket-weather-paper-v2/authority.py"


def load_base():
    path = Path(__file__).with_name("attest-all-paper-runtime.py")
    spec = importlib.util.spec_from_file_location("_base", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def proc_env(pid: int) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in Path(f"/proc/{pid}/environ").read_bytes().split(b"\0"):
        if b"=" in item:
            key, value = item.split(b"=", 1)
            out[key.decode()] = value.decode()
    return out


def _assert_unit_contract(unit: str, *, python: str, final_module: str) -> None:
    exec_lines = [line for line in unit.splitlines() if line.startswith("ExecStart=")]
    if len(exec_lines) != 1:
        raise RuntimeError("ALL_PAPER_UNIT_EXECSTART_COUNT_INVALID")
    exec_line = exec_lines[0]
    if not exec_line.startswith("ExecStart=/usr/bin/env -i "):
        raise RuntimeError("ALL_PAPER_UNIT_ENV_ALLOWLIST_LAUNCH_MISSING")
    required_fragments = (
        f" {python} -E -s -m {final_module} ",
        " PATH=/usr/bin:/bin ",
        " PYTHONNOUSERSITE=1 ",
        " PYTHONDONTWRITEBYTECODE=1 ",
        " ALPHA_DISABLE_DOTENV=1 ",
        " TELEGRAM_BOT_TOKEN=${TELEGRAM_BOT_TOKEN} ",
        " TELEGRAM_CHAT_ID=${TELEGRAM_CHAT_ID} ",
    )
    padded = f" {exec_line} "
    for fragment in required_fragments:
        if fragment not in padded:
            raise RuntimeError("ALL_PAPER_UNIT_RUNTIME_IDENTITY_MISMATCH:" + fragment.strip())
    for row in ("Environment=PYTHONNOUSERSITE=1", "Environment=PYTHONDONTWRITEBYTECODE=1"):
        if row not in unit:
            raise RuntimeError("ALL_PAPER_UNIT_RUNTIME_IDENTITY_MISMATCH:" + row)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--release-file", type=Path, required=True)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--status", type=Path)
    parser.add_argument("--unit", default="polymarket-weather-paper.service")
    parser.add_argument("--require-active", action="store_true")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--expected-release-sha", required=True)
    parser.add_argument("--generation-id", required=True)
    args = parser.parse_args()

    base = load_base()
    base.FINAL_ALL_PAPER_MODULE = FINAL_MODULE
    base.KNOWN_WEATHER_WRITER_MARKERS = tuple(
        dict.fromkeys((*base.KNOWN_WEATHER_WRITER_MARKERS, "weather_only_live_paper_all_signals_final_v10.py"))
    )
    facts = base.collect_facts(
        unit_name=args.unit,
        app_dir=args.app_dir,
        release_file=args.release_file,
        db_path=args.db,
    )

    sha = args.expected_release_sha.lower()
    if len(sha) != 40 or any(ch not in "0123456789abcdef" for ch in sha):
        raise RuntimeError("ALL_PAPER_EXPECTED_RELEASE_SHA_INVALID")
    release_root = args.app_dir.resolve() / ".releases" / sha
    argv0_expected = str(release_root / "venv/bin/python")
    expected_executable = (release_root / "venv/bin/python").resolve()
    _assert_unit_contract(facts.unit_text, python=argv0_expected, final_module=FINAL_MODULE)

    unset: set[str] = set()
    for line in facts.unit_text.splitlines():
        if line.startswith("UnsetEnvironment="):
            unset.update(line.split("=", 1)[1].split())
    missing = set(FORBIDDEN) - unset
    if missing:
        raise RuntimeError(
            "ALL_PAPER_UNIT_FORBIDDEN_ENV_UNSET_INCOMPLETE:" + ",".join(sorted(missing))
        )

    subprocess.run(
        [
            "/usr/bin/python3", AUTH, "verify-candidate-environment",
            "--generation-id", args.generation_id,
            "--app-dir", str(args.app_dir),
            "--candidate-sha", sha,
            "--environment-manifest", str(release_root / "environment-manifest.json"),
        ],
        check=True,
        env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"},
    )

    if args.require_active and not facts.active:
        raise RuntimeError("ALL_PAPER_ACTIVE_REQUIRED")
    if facts.active:
        pid = int(facts.main_pid)
        environment = proc_env(pid)
        leaked = [name for name in FORBIDDEN if name in environment]
        if leaked:
            raise RuntimeError("ALL_PAPER_PROCESS_FORBIDDEN_ENV:" + ",".join(sorted(leaked)))
        if environment.get("PYTHONNOUSERSITE") != "1":
            raise RuntimeError("ALL_PAPER_PROCESS_USER_SITE_FLAG_MISSING")
        allowed_process_names = {
            "PATH", "HOME", "LANG", "PYTHONUNBUFFERED", "PYTHONNOUSERSITE",
            "PYTHONDONTWRITEBYTECODE", "ALPHA_DISABLE_DOTENV",
            "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID",
        }
        unexpected = sorted(set(environment) - allowed_process_names)
        if unexpected:
            raise RuntimeError("ALL_PAPER_PROCESS_ENV_NOT_ALLOWLISTED:" + ",".join(unexpected))
        argv = list(facts.process_argv)
        if (
            not argv
            or argv[0] != argv0_expected
            or "-E" not in argv
            or "-s" not in argv
            or FINAL_MODULE not in argv
        ):
            raise RuntimeError("ALL_PAPER_PROCESS_ARGV_IDENTITY_MISMATCH")
        if Path(f"/proc/{pid}/exe").resolve() != expected_executable:
            raise RuntimeError("ALL_PAPER_PROCESS_EXECUTABLE_MISMATCH")

    status: dict = {}
    if args.status and args.status.exists():
        status = json.loads(args.status.read_text(encoding="utf-8"))
    if status:
        if status.get("runtime_python_executable") != argv0_expected:
            raise RuntimeError("ALL_PAPER_STATUS_PYTHON_EXECUTABLE_MISMATCH")
        if status.get("python_user_site_disabled") is not True:
            raise RuntimeError("ALL_PAPER_STATUS_USER_SITE_NOT_DISABLED")
        allowed_roots = [
            str(args.app_dir.resolve()),
            str((release_root / "venv").resolve()),
            "/usr/lib",
            "/usr/local/lib",
        ]
        for item in status.get("runtime_sys_path") or []:
            if not item:
                continue
            resolved = str(Path(item).resolve())
            if not any(
                resolved == root or resolved.startswith(root.rstrip("/") + "/")
                for root in allowed_roots
            ):
                raise RuntimeError("ALL_PAPER_SYS_PATH_UNAPPROVED:" + resolved)

    payload = {
        "acceptance": "PASS_ALL_PAPER_RUNTIME_V10_ATTESTATION",
        "release_sha": sha,
        "generation_id": args.generation_id,
        "final_module": FINAL_MODULE,
        "active": facts.active,
        "forbidden_environment_absent": True,
        "process_environment_allowlisted": True,
        "candidate_release_venv": argv0_expected,
        "financial_authority": False,
        "automatic_order_placement": False,
        "wallet_or_order_api_loaded": False,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
