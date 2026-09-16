"""Behavioral regressions for the externally provisioned authority protocol.

The implementation loaded here is only a reference. Temporary fixture anchors do
not certify independent provenance. No systemctl/network/financial calls occur.
"""
from __future__ import annotations

import concurrent.futures
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import threading
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "host_trust/weather-paper-authority-v3/authority.py"


def load():
    spec = importlib.util.spec_from_file_location("production_host_authority_reference", SOURCE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, stderr=subprocess.DEVNULL).strip()


@pytest.fixture
def host(tmp_path, monkeypatch):
    m = load()
    app = tmp_path / "app"
    app.mkdir()
    git(app, "init", "-q")
    git(app, "config", "user.name", "Fixture")
    git(app, "config", "user.email", "fixture@example.invalid")
    (app / ".gitignore").write_text(".venv/\n")
    package = app / "polymarket_scanner/production"
    package.mkdir(parents=True)
    (package.parent / "__init__.py").write_text("")
    (package / "__init__.py").write_text("IDENTITY = 'fixture'\n")
    (package / "__main__.py").write_text("import os,time\nprint(os.readlink('/proc/self'),flush=True)\ntime.sleep(30)\n")
    locks = {"signals": ("requirements-runtime-hashed.txt", "demo", "1.0"),
             "execution": ("requirements-execution-hashed.txt", "eth-account", "0.13.7")}
    for filename, name, version in locks.values():
        (app / filename).write_text(f"{name}=={version} --hash=sha256:{'a' * 64}\n")
    (app / "version.txt").write_text("A\n")
    git(app, "add", ".")
    git(app, "commit", "-qm", "A")
    a = git(app, "rev-parse", "HEAD")
    (app / "version.txt").write_text("B\n")
    git(app, "commit", "-qam", "B")
    b = git(app, "rev-parse", "HEAD")
    git(app, "checkout", "--detach", "-q", a)
    legacy = app / ".venv"
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(legacy)], check=True)
    state = tmp_path / "state"
    state.mkdir()
    db = state / "signals.sqlite"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE signal_history (id INTEGER PRIMARY KEY, body TEXT)")
    con.execute("INSERT INTO signal_history(body) VALUES ('predecessor')")
    con.commit()
    con.close()
    execution_state = tmp_path / "execution-state"
    execution_state.mkdir()
    account = execution_state / "execution.sqlite"
    con = sqlite3.connect(account)
    con.execute("CREATE TABLE actual_history (id INTEGER PRIMARY KEY, body TEXT)")
    con.execute("INSERT INTO actual_history(body) VALUES ('actual-account-journal-must-not-be-rewound')")
    con.commit()
    con.close()
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    release = tmp_path / "release.sha"
    release.write_text(a + "\n")
    # pytest may itself be invoked through ../venv/bin/python. The independent
    # host policy binds an approved canonical interpreter, not the runner's
    # lexical command path (which is deliberately invalid when it contains '..').
    approved_python = Path(sys.executable).resolve(strict=True)
    policy = {"app_dir": str(app), "release_file": str(release), "unit_file": str(tmp_path / "signals.service"),
              "unit_name": "signals.service", "db_path": str(db), "execution_db_path": str(account),
              "signal_status_path": str(state / "status.json"), "execution_status_path": str(execution_state / "status.json"),
              "legacy_predecessor_venv": str(legacy), "runtime_root": str(runtime), "runtime_python": str(approved_python),
              "runtime_python_sha256": m.sha256_file(approved_python), "deploy_user": "deployer",
              "deploy_uid": 1001, "deploy_gid": 1001, "repo_remote_url": "https://github.com/example/fixture.git",
              "approval_file": str(tmp_path / "approved.json"), "database_restore_policy": "unchanged_only", "shared_read_group": "alpha-read",
              "writer_lock_paths": [str(state / "weather-paper-runtime.lock"), str(account) + ".writer.lock"],
              "components": {}}
    for index, (name, (filename, _, _)) in enumerate(locks.items(), 1):
        unit = tmp_path / f"{name}.service"
        unit.write_text(f"[Service]\nDescription=Predecessor {name}\n")
        policy["components"][name] = {"module": m.FINAL_MODULE, "command": name, "config_file": str(tmp_path / f"{name}.json"),
                                      "lock_file": filename, "unit_name": f"{name}.service", "unit_file": str(unit),
                                      "user": name, "uid": 1001 + index, "gid": 2000}
        Path(policy["components"][name]["config_file"]).write_text(json.dumps({
            "mode": "LIVE_EXECUTION", "signal_db": str(db), "execution_db": str(account),
            "status_path": policy["signal_status_path"], "execution_status_path": policy["execution_status_path"],
        }))
    approved = []
    for sha in (a, b):
        approved.append({"sha": sha, "tree": git(app, "rev-parse", sha + "^{tree}"), "components": {
            name: {"lock_sha256": m.sha256_file(app / filename), "distributions": {distribution: version}}
            for name, (filename, distribution, version) in locks.items()}})
    Path(policy["approval_file"]).write_text(json.dumps({"version": "alpha-host-release-approvals-v1", "approved": approved}))
    monkeypatch.setattr(m, "ACTIVE", tmp_path / "active-cutover.json")
    monkeypatch.setattr(m, "MUTATION_LOCK", runtime / "authority.lock")
    generations = tmp_path / "generations"
    monkeypatch.setattr(m, "_generation_root", lambda policy: generations)
    monkeypatch.setattr(m, "verify_self", lambda *args, **kwargs: {"policy": policy, "policy_sha256": m._sha_bytes(m._canonical(policy))})
    real_run = m._run
    calls = []

    def isolated_run(args, **kwargs):
        calls.append(args)
        if args[0] == "/usr/bin/systemctl":
            raise AssertionError("Real systemctl is forbidden in these tests")
        if "venv" in args and "-m" in args:
            subprocess.run([sys.executable, "-m", "venv", "--copies", "--without-pip", args[-1]], check=True)
            # Keep real copied interpreters but share same-environment aliases;
            # otherwise hundreds of temp venvs can consume tens of GB on CI.
            binary = Path(args[-1]) / "bin/python"
            for alias in binary.parent.glob("python*"):
                if alias != binary and alias.is_file() and m.sha256_file(alias) == m.sha256_file(binary):
                    alias.unlink()
                    os.link(binary, alias)
            return subprocess.CompletedProcess(args, 0, "", "")
        if "pip" in args and "-m" in args:
            if "install" in args:
                venv = Path(args[0]).parent.parent
                site = next((venv / "lib").glob("python*/site-packages"))
                lock = Path(args[-1])
                for name, version in m._lock_pins(lock).items():
                    info = site / f"{name.replace('-', '_')}-{version}.dist-info"
                    info.mkdir()
                    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n")
                    (site / (name.replace('-', '_') + ".py")).write_text("VALUE = 1\n")
            return subprocess.CompletedProcess(args, 0, "", "")
        return real_run(args, **kwargs)

    monkeypatch.setattr(m, "_run", isolated_run)
    return SimpleNamespace(m=m, app=app, policy=policy, a=a, b=b, root=tmp_path, db=db,
                           account=account, calls=calls, real_run=real_run)


def cutover(host):
    return host.m.create_cutover(host.b, require_root=False)


def prepare(host):
    gid = cutover(host)
    manifest = host.m.prepare_candidate(gid, host.b, require_root=False)
    return gid, manifest


def test_cutover_prepare_verifies_after_final_modes_and_uses_component_locks(host):
    gid, manifest = prepare(host)
    result = host.m.verify_runtime_files(gid, host.b, require_root=False)
    assert result["manifest"] == manifest
    assert set(manifest["components"]) == {"signals", "execution"}
    assert manifest["components"]["signals"]["distributions"] == [{"name": "demo", "version": "1.0"}]
    assert manifest["components"]["execution"]["distributions"] == [{"name": "eth-account", "version": "0.13.7"}]
    assert (Path(manifest["source_path"]) / "version.txt").stat().st_mode & 0o777 == 0o444
    assert "--only-binary=:all:" in next(call for call in host.calls if "pip" in call and "install" in call)


def test_source_export_never_imports_candidate_git_configuration_or_hooks(host):
    marker = host.root / "hook-executed"
    hooks = host.root / "malicious-hooks"
    hooks.mkdir()
    for name in ("post-checkout", "reference-transaction"):
        path = hooks / name
        path.write_text(f"#!/bin/sh\nprintf bad > '{marker}'\n")
        path.chmod(0o755)
    git(host.app, "config", "core.hooksPath", str(hooks))
    git(host.app, "config", "core.fsmonitor", "false")
    gid, manifest = prepare(host)
    host.m.activate_checkout(gid, host.b, require_root=False)
    assert not marker.exists()
    objects = host.m._generation_root(host.policy) / gid / "objects.git"
    config = (objects / "config").read_text()
    assert "hooksPath" not in config and str(hooks) not in config
    assert not list((objects / "hooks").glob("*"))
    assert not any("safe.directory" in part for call in host.calls for part in call)
    wrapped = host.m._deploy_command(host.policy, ["/usr/bin/git", "status"], require_root=True)
    assert wrapped[:3] == ["/usr/bin/setpriv", "--reuid=1001", "--regid=1001"]
    assert "--clear-groups" in wrapped and "--bounding-set=-all" in wrapped


def test_ordinary_legacy_venv_restores_after_entire_candidate_git_is_destroyed(host):
    gid, _ = prepare(host)
    before_account = host.account.read_bytes()
    host.m.activate_checkout(gid, host.b, require_root=False)
    shutil.rmtree(host.app)
    host.app.mkdir()
    (host.app / ".git").mkdir()
    (host.app / "corrupted").write_text("no candidate helpers available")
    host.m.recover(gid, require_root=False)
    assert git(host.app, "rev-parse", "HEAD") == host.a
    assert (host.app / "version.txt").read_text() == "A\n"
    assert (host.app / ".venv/bin/python").exists()
    assert host.account.read_bytes() == before_account
    assert not host.m.ACTIVE.exists()
    assert not (Path(host.policy["runtime_root"]) / "active-release.json").exists()


def test_second_snapshot_while_candidate_current_cannot_replace_predecessor(host):
    gid, _ = prepare(host)
    manifest = host.m._generation_root(host.policy) / gid / "manifest.json"
    before = manifest.read_bytes()
    host.m.activate_checkout(gid, host.b, require_root=False)
    with pytest.raises(host.m.AuthorityError, match="CUTOVER_ALREADY_ACTIVE"):
        host.m.create_cutover(host.b, require_root=False)
    assert manifest.read_bytes() == before
    host.m.recover(gid, require_root=False)
    assert git(host.app, "rev-parse", "HEAD") == host.a


def test_concurrent_root_mutations_cannot_both_create_a_generation(host, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    original = host.m._import_app_objects

    def blocked(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return original(*args, **kwargs)

    monkeypatch.setattr(host.m, "_import_app_objects", blocked)
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(cutover, host)
        assert entered.wait(5)
        with pytest.raises(host.m.AuthorityError, match="CUTOVER_MUTATION_BUSY"):
            cutover(host)
        release.set()
        gid = first.result(timeout=10)
    assert json.loads(host.m.ACTIVE.read_text())["generation_id"] == gid
    assert len(list(host.m._generation_root(host.policy).iterdir())) == 1


def test_failed_stop_and_still_active_writer_prevent_all_recovery_mutation(host, monkeypatch):
    gid = cutover(host)
    before = host.db.read_bytes()
    marker = host.app / "version.txt"

    def failed_stop(args, **kwargs):
        return subprocess.CompletedProcess(args, 1, "", "")

    monkeypatch.setattr(host.m, "_run", failed_stop)
    # This case exercises the stop-before-mutation protocol body. Root admission
    # and custody have their own negative tests; CI does not own root fixture files.
    monkeypatch.setattr(host.m, "_root_custody", host.m._regular_nosymlink)
    monkeypatch.setattr(host.m, "_secure_root_dir", lambda path: None)
    with pytest.raises(host.m.AuthorityError, match="RECOVERY_SERVICE_STOP_FAILED"):
        host.m.recover.__wrapped__(gid, require_root=True)
    assert host.db.read_bytes() == before and marker.read_text() == "A\n"
    assert host.m.ACTIVE.exists()


def test_root_mutation_admission_rejects_unprivileged_caller_before_services(host, monkeypatch):
    gid = cutover(host)
    proxy = SimpleNamespace(**{key: value for key, value in vars(host.m.os).items() if key != "geteuid"})
    proxy.geteuid = lambda: 1000
    monkeypatch.setattr(host.m, "os", proxy)
    calls = []
    monkeypatch.setattr(host.m, "_run", lambda *args, **kwargs: calls.append(args))
    with pytest.raises(host.m.AuthorityError, match="CUTOVER_ROOT_REQUIRED"):
        host.m.recover(gid, require_root=True)
    assert not calls and host.m.ACTIVE.exists()


def test_root_directory_custody_rejects_unprivileged_owner_even_with_sealed_mode(host, monkeypatch):
    directory = host.root / "sealed-but-not-root-owned"
    directory.mkdir(mode=0o755)
    original = Path.lstat

    def unprivileged_owner(path):
        captured = original(path)
        if path == directory and captured.st_uid == 0:
            values = list(captured)
            values[4] = 1000
            return os.stat_result(values)
        return captured

    monkeypatch.setattr(Path, "lstat", unprivileged_owner)
    with pytest.raises(host.m.AuthorityError, match="AUTHORITY_DIRECTORY_CUSTODY_INVALID"):
        host.m._secure_root_dir(directory)


def test_writer_lock_blocks_recovery_even_if_systemd_is_inactive(host):
    import fcntl
    gid = cutover(host)
    path = Path(host.policy["writer_lock_paths"][0])
    with path.open("w") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(host.m.AuthorityError, match="CUTOVER_WRITER_STILL_ACTIVE"):
            host.m.recover(gid, require_root=False)
    assert host.m.ACTIVE.exists()


def test_post_cutover_signal_history_and_actual_journal_are_never_rewound(host):
    gid = cutover(host)
    con = sqlite3.connect(host.db)
    con.execute("INSERT INTO signal_history(body) VALUES ('delivered after cutover')")
    con.commit()
    con.close()
    before = host.account.read_bytes()
    with pytest.raises(host.m.AuthorityError, match="RECOVERY_DB_CHANGED_REQUIRES_OPERATOR_RECONCILIATION"):
        host.m.recover(gid, require_root=False)
    con = sqlite3.connect(host.db)
    assert con.execute("SELECT COUNT(*) FROM signal_history").fetchone()[0] == 2
    con.close()
    assert host.account.read_bytes() == before and host.m.ACTIVE.exists()


def test_database_owner_is_restored_before_atomic_publication(host, monkeypatch):
    calls = []
    real = host.m.os
    proxy = SimpleNamespace(**{key: value for key, value in vars(real).items() if key != "fchown"})
    proxy.fchown = lambda fd, uid, gid: calls.append((uid, gid))
    monkeypatch.setattr(host.m, "os", proxy)
    target = host.root / "restored.sqlite"
    host.m._restore_database(host.db, target, {"uid": 1002, "gid": 1003}, require_root=True)
    assert calls == [(1002, 1003)]
    assert target.stat().st_mode & 0o777 == 0o640
    assert target.read_bytes() == host.db.read_bytes()


def test_pinned_versions_duplicates_and_unapproved_execution_dependencies_fail(host):
    m = host.m
    lock = host.app / "requirements-execution-hashed.txt"
    m._validate_inventory([{"name": "eth-account", "version": "0.13.7"}], lock, {"eth-account": "0.13.7"})
    with pytest.raises(m.AuthorityError, match="VERSION_MISMATCH"):
        m._validate_inventory([{"name": "eth-account", "version": "99.0"}], lock)
    with pytest.raises(m.AuthorityError, match="DUPLICATE_DISTRIBUTION"):
        m._validate_inventory([{"name": "eth-account", "version": "0.13.7"}] * 2, lock)
    with pytest.raises(m.AuthorityError, match="NOT_APPROVED_FOR_COMPONENT"):
        m._validate_inventory([{"name": "eth-account", "version": "0.13.7"}], lock, {"httpx": "1.0"})


def test_sealed_environment_rejects_sitecustomize_before_executing_it(host):
    gid, manifest = prepare(host)
    venv = Path(manifest["components"]["signals"]["venv_path"])
    site = next((venv / "lib").glob("python*/site-packages"))
    marker = host.root / "injected-hook-executed"
    (site / "sitecustomize.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    with pytest.raises(host.m.AuthorityError, match="RUNTIME_VENV_TREE_MISMATCH"):
        host.m.verify_runtime_files(gid, host.b, require_root=False)
    assert not marker.exists()


def test_production_units_have_isolated_privileged_prestart_and_no_environment_file(host):
    gid, manifest = prepare(host)
    for name in ("signals", "execution"):
        text = host.m._render_unit(host.policy, gid, host.b, manifest, name)
        assert "EnvironmentFile=" not in text
        assert "ExecStartPre=+/usr/bin/env -i PATH=/usr/bin:/bin HOME=/nonexistent LANG=C.UTF-8 /usr/bin/python3 -I -s -E /usr/local/libexec/" in text
        assert f"-m polymarket_scanner.production {name} --config " in text
        assert "--paper-stake" not in text and "final_v10" not in text
        assert str(SOURCE) not in text
    assert "User=signals" in Path(host.policy["unit_file"]).read_text()
    assert "User=execution" in Path(host.policy["components"]["execution"]["unit_file"]).read_text()


def test_effective_import_environment_isolated_from_loader_inputs(host, monkeypatch):
    _, manifest = prepare(host)
    for key in ("PYTHONPATH", "PYTHONHOME", "PYTHONUSERBASE", "LD_PRELOAD", "LD_LIBRARY_PATH"):
        monkeypatch.setenv(key, "/tmp/untrusted-fixture")
    result = host.m._verify_import_environment(host.policy, Path(manifest["source_path"]),
                                              Path(manifest["components"]["signals"]["venv_path"]), require_root=False)
    assert result["isolated"] == 1 and result["user_site"] is False
    assert "/tmp/untrusted-fixture" not in result["path"]


def test_unapproved_release_and_wrong_generation_fail_before_mutation(host):
    path = Path(host.policy["approval_file"])
    data = json.loads(path.read_text())
    data["approved"] = [row for row in data["approved"] if row["sha"] == host.a]
    path.write_text(json.dumps(data))
    with pytest.raises(host.m.AuthorityError, match="HOST_RELEASE_NOT_INDEPENDENTLY_APPROVED"):
        cutover(host)
    assert not host.m.ACTIVE.exists()


def test_privileged_prepare_seals_final_modes_before_hashing(host, monkeypatch):
    gid = cutover(host)
    # The fixture cannot own unmapped numeric UIDs. Recreate these leases through
    # the recorded privileged fchown path below instead of simulating wrong owners.
    for path in host.policy["writer_lock_paths"]:
        Path(path).unlink()
    real_os = host.m.os
    ownership = []
    proxy = SimpleNamespace(**{k: v for k, v in vars(real_os).items() if k not in {"chown", "fchown"}})
    proxy.chown = lambda *args, **kwargs: ownership.append(("chown", args[1:]))
    proxy.fchown = lambda *args: ownership.append(("fchown", args[1:]))
    monkeypatch.setattr(host.m, "os", proxy)
    monkeypatch.setattr(host.m, "_root_custody", host.m._regular_nosymlink)
    monkeypatch.setattr(host.m, "_secure_root_dir", lambda path: None)
    real_run = host.m._run

    def services_are_stopped(args, **kwargs):
        if args[0] == "/usr/bin/systemctl":
            return subprocess.CompletedProcess(args, 0, "MainPID=0\nActiveState=inactive\n", "")
        return real_run(args, **kwargs)

    monkeypatch.setattr(host.m, "_run", services_are_stopped)
    # Exercise the root-mode body including chown/sealing and service verification;
    # identity syscalls are recorded because CI need not own numeric fixture UIDs.
    result = host.m.prepare_candidate.__wrapped__(gid, host.b, require_root=True)
    verified = host.m.verify_runtime_files(gid, host.b, require_root=True)
    assert result == verified["manifest"]
    assert any(row[0] == "fchown" for row in ownership)
    assert result["source_tree_sha256"] == host.m._tree_digest(Path(result["source_path"]))


def test_active_release_and_both_environments_restored_on_next_generation_failure(host):
    gid_b, manifest_b = prepare(host)
    host.m.activate_checkout(gid_b, host.b, require_root=False)
    host.m.finalize(gid_b, host.b, require_root=False)
    pointer = Path(host.policy["runtime_root"]) / "active-release.json"
    previous_pointer = pointer.read_bytes()
    (host.app / "version.txt").write_text("C\n")
    git(host.app, "commit", "-qam", "C")
    c = git(host.app, "rev-parse", "HEAD")
    git(host.app, "checkout", "--detach", "-q", host.b)
    approvals = Path(host.policy["approval_file"])
    data = json.loads(approvals.read_text())
    row = dict(data["approved"][1], sha=c, tree=git(host.app, "rev-parse", c + "^{tree}"))
    data["approved"].append(row)
    approvals.write_text(json.dumps(data))
    gid_c = host.m.create_cutover(c, require_root=False)
    manifest_c = host.m.prepare_candidate(gid_c, c, require_root=False)
    host.m.activate_checkout(gid_c, c, require_root=False)
    # Model a crash after writing the new active-release pointer, before deleting
    # the generation marker. Recovery must restore its sealed predecessor pointer.
    host.m._safe_write(pointer, host.m._canonical({"version": "weather-paper-active-release-v3", "candidate_sha": c,
                                                  "venv_path": manifest_c["venv_path"]}), root_custody=False)
    host.m.recover(gid_c, require_root=False)
    assert pointer.read_bytes() == previous_pointer
    assert (host.app / "version.txt").read_text() == "B\n"
    for component in manifest_b["components"].values():
        assert host.m._tree_digest(Path(component["venv_path"])) == component["venv_tree_sha256"]


def test_wrong_generation_and_candidate_do_not_authorize_existing_runtime(host):
    gid, _ = prepare(host)
    with pytest.raises(host.m.AuthorityError, match="CUTOVER_CANDIDATE_MISMATCH"):
        host.m.verify_runtime_files(gid, host.a, require_root=False)
    active = json.loads(host.m.ACTIVE.read_text())
    active["generation_id"] = "f" * 64
    host.m._safe_write(host.m.ACTIVE, host.m._canonical(active), root_custody=False)
    with pytest.raises(host.m.AuthorityError, match="CUTOVER_ACTIVE_GENERATION_MISMATCH"):
        host.m.finalize(gid, host.b, require_root=False)
    assert host.m.ACTIVE.exists()


def test_tar_cannot_write_members_through_an_interpreter_symlink(host):
    import io
    archive = host.root / "escape.tar"
    outside = host.root / "outside"
    outside.mkdir()
    with tarfile.open(archive, "w") as out:
        root = tarfile.TarInfo("venv")
        root.type = tarfile.DIRTYPE
        out.addfile(root)
        link = tarfile.TarInfo("venv/bin")
        link.type = tarfile.SYMTYPE
        link.linkname = str(outside)
        out.addfile(link)
        payload = tarfile.TarInfo("venv/bin/escaped")
        payload.size = 1
        out.addfile(payload, io.BytesIO(b"x"))
    with pytest.raises(host.m.AuthorityError, match="SYMLINK"):
        host.m._safe_extract_venv(archive, host.root, "venv")
    assert not (outside / "escaped").exists()


def test_database_schema_version_changes_are_not_hidden_by_equal_dump(host):
    before = host.m._logical_db_digest(host.db)
    con = sqlite3.connect(host.db)
    con.execute("PRAGMA user_version=91")
    con.close()
    assert host.m._logical_db_digest(host.db) != before


def test_installed_inventory_reads_metadata_without_loading_site_hooks(host):
    legacy = host.app / ".venv"
    site = next((legacy / "lib").glob("python*/site-packages"))
    marker = host.root / "metadata-hook-executed"
    (site / "sitecustomize.py").write_text(f"from pathlib import Path\nPath({str(marker)!r}).touch()\n")
    assert host.m._inventory(legacy / "bin/python") == []
    assert not marker.exists()


def test_changed_execution_journal_requires_forward_recovery_and_remains_intact(host):
    gid, _ = prepare(host)
    host.m.activate_checkout(gid, host.b, require_root=False)
    con = sqlite3.connect(host.account)
    con.execute("INSERT INTO actual_history(body) VALUES ('committed real fill after cutover')")
    con.commit()
    con.close()
    before = host.account.read_bytes()
    with pytest.raises(host.m.AuthorityError, match="EXECUTION_JOURNAL_CHANGED_REQUIRES_FORWARD_RECOVERY"):
        host.m.recover(gid, require_root=False)
    assert host.account.read_bytes() == before
    assert git(host.app, "rev-parse", "HEAD") == host.b
    assert host.m.ACTIVE.exists()


def test_configuration_cannot_redirect_writer_outside_bound_state_paths(host):
    gid, _ = prepare(host)
    config = Path(host.policy["components"]["signals"]["config_file"])
    raw = json.loads(config.read_text())
    raw["signal_db"] = str(host.root / "unlocked.sqlite")
    config.write_text(json.dumps(raw))
    with pytest.raises(host.m.AuthorityError, match="SIGNAL_CONFIG_PATH_MISMATCH"):
        host.m.verify_runtime_files(gid, host.b, require_root=False)


def test_operator_risk_configuration_changes_require_matching_component_identity(host):
    gid, _ = prepare(host)
    configs = [Path(component["config_file"]) for component in host.policy["components"].values()]
    before = host.m.verify_runtime_files(gid, host.b, require_root=False)["current_configuration_sha256"]
    for config in configs:
        raw = json.loads(config.read_text())
        raw["operator_risk_fixture"] = "explicit new limits"
        config.write_text(json.dumps(raw))
        if config == configs[0]:
            with pytest.raises(host.m.AuthorityError, match="COMPONENT_CONFIG_IDENTITY_MISMATCH"):
                host.m.verify_runtime_files(gid, host.b, require_root=False)
    after = host.m.verify_runtime_files(gid, host.b, require_root=False)["current_configuration_sha256"]
    assert len(set(after.values())) == 1 and before != after


def test_signal_only_component_does_not_require_execution_or_signing_provisioning(host):
    host.policy["components"].pop("execution")
    host.policy["writer_lock_paths"] = host.policy["writer_lock_paths"][:1]
    config = Path(host.policy["components"]["signals"]["config_file"])
    config.write_text(json.dumps({"mode": "LIVE_SIGNALS", "signal_db": str(host.db), "status_path": host.policy["signal_status_path"]}))
    assert host.m._validate_policy(host.policy)["components"].keys() == {"signals"}
    assert host.m._component_configurations(host.policy, require_root=False).keys() == {"signals"}


def test_atomic_directory_publication_failure_preserves_previous_directory(host, monkeypatch):
    previous, staged = host.root / "previous", host.root / "staged"
    previous.mkdir()
    staged.mkdir()
    (previous / "identity").write_text("predecessor")
    (staged / "identity").write_text("candidate")
    original = host.m.os.replace

    def failed_publish(source, destination):
        if Path(source) == staged:
            raise OSError("fixture publication failure")
        return original(source, destination)

    monkeypatch.setattr(host.m.os, "replace", failed_publish)
    with pytest.raises(OSError, match="fixture publication failure"):
        host.m._publish_directory(staged, previous)
    assert (previous / "identity").read_text() == "predecessor"
    assert (staged / "identity").read_text() == "candidate"


def test_failed_recovery_staging_can_be_retried_without_candidate_helpers(host, monkeypatch):
    gid, _ = prepare(host)
    host.m.activate_checkout(gid, host.b, require_root=False)
    original = host.m._safe_extract_venv
    monkeypatch.setattr(host.m, "_safe_extract_venv", lambda *args, **kwargs: host.m.fail("FIXTURE_EXTRACTION_FAILED"))
    with pytest.raises(host.m.AuthorityError, match="FIXTURE_EXTRACTION_FAILED"):
        host.m.recover(gid, require_root=False)
    assert git(host.app, "rev-parse", "HEAD") == host.b
    assert (host.app / ".venv/bin/python").exists()
    monkeypatch.setattr(host.m, "_safe_extract_venv", original)
    host.m.recover(gid, require_root=False)
    assert git(host.app, "rev-parse", "HEAD") == host.a
    assert not host.m.ACTIVE.exists()


@pytest.mark.parametrize("injected", [False, True])
def test_actual_process_environment_identity_is_verified(host, monkeypatch, injected):
    # Portable fixture process runs as the test identity. Production policy tests
    # separately require unprivileged, distinct, independently provisioned UIDs.
    for component in host.policy["components"].values():
        component.update(uid=os.getuid(), gid=os.getgid())
    gid, manifest = prepare(host)
    python = str(Path(manifest["venv_path"]) / "bin/python")
    env = {"PATH": "/usr/bin:/bin", "HOME": "/nonexistent", "LANG": "C.UTF-8", "PYTHONUNBUFFERED": "1",
           "PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "ALPHA_DISABLE_DOTENV": "1",
           "ALPHA_RELEASE_SHA": host.b, "ALPHA_CUTOVER_GENERATION": gid, "ALPHA_RUNTIME_SOURCE": manifest["source_path"]}
    if injected:
        env["PYTHONUSERBASE"] = str(host.root / "untrusted")
    process = subprocess.Popen([python, "-I", "-s", "-E", "-B", "-m", host.m.FINAL_MODULE, "signals", "--config",
                                host.policy["components"]["signals"]["config_file"]], cwd=manifest["source_path"], env=env,
                               stdout=subprocess.PIPE, text=True)
    # Some containers expose host procfs while Popen returns a PID-namespace ID.
    # Read only the disposable child's actual procfs identity, as host systemd does.
    proc_pid = int(process.stdout.readline().strip())
    original_run = host.m._run

    def mock_service(args, **kwargs):
        if args[0] == "/usr/bin/systemctl":
            return subprocess.CompletedProcess(args, 0, str(proc_pid) + "\n", "")
        return original_run(args, **kwargs)

    monkeypatch.setattr(host.m, "_run", mock_service)
    monkeypatch.setattr(host.m, "_root_custody", host.m._regular_nosymlink)
    monkeypatch.setattr(host.m, "_secure_root_dir", lambda path: None)
    monkeypatch.setattr(host.m, "_deploy_command", lambda policy, args, **kwargs: args)
    try:
        if injected:
            with pytest.raises(host.m.AuthorityError, match="PROCESS_ENV_IDENTITY_MISMATCH"):
                host.m.verify_process(gid, host.b)
        else:
            status_path = Path(f"/proc/{proc_pid}/status")
            real_status = status_path.read_text()
            groups_line = next(line for line in real_status.splitlines() if line.startswith("Groups:"))
            actual_groups = {int(value) for value in groups_line.split()[1:]}
            if actual_groups - {os.getgid()}:
                # Unprivileged CI processes inherit runner groups and cannot
                # clear them. They must be rejected by the real production gate.
                with pytest.raises(host.m.AuthorityError, match="PROCESS_GROUPS_MISMATCH"):
                    host.m.verify_process(gid, host.b)
                imports = host.m._verify_import_environment(host.policy, Path(manifest["source_path"]),
                                                           Path(manifest["venv_path"]), require_root=False)
                assert imports["isolated"] == 1
            else:
                result = host.m.verify_process(gid, host.b)
                assert result["pid"] == proc_pid and result["component"] == "signals"
                assert result["imports"]["isolated"] == 1
            # Exercise rejection even on runners that already have clean groups.
            # Only this second observation is synthetic; the real /proc identity
            # and environment above are never normalized or accepted artificially.
            forbidden_group = os.getgid() + 1
            rejected_status = real_status.replace(groups_line, f"Groups:\t{os.getgid()} {forbidden_group}")
            original_read_text = Path.read_text

            def extra_group(path, *args, **kwargs):
                return rejected_status if path == status_path else original_read_text(path, *args, **kwargs)

            monkeypatch.setattr(Path, "read_text", extra_group)
            with pytest.raises(host.m.AuthorityError, match="PROCESS_GROUPS_MISMATCH"):
                host.m.verify_process(gid, host.b)
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_systemd_validates_both_rendered_component_units(host):
    verifier = shutil.which("systemd-analyze")
    if verifier is None:
        pytest.skip("systemd-analyze unavailable on this runner; target-host validation remains required")
    prepare(host)
    result = subprocess.run([verifier, "verify", "--man=no", *[row["unit_file"] for row in host.policy["components"].values()]],
                            env={"PATH": "/usr/bin:/bin", "SYSTEMD_LOG_LEVEL": "err"},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_inventory_never_reopens_candidate_file_by_untrusted_path(host, monkeypatch):
    import contextlib
    import hashlib
    tree = host.root / "untrusted-tree"
    tree.mkdir()
    ordinary = tree / "data.txt"
    ordinary.write_bytes(b"public-fixture")
    private = host.root / "private-fixture"
    private.write_bytes(b"secret-fixture")
    private.chmod(0o600)
    original_open = Path.open

    @contextlib.contextmanager
    def swapped_path_open(path, *args, **kwargs):
        # Reproduces replacement after lstat, before a pathname-based privileged
        # open. Descriptor-relative ingestion must never invoke this unsafe API.
        if path == ordinary:
            ordinary.unlink()
            ordinary.symlink_to(private)
            try:
                with original_open(path, *args, **kwargs) as stream:
                    yield stream
            finally:
                ordinary.unlink()
                with original_open(ordinary, "wb") as stream:
                    stream.write(b"public-fixture")
        else:
            with original_open(path, *args, **kwargs) as stream:
                yield stream

    monkeypatch.setattr(Path, "open", swapped_path_open)
    row = next(row for row in host.m._tree_entries(tree) if row["kind"] == "file")
    assert row["sha256"] == hashlib.sha256(b"public-fixture").hexdigest()


def test_directory_swap_after_inspection_cannot_escape_descriptor_boundary(host, monkeypatch):
    tree, outside = host.root / "tree", host.root / "outside"
    (tree / "nested").mkdir(parents=True)
    outside.mkdir()
    (outside / "private-fixture").write_text("not part of the snapshot")
    original_open = host.m.os.open
    swaps = []

    def replaced_directory(path, flags, *args, **kwargs):
        if path == "nested" and kwargs.get("dir_fd") is not None:
            (tree / "nested").rename(tree / "original-nested")
            (tree / "nested").symlink_to(outside, target_is_directory=True)
            swaps.append(path)
        return original_open(path, flags, *args, **kwargs)

    monkeypatch.setattr(host.m.os, "open", replaced_directory)
    with pytest.raises(OSError):
        host.m._tree_entries(tree)
    assert swaps == ["nested"]


def test_privileged_snapshot_cannot_include_files_unreadable_by_deploy_principal(host):
    venv = host.app / ".venv"
    private = venv / "root-private-fixture"
    private.write_text("not a readable dependency")
    private.chmod(0o600)
    with pytest.raises(host.m.AuthorityError, match="INPUT_NOT_READABLE_BY_DEPLOY_PRINCIPAL"):
        host.m._snapshot_venv(venv, host.root / "unsafe.tar", host.root / "unsafe.json",
                              reader_uid=98765, reader_gid=98765)
    assert not (host.root / "unsafe.tar").exists()


def test_hardlinked_venv_files_restore_from_validated_regular_members(host):
    venv = host.app / ".venv"
    (venv / "hardlink-a").write_text("reviewed fixture bytes")
    os.link(venv / "hardlink-a", venv / "hardlink-b")
    archive, manifest = host.root / "hardlinks.tar", host.root / "hardlinks.json"
    host.m._snapshot_venv(venv, archive, manifest, root_custody=False)
    destination = host.root / "restored"
    destination.mkdir()
    host.m._safe_extract_venv(archive, destination, venv.name, manifest)
    assert host.m._tree_digest(destination / venv.name) == host.m._tree_digest(venv)
    with tarfile.open(archive) as source:
        assert not any(member.islnk() for member in source.getmembers())


def test_snapshot_root_cannot_traverse_a_symlinked_ancestor(host):
    private = host.root / "private-ancestor"
    (private / "venv").mkdir(parents=True)
    (private / "venv/data").write_text("protected by ancestor custody")
    private.chmod(0o700)
    redirected = host.root / "redirected"
    redirected.symlink_to(private, target_is_directory=True)
    with pytest.raises(OSError):
        host.m._tree_entries(redirected / "venv")


def test_changed_journal_recovery_refusal_disables_units_before_returning(host, monkeypatch):
    import contextlib
    gid = cutover(host)
    con = sqlite3.connect(host.account)
    con.execute("INSERT INTO actual_history(body) VALUES ('post-cutover committed fill')")
    con.commit()
    con.close()
    before = host.account.read_bytes()
    calls = []

    @contextlib.contextmanager
    def confirmed_stopped(policy, *, stop, require_root):
        assert stop is True and require_root is True
        yield

    def record_unit_operation(args, **kwargs):
        calls.append(args)
        assert args[:2] == ["/usr/bin/systemctl", "disable"]
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(host.m, "_quiescent_writers", confirmed_stopped)
    monkeypatch.setattr(host.m, "_run", record_unit_operation)
    monkeypatch.setattr(host.m, "_root_custody", host.m._regular_nosymlink)
    monkeypatch.setattr(host.m, "_secure_root_dir", lambda path: None)
    with pytest.raises(host.m.AuthorityError, match="EXECUTION_JOURNAL_CHANGED_REQUIRES_FORWARD_RECOVERY"):
        host.m.recover.__wrapped__(gid, require_root=True)
    assert [args[-1] for args in calls] == ["signals.service", "execution.service"]
    assert host.account.read_bytes() == before and host.m.ACTIVE.exists()
