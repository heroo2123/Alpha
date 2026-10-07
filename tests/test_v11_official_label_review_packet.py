"""Adverse/synthetic exercises for the offline label-review preflight/join.

All data here is synthetic and in-memory. No file, network, archive, scorer,
or supervisor is touched. Passing these tests is a mechanism check only; it
grants no label, qualification, settlement, calibration, or financial credit.
"""
from __future__ import annotations

from copy import deepcopy
import dataclasses

import pytest

from polymarket_scanner.v11.evidence import canonical, digest
from polymarket_scanner.v11.official_label_review_packet import (
    MATCH, MISMATCH, UNAVAILABLE, ReviewPacket, build_review_packet,
)
from polymarket_scanner.v11.rules import RuleFingerprint


EVENT_ID = "synthetic-label-review-event"
STATION = "KATL"
TARGET_DATE = "2026-10-05"
TIMEZONE = "America/New_York"
UNIT = "F"

PARTITION = [
    {"market_id": "m0", "condition_id": "c0", "yes_token": "y0", "no_token": "n0", "lower": None, "upper": 69, "unit": "F"},
    {"market_id": "m1", "condition_id": "c1", "yes_token": "y1", "no_token": "n1", "lower": 70, "upper": 74, "unit": "F"},
    {"market_id": "m2", "condition_id": "c2", "yes_token": "y2", "no_token": "n2", "lower": 75, "upper": None, "unit": "F"},
]

SOURCE_CONTRACT_VERSION = "offline_official_settlement_source_contract_v1"


def must(condition):
    if not condition:
        pytest.fail("offline label review packet condition failed")


def make_rule():
    p = {"event_id": EVENT_ID, "station": STATION, "target_date": TARGET_DATE,
         "timezone": TIMEZONE, "unit": UNIT, "partition": deepcopy(PARTITION)}
    return RuleFingerprint(canonical(p), digest(p), "a" * 64)


def bucket_identity(b):
    return {"market_id": b["market_id"], "condition_id": b["condition_id"],
            "yes_token": b["yes_token"], "no_token": b["no_token"]}


def make_decision(rule, *, buckets=None, seq=10, recorded_at=1000.0, event_id=EVENT_ID, rule_sha=None):
    if buckets is None:
        buckets = tuple(bucket_identity(b) for b in PARTITION)
    return {"event_id": event_id, "seq": seq, "recorded_at": recorded_at,
            "rule_fingerprint_sha256": rule_sha if rule_sha is not None else rule.sha256,
            "buckets": buckets}


def make_capture(*, seq=11, recorded_at=1001.0, event_id=EVENT_ID):
    return {"event_id": event_id, "seq": seq, "recorded_at": recorded_at}


def make_rule_receipt(rule, *, seq=5, recorded_at=500.0, event_id=EVENT_ID, fingerprint=None):
    return {"event_id": event_id, "seq": seq, "recorded_at": recorded_at,
            "fingerprint": fingerprint if fingerprint is not None else rule.sha256}


def make_disclosure(*, id_="d1", seq=20, recorded_at=2000.0, raw_sha256=None, selected=True):
    out = {"id": id_, "seq": seq, "recorded_at": recorded_at, "selected": selected}
    if raw_sha256 is not None:
        out["raw_sha256"] = raw_sha256
    return out


def make_source_claim(*, rule, code="SYNTHETIC_DERIVATION_ONLY", winning_market_id="m1",
                      raw_sha256="c" * 64):
    return {"version": SOURCE_CONTRACT_VERSION, "code": code,
            "rule_fingerprint_sha256": rule.sha256,
            "synthetic_mechanism_only": True, "independent_label_attestation": False,
            "settlement_authority": False, "financial_authority": False,
            "automatic_promotion": False, "source_id": "https://example.invalid/wrh",
            "source_version": "synthetic-v1", "raw_sha256": raw_sha256,
            "target_date": TARGET_DATE, "station": STATION,
            "population": "WRH_HOURLY_DATA", "finality_at": 1500000.0,
            "whole_degree_value": 72, "winning_market_id": winning_market_id,
            "winning_yes_token": "y1", "losing_yes_tokens": ("y0", "y2"), "no_data": False}


def make_gamma(*, raw_sha256="e" * 64, winning_market_id="m1"):
    return {"event_id": EVENT_ID, "station": STATION, "target_date": TARGET_DATE,
            "timezone": TIMEZONE, "unit": UNIT, "raw_sha256": raw_sha256,
            "winning_market_id": winning_market_id}


def full_valid_kwargs():
    rule = make_rule()
    # The disclosure's raw_sha256 is bound to the source claim's own default
    # raw_sha256 ("c" * 64 in make_source_claim) so the baseline fixture has
    # an actual attested receipt for the bytes that produced the winner, not
    # just an unrelated later receipt (see SOURCE_DISCLOSURE_UNBOUND).
    return dict(rule=rule, rule_receipt=make_rule_receipt(rule), decision=make_decision(rule),
                capture=make_capture(), disclosures=(make_disclosure(raw_sha256="c" * 64),),
                source_claim=make_source_claim(rule=rule), gamma_comparator=make_gamma())


def assert_authority_false(result):
    must(result.independent_label_attestation is False)
    must(result.settlement_authority is False)
    must(result.calibration_authority is False)
    must(result.financial_authority is False)
    must(result.automatic_promotion is False)
    must(result.qualified is False)


def test_fully_valid_input_is_mechanism_consistent_and_matches():
    result = build_review_packet(**full_valid_kwargs())
    must(result.mechanism_consistent is True)
    must(result.violations == ())
    must(result.gamma_comparison == MATCH)
    assert_authority_false(result)


def test_reordered_buckets_rejected():
    kwargs = full_valid_kwargs()
    buckets = tuple(bucket_identity(b) for b in (PARTITION[1], PARTITION[0], PARTITION[2]))
    kwargs["decision"] = make_decision(kwargs["rule"], buckets=buckets)
    result = build_review_packet(**kwargs)
    must("BUCKET_REORDERED" in result.violations)
    must(result.mechanism_consistent is False)
    assert_authority_false(result)


def test_omitted_bucket_rejected():
    kwargs = full_valid_kwargs()
    buckets = tuple(bucket_identity(b) for b in (PARTITION[0], PARTITION[1]))
    kwargs["decision"] = make_decision(kwargs["rule"], buckets=buckets)
    result = build_review_packet(**kwargs)
    must("BUCKET_SET_MISMATCH" in result.violations)
    assert_authority_false(result)


def test_duplicated_bucket_rejected():
    kwargs = full_valid_kwargs()
    buckets = tuple(bucket_identity(b) for b in (PARTITION[0], PARTITION[1], PARTITION[2], PARTITION[1]))
    kwargs["decision"] = make_decision(kwargs["rule"], buckets=buckets)
    result = build_review_packet(**kwargs)
    must("BUCKET_DUPLICATED" in result.violations)
    assert_authority_false(result)


def test_swapped_condition_yes_token_rejected():
    kwargs = full_valid_kwargs()
    swapped0 = dict(bucket_identity(PARTITION[0]), yes_token="y1")
    swapped1 = dict(bucket_identity(PARTITION[1]), yes_token="y0")
    buckets = (swapped0, swapped1, bucket_identity(PARTITION[2]))
    kwargs["decision"] = make_decision(kwargs["rule"], buckets=buckets)
    result = build_review_packet(**kwargs)
    must("BUCKET_TOKEN_MISMATCH" in result.violations)
    assert_authority_false(result)


def test_stale_or_drifted_rule_preimage_fingerprint_mismatch():
    kwargs = full_valid_kwargs()
    kwargs["rule_receipt"] = make_rule_receipt(kwargs["rule"], fingerprint="b" * 64)
    result = build_review_packet(**kwargs)
    must("RULE_FINGERPRINT_MISMATCH" in result.violations)
    assert_authority_false(result)


def test_missing_original_rule_receipt_admission():
    kwargs = full_valid_kwargs()
    kwargs["rule_receipt"] = None
    result = build_review_packet(**kwargs)
    must("RULE_RECEIPT_MISSING" in result.violations)
    assert_authority_false(result)


@pytest.mark.parametrize("disclosure_recorded_at,disclosure_seq,should_violate", [
    (1001.1, 12, False),   # strictly after capture: valid
    (1000.0, 10, True),    # exactly at decision time: lookahead, rejected
    (900.0, 1, True),      # strictly before decision: lookahead, rejected
])
def test_disclosure_timing_vs_decision_lookahead(disclosure_recorded_at, disclosure_seq, should_violate):
    kwargs = full_valid_kwargs()
    kwargs["disclosures"] = (make_disclosure(recorded_at=disclosure_recorded_at, seq=disclosure_seq),)
    result = build_review_packet(**kwargs)
    must(("LOOKAHEAD_VIOLATION" in result.violations) == should_violate)
    assert_authority_false(result)


def test_post_capture_selected_receipt_cannot_mask_earlier_disclosure():
    kwargs = full_valid_kwargs()
    early_unselected = make_disclosure(id_="early", seq=2, recorded_at=200.0, selected=False)
    later_selected = make_disclosure(id_="later", seq=30, recorded_at=3000.0, selected=True)
    kwargs["disclosures"] = (early_unselected, later_selected)
    result = build_review_packet(**kwargs)
    must("LOOKAHEAD_VIOLATION" in result.violations)
    assert_authority_false(result)


def test_duplicated_conflicting_receipts_same_event():
    kwargs = full_valid_kwargs()
    first = make_disclosure(id_="dup", seq=20, recorded_at=2000.0)
    conflicting = make_disclosure(id_="dup", seq=21, recorded_at=2100.0)
    kwargs["disclosures"] = (first, conflicting)
    result = build_review_packet(**kwargs)
    must("DISCLOSURE_CONFLICT" in result.violations)
    assert_authority_false(result)


def test_gamma_hash_equal_to_source_hash_treated_with_suspicion():
    # An "independent" comparator sharing the exact source document hash is not
    # independent evidence -- identical bytes cannot corroborate themselves.
    # This must never be reported as MATCH even if the winners also agree.
    kwargs = full_valid_kwargs()
    source_claim = kwargs["source_claim"]
    kwargs["gamma_comparator"] = make_gamma(raw_sha256=source_claim["raw_sha256"], winning_market_id="m1")
    result = build_review_packet(**kwargs)
    must("GAMMA_SOURCE_HASH_COLLISION_SUSPECTED" in result.violations)
    must(result.gamma_comparison == MISMATCH)
    assert_authority_false(result)


def test_gamma_agrees_with_source_result():
    kwargs = full_valid_kwargs()
    result = build_review_packet(**kwargs)
    must(result.gamma_comparison == MATCH)
    must("GAMMA_SOURCE_HASH_COLLISION_SUSPECTED" not in result.violations)
    assert_authority_false(result)


def test_gamma_disagrees_with_source_result():
    kwargs = full_valid_kwargs()
    kwargs["gamma_comparator"] = make_gamma(winning_market_id="m2")
    result = build_review_packet(**kwargs)
    must(result.gamma_comparison == MISMATCH)
    assert_authority_false(result)


def test_gamma_vector_absent_entirely():
    kwargs = full_valid_kwargs()
    kwargs["gamma_comparator"] = None
    result = build_review_packet(**kwargs)
    must(result.gamma_comparison == UNAVAILABLE)
    assert_authority_false(result)


@pytest.mark.parametrize("mutate", [
    lambda kwargs: kwargs["decision"].update(recorded_at="not-a-number"),
    lambda kwargs: kwargs["decision"].update(seq="10"),
    lambda kwargs: kwargs["capture"].update(recorded_at=None),
    lambda kwargs: kwargs["rule_receipt"].update(recorded_at=[]),
])
def test_malformed_wrong_type_clock_fields_rejected(mutate):
    kwargs = full_valid_kwargs()
    mutate(kwargs)
    result = build_review_packet(**kwargs)
    must(result.mechanism_consistent is False)
    must(len(result.violations) > 0)
    assert_authority_false(result)


@pytest.mark.parametrize("mutate,expected_code", [
    (lambda kwargs: kwargs["rule_receipt"].update(fingerprint=12345), "RULE_RECEIPT_MALFORMED"),
    (lambda kwargs: kwargs["decision"].update(rule_fingerprint_sha256=b"not-a-string"), "DECISION_MALFORMED"),
    (lambda kwargs: kwargs.__setitem__("gamma_comparator", make_gamma(raw_sha256=42)), "GAMMA_MALFORMED"),
])
def test_malformed_wrong_type_hash_fields_rejected(mutate, expected_code):
    kwargs = full_valid_kwargs()
    mutate(kwargs)
    result = build_review_packet(**kwargs)
    must(expected_code in result.violations)
    assert_authority_false(result)


def test_fahrenheit_celsius_identity_mismatch_between_inputs():
    kwargs = full_valid_kwargs()
    kwargs["gamma_comparator"] = make_gamma()
    kwargs["gamma_comparator"]["unit"] = "C"
    result = build_review_packet(**kwargs)
    must("UNIT_MISMATCH" in result.violations)
    assert_authority_false(result)


def test_dst_related_timezone_identity_mismatch_between_inputs():
    kwargs = full_valid_kwargs()
    kwargs["gamma_comparator"] = make_gamma()
    kwargs["gamma_comparator"]["timezone"] = "America/Chicago"
    result = build_review_packet(**kwargs)
    must("TIMEZONE_MISMATCH" in result.violations)
    assert_authority_false(result)


def test_synthetic_derivation_only_source_claim_never_upgraded():
    kwargs = full_valid_kwargs()
    result = build_review_packet(**kwargs)
    must(result.source_claim["code"] == "SYNTHETIC_DERIVATION_ONLY")
    must(result.source_claim["synthetic_mechanism_only"] is True)
    must(result.source_claim["independent_label_attestation"] is False)
    must(result.source_claim["settlement_authority"] is False)
    must(result.source_claim["financial_authority"] is False)
    must(result.source_claim["automatic_promotion"] is False)
    assert_authority_false(result)


def test_non_synthetic_source_claim_code_never_produces_a_winner_or_match():
    kwargs = full_valid_kwargs()
    kwargs["source_claim"] = make_source_claim(rule=kwargs["rule"], code="SOURCE_SEMANTICS_MISMATCH")
    kwargs["source_claim"].pop("winning_market_id", None)
    result = build_review_packet(**kwargs)
    must(result.gamma_comparison == UNAVAILABLE)
    # A refusal code with no winner must never be reported as mechanism-
    # consistent just because there happens to be nothing left to disagree
    # with -- that would be the fail-open gap this mechanism exists to catch.
    must("SOURCE_WINNER_UNAVAILABLE" in result.violations)
    must(result.mechanism_consistent is False)
    assert_authority_false(result)


def test_refusal_code_source_claim_with_no_gamma_still_flags_winner_unavailable():
    # Same refusal shape as above, but with Gamma entirely absent too.
    # SOURCE_WINNER_UNAVAILABLE used to be emitted only inside the Gamma
    # branch, so this combination previously gave mechanism_consistent=True.
    kwargs = full_valid_kwargs()
    kwargs["source_claim"] = make_source_claim(rule=kwargs["rule"], code="MISSING_SOURCE")
    kwargs["source_claim"].pop("winning_market_id", None)
    kwargs["gamma_comparator"] = None
    result = build_review_packet(**kwargs)
    must(result.gamma_comparison == UNAVAILABLE)
    must("SOURCE_WINNER_UNAVAILABLE" in result.violations)
    must(result.mechanism_consistent is False)
    assert_authority_false(result)


def test_station_identity_binding_still_checked_on_refusal_source_claim_code():
    # Station/target_date identity binding must not be skipped just because
    # the source claim's code is a refusal rather than SYNTHETIC_DERIVATION_ONLY.
    kwargs = full_valid_kwargs()
    claim = make_source_claim(rule=kwargs["rule"], code="MISSING_SOURCE")
    claim.pop("winning_market_id", None)
    claim["station"] = "KXYZ"
    kwargs["source_claim"] = claim
    result = build_review_packet(**kwargs)
    must("STATION_MISMATCH" in result.violations)
    must(result.mechanism_consistent is False)
    assert_authority_false(result)


def test_no_input_can_ever_set_an_authority_flag_true():
    """Adversarial: try every available lever to force an authority flag True.

    This actively attempts to break the hardcoded-False guarantee: a
    source_claim that lies about its own authority flags, a gamma_comparator
    and rule_receipt/decision/capture carrying spoofed authority-looking keys,
    and a direct attempt to pass the dataclass fields as constructor kwargs.
    None of this may ever result in a True value on the output.
    """
    rule = make_rule()
    lying_source_claim = {
        "version": SOURCE_CONTRACT_VERSION, "code": "SYNTHETIC_DERIVATION_ONLY",
        "rule_fingerprint_sha256": rule.sha256,
        "synthetic_mechanism_only": False, "independent_label_attestation": True,
        "settlement_authority": True, "financial_authority": True,
        "automatic_promotion": True, "qualified": True, "calibration_authority": True,
        "winning_market_id": "m1", "station": STATION, "target_date": TARGET_DATE,
        "raw_sha256": "d" * 64,
    }
    spoofed_decision = make_decision(rule)
    spoofed_decision["independent_label_attestation"] = True
    spoofed_decision["qualified"] = True
    spoofed_rule_receipt = make_rule_receipt(rule)
    spoofed_rule_receipt["financial_authority"] = True
    spoofed_gamma = make_gamma()
    spoofed_gamma["settlement_authority"] = True
    spoofed_gamma["automatic_promotion"] = True

    result = build_review_packet(
        rule=rule, rule_receipt=spoofed_rule_receipt, decision=spoofed_decision,
        capture=make_capture(), disclosures=(make_disclosure(),),
        source_claim=lying_source_claim, gamma_comparator=spoofed_gamma,
    )
    # The lie must itself be caught as a structural violation, not silently
    # absorbed or upgraded into real authority.
    must("SOURCE_CLAIM_AUTHORITY_VIOLATION" in result.violations)
    must(result.mechanism_consistent is False)
    assert_authority_false(result)

    # Every authority-shaped field on the dataclass is a literal-default,
    # keyword-only-less field: attempting to pass it as a constructor
    # argument to the public builder is a TypeError, not a path to True.
    for flag_name in ("independent_label_attestation", "settlement_authority",
                      "calibration_authority", "financial_authority",
                      "automatic_promotion", "qualified"):
        with pytest.raises(TypeError):
            build_review_packet(
                rule=rule, rule_receipt=make_rule_receipt(rule), decision=make_decision(rule),
                capture=make_capture(), disclosures=(make_disclosure(),),
                source_claim=make_source_claim(rule=rule), gamma_comparator=make_gamma(),
                **{flag_name: True},
            )

    # And constructing the dataclass directly still defaults every one of
    # those fields to a literal False, confirmed by field introspection
    # rather than by trusting any particular call site.
    for f in dataclasses.fields(ReviewPacket):
        if f.name in ("independent_label_attestation", "settlement_authority",
                      "calibration_authority", "financial_authority",
                      "automatic_promotion", "qualified"):
            must(f.default is False)
    direct = ReviewPacket(version="x", event_id=None, station=None, target_date=None,
                          rule_fingerprint_sha256=None, source_claim={}, gamma_comparison=UNAVAILABLE,
                          violations=(), mechanism_consistent=True)
    assert_authority_false(direct)

    # The guarantee must hold for the type itself, not only the public
    # builder: dataclasses.replace(...) bypasses build_review_packet
    # entirely, so __post_init__ must independently refuse a True flag.
    with pytest.raises(ValueError):
        dataclasses.replace(direct, qualified=True)
    # source_claim must also be read-only once the packet is constructed,
    # not a mutable shallow-copied dict a caller could silently alter.
    with pytest.raises(TypeError):
        direct.source_claim["new_key"] = "x"  # MappingProxyType rejects writes


def test_empty_disclosures_tuple_flags_missing_evidence():
    # F1: with no label-determining receipt at all there is nothing to check
    # for lookahead, but that must be an explicit violation, not silence.
    kwargs = full_valid_kwargs()
    kwargs["disclosures"] = ()
    result = build_review_packet(**kwargs)
    must("DISCLOSURE_MISSING" in result.violations)
    must(result.mechanism_consistent is False)
    assert_authority_false(result)


def test_source_raw_sha256_not_bound_to_any_disclosure():
    # F2: a disclosure that passes the lookahead check (strictly after
    # decision/capture) but whose raw_sha256 has nothing to do with the
    # source claim's own raw_sha256 must not be accepted as evidence for
    # the bytes that actually produced the winner.
    kwargs = full_valid_kwargs()
    kwargs["disclosures"] = (make_disclosure(raw_sha256="f" * 64),)
    result = build_review_packet(**kwargs)
    must("LOOKAHEAD_VIOLATION" not in result.violations)
    must("SOURCE_DISCLOSURE_UNBOUND" in result.violations)
    must(result.mechanism_consistent is False)
    assert_authority_false(result)


def test_winner_outside_rule_partition_rejected():
    # F4: a "ghost" market id that both the source claim and Gamma happen to
    # agree on is not a real winner unless it is actually a member of the
    # rule's own bucket partition.
    kwargs = full_valid_kwargs()
    claim = make_source_claim(rule=kwargs["rule"], winning_market_id="ghost")
    kwargs["source_claim"] = claim
    kwargs["gamma_comparator"] = make_gamma(winning_market_id="ghost")
    result = build_review_packet(**kwargs)
    must("WINNER_NOT_IN_PARTITION" in result.violations)
    must(result.gamma_comparison != MATCH)
    must(result.mechanism_consistent is False)
    assert_authority_false(result)


def test_winning_yes_token_mismatch_against_bucket_rejected():
    # F4: winning_yes_token must match the winning bucket's own yes_token,
    # not just be accepted as whatever the source claim happens to assert.
    kwargs = full_valid_kwargs()
    claim = make_source_claim(rule=kwargs["rule"], winning_market_id="m1")
    claim["winning_yes_token"] = "y0"  # m1's real yes_token is "y1"
    kwargs["source_claim"] = claim
    result = build_review_packet(**kwargs)
    must("WINNING_TOKEN_MISMATCH" in result.violations)
    must(result.mechanism_consistent is False)
    assert_authority_false(result)


def test_incomplete_rule_identity_flagged():
    # F5: a rule payload missing one of its own identity fields (here,
    # timezone) must not silently skip the matching checks that depend on
    # it -- it must be an explicit violation.
    p = {"event_id": EVENT_ID, "station": STATION, "target_date": TARGET_DATE,
         "unit": UNIT, "partition": deepcopy(PARTITION)}  # timezone omitted
    rule = RuleFingerprint(canonical(p), digest(p), "a" * 64)
    kwargs = full_valid_kwargs()
    kwargs["rule"] = rule
    kwargs["rule_receipt"] = make_rule_receipt(rule)
    kwargs["decision"] = make_decision(rule)
    kwargs["source_claim"] = make_source_claim(rule=rule)
    result = build_review_packet(**kwargs)
    must("RULE_IDENTITY_INCOMPLETE" in result.violations)
    must(result.mechanism_consistent is False)
    assert_authority_false(result)


def test_exact_duplicate_disclosure_receipt_flagged():
    # F6: an exactly-duplicated receipt (same content, not just same id) is
    # deliberately flagged rather than silently tolerated, even though it is
    # harmless on its own -- distinct from DISCLOSURE_CONFLICT, which is for
    # genuinely conflicting duplicates.
    kwargs = full_valid_kwargs()
    first = make_disclosure(id_="dup-exact", seq=20, recorded_at=2000.0, raw_sha256="c" * 64)
    exact_again = make_disclosure(id_="dup-exact", seq=20, recorded_at=2000.0, raw_sha256="c" * 64)
    kwargs["disclosures"] = (first, exact_again)
    result = build_review_packet(**kwargs)
    must("DISCLOSURE_DUPLICATED" in result.violations)
    must("DISCLOSURE_CONFLICT" not in result.violations)
    assert_authority_false(result)


def test_rule_receipt_same_seq_and_time_as_decision_rejected():
    # F7: "predate" must mean strictly before, matching the strict lookahead
    # check used for disclosures -- a receipt at the exact same seq/time as
    # the decision is not a predate.
    kwargs = full_valid_kwargs()
    kwargs["rule_receipt"] = make_rule_receipt(kwargs["rule"], seq=10, recorded_at=1000.0)
    result = build_review_packet(**kwargs)
    must("RULE_RECEIPT_NOT_BEFORE_DECISION" in result.violations)
    must(result.mechanism_consistent is False)
    assert_authority_false(result)
