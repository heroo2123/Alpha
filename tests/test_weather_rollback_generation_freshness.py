"""Behavioral generation/archive replacements for retired shell spellings."""
from pathlib import Path
import json
import pytest
from test_host_authority_production_boundary import host, cutover, prepare, git


def test_archive_integrity_alone_does_not_prove_current_venv_and_tree_check_detects_drift(host):
    gid = cutover(host)
    gdir, data = host.m._load_generation(gid, host.policy, require_root=False)
    archive = gdir / "predecessor-venv.tar"
    before = host.m.sha256_file(archive)
    (host.app / ".venv/injected.py").write_text("fixture pollution")
    assert host.m.sha256_file(archive) == before
    assert host.m._tree_digest(host.app / ".venv") != data["predecessor_venv_tree_sha256"]
    host.m.recover(gid, require_root=False)
    assert host.m._tree_digest(host.app / ".venv") == data["predecessor_venv_tree_sha256"]


def test_snapshot_of_candidate_cannot_replace_immutable_predecessor(host):
    gid, _ = prepare(host)
    host.m.activate_checkout(gid, host.b, require_root=False)
    gdir, before = host.m._load_generation(gid, host.policy, require_root=False)
    with pytest.raises(host.m.AuthorityError, match="CUTOVER_ALREADY_ACTIVE"):
        cutover(host)
    assert json.loads((gdir / "manifest.json").read_text()) == before
    host.m.recover(gid, require_root=False)
    assert git(host.app, "rev-parse", "HEAD") == host.a


def test_wrong_generation_recovery_cannot_select_an_unrelated_predecessor(host):
    gid = cutover(host)
    before = host.m.ACTIVE.read_bytes()
    with pytest.raises((OSError, host.m.AuthorityError)):
        host.m.recover("f" * 64, require_root=False)
    assert host.m.ACTIVE.read_bytes() == before
    assert git(host.app, "rev-parse", "HEAD") == host.a


def test_corrupted_rollback_archive_blocks_preparation_before_checkout_or_environment_mutation(host):
    gid = cutover(host)
    gdir = host.m._generation_root(host.policy) / gid
    before = host.m._tree_digest(host.app / ".venv")
    host.m._safe_write(gdir / "predecessor-venv.tar", b"corrupted", root_custody=False)
    with pytest.raises(host.m.AuthorityError, match="CUTOVER_PAYLOAD_DIGEST_MISMATCH"):
        host.m.prepare_candidate(gid, host.b, require_root=False)
    assert git(host.app, "rev-parse", "HEAD") == host.a
    assert host.m._tree_digest(host.app / ".venv") == before
