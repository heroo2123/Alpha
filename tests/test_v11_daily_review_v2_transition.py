"""Synthetic candidate verification only; no protected or runtime files.

Builds a synthetic MIGRATE -> ACTIVATE -> SUPERSEDE -> GATE chain and checks
that verify_transition accepts the valid chain and refuses each adversarial
mutation with a distinct code. Uses exc.value.args[0] equality rather than
pytest.raises(match=...) throughout: under `python -O`, `match=` has been
observed elsewhere in this repo to stop checking message content, so code
identity is asserted directly instead.
"""
from copy import deepcopy
from dataclasses import fields

import pytest

from polymarket_scanner.v11 import certification
from polymarket_scanner.v11 import daily_review_v2 as reader
from polymarket_scanner.v11 import daily_review_v2_transition as tr


H1 = "1" * 64
H2 = "2" * 64
H3 = "3" * 64


def put(objects, value):
    raw = reader.canonical_bytes(value)
    digest = reader.sha256(raw)
    objects[digest] = raw
    return digest


def make_review_commission(objects, *, generation_id, review_id, candidate_approval_sha256,
                            scope_key=H1, target_date="2026-10-08",
                            event_id="KATL-high-20261008", approved_at=100,
                            expires_at=500, commissioned_at=110,
                            generation_descriptor_sha256="4" * 64):
    context = dict(namespace="daily-shadow", stage="SHADOW", scope_key=scope_key,
                    metadata_fingerprint=H2, rule_fingerprint=H3,
                    target_date=target_date, event_id=event_id, generation_id=generation_id)
    descriptor_hash = generation_descriptor_sha256
    review = dict(**context, review_id=review_id,
                  generation_descriptor_sha256=descriptor_hash,
                  approved_at=approved_at, expires_at=expires_at, reviewed_through_seq=1,
                  runtime_prefix_sha256="5" * 64, review_projection_sha256="6" * 64,
                  candidate_approval_sha256=candidate_approval_sha256,
                  capability_proofs={"IDENTITY": dict(id="proof-1", sha256="7" * 64, seq=1)})
    review_raw = reader.canonical_bytes(review)
    review_hash = reader.sha256(review_raw)
    commission = dict(review_id=review_id, review_sha256=review_hash, generation_id=generation_id,
                       target_date=target_date, event_id=event_id,
                       generation_descriptor_sha256=descriptor_hash,
                       candidate_approval_sha256=candidate_approval_sha256,
                       commissioned_at=commissioned_at)
    commission_raw = reader.canonical_bytes(commission)
    commission_hash = reader.sha256(commission_raw)
    selection = dict(**context, review_id=review_id, review_sha256=review_hash,
                      commission_sha256=commission_hash)
    objects[review_hash] = review_raw
    objects[commission_hash] = commission_raw
    return dict(review=review, review_hash=review_hash, commission=commission,
                commission_hash=commission_hash, selection=selection)


def provenance_obj(*, predecessor_evidence_sha256, generation_descriptor_sha256):
    return dict(version=tr.PROVENANCE_VERSION,
                generation_descriptor_sha256=generation_descriptor_sha256,
                predecessor_evidence_sha256=predecessor_evidence_sha256,
                genesis_import_sha256="1" * 64, seed_sha256="2" * 64, seed_count=1,
                release_commit="a" * 40, runner_sha256="3" * 64, config_sha256="4" * 64,
                import_closure_sha256="5" * 64, scheduler_identity="scheduler-1",
                audit_identity="audit-1", coordinator_identity="coordinator-1",
                timezone="America/New_York")


def interval_obj(objects, *, status="NOT_APPLICABLE", start_at=0, end_at=0, tag="interval"):
    evidence_hash = put(objects, dict(kind=tag))
    return dict(start_at=start_at, end_at=end_at, status=status, evidence_sha256=evidence_hash)


def finalize(core, transition_obj, objects, *, transition_id="transition-1"):
    transition_raw = reader.canonical_bytes(transition_obj)
    transition_hash = reader.sha256(transition_raw)
    objects[transition_hash] = transition_raw
    index = dict(core, supersession_objects=[dict(transition_id=transition_id,
                                                   transition_sha256=transition_hash)])
    index_bytes = reader.canonical_bytes(index)
    return index_bytes, transition_hash


def migrate_scenario():
    objects = {}
    member = dict(namespace="daily-shadow", stage="SHADOW", scope_key=H1,
                  metadata_fingerprint=H2, rule_fingerprint=H3, review_id="legacy-review-1",
                  reviewer="reviewer-1", approved_at=50, expires_at=400,
                  reviewed_through_seq=1, capability_proofs={})
    manifest = dict(version="alpha_v11_certification_reviews_v1", reviews=[member])
    manifest_raw = reader.canonical_bytes(manifest)
    manifest_hash = reader.sha256(manifest_raw)
    member_canonical_hash = reader.sha256(reader.canonical_bytes(member))

    candidate_approval_hash = put(objects, dict(kind="candidate-approval-migrate"))
    rc = make_review_commission(objects, generation_id="generation-1", review_id="new-review-1",
                                 candidate_approval_sha256=candidate_approval_hash)
    predecessor_evidence_hash = put(objects, dict(kind="predecessor-evidence-migrate"))
    provenance_hash = put(objects, provenance_obj(
        predecessor_evidence_sha256=predecessor_evidence_hash,
        generation_descriptor_sha256=rc["review"]["generation_descriptor_sha256"]))
    commissioning_hash = put(objects, dict(kind="commissioning-approval-migrate"))
    interval = interval_obj(objects, tag="interval-migrate")

    core = dict(version=tr.EXT_VERSION, revision=1,
                previous_registry_sha256=manifest_hash,
                review_objects=[dict(review_id=rc["review"]["review_id"],
                                      review_sha256=rc["review_hash"])],
                active_selections=[rc["selection"]])
    next_state_sha256 = reader.sha256(reader.canonical_bytes(core))
    semantic_key = {key: rc["selection"][key] for key in reader.KEY}
    old_selection = dict(kind="LEGACY_UNBOUND", manifest_sha256=manifest_hash,
                         review_member_index=0, review_id="legacy-review-1",
                         canonical_member_sha256=member_canonical_hash)
    transition_obj = dict(
        version=tr.TRANSITION_VERSION, transition_id="transition-1",
        request_sha256="8" * 64, operation="MIGRATE", reason="INITIAL_GENERATION",
        previous_revision=0, previous_registry_sha256=manifest_hash,
        next_revision=1, next_state_sha256=next_state_sha256,
        semantic_key=semantic_key, old_selection=old_selection, new_selection=rc["selection"],
        predecessor_evidence_sha256=predecessor_evidence_hash,
        successor_provenance_sha256=provenance_hash,
        candidate_approval_sha256=candidate_approval_hash,
        commissioning_approval_sha256=commissioning_hash,
        cutover_at=120, incomplete_interval=interval)
    index_bytes, transition_hash = finalize(core, transition_obj, objects)
    return dict(index_bytes=index_bytes, objects=objects, predecessor_bytes=manifest_raw,
                predecessor_object_bytes={}, core=core, transition_obj=transition_obj,
                transition_hash=transition_hash, semantic_key_tuple=tuple(semantic_key[k] for k in reader.KEY),
                review=rc["review"], review_hash=rc["review_hash"], commission_hash=rc["commission_hash"],
                selection=rc["selection"])


def activate_scenario(base=None):
    base = base or migrate_scenario()
    predecessor_bytes = base["index_bytes"]
    predecessor_object_bytes = {base["review_hash"]: base["objects"][base["review_hash"]],
                                base["commission_hash"]: base["objects"][base["commission_hash"]]}
    objects = {base["review_hash"]: base["objects"][base["review_hash"]],
               base["commission_hash"]: base["objects"][base["commission_hash"]]}

    candidate_approval_hash = put(objects, dict(kind="candidate-approval-activate"))
    rc = make_review_commission(objects, generation_id="generation-2", review_id="new-review-2",
                                 scope_key=H1[:-1] + "9", candidate_approval_sha256=candidate_approval_hash)
    predecessor_evidence_hash = put(objects, dict(kind="predecessor-evidence-activate"))
    provenance_hash = put(objects, provenance_obj(
        predecessor_evidence_sha256=predecessor_evidence_hash,
        generation_descriptor_sha256=rc["review"]["generation_descriptor_sha256"]))
    commissioning_hash = put(objects, dict(kind="commissioning-approval-activate"))
    interval = interval_obj(objects, tag="interval-activate")

    core = dict(version=tr.EXT_VERSION, revision=2,
                previous_registry_sha256=reader.sha256(predecessor_bytes),
                review_objects=[dict(review_id=base["review"]["review_id"],
                                      review_sha256=base["review_hash"]),
                                dict(review_id=rc["review"]["review_id"],
                                     review_sha256=rc["review_hash"])],
                active_selections=[base["selection"], rc["selection"]])
    next_state_sha256 = reader.sha256(reader.canonical_bytes(core))
    semantic_key = {key: rc["selection"][key] for key in reader.KEY}
    transition_obj = dict(
        version=tr.TRANSITION_VERSION, transition_id="transition-2",
        request_sha256="9" * 64, operation="ACTIVATE", reason="INITIAL_GENERATION",
        previous_revision=1, previous_registry_sha256=reader.sha256(predecessor_bytes),
        next_revision=2, next_state_sha256=next_state_sha256,
        semantic_key=semantic_key, old_selection=None, new_selection=rc["selection"],
        predecessor_evidence_sha256=predecessor_evidence_hash,
        successor_provenance_sha256=provenance_hash,
        candidate_approval_sha256=candidate_approval_hash,
        commissioning_approval_sha256=commissioning_hash,
        cutover_at=220, incomplete_interval=interval)
    index_bytes, transition_hash = finalize(core, transition_obj, objects, transition_id="transition-2")
    return dict(index_bytes=index_bytes, objects=objects, predecessor_bytes=predecessor_bytes,
                predecessor_object_bytes=predecessor_object_bytes, core=core,
                transition_obj=transition_obj, transition_hash=transition_hash,
                semantic_key_tuple=tuple(semantic_key[k] for k in reader.KEY),
                review=rc["review"], review_hash=rc["review_hash"], commission_hash=rc["commission_hash"],
                selection=rc["selection"], base=base)


def supersede_scenario(base=None):
    base = base or activate_scenario()
    predecessor_bytes = base["index_bytes"]
    carried = {k: base["objects"][k] for k in
               (base["base"]["review_hash"], base["base"]["commission_hash"],
                base["review_hash"], base["commission_hash"])}
    predecessor_object_bytes = dict(carried)
    objects = dict(carried)

    candidate_approval_hash = put(objects, dict(kind="candidate-approval-supersede"))
    rc = make_review_commission(objects, generation_id="generation-3", review_id="new-review-3",
                                 scope_key=base["selection"]["scope_key"],
                                 candidate_approval_sha256=candidate_approval_hash)
    predecessor_evidence_hash = put(objects, dict(kind="predecessor-evidence-supersede"))
    provenance_hash = put(objects, provenance_obj(
        predecessor_evidence_sha256=predecessor_evidence_hash,
        generation_descriptor_sha256=rc["review"]["generation_descriptor_sha256"]))
    commissioning_hash = put(objects, dict(kind="commissioning-approval-supersede"))
    interval = interval_obj(objects, tag="interval-supersede")

    core = dict(version=tr.EXT_VERSION, revision=3,
                previous_registry_sha256=reader.sha256(predecessor_bytes),
                review_objects=[
                    dict(review_id=base["base"]["review"]["review_id"],
                         review_sha256=base["base"]["review_hash"]),
                    dict(review_id=base["review"]["review_id"], review_sha256=base["review_hash"]),
                    dict(review_id=rc["review"]["review_id"], review_sha256=rc["review_hash"])],
                active_selections=[base["base"]["selection"], rc["selection"]])
    next_state_sha256 = reader.sha256(reader.canonical_bytes(core))
    semantic_key = {key: rc["selection"][key] for key in reader.KEY}
    old_selection = dict(kind="V2", selection=base["selection"])
    transition_obj = dict(
        version=tr.TRANSITION_VERSION, transition_id="transition-3",
        request_sha256="a" * 64, operation="SUPERSEDE", reason="REVIEWED_RESELECTION",
        previous_revision=2, previous_registry_sha256=reader.sha256(predecessor_bytes),
        next_revision=3, next_state_sha256=next_state_sha256,
        semantic_key=semantic_key, old_selection=old_selection, new_selection=rc["selection"],
        predecessor_evidence_sha256=predecessor_evidence_hash,
        successor_provenance_sha256=provenance_hash,
        candidate_approval_sha256=candidate_approval_hash,
        commissioning_approval_sha256=commissioning_hash,
        cutover_at=320, incomplete_interval=interval)
    index_bytes, transition_hash = finalize(core, transition_obj, objects, transition_id="transition-3")
    return dict(index_bytes=index_bytes, objects=objects, predecessor_bytes=predecessor_bytes,
                predecessor_object_bytes=predecessor_object_bytes, core=core,
                transition_obj=transition_obj, transition_hash=transition_hash,
                semantic_key_tuple=tuple(semantic_key[k] for k in reader.KEY),
                review=rc["review"], review_hash=rc["review_hash"], commission_hash=rc["commission_hash"],
                selection=rc["selection"], old_selection=base["selection"], base=base)


def gate_scenario(base=None):
    base = base or supersede_scenario()
    predecessor_bytes = base["index_bytes"]
    predecessor_object_bytes = dict(base["objects"])
    objects = dict(base["objects"])

    predecessor_evidence_hash = put(objects, dict(kind="predecessor-evidence-gate"))
    commissioning_hash = put(objects, dict(kind="commissioning-approval-gate"))
    interval = interval_obj(objects, status="INCOMPLETE", tag="interval-gate")

    core = dict(version=tr.EXT_VERSION, revision=4,
                previous_registry_sha256=reader.sha256(predecessor_bytes),
                review_objects=list(base["core"]["review_objects"]),
                active_selections=[base["base"]["base"]["selection"]])
    next_state_sha256 = reader.sha256(reader.canonical_bytes(core))
    semantic_key = {key: base["selection"][key] for key in reader.KEY}
    old_selection = dict(kind="V2", selection=base["selection"])
    transition_obj = dict(
        version=tr.TRANSITION_VERSION, transition_id="transition-4",
        request_sha256="b" * 64, operation="GATE", reason="FAIL_CLOSED",
        previous_revision=3, previous_registry_sha256=reader.sha256(predecessor_bytes),
        next_revision=4, next_state_sha256=next_state_sha256,
        semantic_key=semantic_key, old_selection=old_selection, new_selection=None,
        predecessor_evidence_sha256=predecessor_evidence_hash,
        successor_provenance_sha256=None,
        candidate_approval_sha256=None,
        commissioning_approval_sha256=commissioning_hash,
        cutover_at=420, incomplete_interval=interval)
    index_bytes, transition_hash = finalize(core, transition_obj, objects, transition_id="transition-4")
    return dict(index_bytes=index_bytes, objects=objects, predecessor_bytes=predecessor_bytes,
                predecessor_object_bytes=predecessor_object_bytes, core=core,
                transition_obj=transition_obj, transition_hash=transition_hash,
                semantic_key_tuple=tuple(semantic_key[k] for k in reader.KEY), base=base)


def call(scenario, **overrides):
    kwargs = dict(index_bytes=scenario["index_bytes"], object_bytes=scenario["objects"],
                  predecessor_bytes=scenario["predecessor_bytes"],
                  predecessor_object_bytes=scenario["predecessor_object_bytes"])
    kwargs.update(overrides)
    return tr.verify_transition(**kwargs)


def refused(code, scenario, **overrides):
    with pytest.raises(reader.ReviewV2Error) as exc_info:
        call(scenario, **overrides)
    assert exc_info.value.args[0] == code


def rebuilt(scenario, *, core=None, transition_obj=None, transition_id=None, objects=None):
    core = core if core is not None else scenario["core"]
    transition_obj = transition_obj if transition_obj is not None else scenario["transition_obj"]
    objects = objects if objects is not None else scenario["objects"]
    tid = transition_id or scenario["transition_obj"]["transition_id"]
    index_bytes, _ = finalize(core, transition_obj, objects, transition_id=tid)
    return index_bytes


def provenance_for(objects, *, predecessor_evidence_sha256, review):
    return put(objects, provenance_obj(predecessor_evidence_sha256=predecessor_evidence_sha256,
                                        generation_descriptor_sha256=review["generation_descriptor_sha256"]))


def next_edge(predecessor, operation, reason, *, active, old_selection, new_selection,
              review=None, add_reviews=(), candidate_approval_sha256=None,
              provenance_hash=None, cutover_at=150, extra_objects=None, transition_id=None,
              request_sha256="c" * 64):
    """Build a complete, self-consistent next edge on top of ``predecessor``
    (a scenario/edge dict with ``index_bytes``/``objects``/``core``), for
    adversarial tests that need a revision beyond the base four-step chain.
    ``review`` supplies the new review object for provenance/candidate
    binding when ``new_selection`` is not None; both default to automatic,
    self-consistent values unless a test overrides them to probe one
    specific refusal."""
    pred_index = reader._parse(predecessor["index_bytes"], reader.MAX_INDEX_BYTES)
    objects = dict(predecessor["objects"])
    if extra_objects:
        objects.update(extra_objects)
    tag = operation + str(pred_index["revision"])
    predecessor_evidence_hash = put(objects, dict(kind="pe-" + tag))
    commissioning_hash = put(objects, dict(kind="ca-" + tag))
    interval = interval_obj(objects, tag="iv-" + tag)
    if provenance_hash is None and new_selection is not None and review is not None:
        provenance_hash = provenance_for(objects, predecessor_evidence_sha256=predecessor_evidence_hash,
                                          review=review)
    if candidate_approval_sha256 is None and review is not None:
        candidate_approval_sha256 = review["candidate_approval_sha256"]
    core = dict(version=tr.EXT_VERSION, revision=pred_index["revision"] + 1,
                previous_registry_sha256=reader.sha256(predecessor["index_bytes"]),
                review_objects=list(predecessor["core"]["review_objects"]) + list(add_reviews),
                active_selections=active)
    key_source = new_selection if new_selection is not None else old_selection["selection"]
    semantic_key = {k: key_source[k] for k in reader.KEY}
    transition_obj = dict(
        version=tr.TRANSITION_VERSION, transition_id=transition_id or ("t-" + tag),
        request_sha256=request_sha256, operation=operation, reason=reason,
        previous_revision=pred_index["revision"], previous_registry_sha256=reader.sha256(predecessor["index_bytes"]),
        next_revision=pred_index["revision"] + 1, next_state_sha256=reader.sha256(reader.canonical_bytes(core)),
        semantic_key=semantic_key, old_selection=old_selection, new_selection=new_selection,
        predecessor_evidence_sha256=predecessor_evidence_hash,
        successor_provenance_sha256=provenance_hash,
        candidate_approval_sha256=candidate_approval_sha256,
        commissioning_approval_sha256=commissioning_hash,
        cutover_at=cutover_at, incomplete_interval=interval)
    index_bytes, _ = finalize(core, transition_obj, objects, transition_id=transition_obj["transition_id"])
    return dict(index_bytes=index_bytes, objects=objects, core=core, transition_obj=transition_obj,
                predecessor_bytes=predecessor["index_bytes"],
                predecessor_object_bytes=dict(predecessor["objects"]))


# ---------------------------------------------------------------------------
# Valid chain
# ---------------------------------------------------------------------------

def test_migrate_establishes_revision_one():
    scenario = migrate_scenario()
    result = call(scenario)
    assert result.operation == "MIGRATE"
    assert result.revision == 1
    assert result.semantic_key == scenario["semantic_key_tuple"]
    assert result.review_id == scenario["review"]["review_id"]
    assert result.review_sha256 == scenario["review_hash"]
    assert result.commission_sha256 == scenario["commission_hash"]
    assert result.candidate_approval_sha256 == scenario["review"]["candidate_approval_sha256"]
    assert result.next_state_sha256 == reader.sha256(reader.canonical_bytes(scenario["core"]))


def test_activate_new_key_after_migrate():
    scenario = activate_scenario()
    result = call(scenario)
    assert result.operation == "ACTIVATE"
    assert result.revision == 2
    assert result.review_id == scenario["review"]["review_id"]


def test_supersede_reselection():
    scenario = supersede_scenario()
    result = call(scenario)
    assert result.operation == "SUPERSEDE"
    assert result.revision == 3
    assert result.review_id == scenario["review"]["review_id"]


def test_gate_removes_selection():
    scenario = gate_scenario()
    result = call(scenario)
    assert result.operation == "GATE"
    assert result.revision == 4
    assert result.review_id is None
    assert result.review_sha256 is None
    assert result.commission_sha256 is None
    assert result.candidate_approval_sha256 is None


def test_commissioning_subject_sha256_omits_only_its_own_field():
    scenario = migrate_scenario()
    result = call(scenario)
    subject = dict(scenario["transition_obj"])
    del subject["commissioning_approval_sha256"]
    assert result.commissioning_subject_sha256 == reader.sha256(reader.canonical_bytes(subject))


def test_verify_transition_takes_no_now_parameter():
    scenario = migrate_scenario()
    with pytest.raises(TypeError):
        call(scenario, now=1000)


def test_verified_transition_has_no_authorizing_field():
    # Explicit non-authorizing-packet assertion: success still carries no
    # field that could be mistaken for an admission/approval/custody grant.
    names = {f.name for f in fields(tr.VerifiedTransition)}
    forbidden = {"authorized", "approved", "admitted", "pass", "eligible",
                 "activation_authorized", "financial_authority"}
    assert not names & forbidden
    result = call(migrate_scenario())
    assert not hasattr(result, "authorized")


def test_old_v1_reader_refuses_extended_index_even_with_valid_custody(tmp_path, monkeypatch):
    scenario = migrate_scenario()
    path = tmp_path / "registry.json"
    path.write_bytes(scenario["index_bytes"])
    monkeypatch.setattr(certification, "REVIEW_PATH", path)
    monkeypatch.setattr(certification, "_root_custody", lambda candidate: candidate.stat())
    from polymarket_scanner.v11.evidence import EvidenceError
    with pytest.raises(EvidenceError) as exc_info:
        certification.protected_reviews()
    assert exc_info.value.args[0] == "CERTIFICATION_REVIEW_SCHEMA"


def test_old_five_key_v2_reader_refuses_extended_index():
    scenario = migrate_scenario()
    index = reader._parse(scenario["index_bytes"], reader.MAX_INDEX_BYTES)
    assert set(index) == set(tr.EXT_INDEX_KEYS)
    with pytest.raises(reader.ReviewV2Error) as exc_info:
        reader.select_active_review(
            index_bytes=scenario["index_bytes"], object_bytes=scenario["objects"],
            descriptor_bytes=b"{}", prefix_bytes=b"[]", store_identity={},
            namespace="daily-shadow", stage="SHADOW", scope_key=H1,
            metadata_fingerprint=H2, rule_fingerprint=H3, target_date="2026-10-08",
            event_id="KATL-high-20261008", generation_id="generation-1",
            expected_revision=1, now=1000)
    assert exc_info.value.args[0] == "OBJECT_SCHEMA"


# ---------------------------------------------------------------------------
# Schema / graph refusals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("entries", [[], [dict(transition_id="t", transition_sha256="0" * 64)] * 2])
def test_supersession_shape_bounds(entries):
    scenario = migrate_scenario()
    index = reader._parse(scenario["index_bytes"], reader.MAX_INDEX_BYTES)
    index = dict(index, supersession_objects=entries)
    index_bytes = reader.canonical_bytes(index)
    refused("SUPERSESSION_SHAPE", scenario, index_bytes=index_bytes)


def test_dangling_transition_reference():
    scenario = migrate_scenario()
    index = reader._parse(scenario["index_bytes"], reader.MAX_INDEX_BYTES)
    index = dict(index, supersession_objects=[dict(transition_id="transition-1",
                                                     transition_sha256="0" * 64)])
    refused("DANGLING_OBJECT", scenario, index_bytes=reader.canonical_bytes(index))


def test_malformed_transition_missing_field():
    scenario = migrate_scenario()
    transition_obj = dict(scenario["transition_obj"])
    del transition_obj["cutover_at"]
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("OBJECT_SCHEMA", scenario, index_bytes=index_bytes)


def test_unsupported_transition_version():
    scenario = migrate_scenario()
    transition_obj = dict(scenario["transition_obj"], version="other")
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("UNSUPPORTED_TRANSITION_VERSION", scenario, index_bytes=index_bytes)


def test_unsupported_index_version():
    scenario = migrate_scenario()
    core = dict(scenario["core"], version="alpha_v11_certification_reviews_v2")
    transition_obj = dict(scenario["transition_obj"],
                           next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(scenario, core=core, transition_obj=transition_obj)
    refused("UNSUPPORTED_VERSION", scenario, index_bytes=index_bytes)


def test_core_hash_mismatch():
    scenario = migrate_scenario()
    transition_obj = dict(scenario["transition_obj"], next_state_sha256="0" * 64)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("CORE_HASH_MISMATCH", scenario, index_bytes=index_bytes)


def test_predecessor_hash_mismatch_index_vs_transition():
    scenario = migrate_scenario()
    core = dict(scenario["core"], previous_registry_sha256="0" * 64)
    index_bytes = rebuilt(scenario, core=core)
    refused("PREDECESSOR_HASH_MISMATCH", scenario, index_bytes=index_bytes)


def test_predecessor_hash_mismatch_wrong_predecessor_bytes():
    scenario = migrate_scenario()
    refused("PREDECESSOR_HASH_MISMATCH", scenario,
            predecessor_bytes=reader.canonical_bytes(dict(
                version="alpha_v11_certification_reviews_v1", reviews=[])))


@pytest.mark.parametrize("operation,reason", [("ACTIVATE", "FAIL_CLOSED"),
                                               ("GATE", "INITIAL_GENERATION"),
                                               ("SUPERSEDE", "INITIAL_GENERATION")])
def test_operation_reason_pairing(operation, reason):
    scenario = migrate_scenario()
    transition_obj = dict(scenario["transition_obj"], operation=operation, reason=reason)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("OPERATION_REASON_PAIRING", scenario, index_bytes=index_bytes)


def test_migrate_revision_fixed():
    scenario = migrate_scenario()
    core = dict(scenario["core"], revision=2)
    transition_obj = dict(scenario["transition_obj"], previous_revision=1, next_revision=2,
                           next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(scenario, core=core, transition_obj=transition_obj)
    refused("MIGRATE_REVISION_FIXED", scenario, index_bytes=index_bytes)


def test_revision_sequence_mismatch():
    scenario = activate_scenario()
    transition_obj = dict(scenario["transition_obj"], next_revision=5)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("REVISION_SEQUENCE_MISMATCH", scenario, index_bytes=index_bytes)


def test_duplicate_review_id():
    scenario = migrate_scenario()
    core = deepcopy(scenario["core"])
    core["review_objects"].append(dict(core["review_objects"][0]))
    index_bytes = rebuilt(scenario, core=core,
                           transition_obj=dict(scenario["transition_obj"],
                                                next_state_sha256=reader.sha256(
                                                    reader.canonical_bytes(core))))
    refused("DUPLICATE_REVIEW_ID", scenario, index_bytes=index_bytes)


def test_duplicate_active_semantic_key():
    scenario = activate_scenario()
    core = deepcopy(scenario["core"])
    core["active_selections"].append(dict(core["active_selections"][0]))
    transition_obj = dict(scenario["transition_obj"],
                           next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(scenario, core=core, transition_obj=transition_obj)
    refused("DUPLICATE_ACTIVE_SEMANTIC_KEY", scenario, index_bytes=index_bytes)


# ---------------------------------------------------------------------------
# Binding / forged selection refusals
# ---------------------------------------------------------------------------

def test_old_selection_forged():
    scenario = supersede_scenario()
    forged = dict(scenario["old_selection"], review_id="someone-elses-review")
    transition_obj = dict(scenario["transition_obj"],
                           old_selection=dict(kind="V2", selection=forged))
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("OLD_SELECTION_FORGED", scenario, index_bytes=index_bytes)


def test_old_selection_forbidden_for_activate():
    scenario = activate_scenario()
    fake_old = dict(kind="V2", selection=scenario["base"]["selection"])
    transition_obj = dict(scenario["transition_obj"], old_selection=fake_old)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("OLD_SELECTION_FORBIDDEN_FOR_ACTIVATE", scenario, index_bytes=index_bytes)


def test_activate_requires_no_prior_selection():
    scenario = activate_scenario()
    # Reuse the already-active migrate key instead of a fresh one; a single
    # replacing entry (not two) so DUPLICATE_ACTIVE_SEMANTIC_KEY cannot mask
    # the check under test.
    migrate_key_selection = scenario["base"]["selection"]
    semantic_key = {k: migrate_key_selection[k] for k in reader.KEY}
    objects = dict(scenario["objects"])
    candidate_approval_hash = put(objects, dict(kind="candidate-approval-conflict"))
    rc = make_review_commission(objects, generation_id="generation-conflict",
                                 review_id="conflict-review",
                                 scope_key=migrate_key_selection["scope_key"],
                                 candidate_approval_sha256=candidate_approval_hash)
    core = deepcopy(scenario["core"])
    core["review_objects"].append(dict(review_id=rc["review"]["review_id"],
                                        review_sha256=rc["review_hash"]))
    core["active_selections"] = [rc["selection"]]
    transition_obj = dict(scenario["transition_obj"], semantic_key=semantic_key,
                           new_selection=rc["selection"],
                           next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(scenario, core=core, transition_obj=transition_obj, objects=objects)
    refused("ACTIVATE_REQUIRES_NO_PRIOR_SELECTION", scenario, index_bytes=index_bytes, object_bytes=objects)


def test_new_selection_not_published():
    scenario = activate_scenario()
    different = dict(scenario["selection"], review_id="ghost-review")
    transition_obj = dict(scenario["transition_obj"], new_selection=different)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("NEW_SELECTION_NOT_PUBLISHED", scenario, index_bytes=index_bytes)


def test_supersede_requires_change():
    scenario = supersede_scenario()
    same = dict(scenario["old_selection"])
    core = deepcopy(scenario["core"])
    core["active_selections"] = [scenario["base"]["base"]["selection"], same]
    core["review_objects"] = [e for e in core["review_objects"]
                               if e["review_id"] != scenario["review"]["review_id"]]
    transition_obj = dict(scenario["transition_obj"], new_selection=same,
                           next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(scenario, core=core, transition_obj=transition_obj)
    refused("SUPERSEDE_REQUIRES_CHANGE", scenario, index_bytes=index_bytes)


def test_unrelated_selection_changed():
    scenario = activate_scenario()
    base = scenario["base"]
    objects = dict(scenario["objects"])
    candidate_approval_hash = put(objects, dict(kind="candidate-approval-rewrite"))
    rc = make_review_commission(objects, generation_id="generation-1b",
                                 review_id=base["review"]["review_id"] + "-b",
                                 scope_key=base["selection"]["scope_key"],
                                 candidate_approval_sha256=candidate_approval_hash)
    core = deepcopy(scenario["core"])
    core["review_objects"].append(dict(review_id=rc["review"]["review_id"],
                                        review_sha256=rc["review_hash"]))
    core["active_selections"][0] = rc["selection"]
    transition_obj = dict(scenario["transition_obj"],
                           next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(scenario, core=core, transition_obj=transition_obj, objects=objects)
    refused("UNRELATED_SELECTION_CHANGED", scenario, index_bytes=index_bytes, object_bytes=objects)


def test_review_history_rewritten():
    scenario = activate_scenario()
    core = deepcopy(scenario["core"])
    core["review_objects"] = [e for e in core["review_objects"]
                               if e["review_id"] != scenario["base"]["review"]["review_id"]]
    transition_obj = dict(scenario["transition_obj"],
                           next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(scenario, core=core, transition_obj=transition_obj)
    refused("DANGLING_ACTIVE_REVIEW", scenario, index_bytes=index_bytes)


# ---------------------------------------------------------------------------
# Approval / provenance / DAG refusals
# ---------------------------------------------------------------------------

def test_candidate_approval_required_for_non_gate():
    scenario = activate_scenario()
    transition_obj = dict(scenario["transition_obj"], candidate_approval_sha256=None)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("CANDIDATE_APPROVAL_REQUIRED", scenario, index_bytes=index_bytes)


def test_successor_provenance_required_for_non_gate():
    scenario = activate_scenario()
    transition_obj = dict(scenario["transition_obj"], successor_provenance_sha256=None)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("SUCCESSOR_PROVENANCE_REQUIRED", scenario, index_bytes=index_bytes)


def test_gate_accepts_null_approvals():
    scenario = gate_scenario()
    assert scenario["transition_obj"]["candidate_approval_sha256"] is None
    assert scenario["transition_obj"]["successor_provenance_sha256"] is None
    result = call(scenario)
    assert result.operation == "GATE"


def test_candidate_approval_binding_mismatch():
    scenario = activate_scenario()
    objects = dict(scenario["objects"])
    other_hash = put(objects, dict(kind="unbound-candidate-approval"))
    transition_obj = dict(scenario["transition_obj"], candidate_approval_sha256=other_hash)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj, objects=objects)
    refused("CANDIDATE_APPROVAL_BINDING_MISMATCH", scenario, index_bytes=index_bytes, object_bytes=objects)


def test_metadata_shared_reference_allowed():
    # A true forward-hash cycle (digest A's bytes containing digest B, and
    # B's bytes containing A) cannot be constructed by forward hashing at
    # all -- confirming "impossible by construction." What *is*
    # constructible, and must not be mistaken for a cycle, is the same
    # evidence object legitimately reachable from two different transition
    # references (here: commissioning approval and incomplete-interval
    # evidence happen to be the same retained object). The walker memoizes
    # by digest rather than erroring on every revisit, so this succeeds.
    scenario = migrate_scenario()
    objects = dict(scenario["objects"])
    shared_hash = put(objects, dict(kind="shared-evidence"))
    interval = dict(scenario["transition_obj"]["incomplete_interval"], evidence_sha256=shared_hash)
    transition_obj = dict(scenario["transition_obj"], commissioning_approval_sha256=shared_hash,
                           incomplete_interval=interval)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj, objects=objects)
    result = call(scenario, index_bytes=index_bytes, object_bytes=objects)
    assert result.operation == "MIGRATE"


def test_metadata_graph_bound():
    scenario = migrate_scenario()
    objects = dict(scenario["objects"])
    digest = put(objects, dict(kind="chain-tail"))
    for i in range(tr.MAX_METADATA_OBJECTS + 1):
        raw = reader.canonical_bytes(dict(kind="chain-link", next=digest))
        digest = reader.sha256(raw)
        objects[digest] = raw
    transition_obj = dict(scenario["transition_obj"], predecessor_evidence_sha256=digest)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj, objects=objects)
    refused("METADATA_GRAPH_BOUND", scenario, index_bytes=index_bytes, object_bytes=objects)


# ---------------------------------------------------------------------------
# Bound / malformed-field refusals
# ---------------------------------------------------------------------------

def test_incomplete_interval_order():
    scenario = migrate_scenario()
    bad_interval = dict(scenario["transition_obj"]["incomplete_interval"], start_at=10, end_at=5)
    transition_obj = dict(scenario["transition_obj"], incomplete_interval=bad_interval)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("INCOMPLETE_INTERVAL_ORDER", scenario, index_bytes=index_bytes)


def test_incomplete_interval_status():
    scenario = migrate_scenario()
    bad_interval = dict(scenario["transition_obj"]["incomplete_interval"], status="BOGUS")
    transition_obj = dict(scenario["transition_obj"], incomplete_interval=bad_interval)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("INCOMPLETE_INTERVAL_STATUS", scenario, index_bytes=index_bytes)


def test_object_map_bound():
    scenario = migrate_scenario()
    objects = dict(scenario["objects"])
    for i in range(reader.MAX_REVIEWS * 2 + 1):
        objects["pad" + str(i)] = b"x"
    refused("OBJECT_MAP_BOUND", scenario, object_bytes=objects)


def test_predecessor_object_map_bound():
    scenario = activate_scenario()
    padded = dict(scenario["predecessor_object_bytes"])
    for i in range(reader.MAX_REVIEWS * 2 + 1):
        padded["pad" + str(i)] = b"x"
    refused("OBJECT_MAP_BOUND", scenario, predecessor_object_bytes=padded)


# ---------------------------------------------------------------------------
# F1 -- historical (superseded / FAIL_CLOSED-gated) reviews must not be
# reselectable with their original commission and candidate approval.
# ---------------------------------------------------------------------------

def test_activate_reselects_gated_historical_review_refused():
    sup = supersede_scenario()
    g = gate_scenario(sup)
    r3_selection = sup["selection"]
    r3_review = sup["review"]
    e = next_edge(g, "ACTIVATE", "INITIAL_GENERATION",
                  active=list(g["core"]["active_selections"]) + [r3_selection],
                  old_selection=None, new_selection=r3_selection, review=r3_review, cutover_at=120)
    refused("HISTORICAL_REVIEW_REUSE", e)


def test_supersede_rolls_back_to_previously_superseded_review_refused():
    sup = supersede_scenario()
    act = sup["base"]
    r2_selection = act["selection"]
    r2_review = act["review"]
    e = next_edge(sup, "SUPERSEDE", "REVIEWED_RESELECTION",
                  active=[act["base"]["selection"], r2_selection],
                  old_selection=dict(kind="V2", selection=sup["selection"]),
                  new_selection=r2_selection, review=r2_review, cutover_at=120)
    refused("HISTORICAL_REVIEW_REUSE", e)


# ---------------------------------------------------------------------------
# F2 -- MIGRATE must refuse without a reviewed multi-member migration plan:
# the raw v1 manifest must have exactly one member.
# ---------------------------------------------------------------------------

BASE_LEGACY_MEMBER = dict(namespace="daily-shadow", stage="SHADOW", scope_key=H1,
                          metadata_fingerprint=H2, rule_fingerprint=H3, review_id="legacy-review-1",
                          reviewer="reviewer-1", approved_at=50, expires_at=400,
                          reviewed_through_seq=1, capability_proofs={})


def legacy_manifest_scenario(members, idx):
    manifest = dict(version=tr.LEGACY_V1_VERSION, reviews=members)
    manifest_raw = reader.canonical_bytes(manifest)
    manifest_hash = reader.sha256(manifest_raw)
    member = members[idx]
    member_hash = reader.sha256(reader.canonical_bytes(member))
    objects = {}
    candidate_approval_hash = put(objects, dict(kind="candidate-approval-migrate2"))
    rc = make_review_commission(objects, generation_id="generation-m2", review_id="new-review-m2",
                                candidate_approval_sha256=candidate_approval_hash)
    predecessor_evidence_hash = put(objects, dict(kind="predecessor-evidence-migrate2"))
    provenance_hash = provenance_for(objects, predecessor_evidence_sha256=predecessor_evidence_hash,
                                     review=rc["review"])
    commissioning_hash = put(objects, dict(kind="commissioning-approval-migrate2"))
    interval = interval_obj(objects, tag="interval-migrate2")
    core = dict(version=tr.EXT_VERSION, revision=1, previous_registry_sha256=manifest_hash,
                review_objects=[dict(review_id=rc["review"]["review_id"], review_sha256=rc["review_hash"])],
                active_selections=[rc["selection"]])
    old_selection = dict(kind="LEGACY_UNBOUND", manifest_sha256=manifest_hash, review_member_index=idx,
                         review_id=member["review_id"], canonical_member_sha256=member_hash)
    transition_obj = dict(
        version=tr.TRANSITION_VERSION, transition_id="transition-m2", request_sha256="8" * 64,
        operation="MIGRATE", reason="INITIAL_GENERATION", previous_revision=0,
        previous_registry_sha256=manifest_hash, next_revision=1,
        next_state_sha256=reader.sha256(reader.canonical_bytes(core)),
        semantic_key={k: rc["selection"][k] for k in reader.KEY},
        old_selection=old_selection, new_selection=rc["selection"],
        predecessor_evidence_sha256=predecessor_evidence_hash,
        successor_provenance_sha256=provenance_hash, candidate_approval_sha256=candidate_approval_hash,
        commissioning_approval_sha256=commissioning_hash, cutover_at=120, incomplete_interval=interval)
    index_bytes, _ = finalize(core, transition_obj, objects, transition_id="transition-m2")
    return dict(index_bytes=index_bytes, objects=objects, predecessor_bytes=manifest_raw,
                predecessor_object_bytes={})


def test_migrate_refuses_manifest_with_two_distinct_key_members():
    other = dict(BASE_LEGACY_MEMBER, review_id="legacy-review-OTHER", scope_key="e" * 64)
    scenario = legacy_manifest_scenario([BASE_LEGACY_MEMBER, other], 0)
    refused("LEGACY_MANIFEST_MUST_HAVE_SINGLE_MEMBER", scenario)


def test_migrate_refuses_manifest_with_duplicate_key_members():
    dup = dict(BASE_LEGACY_MEMBER, review_id="legacy-review-DUP")
    scenario = legacy_manifest_scenario([BASE_LEGACY_MEMBER, dup], 0)
    refused("LEGACY_MANIFEST_MUST_HAVE_SINGLE_MEMBER", scenario)


def test_migrate_accepts_single_member_manifest():
    scenario = legacy_manifest_scenario([BASE_LEGACY_MEMBER], 0)
    result = call(scenario)
    assert result.operation == "MIGRATE"


def test_legacy_member_index_bound():
    scenario = migrate_scenario()
    old = dict(scenario["transition_obj"]["old_selection"], review_member_index=1)
    transition_obj = dict(scenario["transition_obj"], old_selection=old)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("LEGACY_MEMBER_INDEX_BOUND", scenario, index_bytes=index_bytes)


def test_legacy_member_id_mismatch():
    scenario = migrate_scenario()
    old = dict(scenario["transition_obj"]["old_selection"], review_id="not-the-member")
    transition_obj = dict(scenario["transition_obj"], old_selection=old)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("LEGACY_MEMBER_ID_MISMATCH", scenario, index_bytes=index_bytes)


def test_legacy_member_hash_mismatch():
    scenario = migrate_scenario()
    old = dict(scenario["transition_obj"]["old_selection"], canonical_member_sha256="0" * 64)
    transition_obj = dict(scenario["transition_obj"], old_selection=old)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("LEGACY_MEMBER_HASH_MISMATCH", scenario, index_bytes=index_bytes)


def test_legacy_manifest_hash_mismatch():
    scenario = migrate_scenario()
    old = dict(scenario["transition_obj"]["old_selection"], manifest_sha256="0" * 64)
    transition_obj = dict(scenario["transition_obj"], old_selection=old)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("LEGACY_MANIFEST_HASH_MISMATCH", scenario, index_bytes=index_bytes)


def test_legacy_manifest_schema_wrong_version():
    s = migrate_scenario()
    bad_manifest = reader.canonical_bytes(dict(version="other", reviews=[]))
    bad_hash = reader.sha256(bad_manifest)
    core = dict(s["core"], previous_registry_sha256=bad_hash)
    old_selection = dict(s["transition_obj"]["old_selection"], manifest_sha256=bad_hash)
    transition_obj = dict(s["transition_obj"], previous_registry_sha256=bad_hash, old_selection=old_selection,
                          next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(s, core=core, transition_obj=transition_obj)
    refused("LEGACY_MANIFEST_SCHEMA", s, index_bytes=index_bytes, predecessor_bytes=bad_manifest)


def test_migrate_member_semantic_key_mismatch():
    s = migrate_scenario()
    bad_member = dict(namespace="daily-shadow", stage="SHADOW", scope_key="e" * 64,
                      metadata_fingerprint=H2, rule_fingerprint=H3, review_id="legacy-review-1",
                      reviewer="reviewer-1", approved_at=50, expires_at=400,
                      reviewed_through_seq=1, capability_proofs={})
    manifest = dict(version=tr.LEGACY_V1_VERSION, reviews=[bad_member])
    manifest_raw = reader.canonical_bytes(manifest)
    manifest_hash = reader.sha256(manifest_raw)
    member_hash = reader.sha256(reader.canonical_bytes(bad_member))
    core = dict(s["core"], previous_registry_sha256=manifest_hash)
    old_selection = dict(s["transition_obj"]["old_selection"], manifest_sha256=manifest_hash,
                        canonical_member_sha256=member_hash)
    transition_obj = dict(s["transition_obj"], previous_registry_sha256=manifest_hash,
                          old_selection=old_selection,
                          next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(s, core=core, transition_obj=transition_obj)
    refused("SEMANTIC_KEY_MISMATCH", s, index_bytes=index_bytes, predecessor_bytes=manifest_raw)


def test_migrate_must_establish_single_selection():
    s = migrate_scenario()
    objects = dict(s["objects"])
    ca = put(objects, dict(kind="extra-ca-migrate"))
    rc = make_review_commission(objects, generation_id="g-extra", review_id="extra-review",
                                scope_key="d" * 64, candidate_approval_sha256=ca)
    core = deepcopy(s["core"])
    core["review_objects"].append(dict(review_id="extra-review", review_sha256=rc["review_hash"]))
    core["active_selections"].append(rc["selection"])
    transition_obj = dict(s["transition_obj"], next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(s, core=core, transition_obj=transition_obj, objects=objects)
    refused("MIGRATE_MUST_ESTABLISH_SINGLE_SELECTION", s, index_bytes=index_bytes, object_bytes=objects)


def test_migrate_requires_old_selection():
    mig = migrate_scenario()
    transition_obj = dict(mig["transition_obj"], old_selection=None)
    index_bytes = rebuilt(mig, transition_obj=transition_obj)
    refused("OLD_SELECTION_REQUIRED", mig, index_bytes=index_bytes)


# ---------------------------------------------------------------------------
# F3 -- tests for refusals the mutation harness found undetected.
# ---------------------------------------------------------------------------

def tamper_predecessor(scenario, mutate_fn):
    """Rebuild ``scenario``'s predecessor index bytes via ``mutate_fn``,
    re-pointing the current index/transition's ``previous_registry_sha256``
    at the new bytes so the edit is reachable past the hash-binding checks."""
    pred = reader._parse(scenario["predecessor_bytes"], reader.MAX_INDEX_BYTES)
    pred = mutate_fn(dict(pred))
    pred_bytes = reader.canonical_bytes(pred)
    pred_hash = reader.sha256(pred_bytes)
    core = dict(scenario["core"], previous_registry_sha256=pred_hash)
    transition_obj = dict(scenario["transition_obj"], previous_registry_sha256=pred_hash,
                          next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(scenario, core=core, transition_obj=transition_obj)
    return index_bytes, pred_bytes


def test_predecessor_index_bound():
    act = activate_scenario()
    index_bytes, pred_bytes = tamper_predecessor(act, lambda p: dict(p, active_selections={}))
    refused("INDEX_BOUND", act, index_bytes=index_bytes, predecessor_bytes=pred_bytes)


def test_predecessor_review_id_mismatch():
    act = activate_scenario()

    def mutate(p):
        entries = list(p["review_objects"])
        entries[0] = dict(entries[0], review_id="renamed-review")
        return dict(p, review_objects=entries)

    index_bytes, pred_bytes = tamper_predecessor(act, mutate)
    refused("REVIEW_ID_MISMATCH", act, index_bytes=index_bytes, predecessor_bytes=pred_bytes)


def test_predecessor_active_review_context_mismatch():
    act = activate_scenario()

    def mutate(p):
        active = list(p["active_selections"])
        active[0] = dict(active[0], event_id="different-event")
        return dict(p, active_selections=active)

    index_bytes, pred_bytes = tamper_predecessor(act, mutate)
    refused("ACTIVE_REVIEW_CONTEXT_MISMATCH", act, index_bytes=index_bytes, predecessor_bytes=pred_bytes)


def test_predecessor_supersession_shape():
    act = activate_scenario()
    index_bytes, pred_bytes = tamper_predecessor(act, lambda p: dict(p, supersession_objects=[]))
    refused("SUPERSESSION_SHAPE", act, index_bytes=index_bytes, predecessor_bytes=pred_bytes)


def test_predecessor_revision_mismatch():
    act = activate_scenario()
    mig = act["base"]
    tampered_index = dict(reader._parse(mig["index_bytes"], reader.MAX_INDEX_BYTES), revision=2)
    tampered_bytes = reader.canonical_bytes(tampered_index)
    tampered_hash = reader.sha256(tampered_bytes)
    core = dict(act["core"], previous_registry_sha256=tampered_hash)
    transition_obj = dict(act["transition_obj"], previous_registry_sha256=tampered_hash,
                          next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(act, core=core, transition_obj=transition_obj)
    refused("PREDECESSOR_REVISION_MISMATCH", act, index_bytes=index_bytes, predecessor_bytes=tampered_bytes)


def test_commission_before_review():
    scenario = migrate_scenario()
    objects = dict(scenario["objects"])
    commission = dict(review_id=scenario["review"]["review_id"], review_sha256=scenario["review_hash"],
                      generation_id=scenario["review"]["generation_id"],
                      target_date=scenario["review"]["target_date"], event_id=scenario["review"]["event_id"],
                      generation_descriptor_sha256=scenario["review"]["generation_descriptor_sha256"],
                      candidate_approval_sha256=scenario["review"]["candidate_approval_sha256"],
                      commissioned_at=10)
    commission_raw = reader.canonical_bytes(commission)
    commission_hash = reader.sha256(commission_raw)
    objects[commission_hash] = commission_raw
    core = deepcopy(scenario["core"])
    core["active_selections"][0] = dict(core["active_selections"][0], commission_sha256=commission_hash)
    transition_obj = dict(scenario["transition_obj"], new_selection=core["active_selections"][0],
                          next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(scenario, core=core, transition_obj=transition_obj, objects=objects)
    refused("COMMISSION_BEFORE_REVIEW", scenario, index_bytes=index_bytes, object_bytes=objects)


def test_commission_binding_mismatch():
    scenario = migrate_scenario()
    objects = dict(scenario["objects"])
    commission = dict(review_id=scenario["review"]["review_id"], review_sha256=scenario["review_hash"],
                      generation_id="wrong-generation", target_date=scenario["review"]["target_date"],
                      event_id=scenario["review"]["event_id"],
                      generation_descriptor_sha256=scenario["review"]["generation_descriptor_sha256"],
                      candidate_approval_sha256=scenario["review"]["candidate_approval_sha256"],
                      commissioned_at=110)
    commission_raw = reader.canonical_bytes(commission)
    commission_hash = reader.sha256(commission_raw)
    objects[commission_hash] = commission_raw
    core = deepcopy(scenario["core"])
    core["active_selections"][0] = dict(core["active_selections"][0], commission_sha256=commission_hash)
    transition_obj = dict(scenario["transition_obj"], new_selection=core["active_selections"][0],
                          next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(scenario, core=core, transition_obj=transition_obj, objects=objects)
    refused("COMMISSION_BINDING_MISMATCH", scenario, index_bytes=index_bytes, object_bytes=objects)


def test_gate_old_selection_forged():
    scenario = gate_scenario()
    forged = dict(scenario["transition_obj"]["old_selection"]["selection"], commission_sha256="0" * 64)
    transition_obj = dict(scenario["transition_obj"], old_selection=dict(kind="V2", selection=forged))
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("OLD_SELECTION_FORGED", scenario, index_bytes=index_bytes)


def test_gate_selection_not_removed():
    scenario = gate_scenario()
    core = dict(scenario["core"],
                active_selections=list(scenario["core"]["active_selections"]) + [scenario["base"]["selection"]])
    transition_obj = dict(scenario["transition_obj"], next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(scenario, core=core, transition_obj=transition_obj)
    refused("GATE_SELECTION_NOT_REMOVED", scenario, index_bytes=index_bytes)


def test_review_history_rewritten_real_trigger_on_non_active_review():
    sup = supersede_scenario()
    act = sup["base"]
    core = deepcopy(sup["core"])
    core["review_objects"] = [e for e in core["review_objects"] if e["review_id"] != act["review"]["review_id"]]
    transition_obj = dict(sup["transition_obj"], next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(sup, core=core, transition_obj=transition_obj)
    refused("REVIEW_HISTORY_REWRITTEN", sup, index_bytes=index_bytes)


def prov_mutated(sup, **kw):
    objects = dict(sup["objects"])
    pe = sup["transition_obj"]["predecessor_evidence_sha256"]
    base = provenance_obj(predecessor_evidence_sha256=pe,
                          generation_descriptor_sha256=sup["review"]["generation_descriptor_sha256"])
    p = put(objects, dict(base, **kw))
    transition_obj = dict(sup["transition_obj"], successor_provenance_sha256=p)
    index_bytes = rebuilt(sup, transition_obj=transition_obj, objects=objects)
    return index_bytes, objects


def test_provenance_shape_extra_field():
    sup = supersede_scenario()
    index_bytes, objects = prov_mutated(sup, extra_field=1)
    refused("OBJECT_SCHEMA", sup, index_bytes=index_bytes, object_bytes=objects)


def test_provenance_unsupported_version():
    sup = supersede_scenario()
    index_bytes, objects = prov_mutated(sup, version="other")
    refused("UNSUPPORTED_PROVENANCE_VERSION", sup, index_bytes=index_bytes, object_bytes=objects)


def test_provenance_timezone():
    sup = supersede_scenario()
    index_bytes, objects = prov_mutated(sup, timezone="UTC")
    refused("PROVENANCE_TIMEZONE", sup, index_bytes=index_bytes, object_bytes=objects)


def test_provenance_release_commit_schema():
    sup = supersede_scenario()
    index_bytes, objects = prov_mutated(sup, release_commit="A" * 40)
    refused("PROVENANCE_RELEASE_COMMIT_SCHEMA", sup, index_bytes=index_bytes, object_bytes=objects)


def test_provenance_predecessor_mismatch():
    sup = supersede_scenario()
    index_bytes, objects = prov_mutated(sup, predecessor_evidence_sha256="0" * 64)
    refused("PROVENANCE_PREDECESSOR_MISMATCH", sup, index_bytes=index_bytes, object_bytes=objects)


def test_provenance_descriptor_mismatch():
    sup = supersede_scenario()
    index_bytes, objects = prov_mutated(sup, generation_descriptor_sha256="0" * 64)
    refused("PROVENANCE_DESCRIPTOR_MISMATCH", sup, index_bytes=index_bytes, object_bytes=objects)


@pytest.mark.parametrize("field", ["predecessor_evidence_sha256", "commissioning_approval_sha256"])
def test_dangling_approval_evidence_reference(field):
    sup = supersede_scenario()
    transition_obj = dict(sup["transition_obj"], **{field: "0" * 64})
    index_bytes = rebuilt(sup, transition_obj=transition_obj)
    refused("DANGLING_OBJECT", sup, index_bytes=index_bytes)


def test_dangling_incomplete_interval_evidence():
    sup = supersede_scenario()
    bad_interval = dict(sup["transition_obj"]["incomplete_interval"], evidence_sha256="0" * 64)
    transition_obj = dict(sup["transition_obj"], incomplete_interval=bad_interval)
    index_bytes = rebuilt(sup, transition_obj=transition_obj)
    refused("DANGLING_OBJECT", sup, index_bytes=index_bytes)


def test_old_selection_required_for_supersede():
    sup = supersede_scenario()
    transition_obj = dict(sup["transition_obj"], old_selection=None)
    index_bytes = rebuilt(sup, transition_obj=transition_obj)
    refused("OLD_SELECTION_REQUIRED", sup, index_bytes=index_bytes)


def test_old_selection_schema_missing_kind():
    sup = supersede_scenario()
    transition_obj = dict(sup["transition_obj"], old_selection={"selection": sup["old_selection"]})
    index_bytes = rebuilt(sup, transition_obj=transition_obj)
    refused("OLD_SELECTION_SCHEMA", sup, index_bytes=index_bytes)


def test_old_selection_kind_invalid_v2_for_migrate():
    mig = migrate_scenario()
    transition_obj = dict(mig["transition_obj"], old_selection=dict(kind="V2", selection=mig["selection"]))
    index_bytes = rebuilt(mig, transition_obj=transition_obj)
    refused("OLD_SELECTION_KIND_INVALID", mig, index_bytes=index_bytes)


def test_old_selection_kind_invalid_legacy_for_non_migrate():
    sup = supersede_scenario()
    legacy_old = dict(kind="LEGACY_UNBOUND", manifest_sha256="0" * 64, review_member_index=0,
                      review_id="x", canonical_member_sha256="0" * 64)
    transition_obj = dict(sup["transition_obj"], old_selection=legacy_old)
    index_bytes = rebuilt(sup, transition_obj=transition_obj)
    refused("OLD_SELECTION_KIND_INVALID", sup, index_bytes=index_bytes)


def test_old_selection_kind_unknown():
    sup = supersede_scenario()
    transition_obj = dict(sup["transition_obj"], old_selection=dict(kind="BOGUS"))
    index_bytes = rebuilt(sup, transition_obj=transition_obj)
    refused("OLD_SELECTION_KIND_UNKNOWN", sup, index_bytes=index_bytes)


def test_new_selection_required():
    sup = supersede_scenario()
    transition_obj = dict(sup["transition_obj"], new_selection=None)
    index_bytes = rebuilt(sup, transition_obj=transition_obj)
    refused("NEW_SELECTION_REQUIRED", sup, index_bytes=index_bytes)


def test_new_selection_forbidden_for_gate():
    g = gate_scenario()
    transition_obj = dict(g["transition_obj"], new_selection=g["base"]["selection"])
    index_bytes = rebuilt(g, transition_obj=transition_obj)
    refused("NEW_SELECTION_FORBIDDEN_FOR_GATE", g, index_bytes=index_bytes)


def test_transition_id_mismatch():
    sup = supersede_scenario()
    index = reader._parse(sup["index_bytes"], reader.MAX_INDEX_BYTES)
    index = dict(index, supersession_objects=[dict(
        transition_id="other-id", transition_sha256=index["supersession_objects"][0]["transition_sha256"])])
    index_bytes = reader.canonical_bytes(index)
    refused("TRANSITION_ID_MISMATCH", sup, index_bytes=index_bytes)


def test_unknown_operation_string():
    scenario = migrate_scenario()
    transition_obj = dict(scenario["transition_obj"], operation="ROLLBACK")
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("UNKNOWN_OPERATION", scenario, index_bytes=index_bytes)


def test_unknown_reason_string():
    scenario = migrate_scenario()
    transition_obj = dict(scenario["transition_obj"], reason="BOGUS_REASON")
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("UNKNOWN_REASON", scenario, index_bytes=index_bytes)


def test_semantic_key_unsupported_stage():
    sup = supersede_scenario()
    bad_key = dict(sup["transition_obj"]["semantic_key"], stage="BOGUS_STAGE")
    transition_obj = dict(sup["transition_obj"], semantic_key=bad_key)
    index_bytes = rebuilt(sup, transition_obj=transition_obj)
    refused("UNSUPPORTED_STAGE", sup, index_bytes=index_bytes)


def test_semantic_key_malformed_hash_field():
    scenario = migrate_scenario()
    bad_key = dict(scenario["transition_obj"]["semantic_key"], scope_key="not-a-hash")
    transition_obj = dict(scenario["transition_obj"], semantic_key=bad_key)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("STRING_SCHEMA", scenario, index_bytes=index_bytes)


def test_transition_hash_field_malformed():
    scenario = migrate_scenario()
    transition_obj = dict(scenario["transition_obj"], request_sha256="not-a-hash")
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("STRING_SCHEMA", scenario, index_bytes=index_bytes)


def test_cutover_at_not_integer():
    scenario = migrate_scenario()
    transition_obj = dict(scenario["transition_obj"], cutover_at="120")
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("INTEGER_SCHEMA", scenario, index_bytes=index_bytes)


def test_incomplete_interval_shape_missing_field():
    scenario = migrate_scenario()
    bad_interval = dict(scenario["transition_obj"]["incomplete_interval"])
    del bad_interval["status"]
    transition_obj = dict(scenario["transition_obj"], incomplete_interval=bad_interval)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("OBJECT_SCHEMA", scenario, index_bytes=index_bytes)


def test_metadata_aggregate_bytes_bound():
    scenario = migrate_scenario()
    objects = dict(scenario["objects"])
    digest = put(objects, dict(kind="agg-tail"))
    padding = "p" * 260000
    for i in range(70):
        raw = reader.canonical_bytes(dict(kind="agg-link", i=i, next=digest, pad=padding))
        while len(raw) > reader.MAX_OBJECT_BYTES:
            padding = padding[:-1000]
            raw = reader.canonical_bytes(dict(kind="agg-link", i=i, next=digest, pad=padding))
        digest = reader.sha256(raw)
        objects[digest] = raw
    transition_obj = dict(scenario["transition_obj"], predecessor_evidence_sha256=digest)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj, objects=objects)
    refused("METADATA_GRAPH_BOUND", scenario, index_bytes=index_bytes, object_bytes=objects)


# ---------------------------------------------------------------------------
# F4 -- typed exceptions: no RecursionError/TypeError escapes malformed
# (but otherwise canonical, size-bounded) input.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [["SUPERSEDE"], {"a": 1}, 123, None])
def test_operation_non_string_refuses_typed(value):
    sup = supersede_scenario()
    transition_obj = dict(sup["transition_obj"], operation=value)
    index_bytes = rebuilt(sup, transition_obj=transition_obj)
    refused("UNKNOWN_OPERATION", sup, index_bytes=index_bytes)


@pytest.mark.parametrize("value", [["FAIL_CLOSED"], {"a": 1}, 123, None])
def test_reason_non_string_refuses_typed(value):
    sup = supersede_scenario()
    transition_obj = dict(sup["transition_obj"], reason=value)
    index_bytes = rebuilt(sup, transition_obj=transition_obj)
    refused("UNKNOWN_REASON", sup, index_bytes=index_bytes)


@pytest.mark.parametrize("value", [[], {"a": 1}, 123, None])
def test_interval_status_non_string_refuses_typed(value):
    sup = supersede_scenario()
    bad_interval = dict(sup["transition_obj"]["incomplete_interval"], status=value)
    transition_obj = dict(sup["transition_obj"], incomplete_interval=bad_interval)
    index_bytes = rebuilt(sup, transition_obj=transition_obj)
    refused("INCOMPLETE_INTERVAL_STATUS", sup, index_bytes=index_bytes)


def test_provenance_release_commit_non_string_refuses_typed():
    sup = supersede_scenario()
    index_bytes, objects = prov_mutated(sup, release_commit=123)
    refused("PROVENANCE_RELEASE_COMMIT_SCHEMA", sup, index_bytes=index_bytes, object_bytes=objects)


def test_deeply_nested_commissioning_object_does_not_recursion_error():
    sup = supersede_scenario()
    objects = dict(sup["objects"])
    depth = 4000
    raw = b"[" * depth + b'"x"' + b"]" * depth
    digest = reader.sha256(raw)
    objects[digest] = raw
    transition_obj = dict(sup["transition_obj"], commissioning_approval_sha256=digest)
    index_bytes = rebuilt(sup, transition_obj=transition_obj, objects=objects)
    result = tr.verify_transition(index_bytes=index_bytes, object_bytes=objects,
                                  predecessor_bytes=sup["predecessor_bytes"],
                                  predecessor_object_bytes=sup["predecessor_object_bytes"])
    assert result.operation == "SUPERSEDE"


# ---------------------------------------------------------------------------
# F5 -- cutover_at must fall within [max(approved_at, commissioned_at),
# expires_at) for the new selection; there is no `now` parameter.
# ---------------------------------------------------------------------------

def test_cutover_before_approval_and_commission():
    scenario = migrate_scenario()
    transition_obj = dict(scenario["transition_obj"], cutover_at=50)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("CUTOVER_TIME_ORDER", scenario, index_bytes=index_bytes)


def test_cutover_at_or_after_expiry():
    scenario = migrate_scenario()
    transition_obj = dict(scenario["transition_obj"], cutover_at=500)
    index_bytes = rebuilt(scenario, transition_obj=transition_obj)
    refused("CUTOVER_TIME_ORDER", scenario, index_bytes=index_bytes)


# ---------------------------------------------------------------------------
# F7 -- sparse-seed supersession requires a distinct descriptor, not just a
# distinct generation_id.
# ---------------------------------------------------------------------------

def test_sparse_seed_requires_new_generation_and_descriptor():
    sup = supersede_scenario()
    # new-review-3's generation_id ("generation-3") already differs from the
    # existing selection's ("generation-2"); only the descriptor is still
    # identical ("4" * 64 for every fixture review), so this isolates the
    # descriptor half of the F7 fix.
    transition_obj = dict(sup["transition_obj"], reason="SPARSE_SEED_NEW_GENERATION")
    index_bytes = rebuilt(sup, transition_obj=transition_obj)
    refused("SPARSE_SEED_REQUIRES_NEW_GENERATION", sup, index_bytes=index_bytes)


def test_sparse_seed_requires_new_generation_same_generation_id():
    sup = supersede_scenario()
    objects = dict(sup["objects"])
    ca = sup["transition_obj"]["candidate_approval_sha256"]
    rc = make_review_commission(objects, generation_id=sup["base"]["selection"]["generation_id"],
                                review_id="new-review-3b", scope_key=sup["selection"]["scope_key"],
                                candidate_approval_sha256=ca)
    core = deepcopy(sup["core"])
    core["review_objects"][-1] = dict(review_id="new-review-3b", review_sha256=rc["review_hash"])
    core["active_selections"][-1] = rc["selection"]
    transition_obj = dict(sup["transition_obj"], reason="SPARSE_SEED_NEW_GENERATION",
                          new_selection=rc["selection"],
                          next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(sup, core=core, transition_obj=transition_obj, objects=objects)
    refused("SPARSE_SEED_REQUIRES_NEW_GENERATION", sup, index_bytes=index_bytes, object_bytes=objects)


def test_sparse_seed_with_new_generation_and_descriptor_accepted():
    sup = supersede_scenario()
    objects = dict(sup["objects"])
    ca = put(objects, dict(kind="ca-sparse-ok"))
    rc = make_review_commission(objects, generation_id="generation-sparse-ok",
                                review_id="new-review-sparse-ok", scope_key=sup["selection"]["scope_key"],
                                candidate_approval_sha256=ca, generation_descriptor_sha256="9" * 64)
    predecessor_evidence_hash = put(objects, dict(kind="pe-sparse-ok"))
    provenance_hash = provenance_for(objects, predecessor_evidence_sha256=predecessor_evidence_hash,
                                     review=rc["review"])
    commissioning_hash = put(objects, dict(kind="ca-commissioning-sparse-ok"))
    interval = interval_obj(objects, tag="iv-sparse-ok")
    core = deepcopy(sup["core"])
    core["review_objects"][-1] = dict(review_id="new-review-sparse-ok", review_sha256=rc["review_hash"])
    core["active_selections"][-1] = rc["selection"]
    transition_obj = dict(sup["transition_obj"], reason="SPARSE_SEED_NEW_GENERATION",
                          new_selection=rc["selection"], predecessor_evidence_sha256=predecessor_evidence_hash,
                          successor_provenance_sha256=provenance_hash, candidate_approval_sha256=ca,
                          commissioning_approval_sha256=commissioning_hash, incomplete_interval=interval,
                          next_state_sha256=reader.sha256(reader.canonical_bytes(core)))
    index_bytes = rebuilt(sup, core=core, transition_obj=transition_obj, objects=objects)
    result = tr.verify_transition(index_bytes=index_bytes, object_bytes=objects,
                                  predecessor_bytes=sup["predecessor_bytes"],
                                  predecessor_object_bytes=sup["predecessor_object_bytes"])
    assert result.operation == "SUPERSEDE"
