"""Synthetic, offline refusal/state-machine tests for the Gate 3 evidence-only
preflight package checker (protocol section 7 implementation slice).

No test here creates a socket, imports an HTTP/provider client, or performs a
DNS lookup. Positive-path fixtures are clearly synthetic/offline identities
(``synthetic:``-prefixed paths and fabricated sha256 values) and never confer
real execution authority, provider rights or G3-L credit.
"""

import copy
import hashlib
import json

import pytest

from tools.v11_gate3_evidence_preflight_checker import (
    ALLOWED_OUTCOMES, BINDING_SCHEMA_NAME, ClockObservation, LedgerEntry,
    OUTCOME_REFUSED, OUTCOME_SATISFIED, PACKAGE_SCHEMA_NAME,
    PreflightPackageCheckerError, ResourceObservation,
    RESTRICTIONS_SCHEMA_NAME, StateLedger, check_evidence_preflight_package,
    strict_json_loads,
)

REAL_PRIVATE_ROOT = "/home/alphaadmin/AlphaV11_Gate3EvidencePreflight/20261002-gefs-index-v1"
REAL_PROTOCOL_PATH = "docs/V11_R09_GATE3_EVIDENCE_PREFLIGHT_PROTOCOL_20261002.md"
REAL_BINDING_PATH = "docs/V11_R09_GATE3_EVIDENCE_PREFLIGHT_PACKAGE_20261002.json"

GOOD_CLOCK = ClockObservation(
    measured_utc="2026-10-02T10:05:00Z",
    uncertainty_seconds=0.3,
    calibration_age_seconds=10.0,
    monotonic_consistent=True,
)
GOOD_RESOURCES = ResourceObservation(
    free_disk_bytes_after_reservation=3_300_000_000,
    mem_available_bytes_after_reservation=600_000_000,
    physically_reserved_bytes=67_108_864,
)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _synthetic_ref(tag: str, data: bytes = b"synthetic") -> dict:
    return {
        "sha256": _sha(f"{tag}:{data!r}".encode()),
        "byte_length": len(data),
        "path": f"synthetic://offline-fixture/{tag}",
    }


def _synthetic_package_raw() -> bytes:
    """A structurally complete, fully-satisfied synthetic package: every
    prerequisite, storage, review and lineage gap closed with fabricated
    offline identities. Still frozen to NO_GO/False/0 — the checker can
    never observe this package granting itself real authority."""

    pkg = {
        "author_model": "synthetic-test-fixture",
        "baseline_commit": "74075b524b0301fdd13a45d03d9c42876564a9d3",
        "blocking_reasons": [],
        "campaign_id": "alpha-v11-evidence-preflight-20261002",
        "capture_eligibility": False,
        "capture_g3l": "NO_GO",
        "directories": [
            {"device": 1, "inode": 1, "mode": "0700", "path": "synthetic://root",
             "qualification": "OBSERVATION_ONLY", "uid": 1000},
        ],
        "disallowed": [
            "ECMWF", "FIELD", "HEAD", "METADATA", "PROBE", "REDIRECT", "RETRY",
            "FAILOVER", "CREDENTIALS", "NATIVE_DECODE", "FINANCIAL_EXECUTION",
            "V10_CHANGE", "AUTHORITY_INSTALLATION",
        ],
        "execution_authority": False,
        "independent_execution_review": {
            "binding": "DETACHED_ENVELOPE_OF_FINAL_PACKAGE_AND_RUNTIME",
            "present": True,
        },
        "input_refs": [dict(_synthetic_ref("input-0"), repository_path="docs/synthetic-input-0.md")],
        "limits": {
            "address_space_bytes": 268_435_456, "attempt_deadline_seconds": 30,
            "campaign_attempts": 8, "campaign_body_bytes": 33_554_432,
            "campaign_elapsed_seconds": 120, "clock_calibration_max_age_seconds": 60,
            "clock_uncertainty_seconds": 1, "combined_preflight_capture_attempts": 3600,
            "combined_preflight_capture_body_bytes": 1_073_741_824, "cpu_seconds": 10,
            "descriptor_bytes": 4096, "diagnostic_output_bytes": 1_048_576,
            "dns_answers": 16, "free_disk_floor_after_reservation_bytes": 2_147_483_648,
            "free_disk_target_after_reservation_bytes": 3_221_225_472,
            "header_bytes": 4096, "header_count": 32, "header_name_bytes": 64,
            "header_value_bytes": 1024, "in_flight": 1, "index_line_bytes": 4096,
            "index_rows": 8192, "journal_bytes_each": 8_388_608,
            "journal_record_bytes": 65_536, "journal_records_each": 2048,
            "memory_floor_after_reservation_bytes": 536_870_912,
            "minimum_start_spacing_seconds": 2, "physical_storage_reservation_bytes": 67_108_864,
            "stage_attempts": 1, "stage_body_bytes": 3_145_728,
            "stage_elapsed_seconds": 60, "working_rss_bytes": 134_217_728,
        },
        "prerequisites": {
            "accepted_protocol_design_review": _synthetic_ref("prereq-design-review"),
            "anonymous_access_and_exact_path_review": _synthetic_ref("prereq-access"),
            "clock_method_and_calibration_review": _synthetic_ref("prereq-clock"),
            "complete_restriction_and_control_domain_review": _synthetic_ref("prereq-domain"),
            "dns_tls_trust_and_bounded_transport_review": _synthetic_ref("prereq-transport"),
            "independently_retained_history_head": _synthetic_ref("prereq-history-head"),
            "index_parser_semantics_review": _synthetic_ref("prereq-parser"),
            "owner_directive_original_record": {
                "byte_length": 1441, "path": "synthetic://owner-directive",
                "qualification": "OWNER_INSTRUCTION_ONLY_NOT_PROVIDER_RIGHTS",
                "sha256": _sha(b"owner-directive"),
            },
            "post_reservation_resource_enforcement_review": _synthetic_ref("prereq-resource"),
            "private_storage_persistence_lock_and_reservation_review": _synthetic_ref("prereq-storage"),
            "runtime_implementation_review": _synthetic_ref("prereq-runtime"),
            "runtime_source_and_transitive_build_lock": _synthetic_ref("prereq-buildlock"),
            "shared_lineage_and_unfinished_intent_reconciliation": _synthetic_ref("prereq-lineage"),
        },
        "qualification_credit": 0,
        "requests": [{
            "access_status": "UNQUALIFIED", "mapping_status": "PROPOSED_UNQUALIFIED",
            "method": "GET", "origin": "https://noaa-gefs-pds.s3.amazonaws.com",
            "path": "/gefs.20261002/00/atmos/pgrb2ap5/gec00.t00z.pgrb2a.0p50.f024.idx",
            "port": 443, "provider": "GEFS", "purpose": "INDEX_DISCOVERY_ONLY",
            "query": None, "range": None, "request_body": None,
            "request_headers": {
                "Accept": "text/plain", "Accept-Encoding": "identity",
                "Connection": "close", "Host": "noaa-gefs-pds.s3.amazonaws.com",
                "User-Agent": "AlphaV11-EvidencePreflight/1",
            },
            "request_id": "p1-gefs-2026100200-c00-f024-index",
            "restriction_status": "BLOCKED_UNKNOWN_LINEAGE_AND_SCOPE",
        }],
        "response_contract": {
            "automatic_chained_request": False, "content_encoding": "identity",
            "content_length_max": 3_145_728, "content_length_min": 1,
            "etag_scope": "INDEX_DIAGNOSTIC_ONLY", "grib_decode": False,
            "http_status": 200, "parser_status": "ABSENT_REQUIRES_INDEPENDENT_IMPLEMENTATION_REVIEW",
            "transfer_encoding": None,
        },
        "restrictions_ref": None,  # filled in by caller once restrictions bytes are known
        "run_utc": "2026-10-02T00:00:00Z",
        "schema": PACKAGE_SCHEMA_NAME,
        "scope": "INDEX_DISCOVERY_ONLY_NOT_CAPTURE",
        "stage_id": "P1_GEFS_INDEX",
        "state": "PREPARED_BLOCKED",
        "storage_qualification": {
            "live_ledger_created": True,
            "persistence_review": _synthetic_ref("prereq-persistence"),
            "physically_reserved_bytes": 67_108_864,
        },
        "unknown_outputs_not_required_as_inputs": [
            "index_body_sha256", "index_etag", "selected_index_row",
            "proposed_field_range", "actual_phase_clocks",
        ],
        "window": {
            "automatic_roll_forward": False,
            "dispatch_not_before_utc": "2026-10-02T10:00:00Z",
            "expires_utc": "2026-10-02T13:30:00Z",
        },
    }
    return pkg


def _synthetic_restrictions_raw() -> bytes:
    restrictions = {
        "complete_lineage_review": _synthetic_ref("lineage-review"),
        "execution_authority": False,
        "inventory_audit_ref": _synthetic_ref("inventory-audit"),
        "known_control_domains": {
            "ECMWF": {
                "models": ["IFS", "AIFS"],
                "origins": ["https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com", "https://data.ecmwf.int"],
                "resumption_review": None,
                "status": "HELD",
            },
            "GEFS": {
                "origins": ["https://noaa-gefs-pds.s3.amazonaws.com"],
                "scope_independence_review": _synthetic_ref("gefs-scope-review"),
                "status": "SCOPE_INDEPENDENCE_CONFIRMED",
            },
        },
        "raw_source_refs": [],
        "records": [],
        "schema": RESTRICTIONS_SCHEMA_NAME,
        "shared_history_head": _sha(b"synthetic-history-head"),
        "status": "RECONCILED_SYNTHETIC_FIXTURE",
        "unresolved_attempt_reconciliation": _synthetic_ref("unresolved-reconciliation"),
    }
    return json.dumps(restrictions).encode()


def _synthetic_protocol_raw() -> bytes:
    return b"synthetic offline protocol fixture bytes, not the real protocol"


def _synthetic_binding_raw(package_raw: bytes, restrictions_raw: bytes, protocol_raw: bytes) -> bytes:
    binding = {
        "allowed_review_verdicts": ["PASS_IN_SCOPE_DESIGN_BLOCKED_PACKAGE", "CHANGES_REQUIRED"],
        "baseline_commit": "74075b524b0301fdd13a45d03d9c42876564a9d3",
        "formal": "1/50",
        "g3l": "NO_GO",
        "intended_review_scope": "DESIGN_AND_EXACT_BLOCKED_PRIVATE_PACKAGE_NOT_EXECUTION",
        "missing_prerequisites": [],
        "native_decode_calls": 0,
        "owner_instruction_record": _synthetic_ref("owner-instruction"),
        "prepared_at_utc": "2026-10-02T09:25:30.001040+00:00",
        "private_package": {"byte_length": len(package_raw), "path": "synthetic://package",
                             "sha256": _sha(package_raw)},
        "private_restrictions": {"byte_length": len(restrictions_raw), "path": "synthetic://restrictions",
                                  "sha256": _sha(restrictions_raw)},
        "private_root": "synthetic://root",
        "protocol": {"byte_length": len(protocol_raw), "path": "synthetic://protocol",
                     "sha256": _sha(protocol_raw)},
        "provider_requests": 0,
        "qualification_credit": 0,
        "raw_restriction_sources": [],
        "schema": BINDING_SCHEMA_NAME,
        "score": "91/200",
        "source_inputs": [],
        "status": "CANDIDATE_DESIGN_AND_BLOCKED_PACKAGE",
    }
    return json.dumps(binding).encode()


def _synthetic_fixture():
    """Build a fully mutually-consistent synthetic (package, restrictions,
    protocol, binding) tuple that satisfies every checker requirement."""
    restrictions_raw = _synthetic_restrictions_raw()
    protocol_raw = _synthetic_protocol_raw()
    pkg = _synthetic_package_raw()
    pkg["restrictions_ref"] = {
        "byte_length": len(restrictions_raw), "path": "synthetic://restrictions",
        "sha256": _sha(restrictions_raw),
    }
    package_raw = json.dumps(pkg).encode()
    binding_raw = _synthetic_binding_raw(package_raw, restrictions_raw, protocol_raw)
    return package_raw, restrictions_raw, protocol_raw, binding_raw


def _run(package_raw, restrictions_raw, protocol_raw, binding_raw, **kwargs):
    kwargs.setdefault("clock", GOOD_CLOCK)
    kwargs.setdefault("resources", GOOD_RESOURCES)
    return check_evidence_preflight_package(
        package=strict_json_loads(package_raw),
        restrictions=strict_json_loads(restrictions_raw),
        binding=strict_json_loads(binding_raw),
        package_raw=package_raw,
        restrictions_raw=restrictions_raw,
        protocol_raw=protocol_raw,
        **kwargs,
    )


# -- Positive path: synthetic identities, no real permission -----------------

def test_fully_satisfied_synthetic_package_is_schema_and_policy_satisfied():
    fixture = _synthetic_fixture()
    result = _run(*fixture)
    assert result.outcome == OUTCOME_SATISFIED
    assert result.refusal_reasons == ()
    assert result.outcome in ALLOWED_OUTCOMES
    # A satisfied checker result never flips any real-authority field.
    pkg = strict_json_loads(fixture[0])
    assert pkg["execution_authority"] is False
    assert pkg["capture_eligibility"] is False
    assert pkg["capture_g3l"] == "NO_GO"
    assert pkg["qualification_credit"] == 0
    assert result.eligibility == "DISCOVERY_ONLY_NOT_G3E"


def test_checker_never_emits_a_third_outcome_or_forbidden_promotion_label():
    fixture = _synthetic_fixture()
    result = _run(*fixture)
    forbidden = {"CAPTURED", "READY", "QUALIFIED", "G3L_PASS", "EXECUTABLE_PREFLIGHT_PASS", "GO"}
    assert result.outcome not in forbidden
    assert set(ALLOWED_OUTCOMES).isdisjoint(forbidden)


# -- Negative path: the real concrete candidate package is refused -----------

def test_refuses_the_actual_concrete_candidate_package():
    package_raw = open(f"{REAL_PRIVATE_ROOT}/package.json", "rb").read()
    restrictions_raw = open(f"{REAL_PRIVATE_ROOT}/restriction-history.json", "rb").read()
    protocol_raw = open(REAL_PROTOCOL_PATH, "rb").read()
    binding_raw = open(REAL_BINDING_PATH, "rb").read()
    binding = strict_json_loads(binding_raw)
    # Self-verify the exact bytes against the committed public binding before
    # trusting them as "this concrete package" (never trust an unverified copy).
    assert _sha(package_raw) == binding["private_package"]["sha256"]
    assert _sha(restrictions_raw) == binding["private_restrictions"]["sha256"]
    assert _sha(protocol_raw) == binding["protocol"]["sha256"]

    result = _run(package_raw, restrictions_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    for expected in (
        "NULL_PREREQUISITE:accepted_protocol_design_review",
        "NULL_PREREQUISITE:runtime_implementation_review",
        "NULL_PREREQUISITE:shared_lineage_and_unfinished_intent_reconciliation",
        "NO_PHYSICAL_STORAGE_RESERVATION",
        "MISSING_EXECUTION_REVIEW",
        "MISSING_LIVE_LEDGER",
        "MISSING_STORAGE_PERSISTENCE_REVIEW",
        "GEFS_LINEAGE_UNRESOLVED",
        "UNRESOLVED_GEFS_SCOPE",
        "NULL_COMPLETE_LINEAGE_REVIEW",
        "NULL_SHARED_HISTORY_HEAD",
        "NULL_UNRESOLVED_ATTEMPT_RECONCILIATION",
    ):
        assert expected in result.refusal_reasons
    assert len([r for r in result.refusal_reasons if r.startswith("NULL_PREREQUISITE:")]) == 12


# -- Negative path: changed path/date/limit -----------------------------------

@pytest.mark.parametrize("mutate,expected_reason", [
    (lambda pkg: pkg["requests"][0].__setitem__(
        "path", "/gefs.20261003/00/atmos/pgrb2ap5/gec00.t00z.pgrb2a.0p50.f024.idx"),
     "CHANGED_REQUEST_PATH"),
    (lambda pkg: pkg.__setitem__("run_utc", "2026-10-03T00:00:00Z"),
     "CHANGED_FIELD:package.run_utc"),
    (lambda pkg: pkg["limits"].__setitem__("campaign_attempts", 99),
     "CHANGED_FIELD:limits.campaign_attempts"),
    (lambda pkg: pkg["window"].__setitem__("expires_utc", "2026-10-02T23:59:59Z"),
     "CHANGED_FIELD:window.expires_utc"),
])
def test_refuses_changed_path_date_or_limit(mutate, expected_reason):
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    pkg = strict_json_loads(package_raw)
    mutate(pkg)
    mutated_raw = json.dumps(pkg).encode()
    result = _run(mutated_raw, restrictions_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert expected_reason in result.refusal_reasons


# -- Negative path: unknown keys / null prerequisites -------------------------

def test_refuses_unknown_top_level_key():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    pkg = strict_json_loads(package_raw)
    pkg["unexpected_extra_field"] = "synthetic"
    mutated_raw = json.dumps(pkg).encode()
    result = _run(mutated_raw, restrictions_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "UNKNOWN_KEY:package.unexpected_extra_field" in result.refusal_reasons


def test_refuses_null_prerequisite():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    pkg = strict_json_loads(package_raw)
    pkg["prerequisites"]["runtime_implementation_review"] = None
    mutated_raw = json.dumps(pkg).encode()
    result = _run(mutated_raw, restrictions_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "NULL_PREREQUISITE:runtime_implementation_review" in result.refusal_reasons


def test_refuses_duplicate_json_key():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    raw_text = package_raw.decode()
    dup = raw_text.replace('"schema":', '"schema": "DUPLICATE_PLACEHOLDER", "schema":', 1)
    with pytest.raises(PreflightPackageCheckerError):
        strict_json_loads(dup.encode())


def test_rejects_nonfinite_json_constant():
    with pytest.raises(PreflightPackageCheckerError):
        strict_json_loads(b'{"x": NaN}')


# -- Negative path: unresolved history -----------------------------------------

@pytest.mark.parametrize("mutate,expected_reason", [
    (lambda r: r.__setitem__("complete_lineage_review", None), "NULL_COMPLETE_LINEAGE_REVIEW"),
    (lambda r: r.__setitem__("shared_history_head", None), "NULL_SHARED_HISTORY_HEAD"),
    (lambda r: r.__setitem__("unresolved_attempt_reconciliation", None),
     "NULL_UNRESOLVED_ATTEMPT_RECONCILIATION"),
    (lambda r: r["known_control_domains"]["GEFS"].__setitem__("scope_independence_review", None),
     "UNRESOLVED_GEFS_SCOPE"),
    (lambda r: r["known_control_domains"]["GEFS"].__setitem__(
        "status", "BLOCKED_UNKNOWN_LINEAGE_AND_SCOPE"), "GEFS_LINEAGE_UNRESOLVED"),
])
def test_refuses_unresolved_restriction_history(mutate, expected_reason):
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    restrictions = strict_json_loads(restrictions_raw)
    mutate(restrictions)
    mutated_raw = json.dumps(restrictions).encode()
    result = _run(package_raw, mutated_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert expected_reason in result.refusal_reasons


def test_refuses_ecmwf_hold_misrecorded_as_not_held():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    restrictions = strict_json_loads(restrictions_raw)
    restrictions["known_control_domains"]["ECMWF"]["status"] = "RESUMED"
    mutated_raw = json.dumps(restrictions).encode()
    result = _run(package_raw, mutated_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "ECMWF_HOLD_NOT_RECOGNIZED" in result.refusal_reasons


# -- Negative path: changed private bytes --------------------------------------

def test_refuses_changed_private_package_bytes_vs_binding():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    tampered_package_raw = package_raw + b" "  # single byte appended after binding
    result = _run(tampered_package_raw, restrictions_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "CHANGED_PRIVATE_PACKAGE_BYTES" in result.refusal_reasons


def test_refuses_changed_restrictions_bytes_vs_package_ref():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    tampered_restrictions_raw = restrictions_raw + b" "
    result = _run(package_raw, tampered_restrictions_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "CHANGED_PRIVATE_RESTRICTIONS_BYTES" in result.refusal_reasons


def test_refuses_changed_protocol_bytes_vs_binding():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    tampered_protocol_raw = protocol_raw + b" extra"
    result = _run(package_raw, restrictions_raw, tampered_protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "CHANGED_PROTOCOL_BYTES" in result.refusal_reasons


# -- Negative path: missing review/terminal ------------------------------------

def test_refuses_missing_independent_execution_review():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    pkg = strict_json_loads(package_raw)
    pkg["independent_execution_review"]["present"] = False
    mutated_raw = json.dumps(pkg).encode()
    result = _run(mutated_raw, restrictions_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "MISSING_EXECUTION_REVIEW" in result.refusal_reasons


def test_refuses_mislabeled_execution_review_binding():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    pkg = strict_json_loads(package_raw)
    pkg["independent_execution_review"]["binding"] = "SOME_OTHER_BINDING"
    mutated_raw = json.dumps(pkg).encode()
    result = _run(mutated_raw, restrictions_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "INVALID_EXECUTION_REVIEW_BINDING" in result.refusal_reasons


# -- Negative path: expired clock -----------------------------------------------

def test_refuses_expired_clock_window():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    expired_clock = ClockObservation("2026-10-02T14:00:00Z", 0.3, 10.0, True)
    result = _run(package_raw, restrictions_raw, protocol_raw, binding_raw, clock=expired_clock)
    assert result.outcome == OUTCOME_REFUSED
    assert "EXPIRED_WINDOW" in result.refusal_reasons


def test_refuses_stale_clock_calibration():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    stale_clock = ClockObservation("2026-10-02T10:05:00Z", 0.3, 61.0, True)
    result = _run(package_raw, restrictions_raw, protocol_raw, binding_raw, clock=stale_clock)
    assert result.outcome == OUTCOME_REFUSED
    assert "EXPIRED_CLOCK_CALIBRATION" in result.refusal_reasons


def test_refuses_excessive_clock_uncertainty():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    uncertain_clock = ClockObservation("2026-10-02T10:05:00Z", 1.5, 10.0, True)
    result = _run(package_raw, restrictions_raw, protocol_raw, binding_raw, clock=uncertain_clock)
    assert result.outcome == OUTCOME_REFUSED
    assert "EXCESSIVE_CLOCK_UNCERTAINTY" in result.refusal_reasons


def test_refuses_nonmonotonic_clock():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    stepping_clock = ClockObservation("2026-10-02T10:05:00Z", 0.3, 10.0, False)
    result = _run(package_raw, restrictions_raw, protocol_raw, binding_raw, clock=stepping_clock)
    assert result.outcome == OUTCOME_REFUSED
    assert "NONMONOTONIC_CLOCK" in result.refusal_reasons


# -- Negative path: insufficient post-reservation resources ---------------------

def test_refuses_insufficient_post_reservation_disk():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    low_disk = ResourceObservation(1_000_000_000, 600_000_000, 67_108_864)
    result = _run(package_raw, restrictions_raw, protocol_raw, binding_raw, resources=low_disk)
    assert result.outcome == OUTCOME_REFUSED
    assert "INSUFFICIENT_POST_RESERVATION_DISK" in result.refusal_reasons


def test_refuses_insufficient_post_reservation_memory():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    low_mem = ResourceObservation(3_300_000_000, 100_000_000, 67_108_864)
    result = _run(package_raw, restrictions_raw, protocol_raw, binding_raw, resources=low_mem)
    assert result.outcome == OUTCOME_REFUSED
    assert "INSUFFICIENT_POST_RESERVATION_MEMORY" in result.refusal_reasons


def test_refuses_zero_physical_storage_reservation():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    pkg = strict_json_loads(package_raw)
    pkg["storage_qualification"]["physically_reserved_bytes"] = 0
    mutated_raw = json.dumps(pkg).encode()
    no_reservation = ResourceObservation(3_300_000_000, 600_000_000, 0)
    result = _run(mutated_raw, restrictions_raw, protocol_raw, binding_raw, resources=no_reservation)
    assert result.outcome == OUTCOME_REFUSED
    assert "NO_PHYSICAL_STORAGE_RESERVATION" in result.refusal_reasons


# -- Negative path: attempted FIELD / ECMWF / second request --------------------

def test_refuses_attempted_field_purpose():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    pkg = strict_json_loads(package_raw)
    pkg["requests"][0]["purpose"] = "FIELD"
    mutated_raw = json.dumps(pkg).encode()
    result = _run(mutated_raw, restrictions_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "ATTEMPTED_FIELD_OR_NONINDEX_REQUEST" in result.refusal_reasons


def test_refuses_attempted_ecmwf_origin():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    pkg = strict_json_loads(package_raw)
    pkg["requests"][0]["origin"] = "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com"
    mutated_raw = json.dumps(pkg).encode()
    result = _run(mutated_raw, restrictions_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "ATTEMPTED_ECMWF_REQUEST" in result.refusal_reasons


def test_refuses_attempted_head_method():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    pkg = strict_json_loads(package_raw)
    pkg["requests"][0]["method"] = "HEAD"
    mutated_raw = json.dumps(pkg).encode()
    result = _run(mutated_raw, restrictions_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "ATTEMPTED_HEAD_REQUEST" in result.refusal_reasons


def test_refuses_second_request_in_same_package():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    pkg = strict_json_loads(package_raw)
    second = copy.deepcopy(pkg["requests"][0])
    second["request_id"] = "p1-gefs-2026100200-c00-f024-index-second"
    pkg["requests"].append(second)
    mutated_raw = json.dumps(pkg).encode()
    result = _run(mutated_raw, restrictions_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "MULTIPLE_REQUESTS_NOT_PERMITTED" in result.refusal_reasons


def test_refuses_tampered_disallowed_list():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    pkg = strict_json_loads(package_raw)
    pkg["disallowed"].remove("ECMWF")
    mutated_raw = json.dumps(pkg).encode()
    result = _run(mutated_raw, restrictions_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "TAMPERED_DISALLOWED_LIST" in result.refusal_reasons


# -- Negative path: replay / reset (state machine) -------------------------------

def test_refuses_replayed_request_id():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    ledger = StateLedger((LedgerEntry("alpha-v11-evidence-preflight-20261002",
                                       "p1-gefs-2026100200-c00-f024-index"),))
    result = _run(package_raw, restrictions_raw, protocol_raw, binding_raw, ledger=ledger)
    assert result.outcome == OUTCOME_REFUSED
    assert "REPLAYED_REQUEST_ID" in result.refusal_reasons


def test_refuses_campaign_budget_reset_on_restart():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    ledger = StateLedger((LedgerEntry("alpha-v11-evidence-preflight-20261002",
                                       "some-earlier-exhausted-request", attempted=False),))
    result = _run(package_raw, restrictions_raw, protocol_raw, binding_raw,
                  ledger=ledger, restart_requested=True)
    assert result.outcome == OUTCOME_REFUSED
    assert "CAMPAIGN_BUDGET_RESET_NOT_PERMITTED" in result.refusal_reasons


def test_fresh_campaign_without_replay_is_not_refused_on_ledger_grounds():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    ledger = StateLedger(())
    result = _run(package_raw, restrictions_raw, protocol_raw, binding_raw, ledger=ledger)
    assert "REPLAYED_REQUEST_ID" not in result.refusal_reasons
    assert "CAMPAIGN_BUDGET_RESET_NOT_PERMITTED" not in result.refusal_reasons


# -- Negative path: every forbidden attempt to promote outputs -------------------

@pytest.mark.parametrize("mutate,expected_reason", [
    (lambda pkg: pkg.__setitem__("state", "EXECUTABLE_PREFLIGHT_PASS"), "CHANGED_FIELD:package.state"),
    (lambda pkg: pkg.__setitem__("state", "CAPTURED"), "CHANGED_FIELD:package.state"),
    (lambda pkg: pkg.__setitem__("capture_g3l", "GO"), "CHANGED_FIELD:package.capture_g3l"),
    (lambda pkg: pkg.__setitem__("capture_eligibility", True), "CHANGED_FIELD:package.capture_eligibility"),
    (lambda pkg: pkg.__setitem__("execution_authority", True), "CHANGED_FIELD:package.execution_authority"),
    (lambda pkg: pkg.__setitem__("qualification_credit", 1), "CHANGED_FIELD:package.qualification_credit"),
])
def test_refuses_forbidden_output_promotion_on_package(mutate, expected_reason):
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    pkg = strict_json_loads(package_raw)
    mutate(pkg)
    mutated_raw = json.dumps(pkg).encode()
    result = _run(mutated_raw, restrictions_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert expected_reason in result.refusal_reasons


def test_refuses_restrictions_execution_authority_promotion():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    restrictions = strict_json_loads(restrictions_raw)
    restrictions["execution_authority"] = True
    mutated_raw = json.dumps(restrictions).encode()
    result = _run(package_raw, mutated_raw, protocol_raw, binding_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "FORBIDDEN_RESTRICTIONS_EXECUTION_AUTHORITY_PROMOTION" in result.refusal_reasons


def test_refuses_binding_g3l_promotion():
    package_raw, restrictions_raw, protocol_raw, binding_raw = _synthetic_fixture()
    binding = strict_json_loads(binding_raw)
    binding["g3l"] = "GO"
    mutated_raw = json.dumps(binding).encode()
    result = _run(package_raw, restrictions_raw, protocol_raw, mutated_raw)
    assert result.outcome == OUTCOME_REFUSED
    assert "CHANGED_FIELD:binding.g3l" in result.refusal_reasons


def test_check_result_schema_rejects_third_outcome_value():
    from tools.v11_gate3_evidence_preflight_checker import SCHEMA, CheckResult
    with pytest.raises(AssertionError):
        CheckResult(SCHEMA, "SOMETHING_ELSE", "DISCOVERY_ONLY_NOT_G3E", ())


def test_check_result_schema_requires_reasons_iff_refused():
    from tools.v11_gate3_evidence_preflight_checker import SCHEMA, CheckResult
    with pytest.raises(AssertionError):
        CheckResult(SCHEMA, OUTCOME_SATISFIED, "DISCOVERY_ONLY_NOT_G3E", ("SOME_REASON",))
    with pytest.raises(AssertionError):
        CheckResult(SCHEMA, OUTCOME_REFUSED, "DISCOVERY_ONLY_NOT_G3E", ())
