"""Current host provenance, input-isolation and retired-entrypoint boundaries."""
from pathlib import Path
import subprocess
import pytest
from test_host_authority_production_boundary import ROOT, SOURCE, host, load, prepare

RETIRED = (
    "enable-all-paper-persistence.sh", "enable-weather-paper-persistence.sh",
    "pre-release-weather-paper-backup.sh", "preflight-all-paper-deployment.sh",
    "preflight-weather-paper-deployment.sh", "prepare-all-paper-candidate-v2.sh",
    "prepare-all-paper-candidate-v3.sh", "prepare-all-paper-candidate-v4.sh",
    "prepare-all-paper-candidate-v5.sh", "prepare-all-paper-candidate.sh",
    "prepare-weather-paper-candidate.sh", "restore-all-paper-rollback-v2.sh",
    "restore-all-paper-rollback.sh", "setup-all-paper-service.sh",
    "setup-weather-paper-backup-service.sh", "setup-weather-paper-service.sh",
    "snapshot-all-paper-rollback-v2.sh", "snapshot-all-paper-rollback.sh",
    "start-all-paper-candidate-v2.sh", "start-all-paper-candidate-v3.sh",
    "start-all-paper-candidate.sh", "start-weather-paper-candidate.sh",
)


def test_repository_cannot_bootstrap_or_replace_its_authorizer():
    for filename in ("deploy/install-weather-paper-host-trust.sh",
                     "host_trust/weather-paper-authority-v2/bootstrap-host-authority.sh",
                     "host_trust/weather-paper-authority-v3/bootstrap-host-authority.sh"):
        result = subprocess.run(["/bin/bash", str(ROOT / filename), "--bootstrap", "--bundle-sha256", "a" * 64], capture_output=True, text=True)
        assert result.returncode == 40 and "REFUSED:" in result.stderr
    module = load()
    with pytest.raises(module.AuthorityError, match="REFERENCE_AUTHORITY_IS_NOT_HOST_AUTHORITY"):
        module.verify_self()


def test_root_custody_requires_immutable_parent_not_just_file_mode(host):
    root = host.root / "renameable-parent"
    root.mkdir(mode=0o777)
    root.chmod(0o777)
    sealed = root / "authority.py"
    sealed.write_text("reference fixture")
    sealed.chmod(0o444)
    with pytest.raises(host.m.AuthorityError, match="DIRECTORY_CUSTODY_INVALID"):
        host.m._root_path_chain(root)


def test_authority_ignores_caller_path_loader_proxy_and_custom_ca_environment(host, monkeypatch):
    injected = set(host.m.FORBIDDEN_PROCESS_ENV) | {"HOME", "ALPHA_WEATHER_APP_DIR", "WEATHER_PAPER_DB_PATH"}
    for key in injected:
        monkeypatch.setenv(key, "/tmp/caller-controlled")
    result = host.real_run(["/usr/bin/env"])
    actual = dict(line.split("=", 1) for line in result.stdout.splitlines())
    assert not any(actual.get(key) == "/tmp/caller-controlled" for key in injected)
    assert actual["HOME"] == "/nonexistent"


def test_current_protocol_accepts_identities_not_candidate_chosen_privileged_paths():
    module = load()
    for forbidden in ("--app-dir", "--authority-source", "--bundle-sha256", "--deploy-uid"):
        with pytest.raises(SystemExit) as rejected:
            module._parser().parse_args(["create-cutover", "--candidate-sha", "a" * 40, forbidden, "/tmp/untrusted"])
        assert rejected.value.code == 2
    wrapper = (ROOT / "deploy/production-host-control.sh").read_text()
    assert "/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent" in wrapper
    assert '/usr/bin/python3 -I -s -E "${AUTH}"' in wrapper
    assert str(SOURCE) not in wrapper


@pytest.mark.parametrize("filename", RETIRED)
def test_retired_deployment_entrypoints_refuse_before_external_or_filesystem_operations(filename, tmp_path):
    result = subprocess.run(["/bin/bash", str(ROOT / "deploy" / filename), "a" * 40, "b" * 64],
                            cwd=tmp_path, env={"PATH": "/nonexistent", "HOME": str(tmp_path),
                            "ALPHA_WEATHER_APP_DIR": str(tmp_path / "must-not-create"), "ALPHA_CONFIG_DIR": str(tmp_path / "config")},
                            capture_output=True, text=True)
    assert result.returncode == 40 and "production-host-control.sh" in result.stderr
    assert not list(tmp_path.iterdir())
