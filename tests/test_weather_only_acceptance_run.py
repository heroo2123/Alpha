from __future__ import annotations

import hashlib
import inspect

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
from polymarket_scanner.weather_only_acceptance_release_cli import (
    WeatherW7ReleaseCliError,
    validate_weather_w7_release_bundle_file,
)
from dataclasses import replace
from test_weather_only_acceptance_bundle import SHA, START, _base_envelope, _unchanged_containment


CMDLINE_SHA = hashlib.sha256(b"python\0-m\0polymarket_scanner.weather_only_runtime\0").hexdigest()


def _attestation(at: float):
    shell = WeatherW7ReleaseAttestation(
        version=WEATHER_W7_RELEASE_VERSION,
        captured_at=at,
        release_sha=SHA,
        git_head_sha=SHA,
        release_marker_sha256="1" * 64,
        app_dir_sha256="2" * 64,
        scanner_cwd_sha256="2" * 64,
        scanner_cmdline_sha256=CMDLINE_SHA,
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


def test_runner_atomic_output_is_0600_round_trips_and_offline_validates(tmp_path):
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
    offline = validate_weather_w7_release_bundle_file(path, expected_release_sha=SHA)
    assert offline.passed is True


def test_runner_and_offline_validator_reject_symlink_and_expose_no_service_control(tmp_path):
    bundle = _bundle(tmp_path)
    target = tmp_path / "target.json"
    runner.atomic_write_weather_w7_release_bundle(target, bundle)
    link = tmp_path / "link.json"
    link.symlink_to(target)
    with pytest.raises(runner.WeatherW7RunnerError) as raised:
        runner.atomic_write_weather_w7_release_bundle(link, bundle)
    assert raised.value.code == "W7_RUNNER_OUTPUT_PATH_INVALID"
    with pytest.raises(WeatherW7ReleaseCliError) as offline:
        validate_weather_w7_release_bundle_file(link)
    assert offline.value.code == "W7_RELEASE_CLI_EVIDENCE_FILE_INVALID"

    source = inspect.getsource(runner)
    for forbidden in ("systemctl", "subprocess", "start_service", "stop_service", "restart_service"):
        assert forbidden not in source
    assert '"financial_authority": False' in source
