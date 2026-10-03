"""Adversarial byte-binding checks; the real Git/hash checks remain active."""

import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from tools import v11_r09_gate3_g3l_identity_audit as audit_tool


REPO = Path(__file__).resolve().parents[1]
ARGS = dict(target_date="2026-10-04", now_utc=1790977200,
            free_disk_bytes=3_000_000_000, available_memory_bytes=1_000_000_000)


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def git(*args):
    return subprocess.run(["git", *args], cwd=REPO, check=True,
                          stdout=subprocess.PIPE).stdout.decode().strip()


def source_overlay(monkeypatch, mutation):
    source = json.loads((REPO / audit_tool.RECONCILIATION).read_bytes())
    mutation(source)
    replacement = json.dumps(source, sort_keys=True).encode()
    real = audit_tool._evidence_bytes

    def read(repo, path, cache):
        if path == audit_tool.RECONCILIATION:
            cache.setdefault(path, replacement)
            return cache[path]
        return real(repo, path, cache)

    monkeypatch.setattr(audit_tool, "_evidence_bytes", read)


@pytest.mark.parametrize("mutation", [
    lambda source: source["code_byte_observations"]["injected_runtime"].update(
        commit_oid=git("rev-parse", "HEAD"), tree_oid=git("rev-parse", "HEAD^{tree}"),
        sha256=hashlib.sha256((REPO / "tools/v11_r09_gate3_runtime.py").read_bytes()).hexdigest()),
    lambda source: source["code_byte_observations"]["launch_validator"].update(
        commit_oid=source["artifacts"]["tools/v11_r09_gate3_launch_v4.py"]["git_commit"],
        tree_oid=git("rev-parse", source["artifacts"]["tools/v11_r09_gate3_launch_v4.py"]["git_commit"] + "^{tree}")),
    lambda source: source["code_byte_observations"]["offline_decoder_and_clock_types"].update(
        path="tools/v11_r09_gate3_runtime.py"),
    lambda source: source["code_byte_observations"]["launch_validator"].update(
        path="tools/v11_r09_gate3_ledgers.py"),
    lambda source: source["artifacts"]["docs/V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d.md"].update(
        git_commit="HEAD"),
])
def test_rebound_or_aliased_source_cannot_cross_git_root(monkeypatch, mutation):
    source_overlay(monkeypatch, mutation)
    with pytest.raises(ValueError, match="reviewed trust-root bytes changed"):
        audit_tool.audit(REPO, **ARGS)


@pytest.mark.parametrize("path", [audit_tool.REVIEW, audit_tool.REVIEW_REPORT,
                                  audit_tool.REVIEW_TERMINAL])
def test_retained_review_bundle_requires_exact_git_bytes(monkeypatch, path):
    replacement = (REPO / path).read_bytes() + b" "
    real = audit_tool._evidence_bytes

    def read(repo, rel, cache):
        if rel == path:
            cache.setdefault(rel, replacement)
            return cache[rel]
        return real(repo, rel, cache)

    monkeypatch.setattr(audit_tool, "_evidence_bytes", read)
    with pytest.raises(ValueError, match="reviewed trust-root bytes changed"):
        audit_tool.audit(REPO, **ARGS)


@pytest.mark.parametrize("value", [None, "", "HEAD", "A" * 40, "0" * 40])
def test_artifact_commit_rejects_mutable_or_malformed_reference(value):
    with pytest.raises(ValueError):
        audit_tool._validate_artifact_commit(REPO, value, "docs/example.md")


def test_artifact_commit_rejects_tree_blob_and_descendant():
    for oid in (git("rev-parse", audit_tool.MAPPING_COMMIT + "^{tree}"),
                git("rev-parse", audit_tool.MAPPING_COMMIT + ":tools/v11_r09_gate3_launch_v4.py"),
                git("rev-parse", "HEAD")):
        with pytest.raises(ValueError):
            audit_tool._validate_artifact_commit(REPO, oid, "docs/example.md")


def test_trusted_dependency_coverage_is_exact():
    require(set(audit_tool.CODE_BYTE_OBSERVATION_PATHS) == audit_tool.CODE_BYTE_OBSERVATION_NAMES,
            "observation names changed")
    require(audit_tool.REUSABLE_ROW_CODE_DEPENDENCIES["code.mapping_exact_commit_review"] == {
        ("tools/v11_r09_gate3_launch_v4.py", audit_tool.MAPPING_COMMIT)}, "mapping pin changed")
    require(audit_tool.REUSABLE_ROW_CODE_DEPENDENCIES[audit_tool.SLICE3_ID] == {
        ("tools/v11_r09_gate3_runtime.py", audit_tool.SLICE3_COMMIT),
        ("tools/v11_r09_gate3_ledgers.py", audit_tool.SLICE3_COMMIT)}, "slice-3 pins changed")
    require(all(not dependencies for identity, dependencies in
                audit_tool.REUSABLE_ROW_CODE_DEPENDENCIES.items()
                if identity not in ("code.mapping_exact_commit_review", audit_tool.SLICE3_ID)),
            "protocol dependency pin changed")


def test_dependency_coverage_refuses_shrunk_repointed_and_extra_sets():
    runtime = {"path": "tools/v11_r09_gate3_runtime.py", "git_commit": audit_tool.SLICE3_COMMIT}
    ledgers = {"path": "tools/v11_r09_gate3_ledgers.py", "git_commit": audit_tool.SLICE3_COMMIT}
    audit_tool._require_code_dependencies(audit_tool.SLICE3_ID, [runtime, ledgers])
    for refs in ([ledgers],
                 [dict(runtime, git_commit=git("rev-parse", "HEAD")), ledgers],
                 [runtime, ledgers, ledgers]):
        with pytest.raises(ValueError, match="code dependency coverage changed"):
            audit_tool._require_code_dependencies(audit_tool.SLICE3_ID, refs)
    with pytest.raises(ValueError, match="code dependency coverage changed"):
        audit_tool._require_code_dependencies("code.mapping_exact_commit_review", [])


def test_alias_and_hash_consistent_path_substitution_refuse():
    source = json.loads((REPO / audit_tool.RECONCILIATION).read_bytes())
    observations = source["code_byte_observations"]
    artifacts = source["artifacts"]
    seen = set()
    audit_tool._observation_fields("injected_runtime", observations["injected_runtime"],
                                   artifacts, seen)
    alias = dict(observations["offline_decoder_and_clock_types"],
                 path="tools/v11_r09_gate3_runtime.py")
    with pytest.raises(ValueError, match="malformed code_byte_observation"):
        audit_tool._observation_fields("offline_decoder_and_clock_types", alias,
                                       artifacts, seen)
    ledgers_at_mapping = subprocess.run(
        ["git", "cat-file", "blob", audit_tool.MAPPING_COMMIT + ":tools/v11_r09_gate3_ledgers.py"],
        cwd=REPO, check=True, stdout=subprocess.PIPE).stdout
    replacement = dict(observations["launch_validator"],
                       path="tools/v11_r09_gate3_ledgers.py",
                       sha256=hashlib.sha256(ledgers_at_mapping).hexdigest())
    # Even a matching hash for the substituted path cannot change its pinned name.
    with pytest.raises(ValueError, match="malformed code_byte_observation"):
        audit_tool._observation_fields("launch_validator", replacement, artifacts, set())


def test_fifo_and_symlink_are_rejected_without_reading(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    reconciliation = tmp_path / audit_tool.RECONCILIATION
    os.mkfifo(reconciliation)
    with pytest.raises(ValueError, match="non-regular evidence file"):
        audit_tool.audit(tmp_path, **ARGS)
    reconciliation.unlink()
    reconciliation.symlink_to(REPO / audit_tool.RECONCILIATION)
    with pytest.raises(ValueError, match="unreadable evidence file"):
        audit_tool.audit(tmp_path, **ARGS)


def test_one_read_cache_binds_parsed_and_hashed_bytes_after_swap(tmp_path):
    path = tmp_path / "evidence"
    first = b'{"first":true}'
    path.write_bytes(first)
    cache = {}
    data = audit_tool._evidence_bytes(tmp_path, "evidence", cache)
    path.write_bytes(b'{"second":true}')
    require(audit_tool._evidence_bytes(tmp_path, "evidence", cache) == data,
            "cached bytes changed after swap")
    require(json.loads(data) == {"first": True}, "parsed bytes changed after swap")
    require(audit_tool._digest(data) == hashlib.sha256(first).hexdigest(),
            "hashed bytes changed after swap")


def test_local_git_replace_rebind_cannot_retain_drifted_slice3(tmp_path):
    # Reproduce the review's four-object attack with real replace refs in an
    # isolated local clone. No monkeypatch can hide a missed Git call here.
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--shared", "--quiet", str(REPO), str(clone)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def local_git(*args, data=None):
        return subprocess.run(["git", *args], cwd=clone, input=data, check=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE).stdout

    def replace_path(commit, path, changed):
        old = local_git("--no-replace-objects", "rev-parse", f"{commit}:{path}").decode().strip()
        new = local_git("hash-object", "-w", "--stdin", data=changed).decode().strip()
        local_git("replace", "-f", old, new)
        require(local_git("cat-file", "blob", f"{commit}:{path}") == changed,
                f"replace ref did not rebind {path}")
        require(local_git("--no-replace-objects", "cat-file", "blob",
                          f"{commit}:{path}") != changed,
                f"historical bytes unexpectedly changed for {path}")

    runtime_path = audit_tool.CODE_BYTE_OBSERVATION_PATHS["injected_runtime"]
    runtime = (clone / runtime_path).read_bytes()
    replace_path(audit_tool.SLICE3_COMMIT, runtime_path, runtime)
    # A replacement by itself must not poison the historical baseline.
    before_rebind = audit_tool.audit(clone, **ARGS)
    require(before_rebind["identities"][audit_tool.SLICE3_ID]["category"] == audit_tool.FUTURE,
            "replaced historical runtime changed slice-3 classification")

    reconciliation = json.loads((clone / audit_tool.RECONCILIATION).read_bytes())
    runtime_hash = hashlib.sha256(runtime).hexdigest()
    reconciliation["code_byte_observations"]["injected_runtime"]["sha256"] = runtime_hash
    reconciliation["artifacts"][runtime_path].update(
        sha256=runtime_hash, byte_length=len(runtime))
    rebound_source = (json.dumps(reconciliation, indent=2, sort_keys=True) + "\n").encode()
    (clone / audit_tool.RECONCILIATION).write_bytes(rebound_source)
    replace_path(audit_tool.RECONCILIATION_COMMIT, audit_tool.RECONCILIATION,
                 rebound_source)

    verdict = json.loads((clone / audit_tool.REVIEW).read_bytes())
    verdict["candidate_json_sha256"] = hashlib.sha256(rebound_source).hexdigest()
    rebound_verdict = (json.dumps(verdict, indent=2, sort_keys=True) + "\n").encode()
    (clone / audit_tool.REVIEW).write_bytes(rebound_verdict)
    replace_path(audit_tool.REVIEW_RETENTION_COMMIT, audit_tool.REVIEW,
                 rebound_verdict)

    terminal = json.loads((clone / audit_tool.REVIEW_TERMINAL).read_bytes())
    terminal["verdict_sha256"] = hashlib.sha256(rebound_verdict).hexdigest()
    rebound_terminal = (json.dumps(terminal, indent=2, sort_keys=True) + "\n").encode()
    (clone / audit_tool.REVIEW_TERMINAL).write_bytes(rebound_terminal)
    replace_path(audit_tool.REVIEW_RETENTION_COMMIT, audit_tool.REVIEW_TERMINAL,
                 rebound_terminal)

    require(len(local_git("for-each-ref", "--format=%(refname)", "refs/replace").splitlines()) == 4,
            "attack did not install all four replace refs")
    with pytest.raises(ValueError, match="reviewed trust-root bytes changed"):
        audit_tool.audit(clone, **ARGS)


def test_local_graft_cannot_rebind_artifact_ancestry(tmp_path):
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--shared", "--quiet", str(REPO), str(clone)],
                   check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=clone, check=True,
                          stdout=subprocess.PIPE).stdout.decode().strip()
    grafts = clone / ".git/info/grafts"
    grafts.write_text(f"{audit_tool.RECONCILIATION_COMMIT} {head}\n")
    with pytest.raises(ValueError, match="not a reviewed ancestor"):
        audit_tool._validate_artifact_commit(clone, head, "docs/example.md")
