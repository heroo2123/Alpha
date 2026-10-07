"""Adversarial checks for the proposed, unreviewed current-executable pins."""

import ast
import copy
import json
import os
import subprocess
from pathlib import Path

import pytest

from tools import v11_gate3_current_executable_binding as binding

REPO = Path(__file__).resolve().parents[1]
SOURCE = "d1c5602aa77e0d835e416d281b4a78754a3a79df"
MODULE_ROOTS = {
    "tools/v11_r09_gate3_collector.py",
    "tools/v11_r09_gate3_runtime.py",
    "tools/v11_r09_gate3_ledgers.py",
    "tools/v11_r09_gate3_message_sizes.py",
    "tools/v11_r09_gate3_launch.py",
    "tools/v11_gate3_preflight_attempt_model.py",
    "tools/v11_gate3_evidence_intake_guard.py",
    "tools/v11_gate3_raw_decoder_binding.py",
    "tools/v11_r09_gate3_store_v1.py",
}


def _source_git(*args):
    return subprocess.run(["git", "--no-replace-objects", *args], cwd=REPO,
                          env={**os.environ, "GIT_NO_REPLACE_OBJECTS": "1",
                               "GIT_GRAFT_FILE": os.devnull}, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout


def _source_local_import_closure():
    """Independently walk source-commit AST imports and package initializers."""
    files = set(_source_git("ls-tree", "-r", "--name-only", SOURCE).decode().splitlines())
    pending = list(MODULE_ROOTS)
    visited = set()
    while pending:
        path = pending.pop()
        if path in visited:
            continue
        visited.add(path)
        tree = ast.parse(_source_git("show", f"{SOURCE}:{path}"), filename=path)
        package = path.split("/")[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                call = ast.unparse(node.func)
                if call in {"__import__", "importlib.import_module",
                            "importlib.util.spec_from_file_location",
                            "importlib.machinery.SourceFileLoader"}:
                    pytest.fail(f"dynamic local import needs review: {path}: {call}")
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    prefix = package[:len(package) - node.level + 1]
                    base = ".".join(prefix + ([node.module] if node.module else []))
                else:
                    base = node.module or ""
                names = [base] + [base + "." + alias.name for alias in node.names]
            else:
                continue
            for name in names:
                parts = name.split(".")
                for length in range(1, len(parts) + 1):
                    stem = "/".join(parts[:length])
                    for candidate in (stem + ".py", stem + "/__init__.py"):
                        if candidate in files and candidate not in visited:
                            pending.append(candidate)
    return visited


def test_source_local_import_closure_is_pinned():
    closure = _source_local_import_closure()
    missing = closure - binding.PATHS
    if missing:
        pytest.fail(f"source local imports lack pins: {sorted(missing)}")
    if len(closure) != 81:
        pytest.fail(f"source local import closure changed: {len(closure)}")
    for path in ("tools/v11_multimodel_panel.py",
                 "tools/v11_r09_gate3_launch_v4.py",
                 "tools/v11_r09_gate3_offline_io.py",
                 "polymarket_scanner/v11/ecmwf_grib.py"):
        if path not in closure:
            pytest.fail(f"closure scan missed direct or transitive import: {path}")


def test_formerly_unpinned_local_import_drift_refuses_live_and_committed(tmp_path):
    clone = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--shared", "--quiet", str(REPO), str(clone)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (clone / binding.MANIFEST).write_bytes((REPO / binding.MANIFEST).read_bytes())
    target = "tools/v11_multimodel_panel.py"
    source = (clone / target).read_bytes()
    (clone / target).write_bytes(source + b"\n# adverse local import drift\n")
    for committed in (False, True):
        if committed:
            subprocess.run(["git", "add", "--", target], cwd=clone, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            subprocess.run(["git", "-c", "user.name=Binding Test",
                            "-c", "user.email=binding@example.invalid", "commit", "-qm",
                            "adverse local import drift"], cwd=clone, check=True,
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        with pytest.raises(ValueError) as exc:
            binding.verify(clone)
        if str(exc.value) != f"current executable/dependency byte drift: {target}":
            pytest.fail(f"unexpected refusal for local import drift: {exc.value}")


def test_missing_pinned_live_file_has_stable_refusal(tmp_path):
    clone = tmp_path / "checkout"
    subprocess.run(["git", "clone", "--shared", "--quiet", str(REPO), str(clone)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (clone / binding.MANIFEST).write_bytes((REPO / binding.MANIFEST).read_bytes())
    target = "tools/v11_multimodel_panel.py"
    (clone / target).unlink()
    with pytest.raises(ValueError) as exc:
        binding.verify(clone)
    if str(exc.value) != f"non-regular binding path: {target}":
        pytest.fail(f"unexpected missing-file refusal: {exc.value}")


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
    assert len(binding.PATHS) == 92
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
