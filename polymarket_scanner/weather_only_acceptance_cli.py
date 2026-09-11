from __future__ import annotations

"""Offline validator for a completed weather W7 evidence envelope.

No networking, service control, database writes, Telegram access, order access or
credential loading occurs here.  The command only reads one bounded regular JSON file,
reconstructs the strict evidence envelope, re-evaluates the frozen W7 policy, and
prints the resulting research/shadow acceptance report.
"""

import argparse
import json
from pathlib import Path

from .weather_only_acceptance import WeatherW7AcceptanceReport
from .weather_only_acceptance_evidence import (
    WeatherW7EvidenceError,
    load_weather_w7_evidence_json,
)


MAX_W7_EVIDENCE_BYTES = 16 * 1024 * 1024


class WeatherW7CliError(RuntimeError):
    def __init__(self, code: str):
        self.code = str(code)
        super().__init__(self.code)


def validate_weather_w7_evidence_file(
    path: str | Path,
    *,
    expected_release_sha: str | None = None,
) -> WeatherW7AcceptanceReport:
    source = Path(path)
    try:
        if source.is_symlink() or not source.is_file():
            raise WeatherW7CliError("W7_CLI_EVIDENCE_FILE_INVALID")
        size = source.stat().st_size
    except OSError:
        raise WeatherW7CliError("W7_CLI_EVIDENCE_FILE_INVALID") from None
    if size <= 0 or size > MAX_W7_EVIDENCE_BYTES:
        raise WeatherW7CliError("W7_CLI_EVIDENCE_SIZE_INVALID")
    try:
        raw = source.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise WeatherW7CliError("W7_CLI_EVIDENCE_READ_FAILED") from None
    try:
        _, report = load_weather_w7_evidence_json(
            raw,
            expected_release_sha=expected_release_sha,
        )
    except WeatherW7EvidenceError as exc:
        raise WeatherW7CliError(f"W7_CLI_EVIDENCE_INVALID:{exc.code}") from None
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--expected-release-sha")
    args = parser.parse_args()
    try:
        report = validate_weather_w7_evidence_file(
            args.evidence,
            expected_release_sha=args.expected_release_sha,
        )
    except WeatherW7CliError as exc:
        print(json.dumps({
            "valid_evidence": False,
            "error": exc.code,
            "financial_authority": False,
            "financial_delivery": False,
            "detector_promotion_authority": False,
            "automatic_order_placement": False,
        }, sort_keys=True))
        raise SystemExit(3)

    print(json.dumps({
        "valid_evidence": True,
        "report": report.as_dict(),
        "financial_authority": False,
        "financial_delivery": False,
        "detector_promotion_authority": False,
        "automatic_order_placement": False,
    }, sort_keys=True))
    raise SystemExit(0 if report.passed else 2)


if __name__ == "__main__":
    main()
