#!/usr/bin/env python3
from __future__ import annotations

"""Extract the exact weather-paper environment allowlist without printing secrets."""

import argparse
import os
from pathlib import Path


ALLOWED = ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "SYNOPTIC_PWS_TOKEN")
REQUIRED = ALLOWED


class EnvExtractionError(RuntimeError):
    pass


def extract(source: Path) -> list[str]:
    if not source.is_file() or source.is_symlink():
        raise EnvExtractionError("SOURCE_ENV_INVALID")
    found: dict[str, list[str]] = {key: [] for key in ALLOWED}
    for raw in source.read_text(encoding="utf-8").splitlines():
        stripped = raw.lstrip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key in found:
            if not value.strip():
                raise EnvExtractionError(f"{key}_EMPTY")
            found[key].append(stripped)
    for key in REQUIRED:
        if len(found[key]) != 1:
            raise EnvExtractionError(f"{key}_MISSING_OR_DUPLICATED")
    rows: list[str] = []
    for key in ALLOWED:
        rows.extend(found[key])
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        rows = extract(args.source.expanduser().resolve())
    except (OSError, UnicodeError, EnvExtractionError) as exc:
        print(getattr(exc, "args", ["ENV_EXTRACTION_FAILED"])[0])
        return 2
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(rows) + "\n", encoding="utf-8")
    os.chmod(output, 0o600)
    print("PASS_WEATHER_PAPER_ENV_ALLOWLIST")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
