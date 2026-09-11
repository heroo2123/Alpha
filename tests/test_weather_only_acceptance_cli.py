from __future__ import annotations

import pytest

from polymarket_scanner.weather_only_acceptance import WeatherW7RunEvidence, WeatherW7Sample
from polymarket_scanner.weather_only_acceptance_cli import (
    WeatherW7CliError,
    validate_weather_w7_evidence_file,
)
from polymarket_scanner.weather_only_acceptance_evidence import (
    build_weather_w7_evidence_envelope,
    dump_weather_w7_evidence_json,
)


SHA = "a" * 40
START = 1_800_000_000.0


def _evidence(*, outbox_after=5) -> WeatherW7RunEvidence:
    samples = []
    for index in range(91):
        has_source = index == 45
        samples.append(WeatherW7Sample(
            observed_at=START + index * 30.0,
            cycle_ok=True,
            process_rss_bytes=200 * 1024 * 1024,
            swap_used_bytes=0,
            host_mem_available_bytes=200 * 1024 * 1024,
            incremental_evaluation_seconds=1.0,
            incremental_evaluation_evidence_sha256="1" * 64,
            source_update_confirmation_seconds=4.0 if has_source else None,
            source_update_evidence_sha256="2" * 64 if has_source else None,
            weather_event_count=300,
            non_weather_materialized_count=0,
            exact_clob_required_for_candidates=True,
            financial_authority=False,
            financial_delivery=False,
            automatic_order_placement=False,
        ))
    return WeatherW7RunEvidence(
        release_sha=SHA,
        samples=tuple(samples),
        telegram_outbox_before=5,
        telegram_outbox_after=outbox_after,
        detector_promotions=0,
        order_attempts=0,
        actual_orders_placed=0,
        actual_fills_recorded=0,
        service_restart_count=0,
    )


def _write(tmp_path, evidence):
    envelope = build_weather_w7_evidence_envelope(
        evidence,
        created_at=START + 91 * 30.0,
    )
    path = tmp_path / "w7.json"
    path.write_text(dump_weather_w7_evidence_json(envelope), encoding="utf-8")
    return path


def test_offline_cli_validator_returns_pass_report_for_valid_envelope(tmp_path):
    path = _write(tmp_path, _evidence())
    report = validate_weather_w7_evidence_file(path, expected_release_sha=SHA)
    assert report.passed is True
    assert report.financial_authority is False
    assert report.automatic_order_placement is False


def test_offline_cli_validator_preserves_valid_but_failing_acceptance(tmp_path):
    path = _write(tmp_path, _evidence(outbox_after=6))
    report = validate_weather_w7_evidence_file(path, expected_release_sha=SHA)
    assert report.passed is False
    assert "TELEGRAM_OUTBOX_CHANGED" in report.reasons


def test_offline_cli_rejects_tamper_wrong_release_symlink_and_empty_file(tmp_path):
    path = _write(tmp_path, _evidence())
    raw = path.read_text(encoding="utf-8").replace('"process_rss_bytes": 209715200', '"process_rss_bytes": 1', 1)
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(WeatherW7CliError) as tampered:
        validate_weather_w7_evidence_file(path)
    assert tampered.value.code.startswith("W7_CLI_EVIDENCE_INVALID:W7_EVIDENCE_SHA_MISMATCH")

    path = _write(tmp_path, _evidence())
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
