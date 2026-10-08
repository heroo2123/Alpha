"""Pure, bounded v2 daily-review candidate verifier.

This module accepts bytes supplied by a future protected reader. It does not
establish root custody, writer ownership, or permission to run a day. See
docs/v11-daily-review-v2-reader-contract.md before integrating it.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import hashlib
import json
import re
from typing import Mapping


VERSION = "alpha_v11_certification_reviews_v2"
STAGES = frozenset(("PAPER", "SHADOW", "CANARY_ELIGIBLE", "CANARY_VERIFIED", "LIVE_LIMITED"))
MAX_INDEX_BYTES = 1_048_576
MAX_OBJECT_BYTES = 262_144
MAX_PREFIX_BYTES = 67_108_864
MAX_REVIEWS = 1000
MAX_ROWS = 100_000
HEX = re.compile(r"[0-9a-f]{64}\Z")
DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")


class ReviewV2Error(ValueError):
    """A candidate is malformed or has no valid active selection."""


def _deny(code: str):
    raise ReviewV2Error(code)


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            _deny("DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _constant(_value):
    _deny("NONFINITE_JSON_NUMBER")


def _bounded_bytes(raw: bytes, limit: int):
    if type(raw) is not bytes or len(raw) > limit or not raw:
        _deny("BYTE_BOUND")
    return raw


def _parse(raw: bytes, limit: int):
    _bounded_bytes(raw, limit)
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=_constant)
    except ReviewV2Error:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise ReviewV2Error("INVALID_JSON") from exc
    # Require one representation for every content-addressed object.
    if _encode(value) != raw:
        _deny("NONCANONICAL_JSON")
    return value


def _encode(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=True, allow_nan=False).encode("ascii")
    except (TypeError, ValueError, RecursionError) as exc:
        raise ReviewV2Error("INVALID_JSON_VALUE") from exc


def sha256(value: bytes) -> str:
    if type(value) is not bytes:
        _deny("HASH_INPUT_TYPE")
    return hashlib.sha256(value).hexdigest()


def canonical_bytes(value) -> bytes:
    """Canonical encoding for synthetic fixtures and future protected writers."""
    return _encode(value)


def _shape(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        _deny("OBJECT_SCHEMA")


def _string(value, pattern=NAME):
    if type(value) is not str or not pattern.fullmatch(value):
        _deny("STRING_SCHEMA")
    return value


def _day(value):
    _string(value, DAY)
    try:
        date.fromisoformat(value)
    except ValueError as exc:
        raise ReviewV2Error("INVALID_DATE") from exc
    return value


def _path(value):
    if (type(value) is not str or len(value) > 512 or not value.startswith("/")
            or "//" in value or any(part in (".", "..") for part in value.split("/"))
            or not re.fullmatch(r"/[A-Za-z0-9_./-]+", value)):
        _deny("STORE_PATH_SCHEMA")
    return value


def _hash(value):
    return _string(value, HEX)


def _int(value, *, minimum=0):
    if type(value) is not int or value < minimum or value > 9_999_999_999:
        _deny("INTEGER_SCHEMA")
    return value


KEY = ("namespace", "stage", "scope_key", "metadata_fingerprint",
       "rule_fingerprint")
CONTEXT = KEY + ("target_date", "event_id", "generation_id")
SELECTION = CONTEXT + ("review_id", "review_sha256", "commission_sha256")
REVIEW = CONTEXT + ("review_id", "generation_descriptor_sha256", "approved_at",
                          "expires_at", "reviewed_through_seq",
                          "runtime_prefix_sha256", "review_projection_sha256",
                          "candidate_approval_sha256", "capability_proofs")
COMMISSION = ("review_id", "review_sha256", "generation_id", "target_date",
              "event_id", "generation_descriptor_sha256",
              "candidate_approval_sha256", "commissioned_at")
DESCRIPTOR = ("generation_id", "namespace", "target_date", "event_id",
              "store_path", "store_device", "store_inode", "marker_sha256")
STORE = ("generation_id", "namespace", "target_date", "event_id",
         "store_path", "store_device", "store_inode", "marker_sha256")
ROW = ("seq", "id", "kind", "event_id", "body", "body_sha256",
       "recorded_at", "available_at")


def _context(value):
    for key in ("namespace", "stage", "event_id", "generation_id"):
        _string(value[key])
    _day(value["target_date"])
    for key in ("scope_key", "metadata_fingerprint", "rule_fingerprint"):
        _hash(value[key])
    if value["stage"] not in STAGES:
        _deny("UNSUPPORTED_STAGE")


def _object(object_bytes: Mapping[str, bytes], digest: str):
    _hash(digest)
    if digest not in object_bytes:
        _deny("DANGLING_OBJECT")
    raw = object_bytes[digest]
    _bounded_bytes(raw, MAX_OBJECT_BYTES)
    if sha256(raw) != digest:
        _deny("TAMPERED_OBJECT")
    return _parse(raw, MAX_OBJECT_BYTES)


def _review(value):
    _shape(value, REVIEW)
    _context(value)
    _string(value["review_id"])
    for key in ("generation_descriptor_sha256", "runtime_prefix_sha256", "review_projection_sha256",
                "candidate_approval_sha256"):
        _hash(value[key])
    _int(value["approved_at"])
    _int(value["expires_at"])
    if value["approved_at"] >= value["expires_at"]:
        _deny("REVIEW_TIME_ORDER")
    _int(value["reviewed_through_seq"], minimum=1)
    proofs = value["capability_proofs"]
    if type(proofs) is not dict or not 1 <= len(proofs) <= 64:
        _deny("PROOF_SCHEMA")
    for capability, ref in proofs.items():
        _string(capability)
        _shape(ref, ("id", "sha256", "seq"))
        _string(ref["id"])
        _hash(ref["sha256"])
        _int(ref["seq"], minimum=1)


def _validate_index(index, objects):
    _shape(index, ("version", "revision", "previous_registry_sha256",
                   "review_objects", "active_selections"))
    if index["version"] != VERSION:
        _deny("UNSUPPORTED_VERSION")
    _int(index["revision"], minimum=1)
    _hash(index["previous_registry_sha256"])
    entries = index["review_objects"]
    active = index["active_selections"]
    if (type(entries) is not list or type(active) is not list
            or len(entries) > MAX_REVIEWS or len(active) > MAX_REVIEWS):
        _deny("INDEX_BOUND")
    by_id = {}
    for entry in entries:
        _shape(entry, ("review_id", "review_sha256"))
        rid = _string(entry["review_id"])
        digest = _hash(entry["review_sha256"])
        if rid in by_id:
            _deny("DUPLICATE_REVIEW_ID")
        review = _object(objects, digest)
        _review(review)
        if review["review_id"] != rid:
            _deny("REVIEW_ID_MISMATCH")
        by_id[rid] = (digest, review)
    seen = set()
    for selection in active:
        _shape(selection, SELECTION)
        _context(selection)
        rid = _string(selection["review_id"])
        digest = _hash(selection["review_sha256"])
        _hash(selection["commission_sha256"])
        semantic = tuple(selection[key] for key in KEY)
        if semantic in seen:
            _deny("DUPLICATE_ACTIVE_SEMANTIC_KEY")
        seen.add(semantic)
        if rid not in by_id or by_id[rid][0] != digest:
            _deny("DANGLING_ACTIVE_REVIEW")
        review = by_id[rid][1]
        if any(review[key] != selection[key] for key in CONTEXT + ("review_id",)):
            _deny("ACTIVE_REVIEW_CONTEXT_MISMATCH")
        commission = _object(objects, selection["commission_sha256"])
        _shape(commission, COMMISSION)
        for key in ("review_id", "generation_id", "event_id"):
            _string(commission[key])
        _day(commission["target_date"])
        for key in ("review_sha256", "generation_descriptor_sha256",
                    "candidate_approval_sha256"):
            _hash(commission[key])
        _int(commission["commissioned_at"])
        if commission["commissioned_at"] < review["approved_at"]:
            _deny("COMMISSION_BEFORE_REVIEW")
        expected = {key: selection[key] for key in
                    ("review_id", "review_sha256", "generation_id", "target_date", "event_id")}
        expected.update({key: review[key] for key in
                         ("generation_descriptor_sha256", "candidate_approval_sha256")})
        if any(commission[key] != value for key, value in expected.items()):
            _deny("COMMISSION_BINDING_MISMATCH")
    return by_id


def _prefix(raw, review, descriptor, store):
    _shape(descriptor, DESCRIPTOR)
    _shape(store, STORE)
    for key in ("generation_id", "namespace", "event_id"):
        _string(descriptor[key])
        _string(store[key])
    for item in (descriptor, store):
        _path(item["store_path"])
        _day(item["target_date"])
        _int(item["store_device"], minimum=1)
        _int(item["store_inode"], minimum=1)
        _hash(item["marker_sha256"])
    if descriptor != store:
        _deny("MIXED_STORE_IDENTITY")
    if any(descriptor[key] != review[key] for key in
           ("generation_id", "namespace", "target_date", "event_id")):
        _deny("DESCRIPTOR_CONTEXT_MISMATCH")
    if sha256(canonical_bytes(descriptor)) != review["generation_descriptor_sha256"]:
        _deny("DESCRIPTOR_HASH_MISMATCH")
    rows = _parse(raw, MAX_PREFIX_BYTES)
    if type(rows) is not list or len(rows) > MAX_ROWS:
        _deny("PREFIX_BOUND")
    frontier = review["reviewed_through_seq"]
    if len(rows) != frontier:
        _deny("SPARSE_OR_EXTENDED_PREFIX")
    seen_ids = set()
    for seq, row in enumerate(rows, 1):
        _shape(row, ROW)
        if _int(row["seq"], minimum=1) != seq:
            _deny("SPARSE_PREFIX")
        rid = _string(row["id"])
        if rid in seen_ids:
            _deny("DUPLICATE_PREFIX_ID")
        seen_ids.add(rid)
        _string(row["kind"])
        _string(row["event_id"])
        _int(row["recorded_at"])
        _int(row["available_at"])
        _hash(row["body_sha256"])
        if row["body_sha256"] != sha256(canonical_bytes(row["body"])):
            _deny("PREFIX_BODY_MISMATCH")
    if sha256(raw) != review["runtime_prefix_sha256"]:
        _deny("PREFIX_DIGEST_MISMATCH")
    for cap, ref in review["capability_proofs"].items():
        if ref["seq"] > frontier:
            _deny("PROOF_OUTSIDE_PREFIX")
        row = rows[ref["seq"] - 1]
        if (row["id"] != ref["id"] or row["body_sha256"] != ref["sha256"]
                or row["kind"] != "REGISTRY"):
            _deny("PROOF_REFERENCE_MISMATCH")
        body = row["body"]
        _shape(body, ("action", "scope_key", "capability", "result",
                      "metadata_fingerprint", "rule_fingerprint"))
        if (body["action"] != "CAPABILITY_EVIDENCE" or body["result"] != "PASS"
                or body["capability"] != cap
                or any(body[key] != review[key] for key in
                       ("scope_key", "metadata_fingerprint", "rule_fingerprint"))
                or row["event_id"] != review["event_id"]
                or row["recorded_at"] > review["approved_at"]
                or row["available_at"] > review["approved_at"]):
            _deny("PROOF_SEMANTICS_MISMATCH")


@dataclass(frozen=True)
class SelectedReview:
    revision: int
    registry_sha256: str
    review_id: str
    review_sha256: str
    commission_sha256: str
    generation_id: str
    generation_descriptor_sha256: str
    reviewed_through_seq: int
    runtime_prefix_sha256: str
    expires_at: int


def select_active_review(*, index_bytes: bytes, object_bytes: Mapping[str, bytes],
                         descriptor_bytes: bytes, prefix_bytes: bytes, store_identity: dict,
                         namespace: str, stage: str, scope_key: str,
                         metadata_fingerprint: str, rule_fingerprint: str,
                         target_date: str, event_id: str, generation_id: str,
                         expected_revision: int, now: int) -> SelectedReview:
    """Validate all entries and one exact selection; return a non-authorizing pin."""
    if type(object_bytes) is not dict or len(object_bytes) > MAX_REVIEWS * 2:
        _deny("OBJECT_MAP_BOUND")
    _int(expected_revision, minimum=1)
    _int(now)
    request = dict(namespace=namespace, stage=stage, scope_key=scope_key,
                   metadata_fingerprint=metadata_fingerprint,
                   rule_fingerprint=rule_fingerprint, target_date=target_date,
                   event_id=event_id, generation_id=generation_id)
    _context(request)
    index = _parse(index_bytes, MAX_INDEX_BYTES)
    by_id = _validate_index(index, object_bytes)
    if index["revision"] != expected_revision:
        _deny("STALE_REVISION")
    matches = [entry for entry in index["active_selections"]
               if all(entry[key] == request[key] for key in CONTEXT)]
    if len(matches) != 1:
        _deny("ACTIVE_SELECTION_MISSING_OR_AMBIGUOUS")
    selection = matches[0]
    review = by_id[selection["review_id"]][1]
    if not review["approved_at"] <= now < review["expires_at"]:
        _deny("SELECTION_NOT_CURRENT")
    commission = _object(object_bytes, selection["commission_sha256"])
    if not commission["commissioned_at"] <= now:
        _deny("COMMISSION_NOT_CURRENT")
    descriptor = _parse(descriptor_bytes, MAX_OBJECT_BYTES)
    _prefix(prefix_bytes, review, descriptor, store_identity)
    return SelectedReview(index["revision"], sha256(index_bytes),
                          selection["review_id"], selection["review_sha256"],
                          selection["commission_sha256"], generation_id,
                          review["generation_descriptor_sha256"],
                          review["reviewed_through_seq"],
                          review["runtime_prefix_sha256"], review["expires_at"])
