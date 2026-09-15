#!/usr/bin/env python3
from __future__ import annotations

"""Run the mature first-cycle waiter with final operator/config acceptance."""

import importlib.util
from pathlib import Path

from polymarket_scanner.weather_only_all_paper_deployment_acceptance_v2 import (
    accept_first_all_paper_cycle_v2,
)


def _load_base():
    path = Path(__file__).with_name("verify-all-paper-first-cycle.py")
    spec = importlib.util.spec_from_file_location("_all_paper_first_cycle_base", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("ALL_PAPER_BASE_FIRST_CYCLE_IMPORT_FAILED")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    base = _load_base()
    base.accept_first_all_paper_cycle = accept_first_all_paper_cycle_v2
    return int(base.main())


if __name__ == "__main__":
    raise SystemExit(main())
