from __future__ import annotations

from dataclasses import replace

import pytest

from polymarket_scanner.weather_only_runtime_attestation import (
    CANONICAL_MODULE,
    WeatherRuntimeAttestationError,
    WeatherRuntimeFacts,
    attest_weather_runtime,
)


APP = "/opt/alpha"
PYTHON = "/opt/alpha/.venv/bin/python"
DB = "/var/lib/polymarket-weather-paper/weather-paper.sqlite"
SHA = "a" * 40
UNIT = "\n".join([
    "[Service]",
    f"WorkingDirectory={APP}",
    f"ExecStart={PYTHON} -m {CANONICAL_MODULE} --db {DB} --status /run/weather.json --release-file /opt/alpha/release.sha",
])
ARGV = (
    PYTHON,
    "-m",
    CANONICAL_MODULE,
    "--db",
    DB,
    "--status",
    "/run/weather.json",
    "--release-file",
    "/opt/alpha/release.sha",
)


def _facts(**changes):
    base = WeatherRuntimeFacts(
        unit_name="polymarket-weather-paper.service",
        unit_text=UNIT,
        active=True,
        main_pid=4242,
        process_cwd=APP,
        process_executable=PYTHON,
        process_argv=ARGV,
        repo_head_sha=SHA,
        release_marker_sha=SHA,
        matching_weather_process_argvs=(ARGV,),
    )
    return replace(base, **changes)


def _attest(facts):
    return attest_weather_runtime(
        facts,
        expected_app_dir=APP,
        expected_python=PYTHON,
        expected_db_path=DB,
        expected_release_sha=SHA,
    )


def test_canonical_single_process_with_matching_release_is_attested():
    result = _attest(_facts())
    assert result.installed_unit_attested is True
    assert result.process_attested is True
    assert result.deployment_proven is True
    assert result.inactive_safe_state is False
    assert "PROCESS_CANONICAL_ENTRYPOINT" in result.checks
    assert result.financial_authority is False


def test_correct_sha_with_stale_v3_execstart_fails_r22_gate():
    stale_unit = UNIT.replace(CANONICAL_MODULE, "polymarket_scanner.weather_only_live_paper_v3")
    with pytest.raises(WeatherRuntimeAttestationError, match="WEATHER_RUNTIME_UNIT_ENTRYPOINT_MISMATCH"):
        _attest(_facts(unit_text=stale_unit))


def test_running_wrong_interpreter_fails_even_when_unit_and_sha_are_correct():
    with pytest.raises(WeatherRuntimeAttestationError, match="WEATHER_RUNTIME_PROCESS_INTERPRETER_MISMATCH"):
        _attest(_facts(process_executable="/usr/bin/python3"))


def test_duplicate_obsolete_weather_process_fails_closed():
    old = (
        PYTHON,
        "-m",
        "polymarket_scanner.weather_only_live_paper_v3",
        "--db",
        DB,
    )
    with pytest.raises(WeatherRuntimeAttestationError, match="WEATHER_RUNTIME_DUPLICATE_OR_OBSOLETE_PROCESS"):
        _attest(_facts(matching_weather_process_argvs=(ARGV, old)))


def test_release_marker_or_checkout_drift_fails_before_process_claim():
    with pytest.raises(WeatherRuntimeAttestationError, match="WEATHER_RUNTIME_RELEASE_IDENTITY_MISMATCH"):
        _attest(_facts(release_marker_sha="b" * 40))
    with pytest.raises(WeatherRuntimeAttestationError, match="WEATHER_RUNTIME_RELEASE_IDENTITY_MISMATCH"):
        _attest(_facts(repo_head_sha="c" * 40))


def test_inactive_service_attests_installed_unit_but_does_not_claim_deployment():
    facts = _facts(
        active=False,
        main_pid=None,
        process_cwd=None,
        process_executable=None,
        process_argv=(),
        matching_weather_process_argvs=(),
    )
    result = _attest(facts)
    assert result.installed_unit_attested is True
    assert result.process_attested is False
    assert result.deployment_proven is False
    assert result.inactive_safe_state is True
    assert "SERVICE_INACTIVE_NO_DEPLOYMENT_CLAIM" in result.checks


def test_unit_db_path_drift_is_detected():
    wrong = UNIT.replace(DB, "/tmp/wrong.sqlite")
    with pytest.raises(WeatherRuntimeAttestationError, match="WEATHER_RUNTIME_UNIT_DB_MISMATCH"):
        _attest(_facts(unit_text=wrong))
