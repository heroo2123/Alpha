from __future__ import annotations

import json
from dataclasses import replace

import pytest

from polymarket_scanner.weather_only_acceptance_bundle import build_weather_w7_acceptance_bundle
from polymarket_scanner.weather_only_acceptance_release import (
    WEATHER_W7_RELEASE_VERSION,
    WeatherW7ReleaseAttestation,
    _attestation_payload,
    _sha as _release_sha,
    build_weather_w7_release_manifest,
)
from polymarket_scanner.weather_only_acceptance_release_bundle import (
    WeatherW7ReleaseBundleError,
    build_weather_w7_release_bound_bundle,
    dump_weather_w7_release_bound_bundle_json,
    load_weather_w7_release_bound_bundle_json,
    validate_weather_w7_release_bound_bundle,
)
from test_weather_only_acceptance_bundle import SHA, START, _base_envelope, _unchanged_containment


def _attestation(captured_at: float, *, pid: int = 321, runtime_sha: str = "d" * 64):
    shell = WeatherW7ReleaseAttestation(
        version=WEATHER_W7_RELEASE_VERSION,
        captured_at=captured_at,
        release_sha=SHA,
        git_head_sha=SHA,
        release_marker_sha256="1" * 64,
        app_dir_sha256="2" * 64,
        scanner_cwd_sha256="2" * 64,
        runtime_source_sha256=runtime_sha,
        scanner_process_id=pid,
        evidence_sha256="0" * 64,
    )
    return replace(shell, evidence_sha256=_release_sha(_attestation_payload(shell)))


def _release_manifest():
    return build_weather_w7_release_manifest(
        before=_attestation(START - 2.0),
        after=_attestation(START + 2702.0),
    )


def _bundle(tmp_path):
    _, containment = _unchanged_containment(tmp_path)
    inner = build_weather_w7_acceptance_bundle(
        w7_evidence=_base_envelope(),
        containment_manifest=containment,
    )
    return build_weather_w7_release_bound_bundle(
        acceptance_bundle=inner,
        release_manifest=_release_manifest(),
    )


def test_release_bound_bundle_round_trip_and_expected_sha(tmp_path):
    bundle = _bundle(tmp_path)
    loaded, report = load_weather_w7_release_bound_bundle_json(
        dump_weather_w7_release_bound_bundle_json(bundle),
        expected_release_sha=SHA,
    )
    assert loaded == bundle
    assert report.passed is True
    assert bundle.release_manifest.before.release_sha == SHA
    assert bundle.release_manifest.after.release_sha == SHA
    assert bundle.financial_authority is False
    assert bundle.financial_delivery is False
    assert bundle.detector_promotion_authority is False
    assert bundle.automatic_order_placement is False


def test_release_claim_process_and_time_must_cross_link_to_inner_bundle(tmp_path):
    bundle = _bundle(tmp_path)
    with pytest.raises(WeatherW7ReleaseBundleError) as expected:
        validate_weather_w7_release_bound_bundle(bundle, expected_release_sha="b" * 40)
    assert expected.value.code == "W7_RELEASE_BUNDLE_EXPECTED_SHA_MISMATCH"

    wrong_pid_manifest = build_weather_w7_release_manifest(
        before=_attestation(START - 2.0, pid=999),
        after=_attestation(START + 2702.0, pid=999),
    )
    with pytest.raises(WeatherW7ReleaseBundleError) as pid:
        build_weather_w7_release_bound_bundle(
            acceptance_bundle=bundle.acceptance_bundle,
            release_manifest=wrong_pid_manifest,
        )
    assert pid.value.code == "W7_RELEASE_BUNDLE_PROCESS_ID_MISMATCH"

    late_manifest = build_weather_w7_release_manifest(
        before=_attestation(START + 1.0),
        after=_attestation(START + 2702.0),
    )
    with pytest.raises(WeatherW7ReleaseBundleError) as late:
        build_weather_w7_release_bound_bundle(
            acceptance_bundle=bundle.acceptance_bundle,
            release_manifest=late_manifest,
        )
    assert late.value.code == "W7_RELEASE_BUNDLE_RELEASE_STARTED_AFTER_SAMPLES"


def test_release_identity_change_and_outer_digest_tamper_fail_closed(tmp_path):
    bundle = _bundle(tmp_path)
    changed = _attestation(START + 2702.0, runtime_sha="e" * 64)
    with pytest.raises(Exception):
        build_weather_w7_release_manifest(
            before=_attestation(START - 2.0),
            after=changed,
        )

    raw = json.loads(dump_weather_w7_release_bound_bundle_json(bundle))
    raw["release_manifest"]["before"]["captured_at"] = START - 3.0
    with pytest.raises(WeatherW7ReleaseBundleError) as tamper:
        load_weather_w7_release_bound_bundle_json(json.dumps(raw))
    assert tamper.value.code.startswith("W7_RELEASE_BUNDLE_MANIFEST_INVALID:") or tamper.value.code == "W7_RELEASE_BUNDLE_DIGEST_MISMATCH"
