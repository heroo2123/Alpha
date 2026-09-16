"""Independent approval/custody checks for the current production host protocol.

The older v2 source-text assertions are superseded by behavioral generation and
loader regressions. A fixture-generated digest proves integrity, never provenance.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from test_host_authority_production_boundary import host, cutover, prepare


def test_policy_requires_separate_execution_identity_and_account_database(host):
    policy = copy.deepcopy(host.policy)
    policy["components"]["execution"]["uid"] = policy["components"]["signals"]["uid"]
    with pytest.raises(host.m.AuthorityError, match="EXECUTION_IDENTITY_NOT_ISOLATED"):
        host.m._validate_policy(policy)
    policy = copy.deepcopy(host.policy)
    policy["execution_db_path"] = policy["db_path"]
    with pytest.raises(host.m.AuthorityError, match="ACCOUNT_JOURNAL_MUST_BE_SEPARATE"):
        host.m._validate_policy(policy)
    policy = copy.deepcopy(host.policy)
    policy["execution_db_path"] = str(Path(policy["db_path"]).parent / "account.sqlite")
    with pytest.raises(host.m.AuthorityError, match="STATE_DIRECTORIES_MUST_BE_SEPARATE"):
        host.m._validate_policy(policy)


def test_policy_rejects_arbitrary_modules_lock_names_and_writer_lock_paths(host):
    for field, value, code in (("module", "candidate_privileged_verifier", "MODULE_INVALID"),
                               ("lock_file", "unreviewed.txt", "LOCK_INVALID")):
        policy = copy.deepcopy(host.policy)
        policy["components"]["execution"][field] = value
        with pytest.raises(host.m.AuthorityError, match=code):
            host.m._validate_policy(policy)
    policy = copy.deepcopy(host.policy)
    policy["writer_lock_paths"] = [str(host.root / "unrelated.lock")]
    with pytest.raises(host.m.AuthorityError, match="WRITER_LOCK_BINDING_INVALID"):
        host.m._validate_policy(policy)


def test_policy_does_not_allow_blind_financial_or_signal_database_rewind(host):
    policy = copy.deepcopy(host.policy)
    policy["database_restore_policy"] = "always_replace"
    with pytest.raises(host.m.AuthorityError, match="DATABASE_REWIND_FORBIDDEN"):
        host.m._validate_policy(policy)


def test_successful_policy_validation_preserves_exact_component_selection(host):
    actual = host.m._validate_policy(host.policy)
    assert actual["components"] == host.policy["components"]
    assert actual["database_restore_policy"] == "unchanged_only"


def test_independent_approval_requires_complete_component_and_exact_distribution_data(host):
    path = Path(host.policy["approval_file"])
    data = json.loads(path.read_text())
    row = next(row for row in data["approved"] if row["sha"] == host.b)
    row["components"].pop("execution")
    path.write_text(json.dumps(data))
    with pytest.raises(host.m.AuthorityError, match="COMPONENT_APPROVAL_MISSING"):
        cutover(host)


def test_mismatched_approved_tree_is_rejected_after_sanitized_object_import(host):
    path = Path(host.policy["approval_file"])
    data = json.loads(path.read_text())
    next(row for row in data["approved"] if row["sha"] == host.b)["tree"] = "f" * 40
    path.write_text(json.dumps(data))
    with pytest.raises(host.m.AuthorityError, match="APPROVED_TREE_MISMATCH"):
        cutover(host)
    assert not host.m.ACTIVE.exists()


def test_sealed_inventory_detects_package_file_change_without_version_change(host):
    gid, manifest = prepare(host)
    venv = Path(manifest["components"]["signals"]["venv_path"])
    path = next((venv / "lib").glob("python*/site-packages/demo.py"))
    path.chmod(0o644)
    path.write_text("VALUE = 'injected while retaining metadata version'\n")
    with pytest.raises(host.m.AuthorityError, match="RUNTIME_VENV_TREE_MISMATCH"):
        host.m.verify_runtime_files(gid, host.b, require_root=False)


def test_installed_unit_disappearance_is_not_accepted(host):
    gid, _ = prepare(host)
    Path(host.policy["unit_file"]).unlink()
    with pytest.raises(host.m.AuthorityError, match="RUNTIME_INSTALLED_UNIT_MISMATCH"):
        host.m.verify_runtime_files(gid, host.b, require_root=False)


def test_generation_seals_code_environment_unit_database_and_service_identity(host):
    gid = cutover(host)
    _, data = host.m._load_generation(gid, host.policy, require_root=False)
    assert data["candidate_sha"] == host.b and data["predecessor_sha"] == host.a
    assert data["db_path"] == str(host.db)
    assert data["predecessor_db_logical_sha256"] == host.m._logical_db_digest(host.db)
    assert data["policy_sha256"] == host.m._sha_bytes(host.m._canonical(host.policy))
    assert set(data["predecessor_units"]) == {"signals", "execution"}
    assert all(len(data[key]) == 64 for key in ("predecessor_bundle_sha256", "objects_tree_sha256", "predecessor_venv_tree_sha256"))
