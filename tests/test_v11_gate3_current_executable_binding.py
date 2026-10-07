"""Adversarial checks for the proposed, unreviewed current-executable pins."""

import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from tools import v11_gate3_current_executable_binding as binding

REPO = Path(__file__).resolve().parents[1]


def _manifest():
    return json.loads((REPO / binding.MANIFEST).read_bytes())


def _altered(tmp_path, change):
    data = copy.deepcopy(_manifest())
    change(data)
    path = tmp_path / "binding.json"
    path.write_text(json.dumps(data, sort_keys=True))
    return path


def test_exact_candidate_pins_all_three_and_dependent_protocol_tests():
    result = binding.verify(REPO)
    assert result == {"source_commit": binding.SOURCE_COMMIT,
                      "source_tree": binding.SOURCE_TREE,
                      "verified_files": len(binding.PATHS),
                      "launchable": False, "qualification_credit": 0}
    assert len(binding.HISTORICAL) == 3
    assert len(binding.PATHS) == 20
    assert {p for p in binding.PATHS if p.startswith("tests/")} == {
        "tests/test_v11_r09_gate3_collector.py",
        "tests/test_v11_r09_gate3_message_sizes.py",
        "tests/test_v11_r09_gate3_runtime.py",
        "tests/test_v11_r09_gate3_ledgers.py",
        "tests/test_v11_gate3_attempt_runtime_wiring.py",
        "tests/test_v11_gate3_evidence_intake_launch_wiring.py",
        "tests/test_v11_gate3_raw_decoder_binding.py",
    }


def test_partial_pinning_and_claimed_credit_refuse(tmp_path):
    target = "tools/v11_r09_gate3_ledgers.py"
    for change in (
        lambda m: m["files"].pop(target),
        lambda m: m["historical_baselines"].pop(target),
        lambda m: m["drift_commits"].pop(target),
        lambda m: m.update(qualification_credit=1),
        lambda m: m.update(launchable=True),
    ):
        with pytest.raises(ValueError, match="coverage or authority"):
            binding.verify(REPO, _altered(tmp_path, change))


def test_manifest_blob_digest_and_stale_review_reuse_refuse(tmp_path):
    target = "tools/v11_r09_gate3_runtime.py"
    for change, reason in (
        (lambda m: m["files"][target].update(sha256="f" * 64), "source blob or digest"),
        (lambda m: m["files"][target].update(git_blob="f" * 40), "source blob or digest"),
        (lambda m: m["historical_baselines"][target].update(commit=binding.SOURCE_COMMIT),
         "historical review baseline"),
        (lambda m: m["drift_commits"][target].clear(), "partial drift trace"),
    ):
        with pytest.raises(ValueError, match=reason):
            binding.verify(REPO, _altered(tmp_path, change))
    with pytest.raises(ValueError, match="partial drift trace"):
        binding.verify(REPO, _altered(tmp_path, lambda m:
            m["drift_commits"][target].pop(3)))


def test_live_byte_drift_refuses_even_when_source_git_blob_is_intact(monkeypatch):
    original = binding._live
    target = "tools/v11_r09_gate3_collector.py"
    monkeypatch.setattr(binding, "_live", lambda repo, path:
                        original(repo, path) + b"\n" if path == target else original(repo, path))
    with pytest.raises(ValueError, match="current executable/dependency byte drift"):
        binding.verify(REPO)


def test_git_replace_cannot_rebind_pinned_blob(tmp_path):
    clone = tmp_path / "objects"
    subprocess.run(["git", "clone", "--shared", "--no-checkout", "--quiet", str(REPO), str(clone)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    path = "tools/v11_r09_gate3_runtime.py"
    old_oid, original = binding._blob(clone, binding.SOURCE_COMMIT, path)
    changed = original + b"\n# replacement attack\n"
    new_oid = subprocess.run(["git", "hash-object", "-w", "--stdin"], cwd=clone,
                             input=changed, check=True, stdout=subprocess.PIPE).stdout.decode().strip()
    subprocess.run(["git", "replace", old_oid, new_oid], cwd=clone, check=True,
                   stdout=subprocess.PIPE)
    assert subprocess.run(["git", "cat-file", "blob", old_oid], cwd=clone,
                          check=True, stdout=subprocess.PIPE).stdout == changed
    assert binding._blob(clone, binding.SOURCE_COMMIT, path) == (old_oid, original)


def test_local_graft_cannot_forge_source_ancestry(tmp_path):
    clone = tmp_path / "objects"
    subprocess.run(["git", "clone", "--shared", "--no-checkout", "--quiet", str(REPO), str(clone)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    unrelated = subprocess.run(["git", "commit-tree", binding.SOURCE_TREE], cwd=clone,
                               input=b"unrelated\n", check=True,
                               env={**os.environ, "GIT_AUTHOR_NAME": "Binding Test",
                                    "GIT_AUTHOR_EMAIL": "binding@example.invalid",
                                    "GIT_COMMITTER_NAME": "Binding Test",
                                    "GIT_COMMITTER_EMAIL": "binding@example.invalid"},
                               stdout=subprocess.PIPE).stdout.decode().strip()
    (clone / ".git/info/grafts").write_text(f"{unrelated} {binding.SOURCE_COMMIT}\n")
    assert not binding._raw_ancestor(clone, binding.SOURCE_COMMIT, unrelated)


def test_manifest_duplicate_key_refuses(tmp_path):
    raw = (REPO / binding.MANIFEST).read_text()
    path = tmp_path / "duplicate.json"
    path.write_text(raw.replace('"launchable": false,',
                                '"launchable": false, "launchable": false,', 1))
    with pytest.raises(ValueError, match="duplicate manifest key"):
        binding.verify(REPO, path)
