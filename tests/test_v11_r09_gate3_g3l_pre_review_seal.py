"""Adversarial/focused tests for the G3-L PRE_REVIEW sealing slice.

These prove: only identities the unmodified identity audit already classifies
as retained/offline-reconciled are ever sealed; run/window-scoped identities
are refused outright (no stale-window reuse); drifted or tampered bytes fail
closed instead of being silently sealed; no network access occurs; and the
measured missing_before/missing_after delta is exact and reproducible.
"""

import hashlib
import os
import socket
from pathlib import Path

import pytest

from tools import v11_r09_gate3_g3l_identity_audit as identity_audit
from tools import v11_r09_gate3_g3l_pre_review_seal as subject
from tools.v11_r09_gate3_g3l_prep import ALL_IDS, PRE_REVIEW_IDS, RUN_SPECIFIC, WINDOW_SPECIFIC

REPO = Path(__file__).resolve().parents[1]
ARGS = dict(target_date="2026-10-04", now_utc=1790977200,
            free_disk_bytes=3_000_000_000, available_memory_bytes=1_000_000_000)
DRIFTED_MAPPING = "code.mapping_exact_commit_review"
RETAINED_CONTROL = "protocol.g3i_composition_review_terminal"
EXPECTED_SEALED = frozenset(subject.SEAL_MAP) - {DRIFTED_MAPPING}


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
    assert result["missing_after"] == 71
    assert set(result["sealed_identities"]) == EXPECTED_SEALED
    assert len(result["sealed_identities"]) == 6
    assert result["skipped_identities"] == [{
        "id": DRIFTED_MAPPING,
        "reason": f"category is {identity_audit.FUTURE}, not retained",
    }]
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
    fake_map = {RETAINED_CONTROL: (
        "docs/V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d.md",
        "docs/V11_R09_GATE3_COLLECTION_PROTOCOL.md")}
    with pytest.raises(ValueError, match="not in this identity's own verified source_refs"):
        subject.sealed_evidence(REPO, object_root=tmp_path, seal_map=fake_map, **ARGS)


def test_drifted_bytes_are_skipped_not_sealed(tmp_path, monkeypatch):
    # Same drift-simulation technique as the identity audit's own regression
    # tests: force one retained row's live bytes to disagree with the
    # reviewed baseline, and confirm the sealing tool skips exactly that
    # identity instead of sealing stale/wrongful credit.
    actual = identity_audit._repo_ref
    drifted_path = "docs/V11_R09_GATE3_COMPOSITION_REVIEW_0b7209d.md"

    def changed(repo, rel_path, commit=None):
        result = actual(repo, rel_path, commit)
        if commit is None and rel_path == drifted_path:
            result = dict(result)
            result["sha256"] = "1" * 64
        return result

    monkeypatch.setattr(identity_audit, "_repo_ref", changed)
    evidence, sealed, skipped = subject.sealed_evidence(REPO, object_root=tmp_path, **ARGS)
    assert RETAINED_CONTROL not in sealed
    assert evidence[RETAINED_CONTROL] is None
    assert any(row["id"] == RETAINED_CONTROL for row in skipped)
    # Every other, undrifted identity still seals normally.
    assert set(sealed) == EXPECTED_SEALED - {RETAINED_CONTROL}


def test_tampered_sealed_object_is_caught_by_check_inventory_not_silently_trusted(tmp_path):
    # Even after a slot is sealed, check_inventory independently re-hashes the
    # object-root bytes; a bit-flip after sealing must surface as INVALID,
    # proving the schema is not merely trusting the sealer's own claim.
    from tools.v11_r09_gate3_g3l_prep import SCHEMA, check_inventory
    evidence, sealed, _ = subject.sealed_evidence(REPO, object_root=tmp_path, **ARGS)
    target = RETAINED_CONTROL
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
    assert result["sealed_identity_count"] == 6


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
        return findings + [{"id": RETAINED_CONTROL, "state": "MISSING",
                            "reason": "forced for test"}]

    monkeypatch.setattr(subject, "check_inventory", lying)
    with pytest.raises(ValueError, match=rf"not clean.*{RETAINED_CONTROL}=MISSING"):
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


def test_dangling_symlink_object_path_is_refused_not_written_through(tmp_path):
    # R1 repro: pre-create object_root/<sha256> as a dangling symlink to a
    # writable path outside object_root. The old exists()-then-write_bytes()
    # implementation followed the link and wrote the evidence bytes outside
    # the object root. The fixed implementation must refuse this path
    # entirely and must never create the external target.
    object_root = tmp_path / "objects"
    object_root.mkdir()
    escape_target = tmp_path / "outside" / "escaped.bin"
    escape_target.parent.mkdir()
    data = b"pre-review evidence bytes"
    sha = hashlib.sha256(data).hexdigest()
    (object_root / sha).symlink_to(escape_target)
    with pytest.raises(ValueError, match="not a safe regular file"):
        subject._seal_object(object_root, data)
    assert not os.path.lexists(escape_target)


def test_preexisting_symlink_to_matching_bytes_is_still_refused(tmp_path):
    # Even when the symlink target already holds byte-identical content, the
    # object must never be trusted through a symlink -- reuse is only valid
    # through a no-follow regular-file descriptor on the object path itself.
    object_root = tmp_path / "objects"
    object_root.mkdir()
    data = b"byte identical content"
    sha = hashlib.sha256(data).hexdigest()
    real_target = tmp_path / "real-object.bin"
    real_target.write_bytes(data)
    (object_root / sha).symlink_to(real_target)
    with pytest.raises(ValueError, match="not a safe regular file"):
        subject._seal_object(object_root, data)


def test_seal_object_ignores_a_racy_exists_check(tmp_path, monkeypatch):
    # TOCTOU repro: force Path.exists() to always report "absent" (as it
    # would mid-race between a check and a write) and confirm the
    # implementation never relies on such a check -- creation is atomic
    # (O_CREAT|O_EXCL|O_NOFOLLOW), so a pre-planted symlink is still refused.
    monkeypatch.setattr(Path, "exists", lambda self: False)
    object_root = tmp_path / "objects"
    object_root.mkdir()
    escape_target = tmp_path / "toctou-escape.bin"
    data = b"toctou payload"
    sha = hashlib.sha256(data).hexdigest()
    (object_root / sha).symlink_to(escape_target)
    with pytest.raises(ValueError, match="not a safe regular file"):
        subject._seal_object(object_root, data)
    assert not os.path.lexists(escape_target)


def test_hardlink_alias_with_mismatched_content_is_rejected(tmp_path):
    # A hardlink is a regular file (no symlink type to refuse), so it must
    # fall through to byte verification via the open descriptor and be
    # caught as a digest collision rather than silently trusted.
    object_root = tmp_path / "objects"
    object_root.mkdir()
    external = tmp_path / "external-payload.bin"
    external.write_bytes(b"attacker-controlled bytes")
    data = b"expected sealed evidence bytes"
    sha = hashlib.sha256(data).hexdigest()
    os.link(external, object_root / sha)
    with pytest.raises(ValueError, match="digest collision"):
        subject._seal_object(object_root, data)


def test_tamper_after_seal_via_hardlink_alias_surfaces_as_invalid(tmp_path):
    from tools.v11_r09_gate3_g3l_prep import SCHEMA, check_inventory

    evidence, sealed, _ = subject.sealed_evidence(REPO, object_root=tmp_path, **ARGS)
    target = RETAINED_CONTROL
    assert target in sealed
    sealed_path = tmp_path / evidence[target]["ref"]["path"]
    alias = tmp_path / "attacker-alias.bin"
    os.link(sealed_path, alias)
    alias.write_bytes(b"corrupted through a hardlink alias")
    inventory = {"schema": SCHEMA, "launchable": False,
                "target_date": ARGS["target_date"], "evidence": evidence}
    findings = check_inventory(inventory, target_date=ARGS["target_date"],
                               now_utc=ARGS["now_utc"], stage="PRE_REVIEW",
                               object_root=tmp_path)
    assert next(f for f in findings if f["id"] == target)["state"] == "INVALID"


def test_build_refuses_when_consumer_reports_sealed_identity_invalid(tmp_path, monkeypatch):
    # R2 repro: the old builder only treated MISSING as disqualifying, so a
    # consumer finding of INVALID (tampered/aliased object, or any other
    # non-clean state) on an allegedly sealed identity slipped through as
    # successfully sealed. It must now be a hard build refusal.
    from tools import v11_r09_gate3_g3l_prep as prep

    actual = prep.check_inventory
    target = RETAINED_CONTROL

    def lying(inventory, **kwargs):
        findings = [f for f in actual(inventory, **kwargs) if f["id"] != target]
        findings.append({"id": target, "state": "INVALID", "reason": "forced for test"})
        return findings

    monkeypatch.setattr(subject, "check_inventory", lying)
    with pytest.raises(ValueError, match=rf"not clean.*{target}=INVALID"):
        subject.build_pre_review_inventory(REPO, object_root=tmp_path, **ARGS)


def test_ordinary_valid_object_reuse_across_independent_calls(tmp_path):
    # Non-adversarial control: re-running the sealer against the same
    # object_root must reuse the existing byte-identical objects cleanly,
    # not error, and must not duplicate or alter any sealed digest.
    first_evidence, first_sealed, first_skipped = subject.sealed_evidence(
        REPO, object_root=tmp_path, **ARGS)
    second_evidence, second_sealed, second_skipped = subject.sealed_evidence(
        REPO, object_root=tmp_path, **ARGS)
    assert first_sealed == second_sealed
    assert first_skipped == second_skipped
    assert first_evidence == second_evidence


def test_all_pre_review_ids_accounted_for_as_sealed_or_missing(tmp_path):
    result = subject.build_pre_review_inventory(REPO, object_root=tmp_path, **ARGS)
    sealed = set(result["sealed_identities"])
    missing = {f["id"] for f in result["findings"]}
    assert sealed | missing == set(PRE_REVIEW_IDS)
    assert sealed & missing == set()
    # FINAL_ONLY identities are untouched at PRE_REVIEW stage, by design.
    for final_only in set(ALL_IDS) - set(PRE_REVIEW_IDS):
        assert final_only not in sealed and final_only not in missing
