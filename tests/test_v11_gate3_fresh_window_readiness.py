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

import pytest

from tools.v11_gate3_evidence_preflight_checker import (
    ClockObservation,
    NULLABLE_PREREQS,
    PREREQ_KEYS,
    ResourceObservation,
    RESTRICTIONS_SCHEMA_NAME,
    strict_json_loads,
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
    measured_utc="2026-10-05T10:05:00Z",
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

def test_fully_satisfied_synthetic_inputs_are_candidate_not_executable():
    result = _call()
    assert result.outcome == OUTCOME_CANDIDATE
    assert result.outcome in ALLOWED_OUTCOMES
    assert result.outcome not in FORBIDDEN_OUTCOME_LABELS
    assert result.window_is_fresh is True
    assert result.clock_ready is True
    assert result.storage_ready is True
    assert result.restriction_history_preserved is True
    assert result.missing_prerequisites == ()
    assert result.refusal_reasons == ()
    assert result.incompleteness_reasons == ()
    assert result.eligibility == ELIGIBILITY_LABEL
    assert result.schema == SCHEMA


def test_result_to_dict_is_json_serializable():
    result = _call()
    encoded = json.dumps(result.to_dict())
    assert "PREPARATION_CANDIDATE" in encoded


def test_never_emits_a_forbidden_promotion_label():
    result = _call()
    assert set(ALLOWED_OUTCOMES).isdisjoint(FORBIDDEN_OUTCOME_LABELS)
    assert result.outcome not in FORBIDDEN_OUTCOME_LABELS


# -- Refusal tier: safety-relevant structural problems -------------------

def test_refuses_identical_expired_window_as_silent_roll_forward():
    result = _call(proposed_window=EXPIRED_WINDOW, now_utc="2026-10-02T09:00:00Z")
    assert result.outcome == OUTCOME_REFUSED
    assert "IDENTICAL_TO_EXPIRED_WINDOW_SILENT_ROLL_FORWARD" in result.refusal_reasons


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


def test_refuses_unknown_window_key():
    extra = dict(FRESH_WINDOW, unexpected="x")
    result = _call(proposed_window=extra)
    assert result.outcome == OUTCOME_REFUSED
    assert "UNKNOWN_KEY:proposed_window.unexpected" in result.refusal_reasons


def test_refuses_missing_window_key():
    incomplete_window = {"automatic_roll_forward": False, "dispatch_not_before_utc": FRESH_WINDOW["dispatch_not_before_utc"]}
    result = _call(proposed_window=incomplete_window)
    assert result.outcome == OUTCOME_REFUSED
    assert "MISSING_KEY:proposed_window.expires_utc" in result.refusal_reasons


def test_refuses_unknown_prerequisite_key():
    prereqs = _full_prerequisites()
    prereqs["unexpected_extra_prereq"] = _ref("extra")
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_REFUSED
    assert "UNKNOWN_KEY:prerequisites.unexpected_extra_prereq" in result.refusal_reasons


def test_refuses_missing_prerequisite_key():
    prereqs = _full_prerequisites()
    del prereqs["runtime_implementation_review"]
    result = _call(prerequisites=prereqs)
    assert result.outcome == OUTCOME_REFUSED
    assert "MISSING_KEY:prerequisites.runtime_implementation_review" in result.refusal_reasons


def test_refuses_invalid_restrictions_json():
    result = _call(restrictions_raw=b"{not json")
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_RESTRICTIONS_JSON" in result.refusal_reasons


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
