from __future__ import annotations

import inspect
import json

import pytest

from polymarket_scanner import weather_only_acceptance_run as runner
from polymarket_scanner.weather_only_acceptance_bundle import build_weather_w7_acceptance_bundle
from polymarket_scanner.weather_only_acceptance_release import (
    WEATHER_W7_RELEASE_VERSION,
    WeatherW7ReleaseAttestation,
    _attestation_payload,
    _sha,
    build_weather_w7_release_manifest,
)
from polymarket_scanner.weather_only_acceptance_release_bundle import (
    build_weather_w7_release_bound_bundle,
    load_weather_w7_release_bound_bundle_json,
)
from dataclasses import replace
from test_weather_only_acceptance_bundle import SHA, START, _base_envelope, _unchanged_containment


def _attestation(at: float):
    shell = WeatherW7ReleaseAttestation(
        version=WEATHER_W7_RELEASE_VERSION,
        captured_at=at,
        release_sha=SHA,
        git_head_sha=SHA,
        release_marker_sha256="1" * 64,
        app_dir_sha256="2" * 64,
        scanner_cwd_sha256="2" * 64,
        runtime_source_sha256="3" * 64,
        scanner_process_id=321,
        evidence_sha256="0" * 64,
    )
    return replace(shell, evidence_sha256=_sha(_attestation_payload(shell)))


def _bundle(tmp_path):
    _, containment = _unchanged_containment(tmp_path)
    inner = build_weather_w7_acceptance_bundle(
        w7_evidence=_base_envelope(),
        containment_manifest=containment,
    )
    release = build_weather_w7_release_manifest(
        before=_attestation(START - 2.0),
        after=_attestation(START + 2702.0),
    )
    return build_weather_w7_release_bound_bundle(
        acceptance_bundle=inner,
        release_manifest=release,
    )


def test_runner_atomic_output_is_0600_and_round_trips(tmp_path):
    bundle = _bundle(tmp_path)
    path = tmp_path / "w7-release-bound.json"
    runner.atomic_write_weather_w7_release_bundle(path, bundle)
    assert path.stat().st_mode & 0o777 == 0o600
    loaded, report = load_weather_w7_release_bound_bundle_json(
        path.read_text(encoding="utf-8"),
        expected_release_sha=SHA,
    )
    assert loaded == bundle
    assert report.passed is True


def test_runner_rejects_symlink_output_and_exposes_no_service_control(tmp_path):
    bundle = _bundle(tmp_path)
    target = tmp_path / "target.json"
    target.write_text("{}", encoding="utf-8")
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(runner.WeatherW7RunnerError) as raised:
        runner.atomic_write_weather_w7_release_bundle(link, bundle)
    assert raised.value.code == "W7_RUNNER_OUTPUT_PATH_INVALID"

    source = inspect.getsource(runner)
    for forbidden in ("systemctl", "subprocess", "start_service", "stop_service", "restart_service"):
        assert forbidden not in source
    assert "financial_authority\": False" in source
