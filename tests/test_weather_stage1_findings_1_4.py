"""Current F1-F4 regressions; obsolete installer/string checks were replaced.

Full cutover, corrupt-checkout recovery, fresh environments, and loader behavior
are exercised in test_host_authority_production_boundary.py. These cases retain
Stage1's approval and input-isolation coverage against the actual host protocol.
"""
from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from test_host_authority_production_boundary import ROOT, SOURCE, cutover, host, load, prepare, git


@pytest.mark.parametrize("relative", [
    "deploy/install-weather-paper-host-trust.sh",
    "host_trust/weather-paper-authority-v2/bootstrap-host-authority.sh",
    "host_trust/weather-paper-authority-v3/bootstrap-host-authority.sh",
])
def test_candidate_has_no_host_bootstrap_or_update_operation(relative):
    result = subprocess.run(["/bin/bash", str(ROOT / relative), "--bootstrap", "--authority-source", str(ROOT)],
                            capture_output=True, text=True, check=False)
    assert result.returncode == 40
    assert "REFUSED:" in result.stderr


def test_candidate_reference_cannot_assert_installed_authority_identity():
    m = load()
    with pytest.raises(m.AuthorityError, match="REFERENCE_AUTHORITY_IS_NOT_HOST_AUTHORITY"):
        m.verify_self()


@pytest.mark.parametrize("filename", ["release-gate.py", "restore-rollback.sh"])
def test_candidate_authentication_code_change_does_not_authorize_its_new_commit(host, filename):
    (host.app / filename).write_text("malicious replacement candidate\n")
    git(host.app, "add", filename)
    git(host.app, "commit", "-qm", "unauthorized host replacement")
    changed = git(host.app, "rev-parse", "HEAD")
    git(host.app, "checkout", "--detach", "-q", host.a)
    with pytest.raises(host.m.AuthorityError, match="NOT_INDEPENDENTLY_APPROVED"):
        host.m.create_cutover(changed, require_root=False)
    assert not host.m.ACTIVE.exists()


def test_branch_movement_does_not_change_approved_candidate_identity(host):
    git(host.app, "branch", "moving", host.b)
    git(host.app, "branch", "-f", "moving", host.a)
    gid = cutover(host)
    _, manifest = host.m._load_generation(gid, host.policy, require_root=False)
    assert manifest["candidate_sha"] == host.b
    assert manifest["predecessor_sha"] == host.a


def test_fresh_environments_exclude_polluted_predecessor_packages(host):
    site = next((host.app / ".venv/lib").glob("python*/site-packages"))
    (site / "injected_only.py").write_text("VALUE = 'injected'\n")
    (site / "removed_dependency.py").write_text("VALUE = 'removed'\n")
    before = host.m._tree_digest(host.app / ".venv")
    _, manifest = prepare(host)
    assert host.m._tree_digest(host.app / ".venv") == before
    for component in manifest["components"].values():
        python = str(Path(component["venv_path"]) / "bin/python")
        for name in ("injected_only", "removed_dependency"):
            result = subprocess.run([python, "-I", "-s", "-E", "-B", "-c", f"import {name}"],
                                    capture_output=True, text=True, check=False, env={"PATH": "/usr/bin:/bin", "HOME": "/nonexistent"})
            assert result.returncode != 0


@pytest.mark.parametrize("key", ["PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "PYTHONSTARTUP", "PYTHONINSPECT", "LD_PRELOAD", "LD_LIBRARY_PATH"])
def test_authority_subprocess_does_not_inherit_python_or_native_loader_environment(host, monkeypatch, key):
    monkeypatch.setenv(key, "/tmp/untrusted-fixture")
    result = host.real_run(["/usr/bin/env"])
    actual_keys = {line.split("=", 1)[0] for line in result.stdout.splitlines() if "=" in line}
    assert key not in actual_keys


def test_wrong_lock_digest_cannot_prepare_even_with_valid_release_sha(host):
    path = Path(host.policy["approval_file"])
    approvals = json.loads(path.read_text())
    next(row for row in approvals["approved"] if row["sha"] == host.b)["components"]["signals"]["lock_sha256"] = "f" * 64
    path.write_text(json.dumps(approvals))
    gid = cutover(host)
    with pytest.raises(host.m.AuthorityError, match="RUNTIME_LOCK_NOT_INDEPENDENTLY_APPROVED"):
        host.m.prepare_candidate(gid, host.b, require_root=False)
