from __future__ import annotations

import copy
import json

import pytest

from polymarket_scanner.weather_only_acceptance_evidence import (
    WEATHER_W7_EVIDENCE_VERSION,
    WeatherW7EvidenceError,
    _sha,
    build_weather_w7_evidence_envelope,
    dump_weather_w7_evidence_json,
    load_weather_w7_evidence_json,
    validate_weather_w7_evidence_envelope,
)
from polymarket_scanner.weather_only_runtime import WEATHER_SHADOW_RUNTIME_VERSION
from weather_w7_measurement_fixtures import build_w7_measurement_fixture


SHA = "a" * 40
START = 1_800_000_000.0


def _fixture(**kwargs):
    return build_w7_measurement_fixture(release_sha=SHA, start=START, **kwargs)


def _envelope():
    evidence, manifest = _fixture()
    return build_weather_w7_evidence_envelope(
        evidence,
        measurement_manifest=manifest,
        created_at=START + 91 * 30.0,
    )


def _envelope_json() -> str:
    return dump_weather_w7_evidence_json(_envelope())


def _rehash_outer(raw: dict) -> dict:
    payload = copy.deepcopy(raw)
    payload.pop("evidence_sha256", None)
    raw["evidence_sha256"] = _sha(payload)
    return raw


def test_w7_envelope_round_trip_binds_release_runtime_policy_and_actual_measurements():
    envelope = _envelope()
    assert envelope.version == WEATHER_W7_EVIDENCE_VERSION
    assert envelope.runtime_version == WEATHER_SHADOW_RUNTIME_VERSION
    assert len(envelope.policy_sha256) == 64
    assert len(envelope.evidence_sha256) == 64
    assert len(envelope.measurement_manifest.incremental_measurements) == 91
    assert len(envelope.measurement_manifest.source_update_measurements) == 1
    assert envelope.financial_authority is False

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


def test_resource_tampering_is_detected_by_outer_digest():
    raw = json.loads(_envelope_json())
    raw["run_evidence"]["samples"][10]["process_rss_bytes"] = 1
    with pytest.raises(WeatherW7EvidenceError) as raised:
        load_weather_w7_evidence_json(json.dumps(raw), expected_release_sha=SHA)
    assert raised.value.code == "W7_EVIDENCE_SHA_MISMATCH"


def test_latency_cannot_be_forged_even_if_attacker_rehashes_outer_envelope():
    raw = json.loads(_envelope_json())
    raw["run_evidence"]["samples"][10]["incremental_evaluation_seconds"] = 0.000001
    _rehash_outer(raw)
    with pytest.raises(WeatherW7EvidenceError) as raised:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert raised.value.code == "W7_EVIDENCE_INCREMENTAL_LATENCY_MISMATCH:10"

    raw = json.loads(_envelope_json())
    raw["run_evidence"]["samples"][45]["source_update_confirmation_seconds"] = 0.000001
    _rehash_outer(raw)
    with pytest.raises(WeatherW7EvidenceError) as source:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert source.value.code == "W7_EVIDENCE_SOURCE_LATENCY_MISMATCH:45"


def test_measurement_record_tampering_fails_even_if_outer_envelope_is_rehashed():
    raw = json.loads(_envelope_json())
    raw["measurement_manifest"]["incremental_measurements"][0]["incremental_evaluation_seconds"] = 0.1
    _rehash_outer(raw)
    with pytest.raises(WeatherW7EvidenceError) as raised:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert "W7_INCREMENTAL_MEASUREMENT_INVALID" in raised.value.code


def test_replayed_incremental_measurement_and_orphan_records_fail_closed():
    raw = json.loads(_envelope_json())
    first_ref = raw["run_evidence"]["samples"][0]["incremental_evaluation_evidence_sha256"]
    raw["run_evidence"]["samples"][1]["incremental_evaluation_evidence_sha256"] = first_ref
    _rehash_outer(raw)
    with pytest.raises(WeatherW7EvidenceError) as replayed:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert replayed.value.code == "W7_EVIDENCE_INCREMENTAL_MEASUREMENT_REPLAYED"

    raw = json.loads(_envelope_json())
    raw["measurement_manifest"]["incremental_measurements"].append(
        copy.deepcopy(raw["measurement_manifest"]["incremental_measurements"][0])
    )
    # Duplicate manifest records are rejected before an orphan can be hidden.
    manifest_payload = copy.deepcopy(raw["measurement_manifest"])
    manifest_payload.pop("manifest_sha256", None)
    raw["measurement_manifest"]["manifest_sha256"] = _sha(manifest_payload)
    _rehash_outer(raw)
    with pytest.raises(WeatherW7EvidenceError) as duplicate:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert "W7_INCREMENTAL_MEASUREMENT_DUPLICATE" in duplicate.value.code


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
    raw["measurement_manifest"]["extra"] = 1
    with pytest.raises(WeatherW7EvidenceError) as manifest_error:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert "W7_MEASUREMENT_MANIFEST_SCHEMA_INVALID" in manifest_error.value.code


def test_latency_measurement_sha_shape_and_nullable_pairing_fail_closed():
    raw = json.loads(_envelope_json())
    raw["run_evidence"]["samples"][0]["incremental_evaluation_evidence_sha256"] = "short"
    with pytest.raises(WeatherW7EvidenceError) as incremental:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert incremental.value.code == "W7_EVIDENCE_MEASUREMENT_SHA_INVALID"

    raw = json.loads(_envelope_json())
    raw["run_evidence"]["samples"][45]["source_update_evidence_sha256"] = None
    with pytest.raises(WeatherW7EvidenceError) as source:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert source.value.code == "W7_EVIDENCE_SAMPLE_INVALID:W7_SOURCE_UPDATE_EVIDENCE_SHA_INVALID"

    raw = json.loads(_envelope_json())
    raw["run_evidence"]["samples"][0]["source_update_evidence_sha256"] = "2" * 64
    with pytest.raises(WeatherW7EvidenceError) as dangling:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert dangling.value.code == "W7_EVIDENCE_SAMPLE_INVALID:W7_SOURCE_UPDATE_EVIDENCE_WITHOUT_LATENCY"


def test_runtime_policy_release_and_authority_drift_fail_closed():
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

    raw = json.loads(_envelope_json())
    raw["financial_authority"] = True
    with pytest.raises(WeatherW7EvidenceError) as authority:
        load_weather_w7_evidence_json(json.dumps(raw))
    assert authority.value.code == "W7_EVIDENCE_AUTHORITY_BOUNDARY_BROKEN"


def test_envelope_cannot_be_created_before_latest_sample_and_manifest_is_required():
    evidence, manifest = _fixture()
    with pytest.raises(WeatherW7EvidenceError) as raised:
        build_weather_w7_evidence_envelope(
            evidence,
            measurement_manifest=manifest,
            created_at=START + 10.0,
        )
    assert raised.value.code == "W7_EVIDENCE_CREATED_BEFORE_LAST_SAMPLE"


def test_build_rejects_runtime_version_drift_and_preserves_failing_run_without_authority():
    evidence, manifest = _fixture()
    with pytest.raises(WeatherW7EvidenceError) as raised:
        build_weather_w7_evidence_envelope(
            evidence,
            measurement_manifest=manifest,
            created_at=START + 91 * 30.0,
            runtime_version="stale-runtime",
        )
    assert raised.value.code == "W7_EVIDENCE_RUNTIME_VERSION_MISMATCH"

    failing, failing_manifest = _fixture(telegram_outbox_after=437)
    envelope = build_weather_w7_evidence_envelope(
        failing,
        measurement_manifest=failing_manifest,
        created_at=START + 91 * 30.0,
    )
    report = validate_weather_w7_evidence_envelope(envelope)
    assert report.passed is False
    assert "TELEGRAM_OUTBOX_CHANGED" in report.reasons
    assert report.financial_authority is False
