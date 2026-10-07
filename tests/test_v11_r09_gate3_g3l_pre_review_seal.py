"""Adversarial/focused tests for the G3-L PRE_REVIEW sealing slice.

These prove: only identities the unmodified identity audit already classifies
as retained/offline-reconciled are ever sealed; run/window-scoped identities
are refused outright (no stale-window reuse); drifted or tampered bytes fail
closed instead of being silently sealed; no network access occurs; and the
measured missing_before/missing_after delta is exact and reproducible.
"""

import hashlib
import socket
from pathlib import Path

import pytest

from tools import v11_r09_gate3_g3l_identity_audit as identity_audit
from tools import v11_r09_gate3_g3l_pre_review_seal as subject
from tools.v11_r09_gate3_g3l_prep import ALL_IDS, PRE_REVIEW_IDS, RUN_SPECIFIC, WINDOW_SPECIFIC

REPO = Path(__file__).resolve().parents[1]
ARGS = dict(target_date="2026-10-04", now_utc=1790977200,
            free_disk_bytes=3_000_000_000, available_memory_bytes=1_000_000_000)
EXPECTED_SEALED = frozenset(subject.SEAL_MAP)


def test_seal_map_never_touches_run_or_window_scoped_identities():
    assert EXPECTED_SEALED.isdisjoint(RUN_SPECIFIC)
    assert EXPECTED_SEALED.isdisjoint(WINDOW_SPECIFIC)


def test_seal_map_identities_are_all_real_pre_review_ids():
    assert EXPECTED_SEALED <= set(PRE_REVIEW_IDS)
    for ref_path, review_path in subject.SEAL_MAP.values():
        assert ref_path != review_path


def test_exact_missing_before_after_and_sealed_set(tmp_path):
    result = subject.build_pre_review_inventory(REPO, object_root=tmp_path, **ARGS)
    assert result["qualification_credit"] == 0
    assert result["launchable"] is False
    assert result["missing_before"] == 77
    assert result["missing_after"] == 70
    assert set(result["sealed_identities"]) == EXPECTED_SEALED
    assert len(result["sealed_identities"]) == 7
    assert result["skipped_identities"] == []
    missing_ids = {f["id"] for f in result["findings"]}
    assert missing_ids == set(PRE_REVIEW_IDS) - EXPECTED_SEALED
    assert missing_ids.isdisjoint(EXPECTED_SEALED)


def test_no_wrongful_credit_for_future_or_invented_identity(tmp_path):
    # A FUTURE-category identity (never passed independent review in scope)
    # must be skipped, not sealed, even if a seal_map tried to include it
    # with otherwise well-formed paths drawn from its own real source_refs.
    fake_map = dict(subject.SEAL_MAP)
    fake_map[identity_audit.SLICE3_ID] = (
        "tools/v11_r09_gate3_runtime.py", "tools/v11_r09_gate3_ledgers.py")
    evidence, sealed, skipped = subject.sealed_evidence(
        REPO, object_root=tmp_path, seal_map=fake_map, **ARGS)
    assert identity_audit.SLICE3_ID not in sealed
    assert evidence[identity_audit.SLICE3_ID] is None
    assert any(row["id"] == identity_audit.SLICE3_ID for row in skipped)


def test_refuses_to_seal_a_run_specific_identity(tmp_path):
    fake_map = {sorted(RUN_SPECIFIC)[0]: ("a", "b")}
    with pytest.raises(ValueError, match="run/window-scoped"):
        subject.sealed_evidence(REPO, object_root=tmp_path, seal_map=fake_map, **ARGS)


def test_refuses_to_seal_a_window_specific_identity(tmp_path):
    fake_map = {sorted(WINDOW_SPECIFIC)[0]: ("a", "b")}
    with pytest.raises(ValueError, match="run/window-scoped"):
        subject.sealed_evidence(REPO, object_root=tmp_path, seal_map=fake_map, **ARGS)


def test_refuses_identical_ref_and_review_path(tmp_path):
    fake_map = {"code.mapping_exact_commit_review": ("docs/x.md", "docs/x.md")}
    with pytest.raises(ValueError, match="must differ"):
        subject.sealed_evidence(REPO, object_root=tmp_path, seal_map=fake_map, **ARGS)


def test_refuses_unknown_identity(tmp_path):
    fake_map = {"not.a.real.identity": ("docs/a.md", "docs/b.md")}
    with pytest.raises(ValueError, match="unknown identity"):
        subject.sealed_evidence(REPO, object_root=tmp_path, seal_map=fake_map, **ARGS)


def test_refuses_path_not_in_identitys_own_verified_refs(tmp_path):
    fake_map = {"code.mapping_exact_commit_review": (
        "docs/V11_R09_GATE3_V4_PROVIDER_MAPPING_REPAIR_REVIEW_23c11e0.md",
        "docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md")}
    with pytest.raises(ValueError, match="not in this identity's own verified source_refs"):
        subject.sealed_evidence(REPO, object_root=tmp_path, seal_map=fake_map, **ARGS)


def test_drifted_bytes_are_skipped_not_sealed(tmp_path, monkeypatch):
    # Same drift-simulation technique as the identity audit's own regression
    # tests: force one retained row's live bytes to disagree with the
    # reviewed baseline, and confirm the sealing tool skips exactly that
    # identity instead of sealing stale/wrongful credit.
    actual = identity_audit._repo_ref
    drifted_path = "docs/V11_R09_GATE3_V4_PROVIDER_MAPPING_REPAIR_REVIEW_23c11e0.md"

    def changed(repo, rel_path, commit=None):
        result = actual(repo, rel_path, commit)
        if commit is None and rel_path == drifted_path:
            result = dict(result)
            result["sha256"] = "1" * 64
        return result

    monkeypatch.setattr(identity_audit, "_repo_ref", changed)
    evidence, sealed, skipped = subject.sealed_evidence(REPO, object_root=tmp_path, **ARGS)
    assert "code.mapping_exact_commit_review" not in sealed
    assert evidence["code.mapping_exact_commit_review"] is None
    assert any(row["id"] == "code.mapping_exact_commit_review" for row in skipped)
    # Every other, undrifted identity still seals normally.
    assert set(sealed) == EXPECTED_SEALED - {"code.mapping_exact_commit_review"}


def test_tampered_sealed_object_is_caught_by_check_inventory_not_silently_trusted(tmp_path):
    # Even after a slot is sealed, check_inventory independently re-hashes the
    # object-root bytes; a bit-flip after sealing must surface as INVALID,
    # proving the schema is not merely trusting the sealer's own claim.
    from tools.v11_r09_gate3_g3l_prep import SCHEMA, check_inventory
    evidence, sealed, _ = subject.sealed_evidence(REPO, object_root=tmp_path, **ARGS)
    target = "code.mapping_exact_commit_review"
    assert target in sealed
    tampered_path = tmp_path / evidence[target]["ref"]["path"]
    data = bytearray(tampered_path.read_bytes())
    data[0] ^= 0xFF
    tampered_path.write_bytes(bytes(data))
    inventory = {"schema": SCHEMA, "launchable": False,
                "target_date": ARGS["target_date"], "evidence": evidence}
    findings = check_inventory(inventory, target_date=ARGS["target_date"],
                               now_utc=ARGS["now_utc"], stage="PRE_REVIEW",
                               object_root=tmp_path)
    assert next(f for f in findings if f["id"] == target)["state"] == "INVALID"


def test_no_network_access(tmp_path, monkeypatch):
    monkeypatch.setattr(socket.socket, "connect",
                        lambda *args: pytest.fail("socket access"))
    result = subject.build_pre_review_inventory(REPO, object_root=tmp_path, **ARGS)
    assert result["sealed_identity_count"] == 7


def test_sealed_objects_are_exact_byte_copies(tmp_path):
    evidence, sealed, _ = subject.sealed_evidence(REPO, object_root=tmp_path, **ARGS)
    for identity in sealed:
        entry = evidence[identity]
        for slot in ("ref", "review_ref"):
            blob = tmp_path / entry[slot]["path"]
            data = blob.read_bytes()
            assert hashlib.sha256(data).hexdigest() == entry[slot]["sha256"]
            assert len(data) == entry[slot]["byte_length"]
        assert entry["ref"]["sha256"] != entry["review_ref"]["sha256"]


def test_build_inventory_self_check_raises_if_check_inventory_disagrees(tmp_path, monkeypatch):
    from tools import v11_r09_gate3_g3l_prep as prep

    actual = prep.check_inventory

    def lying(inventory, **kwargs):
        findings = actual(inventory, **kwargs)
        return findings + [{"id": "code.mapping_exact_commit_review", "state": "MISSING",
                            "reason": "forced for test"}]

    monkeypatch.setattr(subject, "check_inventory", lying)
    with pytest.raises(ValueError, match="still reported MISSING"):
        subject.build_pre_review_inventory(REPO, object_root=tmp_path, **ARGS)


def test_unsupported_media_type_refused(tmp_path):
    with pytest.raises(ValueError, match="unsupported sealed media type"):
        subject._media_type("docs/foo.bin")


def test_object_root_digest_collision_detected(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    sha = hashlib.sha256(b"a").hexdigest()
    (tmp_path / sha).write_bytes(b"different bytes same name")
    with pytest.raises(ValueError, match="digest collision"):
        subject._seal_object(tmp_path, b"a")


def test_all_pre_review_ids_accounted_for_as_sealed_or_missing(tmp_path):
    result = subject.build_pre_review_inventory(REPO, object_root=tmp_path, **ARGS)
    sealed = set(result["sealed_identities"])
    missing = {f["id"] for f in result["findings"]}
    assert sealed | missing == set(PRE_REVIEW_IDS)
    assert sealed & missing == set()
    # FINAL_ONLY identities are untouched at PRE_REVIEW stage, by design.
    for final_only in set(ALL_IDS) - set(PRE_REVIEW_IDS):
        assert final_only not in sealed and final_only not in missing
