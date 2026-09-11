from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_acceptance_cli import (
    WeatherW7CliError,
    validate_weather_w7_evidence_file,
)
from polymarket_scanner.weather_only_acceptance_evidence import (
    build_weather_w7_evidence_envelope,
    dump_weather_w7_evidence_json,
)
from weather_w7_measurement_fixtures import build_w7_measurement_fixture


SHA = "a" * 40
START = 1_800_000_000.0


def _fixture(*, outbox_after=5):
    return build_w7_measurement_fixture(
        release_sha=SHA,
        start=START,
        weather_event_count=300,
        telegram_outbox_before=5,
        telegram_outbox_after=outbox_after,
    )


def _write(tmp_path, *, outbox_after=5):
    evidence, manifest = _fixture(outbox_after=outbox_after)
    envelope = build_weather_w7_evidence_envelope(
        evidence,
        measurement_manifest=manifest,
        created_at=START + 91 * 30.0,
    )
    path = tmp_path / "w7.json"
    path.write_text(dump_weather_w7_evidence_json(envelope), encoding="utf-8")
    return path


def test_offline_cli_validator_returns_pass_report_for_valid_envelope(tmp_path):
    path = _write(tmp_path)
    report = validate_weather_w7_evidence_file(path, expected_release_sha=SHA)
    assert report.passed is True
    assert report.financial_authority is False
    assert report.automatic_order_placement is False


def test_offline_cli_validator_preserves_valid_but_failing_acceptance(tmp_path):
    path = _write(tmp_path, outbox_after=6)
    report = validate_weather_w7_evidence_file(path, expected_release_sha=SHA)
    assert report.passed is False
    assert "TELEGRAM_OUTBOX_CHANGED" in report.reasons


def test_offline_cli_rejects_tamper_wrong_release_symlink_and_empty_file(tmp_path):
    path = _write(tmp_path)
    raw = path.read_text(encoding="utf-8").replace('"process_rss_bytes": 209715200', '"process_rss_bytes": 1', 1)
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(WeatherW7CliError) as tampered:
        validate_weather_w7_evidence_file(path)
    assert tampered.value.code.startswith("W7_CLI_EVIDENCE_INVALID:W7_EVIDENCE_SHA_MISMATCH")

    path = _write(tmp_path)
    with pytest.raises(WeatherW7CliError) as wrong_release:
        validate_weather_w7_evidence_file(path, expected_release_sha="b" * 40)
    assert "W7_EVIDENCE_EXPECTED_RELEASE_MISMATCH" in wrong_release.value.code

    link = tmp_path / "link.json"
    link.symlink_to(path)
    with pytest.raises(WeatherW7CliError) as symlink:
        validate_weather_w7_evidence_file(link)
    assert symlink.value.code == "W7_CLI_EVIDENCE_FILE_INVALID"

    empty = tmp_path / "empty.json"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(WeatherW7CliError) as empty_error:
        validate_weather_w7_evidence_file(empty)
    assert empty_error.value.code == "W7_CLI_EVIDENCE_SIZE_INVALID"
