#!/usr/bin/env python3
from __future__ import annotations

"""Read-only host collector for the exact all-weather PAPER V8 runtime."""

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_SCRIPT_ROOT = Path(__file__).resolve().parent.parent
if str(_SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_ROOT))

from polymarket_scanner.weather_only_runtime_attestation import (  # noqa: E402
    WeatherRuntimeAttestationError,
    WeatherRuntimeFacts,
    attest_weather_runtime,
)


FINAL_ALL_PAPER_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_v8"
KNOWN_WEATHER_WRITER_MARKERS = (
    "polymarket_scanner.weather_only_live_paper",
    "weather_only_live_paper.py",
    "weather_only_live_paper_v2.py",
    "weather_only_live_paper_v3.py",
    "weather_only_live_paper_v4.py",
    "weather_only_live_paper_corrective.py",
    "weather_only_live_paper_final.py",
    "weather_only_live_paper_three_layer_validation.py",
    "weather_only_live_paper_all_signals.py",
    "weather_only_live_paper_all_signals_v2.py",
    "weather_only_live_paper_all_signals_v3.py",
    "weather_only_live_paper_all_signals_v4.py",
    "weather_only_live_paper_all_signals_v5.py",
    "weather_only_live_paper_all_signals_v6.py",
    "weather_only_live_paper_all_signals_v7.py",
    "weather_only_live_paper_all_signals_v8.py",
)


def _run(args: list[str], *, allow_nonzero: bool = False) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        args, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False
    )
    if completed.returncode != 0 and not allow_nonzero:
        raise RuntimeError(f"COMMAND_FAILED:{args[0]}:{completed.returncode}")
    return completed


def _read_sha(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip().split()[0].strip().lower()


def _repo_head(app_dir: Path) -> str:
    return _run(["git", "-C", str(app_dir), "rev-parse", "HEAD"]).stdout.strip().lower()


def _unit_text(unit_name: str) -> str:
    return _run(["systemctl", "cat", unit_name]).stdout


def _active(unit_name: str) -> bool:
    return _run(["systemctl", "is-active", "--quiet", unit_name], allow_nonzero=True).returncode == 0


def _enabled(unit_name: str) -> bool:
    return _run(["systemctl", "is-enabled", "--quiet", unit_name], allow_nonzero=True).returncode == 0


def _main_pid(unit_name: str) -> int | None:
    raw = _run(["systemctl", "show", unit_name, "--property", "MainPID", "--value"]).stdout.strip()
    try:
        value = int(raw)
    except ValueError:
        return None
    return value if value > 1 else None


def _proc_argv(pid: int) -> tuple[str, ...]:
    raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    return tuple(part.decode("utf-8", errors="strict") for part in raw.split(b"\0") if part)


def _matching_weather_processes(db_path: Path) -> tuple[tuple[str, ...], ...]:
    rows: list[tuple[str, ...]] = []
    db_text = str(db_path.expanduser().resolve())
    own_pid = os.getpid()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid == own_pid:
            continue
        try:
            argv = _proc_argv(pid)
        except (FileNotFoundError, PermissionError, ProcessLookupError, UnicodeDecodeError):
            continue
        joined = " ".join(argv)
        if db_text in joined or any(marker in joined for marker in KNOWN_WEATHER_WRITER_MARKERS):
            rows.append(argv)
    rows.sort(key=lambda row: " ".join(row))
    return tuple(rows)


def collect_facts(*, unit_name: str, app_dir: Path, release_file: Path, db_path: Path) -> WeatherRuntimeFacts:
    active = _active(unit_name)
    if not active and _enabled(unit_name):
        raise RuntimeError("INACTIVE_ALL_PAPER_SERVICE_STILL_ENABLED")
    pid = _main_pid(unit_name) if active else None
    if active and pid is None:
        raise RuntimeError("ACTIVE_ALL_PAPER_SERVICE_MAINPID_MISSING")
    if pid is None:
        cwd = None
        executable = None
        argv: tuple[str, ...] = ()
    else:
        cwd = os.readlink(f"/proc/{pid}/cwd")
        executable = os.readlink(f"/proc/{pid}/exe")
        argv = _proc_argv(pid)
    return WeatherRuntimeFacts(
        unit_name=unit_name,
        unit_text=_unit_text(unit_name),
        active=active,
        main_pid=pid,
        process_cwd=cwd,
        process_executable=executable,
        process_argv=argv,
        repo_head_sha=_repo_head(app_dir),
        release_marker_sha=_read_sha(release_file),
        matching_weather_process_argvs=_matching_weather_processes(db_path),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app-dir", type=Path, required=True)
    parser.add_argument("--release-file", type=Path, required=True)
    parser.add_argument("--unit", default="polymarket-weather-paper.service")
    parser.add_argument("--db", type=Path, default=Path("/var/lib/polymarket-weather-paper/weather-paper.sqlite"))
    parser.add_argument("--status", type=Path, default=Path("/var/lib/polymarket-weather-paper/status.json"))
    parser.add_argument("--environment-file", type=Path)
    parser.add_argument("--require-active", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    app_dir = args.app_dir.expanduser().resolve()
    release_file = args.release_file.expanduser().resolve()
    db_path = args.db.expanduser().resolve()
    environment_file = (
        args.environment_file.expanduser().resolve()
        if args.environment_file is not None
        else release_file.parent / "weather-paper.env"
    )
    python = app_dir / ".venv" / "bin" / "python"
    expected_release = _read_sha(release_file)
    try:
        facts = collect_facts(
            unit_name=args.unit, app_dir=app_dir, release_file=release_file, db_path=db_path
        )
        attestation = attest_weather_runtime(
            facts,
            expected_app_dir=app_dir,
            expected_python=python,
            expected_db_path=db_path,
            expected_status_path=args.status,
            expected_release_file=release_file,
            expected_environment_file=environment_file,
            expected_release_sha=expected_release,
            expected_module=FINAL_ALL_PAPER_MODULE,
        )
        payload = {"facts": facts.as_dict(), "attestation": attestation.as_dict()}
        if args.require_active and not attestation.deployment_proven:
            payload["acceptance"] = "FAIL_ACTIVE_ALL_PAPER_RUNTIME_NOT_PROVEN"
            exit_code = 2
        elif attestation.deployment_proven:
            payload["acceptance"] = "PASS_CANONICAL_ALL_PAPER_ACTIVE_RUNTIME_ATTESTED"
            exit_code = 0
        else:
            payload["acceptance"] = "PASS_ALL_PAPER_UNIT_INACTIVE_NO_DEPLOYMENT_CLAIM"
            exit_code = 0
    except (WeatherRuntimeAttestationError, RuntimeError, OSError, UnicodeError) as exc:
        payload = {"acceptance": "FAIL_ALL_PAPER_RUNTIME_ATTESTATION", "error": getattr(exc, "code", str(exc))}
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
