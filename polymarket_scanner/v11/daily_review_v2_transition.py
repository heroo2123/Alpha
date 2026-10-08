"""Pure, bounded verifier for a proposed v2 supersession transition index.

This module is a separate wire format and a separate API from
``daily_review_v2``. It does not modify that module and does not change the
five-key v2 index or the legacy v1 refusal in any way; both remain exactly as
reviewed. This module imports ``daily_review_v2``'s existing private
primitives (``_shape``, ``_hash``, ``_string``, ``_int``, ``_day``, ``_object``,
``_parse``, ``_context``, ``_review``, ``canonical_bytes``, ``sha256``) rather
than reimplementing canonicalization, hashing or schema primitives a second
time; only the index/transition orchestration logic, which has a different
exact key set and therefore cannot reuse ``_validate_index`` directly, is
duplicated here.

Like ``daily_review_v2.select_active_review``, every function here is pure:
no file is opened, no root custody is established, and no return value is an
admission, certification or authorization decision. ``verify_transition``
returns a ``VerifiedTransition`` pin that records internal graph consistency
only. It carries no "authorized"/"admitted"/"approved" field: the absence of
such a field is deliberate, so a caller cannot mistake a successful return for
a PASS. Approval and provenance *objects* referenced by a transition are
resolved, size-bounded and hash-verified here, but their issuer/custody/policy
authenticity is explicitly out of scope; see
docs/v11-daily-review-v2-transition-reader-contract.md.
"""
from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Mapping, Optional

from . import daily_review_v2 as reader


EXT_VERSION = "alpha_v11_certification_reviews_v2_transition_1"
TRANSITION_VERSION = "alpha_v11_daily_transition_v1"
PROVENANCE_VERSION = "alpha_v11_daily_generation_provenance_v1"
LEGACY_V1_VERSION = "alpha_v11_certification_reviews_v1"

MAX_METADATA_OBJECTS = 256
MAX_METADATA_AGGREGATE_BYTES = 16 * 1024 * 1024
GIT_SHA = re.compile(r"[0-9a-f]{40}\Z")

OPERATIONS = frozenset(("MIGRATE", "ACTIVATE", "SUPERSEDE", "GATE"))
REASONS = frozenset(("INITIAL_GENERATION", "SPARSE_SEED_NEW_GENERATION",
                     "REVIEWED_RESELECTION", "FAIL_CLOSED"))
PAIRING = {
    "ACTIVATE": frozenset(("INITIAL_GENERATION",)),
    "SUPERSEDE": frozenset(("SPARSE_SEED_NEW_GENERATION", "REVIEWED_RESELECTION")),
    "GATE": frozenset(("FAIL_CLOSED",)),
    "MIGRATE": frozenset(("INITIAL_GENERATION", "SPARSE_SEED_NEW_GENERATION")),
}

CORE_KEYS = ("version", "revision", "previous_registry_sha256",
             "review_objects", "active_selections")
EXT_INDEX_KEYS = CORE_KEYS + ("supersession_objects",)

TRANSITION = ("version", "transition_id", "request_sha256", "operation", "reason",
              "previous_revision", "previous_registry_sha256", "next_revision",
              "next_state_sha256", "semantic_key", "old_selection", "new_selection",
              "predecessor_evidence_sha256", "successor_provenance_sha256",
              "candidate_approval_sha256", "commissioning_approval_sha256",
              "cutover_at", "incomplete_interval")
OLD_SELECTION_V2 = ("kind", "selection")
OLD_SELECTION_LEGACY = ("kind", "manifest_sha256", "review_member_index",
                        "review_id", "canonical_member_sha256")
INCOMPLETE_INTERVAL = ("start_at", "end_at", "status", "evidence_sha256")
INTERVAL_STATUS = frozenset(("INCOMPLETE", "NOT_APPLICABLE"))
PROVENANCE = ("version", "generation_descriptor_sha256", "predecessor_evidence_sha256",
              "genesis_import_sha256", "seed_sha256", "seed_count", "release_commit",
              "runner_sha256", "config_sha256", "import_closure_sha256",
              "scheduler_identity", "audit_identity", "coordinator_identity", "timezone")


def _deny(code: str):
    raise reader.ReviewV2Error(code)


def _leaf_strings(value):
    """Iterative (non-recursive) leaf-string walk: a canonical, hash-verified
    object of bounded byte size can still nest thousands of levels deep, and
    a recursive walk over that nesting raises an uncaught RecursionError
    before any typed refusal is reached. An explicit stack has no such
    depth-dependent failure mode."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
        elif isinstance(item, str):
            yield item


def _sorted_selections(entries):
    return sorted(entries, key=reader.canonical_bytes)


def _find_by_key(active_list, key_tuple):
    matches = [entry for entry in active_list
               if tuple(entry[key] for key in reader.KEY) == key_tuple]
    if len(matches) > 1:
        _deny("DUPLICATE_ACTIVE_SEMANTIC_KEY")
    return matches[0] if matches else None


def _validate_supersession_shape(value):
    if type(value) is not list or len(value) != 1:
        _deny("SUPERSESSION_SHAPE")
    entry = value[0]
    reader._shape(entry, ("transition_id", "transition_sha256"))
    reader._string(entry["transition_id"])
    reader._hash(entry["transition_sha256"])
    return entry


def _validate_registry(index, objects, *, expect_version):
    """Validate the five core v2 fields; duplicated from ``_validate_index``
    because its exact-shape check forbids the extra ``supersession_objects``
    key and its version constant is the unextended one. All lower-level work
    (hashing, canonicalization, per-object schema) calls back into the
    reviewed primitives in ``daily_review_v2`` unchanged."""
    reader._shape(index, CORE_KEYS)
    if index["version"] != expect_version:
        _deny("UNSUPPORTED_VERSION")
    reader._int(index["revision"], minimum=1)
    reader._hash(index["previous_registry_sha256"])
    entries = index["review_objects"]
    active = index["active_selections"]
    if (type(entries) is not list or type(active) is not list
            or len(entries) > reader.MAX_REVIEWS or len(active) > reader.MAX_REVIEWS):
        _deny("INDEX_BOUND")
    by_id = {}
    for entry in entries:
        reader._shape(entry, ("review_id", "review_sha256"))
        rid = reader._string(entry["review_id"])
        digest = reader._hash(entry["review_sha256"])
        if rid in by_id:
            _deny("DUPLICATE_REVIEW_ID")
        review = reader._object(objects, digest)
        reader._review(review)
        if review["review_id"] != rid:
            _deny("REVIEW_ID_MISMATCH")
        by_id[rid] = (digest, review)
    seen = set()
    for selection in active:
        reader._shape(selection, reader.SELECTION)
        reader._context(selection)
        rid = reader._string(selection["review_id"])
        digest = reader._hash(selection["review_sha256"])
        reader._hash(selection["commission_sha256"])
        semantic = tuple(selection[key] for key in reader.KEY)
        if semantic in seen:
            _deny("DUPLICATE_ACTIVE_SEMANTIC_KEY")
        seen.add(semantic)
        if rid not in by_id or by_id[rid][0] != digest:
            _deny("DANGLING_ACTIVE_REVIEW")
        review = by_id[rid][1]
        if any(review[key] != selection[key] for key in reader.CONTEXT + ("review_id",)):
            _deny("ACTIVE_REVIEW_CONTEXT_MISMATCH")
        commission = reader._object(objects, selection["commission_sha256"])
        reader._shape(commission, reader.COMMISSION)
        for key in ("review_id", "generation_id", "event_id"):
            reader._string(commission[key])
        reader._day(commission["target_date"])
        for key in ("review_sha256", "generation_descriptor_sha256", "candidate_approval_sha256"):
            reader._hash(commission[key])
        reader._int(commission["commissioned_at"])
        if commission["commissioned_at"] < review["approved_at"]:
            _deny("COMMISSION_BEFORE_REVIEW")
        expected = {key: selection[key] for key in
                    ("review_id", "review_sha256", "generation_id", "target_date", "event_id")}
        expected.update({key: review[key] for key in
                         ("generation_descriptor_sha256", "candidate_approval_sha256")})
        if any(commission[key] != value for key, value in expected.items()):
            _deny("COMMISSION_BINDING_MISMATCH")
    return by_id, active


def _walk_metadata(digest, objects, budget, path=()):
    """Resolve one bounded metadata object and follow any nested hash-shaped
    string that is itself a key in ``objects``, with an explicit ancestor-path
    cycle guard and an aggregate-size bound. A digest reachable from two
    different, unrelated branches (a legitimate shared/diamond reference,
    such as a provenance object's own ``predecessor_evidence_sha256`` field
    pointing at the same evidence the transition already names) is memoized,
    not refused; only an actual cycle -- the same digest reappearing among
    its own ancestors -- refuses. A true forward-hash cycle (digest A's bytes
    containing digest B, and B's bytes containing A) cannot be constructed by
    forward hashing in the first place, so this guard is defense-in-depth,
    not something a synthetic fixture can trigger. Opaque content (no
    reviewed schema) is only resolved, bounded and hash-verified here, never
    semantically authenticated."""
    if digest in path:
        _deny("CIRCULAR_EVIDENCE")
    if digest in budget["resolved"]:
        return budget["resolved"][digest]
    budget["count"] += 1
    if budget["count"] > MAX_METADATA_OBJECTS:
        _deny("METADATA_GRAPH_BOUND")
    value = reader._object(objects, digest)
    budget["bytes"] += len(objects[digest])
    if budget["bytes"] > MAX_METADATA_AGGREGATE_BYTES:
        _deny("METADATA_GRAPH_BOUND")
    budget["resolved"][digest] = value
    for leaf in _leaf_strings(value):
        if leaf != digest and reader.HEX.fullmatch(leaf) and leaf in objects:
            _walk_metadata(leaf, objects, budget, path + (digest,))
    return value


def _parse_legacy_manifest(raw: bytes):
    reader._bounded_bytes(raw, reader.MAX_INDEX_BYTES)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise reader.ReviewV2Error("LEGACY_MANIFEST_INVALID_JSON") from exc
    if type(value) is not dict or value.get("version") != LEGACY_V1_VERSION:
        _deny("LEGACY_MANIFEST_SCHEMA")
    return value


def _validate_semantic_key(value):
    reader._shape(value, reader.KEY)
    reader._string(value["namespace"])
    reader._string(value["stage"])
    if value["stage"] not in reader.STAGES:
        _deny("UNSUPPORTED_STAGE")
    for key in ("scope_key", "metadata_fingerprint", "rule_fingerprint"):
        reader._hash(value[key])
    return tuple(value[key] for key in reader.KEY)


def _validate_old_selection(value, operation):
    if value is None:
        if operation != "ACTIVATE":
            _deny("OLD_SELECTION_REQUIRED")
        return None
    if type(value) is not dict or "kind" not in value:
        _deny("OLD_SELECTION_SCHEMA")
    kind = value["kind"]
    if kind == "V2":
        if operation == "ACTIVATE":
            _deny("OLD_SELECTION_FORBIDDEN_FOR_ACTIVATE")
        if operation == "MIGRATE":
            _deny("OLD_SELECTION_KIND_INVALID")
        reader._shape(value, OLD_SELECTION_V2)
        selection = value["selection"]
        reader._shape(selection, reader.SELECTION)
        reader._context(selection)
        reader._string(selection["review_id"])
        reader._hash(selection["review_sha256"])
        reader._hash(selection["commission_sha256"])
        return ("V2", selection)
    if kind == "LEGACY_UNBOUND":
        if operation != "MIGRATE":
            _deny("OLD_SELECTION_KIND_INVALID")
        reader._shape(value, OLD_SELECTION_LEGACY)
        reader._hash(value["manifest_sha256"])
        reader._int(value["review_member_index"], minimum=0)
        reader._string(value["review_id"])
        reader._hash(value["canonical_member_sha256"])
        return ("LEGACY_UNBOUND", value)
    _deny("OLD_SELECTION_KIND_UNKNOWN")


def _validate_new_selection(value, operation):
    if value is None:
        if operation != "GATE":
            _deny("NEW_SELECTION_REQUIRED")
        return None
    if operation == "GATE":
        _deny("NEW_SELECTION_FORBIDDEN_FOR_GATE")
    reader._shape(value, reader.SELECTION)
    reader._context(value)
    reader._string(value["review_id"])
    reader._hash(value["review_sha256"])
    reader._hash(value["commission_sha256"])
    return value


@dataclass(frozen=True)
class VerifiedTransition:
    """A non-authorizing pin: internal graph consistency only. No field here
    grants admission, approval authenticity or custody. ``review_id``,
    ``review_sha256``, ``commission_sha256`` and ``candidate_approval_sha256``
    are the new selection pin for this edge; they are ``None`` for ``GATE``
    (which has no new selection). ``commissioning_subject_sha256`` is the
    digest of the transition content the commissioning approval is expected
    to attest to (the transition with only its own
    ``commissioning_approval_sha256`` field omitted) -- never a claim that
    any approval was actually verified against it."""
    revision: int
    registry_sha256: str
    previous_registry_sha256: str
    next_state_sha256: str
    transition_id: str
    transition_sha256: str
    operation: str
    reason: str
    semantic_key: tuple
    cutover_at: int
    review_id: Optional[str]
    review_sha256: Optional[str]
    commission_sha256: Optional[str]
    candidate_approval_sha256: Optional[str]
    commissioning_subject_sha256: str


def verify_transition(*, index_bytes: bytes, object_bytes: Mapping[str, bytes],
                       predecessor_bytes: bytes,
                       predecessor_object_bytes: Mapping[str, bytes]) -> VerifiedTransition:
    """Validate one transition edge from an explicitly supplied, already
    trusted predecessor to the supplied new index. This validates exactly one
    edge; it does not walk or re-verify the predecessor's own transition.

    There is no caller-supplied current time: no freshness/clock check is
    performed here (that remains a future publisher's responsibility). The
    only time-ordering guarantee this module makes is content-internal --
    ``cutover_at`` for the new selection, if any, must fall within
    ``[max(approved_at, commissioned_at), expires_at)``."""
    if type(object_bytes) is not dict or len(object_bytes) > reader.MAX_REVIEWS * 2:
        _deny("OBJECT_MAP_BOUND")
    if (type(predecessor_object_bytes) is not dict
            or len(predecessor_object_bytes) > reader.MAX_REVIEWS * 2):
        _deny("OBJECT_MAP_BOUND")

    index = reader._parse(index_bytes, reader.MAX_INDEX_BYTES)
    reader._shape(index, EXT_INDEX_KEYS)
    supersession_entry = _validate_supersession_shape(index["supersession_objects"])
    core = {key: index[key] for key in CORE_KEYS}
    by_id, active_selections = _validate_registry(core, object_bytes, expect_version=EXT_VERSION)
    registry_sha256 = reader.sha256(index_bytes)
    next_state_sha256 = reader.sha256(reader.canonical_bytes(core))

    budget = {"count": 0, "bytes": 0, "resolved": {}}
    transition_sha256 = supersession_entry["transition_sha256"]
    transition = _walk_metadata(transition_sha256, object_bytes, budget)
    reader._shape(transition, TRANSITION)
    if transition["version"] != TRANSITION_VERSION:
        _deny("UNSUPPORTED_TRANSITION_VERSION")
    if transition["transition_id"] != supersession_entry["transition_id"]:
        _deny("TRANSITION_ID_MISMATCH")

    for field in ("request_sha256", "previous_registry_sha256", "next_state_sha256",
                  "predecessor_evidence_sha256", "commissioning_approval_sha256"):
        reader._hash(transition[field])
    for field in ("successor_provenance_sha256", "candidate_approval_sha256"):
        if transition[field] is not None:
            reader._hash(transition[field])

    operation = transition["operation"]
    reason = transition["reason"]
    if type(operation) is not str or operation not in OPERATIONS:
        _deny("UNKNOWN_OPERATION")
    if type(reason) is not str or reason not in REASONS:
        _deny("UNKNOWN_REASON")
    if reason not in PAIRING[operation]:
        _deny("OPERATION_REASON_PAIRING")

    previous_revision = reader._int(transition["previous_revision"], minimum=0)
    next_revision = reader._int(transition["next_revision"], minimum=1)
    if next_revision != previous_revision + 1 or next_revision != index["revision"]:
        _deny("REVISION_SEQUENCE_MISMATCH")
    if operation == "MIGRATE":
        if previous_revision != 0 or next_revision != 1:
            _deny("MIGRATE_REVISION_FIXED")
    elif previous_revision < 1:
        _deny("NON_MIGRATE_REQUIRES_EXTENDED_PREDECESSOR")

    if transition["previous_registry_sha256"] != index["previous_registry_sha256"]:
        _deny("PREDECESSOR_HASH_MISMATCH")
    if transition["previous_registry_sha256"] != reader.sha256(predecessor_bytes):
        _deny("PREDECESSOR_HASH_MISMATCH")
    if transition["next_state_sha256"] != next_state_sha256:
        _deny("CORE_HASH_MISMATCH")

    semantic_key_tuple = _validate_semantic_key(transition["semantic_key"])
    old_selection = _validate_old_selection(transition["old_selection"], operation)
    new_selection = _validate_new_selection(transition["new_selection"], operation)
    if new_selection is not None and tuple(new_selection[key] for key in reader.KEY) != semantic_key_tuple:
        _deny("SEMANTIC_KEY_MISMATCH")

    if operation == "MIGRATE":
        manifest = _parse_legacy_manifest(predecessor_bytes)
        kind, legacy = old_selection
        if legacy["manifest_sha256"] != reader.sha256(predecessor_bytes):
            _deny("LEGACY_MANIFEST_HASH_MISMATCH")
        members = manifest.get("reviews")
        if type(members) is not list or len(members) != 1:
            _deny("LEGACY_MANIFEST_MUST_HAVE_SINGLE_MEMBER")
        index_in_manifest = legacy["review_member_index"]
        if not 0 <= index_in_manifest < len(members):
            _deny("LEGACY_MEMBER_INDEX_BOUND")
        member = members[index_in_manifest]
        if type(member) is not dict or member.get("review_id") != legacy["review_id"]:
            _deny("LEGACY_MEMBER_ID_MISMATCH" if type(member) is dict else "LEGACY_MEMBER_SCHEMA")
        if reader.sha256(reader.canonical_bytes(member)) != legacy["canonical_member_sha256"]:
            _deny("LEGACY_MEMBER_HASH_MISMATCH")
        if tuple(member.get(key) for key in reader.KEY) != semantic_key_tuple:
            _deny("SEMANTIC_KEY_MISMATCH")
        current_at_key = _find_by_key(active_selections, semantic_key_tuple)
        if (current_at_key != new_selection or len(active_selections) != 1 or len(by_id) != 1):
            _deny("MIGRATE_MUST_ESTABLISH_SINGLE_SELECTION")
    else:
        predecessor_index = reader._parse(predecessor_bytes, reader.MAX_INDEX_BYTES)
        reader._shape(predecessor_index, EXT_INDEX_KEYS)
        _validate_supersession_shape(predecessor_index["supersession_objects"])
        predecessor_core = {key: predecessor_index[key] for key in CORE_KEYS}
        predecessor_by_id, predecessor_active = _validate_registry(
            predecessor_core, predecessor_object_bytes, expect_version=EXT_VERSION)
        if predecessor_index["revision"] != previous_revision:
            _deny("PREDECESSOR_REVISION_MISMATCH")

        existing = _find_by_key(predecessor_active, semantic_key_tuple)
        if operation == "ACTIVATE":
            if existing is not None:
                _deny("ACTIVATE_REQUIRES_NO_PRIOR_SELECTION")
        elif operation == "SUPERSEDE":
            if existing is None or old_selection is None or old_selection[0] != "V2":
                _deny("SUPERSEDE_REQUIRES_PRIOR_SELECTION")
            if old_selection[1] != existing:
                _deny("OLD_SELECTION_FORGED")
            if (new_selection["review_id"], new_selection["review_sha256"]) == (
                    existing["review_id"], existing["review_sha256"]):
                _deny("SUPERSEDE_REQUIRES_CHANGE")
            if reason == "SPARSE_SEED_NEW_GENERATION":
                existing_review = predecessor_by_id[existing["review_id"]][1]
                new_review_obj = by_id[new_selection["review_id"]][1]
                if (new_selection["generation_id"] == existing["generation_id"]
                        or new_review_obj["generation_descriptor_sha256"]
                        == existing_review["generation_descriptor_sha256"]):
                    _deny("SPARSE_SEED_REQUIRES_NEW_GENERATION")
        elif operation == "GATE":
            if existing is None or old_selection is None or old_selection[0] != "V2":
                _deny("GATE_REQUIRES_PRIOR_SELECTION")
            if old_selection[1] != existing:
                _deny("OLD_SELECTION_FORGED")

        if new_selection is not None and (
                new_selection["review_id"] in predecessor_by_id
                or new_selection["review_sha256"] in
                {digest for digest, _review in predecessor_by_id.values()}):
            # A review that already has a history entry at this predecessor
            # -- whether still active, superseded, or GATE-removed -- cannot
            # become a fresh candidate again with its original commission
            # and approval. Rollback/reselection needs a new monotonic
            # review, not an automatic resumption of a historical one. The
            # degenerate same-review no-op (new_selection already equal to
            # the still-current selection) is caught above by
            # SUPERSEDE_REQUIRES_CHANGE instead, which is the more specific
            # diagnosis for that particular case.
            _deny("HISTORICAL_REVIEW_REUSE")

        for rid, (digest, _rev) in predecessor_by_id.items():
            if rid not in by_id or by_id[rid][0] != digest:
                _deny("REVIEW_HISTORY_REWRITTEN")
        predecessor_others = [entry for entry in predecessor_active
                               if tuple(entry[key] for key in reader.KEY) != semantic_key_tuple]
        current_others = [entry for entry in active_selections
                           if tuple(entry[key] for key in reader.KEY) != semantic_key_tuple]
        if _sorted_selections(predecessor_others) != _sorted_selections(current_others):
            _deny("UNRELATED_SELECTION_CHANGED")
        current_at_key = _find_by_key(active_selections, semantic_key_tuple)
        if operation == "GATE":
            if current_at_key is not None:
                _deny("GATE_SELECTION_NOT_REMOVED")
        elif current_at_key != new_selection:
            _deny("NEW_SELECTION_NOT_PUBLISHED")

    predecessor_evidence_digest = transition["predecessor_evidence_sha256"]
    _walk_metadata(predecessor_evidence_digest, object_bytes, budget)

    successor_provenance_digest = transition["successor_provenance_sha256"]
    if successor_provenance_digest is None:
        if operation != "GATE":
            _deny("SUCCESSOR_PROVENANCE_REQUIRED")
    else:
        provenance = _walk_metadata(successor_provenance_digest, object_bytes, budget)
        reader._shape(provenance, PROVENANCE)
        if provenance["version"] != PROVENANCE_VERSION:
            _deny("UNSUPPORTED_PROVENANCE_VERSION")
        if provenance["timezone"] != "America/New_York":
            _deny("PROVENANCE_TIMEZONE")
        for key in ("generation_descriptor_sha256", "predecessor_evidence_sha256", "genesis_import_sha256",
                    "seed_sha256", "runner_sha256", "config_sha256", "import_closure_sha256"):
            reader._hash(provenance[key])
        reader._int(provenance["seed_count"], minimum=0)
        release_commit = provenance["release_commit"]
        if type(release_commit) is not str or not GIT_SHA.fullmatch(release_commit):
            _deny("PROVENANCE_RELEASE_COMMIT_SCHEMA")
        for key in ("scheduler_identity", "audit_identity", "coordinator_identity"):
            reader._string(provenance[key])
        if provenance["predecessor_evidence_sha256"] != predecessor_evidence_digest:
            _deny("PROVENANCE_PREDECESSOR_MISMATCH")
        if (new_selection is not None and provenance["generation_descriptor_sha256"]
                != by_id[new_selection["review_id"]][1]["generation_descriptor_sha256"]):
            _deny("PROVENANCE_DESCRIPTOR_MISMATCH")

    candidate_approval_digest = transition["candidate_approval_sha256"]
    if candidate_approval_digest is None:
        if operation != "GATE":
            _deny("CANDIDATE_APPROVAL_REQUIRED")
    else:
        candidate_approval = _walk_metadata(candidate_approval_digest, object_bytes, budget)
        if new_selection is not None:
            new_review = by_id[new_selection["review_id"]][1]
            if candidate_approval_digest != new_review["candidate_approval_sha256"]:
                _deny("CANDIDATE_APPROVAL_BINDING_MISMATCH")
            if new_selection["review_sha256"] in _leaf_strings(candidate_approval):
                _deny("CIRCULAR_EVIDENCE")

    _walk_metadata(transition["commissioning_approval_sha256"], object_bytes, budget)

    interval = transition["incomplete_interval"]
    reader._shape(interval, INCOMPLETE_INTERVAL)
    start_at = reader._int(interval["start_at"])
    end_at = reader._int(interval["end_at"])
    if start_at > end_at:
        _deny("INCOMPLETE_INTERVAL_ORDER")
    if type(interval["status"]) is not str or interval["status"] not in INTERVAL_STATUS:
        _deny("INCOMPLETE_INTERVAL_STATUS")
    _walk_metadata(reader._hash(interval["evidence_sha256"]), object_bytes, budget)

    cutover_at = reader._int(transition["cutover_at"])

    selection_review_id = selection_review_sha256 = selection_commission_sha256 = None
    if new_selection is not None:
        new_review_for_cutover = by_id[new_selection["review_id"]][1]
        new_commission_for_cutover = reader._object(object_bytes, new_selection["commission_sha256"])
        if not (max(new_review_for_cutover["approved_at"], new_commission_for_cutover["commissioned_at"])
                <= cutover_at < new_review_for_cutover["expires_at"]):
            _deny("CUTOVER_TIME_ORDER")
        selection_review_id = new_selection["review_id"]
        selection_review_sha256 = new_selection["review_sha256"]
        selection_commission_sha256 = new_selection["commission_sha256"]

    commissioning_subject_sha256 = reader.sha256(reader.canonical_bytes(
        {key: value for key, value in transition.items() if key != "commissioning_approval_sha256"}))

    forbidden_self_refs = {transition_sha256, registry_sha256}
    for leaf in _leaf_strings(transition):
        if leaf in forbidden_self_refs:
            _deny("SELF_HASH_CYCLE")

    return VerifiedTransition(
        revision=index["revision"], registry_sha256=registry_sha256,
        previous_registry_sha256=index["previous_registry_sha256"],
        next_state_sha256=next_state_sha256,
        transition_id=transition["transition_id"], transition_sha256=transition_sha256,
        operation=operation, reason=reason, semantic_key=semantic_key_tuple,
        cutover_at=cutover_at, review_id=selection_review_id,
        review_sha256=selection_review_sha256, commission_sha256=selection_commission_sha256,
        candidate_approval_sha256=candidate_approval_digest,
        commissioning_subject_sha256=commissioning_subject_sha256)
