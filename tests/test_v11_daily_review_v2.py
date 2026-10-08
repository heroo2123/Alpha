"""Synthetic candidate verification only; no protected or runtime files."""
from copy import deepcopy
import json

import pytest

from polymarket_scanner.v11 import certification
from polymarket_scanner.v11 import daily_review_v2 as reader
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.daily_review_v2 import (
    ReviewV2Error, canonical_bytes, select_active_review, sha256,
)


H = "a" * 64


def packet():
    context = dict(namespace="daily-shadow", stage="SHADOW", scope_key=H,
                   metadata_fingerprint="b" * 64, rule_fingerprint="c" * 64,
                   target_date="2026-10-08", event_id="KATL-high-20261008",
                   generation_id="generation-new")
    store = dict(generation_id=context["generation_id"], namespace=context["namespace"],
                 target_date=context["target_date"], event_id=context["event_id"],
                 store_path="/var/lib/alpha/generation-new.db", store_device=9,
                 store_inode=1234, marker_sha256="d" * 64)
    body = dict(action="CAPABILITY_EVIDENCE", scope_key=H, capability="IDENTITY",
                result="PASS", metadata_fingerprint=context["metadata_fingerprint"],
                rule_fingerprint=context["rule_fingerprint"])
    row = dict(seq=1, id="new-proof", kind="REGISTRY", event_id=context["event_id"],
               body=body, body_sha256=sha256(canonical_bytes(body)),
               recorded_at=100, available_at=90)
    review = dict(**context, review_id="new-review",
                  generation_descriptor_sha256=sha256(canonical_bytes(store)),
                  approved_at=110, expires_at=200, reviewed_through_seq=1,
                  runtime_prefix_sha256=sha256(canonical_bytes([row])),
                  review_projection_sha256="e" * 64,
                  candidate_approval_sha256="f" * 64,
                  capability_proofs={"IDENTITY": dict(id=row["id"],
                                                      sha256=row["body_sha256"], seq=1)})
    review_raw = canonical_bytes(review)
    review_hash = sha256(review_raw)
    commission = dict(review_id="new-review", review_sha256=review_hash,
                      generation_id=context["generation_id"],
                      target_date=context["target_date"], event_id=context["event_id"],
                      generation_descriptor_sha256=review["generation_descriptor_sha256"],
                      candidate_approval_sha256=review["candidate_approval_sha256"],
                      commissioned_at=120)
    commission_raw = canonical_bytes(commission)
    commission_hash = sha256(commission_raw)
    selection = dict(**context, review_id="new-review", review_sha256=review_hash,
                     commission_sha256=commission_hash)
    index = dict(version="alpha_v11_certification_reviews_v2", revision=2,
                 previous_registry_sha256="0" * 64,
                 review_objects=[dict(review_id="new-review", review_sha256=review_hash)],
                 active_selections=[selection])
    return dict(index=index, objects={review_hash: review_raw,
                                     commission_hash: commission_raw},
                descriptor=store, prefix=[row], request=context)


def verify(p, **changes):
    kwargs = dict(index_bytes=canonical_bytes(p["index"]), object_bytes=p["objects"],
                  descriptor_bytes=canonical_bytes(p["descriptor"]),
                  prefix_bytes=canonical_bytes(p["prefix"]),
                  store_identity=p["descriptor"], expected_revision=2, now=130,
                  **p["request"])
    kwargs.update(changes)
    return select_active_review(**kwargs)


def replace_review(p, **changes):
    old_hash = p["index"]["review_objects"][0]["review_sha256"]
    review = json.loads(p["objects"].pop(old_hash))
    review.update(changes)
    new_raw = canonical_bytes(review)
    new_hash = sha256(new_raw)
    p["objects"][new_hash] = new_raw
    p["index"]["review_objects"][0]["review_sha256"] = new_hash
    p["index"]["active_selections"][0]["review_sha256"] = new_hash
    old_commission = p["index"]["active_selections"][0]["commission_sha256"]
    commission = json.loads(p["objects"].pop(old_commission))
    commission["review_sha256"] = new_hash
    new_commission = canonical_bytes(commission)
    new_commission_hash = sha256(new_commission)
    p["objects"][new_commission_hash] = new_commission
    p["index"]["active_selections"][0]["commission_sha256"] = new_commission_hash


def refused(p, code, **changes):
    with pytest.raises(ReviewV2Error, match=code):
        verify(p, **changes)


def test_valid_pin_and_immutable_history_is_not_a_selection():
    p = packet()
    old = deepcopy(p)
    old_review_hash = old["index"]["review_objects"][0]["review_sha256"]
    old_review = json.loads(old["objects"][old_review_hash])
    old_review["review_id"] = "old-review"
    old_raw = canonical_bytes(old_review)
    old_hash = sha256(old_raw)
    p["objects"][old_hash] = old_raw
    p["index"]["review_objects"].append(dict(review_id="old-review", review_sha256=old_hash))
    selected = verify(p)
    assert selected.review_id == "new-review"
    assert selected.review_sha256 == old_review_hash
    assert selected.reviewed_through_seq == 1


def test_duplicate_active_semantic_key_even_different_day_and_generation():
    p = packet()
    duplicate = deepcopy(p["index"]["active_selections"][0])
    duplicate["target_date"] = "2026-10-09"
    duplicate["generation_id"] = "generation-other"
    p["index"]["active_selections"].append(duplicate)
    refused(p, "DUPLICATE_ACTIVE_SEMANTIC_KEY")


@pytest.mark.parametrize("change,code", [
    ({"generation_id": "generation-old"}, "ACTIVE_SELECTION_MISSING_OR_AMBIGUOUS"),
    ({"target_date": "2026-10-09"}, "ACTIVE_SELECTION_MISSING_OR_AMBIGUOUS"),
    ({"event_id": "other-event"}, "ACTIVE_SELECTION_MISSING_OR_AMBIGUOUS"),
    ({"scope_key": "9" * 64}, "ACTIVE_SELECTION_MISSING_OR_AMBIGUOUS"),
    ({"expected_revision": 1}, "STALE_REVISION"),
    ({"now": 200}, "SELECTION_NOT_CURRENT"),
    ({"now": 109}, "SELECTION_NOT_CURRENT"),
])
def test_exact_request_revision_and_time(change, code):
    refused(packet(), code, **change)


def test_missing_and_tampered_objects():
    p = packet()
    digest = p["index"]["review_objects"][0]["review_sha256"]
    del p["objects"][digest]
    refused(p, "DANGLING_OBJECT")
    p = packet()
    p["objects"][digest] = b"{}"
    refused(p, "TAMPERED_OBJECT")


@pytest.mark.parametrize("reference", ["review", "commission"])
@pytest.mark.parametrize("raw", [
    pytest.param(b"", id="empty"),
    pytest.param("not bytes", id="wrong-type"),
    pytest.param(b"x" * (reader.MAX_OBJECT_BYTES + 1), id="oversized"),
])
def test_invalid_object_bytes_refuse_before_hash(reference, raw, monkeypatch):
    p = packet()
    selection = p["index"]["active_selections"][0]
    digest = selection["review_sha256" if reference == "review" else "commission_sha256"]
    p["objects"][digest] = raw

    original_sha256 = reader.sha256

    def reject_hash(value):
        if value is raw:
            raise AssertionError("invalid object reached sha256")
        return original_sha256(value)

    monkeypatch.setattr(reader, "sha256", reject_hash)
    refused(p, "BYTE_BOUND")


def test_future_commission_does_not_select():
    p = packet()
    old_hash = p["index"]["active_selections"][0]["commission_sha256"]
    commission = json.loads(p["objects"].pop(old_hash))
    commission["commissioned_at"] = 150
    raw = canonical_bytes(commission)
    digest = sha256(raw)
    p["objects"][digest] = raw
    p["index"]["active_selections"][0]["commission_sha256"] = digest
    refused(p, "COMMISSION_NOT_CURRENT")


def test_duplicate_json_members_and_size_bounds():
    p = packet()
    raw = canonical_bytes(p["index"])
    duplicate = raw.replace(b'"revision":2', b'"revision":2,"revision":2')
    refused(p, "DUPLICATE_JSON_KEY", index_bytes=duplicate)
    refused(p, "BYTE_BOUND", index_bytes=b" " * 1_048_577)
    refused(p, "UNSUPPORTED_VERSION", index_bytes=canonical_bytes(dict(p["index"], version="v1")))


def test_proof_cannot_be_copied_from_old_generation_even_with_same_body_hash():
    p = packet()
    p["prefix"][0]["id"] = "old-proof"
    refused(p, "PREFIX_DIGEST_MISMATCH")
    p = packet()
    p["prefix"][0]["id"] = "old-proof"
    replace_review(p, runtime_prefix_sha256=sha256(canonical_bytes(p["prefix"])))
    refused(p, "PROOF_REFERENCE_MISMATCH")
    p = packet()
    p["prefix"][0]["seq"] = 2
    refused(p, "SPARSE_PREFIX")
    p = packet()
    p["prefix"].append(deepcopy(p["prefix"][0]))
    refused(p, "SPARSE_OR_EXTENDED_PREFIX")


def test_store_identity_and_descriptor_are_bound():
    p = packet()
    changed = dict(p["descriptor"], store_inode=999)
    refused(p, "MIXED_STORE_IDENTITY", store_identity=changed)
    p = packet()
    p["descriptor"]["store_path"] = "/var/lib/alpha/other.db"
    refused(p, "DESCRIPTOR_HASH_MISMATCH")


def test_late_available_proof_refuses_even_with_matching_prefix_digest():
    p = packet()
    p["prefix"][0]["available_at"] = 111
    replace_review(p, runtime_prefix_sha256=sha256(canonical_bytes(p["prefix"])))
    refused(p, "PROOF_SEMANTICS_MISMATCH")


def test_malformed_unselected_history_and_commission_still_refuse():
    p = packet()
    p["index"]["review_objects"].append(dict(review_id="dangling", review_sha256="1" * 64))
    refused(p, "DANGLING_OBJECT")
    p = packet()
    p["index"]["active_selections"][0]["commission_sha256"] = "1" * 64
    refused(p, "DANGLING_OBJECT")
    p = packet()
    raw = canonical_bytes(p["index"])
    refused(p, "NONCANONICAL_JSON", index_bytes=raw + b" ")


def test_explicit_guards_survive_optimized_python():
    # No assert statement implements a refusal in the verifier.
    p = packet()
    assert verify(p).generation_id == "generation-new"
    refused(p, "STALE_REVISION", expected_revision=3)


def test_legacy_v1_reader_refuses_v2_even_with_valid_file_custody(tmp_path, monkeypatch):
    path = tmp_path / "registry.json"
    path.write_bytes(canonical_bytes(packet()["index"]))
    monkeypatch.setattr(certification, "REVIEW_PATH", path)
    monkeypatch.setattr(certification, "_root_custody", lambda candidate: candidate.stat())
    with pytest.raises(EvidenceError, match="CERTIFICATION_REVIEW_SCHEMA"):
        certification.protected_reviews()
