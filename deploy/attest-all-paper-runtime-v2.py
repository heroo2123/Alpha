#!/usr/bin/env python3
from __future__ import annotations

"""Strict additive attestation for the final V9 all-PAPER runtime."""

import importlib.util
from pathlib import Path


FINAL_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final_v9"
DOTENV_ENV_LINE = "Environment=ALPHA_DISABLE_DOTENV=1"
FORBIDDEN_NETWORK_ENVIRONMENT = (
    "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY",
    "http_proxy", "https_proxy", "all_proxy", "no_proxy",
    "SSL_CERT_FILE", "SSL_CERT_DIR",
)
_EXTRA_WRITER_MARKERS = (
    "weather_only_live_paper_all_signals_final_v5.py",
    "weather_only_live_paper_all_signals_final_v6.py",
    "weather_only_live_paper_all_signals_final_v7.py",
    "weather_only_live_paper_all_signals_final_v8.py",
    "weather_only_live_paper_all_signals_final_v9.py",
)


def _load_base():
    path = Path(__file__).with_name("attest-all-paper-runtime.py")
    spec = importlib.util.spec_from_file_location("_all_paper_attest_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ALL_PAPER_BASE_ATTESTER_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); return module


def _proc_environment(pid: int) -> dict[str, str]:
    raw = Path(f"/proc/{int(pid)}/environ").read_bytes(); out: dict[str, str] = {}
    for item in raw.split(b"\0"):
        if not item: continue
        text = item.decode("utf-8", errors="strict")
        if "=" in text:
            key, value = text.split("=", 1); out[key] = value
    return out


def main() -> int:
    base = _load_base()
    base.FINAL_ALL_PAPER_MODULE = FINAL_MODULE
    base.KNOWN_WEATHER_WRITER_MARKERS = tuple(dict.fromkeys((*base.KNOWN_WEATHER_WRITER_MARKERS, *_EXTRA_WRITER_MARKERS)))
    original_collect = base.collect_facts

    def collect_facts(*, unit_name, app_dir, release_file, db_path):
        app = Path(app_dir).expanduser().resolve()
        dotenv = app / ".env"
        if dotenv.exists() or dotenv.is_symlink():
            raise RuntimeError("ALL_PAPER_IMPLICIT_DOTENV_PRESENT")
        facts = original_collect(unit_name=unit_name, app_dir=app, release_file=release_file, db_path=db_path)
        if facts.unit_text.count(DOTENV_ENV_LINE) != 1:
            raise RuntimeError("ALL_PAPER_UNIT_DOTENV_DISABLE_MISSING_OR_DUPLICATED")
        unset_lines = [line.strip() for line in facts.unit_text.splitlines() if line.strip().startswith("UnsetEnvironment=")]
        unset_names: set[str] = set()
        for line in unset_lines:
            unset_names.update(line.split("=", 1)[1].split())
        missing_unset = sorted(set(FORBIDDEN_NETWORK_ENVIRONMENT) - unset_names)
        if missing_unset:
            raise RuntimeError("ALL_PAPER_UNIT_NETWORK_ENV_UNSET_INCOMPLETE:" + ",".join(missing_unset))
        if facts.active:
            if facts.main_pid is None:
                raise RuntimeError("ACTIVE_ALL_PAPER_SERVICE_MAINPID_MISSING")
            env = _proc_environment(int(facts.main_pid))
            if env.get("ALPHA_DISABLE_DOTENV") != "1":
                raise RuntimeError("ALL_PAPER_PROCESS_DOTENV_DISABLE_NOT_PROVEN")
            leaked = sorted(name for name in FORBIDDEN_NETWORK_ENVIRONMENT if name in env)
            if leaked:
                raise RuntimeError("ALL_PAPER_PROCESS_NETWORK_ENVIRONMENT_LEAK:" + ",".join(leaked))
        return facts

    base.collect_facts = collect_facts
    return int(base.main())


if __name__ == "__main__":
    raise SystemExit(main())
