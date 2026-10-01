"""Independent reviewer probes for A4 candidate ef53d61. Each test PASSES when the gap is reproduced."""
import json, os, subprocess, sys, importlib
from pathlib import Path
import pytest
from tests.test_v11_r09_gate3_a4_verify import _write_lock, _sha, source  # noqa: F401
from tools.v11_r09_gate3_a4_verify import VerificationError, open_verified

GIT = ["-c", "user.name=R", "-c", "user.email=r@example.invalid"]

def _commit(root, rel, text, msg):
    (root / rel).write_text(text)
    subprocess.run(["git", "-C", str(root), "add", rel], check=True)
    subprocess.run(["git", "-C", str(root), *GIT, "commit", "-qm", msg], check=True)

def test_restart_under_new_build_accepted_when_resume_digest_omitted(source):
    path, pin = _write_lock(source)
    open_verified(path, expected_lock_sha256=pin).close()
    _commit(source, "pkg/helper.py", "VALUE = 99\n", "different build")
    new_path, new_pin = _write_lock(source)
    # A restarted process that simply omits resume_lock_sha256 runs the new build.
    with open_verified(new_path, expected_lock_sha256=new_pin) as s:
        assert s.run()[0] == 99

def test_duplicate_json_key_last_wins(source):
    path, pin = _write_lock(source)
    raw = path.read_text()
    dup = raw.replace('"entrypoint": "pkg.main"', '"entrypoint": "pkg.main", "entrypoint": "decoder"', 1)
    # Reverse order so a reader sees decoder first? Keep: human sees pkg.main first, parser uses decoder.
    assert dup != raw
    path.write_text(dup)
    with open_verified(path, expected_lock_sha256=_sha(path.read_bytes())) as s:
        assert s.entrypoint == "decoder"

def test_git_replace_ref_spoofs_source_identity(source):
    genuine_blob = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD:pkg/helper.py"]).decode().strip()
    (source / "pkg/helper.py").write_text("VALUE = 666\n")
    evil = subprocess.check_output(["git", "-C", str(source), "hash-object", "-w", "pkg/helper.py"]).decode().strip()
    subprocess.run(["git", "-C", str(source), "replace", genuine_blob, evil], check=True)
    path, pin = _write_lock(source)
    lock = json.loads(path.read_text())
    helper = next(a for a in lock["artifacts"] if a["path"] == "pkg/helper.py")
    assert helper["git_blob"] == genuine_blob  # lock names the genuine reviewed blob ...
    with open_verified(path, expected_lock_sha256=pin) as s:
        assert s.run()[0] == 666            # ... yet unreviewed bytes execute.

def test_pure_python_bootstrap_module_change_undetected(source, tmp_path):
    mod = tmp_path / "bootmod_a4probe.py"
    mod.write_text("X = 1\n")
    sys.path.insert(0, str(tmp_path))
    try:
        importlib.import_module("bootmod_a4probe")
        path, pin = _write_lock(source)
        with open_verified(path, expected_lock_sha256=pin) as s:
            mod.write_text("X = 2  # changed loaded bootstrap source\n")
            s.check_all()  # no refusal: loaded .py bootstrap is not bound
    finally:
        sys.path.remove(str(tmp_path))

def test_child_can_mutate_path_metadata(source):
    _commit(source, "pkg/main.py",
            "def main(runtime):\n    return runtime._probe()\n", "chmod")
    path, pin = _write_lock(source)
    target = source / "decoder.py"
    with open_verified(path, expected_lock_sha256=pin) as s:
        s._probe = lambda: (os.chmod(target, 0o777), os.stat(target).st_mode & 0o777)[1]
        assert s.run() == 0o777

def test_from_import_falls_back_to_unverified_host_module(source):
    # Locked package named like an already-imported host package.
    (source / "json").mkdir()
    (source / "json/__init__.py").write_text("")
    _commit(source, "pkg/main.py",
            "from json import decoder\n"
            "def main(runtime):\n    return decoder.__file__\n", "fallback")
    subprocess.run(["git", "-C", str(source), "add", "json/__init__.py"], check=True)
    subprocess.run(["git", "-C", str(source), *GIT, "commit", "-qm", "pkg"], check=True)
    path, _ = _write_lock(source)
    lock = json.loads(path.read_text())
    data = (source / "json/__init__.py").read_bytes()
    blob = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD:json/__init__.py"]).decode().strip()
    lock["artifacts"].append({"path": "json/__init__.py", "kind": "python", "module": "json",
                              "sha256": _sha(data), "size": len(data), "git_blob": blob})
    path.write_text(json.dumps(lock, sort_keys=True))
    with open_verified(path, expected_lock_sha256=_sha(path.read_bytes())) as s:
        assert not s.run().startswith("sealed://")
