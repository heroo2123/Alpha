from __future__ import annotations

import json

import pytest

from polymarket_scanner.weather_only_acceptance import WeatherW7RunEvidence, WeatherW7Sample
from polymarket_scanner.weather_only_acceptance_evidence import (
    WEATHER_W7_EVIDENCE_VERSION,
    WeatherW7EvidenceError,
    build_weather_w7_evidence_envelope,
    dump_weather_w7_evidence_json,
    load_weather_w7_evidence_json,
    validate_weather_w7_evidence_envelope,
)
from polymarket_scanner.weather_only_runtime import WEATHER_SHADOW_RUNTIME_VERSION


SHA = "a" * 40
START = 1_800_000_000.0


def _sample(index: int, **overrides) -> WeatherW7Sample:
    values = dict(
        observed_at=START + index * 30.0,
        cycle_ok=True,
        process_rss_bytes=200 * 1024 * 1024,
        swap_used_bytes=0,
        host_mem_available_bytes=200 * 1024 * 1024,
        incremental_evaluation_seconds=1.0,
        source_update_confirmation_seconds=4.0 if index == 45 else None,
        weather_event_count=350,
        non_weather_materialized_count=0,
        exact_clob_required_for_candidates=True,
        financial_authority=False,
        financial_delivery=False,
        automatic_order_placement=False,
    )
    values.update(overrides)
    return WeatherW7Sample(**values)


def _evidence(**overrides) -> WeatherW7RunEvidence:
    values = dict(
        release_sha=SHA,
        samples=tuple(_sample(i) for i in range(91)),
        telegram_outbox_before=436,
        telegram_outbox_after=436,
        detector_promotions=0,
        order_attempts=0,
        actual_orders_placed=0,
        actual_fills_recorded=0,
        service_restart_count=0,
    )
    values.update(overrides)
    return WeatherW7RunEvidence(**values)


def _envelope_json() -> str:
    envelope = build_weather_w7_evidence_envelope(
        _evidence(),
        created_at=START + 91 * 30.0,
    )
    return dump_weather_w7_evidence_json(envelope)


def test_w7_envelope_round_trip_binds_release_runtime_policy_and_can_pass():
    envelope = build_weather_w7_evidence_envelope(
        _evidence(),
        created_at=START + 91 * 30.0,
    )
    assert envelope.version == WEATHER_W7_EVIDENCE_VERSION
    assert envelope.runtime_version == WEATHER_SHADOW_RUNTIME_VERSION
    assert len(envelope.policy_sha256) == 64
    assert len(envelope.evidence_sha256) == 64
    assert envelope.financial_authority is False
    assert envelope.financial_delivery is False
    assert envelope.detector_promotion_authority is False
    assert envelope.automatic_order_placement is False

    loaded, report = load_weather_w7_evidence_json(
        dump_weather_w7_evidence_json(envelope),
        expected_release_sha=SHA,
    )
    assert loaded == envelope
    assert report.passed is True
    assert report.release_sha == SHA
    assert report.financial_authority is False
    assert report.financial_delivery is False
    assert report.detector_promotion_authority is False
    assert report.automatic_order_placement is False


def test_sample_tampering_is_detected_by_outer_digest_before_acceptance():
    raw = json.loads(_envelope_json())
    raw["run_evidence"]["samples"][10]["process_rss_bytes"] = 1
    with pytest.raises(WeatherW7EvidenceError) as raised:
        load_weather_w7_evidence_json(json.dumps(raw), expected_release_sha=SHA)
    assert raised.value.code == "W7_EVIDENCE_SHA_MISMATCH"


def test_unknown_or_missing_fields_fail_closed_at_every_serialized_layer():
    raw = json.loads(_envelope_json())
    raw["unexpected"] = True
    with pytest.raises(WeatherW7EvidenceError) as envelope_error:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert envelope_error.value.code == "W7_EVIDENCE_ENVELOPE_SCHEMA_INVALID"

    raw = json.loads(_envelope_json())
    del raw["run_evidence"]["service_restart_count"]
    with pytest.raises(WeatherW7EvidenceError) as run_error:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert run_error.value.code == "W7_EVIDENCE_RUN_SCHEMA_INVALID"

    raw = json.loads(_envelope_json())
    raw["run_evidence"]["samples"][0]["extra"] = 1
    with pytest.raises(WeatherW7EvidenceError) as sample_error:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert sample_error.value.code == "W7_EVIDENCE_SAMPLE_SCHEMA_INVALID"


def test_runtime_policy_and_expected_release_drift_fail_closed():
    raw = json.loads(_envelope_json())
    raw["runtime_version"] = "other-runtime"
    with pytest.raises(WeatherW7EvidenceError) as runtime_error:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert runtime_error.value.code == "W7_EVIDENCE_RUNTIME_VERSION_MISMATCH"

    raw = json.loads(_envelope_json())
    raw["policy_sha256"] = "b" * 64
    with pytest.raises(WeatherW7EvidenceError) as policy_error:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert policy_error.value.code == "W7_EVIDENCE_POLICY_SHA_MISMATCH"

    with pytest.raises(WeatherW7EvidenceError) as release_error:
        load_weather_w7_evidence_json(_envelope_json(), expected_release_sha="b" * 40)
    assert release_error.value.code == "W7_EVIDENCE_EXPECTED_RELEASE_MISMATCH"


def test_release_must_match_inside_and_outside_run_payload():
    raw = json.loads(_envelope_json())
    raw["release_sha"] = "b" * 40
    with pytest.raises(WeatherW7EvidenceError) as raised:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert raised.value.code == "W7_EVIDENCE_RUN_RELEASE_MISMATCH"


def test_authority_bits_cannot_be_serialized_true_even_with_otherwise_valid_shape():
    raw = json.loads(_envelope_json())
    raw["financial_authority"] = True
    with pytest.raises(WeatherW7EvidenceError) as raised:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert raised.value.code == "W7_EVIDENCE_AUTHORITY_BOUNDARY_BROKEN"


def test_bool_as_integer_and_nonfinite_numbers_are_rejected():
    raw = json.loads(_envelope_json())
    raw["run_evidence"]["order_attempts"] = True
    with pytest.raises(WeatherW7EvidenceError) as integer_error:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert integer_error.value.code == "W7_EVIDENCE_INTEGER_INVALID"

    raw = json.loads(_envelope_json())
    raw["run_evidence"]["samples"][0]["incremental_evaluation_seconds"] = "nan"
    with pytest.raises(WeatherW7EvidenceError) as number_error:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert number_error.value.code == "W7_EVIDENCE_NUMBER_INVALID"


def test_envelope_cannot_be_created_before_its_latest_sample():
    with pytest.raises(WeatherW7EvidenceError) as raised:
        build_weather_w7_evidence_envelope(
            _evidence(),
            created_at=START + 10.0,
        )
    assert raised.value.code == "W7_EVIDENCE_CREATED_BEFORE_LAST_SAMPLE"


def test_build_rejects_runtime_version_drift_before_serialization():
    with pytest.raises(WeatherW7EvidenceError) as raised:
        build_weather_w7_evidence_envelope(
            _evidence(),
            created_at=START + 91 * 30.0,
            runtime_version="stale-runtime",
        )
    assert raised.value.code == "W7_EVIDENCE_RUNTIME_VERSION_MISMATCH"


def test_envelope_can_preserve_a_failing_run_without_granting_authority():
    evidence = _evidence(telegram_outbox_after=437)
    envelope = build_weather_w7_evidence_envelope(
        evidence,
        created_at=START + 91 * 30.0,
    )
    report = validate_weather_w7_evidence_envelope(envelope)
    assert report.passed is False
    assert "TELEGRAM_OUTBOX_CHANGED" in report.reasons
    assert report.financial_authority is False
