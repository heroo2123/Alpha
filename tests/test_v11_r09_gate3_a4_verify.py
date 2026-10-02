"""Adversarial offline controls for the unqualified A4 candidate."""

import ctypes
import fcntl
import hashlib
import importlib
import json
import os
import shutil
import signal
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tools.v11_r09_gate3_a4_verify import (VerificationError, _bootstrap_files,
    _seccomp_library, initialize_run_record, open_verified)


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _git(root, *args):
    return subprocess.check_output(["git", "-C", str(root), *args]).decode().strip()


def _environment_digest():
    content = json.dumps(dict(os.environ), sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True).encode()
    return _sha(content)


def _write_lock(root, entry="pkg.main", overrides=None):
    ctypes.CDLL("libseccomp.so.2")
    _seccomp_library()  # Include the confinement library in the pinned bootstrap map.
    commit = _git(root, "rev-parse", "HEAD")
    tree = _git(root, "rev-parse", "HEAD^{tree}")
    entries = []
    for path, kind, module in (
        ("pkg/__init__.py", "python", "pkg"),
        ("pkg/main.py", "python", "pkg.main"),
        ("pkg/helper.py", "python", "pkg.helper"),
        ("decoder.py", "python", "decoder"),
        ("native/libdecoder.so", "native", None),
        ("native/libdependency.so", "native", None),
        ("definitions/memfs.bin", "data", None),
    ):
        data = (root / path).read_bytes()
        entries.append({"path": path, "kind": kind, "module": module,
                        "sha256": _sha(data), "size": len(data),
                        "git_blob": _git(root, "rev-parse", f"HEAD:{path}")})
    exe = Path("/proc/self/exe").read_bytes()
    git_path = Path(shutil.which("git")).resolve()
    git_bytes = git_path.read_bytes()
    mapped = {}
    for line in Path("/proc/self/maps").read_text().splitlines():
        parts = line.split(maxsplit=5)
        if len(parts) >= 6 and "x" in parts[1] and parts[5].startswith("/"):
            mapped[parts[5]] = True
    mappings = []
    for path in sorted(mapped):
        data = Path(path).read_bytes()
        mappings.append({"path": path, "sha256": _sha(data), "size": len(data)})
    lock_file = root.parent / "lock.json"
    lock = {"schema": "ALPHA_V11_GATE3_A4_OFFLINE_LOCK_V1",
            "root": str(root), "commit": commit, "tree": tree,
            "entrypoint": entry, "environment_sha256": _environment_digest(),
            "run_id": "synthetic-run", "run_record": str(lock_file) + ".a4-first-lock",
            "interpreter": {"sha256": _sha(exe), "size": len(exe)},
            "git_tool": {"path": str(git_path), "sha256": _sha(git_bytes),
                         "size": len(git_bytes)},
            "bootstrap_mappings": mappings,
            "bootstrap_files": _bootstrap_files(),
            "artifacts": entries}
    if overrides:
        lock.update(overrides)
    content = json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()
    lock_file.write_bytes(content)
    if not Path(lock["run_record"]).exists():
        initialize_run_record(lock["run_record"], run_id=lock["run_id"],
                              lock_sha256=_sha(content))
    return lock_file, _sha(content)


def _new_run(path, lock):
    run_id = os.urandom(12).hex()
    path = path.with_name(f"lock-{run_id}.json")
    lock["run_id"] = run_id
    lock["run_record"] = str(path) + ".a4-first-lock"
    raw = json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    initialize_run_record(lock["run_record"], run_id=run_id, lock_sha256=_sha(raw))
    return path, _sha(raw)


@pytest.fixture
def source(tmp_path):
    root = tmp_path / "source"
    (root / "pkg").mkdir(parents=True)
    (root / "native").mkdir()
    (root / "definitions").mkdir()
    (root / "pkg/__init__.py").write_text("# reviewed package\n")
    (root / "pkg/helper.py").write_text("VALUE = 7\n")
    (root / "pkg/main.py").write_text(
        "from pkg import helper\n"
        "def main(runtime):\n"
        "    return [helper.VALUE, runtime.read_data('definitions/memfs.bin').decode()]\n")
    (root / "decoder.py").write_text("DECODER = 'reviewed'\n")
    (root / "native/libdecoder.so").write_bytes(b"synthetic-native-payload")
    (root / "native/libdependency.so").write_bytes(b"synthetic-native-dependency")
    (root / "definitions/memfs.bin").write_bytes(b"reviewed-memfs")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=A4 Test",
                    "-c", "user.email=a4@example.invalid", "commit", "-qm", "fixture"],
                   check=True)
    return root


def test_sealed_snapshot_and_reviewed_entrypoint(source):
    path, pin = _write_lock(source)
    with open_verified(path, expected_lock_sha256=pin) as session:
        fd = session.sealed_data("definitions/memfs.bin")
        try:
            assert os.read(fd, 15) == b"reviewed-memfs"
            assert fcntl.fcntl(fd, fcntl.F_GET_SEALS) & fcntl.F_SEAL_WRITE
            with pytest.raises(OSError):
                os.write(fd, b"changed")
        finally:
            os.close(fd)
        assert session.run() == [7, "reviewed-memfs"]


@pytest.mark.parametrize("target", ["pkg/main.py", "pkg/helper.py", "decoder.py",
                                     "native/libdecoder.so", "native/libdependency.so",
                                     "definitions/memfs.bin"])
def test_changed_locked_input_refuses_before_entrypoint(source, target):
    path, pin = _write_lock(source)
    with open_verified(path, expected_lock_sha256=pin) as session:
        (source / target).write_bytes(b"changed payload")
        with pytest.raises(VerificationError, match="ARTIFACT_CHANGED|INPUT_SIZE_CHANGED"):
            session.run()


def test_symlink_and_inode_replacement_after_verification_refuse(source):
    path, pin = _write_lock(source)
    target = source / "definitions/memfs.bin"
    with open_verified(path, expected_lock_sha256=pin) as session:
        original = target.read_bytes()
        target.unlink()
        target.write_bytes(original)
        with pytest.raises(VerificationError, match="PATH_SUBSTITUTED"):
            session.run()
    path, pin = _write_lock(source)
    with open_verified(path, expected_lock_sha256=pin) as session:
        target.rename(source / "definitions/original.bin")
        target.symlink_to("original.bin")
        with pytest.raises(VerificationError, match="PATH_UNSAFE_OR_MISSING"):
            session.run()


def test_unexpected_lazy_import_refuses(source):
    (source / "pkg/main.py").write_text(
        "def main(runtime):\n"
        "    import json\n"
        "    return json.dumps({'bad': 1})\n")
    subprocess.run(["git", "-C", str(source), "add", "pkg/main.py"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=A4 Test",
                    "-c", "user.email=a4@example.invalid", "commit", "-qm", "lazy"],
                   check=True)
    path, pin = _write_lock(source)
    with open_verified(path, expected_lock_sha256=pin) as session:
        with pytest.raises(VerificationError, match="UNEXPECTED_LAZY_IMPORT"):
            session.run()


def test_unexpected_file_open_denied_before_read(source):
    (source / "pkg/main.py").write_text(
        "def main(runtime):\n"
        "    return runtime._artifacts['definitions/memfs.bin'].path and "
        "runtime._open_probe('/etc/passwd')\n")
    subprocess.run(["git", "-C", str(source), "add", "pkg/main.py"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=A4 Test",
                    "-c", "user.email=a4@example.invalid", "commit", "-qm", "open"],
                   check=True)
    path, pin = _write_lock(source)
    with open_verified(path, expected_lock_sha256=pin) as session:
        session._open_probe = lambda name: os.open(name, os.O_RDONLY)
        with pytest.raises(VerificationError, match="Operation not permitted"):
            session.run()


def test_unexpected_lazy_native_load_denied(source):
    (source / "pkg/main.py").write_text("def main(runtime):\n    return runtime._lazy_native()\n")
    subprocess.run(["git", "-C", str(source), "add", "pkg/main.py"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=A4 Test",
                    "-c", "user.email=a4@example.invalid", "commit", "-qm", "native"],
                   check=True)
    path, pin = _write_lock(source)
    with open_verified(path, expected_lock_sha256=pin) as session:
        session._lazy_native = lambda: ctypes.CDLL(str(source / "native/libdecoder.so"))
        with pytest.raises(VerificationError, match="Operation not permitted"):
            session.run()


def test_relative_locked_import_and_unlisted_data_refusal(source):
    (source / "pkg/main.py").write_text(
        "from . import helper\n"
        "def main(runtime):\n"
        "    return helper.VALUE, runtime.read_data('definitions/other.bin')\n")
    subprocess.run(["git", "-C", str(source), "add", "pkg/main.py"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=A4 Test",
                    "-c", "user.email=a4@example.invalid", "commit", "-qm", "relative"],
                   check=True)
    path, pin = _write_lock(source)
    with open_verified(path, expected_lock_sha256=pin) as session:
        with pytest.raises(VerificationError, match="DATA_NOT_LOCKED"):
            session.run()


def test_git_source_mismatch_even_with_matching_local_hash(source):
    path, _ = _write_lock(source)
    (source / "pkg/helper.py").write_text("VALUE = 8\n")
    lock = json.loads(path.read_text())
    helper = next(item for item in lock["artifacts"] if item["path"] == "pkg/helper.py")
    data = (source / "pkg/helper.py").read_bytes()
    helper["size"], helper["sha256"] = len(data), _sha(data)
    path, pin = _new_run(path, lock)
    with pytest.raises(VerificationError, match="GIT_SOURCE_MISMATCH"):
        open_verified(path, expected_lock_sha256=pin)


def test_lock_path_replacement_after_preflight_refuses(source):
    path, pin = _write_lock(source)
    with open_verified(path, expected_lock_sha256=pin) as session:
        old = path.with_suffix(".old")
        path.rename(old)
        path.write_bytes(old.read_bytes())
        with pytest.raises(VerificationError, match="LOCK_PATH_SUBSTITUTED"):
            session.run()


@pytest.mark.parametrize("override", ["LD_PRELOAD", "LD_LIBRARY_PATH", "PYTHONPATH", "PATH"])
def test_lock_env_git_and_restart_refusals(source, monkeypatch, override):
    path, pin = _write_lock(source)
    with pytest.raises(VerificationError, match="LOCK_CHANGED"):
        open_verified(path, expected_lock_sha256="0" * 64)
    with pytest.raises(VerificationError, match="RESTART_BUILD_CHANGED"):
        open_verified(path, expected_lock_sha256=pin, resume_lock_sha256="1" * 64)
    (source / "pkg/helper.py").write_text("VALUE = 9\n")
    subprocess.run(["git", "-C", str(source), "add", "pkg/helper.py"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=A4 Test",
                    "-c", "user.email=a4@example.invalid", "commit", "-qm", "new-build"],
                   check=True)
    new_path, new_pin = _write_lock(source)
    with pytest.raises(VerificationError, match="RESTART_BUILD_CHANGED"):
        open_verified(new_path, expected_lock_sha256=new_pin, resume_lock_sha256=pin)
    path, pin = _write_lock(source)
    path, pin = _new_run(path, json.loads(path.read_text()))
    monkeypatch.setenv(override, "/tmp/override.so")
    with pytest.raises(VerificationError, match="ENV_OVERRIDE"):
        open_verified(path, expected_lock_sha256=pin)
    monkeypatch.undo()
    changed = json.loads(path.read_text())
    changed["tree"] = "0" * 40
    path, invalid_pin = _new_run(path, changed)
    with pytest.raises(VerificationError, match="TREE_CHANGED"):
        open_verified(path, expected_lock_sha256=invalid_pin)


def test_restart_under_new_build_refuses_without_resume_digest(source):
    path, pin = _write_lock(source)
    open_verified(path, expected_lock_sha256=pin).close()
    (source / "pkg/helper.py").write_text("VALUE = 99\n")
    subprocess.run(["git", "-C", str(source), "add", "pkg/helper.py"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=R",
                    "-c", "user.email=r@example.invalid", "commit", "-qm", "new"], check=True)
    new_path, new_pin = _write_lock(source)
    with pytest.raises(VerificationError, match="RESTART_BUILD_CHANGED"):
        open_verified(new_path, expected_lock_sha256=new_pin)


def test_missing_first_lock_record_refuses(source):
    path, pin = _write_lock(source)
    record = Path(json.loads(path.read_text())["run_record"])
    record.unlink()
    with pytest.raises(VerificationError, match="RUN_RECORD_MISSING"):
        open_verified(path, expected_lock_sha256=pin)


def test_run_record_is_write_once_and_cannot_be_redirected(source):
    path, pin = _write_lock(source)
    lock = json.loads(path.read_text())
    with pytest.raises(VerificationError, match="RUN_RECORD_EXISTS"):
        initialize_run_record(lock["run_record"], run_id=lock["run_id"],
                              lock_sha256=pin)
    lock["run_record"] = str(path.parent / "alternate-first-lock")
    raw = json.dumps(lock, sort_keys=True, separators=(",", ":")).encode()
    path.write_bytes(raw)
    initialize_run_record(lock["run_record"], run_id=lock["run_id"],
                          lock_sha256=_sha(raw))
    with pytest.raises(VerificationError, match="RUN_RECORD_PATH_INVALID"):
        open_verified(path, expected_lock_sha256=_sha(raw))


def test_duplicate_key_and_noncanonical_lock_refuse(source):
    path, _ = _write_lock(source)
    lock = json.loads(path.read_text())
    run_id = os.urandom(12).hex()
    path = path.with_name(f"lock-{run_id}.json")
    record = Path(str(path) + ".a4-first-lock")
    lock.update(run_id=run_id, run_record=str(record))
    canonical = json.dumps(lock, sort_keys=True, separators=(",", ":"))
    duplicated = canonical.replace('"entrypoint":"pkg.main"',
                                   '"entrypoint":"pkg.main","entrypoint":"decoder"', 1)
    assert duplicated != canonical
    path.write_text(duplicated)
    pin = _sha(path.read_bytes())
    initialize_run_record(record, run_id=run_id, lock_sha256=pin)
    with pytest.raises(VerificationError, match="LOCK_DUPLICATE_KEY"):
        open_verified(path, expected_lock_sha256=pin)

    lock["run_id"] = os.urandom(12).hex()
    path = path.with_name(f"lock-{lock['run_id']}.json")
    lock["run_record"] = str(path) + ".a4-first-lock"
    raw = json.dumps(lock, sort_keys=True).encode()
    path.write_bytes(raw)
    initialize_run_record(lock["run_record"], run_id=lock["run_id"], lock_sha256=_sha(raw))
    with pytest.raises(VerificationError, match="LOCK_NONCANONICAL"):
        open_verified(path, expected_lock_sha256=_sha(raw))


def test_nonfinite_lock_constant_refuses(source):
    path, _ = _write_lock(source)
    lock = json.loads(path.read_text())
    lock["run_id"] = os.urandom(12).hex()
    path = path.with_name(f"lock-{lock['run_id']}.json")
    lock["run_record"] = str(path) + ".a4-first-lock"
    raw = json.dumps(lock, sort_keys=True, separators=(",", ":")).replace(
        '"schema":"ALPHA_V11_GATE3_A4_OFFLINE_LOCK_V1"',
        '"schema":NaN').encode()
    path.write_bytes(raw)
    initialize_run_record(lock["run_record"], run_id=lock["run_id"], lock_sha256=_sha(raw))
    with pytest.raises(VerificationError, match="LOCK_NONFINITE"):
        open_verified(path, expected_lock_sha256=_sha(raw))


def test_git_replace_ref_spoof_refuses(source):
    genuine = _git(source, "rev-parse", "HEAD:pkg/helper.py")
    (source / "pkg/helper.py").write_text("VALUE = 666\n")
    evil = _git(source, "hash-object", "-w", "pkg/helper.py")
    subprocess.run(["git", "-C", str(source), "replace", genuine, evil], check=True)
    path, pin = _write_lock(source)
    lock = json.loads(path.read_text())
    helper = next(a for a in lock["artifacts"] if a["path"] == "pkg/helper.py")
    assert helper["git_blob"] == genuine
    with pytest.raises(VerificationError, match="GIT_SOURCE_MISMATCH"):
        open_verified(path, expected_lock_sha256=pin)


def test_changed_loaded_python_bootstrap_refuses(source, tmp_path):
    module_path = tmp_path / "bootmod_a4probe.py"
    module_path.write_text("X = 1\n")
    sys.path.insert(0, str(tmp_path))
    try:
        importlib.import_module("bootmod_a4probe")
        path, pin = _write_lock(source)
        with open_verified(path, expected_lock_sha256=pin) as session:
            module_path.write_text("X = 2  # changed loaded bootstrap source\n")
            with pytest.raises(VerificationError, match="BOOTSTRAP_FILES_CHANGED"):
                session.check_all()
    finally:
        sys.path.remove(str(tmp_path))
        sys.modules.pop("bootmod_a4probe", None)


def test_child_path_metadata_mutation_refuses(source):
    (source / "pkg/main.py").write_text("def main(runtime):\n    return runtime._probe()\n")
    subprocess.run(["git", "-C", str(source), "add", "pkg/main.py"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=R",
                    "-c", "user.email=r@example.invalid", "commit", "-qm", "chmod"], check=True)
    path, pin = _write_lock(source)
    target = source / "decoder.py"
    before = target.stat().st_mode & 0o777
    with open_verified(path, expected_lock_sha256=pin) as session:
        session._probe = lambda: os.chmod(target, 0o777)
        with pytest.raises(VerificationError, match="Operation not permitted"):
            session.run()
    assert target.stat().st_mode & 0o777 == before


def test_host_module_from_import_collision_refuses(source):
    (source / "json").mkdir()
    (source / "json/__init__.py").write_text("")
    (source / "pkg/main.py").write_text(
        "from json import decoder\ndef main(runtime):\n    return decoder.__file__\n")
    subprocess.run(["git", "-C", str(source), "add", "pkg/main.py", "json/__init__.py"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=R",
                    "-c", "user.email=r@example.invalid", "commit", "-qm", "collision"], check=True)
    path, _ = _write_lock(source)
    lock = json.loads(path.read_text())
    data = (source / "json/__init__.py").read_bytes()
    lock["artifacts"].append({"path": "json/__init__.py", "kind": "python",
                              "module": "json", "sha256": _sha(data), "size": len(data),
                              "git_blob": _git(source, "rev-parse", "HEAD:json/__init__.py")})
    path, pin = _new_run(path, lock)
    with pytest.raises(VerificationError, match="HOST_MODULE_COLLISION"):
        open_verified(path, expected_lock_sha256=pin)


def test_late_fileless_host_submodule_refuses_in_child(source):
    (source / "pkg/main.py").write_text(
        "from pkg import ghost\ndef main(runtime):\n    return ghost.value()\n")
    subprocess.run(["git", "-C", str(source), "add", "pkg/main.py"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=R",
                    "-c", "user.email=r@example.invalid", "commit", "-qm", "late"], check=True)
    path, pin = _write_lock(source)
    with open_verified(path, expected_lock_sha256=pin) as session:
        ghost = types.ModuleType("pkg.ghost")
        ghost.value = lambda: 616
        sys.modules["pkg.ghost"] = ghost
        try:
            session.check_all()  # Fileless host modules do not enter the file inventory.
            with pytest.raises(VerificationError, match="HOST_MODULE_COLLISION"):
                session.run()
        finally:
            sys.modules.pop("pkg.ghost", None)


def test_unresolved_from_import_refuses_before_host_fallback(source):
    (source / "pkg/main.py").write_text(
        "from pkg import ghost\ndef main(runtime):\n    return ghost\n")
    subprocess.run(["git", "-C", str(source), "add", "pkg/main.py"], check=True)
    subprocess.run(["git", "-C", str(source), "-c", "user.name=R",
                    "-c", "user.email=r@example.invalid", "commit", "-qm", "missing"], check=True)
    path, pin = _write_lock(source)
    with open_verified(path, expected_lock_sha256=pin) as session:
        with pytest.raises(VerificationError, match="UNEXPECTED_LAZY_IMPORT"):
            session.run()


@pytest.mark.parametrize("missing", ["commit", "tree", "blob"])
def test_git_local_promisor_missing_object_refuses_without_helper(source, missing):
    path, pin = _write_lock(source)
    lock = json.loads(path.read_bytes())
    marker = source.parent / "unlocked-git-helper-ran"
    settings = {"core.repositoryformatversion": "1", "extensions.partialClone": "origin",
                "remote.origin.promisor": "true",
                "remote.origin.partialclonefilter": "blob:none",
                "remote.origin.url": "ext::/usr/bin/touch " + str(marker),
                "protocol.ext.allow": "always"}
    for key, value in settings.items():
        subprocess.run(["git", "-C", str(source), "config", key, value], check=True)
    oid = (lock["commit"] if missing == "commit" else lock["tree"] if missing == "tree"
           else next(item["git_blob"] for item in lock["artifacts"]
                     if item["path"] == "pkg/helper.py"))
    (source / ".git" / "objects" / oid[:2] / oid[2:]).unlink()
    with pytest.raises(VerificationError, match="GIT_IDENTITY_UNAVAILABLE"):
        open_verified(path, expected_lock_sha256=pin)
    assert not marker.exists()


def test_isolated_git_reads_packed_objects_on_this_host(source):
    path, pin = _write_lock(source)
    subprocess.run(["git", "-C", str(source), "gc", "--prune=now"], check=True)
    with open_verified(path, expected_lock_sha256=pin) as session:
        assert session.run() == [7, "reviewed-memfs"]


def test_git_source_object_alternate_refuses(source):
    path, pin = _write_lock(source)
    (source / ".git" / "objects" / "info" / "alternates").write_text(
        str(source.parent / "unreviewed-objects") + "\n")
    with pytest.raises(VerificationError, match="GIT_IDENTITY_UNAVAILABLE"):
        open_verified(path, expected_lock_sha256=pin)


def test_git_fifo_object_alternate_refuses_without_blocking(source):
    path, pin = _write_lock(source)
    os.mkfifo(source / ".git/objects/info/alternates", 0o600)

    def deadline(_signum, _frame):
        raise AssertionError("alternate refusal blocked on FIFO")

    previous = signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, 2.0)
    try:
        with pytest.raises(VerificationError, match="GIT_IDENTITY_UNAVAILABLE"):
            open_verified(path, expected_lock_sha256=pin)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)
