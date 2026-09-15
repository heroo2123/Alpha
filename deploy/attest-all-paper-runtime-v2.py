#!/usr/bin/env python3
from __future__ import annotations

"""Strict additive attestation for the operator-synchronized all-PAPER runtime.

This wrapper preserves the mature process/release/unit checks in
``attest-all-paper-runtime.py`` and adds the configuration invariants required by the
final corrective runtime: final-v7 must be the executable module, dotenv loading must
be explicitly disabled in the unit and live process, and no ignored checkout ``.env``
may exist.
"""

import importlib.util
import os
from pathlib import Path


FINAL_MODULE = "polymarket_scanner.weather_only_live_paper_all_signals_final_v7"
DOTENV_ENV_LINE = "Environment=ALPHA_DISABLE_DOTENV=1"
_EXTRA_WRITER_MARKERS = (
    "weather_only_live_paper_all_signals_final_v5.py",
    "weather_only_live_paper_all_signals_final_v6.py",
    "weather_only_live_paper_all_signals_final_v7.py",
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
        if "=" not in text:
            continue
        key, value = text.split("=", 1)
        out[key] = value
    return out


def main() -> int:
    base = _load_base()
    base.FINAL_ALL_PAPER_MODULE = FINAL_MODULE
    base.KNOWN_WEATHER_WRITER_MARKERS = tuple(
        dict.fromkeys((*base.KNOWN_WEATHER_WRITER_MARKERS, *_EXTRA_WRITER_MARKERS))
    )
    original_collect = base.collect_facts

    def collect_facts(*, unit_name, app_dir, release_file, db_path):
        app = Path(app_dir).expanduser().resolve()
        dotenv = app / ".env"
        if dotenv.exists() or dotenv.is_symlink():
            raise RuntimeError("ALL_PAPER_IMPLICIT_DOTENV_PRESENT")

        facts = original_collect(
            unit_name=unit_name,
            app_dir=app,
            release_file=release_file,
            db_path=db_path,
        )
        if facts.unit_text.count(DOTENV_ENV_LINE) != 1:
            raise RuntimeError("ALL_PAPER_UNIT_DOTENV_DISABLE_MISSING_OR_DUPLICATED")
        if facts.active:
            if facts.main_pid is None:
                raise RuntimeError("ACTIVE_ALL_PAPER_SERVICE_MAINPID_MISSING")
            env = _proc_environment(int(facts.main_pid))
            if env.get("ALPHA_DISABLE_DOTENV") != "1":
                raise RuntimeError("ALL_PAPER_PROCESS_DOTENV_DISABLE_NOT_PROVEN")
        return facts

    base.collect_facts = collect_facts
    return int(base.main())


if __name__ == "__main__":
    raise SystemExit(main())
