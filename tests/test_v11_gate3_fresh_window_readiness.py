"""Synthetic, offline tests for the Gate 3 fresh-window preflight
*preparation* readiness planner.

No test here creates a socket, imports an HTTP/provider client, or performs a
DNS lookup. Positive-path fixtures are clearly synthetic/offline identities
(``synthetic://``-prefixed paths and fabricated sha256 values) and never
confer real execution authority, provider rights or G3-L credit.
"""

import copy
import hashlib
import json
from collections.abc import Mapping as _ABCMapping
from dataclasses import replace

import pytest

from tools.v11_gate3_evidence_preflight_checker import (
    ClockObservation,
    NULLABLE_PREREQS,
    OWNER_REF_KEYS,
    PREREQ_KEYS,
    REF_KEYS_NO_REPO,
    ResourceObservation,
    RESTRICTIONS_SCHEMA_NAME,
    strict_json_loads,
    WINDOW_KEYS,
)
from tools.v11_gate3_fresh_window_readiness import (
    ALLOWED_OUTCOMES,
    ELIGIBILITY_LABEL,
    FORBIDDEN_OUTCOME_LABELS,
    FreshWindowReadinessResult,
    OUTCOME_CANDIDATE,
    OUTCOME_INCOMPLETE,
    OUTCOME_REFUSED,
    SCHEMA,
    _bound_diagnostic_output,
    evaluate_fresh_window_readiness,
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _ref(tag: str) -> dict:
    data = tag.encode()
    return {"sha256": _sha(data), "byte_length": len(data), "path": f"synthetic://{tag}"}


OWNER_REF = {
    "byte_length": 5,
    "path": "synthetic://owner-directive",
    "qualification": "OWNER_INSTRUCTION_ONLY_NOT_PROVIDER_RIGHTS",
    "sha256": _sha(b"owner"),
}

FRESH_WINDOW = {
    "automatic_roll_forward": False,
    "dispatch_not_before_utc": "2026-10-05T10:00:00Z",
    "expires_utc": "2026-10-05T13:30:00Z",
}

EXPIRED_WINDOW = {
    "automatic_roll_forward": False,
    "dispatch_not_before_utc": "2026-10-02T10:00:00Z",
    "expires_utc": "2026-10-02T13:30:00Z",
}

NOW_UTC = "2026-10-02T12:00:00Z"

GOOD_CLOCK = ClockObservation(
    measured_utc=NOW_UTC,
    uncertainty_seconds=0.3,
    calibration_age_seconds=10.0,
    monotonic_consistent=True,
)
GOOD_RESOURCES = ResourceObservation(
    free_disk_bytes_after_reservation=3_300_000_000,
    mem_available_bytes_after_reservation=600_000_000,
    physically_reserved_bytes=67_108_864,
)
GOOD_STORAGE_QUALIFICATION = {
    "live_ledger_created": True,
    "persistence_review": _ref("persistence-review"),
    "physically_reserved_bytes": 67_108_864,
}


def _empty_prerequisites() -> dict:
    return {k: None for k in PREREQ_KEYS}


def _full_prerequisites() -> dict:
    prereqs = {k: _ref(k) for k in NULLABLE_PREREQS}
    prereqs["owner_directive_original_record"] = dict(OWNER_REF)
    return prereqs


def _synthetic_restrictions_raw() -> bytes:
    with open("docs/V11_R09_GATE3_A5A6_OFFLINE_AUDIT_20261001.json", "rb") as source:
        retained_records = strict_json_loads(source.read())["restrictions"]
    restrictions = {
        "complete_lineage_review": _ref("lineage-review"),
        "execution_authority": False,
        "inventory_audit_ref": _ref("inventory-audit"),
        "known_control_domains": {
            "ECMWF": {
                "models": ["IFS", "AIFS"],
                "origins": ["https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com", "https://data.ecmwf.int"],
                "resumption_review": None,
                "status": "HELD",
            },
            "GEFS": {
                "origins": ["https://noaa-gefs-pds.s3.amazonaws.com"],
                "scope_independence_review": _ref("gefs-scope-review"),
                "status": "SCOPE_INDEPENDENCE_CONFIRMED",
            },
        },
        "raw_source_refs": [_ref("raw-source-0")],
        "records": retained_records,
        "schema": RESTRICTIONS_SCHEMA_NAME,
        "shared_history_head": _sha(b"synthetic-history-head"),
        "status": "RECONCILED_SYNTHETIC_FIXTURE",
        "unresolved_attempt_reconciliation": _ref("unresolved-reconciliation"),
    }
    return json.dumps(restrictions).encode()


def _call(**overrides):
    kwargs = dict(
        proposed_window=FRESH_WINDOW,
        prerequisites=_full_prerequisites(),
        restrictions_raw=_synthetic_restrictions_raw(),
        storage_qualification=GOOD_STORAGE_QUALIFICATION,
        now_utc=NOW_UTC,
        clock=GOOD_CLOCK,
        resources=GOOD_RESOURCES,
    )
    kwargs.update(overrides)
    return evaluate_fresh_window_readiness(**kwargs)


# -- Positive path -------------------------------------------------------

def test_fully_satisfied_synthetic_inputs_are_blocked_on_three_standing_gaps():
    """Every structural/safety check passes and every prerequisite reference
    is present, but ``PREPARATION_CANDIDATE_*`` remains unreachable: no
    reviewed window duration/horizon ceiling, resource magnitude ceiling, or
    clock provenance boundary exists anywhere in the standing protocol, so
    each is reported as its own standing, honestly-named blocker rather than
    silently treated as satisfied (independent-review findings R2/R3/R4)."""
    result = _call()
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.outcome in ALLOWED_OUTCOMES
    assert result.outcome not in FORBIDDEN_OUTCOME_LABELS
    assert result.window_is_fresh is False
    assert result.clock_ready is False
    assert result.storage_ready is False
    assert result.restriction_history_preserved is True
    assert result.missing_prerequisites == ()
    assert result.refusal_reasons == ()
    assert set(result.incompleteness_reasons) == {
        "NO_REVIEWED_WINDOW_DURATION_HORIZON_POLICY",
        "NO_REVIEWED_RESOURCE_MAGNITUDE_CEILING",
        "NO_REVIEWED_CLOCK_PROVENANCE_BOUNDARY",
    }
    assert result.eligibility == ELIGIBILITY_LABEL
    assert result.schema == SCHEMA


def test_result_to_dict_is_json_serializable():
    result = _call()
    encoded = json.dumps(result.to_dict())
    assert "PREPARATION_INCOMPLETE" in encoded


def test_never_emits_a_forbidden_promotion_label():
    result = _call()
    assert set(ALLOWED_OUTCOMES).isdisjoint(FORBIDDEN_OUTCOME_LABELS)
    assert result.outcome not in FORBIDDEN_OUTCOME_LABELS


# -- Refusal tier: safety-relevant structural problems -------------------

def test_refuses_identical_expired_window_as_silent_roll_forward():
    result = _call(proposed_window=EXPIRED_WINDOW, now_utc="2026-10-02T09:00:00Z")
    assert result.outcome == OUTCOME_REFUSED
    assert "IDENTICAL_TO_EXPIRED_WINDOW_SILENT_ROLL_FORWARD" in result.refusal_reasons


def test_refuses_window_overlapping_expired_window_as_roll_forward():
    # Expired window expiry pushed one second later than the real frozen
    # expiry, with a stale now_utc so "not in the future" doesn't fire
    # first: still an overlap of the one real expired window.
    overlapping = dict(
        EXPIRED_WINDOW,
        expires_utc="2026-10-02T13:30:01Z",
    )
    result = _call(proposed_window=overlapping, now_utc="2026-10-01T00:00:00Z")
    assert result.outcome == OUTCOME_REFUSED
    assert "OVERLAPS_EXPIRED_WINDOW_SILENT_ROLL_FORWARD" in result.refusal_reasons


def test_refuses_window_not_in_the_future():
    past_window = {
        "automatic_roll_forward": False,
        "dispatch_not_before_utc": "2026-09-01T00:00:00Z",
        "expires_utc": "2026-09-01T03:00:00Z",
    }
    result = _call(proposed_window=past_window)
    assert result.outcome == OUTCOME_REFUSED
    assert "PROPOSED_WINDOW_NOT_IN_THE_FUTURE" in result.refusal_reasons


def test_refuses_window_not_ordered():
    backwards = {
        "automatic_roll_forward": False,
        "dispatch_not_before_utc": "2026-10-05T13:30:00Z",
        "expires_utc": "2026-10-05T10:00:00Z",
    }
    result = _call(proposed_window=backwards)
    assert result.outcome == OUTCOME_REFUSED
    assert "PROPOSED_WINDOW_NOT_ORDERED" in result.refusal_reasons


def test_refuses_automatic_roll_forward_true():
    rolled = dict(FRESH_WINDOW, automatic_roll_forward=True)
    result = _call(proposed_window=rolled)
    assert result.outcome == OUTCOME_REFUSED
    assert "AUTOMATIC_ROLL_FORWARD_MUST_BE_FALSE" in result.refusal_reasons


def test_refuses_unparseable_window_timestamp():
    broken = dict(FRESH_WINDOW, expires_utc="not-a-timestamp")
    result = _call(proposed_window=broken)
    assert result.outcome == OUTCOME_REFUSED
    assert "UNPARSEABLE_PROPOSED_WINDOW" in result.refusal_reasons


def test_refuses_unparseable_now_utc():
    result = _call(now_utc="not-a-timestamp")
    assert result.outcome == OUTCOME_REFUSED
    assert "UNPARSEABLE_NOW_UTC" in result.refusal_reasons


def test_refuses_too_many_keys_without_echoing_the_extra_window_key():
    # The schema is closed (3 keys); one extra key is already more than the
    # schema permits, so this is refused on cardinality alone before any
    # per-key reason (echoing or otherwise) is even considered.
    extra = dict(FRESH_WINDOW, unexpected="x")
    result = _call(proposed_window=extra)
    assert result.outcome == OUTCOME_REFUSED
    assert "TOO_MANY_KEYS:proposed_window" in result.refusal_reasons
    assert "unexpected" not in " ".join(result.refusal_reasons)


def test_refuses_same_cardinality_unknown_window_key_without_echoing_it():
    # Same key count as the schema (3), but one expected key is replaced by
    # an unknown one: cardinality alone cannot catch this, so the unknown-key
    # check must still refuse without ever embedding the caller's key text.
    swapped = {
        "dispatch_not_before_utc": FRESH_WINDOW["dispatch_not_before_utc"],
        "expires_utc": FRESH_WINDOW["expires_utc"],
        "PRIVATE_TOKEN_SENTINEL": False,
    }
    result = _call(proposed_window=swapped)
    assert result.outcome == OUTCOME_REFUSED
    assert "UNKNOWN_KEY:proposed_window" in result.refusal_reasons
    assert "MISSING_KEY:proposed_window.automatic_roll_forward" in result.refusal_reasons
    assert "PRIVATE_TOKEN_SENTINEL" not in " ".join(result.refusal_reasons)


def test_refuses_missing_window_key():
    incomplete_window = {"automatic_roll_forward": False, "dispatch_not_before_utc": FRESH_WINDOW["dispatch_not_before_utc"]}
    result = _call(proposed_window=incomplete_window)
    assert result.outcome == OUTCOME_REFUSED
    assert "MISSING_KEY:proposed_window.expires_utc" in result.refusal_reasons


def test_refuses_too_many_keys_without_echoing_the_extra_prerequisite_key():
    # The schema is closed (12 keys); one extra key already exceeds that,
    # so this is refused on cardinality alone, before any per-key reason.
    prereqs = _full_prerequisites()
    prereqs["unexpected_extra_prereq"] = _ref("extra")
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_REFUSED
    assert "TOO_MANY_KEYS:prerequisites" in result.refusal_reasons
    assert "unexpected_extra_prereq" not in " ".join(result.refusal_reasons)


def test_refuses_many_individually_admissible_extra_prerequisite_keys():
    # 1,000 distinct, individually valid-length extra keys must still be
    # refused on cardinality alone -- not iterated into 1,000 reasons.
    prereqs = _full_prerequisites()
    for i in range(1000):
        prereqs[f"extra_admissible_key_{i}" * 20] = "x"
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_REFUSED
    assert result.refusal_reasons == ("TOO_MANY_KEYS:prerequisites",)
    assert sum(len(r.encode("utf-8")) for r in result.refusal_reasons) < 1000


def test_refuses_missing_prerequisite_key():
    prereqs = _full_prerequisites()
    del prereqs["runtime_implementation_review"]
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_REFUSED
    assert "MISSING_KEY:prerequisites.runtime_implementation_review" in result.refusal_reasons


def test_refuses_invalid_restrictions_json():
    result = _call(restrictions_raw=b"{not json")
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_JSON:restrictions" in result.refusal_reasons


def test_refuses_oversized_restrictions_raw():
    oversized = b"{" + b" " * 1_048_577 + b"}"
    result = _call(restrictions_raw=oversized)
    assert result.outcome == OUTCOME_REFUSED
    assert "OVERSIZED_RAW_BYTES:restrictions" in result.refusal_reasons


def test_refuses_oversized_key_without_echoing_it():
    # Same cardinality as the schema (3 keys): one is replaced by a huge
    # key, so this exercises the unsafe-key check specifically, not the
    # cardinality check (covered separately above).
    huge_key = "x" * 5_000_000
    extra = {
        "dispatch_not_before_utc": FRESH_WINDOW["dispatch_not_before_utc"],
        "expires_utc": FRESH_WINDOW["expires_utc"],
        huge_key: "y",
    }
    result = _call(proposed_window=extra)
    assert result.outcome == OUTCOME_REFUSED
    assert "OVERSIZED_OR_INVALID_KEY:proposed_window" in result.refusal_reasons
    assert all(len(reason) < 1000 for reason in result.refusal_reasons)


def test_lone_surrogate_key_is_refused_without_echoing_it():
    # A lone UTF-16 surrogate code point is short (passes any length bound)
    # but cannot be UTF-8 encoded; it must still be caught and must never
    # be embedded verbatim into a reason string. Same cardinality as the
    # schema (3 keys), so this exercises the unsafe-key check, not
    # cardinality.
    extra = {
        "dispatch_not_before_utc": FRESH_WINDOW["dispatch_not_before_utc"],
        "expires_utc": FRESH_WINDOW["expires_utc"],
        "\ud800": "y",
    }
    result = _call(proposed_window=extra)
    assert result.outcome == OUTCOME_REFUSED
    assert "OVERSIZED_OR_INVALID_KEY:proposed_window" in result.refusal_reasons
    for reason in result.refusal_reasons:
        reason.encode("utf-8")


def test_oversized_storage_qualification_key_is_incomplete_not_echoed():
    # Same cardinality as the schema (3 keys): one is replaced by a huge
    # key, so this exercises the unsafe-key check specifically, not the
    # cardinality check.
    huge_key = "x" * 5_000_000
    bad_storage = dict(GOOD_STORAGE_QUALIFICATION)
    del bad_storage["live_ledger_created"]
    bad_storage[huge_key] = "y"
    result = _call(storage_qualification=bad_storage)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.storage_ready is False
    assert "OVERSIZED_OR_INVALID_KEY:storage_qualification" in result.incompleteness_reasons
    assert all(len(reason) < 1000 for reason in result.incompleteness_reasons)


def test_too_many_storage_qualification_keys_is_incomplete_not_echoed():
    bad_storage = dict(GOOD_STORAGE_QUALIFICATION, PRIVATE_TOKEN_SENTINEL="x" * 4000)
    result = _call(storage_qualification=bad_storage)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.storage_ready is False
    assert "TOO_MANY_KEYS:storage_qualification" in result.incompleteness_reasons
    assert "PRIVATE_TOKEN_SENTINEL" not in " ".join(result.incompleteness_reasons)


def test_same_cardinality_unknown_storage_qualification_key_not_echoed():
    bad_storage = dict(GOOD_STORAGE_QUALIFICATION)
    del bad_storage["live_ledger_created"]
    bad_storage["PRIVATE_TOKEN_SENTINEL"] = True
    result = _call(storage_qualification=bad_storage)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.storage_ready is False
    assert "UNKNOWN_KEY:storage_qualification" in result.incompleteness_reasons
    assert "PRIVATE_TOKEN_SENTINEL" not in " ".join(result.incompleteness_reasons)


def test_refuses_restrictions_raw_wrong_type():
    result = _call(restrictions_raw="not-bytes")
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_RESTRICTIONS_RAW_TYPE" in result.refusal_reasons


def test_refuses_proposed_window_wrong_type():
    result = _call(proposed_window=["not", "a", "mapping"])
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_PROPOSED_WINDOW_TYPE" in result.refusal_reasons


def test_refuses_prerequisites_wrong_type():
    result = _call(prerequisites=None)
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_PREREQUISITES_TYPE" in result.refusal_reasons


def test_refuses_clock_wrong_type():
    result = _call(clock="not-a-clock-observation")
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_CLOCK_OBSERVATION_TYPE" in result.refusal_reasons


def test_refuses_resources_wrong_type():
    result = _call(resources=object())
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_RESOURCE_OBSERVATION_TYPE" in result.refusal_reasons


def test_refuses_storage_qualification_wrong_type():
    result = _call(storage_qualification="not-a-mapping")
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_STORAGE_QUALIFICATION_TYPE" in result.refusal_reasons


# -- Incomplete tier: never fabricated, always reported ------------------

def test_fully_null_prerequisites_is_incomplete_not_refused():
    result = _call(prerequisites=_empty_prerequisites())
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.refusal_reasons == ()
    assert len(result.missing_prerequisites) == len(PREREQ_KEYS)
    for key in PREREQ_KEYS:
        assert f"NULL_PREREQUISITE:{key}" in result.incompleteness_reasons


def test_missing_clock_is_incomplete_and_never_fabricated():
    result = _call(clock=None)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.clock_ready is False
    assert "CLOCK_OBSERVATION_NOT_SUPPLIED" in result.incompleteness_reasons


def test_missing_resources_and_storage_qualification_is_incomplete():
    result = _call(resources=None, storage_qualification=None)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.storage_ready is False
    assert "RESOURCE_OBSERVATION_NOT_SUPPLIED" in result.incompleteness_reasons
    assert "STORAGE_QUALIFICATION_NOT_SUPPLIED" in result.incompleteness_reasons


def test_future_dated_clock_disagreeing_with_now_utc_is_incomplete():
    forged_clock = ClockObservation(
        measured_utc="2026-10-05T10:05:00Z",
        uncertainty_seconds=0.3,
        calibration_age_seconds=10.0,
        monotonic_consistent=True,
    )
    result = _call(clock=forged_clock)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.clock_ready is False
    assert "CLOCK_DISAGREES_WITH_NOW_UTC" in result.incompleteness_reasons


def test_honest_clock_within_uncertainty_of_now_utc_clears_quality_floors_only():
    """An honest, well-calibrated clock reading clears every quality floor
    (source, monotonicity, uncertainty, calibration age, agreement with
    ``now_utc``) -- none of those reasons appear -- but ``clock_ready``
    still reports False: agreement alone is consistency, not a reviewed
    provenance/authenticity guarantee (R4), so the standing
    ``NO_REVIEWED_CLOCK_PROVENANCE_BOUNDARY`` blocker remains."""
    honest_clock = ClockObservation(
        measured_utc=NOW_UTC,
        uncertainty_seconds=0.3,
        calibration_age_seconds=10.0,
        monotonic_consistent=True,
    )
    result = _call(clock=honest_clock)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.clock_ready is False
    assert result.incompleteness_reasons == (
        "NO_REVIEWED_CLOCK_PROVENANCE_BOUNDARY",
        "NO_REVIEWED_RESOURCE_MAGNITUDE_CEILING",
        "NO_REVIEWED_WINDOW_DURATION_HORIZON_POLICY",
    )
    for unwanted in (
        "INVALID_CLOCK_SOURCE", "NONMONOTONIC_CLOCK", "INVALID_CLOCK_UNCERTAINTY",
        "EXCESSIVE_CLOCK_UNCERTAINTY", "INVALID_CALIBRATION_AGE",
        "EXPIRED_CLOCK_CALIBRATION", "UNPARSEABLE_CLOCK", "CLOCK_DISAGREES_WITH_NOW_UTC",
    ):
        assert unwanted not in result.incompleteness_reasons


def test_insufficient_clock_uncertainty_is_incomplete():
    bad_clock = ClockObservation(
        measured_utc="2026-10-05T10:05:00Z",
        uncertainty_seconds=5.0,
        calibration_age_seconds=10.0,
        monotonic_consistent=True,
    )
    result = _call(clock=bad_clock)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.clock_ready is False
    assert "EXCESSIVE_CLOCK_UNCERTAINTY" in result.incompleteness_reasons


def test_insufficient_physical_storage_reservation_is_incomplete():
    low_storage = dict(GOOD_STORAGE_QUALIFICATION, physically_reserved_bytes=1024)
    low_resources = ResourceObservation(
        free_disk_bytes_after_reservation=3_300_000_000,
        mem_available_bytes_after_reservation=600_000_000,
        physically_reserved_bytes=1024,
    )
    result = _call(storage_qualification=low_storage, resources=low_resources)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.storage_ready is False
    assert "NO_PHYSICAL_STORAGE_RESERVATION" in result.incompleteness_reasons


def test_tampered_retained_denial_is_incomplete_not_silently_dropped():
    restrictions = strict_json_loads(_synthetic_restrictions_raw())
    restrictions["records"][0]["response"]["sha256"] = _sha(b"tampered")
    result = _call(restrictions_raw=json.dumps(restrictions).encode())
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.restriction_history_preserved is False
    assert "MISSING_OR_DUPLICATED_RETAINED_DENIAL" in result.incompleteness_reasons


def test_unresolved_gefs_lineage_is_incomplete_not_refused():
    restrictions = strict_json_loads(_synthetic_restrictions_raw())
    restrictions["known_control_domains"]["GEFS"]["status"] = "BLOCKED_UNKNOWN_LINEAGE_AND_SCOPE"
    result = _call(restrictions_raw=json.dumps(restrictions).encode())
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.restriction_history_preserved is False
    assert "GEFS_LINEAGE_UNRESOLVED" in result.incompleteness_reasons


def test_ecmwf_resumption_without_review_is_incomplete_holds_preserved():
    restrictions = strict_json_loads(_synthetic_restrictions_raw())
    restrictions["known_control_domains"]["ECMWF"]["status"] = "RESUMED"
    result = _call(restrictions_raw=json.dumps(restrictions).encode())
    assert result.outcome == OUTCOME_INCOMPLETE
    assert "ECMWF_RESUMPTION_UNREVIEWED" in result.incompleteness_reasons


def test_malformed_prerequisite_reference_is_incomplete():
    prereqs = _full_prerequisites()
    prereqs["runtime_implementation_review"] = {"not": "a-valid-ref"}
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert "MALFORMED_PREREQUISITE_REFERENCE:runtime_implementation_review" in result.incompleteness_reasons
    assert "runtime_implementation_review" in result.missing_prerequisites


def test_missing_owner_directive_qualification_is_incomplete():
    prereqs = _full_prerequisites()
    prereqs["owner_directive_original_record"] = dict(OWNER_REF, qualification="WRONG")
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert "MALFORMED_PREREQUISITE_REFERENCE:owner_directive_original_record" in result.incompleteness_reasons


# -- R1: bounded/redacted diagnostics for nested restriction domains ------

def _restrictions_with_ecmwf_override(**overrides) -> bytes:
    restrictions = json.loads(_synthetic_restrictions_raw())
    restrictions["known_control_domains"]["ECMWF"].update(overrides)
    return json.dumps(restrictions).encode()


def test_too_many_nested_ecmwf_domain_keys_is_incomplete_not_echoed():
    raw = _restrictions_with_ecmwf_override(PRIVATE_TOKEN_SENTINEL="x" * 900_000)
    result = _call(restrictions_raw=raw)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.restriction_history_preserved is False
    assert "TOO_MANY_KEYS:known_control_domains.ECMWF" in result.incompleteness_reasons
    assert "PRIVATE_TOKEN_SENTINEL" not in " ".join(result.incompleteness_reasons)
    assert "x" * 100 not in " ".join(result.incompleteness_reasons)
    assert all(len(reason) < 1000 for reason in result.incompleteness_reasons)


def test_same_cardinality_unknown_nested_gefs_domain_key_not_echoed():
    restrictions = json.loads(_synthetic_restrictions_raw())
    gefs = restrictions["known_control_domains"]["GEFS"]
    del gefs["status"]
    gefs["PRIVATE_TOKEN_SENTINEL"] = "x"
    raw = json.dumps(restrictions).encode()
    result = _call(restrictions_raw=raw)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.restriction_history_preserved is False
    assert "UNKNOWN_KEY:known_control_domains.GEFS" in result.incompleteness_reasons
    assert "PRIVATE_TOKEN_SENTINEL" not in " ".join(result.incompleteness_reasons)


def test_overall_diagnostic_output_is_bounded_across_many_simultaneous_issues():
    # Stack cardinality violations across every bounded mapping at once;
    # the aggregate serialized reason output must still stay far below the
    # raw-input byte cap, confirming redaction does not rely on that cap.
    extra_window = dict(FRESH_WINDOW, **{f"extra_{i}": "x" * 100 for i in range(50)})
    prereqs = _full_prerequisites()
    for i in range(50):
        prereqs[f"extra_prereq_{i}" * 10] = "x" * 100
    bad_storage = dict(GOOD_STORAGE_QUALIFICATION, **{f"extra_{i}": "x" * 100 for i in range(50)})
    raw = _restrictions_with_ecmwf_override(**{f"extra_{i}": "x" * 100 for i in range(50)})
    result = _call(
        proposed_window=extra_window, prerequisites=prereqs,
        storage_qualification=bad_storage, restrictions_raw=raw,
    )
    assert result.outcome == OUTCOME_REFUSED
    total_bytes = sum(len(r.encode("utf-8")) for r in result.refusal_reasons)
    assert total_bytes < 1_048_576
    assert total_bytes < 10_000


# -- R2: explicit duration/horizon policy, never an invented threshold ----

def test_absurd_hundred_year_duration_is_blocked_same_as_ordinary_window():
    result = _call(proposed_window={
        "automatic_roll_forward": False,
        "dispatch_not_before_utc": "2026-10-05T10:00:00Z",
        "expires_utc": "2126-10-05T10:00:00Z",
    })
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.window_is_fresh is False
    assert "NO_REVIEWED_WINDOW_DURATION_HORIZON_POLICY" in result.incompleteness_reasons


def test_extreme_future_horizon_is_blocked_same_as_ordinary_window():
    result = _call(proposed_window={
        "automatic_roll_forward": False,
        "dispatch_not_before_utc": "9999-01-01T10:00:00Z",
        "expires_utc": "9999-01-01T13:30:00Z",
    })
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.window_is_fresh is False
    assert "NO_REVIEWED_WINDOW_DURATION_HORIZON_POLICY" in result.incompleteness_reasons


def test_one_microsecond_duration_is_blocked_same_as_ordinary_window():
    result = _call(proposed_window={
        "automatic_roll_forward": False,
        "dispatch_not_before_utc": "2026-10-05T10:00:00Z",
        "expires_utc": "2026-10-05T10:00:00.000001Z",
    })
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.window_is_fresh is False
    assert "NO_REVIEWED_WINDOW_DURATION_HORIZON_POLICY" in result.incompleteness_reasons


def test_ordinary_duration_window_is_blocked_identically_proving_no_hidden_threshold():
    # The FRESH_WINDOW fixture (3.5 hours, near-term) is as reasonable a
    # window as this planner will ever see. It is blocked by the exact same
    # named reason as the absurd cases above -- proving the gap is a
    # genuine absence of reviewed policy, not a hidden threshold that
    # merely exempts "normal-looking" windows.
    result = _call()
    assert result.window_is_fresh is False
    assert "NO_REVIEWED_WINDOW_DURATION_HORIZON_POLICY" in result.incompleteness_reasons


# -- R3: integer magnitude/plausibility, never an invented ceiling --------

def test_resource_magnitude_boundary_values_all_blocked_identically():
    for disk_bytes in (
        2_147_483_648, 2_147_483_647 + 1, 2 ** 64, 10 ** 400,
    ):
        resources = ResourceObservation(
            free_disk_bytes_after_reservation=disk_bytes,
            mem_available_bytes_after_reservation=600_000_000,
            physically_reserved_bytes=67_108_864,
        )
        result = _call(resources=resources)
        assert result.storage_ready is False
        assert "NO_REVIEWED_RESOURCE_MAGNITUDE_CEILING" in result.incompleteness_reasons


def test_huge_reservation_still_requires_exact_equality_with_observation():
    # The standing reservation-agreement check is preserved: a huge
    # qualification reservation that does not match the resource
    # observation's own huge reservation is still flagged distinctly,
    # alongside (not instead of) the missing-ceiling blocker.
    huge = 2 ** 64
    storage_qualification = dict(GOOD_STORAGE_QUALIFICATION, physically_reserved_bytes=huge)
    resources = ResourceObservation(
        free_disk_bytes_after_reservation=3_300_000_000,
        mem_available_bytes_after_reservation=600_000_000,
        physically_reserved_bytes=huge + 1,
    )
    result = _call(storage_qualification=storage_qualification, resources=resources)
    assert result.storage_ready is False
    assert "INCONSISTENT_STORAGE_RESERVATION" in result.incompleteness_reasons
    assert "NO_REVIEWED_RESOURCE_MAGNITUDE_CEILING" in result.incompleteness_reasons


def test_huge_matching_reservation_passes_equality_but_still_blocked_on_ceiling():
    # A huge reservation that *does* agree between both claims satisfies the
    # standing equality/floor checks (no floor or equality reason fires),
    # but the missing-ceiling blocker alone still prevents storage_ready.
    huge = 2 ** 64
    storage_qualification = dict(GOOD_STORAGE_QUALIFICATION, physically_reserved_bytes=huge)
    resources = ResourceObservation(
        free_disk_bytes_after_reservation=3_300_000_000,
        mem_available_bytes_after_reservation=600_000_000,
        physically_reserved_bytes=huge,
    )
    result = _call(storage_qualification=storage_qualification, resources=resources)
    assert result.storage_ready is False
    assert result.incompleteness_reasons == (
        "NO_REVIEWED_CLOCK_PROVENANCE_BOUNDARY",
        "NO_REVIEWED_RESOURCE_MAGNITUDE_CEILING",
        "NO_REVIEWED_WINDOW_DURATION_HORIZON_POLICY",
    )


# -- R4: no trusted provenance boundary for a clock reading ---------------

def test_matching_stale_clock_pair_cannot_make_an_expired_looking_window_fresh():
    # Both now_utc and the clock's own measured_utc agree with each other
    # (internally consistent) but are a full day stale relative to the
    # actual review clock; the standing provenance blocker, not a silently
    # granted freshness, is what prevents this from reading as ready.
    stale_now = "2026-10-01T00:00:00Z"
    stale_clock = ClockObservation(
        measured_utc=stale_now,
        uncertainty_seconds=0.3,
        calibration_age_seconds=10.0,
        monotonic_consistent=True,
    )
    result = _call(now_utc=stale_now, clock=stale_clock, proposed_window={
        "automatic_roll_forward": False,
        "dispatch_not_before_utc": "2026-10-02T13:30:00Z",
        "expires_utc": "2026-10-02T17:00:00Z",
    })
    assert result.clock_ready is False
    assert "NO_REVIEWED_CLOCK_PROVENANCE_BOUNDARY" in result.incompleteness_reasons
    assert "CLOCK_DISAGREES_WITH_NOW_UTC" not in result.incompleteness_reasons


def test_matching_future_forged_clock_pair_is_also_blocked_on_provenance():
    future_now = "9999-01-01T00:00:00Z"
    future_clock = ClockObservation(
        measured_utc=future_now,
        uncertainty_seconds=0.3,
        calibration_age_seconds=10.0,
        monotonic_consistent=True,
    )
    result = _call(now_utc=future_now, clock=future_clock, proposed_window={
        "automatic_roll_forward": False,
        "dispatch_not_before_utc": "9999-01-02T10:00:00Z",
        "expires_utc": "9999-01-02T13:30:00Z",
    })
    assert result.clock_ready is False
    assert "NO_REVIEWED_CLOCK_PROVENANCE_BOUNDARY" in result.incompleteness_reasons
    assert "CLOCK_DISAGREES_WITH_NOW_UTC" not in result.incompleteness_reasons


# -- F1/F2: bounded pre-copy / pre-set-construction cardinality guards ----
#
# Independent Astra/high review of 6af4633 found that the outer closed-key
# guard (_check_closed_bounded) and the nested reference guard (_is_ref via
# _check_storage) ran only after dict(proposed_window)/dict(prerequisites)/
# dict(storage_qualification) had already copied the whole caller-supplied
# mapping, and that a nested direct reference (any prerequisite, or
# storage_qualification["persistence_review"]) could still be an arbitrarily
# large dict reaching the frozen _is_ref's internal set(v.keys()) unbounded.
# These regressions prove both are now refused on a single len() comparison
# alone, before any copy, full iteration, or key-set construction.

class _CountingOversizedMapping(dict):
    """A ``dict`` subclass that declares an arbitrarily large cardinality
    via ``__len__`` while actually holding zero entries, and counts every
    call to the iteration/lookup protocol a full copy or key-set
    construction (``dict(obj)``, ``set(obj.keys())``, ``for k in obj``)
    would use. If cardinality is checked (via ``len()``) before any of
    those, every counter stays at 0 after the call returns.
    """

    def __init__(self, declared_len: int):
        super().__init__()
        self._declared_len = declared_len
        self.iter_calls = 0
        self.keys_calls = 0
        self.getitem_calls = 0

    def __len__(self):
        return self._declared_len

    def __iter__(self):
        self.iter_calls += 1
        return super().__iter__()

    def keys(self):
        self.keys_calls += 1
        return super().keys()

    def __getitem__(self, key):
        self.getitem_calls += 1
        return super().__getitem__(key)


def test_oversized_counting_proposed_window_is_refused_without_copy_or_iteration():
    # A dict *subclass* -- even one that honestly reports an oversized
    # length -- is refused on the exact-built-in-dict type boundary itself
    # (Astra/high F1 re-review of 6af4633: a subclass's len()/keys()/
    # __iter__ are all independently overridable, so a declared length can
    # never be trusted). The guard never even reaches len().
    counting = _CountingOversizedMapping(100_000)
    result = _call(proposed_window=counting)
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_PROPOSED_WINDOW_TYPE" in result.refusal_reasons
    assert counting.iter_calls == 0
    assert counting.keys_calls == 0
    assert counting.getitem_calls == 0


def test_oversized_counting_prerequisites_is_refused_without_copy_or_iteration():
    counting = _CountingOversizedMapping(100_000)
    result = _call(prerequisites=counting)
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_PREREQUISITES_TYPE" in result.refusal_reasons
    assert counting.iter_calls == 0
    assert counting.keys_calls == 0
    assert counting.getitem_calls == 0


def test_oversized_counting_storage_qualification_is_refused_without_copy_or_iteration():
    # Unlike proposed_window/prerequisites, storage_qualification's
    # oversized-dict-subclass case used to surface as readiness-tier
    # INCOMPLETE (via _unsafe_untrusted_submapping's own, now-removed,
    # isinstance(..., Mapping) check). The exact-dict type boundary is a
    # structural precondition, exactly like passing a non-mapping type, so
    # it is now refused at the same structural tier as that case.
    counting = _CountingOversizedMapping(100_000)
    result = _call(storage_qualification=counting)
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_STORAGE_QUALIFICATION_TYPE" in result.refusal_reasons
    assert counting.iter_calls == 0
    assert counting.keys_calls == 0
    assert counting.getitem_calls == 0


def test_ordinary_hundred_thousand_key_proposed_window_refused_with_bounded_output():
    huge = dict(FRESH_WINDOW, **{f"extra_{i}": "x" for i in range(100_000)})
    result = _call(proposed_window=huge)
    assert result.outcome == OUTCOME_REFUSED
    assert result.refusal_reasons == ("TOO_MANY_KEYS:proposed_window",)
    assert sum(len(r.encode("utf-8")) for r in result.refusal_reasons) < 1000


def test_ordinary_hundred_thousand_key_prerequisites_refused_with_bounded_output():
    prereqs = _full_prerequisites()
    for i in range(100_000):
        prereqs[f"extra_{i}"] = "x"
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_REFUSED
    assert result.refusal_reasons == ("TOO_MANY_KEYS:prerequisites",)
    assert sum(len(r.encode("utf-8")) for r in result.refusal_reasons) < 1000


def test_ordinary_hundred_thousand_key_storage_qualification_incomplete_with_bounded_output():
    bad_storage = dict(GOOD_STORAGE_QUALIFICATION, **{f"extra_{i}": "x" for i in range(100_000)})
    result = _call(storage_qualification=bad_storage)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.storage_ready is False
    assert "TOO_MANY_KEYS:storage_qualification" in result.incompleteness_reasons
    assert "NO_REVIEWED_RESOURCE_MAGNITUDE_CEILING" not in result.incompleteness_reasons
    assert sum(len(r.encode("utf-8")) for r in result.incompleteness_reasons) < 1000


def test_oversized_counting_nested_prerequisite_reference_is_incomplete_without_key_set():
    # A schema-sized prerequisite bundle (12 keys, none extra) whose single
    # direct reference value is an arbitrarily large counting mapping: the
    # outer _check_closed_bounded guard cannot catch this (prerequisites
    # itself is not oversized), so the nested guard must.
    counting = _CountingOversizedMapping(100_000)
    prereqs = _full_prerequisites()
    prereqs["runtime_implementation_review"] = counting
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert "MALFORMED_PREREQUISITE_REFERENCE:runtime_implementation_review" in result.incompleteness_reasons
    assert "runtime_implementation_review" in result.missing_prerequisites
    # _is_ref's internal set(v.keys()) was never reached: keys()/__iter__
    # were never called on the oversized reference.
    assert counting.keys_calls == 0
    assert counting.iter_calls == 0


def test_oversized_counting_owner_reference_is_incomplete_without_key_set():
    counting = _CountingOversizedMapping(100_000)
    prereqs = _full_prerequisites()
    prereqs["owner_directive_original_record"] = counting
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert "MALFORMED_PREREQUISITE_REFERENCE:owner_directive_original_record" in result.incompleteness_reasons
    assert "owner_directive_original_record" in result.missing_prerequisites
    assert counting.keys_calls == 0
    assert counting.iter_calls == 0


def test_oversized_counting_storage_persistence_review_is_incomplete_without_key_set():
    counting = _CountingOversizedMapping(100_000)
    storage_qualification = dict(GOOD_STORAGE_QUALIFICATION, persistence_review=counting)
    result = _call(storage_qualification=storage_qualification)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.storage_ready is False
    assert "OVERSIZED_PREREQUISITE_REFERENCE:storage_qualification.persistence_review" in result.incompleteness_reasons
    assert "MISSING_STORAGE_PERSISTENCE_REVIEW" in result.incompleteness_reasons
    assert counting.keys_calls == 0
    assert counting.iter_calls == 0


def test_ordinary_hundred_thousand_key_nested_prerequisite_reference_is_incomplete():
    huge_ref = {f"extra_{i}": "x" for i in range(100_000)}
    prereqs = _full_prerequisites()
    prereqs["runtime_implementation_review"] = huge_ref
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert "MALFORMED_PREREQUISITE_REFERENCE:runtime_implementation_review" in result.incompleteness_reasons
    assert "runtime_implementation_review" in result.missing_prerequisites


def test_ordinary_hundred_thousand_key_owner_reference_is_incomplete():
    huge_ref = {f"extra_{i}": "x" for i in range(100_000)}
    prereqs = _full_prerequisites()
    prereqs["owner_directive_original_record"] = huge_ref
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert "MALFORMED_PREREQUISITE_REFERENCE:owner_directive_original_record" in result.incompleteness_reasons
    assert "owner_directive_original_record" in result.missing_prerequisites


def test_ordinary_hundred_thousand_key_storage_persistence_review_is_incomplete():
    huge_ref = {f"extra_{i}": "x" for i in range(100_000)}
    storage_qualification = dict(GOOD_STORAGE_QUALIFICATION, persistence_review=huge_ref)
    result = _call(storage_qualification=storage_qualification)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.storage_ready is False
    assert "OVERSIZED_PREREQUISITE_REFERENCE:storage_qualification.persistence_review" in result.incompleteness_reasons
    assert "MISSING_STORAGE_PERSISTENCE_REVIEW" in result.incompleteness_reasons


# -- Astra/high re-review of 6af4633 (commit 947bf68): adversarial
# Mapping/dict-subclass regressions ---------------------------------------
#
# That review found the prior commit's len()-based cardinality guard
# (F1/F2 above) trusted an overridable protocol: a Mapping or dict subclass
# can report one length via len() while __iter__ yields a different number
# of entries, or can expose a different key set via keys() than via
# __iter__, so a declared-safe object could still drive unbounded work
# (outer surfaces) or reach the frozen _is_ref's internal set(v.keys())
# uncapped (the fourteen direct reference paths: all twelve nullable
# prerequisite references, the owner reference, and
# storage_qualification["persistence_review"]). These mirror the retained
# review's own probe objects (/tmp/alpha-v11-947bf68-mapping-probes.py:
# Underreported, SplitView, UnderreportedDict) and prove the repair is now
# an exact-built-in-dict type boundary (type(v) is dict), not a len()
# comparison: not one of these objects' overridable methods is ever called.

class _UnderreportedMapping(_ABCMapping):
    """Declares a small, schema-matching length via __len__ while __iter__
    actually yields many more distinct, individually valid short-string
    keys -- the retained review's ``Underreported`` probe object, which
    previously drove full enumeration of every outer surface despite its
    declared length matching the schema."""

    def __init__(self, declared_len, real_len):
        self._declared_len = declared_len
        self._real_len = real_len
        self.iter_calls = 0
        self.getitem_calls = 0

    def __len__(self):
        return self._declared_len

    def __iter__(self):
        self.iter_calls += 1
        for i in range(self._real_len):
            yield f"k{i}"

    def __getitem__(self, key):
        self.getitem_calls += 1
        raise KeyError(key)


def test_underreported_mapping_proposed_window_is_refused_without_iteration():
    obj = _UnderreportedMapping(len(WINDOW_KEYS), 100_000)
    result = _call(proposed_window=obj)
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_PROPOSED_WINDOW_TYPE" in result.refusal_reasons
    assert obj.iter_calls == 0
    assert obj.getitem_calls == 0


def test_underreported_mapping_prerequisites_is_refused_without_iteration():
    obj = _UnderreportedMapping(len(PREREQ_KEYS), 100_000)
    result = _call(prerequisites=obj)
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_PREREQUISITES_TYPE" in result.refusal_reasons
    assert obj.iter_calls == 0
    assert obj.getitem_calls == 0


def test_underreported_mapping_storage_qualification_is_refused_without_iteration():
    obj = _UnderreportedMapping(3, 100_000)
    result = _call(storage_qualification=obj)
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_STORAGE_QUALIFICATION_TYPE" in result.refusal_reasons
    assert obj.iter_calls == 0
    assert obj.getitem_calls == 0


class _SplitViewMapping(_ABCMapping):
    """``__len__`` and ``__iter__`` honestly report only the real,
    schema-sized storage-qualification entries, but ``keys()`` -- the view
    ``dict(mapping)`` actually consumes -- yields additional
    private-sentinel entries. The retained review's ``SplitView`` probe
    object: a guard that only inspects ``__iter__``/``len()`` never
    notices the extra ``keys()``-only entries, so ``dict(storage_
    qualification)`` would previously copy and echo them."""

    def __init__(self, base, extra):
        self._data = dict(base)
        self._extra = extra
        self.iter_calls = 0
        self.keys_calls = 0

    def __len__(self):
        return len(self._data)

    def __iter__(self):
        self.iter_calls += 1
        return iter(self._data)

    def __getitem__(self, key):
        return self._data[key]

    def keys(self):
        self.keys_calls += 1
        yield from self._data
        for i in range(self._extra):
            yield f"REVIEW_PRIVATE_SENTINEL_{i}"


def test_split_view_storage_qualification_is_refused_keys_view_never_consumed():
    obj = _SplitViewMapping(GOOD_STORAGE_QUALIFICATION, 16_000)
    result = _call(storage_qualification=obj)
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_STORAGE_QUALIFICATION_TYPE" in result.refusal_reasons
    assert obj.keys_calls == 0
    assert obj.iter_calls == 0
    encoded = json.dumps(result.to_dict())
    assert "REVIEW_PRIVATE_SENTINEL" not in encoded
    assert len(encoded.encode("utf-8")) < 1000


class _LyingLenDict(dict):
    """A real ``dict`` *subclass* whose only override is ``__len__``,
    reporting a small schema-sized length while actually holding many real
    entries -- the retained review's ``UnderreportedDict`` probe object.
    Proves the guard in front of the frozen ``_is_ref`` (whose first step
    is an unbounded ``set(v.keys())`` copy) is the exact-dict type check,
    not a ``len()`` comparison: ``__len__`` here is never even called."""

    def __init__(self, data, declared_len):
        super().__init__(data)
        self._declared_len = declared_len

    def __len__(self):
        raise AssertionError("len() must never be called on a non-exact-dict reference")


def _lying_ref(declared_len, real_len=100_000):
    return _LyingLenDict({f"extra_{i}": "x" for i in range(real_len)}, declared_len)


@pytest.mark.parametrize("key", sorted(NULLABLE_PREREQS))
def test_lying_len_dict_subclass_nullable_reference_is_incomplete_len_never_called(key):
    prereqs = _full_prerequisites()
    prereqs[key] = _lying_ref(declared_len=len(REF_KEYS_NO_REPO))
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert f"MALFORMED_PREREQUISITE_REFERENCE:{key}" in result.incompleteness_reasons
    assert key in result.missing_prerequisites


def test_lying_len_dict_subclass_owner_reference_is_incomplete_len_never_called():
    prereqs = _full_prerequisites()
    prereqs["owner_directive_original_record"] = _lying_ref(declared_len=len(OWNER_REF_KEYS))
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert "MALFORMED_PREREQUISITE_REFERENCE:owner_directive_original_record" in result.incompleteness_reasons
    assert "owner_directive_original_record" in result.missing_prerequisites


def test_lying_len_dict_subclass_persistence_review_is_incomplete_len_never_called():
    storage_qualification = dict(
        GOOD_STORAGE_QUALIFICATION,
        persistence_review=_lying_ref(declared_len=len(REF_KEYS_NO_REPO)),
    )
    result = _call(storage_qualification=storage_qualification)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.storage_ready is False
    assert "OVERSIZED_PREREQUISITE_REFERENCE:storage_qualification.persistence_review" in result.incompleteness_reasons
    assert "MISSING_STORAGE_PERSISTENCE_REVIEW" in result.incompleteness_reasons


class _HostileText(str):
    def __len__(self):
        raise AssertionError("subclass length invoked")

    def __iter__(self):
        raise AssertionError("subclass iteration invoked")

    def __eq__(self, other):
        raise AssertionError("subclass equality invoked")

    def __ne__(self, other):
        raise AssertionError("subclass inequality invoked")

    def encode(self, *args, **kwargs):
        raise AssertionError("subclass encoding invoked")

    def replace(self, *args, **kwargs):
        raise AssertionError("subclass replacement invoked")

    __hash__ = str.__hash__


@pytest.mark.parametrize("surface", ("proposed_window", "prerequisites", "storage_qualification"))
def test_hostile_outer_key_is_refused_without_calling_string_protocols(surface):
    obj = dict({
        "proposed_window": FRESH_WINDOW,
        "prerequisites": _full_prerequisites(),
        "storage_qualification": GOOD_STORAGE_QUALIFICATION,
    }[surface])
    del obj[next(iter(obj))]
    obj[_HostileText("REVIEW_PRIVATE_SENTINEL")] = None
    result = _call(**{surface: obj})
    reasons = result.refusal_reasons if surface != "storage_qualification" else result.incompleteness_reasons
    assert "OVERSIZED_OR_INVALID_KEY:" + surface in reasons
    assert "REVIEW_PRIVATE_SENTINEL" not in json.dumps(result.to_dict())


@pytest.mark.parametrize("path", sorted(PREREQ_KEYS) + ["storage_qualification.persistence_review"])
@pytest.mark.parametrize("field", ("sha256", "byte_length", "path"))
def test_hostile_reference_leaf_is_rejected_on_every_reference_path(path, field):
    prereqs = _full_prerequisites()
    storage = copy.deepcopy(GOOD_STORAGE_QUALIFICATION)
    if path.startswith("storage_qualification."):
        ref = storage["persistence_review"]
    else:
        ref = prereqs[path]
    ref[field] = _HostileText("REVIEW_PRIVATE_SENTINEL")
    result = _call(prerequisites=prereqs, storage_qualification=storage)
    assert result.outcome == OUTCOME_INCOMPLETE
    if path.startswith("storage_qualification."):
        assert "MALFORMED_PREREQUISITE_REFERENCE:" + path in result.incompleteness_reasons
        assert "MISSING_STORAGE_PERSISTENCE_REVIEW" in result.incompleteness_reasons
    else:
        assert path in result.missing_prerequisites
        assert "MALFORMED_PREREQUISITE_REFERENCE:" + path in result.incompleteness_reasons
    assert "REVIEW_PRIVATE_SENTINEL" not in json.dumps(result.to_dict())


def test_hostile_reference_key_and_owner_qualification_are_rejected():
    prereqs = _full_prerequisites()
    owner = prereqs["owner_directive_original_record"]
    del owner["path"]
    owner[_HostileText("path")] = "synthetic://owner-directive"
    result = _call(prerequisites=prereqs)
    assert "owner_directive_original_record" in result.missing_prerequisites
    prereqs = _full_prerequisites()
    prereqs["owner_directive_original_record"]["qualification"] = _HostileText("PROVIDER_RIGHTS_GRANTED")
    result = _call(prerequisites=prereqs)
    assert "owner_directive_original_record" in result.missing_prerequisites


@pytest.mark.parametrize("field", ("dispatch_not_before_utc", "expires_utc"))
def test_hostile_window_timestamp_is_unparseable_without_string_protocols(field):
    window = dict(FRESH_WINDOW)
    window[field] = _HostileText(window[field])
    result = _call(proposed_window=window)
    assert result.outcome == OUTCOME_REFUSED
    assert "UNPARSEABLE_PROPOSED_WINDOW" in result.refusal_reasons


def test_hostile_now_and_clock_fields_are_rejected_without_protocols():
    result = _call(now_utc=_HostileText(NOW_UTC))
    assert result.outcome == OUTCOME_REFUSED
    assert "UNPARSEABLE_NOW_UTC" in result.refusal_reasons
    for field, value, reason in (
        ("measured_utc", _HostileText(NOW_UTC), "UNPARSEABLE_CLOCK"),
        ("source", _HostileText("LOCAL_AUTHORIZED_ONLY"), "INVALID_CLOCK_SOURCE"),
        ("uncertainty_seconds", 0.3, "INVALID_CLOCK_UNCERTAINTY"),
        ("calibration_age_seconds", 10.0, "INVALID_CALIBRATION_AGE"),
    ):
        if field.endswith("seconds"):
            class HostileNumber(float):
                def __eq__(self, other):
                    raise AssertionError("number equality invoked")
            value = HostileNumber(value)
        result = _call(clock=replace(GOOD_CLOCK, **{field: value}))
        assert reason in result.incompleteness_reasons


@pytest.mark.parametrize("field,reason", (
    ("uncertainty_seconds", "INVALID_CLOCK_UNCERTAINTY"),
    ("calibration_age_seconds", "INVALID_CALIBRATION_AGE"),
))
def test_clock_metaclass_equality_cannot_admit_float_subclass(field, reason):
    class EqualToEveryType(type):
        def __eq__(cls, other):
            return True

        __hash__ = type.__hash__

    class HostileFloat(float, metaclass=EqualToEveryType):
        def __eq__(self, other):
            raise AssertionError("caller numeric equality invoked")

    result = _call(clock=replace(GOOD_CLOCK, **{field: HostileFloat(0.3)}))
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.clock_ready is False
    assert reason in result.incompleteness_reasons


def test_clock_metaclass_equality_cannot_forge_oversized_uncertainty():
    class EqualToEveryType(type):
        def __eq__(cls, other):
            return True

        __hash__ = type.__hash__

    class LyingFloat(float, metaclass=EqualToEveryType):
        def __eq__(self, other):
            raise AssertionError("caller numeric equality invoked")

        def __gt__(self, other):
            raise AssertionError("caller numeric comparison invoked")

    clock = replace(GOOD_CLOCK, uncertainty_seconds=LyingFloat(1e300))
    result = _call(clock=clock)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.clock_ready is False
    assert "INVALID_CLOCK_UNCERTAINTY" in result.incompleteness_reasons


@pytest.mark.parametrize("value", (None, "review", 7, []))
def test_scalar_persistence_review_keeps_checker_diagnostic(value):
    storage = dict(GOOD_STORAGE_QUALIFICATION, persistence_review=value)
    result = _call(storage_qualification=storage)
    assert "MISSING_STORAGE_PERSISTENCE_REVIEW" in result.incompleteness_reasons
    assert not any("PREREQUISITE_REFERENCE:storage_qualification.persistence_review" in x
                   for x in result.incompleteness_reasons)


@pytest.mark.parametrize("base_type", (object, str))
def test_persistence_review_raising_class_is_never_inspected(base_type):
    class RaisingClass(base_type):
        @property
        def __class__(self):
            raise AssertionError("caller __class__ invoked")

    value = RaisingClass() if base_type is object else RaisingClass("private sentinel")
    result = _call(storage_qualification=dict(GOOD_STORAGE_QUALIFICATION, persistence_review=value))
    assert result.outcome == OUTCOME_INCOMPLETE
    assert "MISSING_STORAGE_PERSISTENCE_REVIEW" in result.incompleteness_reasons
    assert not any("PREREQUISITE_REFERENCE:storage_qualification.persistence_review" in x
                   for x in result.incompleteness_reasons)


@pytest.mark.parametrize("keys_behavior", ("valid", "raise", "many"))
def test_persistence_review_flipping_class_never_reaches_checker(keys_behavior):
    class FlippingClass:
        def __init__(self):
            self.calls = 0
            self.protocol_calls = 0

        @property
        def __class__(self):
            self.calls += 1
            return FlippingClass if self.calls == 1 else dict

        def keys(self):
            self.protocol_calls += 1
            if keys_behavior == "valid":
                return GOOD_STORAGE_QUALIFICATION["persistence_review"].keys()
            if keys_behavior == "many":
                return iter(range(100_000))
            raise ValueError("caller keys invoked")

        def get(self, key, default=None):
            self.protocol_calls += 1
            return GOOD_STORAGE_QUALIFICATION["persistence_review"].get(key, default)

    value = FlippingClass()
    result = _call(storage_qualification=dict(GOOD_STORAGE_QUALIFICATION, persistence_review=value))
    assert value.calls == 0
    assert value.protocol_calls == 0
    assert result.outcome == OUTCOME_INCOMPLETE
    assert result.storage_ready is False
    assert "MISSING_STORAGE_PERSISTENCE_REVIEW" in result.incompleteness_reasons
    assert not any("PREREQUISITE_REFERENCE:storage_qualification.persistence_review" in x
                   for x in result.incompleteness_reasons)


def test_missing_persistence_review_keeps_missing_key_diagnostic():
    storage = dict(GOOD_STORAGE_QUALIFICATION)
    del storage["persistence_review"]
    result = _call(storage_qualification=storage)
    assert "MISSING_KEY:storage_qualification.persistence_review" in result.incompleteness_reasons
    assert "MISSING_STORAGE_PERSISTENCE_REVIEW" in result.incompleteness_reasons
    assert "OVERSIZED_PREREQUISITE_REFERENCE:storage_qualification.persistence_review" not in result.incompleteness_reasons


def test_bound_diagnostic_output_counts_json_serialization_overhead_not_bare_text():
    # Many short, individually tiny, distinct reasons whose bare UTF-8 text
    # sum stays under the frozen budget, but whose actual JSON-array
    # serialization (quotes, commas, brackets) pushes past it -- the
    # retained review's finding that summing only each reason's own text
    # omits that overhead, so the 1,061,375-byte split-view probe output
    # exceeded the 1,048,576-byte limit despite passing a bare-text sum.
    reasons = [f"R{i:09d}" for i in range(100_000)]
    bare_text_bytes = sum(len(r.encode("utf-8")) for r in reasons)
    assert bare_text_bytes < 1_048_576
    result = _bound_diagnostic_output(reasons)
    assert result == ("DIAGNOSTIC_OUTPUT_BUDGET_EXCEEDED",)


def test_bound_diagnostic_output_passes_through_ordinary_small_reason_sets():
    result = _bound_diagnostic_output(["B_REASON", "A_REASON", "A_REASON"])
    assert result == ("A_REASON", "B_REASON")


# -- Result invariants ----------------------------------------------------

def test_result_rejects_inconsistent_refused_with_empty_reasons():
    with pytest.raises(ValueError):
        FreshWindowReadinessResult(
            schema=SCHEMA, outcome=OUTCOME_REFUSED, eligibility=ELIGIBILITY_LABEL,
            window_is_fresh=False, clock_ready=False, storage_ready=False,
            restriction_history_preserved=False, missing_prerequisites=(),
            refusal_reasons=(), incompleteness_reasons=(),
        )


def test_result_rejects_candidate_with_missing_prerequisites():
    with pytest.raises(ValueError):
        FreshWindowReadinessResult(
            schema=SCHEMA, outcome=OUTCOME_CANDIDATE, eligibility=ELIGIBILITY_LABEL,
            window_is_fresh=True, clock_ready=True, storage_ready=True,
            restriction_history_preserved=True, missing_prerequisites=("x",),
            refusal_reasons=(), incompleteness_reasons=(),
        )


def test_result_rejects_unknown_outcome():
    with pytest.raises(ValueError):
        FreshWindowReadinessResult(
            schema=SCHEMA, outcome="SOMETHING_ELSE", eligibility=ELIGIBILITY_LABEL,
            window_is_fresh=True, clock_ready=True, storage_ready=True,
            restriction_history_preserved=True, missing_prerequisites=(),
            refusal_reasons=(), incompleteness_reasons=(),
        )


def test_result_rejects_wrong_eligibility_label():
    with pytest.raises(ValueError):
        FreshWindowReadinessResult(
            schema=SCHEMA, outcome=OUTCOME_CANDIDATE, eligibility="WRONG_LABEL",
            window_is_fresh=True, clock_ready=True, storage_ready=True,
            restriction_history_preserved=True, missing_prerequisites=(),
            refusal_reasons=(), incompleteness_reasons=(),
        )


def test_result_rejects_incomplete_with_empty_incompleteness_reasons():
    with pytest.raises(ValueError):
        FreshWindowReadinessResult(
            schema=SCHEMA, outcome=OUTCOME_INCOMPLETE, eligibility=ELIGIBILITY_LABEL,
            window_is_fresh=True, clock_ready=False, storage_ready=True,
            restriction_history_preserved=True, missing_prerequisites=(),
            refusal_reasons=(), incompleteness_reasons=(),
        )


# -- Real concrete frozen state: never claims a free ride -----------------

def test_real_binding_prerequisites_remain_incomplete_today():
    """The actual committed binding JSON currently has all twelve
    prerequisites null. Loading them unmodified into this planner (for a
    distinct, genuinely fresh window) must report every one missing, never
    silently invent a satisfied reference."""
    with open("docs/V11_R09_GATE3_EVIDENCE_PREFLIGHT_PACKAGE_20261002.json", "rb") as handle:
        binding = strict_json_loads(handle.read())
    prereqs = _empty_prerequisites()
    for key in binding["missing_prerequisites"]:
        assert key in prereqs
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_INCOMPLETE
    assert set(binding["missing_prerequisites"]) <= set(result.missing_prerequisites)
