"""Bounded, pure, offline preflight/join over caller-supplied label-review claims.

This module checks internal consistency of caller-supplied claims about one
event/day: an original rule state and its receipt, a decision/capture pair, a
set of label-determining disclosure receipts, the existing
`derive_offline_settlement_source` result (see official_settlement_source.py),
and an independently supplied Gamma comparator vector. It performs no I/O, no
network access, and no file reads beyond its plain Python arguments. It never
calls `derive_offline_settlement_source` or any archive/reader/scorer/
supervisor module -- those results are caller-supplied data, not fetched here.

It mints nothing that looks like a LABEL, adjudicates no publisher rights or
authenticity, marks no event "qualified", and never sets any independent-
label/settlement/calibration/financial/promotion authority flag to True. Those
fields on `ReviewPacket` are frozen with literal `False` defaults and this
module never passes them as constructor arguments anywhere, so there is no
code path, under any input, that can make them True.

A settlement-source claim whose `code` is `SYNTHETIC_DERIVATION_ONLY` (the
only non-authoritative success code the existing contract can produce) is
passed through verbatim in `source_claim`; this module never upgrades, hides,
or rewrites that marking.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import types
from typing import Mapping


VERSION = "offline_official_label_review_packet_v1"

# This matches official_settlement_source.VERSION by literal value only. This
# module deliberately does not import that module, to avoid any appearance of
# invoking it; the literal is a shape expectation on the caller-supplied dict.
_SOURCE_CONTRACT_VERSION = "offline_official_settlement_source_contract_v1"

MATCH = "MATCH"
MISMATCH = "MISMATCH"
UNAVAILABLE = "UNAVAILABLE"


def _clock(value: object) -> bool:
    return type(value) in (int, float) and math.isfinite(value) and value >= 0


def _sha(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _identity(value: object) -> bool:
    return isinstance(value, str) and bool(value)


def _seq(value: object) -> bool:
    return type(value) is int and value >= 0


def _bucket_identity_valid(b: object) -> bool:
    return (isinstance(b, dict)
            and all(_identity(b.get(k)) for k in ("market_id", "condition_id", "yes_token", "no_token")))


def _bucket_violations(partition: list, buckets: object) -> tuple:
    if not isinstance(buckets, tuple) or not buckets or any(not _bucket_identity_valid(b) for b in buckets):
        return ("BUCKET_MALFORMED",)
    out = []
    rule_ids = [b["market_id"] for b in partition]
    dec_ids = [b["market_id"] for b in buckets]
    if len(dec_ids) != len(set(dec_ids)):
        out.append("BUCKET_DUPLICATED")
    if set(dec_ids) != set(rule_ids):
        out.append("BUCKET_SET_MISMATCH")
    elif dec_ids != rule_ids:
        out.append("BUCKET_REORDERED")
    rule_by_id = {b["market_id"]: b for b in partition}
    for b in buckets:
        ref = rule_by_id.get(b["market_id"])
        if ref is not None and (b["condition_id"] != ref["condition_id"]
                                 or b["yes_token"] != ref["yes_token"]
                                 or b["no_token"] != ref["no_token"]):
            out.append("BUCKET_TOKEN_MISMATCH")
    return tuple(out)


@dataclass(frozen=True)
class ReviewPacket:
    version: str
    event_id: str | None
    station: str | None
    target_date: str | None
    rule_fingerprint_sha256: str | None
    source_claim: Mapping[str, object]
    gamma_comparison: str
    violations: tuple[str, ...]
    mechanism_consistent: bool
    # Literal, hardcoded defaults only. No call in this module ever passes
    # these as constructor keyword arguments, so no input can make them True.
    independent_label_attestation: bool = False
    settlement_authority: bool = False
    calibration_authority: bool = False
    financial_authority: bool = False
    automatic_promotion: bool = False
    qualified: bool = False

    def __post_init__(self) -> None:
        # Defense in depth: the public builder never passes these as
        # constructor arguments, but `dataclasses.replace(...)` or a direct
        # `ReviewPacket(...)` call bypasses the builder entirely. Make the
        # type itself refuse to hold a True value on any of these fields, no
        # matter how the instance was constructed.
        for name in ("independent_label_attestation", "settlement_authority",
                     "calibration_authority", "financial_authority",
                     "automatic_promotion", "qualified"):
            if getattr(self, name) is not False:
                raise ValueError(f"{name} must be literal False")
        # `source_claim` is caller data copied into the packet; without this
        # it stays a mutable dict after construction even though the packet
        # itself is frozen. Wrap it read-only so the only way to change it is
        # to build a new packet.
        if isinstance(self.source_claim, dict):
            object.__setattr__(self, "source_claim", types.MappingProxyType(self.source_claim))


def build_review_packet(*, rule, rule_receipt, decision, capture,
                         disclosures, source_claim, gamma_comparator=None) -> ReviewPacket:
    """Pure offline join/preflight. Never raises on malformed caller data.

    ``rule`` is a `polymarket_scanner.v11.rules.RuleFingerprint`. ``rule_receipt``
    is a plain dict describing the original rule admission receipt:
    {"event_id", "seq" (int), "recorded_at" (clock), "fingerprint" (sha256)} or
    None if no receipt is claimed. ``decision`` is a plain dict describing the
    whole-vector causal decision: {"event_id", "seq", "recorded_at",
    "rule_fingerprint_sha256", "buckets": tuple of {"market_id","condition_id",
    "yes_token","no_token"} dicts}. ``capture`` is a plain dict describing the
    capture receipt that pins the decision: {"event_id", "seq", "recorded_at"}.
    ``disclosures`` is a tuple of plain dicts, one per known label-determining
    receipt for this event (every candidate, not only a "selected" one):
    {"id", "seq", "recorded_at", optional "raw_sha256", optional "selected"}.
    ``source_claim`` is the dict returned by
    `official_settlement_source.derive_offline_settlement_source`.
    ``gamma_comparator`` is an independently supplied plain dict or None:
    {"event_id","station","target_date","timezone","unit","raw_sha256",
    "winning_market_id"}.
    """
    violations: list[str] = []

    def V(code: str) -> None:
        violations.append(code)

    from .rules import RuleFingerprint

    p: dict = {}
    rule_sha = None
    if isinstance(rule, RuleFingerprint):
        rule_sha = rule.sha256
        try:
            candidate = rule.payload
        except Exception:
            candidate = None
        if isinstance(candidate, dict):
            p = candidate
        else:
            V("RULE_PREIMAGE_INVALID")
    else:
        V("RULE_PREIMAGE_INVALID")

    partition = p.get("partition")
    if not isinstance(partition, list) or not partition or any(not _bucket_identity_valid(b) for b in partition):
        V("RULE_PARTITION_INVALID")
        partition = []

    event_id = p.get("event_id") if _identity(p.get("event_id")) else None
    station = p.get("station") if _identity(p.get("station")) else None
    target_date = p.get("target_date") if _identity(p.get("target_date")) else None
    timezone_name = p.get("timezone") if _identity(p.get("timezone")) else None
    unit = p.get("unit") if _identity(p.get("unit")) else None

    # A rule payload that parses but is missing one of its own identity
    # fields must say so explicitly: silently skipping the matching checks
    # below (because the field is None) is a fail-open gap, not a pass.
    if p and (event_id is None or station is None or target_date is None
              or timezone_name is None or unit is None):
        V("RULE_IDENTITY_INCOMPLETE")

    partition_market_ids = {b["market_id"] for b in partition}
    partition_by_id = {b["market_id"]: b for b in partition}

    # --- source_claim: pass-through validation only; never upgraded. ---
    winning_market_id = None
    winner_in_partition = True
    source_ok = isinstance(source_claim, dict) and source_claim.get("version") == _SOURCE_CONTRACT_VERSION
    if not source_ok:
        V("SOURCE_CLAIM_MALFORMED")
    else:
        if (source_claim.get("independent_label_attestation") is not False
                or source_claim.get("settlement_authority") is not False
                or source_claim.get("financial_authority") is not False
                or source_claim.get("automatic_promotion") is not False
                or source_claim.get("synthetic_mechanism_only") is not True):
            V("SOURCE_CLAIM_AUTHORITY_VIOLATION")
        if rule_sha is not None and source_claim.get("rule_fingerprint_sha256") != rule_sha:
            V("RULE_FINGERPRINT_SOURCE_MISMATCH")
        # Station/target_date identity binding must hold regardless of the
        # claimed code -- a refusal claim still asserts these fields, and
        # skipping the check on the refusal path would be fail-open.
        if station is not None and source_claim.get("station") != station:
            V("STATION_MISMATCH")
        if target_date is not None and source_claim.get("target_date") != target_date:
            V("TARGET_DATE_MISMATCH")
        if source_claim.get("code") == "SYNTHETIC_DERIVATION_ONLY":
            candidate_winner = source_claim.get("winning_market_id")
            if _identity(candidate_winner):
                winning_market_id = candidate_winner
                if partition_market_ids and candidate_winner not in partition_market_ids:
                    V("WINNER_NOT_IN_PARTITION")
                    winner_in_partition = False
                bucket = partition_by_id.get(candidate_winner)
                if bucket is not None and source_claim.get("winning_yes_token") != bucket["yes_token"]:
                    V("WINNING_TOKEN_MISMATCH")
            else:
                V("SOURCE_CLAIM_MALFORMED")
        # A refusal code, or a SYNTHETIC claim with no usable winner (already
        # separately flagged SOURCE_CLAIM_MALFORMED above in that case),
        # means there is nothing to review. Say so explicitly instead of
        # staying silent whenever Gamma happens to be absent too.
        if winning_market_id is None:
            V("SOURCE_WINNER_UNAVAILABLE")

    # --- rule_receipt: must exist and predate the decision. ---
    rule_receipt_ok = False
    if rule_receipt is None:
        V("RULE_RECEIPT_MISSING")
    elif not isinstance(rule_receipt, dict):
        V("RULE_RECEIPT_MALFORMED")
    else:
        fp, seq, recorded_at, rec_event = (rule_receipt.get("fingerprint"), rule_receipt.get("seq"),
                                           rule_receipt.get("recorded_at"), rule_receipt.get("event_id"))
        if not _sha(fp) or not _seq(seq) or not _clock(recorded_at) or not _identity(rec_event):
            V("RULE_RECEIPT_MALFORMED")
        else:
            rule_receipt_ok = True
            if rule_sha is not None and fp != rule_sha:
                V("RULE_FINGERPRINT_MISMATCH")
            if event_id is not None and rec_event != event_id:
                V("EVENT_ID_MISMATCH")

    # --- decision: whole-vector causal decision; buckets checked below. ---
    decision_ok = False
    d_buckets = None
    if not isinstance(decision, dict):
        V("DECISION_MALFORMED")
    else:
        d_event, d_seq, d_rec, d_rule_sha, d_buckets = (
            decision.get("event_id"), decision.get("seq"), decision.get("recorded_at"),
            decision.get("rule_fingerprint_sha256"), decision.get("buckets"))
        if (not _identity(d_event) or not _seq(d_seq) or not _clock(d_rec)
                or not _sha(d_rule_sha) or not isinstance(d_buckets, tuple)):
            V("DECISION_MALFORMED")
        else:
            decision_ok = True
            if event_id is not None and d_event != event_id:
                V("EVENT_ID_MISMATCH")
            if rule_sha is not None and d_rule_sha != rule_sha:
                V("DECISION_RULE_BINDING_MISMATCH")

    if decision_ok:
        if partition:
            violations.extend(_bucket_violations(partition, d_buckets))
        else:
            V("BUCKET_SET_MISMATCH")

    # --- capture: must be at/after the decision it pins. ---
    capture_ok = False
    if not isinstance(capture, dict):
        V("CAPTURE_MALFORMED")
    else:
        c_event, c_seq, c_rec = capture.get("event_id"), capture.get("seq"), capture.get("recorded_at")
        if not _identity(c_event) or not _seq(c_seq) or not _clock(c_rec):
            V("CAPTURE_MALFORMED")
        else:
            capture_ok = True
            if event_id is not None and c_event != event_id:
                V("EVENT_ID_MISMATCH")

    if rule_receipt_ok and decision_ok:
        # Strictly predate, matching the doc: an equal seq/timestamp is not a
        # predate, the same way the disclosure lookahead check below is
        # strict rather than allowing an at-or-before receipt through.
        if rule_receipt["seq"] >= decision["seq"] or rule_receipt["recorded_at"] >= decision["recorded_at"]:
            V("RULE_RECEIPT_NOT_BEFORE_DECISION")
    if decision_ok and capture_ok:
        if not (decision["seq"] <= capture["seq"] and decision["recorded_at"] <= capture["recorded_at"]):
            V("CAPTURE_BEFORE_DECISION")

    # --- disclosures: every candidate receipt, not only a "selected" one. ---
    valid_disclosures = []
    if not isinstance(disclosures, tuple):
        V("DISCLOSURE_MALFORMED")
    else:
        seen: dict = {}
        for disc in disclosures:
            if not isinstance(disc, dict):
                V("DISCLOSURE_MALFORMED")
                continue
            did, dseq, drec, dsha = (disc.get("id"), disc.get("seq"),
                                     disc.get("recorded_at"), disc.get("raw_sha256"))
            if (not _identity(did) or not _seq(dseq) or not _clock(drec)
                    or (dsha is not None and not _sha(dsha))):
                V("DISCLOSURE_MALFORMED")
                continue
            if did in seen:
                prior = seen[did]
                if prior.get("seq") != dseq or prior.get("recorded_at") != drec or prior.get("raw_sha256") != dsha:
                    V("DISCLOSURE_CONFLICT")
                else:
                    # Exact duplicate of an already-seen receipt. Harmless on
                    # its own, but a silently-tolerated duplicate is still an
                    # unexamined gap for a refusal/consistency mechanism:
                    # flag it explicitly rather than accepting it quietly.
                    V("DISCLOSURE_DUPLICATED")
            else:
                seen[did] = disc
            valid_disclosures.append(disc)

    # With no well-formed disclosure at all there is nothing to check for
    # lookahead or source binding below, but the packet must say so rather
    # than reporting an unqualified "consistent".
    if not valid_disclosures:
        V("DISCLOSURE_MISSING")

    # Every disclosure is checked, selected or not: a later "selected" receipt
    # can never excuse an earlier one that already made the winner knowable.
    # A disclosure only "binds" the source claim's own raw bytes if its hash
    # matches AND it independently passes the lookahead check: a receipt that
    # is itself a lookahead violation cannot attest anything about the bytes
    # that actually produced the winner.
    source_raw_sha = source_claim.get("raw_sha256") if winning_market_id is not None else None
    disclosure_bound_to_source = False
    for disc in valid_disclosures:
        lookahead = False
        if decision_ok and (disc["seq"] <= decision["seq"] or disc["recorded_at"] <= decision["recorded_at"]):
            lookahead = True
        if capture_ok and (disc["seq"] <= capture["seq"] or disc["recorded_at"] <= capture["recorded_at"]):
            lookahead = True
        if lookahead:
            V("LOOKAHEAD_VIOLATION")
        elif _sha(source_raw_sha) and disc.get("raw_sha256") == source_raw_sha:
            disclosure_bound_to_source = True

    # A SYNTHETIC winner is only reviewable if at least one well-formed,
    # non-lookahead disclosure receipt attests the exact bytes that produced
    # it. Without that, the lookahead check above can be satisfied by any
    # unrelated late receipt while the bytes that actually produced the
    # winner have no attested receipt time at all.
    if winning_market_id is not None and not disclosure_bound_to_source:
        V("SOURCE_DISCLOSURE_UNBOUND")

    # --- Gamma comparator: kept as a separate comparator, never merged in. ---
    gamma_comparison = UNAVAILABLE
    if gamma_comparator is None:
        gamma_comparison = UNAVAILABLE
    elif not isinstance(gamma_comparator, dict):
        V("GAMMA_MALFORMED")
        gamma_comparison = UNAVAILABLE
    else:
        g_event, g_station, g_date, g_tz, g_unit, g_sha, g_winner = (
            gamma_comparator.get("event_id"), gamma_comparator.get("station"),
            gamma_comparator.get("target_date"), gamma_comparator.get("timezone"),
            gamma_comparator.get("unit"), gamma_comparator.get("raw_sha256"),
            gamma_comparator.get("winning_market_id"))
        if (not _identity(g_event) or not _identity(g_station) or not _identity(g_date)
                or not _identity(g_tz) or not _identity(g_unit) or not _sha(g_sha) or not _identity(g_winner)):
            V("GAMMA_MALFORMED")
            gamma_comparison = UNAVAILABLE
        else:
            if event_id is not None and g_event != event_id:
                V("EVENT_ID_MISMATCH")
            if station is not None and g_station != station:
                V("STATION_MISMATCH")
            if target_date is not None and g_date != target_date:
                V("TARGET_DATE_MISMATCH")
            if timezone_name is not None and g_tz != timezone_name:
                V("TIMEZONE_MISMATCH")
            if unit is not None and g_unit != unit:
                V("UNIT_MISMATCH")
            gamma_winner_in_partition = not partition_market_ids or g_winner in partition_market_ids
            if not gamma_winner_in_partition:
                V("WINNER_NOT_IN_PARTITION")
            source_sha = source_claim.get("raw_sha256") if isinstance(source_claim, dict) else None
            if winning_market_id is None:
                # Already flagged SOURCE_WINNER_UNAVAILABLE above,
                # unconditionally, whether or not Gamma is even present.
                gamma_comparison = UNAVAILABLE
            elif _sha(source_sha) and g_sha == source_sha:
                # An "independent" Gamma comparator sharing the exact source
                # document hash is not actually independent evidence -- the
                # same bytes cannot corroborate themselves. Treat this as a
                # violation and refuse to call it MATCH even if the claimed
                # winners agree.
                V("GAMMA_SOURCE_HASH_COLLISION_SUSPECTED")
                gamma_comparison = MISMATCH
            elif not winner_in_partition or not gamma_winner_in_partition:
                # A winner id outside the rule's own bucket partition is not
                # a real winner (e.g. a "ghost" id both sides happen to
                # agree on): never report MATCH for that, even if the two
                # claimed ids are literally equal.
                gamma_comparison = MISMATCH
            elif g_winner == winning_market_id:
                gamma_comparison = MATCH
            else:
                gamma_comparison = MISMATCH

    return ReviewPacket(
        version=VERSION,
        event_id=event_id,
        station=station,
        target_date=target_date,
        rule_fingerprint_sha256=rule_sha,
        source_claim=dict(source_claim) if isinstance(source_claim, dict) else {},
        gamma_comparison=gamma_comparison,
        violations=tuple(violations),
        mechanism_consistent=not violations,
    )
