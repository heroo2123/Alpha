"""Regression coverage closing mutation-testing survivors against the Gate-3
current-executable binding verifier (tools/v11_gate3_current_executable_binding.py).

Derived from an independent mutation-testing campaign over candidate be30ef2
("Repin Gate-3 current executable binding to frozen d806c11 source"). Six of
seven surviving mutants are closed here; explicit pytest.fail checks are used
throughout so assertions remain meaningful under python -O.
"""
import hashlib
import json
import os
import subprocess
import zlib
from pathlib import Path

import pytest

from tools import v11_gate3_current_executable_binding as binding

REPO = Path(binding.__file__).resolve().parents[1]
ENV = {**{k: v for k, v in os.environ.items() if k != "GIT_NO_REPLACE_OBJECTS"},
       "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.invalid",
       "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.invalid"}


def _git(c, *a, inp=None):
    return subprocess.run(["git", *a], cwd=c, env=ENV, input=inp, check=True, capture_output=True).stdout


def _clone(tmp_path):
    c = tmp_path / "checkout"
    _git(tmp_path, "clone", "--shared", "--quiet", str(REPO), str(c))
    (c / binding.MANIFEST).write_bytes((REPO / binding.MANIFEST).read_bytes())
    return c


def _refuses(want, repo, manifest=None):
    try:
        binding.verify(repo, manifest)
    except ValueError as exc:
        if not str(exc).startswith(want):
            pytest.fail(f"unexpected refusal: {exc}")
        return
    pytest.fail(f"accepted; expected refusal {want!r}")


def _has_object(c, oid):
    return subprocess.run(["git", "--no-replace-objects", "cat-file", "-e", oid],
                          cwd=c, env=ENV, capture_output=True).returncode == 0


def _evict_from_every_pack(c, oid):
    """Rebuild every pack in `c` without `oid` and drop any loose copy, so the
    object becomes unresolvable regardless of whether it started out packed
    or loose. This makes a subsequently-written forged loose object the sole
    source for `oid`, instead of silently losing to a surviving packed copy
    (git always prefers a packed object over a same-OID loose one)."""
    keep = b"".join(line + b"\n" for line in _git(c, "rev-list", "--objects", "--all").splitlines()
                     if not line.startswith(oid.encode()))
    pack_dir = c / ".git/objects/pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    before = set(pack_dir.glob("*"))
    _git(c, "pack-objects", "--quiet", str(pack_dir / "excl"), inp=keep)
    for stale in before:
        stale.unlink()
    loose = c / ".git/objects" / oid[:2] / oid[2:]
    if loose.exists():
        loose.unlink()
    if _has_object(c, oid):
        pytest.fail(f"pinned blob {oid} still resolvable after evicting every pack and loose "
                    "copy; loose-substitution setup is not reaching the adversarial condition")
    return loose


def test_substituted_loose_blob_refuses_even_with_forged_manifest_and_live_bytes(tmp_path):
    c = tmp_path / "checkout"
    _git(tmp_path, "clone", "--no-hardlinks", "--quiet", str(REPO), str(c))
    (c / binding.MANIFEST).write_bytes((REPO / binding.MANIFEST).read_bytes())
    path = "tools/v11_r09_gate3_runtime.py"
    oid, original = binding._blob(c, binding.SOURCE_COMMIT, path)
    bad = original + b"\n# substituted loose object\n"
    loose = _evict_from_every_pack(c, oid)
    loose.parent.mkdir(parents=True, exist_ok=True)
    loose.write_bytes(zlib.compress(b"blob %d\0" % len(bad) + bad))
    if _git(c, "--no-replace-objects", "cat-file", "blob", oid) != bad:
        pytest.fail("forged loose object did not take effect even after evicting the packed copy")
    (c / path).write_bytes(bad)
    m = json.loads((REPO / binding.MANIFEST).read_bytes())
    m["files"][path].update(sha256=hashlib.sha256(bad).hexdigest(), byte_length=len(bad))
    forged = tmp_path / "forged.json"
    forged.write_text(json.dumps(m))
    _refuses(f"Git blob content mismatch: {path}", c, forged)


def test_committed_drift_with_restored_live_bytes_refuses(tmp_path):
    c = _clone(tmp_path)
    path = "tools/v11_r09_gate3_collector.py"
    original = (c / path).read_bytes()
    (c / path).write_bytes(original + b"\n# drift\n")
    _git(c, "commit", "-qam", "drift")
    (c / path).write_bytes(original)
    _refuses(f"current executable/dependency byte drift: {path}", c)


def test_identical_tree_orphan_head_refuses(tmp_path):
    c = _clone(tmp_path)
    orphan = _git(c, "commit-tree", _git(c, "rev-parse", "HEAD^{tree}").decode().strip(),
                   inp=b"orphan\n").decode().strip()
    _git(c, "-c", "advice.detachedHead=false", "checkout", "--quiet", orphan)
    _refuses("current checkout does not descend from pinned source", c)


_P = "tools/v11_r09_gate3_collector.py"


def _src(*a):
    return _git(REPO, "--no-replace-objects", *a).decode().strip()


def test_baseline_without_drift_refuses(tmp_path, monkeypatch):
    m = json.loads((REPO / binding.MANIFEST).read_bytes())
    m["historical_baselines"][_P] = {"commit": binding.SOURCE_COMMIT, "tree": binding.SOURCE_TREE,
                                      **m["files"][_P]}
    m["drift_commits"][_P] = []
    monkeypatch.setitem(binding.HISTORICAL, _P, binding.SOURCE_COMMIT)
    monkeypatch.setitem(binding.DRIFT_COMMITS, _P, ())
    f = tmp_path / "m.json"
    f.write_text(json.dumps(m))
    _refuses(f"expected post-baseline drift absent: {_P}", REPO, f)


def test_drift_commit_not_touching_path_refuses(tmp_path, monkeypatch):
    base = binding.HISTORICAL[_P]
    nontouch = next(x for x in _src("rev-list", "--first-parent", "--no-merges",
                                     f"{base}..{binding.SOURCE_COMMIT}").split()
                     if _src("rev-parse", f"{x}:{_P}") == _src("rev-parse", f"{x}^:{_P}"))
    m = json.loads((REPO / binding.MANIFEST).read_bytes())
    m["drift_commits"][_P].append({"commit": nontouch, "classification": "bogus"})
    monkeypatch.setitem(binding.DRIFT_COMMITS, _P, binding.DRIFT_COMMITS[_P] + (nontouch,))
    f = tmp_path / "m.json"
    f.write_text(json.dumps(m))
    _refuses(f"drift commit did not change pinned path: {_P}", REPO, f)


def test_drift_commit_predating_baseline_refuses(tmp_path, monkeypatch):
    older = _src("log", "-1", "--format=%H", "--no-merges", f"{binding.HISTORICAL[_P]}^", "--", _P)
    m = json.loads((REPO / binding.MANIFEST).read_bytes())
    m["drift_commits"][_P].insert(0, {"commit": older, "classification": "stale"})
    monkeypatch.setitem(binding.DRIFT_COMMITS, _P, (older,) + binding.DRIFT_COMMITS[_P])
    f = tmp_path / "m.json"
    f.write_text(json.dumps(m))
    _refuses(f"stale or duplicate drift trace: {_P}", REPO, f)
