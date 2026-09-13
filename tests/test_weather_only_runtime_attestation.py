from __future__ import annotations

from dataclasses import replace

import pytest

from polymarket_scanner.weather_only_runtime_attestation import (
    CANONICAL_MODULE,
    WeatherRuntimeAttestationError,
    WeatherRuntimeFacts,
    attest_weather_runtime,
)


APP = "/opt/weather-paper"
PYTHON = f"{APP}/.venv/bin/python"
DB = "/var/lib/polymarket-weather-paper/weather-paper.sqlite"
STATUS = "/var/lib/polymarket-weather-paper/status.json"
RELEASE_FILE = "/home/test/.polymarket-edge-scanner/weather-paper-release.sha"
ENV_FILE = "/home/test/.polymarket-edge-scanner/weather-paper.env"
SHA = "a" * 40
UNIT = "\n".join([
    "[Service]",
    f"WorkingDirectory={APP}",
    f"EnvironmentFile={ENV_FILE}",
    f"ExecStartPre=/bin/bash {APP}/deploy/verify-runtime-release.sh {APP} {RELEASE_FILE}",
    f"ExecStart={PYTHON} -m {CANONICAL_MODULE} --db {DB} --status {STATUS} --release-file {RELEASE_FILE} --interval-seconds 180 --forecast-cache-seconds 900 --forecast-raw-gap-min 0.08 --max-forecast-events 6 --paper-stake-usd 10",
    "NoNewPrivileges=true",
    "PrivateTmp=true",
    "PrivateDevices=true",
    "ProtectHome=read-only",
    "ProtectSystem=strict",
    "ProtectKernelTunables=true",
    "ProtectKernelModules=true",
    "ProtectControlGroups=true",
    "RestrictSUIDSGID=true",
    "LockPersonality=true",
    "MemorySwapMax=0",
    "CapabilityBoundingSet=",
    "AmbientCapabilities=",
])
ARGV = (
    PYTHON,
    "-m",
    CANONICAL_MODULE,
    "--db",
    DB,
    "--status",
    STATUS,
    "--release-file",
    RELEASE_FILE,
    "--interval-seconds",
    "180",
    "--forecast-cache-seconds",
    "900",
    "--forecast-raw-gap-min",
    "0.08",
    "--max-forecast-events",
    "6",
    "--paper-stake-usd",
    "10",
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
        expected_status_path=STATUS,
        expected_release_file=RELEASE_FILE,
        expected_environment_file=ENV_FILE,
        expected_release_sha=SHA,
    )


def test_canonical_single_process_with_matching_isolated_release_is_attested():
    result = _attest(_facts())
    assert result.installed_unit_attested is True
    assert result.process_attested is True
    assert result.deployment_proven is True
    assert result.inactive_safe_state is False
    assert "PROCESS_CANONICAL_ENTRYPOINT" in result.checks
    assert "UNIT_ISOLATED_ENV_FILE_MATCH" in result.checks
    assert "UNIT_RELEASE_FILE_MATCH" in result.checks
    assert "UNIT_HARDENING_MATCH" in result.checks
    assert result.financial_authority is False


def test_correct_sha_with_stale_v3_execstart_fails_r22_gate():
    stale_unit = UNIT.replace(CANONICAL_MODULE, "polymarket_scanner.weather_only_live_paper_v3")
    with pytest.raises(WeatherRuntimeAttestationError, match="WEATHER_RUNTIME_UNIT_ENTRYPOINT_MISMATCH"):
        _attest(_facts(unit_text=stale_unit))


def test_running_wrong_interpreter_fails_even_when_unit_and_sha_are_correct():
    with pytest.raises(WeatherRuntimeAttestationError, match="WEATHER_RUNTIME_PROCESS_INTERPRETER_MISMATCH"):
        _attest(_facts(process_executable="/usr/bin/python3"))


def test_duplicate_obsolete_weather_process_fails_closed():
    old = (PYTHON, "-m", "polymarket_scanner.weather_only_live_paper_v3", "--db", DB)
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
    assert "NO_ORPHAN_WEATHER_PROCESS" in result.checks
    assert "SERVICE_INACTIVE_NO_DEPLOYMENT_CLAIM" in result.checks


def test_inactive_service_rejects_manually_launched_or_orphaned_weather_process():
    facts = _facts(
        active=False,
        main_pid=None,
        process_cwd=None,
        process_executable=None,
        process_argv=(),
        matching_weather_process_argvs=(ARGV,),
    )
    with pytest.raises(
        WeatherRuntimeAttestationError,
        match="WEATHER_RUNTIME_ORPHAN_PROCESS_WHILE_SERVICE_INACTIVE",
    ):
        _attest(facts)


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        (DB, "/tmp/wrong.sqlite", "WEATHER_RUNTIME_UNIT_DB_MISMATCH"),
        (STATUS, "/tmp/wrong-status.json", "WEATHER_RUNTIME_UNIT_STATUS_MISMATCH"),
        (RELEASE_FILE, "/tmp/release.sha", "WEATHER_RUNTIME_UNIT_RELEASE_FILE_MISMATCH"),
        (ENV_FILE, "/tmp/bot.env", "WEATHER_RUNTIME_UNIT_ENV_FILE_MISMATCH"),
        (f"WorkingDirectory={APP}", "WorkingDirectory=/tmp", "WEATHER_RUNTIME_UNIT_WORKDIR_MISMATCH"),
    ],
)
def test_installed_unit_identity_drift_is_detected(old, new, code):
    wrong = UNIT.replace(old, new)
    with pytest.raises(WeatherRuntimeAttestationError, match=code):
        _attest(_facts(unit_text=wrong))


def test_unit_hardening_cannot_be_weakened_by_dropin_or_edit():
    wrong = UNIT.replace("NoNewPrivileges=true", "NoNewPrivileges=false")
    with pytest.raises(
        WeatherRuntimeAttestationError,
        match="WEATHER_RUNTIME_UNIT_HARDENING_NONEWPRIVILEGES_INVALID",
    ):
        _attest(_facts(unit_text=wrong))

    additive_cap = UNIT + "\nCapabilityBoundingSet=CAP_NET_ADMIN\n"
    with pytest.raises(
        WeatherRuntimeAttestationError,
        match="WEATHER_RUNTIME_UNIT_HARDENING_CAPABILITYBOUNDINGSET_INVALID",
    ):
        _attest(_facts(unit_text=additive_cap))


def test_active_process_must_use_exact_status_and_isolated_release_file():
    wrong_status = tuple("/tmp/wrong.json" if value == STATUS else value for value in ARGV)
    with pytest.raises(WeatherRuntimeAttestationError, match="WEATHER_RUNTIME_PROCESS_STATUS_MISMATCH"):
        _attest(_facts(process_argv=wrong_status, matching_weather_process_argvs=(wrong_status,)))

    wrong_release = tuple("/tmp/release.sha" if value == RELEASE_FILE else value for value in ARGV)
    with pytest.raises(WeatherRuntimeAttestationError, match="WEATHER_RUNTIME_PROCESS_RELEASE_FILE_MISMATCH"):
        _attest(_facts(process_argv=wrong_release, matching_weather_process_argvs=(wrong_release,)))


def test_once_mode_is_forbidden_for_deployed_service():
    unit = UNIT.replace(" --interval-seconds", " --once --interval-seconds")
    with pytest.raises(WeatherRuntimeAttestationError, match="WEATHER_RUNTIME_UNIT_ONCE_MODE_FORBIDDEN"):
        _attest(_facts(unit_text=unit))
