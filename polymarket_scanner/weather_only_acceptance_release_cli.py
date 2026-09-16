from __future__ import annotations

"""Offline validator for the final release-bound weather W7 evidence artifact."""

import argparse
import json
from pathlib import Path

from .weather_only_acceptance_release_bundle import (
    WeatherW7ReleaseBundleError,
    load_weather_w7_release_bound_bundle_json,
)


MAX_EVIDENCE_BYTES = 16 * 1024 * 1024


class WeatherW7ReleaseCliError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def validate_weather_w7_release_bundle_file(path: str | Path, *, expected_release_sha: str | None = None):
    target = Path(path)
    if target.is_symlink():
        raise WeatherW7ReleaseCliError("W7_RELEASE_CLI_EVIDENCE_FILE_INVALID")
    try:
        stat = target.stat()
    except OSError:
        raise WeatherW7ReleaseCliError("W7_RELEASE_CLI_EVIDENCE_FILE_INVALID") from None
    if not target.is_file() or stat.st_size <= 0 or stat.st_size > MAX_EVIDENCE_BYTES:
        raise WeatherW7ReleaseCliError("W7_RELEASE_CLI_EVIDENCE_SIZE_INVALID")
    try:
        raw = target.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise WeatherW7ReleaseCliError("W7_RELEASE_CLI_EVIDENCE_READ_FAILED") from None
    try:
        _, report = load_weather_w7_release_bound_bundle_json(
            raw,
            expected_release_sha=expected_release_sha,
        )
    except WeatherW7ReleaseBundleError as exc:
        raise WeatherW7ReleaseCliError(f"W7_RELEASE_CLI_EVIDENCE_INVALID:{exc.code}") from exc
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--release-sha")
    args = parser.parse_args()
    report = validate_weather_w7_release_bundle_file(
        args.evidence,
        expected_release_sha=args.release_sha,
    )
    print(json.dumps(report.as_dict(), sort_keys=True, indent=2))
    raise SystemExit(0 if report.passed else 2)


if __name__ == "__main__":
    main()
