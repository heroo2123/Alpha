"""Offline closed-schema checker for the Gate 3 evidence-only preflight package.

This module performs pure, bounded, in-memory validation of the
``ALPHA_V11_EVIDENCE_PREFLIGHT_PACKAGE_V1`` / ``..._RESTRICTION_INVENTORY_V1`` /
``..._BINDING_V1`` schemas defined in
docs/V11_R09_GATE3_EVIDENCE_PREFLIGHT_PROTOCOL_20261002.md. It creates no
socket, imports no HTTP/provider client, runs no subprocess, and performs no
DNS, decode or filesystem write. A ``CHECKER_SCHEMA_AND_POLICY_SATISFIED_NOT_EXECUTABLE``
result confers no execution authority, provider right or G3-L credit: a real
transport attempt remains a separate, later, explicitly scoped and
independently reviewed implementation. This checker is itself only a
candidate for independent exact-commit review, not an approval.
"""

from __future__ import annotations

import hashlib
import json
from fractions import Fraction
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional, Sequence

SCHEMA = "R09_GATE3_EVIDENCE_PREFLIGHT_PACKAGE_CHECKER_V1"

PACKAGE_SCHEMA_NAME = "ALPHA_V11_EVIDENCE_PREFLIGHT_PACKAGE_V1"
RESTRICTIONS_SCHEMA_NAME = "ALPHA_V11_PREFLIGHT_RESTRICTION_INVENTORY_V1"
BINDING_SCHEMA_NAME = "ALPHA_V11_EVIDENCE_PREFLIGHT_BINDING_V1"

OUTCOME_REFUSED = "CHECKER_REFUSED_BEFORE_DISPATCH"
OUTCOME_SATISFIED = "CHECKER_SCHEMA_AND_POLICY_SATISFIED_NOT_EXECUTABLE"
ALLOWED_OUTCOMES = (OUTCOME_REFUSED, OUTCOME_SATISFIED)
ELIGIBILITY_LABEL = "DISCOVERY_ONLY_NOT_G3E"

MAX_STR = 4096
MAX_ARRAY = 64
MAX_RAW_BYTES = 1_048_576
MAX_REF_BYTES = 1_073_741_824
MAX_LEDGER_ENTRIES = 4096
MAX_LEDGER_ID = 256
SHA256_LEN = 64
SHA256_ALPHABET = set("0123456789abcdef")
GEFS_ADMISSIBLE_STATUSES = frozenset({"SCOPE_INDEPENDENCE_CONFIRMED"})
ECMWF_STATUSES = frozenset({"HELD", "RESUMED"})
HELD_ECMWF_MODELS = frozenset({"IFS", "AIFS"})
HELD_ECMWF_ORIGINS = frozenset({
    "https://ecmwf-forecasts.s3.eu-central-1.amazonaws.com",
    "https://data.ecmwf.int",
})
GEFS_ORIGINS = frozenset({"https://noaa-gefs-pds.s3.amazonaws.com"})
# The three imported denials are part of the cumulative held history, not a
# replaceable sample. These response identities are also retained in the
# public A5/A6 audit; later records may be appended without dropping them.
RETAINED_DENIAL_DIGESTS = {
    (503, "2026-09-30T07:48:13.707996+00:00", "7c21325b9a8c5d3b7f06bed411ae11e6fa6dcb490320671bd8e1d49a64956a28"):
        "0248f8d2eca50e8793d432c17cbe4f4ab3c1f7f3dd485224a8764b853f9b825f",
    (503, "2026-09-30T07:54:39.435641+00:00", "0986be0818f5c4e80bddac64bcd37d8f77da4c43acb380aaa7e4bcb677a51460"):
        "d77cc0c632d3f49947379b4d8f9b820f918957ef637df1ecf425ae0404d2dd87",
    (429, "2026-09-30T07:55:15.376499+00:00", "3850dfdbf4489250268b5f0740240a9f4445e7c5c29e1d03aa0c5446808d7507"):
        "66b3d331c69330a8beace6cab76dbdd73a22a80fcc0bb31c95a69f43c700bcba",
}

# -- Frozen P1 proposal (protocol section 1/3/4/5) --------------------------

FROZEN_SCALARS = {
    "schema": PACKAGE_SCHEMA_NAME,
    "campaign_id": "alpha-v11-evidence-preflight-20261002",
    "stage_id": "P1_GEFS_INDEX",
    "scope": "INDEX_DISCOVERY_ONLY_NOT_CAPTURE",
    "run_utc": "2026-10-02T00:00:00Z",
    "baseline_commit": "74075b524b0301fdd13a45d03d9c42876564a9d3",
    "state": "PREPARED_BLOCKED",
    "capture_g3l": "NO_GO",
    "capture_eligibility": False,
    "execution_authority": False,
    "qualification_credit": 0,
}

FROZEN_REQUEST = {
    "request_id": "p1-gefs-2026100200-c00-f024-index",
    "method": "GET",
    "origin": "https://noaa-gefs-pds.s3.amazonaws.com",
    "path": "/gefs.20261002/00/atmos/pgrb2ap5/gec00.t00z.pgrb2a.0p50.f024.idx",
    "port": 443,
    "provider": "GEFS",
    "purpose": "INDEX_DISCOVERY_ONLY",
    "query": None,
    "range": None,
    "request_body": None,
    # Access/mapping are proposal-stage only; the protocol requires they be
    # independently established by a separate reviewed prerequisite, never by
    # this request record self-asserting a qualified/granted value.
    "access_status": "UNQUALIFIED",
    "mapping_status": "PROPOSED_UNQUALIFIED",
}

FROZEN_REQUEST_HEADERS = {
    "Accept": "text/plain",
    "Accept-Encoding": "identity",
    "Connection": "close",
    "Host": "noaa-gefs-pds.s3.amazonaws.com",
    "User-Agent": "AlphaV11-EvidencePreflight/1",
}

FROZEN_WINDOW = {
    "automatic_roll_forward": False,
    "dispatch_not_before_utc": "2026-10-02T10:00:00Z",
    "expires_utc": "2026-10-02T13:30:00Z",
}

FROZEN_RESPONSE_CONTRACT = {
    "automatic_chained_request": False,
    "content_encoding": "identity",
    "content_length_max": 3_145_728,
    "content_length_min": 1,
    "etag_scope": "INDEX_DIAGNOSTIC_ONLY",
    "grib_decode": False,
    "http_status": 200,
    "transfer_encoding": None,
    # The parser implementation is absent until a separately reviewed
    # implementation exists (protocol section 3); this status cannot change
    # within this package's scope.
    "parser_status": "ABSENT_REQUIRES_INDEPENDENT_IMPLEMENTATION_REVIEW",
}

FROZEN_LIMITS = {
    "address_space_bytes": 268_435_456,
    "attempt_deadline_seconds": 30,
    "campaign_attempts": 8,
    "campaign_body_bytes": 33_554_432,
    "campaign_elapsed_seconds": 120,
    "clock_calibration_max_age_seconds": 60,
    "clock_uncertainty_seconds": 1,
    "combined_preflight_capture_attempts": 3600,
    "combined_preflight_capture_body_bytes": 1_073_741_824,
    "cpu_seconds": 10,
    "descriptor_bytes": 4096,
    "diagnostic_output_bytes": 1_048_576,
    "dns_answers": 16,
    "free_disk_floor_after_reservation_bytes": 2_147_483_648,
    "free_disk_target_after_reservation_bytes": 3_221_225_472,
    "header_bytes": 4096,
    "header_count": 32,
    "header_name_bytes": 64,
    "header_value_bytes": 1024,
    "in_flight": 1,
    "index_line_bytes": 4096,
    "index_rows": 8192,
    "journal_bytes_each": 8_388_608,
    "journal_record_bytes": 65_536,
    "journal_records_each": 2048,
    "memory_floor_after_reservation_bytes": 536_870_912,
    "minimum_start_spacing_seconds": 2,
    "physical_storage_reservation_bytes": 67_108_864,
    "stage_attempts": 1,
    "stage_body_bytes": 3_145_728,
    "stage_elapsed_seconds": 60,
    "working_rss_bytes": 134_217_728,
}

FROZEN_DISALLOWED = frozenset({
    "ECMWF", "FIELD", "HEAD", "METADATA", "PROBE", "REDIRECT", "RETRY",
    "FAILOVER", "CREDENTIALS", "NATIVE_DECODE", "FINANCIAL_EXECUTION",
    "V10_CHANGE", "AUTHORITY_INSTALLATION",
})

FROZEN_BINDING_SCALARS = {
    "schema": BINDING_SCHEMA_NAME,
    "baseline_commit": "74075b524b0301fdd13a45d03d9c42876564a9d3",
    "formal": "1/50",
    "g3l": "NO_GO",
    "intended_review_scope": "DESIGN_AND_EXACT_BLOCKED_PRIVATE_PACKAGE_NOT_EXECUTION",
    "native_decode_calls": 0,
    "provider_requests": 0,
    "qualification_credit": 0,
    "score": "91/200",
    "status": "CANDIDATE_DESIGN_AND_BLOCKED_PACKAGE",
}
FROZEN_BINDING_VERDICTS = frozenset({"PASS_IN_SCOPE_DESIGN_BLOCKED_PACKAGE", "CHANGES_REQUIRED"})

# -- Closed key sets ---------------------------------------------------------

PACKAGE_KEYS = frozenset({
    "author_model", "baseline_commit", "blocking_reasons", "campaign_id",
    "capture_eligibility", "capture_g3l", "directories", "disallowed",
    "execution_authority", "independent_execution_review", "input_refs",
    "limits", "prerequisites", "qualification_credit", "requests",
    "response_contract", "restrictions_ref", "run_utc", "schema", "scope",
    "stage_id", "state", "storage_qualification",
    "unknown_outputs_not_required_as_inputs", "window",
})
REQUEST_KEYS = frozenset({
    "access_status", "mapping_status", "method", "origin", "path", "port",
    "provider", "purpose", "query", "range", "request_body",
    "request_headers", "request_id", "restriction_status",
})
WINDOW_KEYS = frozenset({"automatic_roll_forward", "dispatch_not_before_utc", "expires_utc"})
RESPONSE_CONTRACT_KEYS = frozenset({
    "automatic_chained_request", "content_encoding", "content_length_max",
    "content_length_min", "etag_scope", "grib_decode", "http_status",
    "parser_status", "transfer_encoding",
})
STORAGE_QUALIFICATION_KEYS = frozenset({
    "live_ledger_created", "persistence_review", "physically_reserved_bytes",
})
EXEC_REVIEW_KEYS = frozenset({"binding", "present"})
DIRECTORY_KEYS = frozenset({"device", "inode", "mode", "path", "qualification", "uid"})
REF_KEYS = frozenset({"byte_length", "path", "repository_path", "sha256"})
REF_KEYS_NO_REPO = frozenset({"byte_length", "path", "sha256"})
OWNER_REF_KEYS = frozenset({"byte_length", "path", "qualification", "sha256"})
PREREQ_KEYS = frozenset({
    "accepted_protocol_design_review", "anonymous_access_and_exact_path_review",
    "clock_method_and_calibration_review",
    "complete_restriction_and_control_domain_review",
    "dns_tls_trust_and_bounded_transport_review",
    "independently_retained_history_head", "index_parser_semantics_review",
    "owner_directive_original_record",
    "post_reservation_resource_enforcement_review",
    "private_storage_persistence_lock_and_reservation_review",
    "runtime_implementation_review", "runtime_source_and_transitive_build_lock",
    "shared_lineage_and_unfinished_intent_reconciliation",
})
NULLABLE_PREREQS = PREREQ_KEYS - {"owner_directive_original_record"}

RESTRICTIONS_KEYS = frozenset({
    "complete_lineage_review", "execution_authority", "inventory_audit_ref",
    "known_control_domains", "raw_source_refs", "records", "schema",
    "shared_history_head", "status", "unresolved_attempt_reconciliation",
})
ECMWF_DOMAIN_KEYS = frozenset({"models", "origins", "resumption_review", "status"})
GEFS_DOMAIN_KEYS = frozenset({"origins", "scope_independence_review", "status"})
RESTRICTION_RECORD_KEYS = frozenset({"capture", "expiry_adjudication", "response"})
RESPONSE_CORE_KEYS = frozenset({"status", "received_at"})
RESPONSE_EARLY_KEYS = RESPONSE_CORE_KEYS | {"headers", "sha256", "url"}
RESPONSE_CAPTURE_KEYS = RESPONSE_CORE_KEYS | {
    "bytes", "evidence_class", "headers", "path", "sha256", "source",
}

BINDING_KEYS = frozenset({
    "allowed_review_verdicts", "baseline_commit", "formal", "g3l",
    "intended_review_scope", "missing_prerequisites", "native_decode_calls",
    "owner_instruction_record", "prepared_at_utc", "private_package",
    "private_restrictions", "private_root", "protocol", "provider_requests",
    "qualification_credit", "raw_restriction_sources", "schema", "score",
    "source_inputs", "status",
})


class PreflightPackageCheckerError(Exception):
    """Raised only for input that cannot be checked at all (not JSON)."""


def strict_json_loads(raw: bytes) -> Any:
    """Parse JSON, rejecting duplicate keys and nonfinite constants."""

    def _reject_dupes(pairs: Sequence[tuple]) -> dict:
        seen = set()
        out = {}
        for k, v in pairs:
            if k in seen:
                raise PreflightPackageCheckerError(f"duplicate key: {k!r}")
            seen.add(k)
            out[k] = v
        return out

    def _reject_nonfinite(token: str) -> float:
        raise PreflightPackageCheckerError(f"nonfinite JSON constant: {token}")

    def _reject_overflowing_float(token: str) -> float:
        value = float(token)
        # A numeric literal (e.g. ``1e999``) can parse to +/-inf without ever
        # reaching ``parse_constant``, which only sees the named literals
        # ``NaN``/``Infinity``/``-Infinity``. Reject that case the same way.
        if value != value or value in (float("inf"), float("-inf")):
            raise PreflightPackageCheckerError(f"nonfinite JSON number literal: {token}")
        return value

    try:
        parsed = json.loads(
            raw,
            object_pairs_hook=_reject_dupes,
            parse_constant=_reject_nonfinite,
            parse_float=_reject_overflowing_float,
        )
        # JSON escape sequences can decode to lone UTF-16 surrogates. Those
        # are not Unicode scalar values and cannot be encoded for the retained
        # denial digest. Check every key and value before any policy path can
        # canonicalize one; use an explicit stack for bounded nested input.
        pending = [parsed]
        while pending:
            value = pending.pop()
            if isinstance(value, str):
                if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
                    raise PreflightPackageCheckerError("invalid Unicode scalar")
            elif isinstance(value, dict):
                pending.extend(value.keys())
                pending.extend(value.values())
            elif isinstance(value, list):
                pending.extend(value)
        return parsed
    except PreflightPackageCheckerError:
        raise
    except Exception as exc:  # noqa: BLE001 - surfaced as a single checker reason
        raise PreflightPackageCheckerError(f"invalid JSON: {exc}") from exc


def _is_int(v: Any) -> bool:
    return type(v) is int


def _is_bool(v: Any) -> bool:
    return type(v) is bool


def _is_bounded_str(v: Any, max_len: int = MAX_STR) -> bool:
    return (
        isinstance(v, str) and 0 < len(v) <= max_len
        and not any(0xD800 <= ord(char) <= 0xDFFF for char in v)
    )


def _is_sha256(v: Any) -> bool:
    return (
        isinstance(v, str)
        and len(v) == SHA256_LEN
        and set(v) <= SHA256_ALPHABET
    )


def _is_ref(v: Any, keys: frozenset) -> bool:
    if not isinstance(v, dict) or set(v.keys()) != keys:
        return False
    if not _is_sha256(v.get("sha256")):
        return False
    if not (_is_int(v.get("byte_length")) and 0 < v["byte_length"] <= MAX_REF_BYTES):
        return False
    if not _is_bounded_str(v.get("path")):
        return False
    if "repository_path" in keys and not _is_bounded_str(v.get("repository_path")):
        return False
    if "qualification" in keys and not _is_bounded_str(v.get("qualification")):
        return False
    return True


def _is_ref_list(v: Any, keys: frozenset, max_len: int = MAX_ARRAY) -> bool:
    return isinstance(v, list) and len(v) <= max_len and all(_is_ref(x, keys) for x in v)


def _parse_utc(v: Any) -> Optional[datetime]:
    if not _is_bounded_str(v):
        return None
    # fromisoformat silently truncates extra fractional digits and, on this
    # interpreter, discards fractional offsets whose whole seconds are zero.
    # Only accept an explicit time to microsecond precision and a Z or
    # minute-resolution numeric offset, so the parsed UTC instant preserves
    # every supplied timing component at the dispatch boundaries.
    if re.fullmatch(
        r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z|[+-]\d{2}:\d{2})",
        v,
    ) is None:
        return None
    try:
        parsed = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        # A naive timestamp (no explicit offset) must never be silently
        # reinterpreted in the host's local timezone; refuse it instead.
        return None
    try:
        return parsed.astimezone(timezone.utc)
    except OverflowError:
        # astimezone() on a near-datetime.MIN/MAX value with an extreme UTC
        # offset can overflow; treat it as unparseable, not fatal.
        return None


@dataclass(frozen=True)
class ClockObservation:
    measured_utc: str
    uncertainty_seconds: float
    calibration_age_seconds: float
    monotonic_consistent: bool
    source: str = "LOCAL_AUTHORIZED_ONLY"


@dataclass(frozen=True)
class ResourceObservation:
    free_disk_bytes_after_reservation: int
    mem_available_bytes_after_reservation: int
    physically_reserved_bytes: int


@dataclass(frozen=True)
class LedgerEntry:
    campaign_id: str
    request_id: str
    attempted: bool = True


@dataclass(frozen=True)
class StateLedger:
    entries: Sequence[LedgerEntry] = field(default_factory=tuple)

    def has_attempted(self, campaign_id: str, request_id: str) -> bool:
        return any(
            e.campaign_id == campaign_id and e.request_id == request_id and e.attempted
            for e in self.entries
        )

    def has_campaign(self, campaign_id: str) -> bool:
        return any(e.campaign_id == campaign_id for e in self.entries)


def _valid_ledger(ledger: Any) -> bool:
    # The caller supplies durable history. A false-valued or malformed field
    # must never be interpreted as an empty/fresh history. Bound the retained
    # campaign view before searching it; a larger history needs an explicit
    # reviewed retention/partition decision.
    return (
        type(ledger) is StateLedger
        and type(ledger.entries) in (tuple, list)
        and len(ledger.entries) <= MAX_LEDGER_ENTRIES
        and all(
            type(entry) is LedgerEntry
            and _is_bounded_str(entry.campaign_id, MAX_LEDGER_ID)
            and _is_bounded_str(entry.request_id, MAX_LEDGER_ID)
            and _is_bool(entry.attempted)
            for entry in ledger.entries
        )
    )


@dataclass(frozen=True)
class CheckResult:
    schema: str
    outcome: str
    eligibility: str
    refusal_reasons: tuple

    def __post_init__(self) -> None:
        # Unconditional (not `assert`, which Python -O strips): a third
        # outcome value or an inconsistent reasons/outcome pairing must never
        # be constructible, optimized interpreter or not.
        if self.outcome not in ALLOWED_OUTCOMES:
            raise ValueError(f"not an allowed outcome: {self.outcome!r}")
        if self.eligibility != ELIGIBILITY_LABEL:
            raise ValueError(f"not the fixed eligibility label: {self.eligibility!r}")
        if (self.outcome == OUTCOME_REFUSED) != bool(self.refusal_reasons):
            raise ValueError("refusal_reasons must be non-empty iff outcome is refused")

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "outcome": self.outcome,
            "eligibility": self.eligibility,
            "refusal_reasons": sorted(self.refusal_reasons),
        }


def _check_closed(obj: Any, keys: frozenset, label: str, reasons: list) -> None:
    if not isinstance(obj, dict):
        reasons.append(f"NOT_AN_OBJECT:{label}")
        return
    for k in obj:
        if not isinstance(k, str) or k not in keys:
            reasons.append(f"UNKNOWN_KEY:{label}.{k}")
    for k in keys:
        if k not in obj:
            reasons.append(f"MISSING_KEY:{label}.{k}")


def _check_frozen_scalars(obj: Mapping, frozen: Mapping, label: str, reasons: list) -> None:
    for key, expected in frozen.items():
        actual = obj.get(key)
        # Exact-type equality rejects bool-as-integer (True/1, False/0) and
        # any other cross-type coincidental equality (protocol section 2).
        if type(actual) is not type(expected) or actual != expected:
            reasons.append(f"CHANGED_FIELD:{label}.{key}")


def _is_closed_string_set(v: Any, max_len: int = MAX_ARRAY) -> bool:
    return (
        isinstance(v, list)
        and len(v) <= max_len
        and all(_is_bounded_str(x) for x in v)
        and len(v) == len(set(v))
    )


def _is_nonempty_string_set(v: Any) -> bool:
    return _is_closed_string_set(v) and len(v) > 0


def _check_disallowed(obj: Mapping, reasons: list) -> None:
    disallowed = obj.get("disallowed")
    if not _is_closed_string_set(disallowed):
        reasons.append("MALFORMED_DISALLOWED_LIST")
        return
    if set(disallowed) != FROZEN_DISALLOWED:
        reasons.append("TAMPERED_DISALLOWED_LIST")


def _check_prerequisites(obj: Mapping, reasons: list) -> bool:
    prereq = obj.get("prerequisites")
    _check_closed(prereq, PREREQ_KEYS, "prerequisites", reasons)
    if not isinstance(prereq, dict):
        return False
    owner_ref = prereq.get("owner_directive_original_record")
    if not _is_ref(owner_ref, OWNER_REF_KEYS) or owner_ref.get("qualification") != \
            "OWNER_INSTRUCTION_ONLY_NOT_PROVIDER_RIGHTS":
        reasons.append("MISSING_OR_INVALID_OWNER_DIRECTIVE_RECORD")
    all_satisfied = True
    for key in NULLABLE_PREREQS:
        val = prereq.get(key)
        if val is None:
            reasons.append(f"NULL_PREREQUISITE:{key}")
            all_satisfied = False
        elif not _is_ref(val, REF_KEYS_NO_REPO):
            reasons.append(f"MALFORMED_PREREQUISITE_REFERENCE:{key}")
            all_satisfied = False
    return all_satisfied


def _is_finite_nonneg(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (
        float("inf"), float("-inf")) and v >= 0


def _limit_int(limits: Mapping, key: str) -> int:
    """Fetch a numeric limit, falling back to the frozen default if the
    package's own value is not a plain int. A tampered limit is already
    flagged elsewhere via _check_frozen_scalars; this only prevents a
    non-numeric/boolean value from crashing a downstream comparison."""
    v = limits.get(key)
    return v if _is_int(v) else FROZEN_LIMITS[key]


def _check_storage(obj: Mapping, resources: ResourceObservation, reasons: list) -> bool:
    sq = obj.get("storage_qualification")
    _check_closed(sq, STORAGE_QUALIFICATION_KEYS, "storage_qualification", reasons)
    if type(resources) is not ResourceObservation:
        reasons.append("INVALID_RESOURCE_OBSERVATION")
        return False
    limits = obj.get("limits") if isinstance(obj.get("limits"), dict) else {}
    ok = True
    if not isinstance(sq, dict):
        return False
    if not _is_ref(sq.get("persistence_review"), REF_KEYS_NO_REPO):
        reasons.append("MISSING_STORAGE_PERSISTENCE_REVIEW")
        ok = False
    if sq.get("live_ledger_created") is not True:
        reasons.append("MISSING_LIVE_LEDGER")
        ok = False

    floor = _limit_int(limits, "physical_storage_reservation_bytes")
    reserved = sq.get("physically_reserved_bytes")
    if not _is_int(reserved) or reserved < floor:
        reasons.append("NO_PHYSICAL_STORAGE_RESERVATION")
        ok = False
    observations_valid = all(_is_int(value) and value >= 0 for value in (
        resources.free_disk_bytes_after_reservation,
        resources.mem_available_bytes_after_reservation,
        resources.physically_reserved_bytes,
    ))
    if not observations_valid:
        reasons.append("INVALID_RESOURCE_OBSERVATION")
        return False
    if _is_int(reserved) and reserved >= floor and reserved != resources.physically_reserved_bytes:
        reasons.append("INCONSISTENT_STORAGE_RESERVATION")
        ok = False
    disk_floor = _limit_int(limits, "free_disk_floor_after_reservation_bytes")
    mem_floor = _limit_int(limits, "memory_floor_after_reservation_bytes")
    if resources.free_disk_bytes_after_reservation < disk_floor:
        reasons.append("INSUFFICIENT_POST_RESERVATION_DISK")
        ok = False
    if resources.mem_available_bytes_after_reservation < mem_floor:
        reasons.append("INSUFFICIENT_POST_RESERVATION_MEMORY")
        ok = False
    return ok


def _check_clock(obj: Mapping, clock: ClockObservation, reasons: list) -> bool:
    if type(clock) is not ClockObservation:
        reasons.append("INVALID_CLOCK_OBSERVATION")
        return False
    limits = obj.get("limits") if isinstance(obj.get("limits"), dict) else {}
    window = obj.get("window") if isinstance(obj.get("window"), dict) else {}
    ok = True
    if type(clock.source) is not str or clock.source != "LOCAL_AUTHORIZED_ONLY":
        reasons.append("INVALID_CLOCK_SOURCE")
        ok = False
    if clock.monotonic_consistent is not True:
        # Exact-type check: a truthy non-bool (e.g. the string "false") must
        # never be accepted in place of the real boolean flag.
        reasons.append("NONMONOTONIC_CLOCK")
        ok = False
    uncertainty_valid = _is_finite_nonneg(clock.uncertainty_seconds)
    if not uncertainty_valid:
        reasons.append("INVALID_CLOCK_UNCERTAINTY")
        ok = False
    elif clock.uncertainty_seconds > _limit_int(limits, "clock_uncertainty_seconds"):
        reasons.append("EXCESSIVE_CLOCK_UNCERTAINTY")
        ok = False
    if not _is_finite_nonneg(clock.calibration_age_seconds):
        reasons.append("INVALID_CALIBRATION_AGE")
        ok = False
    elif clock.calibration_age_seconds > _limit_int(limits, "clock_calibration_max_age_seconds"):
        reasons.append("EXPIRED_CLOCK_CALIBRATION")
        ok = False
    measured = _parse_utc(clock.measured_utc)
    lower = _parse_utc(window.get("dispatch_not_before_utc"))
    upper = _parse_utc(window.get("expires_utc"))
    if measured is None:
        reasons.append("UNPARSEABLE_CLOCK")
        ok = False
    elif lower is None or upper is None:
        reasons.append("UNPARSEABLE_WINDOW")
        ok = False
    else:
        # Fold the clock's own stated uncertainty into the bound check: the
        # true time could be anywhere in [measured-u, measured+u], so the
        # window must hold for the whole interval, not just the point value.
        # timedelta(seconds=float) rounds to microseconds and can round
        # *inward*, accepting an interval that begins just before the window.
        # Convert the exact represented numeric value to microseconds and
        # round *outward* to the next supported timestamp tick. This can
        # conservatively refuse a sub-microsecond-safe edge; it cannot erase
        # stated uncertainty. Invalid values use zero here because they are
        # already refused above. Oversized values retain an explicit refusal.
        try:
            ticks = Fraction(clock.uncertainty_seconds if uncertainty_valid else 0) * 1_000_000
            rounded_up_us = (ticks.numerator + ticks.denominator - 1) // ticks.denominator
            margin = timedelta(microseconds=rounded_up_us)
            before_window = measured - margin < lower
            after_window = measured + margin >= upper
        except OverflowError:
            reasons.append("CLOCK_UNCERTAINTY_MARGIN_OVERFLOW")
            ok = False
        else:
            if before_window:
                reasons.append("CLOCK_BEFORE_WINDOW_START")
                ok = False
            if after_window:
                reasons.append("EXPIRED_WINDOW")
                ok = False
    return ok


def _check_execution_review(obj: Mapping, reasons: list) -> bool:
    review = obj.get("independent_execution_review")
    _check_closed(review, EXEC_REVIEW_KEYS, "independent_execution_review", reasons)
    if not isinstance(review, dict):
        return False
    if review.get("binding") != "DETACHED_ENVELOPE_OF_FINAL_PACKAGE_AND_RUNTIME":
        reasons.append("INVALID_EXECUTION_REVIEW_BINDING")
        return False
    if review.get("present") is not True:
        reasons.append("MISSING_EXECUTION_REVIEW")
        return False
    return True


def _is_restriction_record(v: Any) -> bool:
    if not isinstance(v, dict) or set(v) != RESTRICTION_RECORD_KEYS:
        return False
    if not _is_bounded_str(v.get("capture")):
        return False
    expiry = v.get("expiry_adjudication")
    if expiry is not None and not _is_ref(expiry, REF_KEYS_NO_REPO):
        return False
    response = v.get("response")
    if not isinstance(response, dict):
        return False
    keys = set(response)
    if keys not in (RESPONSE_EARLY_KEYS, RESPONSE_CAPTURE_KEYS):
        return False
    status = response.get("status")
    if not _is_int(status) or not 100 <= status <= 599 or _parse_utc(response.get("received_at")) is None:
        return False
    headers = response.get("headers")
    if not isinstance(headers, dict) or len(headers) > MAX_ARRAY or not all(
        _is_bounded_str(k) and _is_bounded_str(value)
        for k, value in headers.items()
    ) or not _is_sha256(response.get("sha256")):
        return False
    if keys == RESPONSE_EARLY_KEYS:
        return _is_bounded_str(response.get("url"))
    return (
        _is_int(response.get("bytes")) and response["bytes"] >= 0
        and _is_bounded_str(response.get("evidence_class"))
        and _is_bounded_str(response.get("path"))
        and _is_bounded_str(response.get("source"))
    )


def _retained_record_digest(record: dict) -> str:
    # Expiry adjudication may later be attached by independent review; the
    # original capture and response themselves remain immutable.
    original = {"capture": record["capture"], "response": record["response"]}
    canonical = json.dumps(original, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _check_restriction_domains(restrictions: Mapping, reasons: list) -> bool:
    domains = restrictions.get("known_control_domains")
    if not isinstance(domains, dict) or set(domains.keys()) != {"ECMWF", "GEFS"}:
        reasons.append("MALFORMED_KNOWN_CONTROL_DOMAINS")
        return False
    ecmwf = domains.get("ECMWF")
    _check_closed(ecmwf, ECMWF_DOMAIN_KEYS, "known_control_domains.ECMWF", reasons)
    ok = True
    if isinstance(ecmwf, dict):
        if not _is_nonempty_string_set(ecmwf.get("models")) or not \
                HELD_ECMWF_MODELS <= set(ecmwf["models"]):
            reasons.append("MALFORMED_ECMWF_MODELS_OR_HELD_SCOPE")
            ok = False
        if not _is_nonempty_string_set(ecmwf.get("origins")) or not \
                HELD_ECMWF_ORIGINS <= set(ecmwf["origins"]):
            reasons.append("MALFORMED_ECMWF_ORIGINS_OR_HELD_SCOPE")
            ok = False
        status = ecmwf.get("status")
        if not _is_bounded_str(status) or status not in ECMWF_STATUSES:
            reasons.append("MALFORMED_ECMWF_STATUS")
            ok = False
        resumption = ecmwf.get("resumption_review")
        if resumption is not None and not _is_ref(resumption, REF_KEYS_NO_REPO):
            reasons.append("MALFORMED_ECMWF_RESUMPTION_REVIEW")
            ok = False
        # A resumption away from HELD is only ever acceptable bound to its
        # own independently reviewed resumption record (protocol section 4);
        # HELD itself needs no additional evidence.
        if status != "HELD" and not _is_ref(resumption, REF_KEYS_NO_REPO):
            reasons.append("ECMWF_RESUMPTION_UNREVIEWED")
            ok = False
    gefs = domains.get("GEFS")
    _check_closed(gefs, GEFS_DOMAIN_KEYS, "known_control_domains.GEFS", reasons)
    if isinstance(gefs, dict):
        if not _is_nonempty_string_set(gefs.get("origins")) or not \
                GEFS_ORIGINS <= set(gefs["origins"]):
            reasons.append("MALFORMED_GEFS_ORIGINS_OR_SCOPE")
            ok = False
        if not _is_ref(gefs.get("scope_independence_review"), REF_KEYS_NO_REPO):
            reasons.append("UNRESOLVED_GEFS_SCOPE")
            ok = False
        gefs_status = gefs.get("status")
        if gefs_status == "BLOCKED_UNKNOWN_LINEAGE_AND_SCOPE":
            reasons.append("GEFS_LINEAGE_UNRESOLVED")
            ok = False
        if not _is_bounded_str(gefs_status) or gefs_status not in GEFS_ADMISSIBLE_STATUSES:
            # Equality between two documents' status strings (e.g. both HELD,
            # DENIED, UNKNOWN or empty) is not proof of an admissible state;
            # only the one explicitly reviewed confirmed status qualifies.
            reasons.append("GEFS_STATUS_NOT_ADMISSIBLE")
            ok = False
    if not _is_ref(restrictions.get("complete_lineage_review"), REF_KEYS_NO_REPO):
        reasons.append("NULL_COMPLETE_LINEAGE_REVIEW")
        ok = False
    if not _is_sha256(restrictions.get("shared_history_head")):
        reasons.append("NULL_SHARED_HISTORY_HEAD")
        ok = False
    if not _is_ref(restrictions.get("unresolved_attempt_reconciliation"), REF_KEYS_NO_REPO):
        reasons.append("NULL_UNRESOLVED_ATTEMPT_RECONCILIATION")
        ok = False
    if not _is_ref(restrictions.get("inventory_audit_ref"), REF_KEYS_NO_REPO):
        reasons.append("MISSING_OR_MALFORMED_INVENTORY_AUDIT_REF")
        ok = False
    if not _is_bounded_str(restrictions.get("status")):
        reasons.append("MALFORMED_RESTRICTIONS_STATUS")
        ok = False
    if restrictions.get("execution_authority") is not False:
        reasons.append("FORBIDDEN_RESTRICTIONS_EXECUTION_AUTHORITY_PROMOTION")
        ok = False
    records = restrictions.get("records")
    if not isinstance(records, list) or not (1 <= len(records) <= MAX_ARRAY) or \
            not all(_is_restriction_record(r) for r in records):
        reasons.append("MISSING_OR_MALFORMED_RESTRICTION_RECORDS")
        ok = False
    else:
        identities = [
            (r["response"]["status"], r["response"]["received_at"], r["response"]["sha256"])
            for r in records
        ]
        if len(identities) != len(set(identities)) or not set(RETAINED_DENIAL_DIGESTS) <= set(identities):
            reasons.append("MISSING_OR_DUPLICATED_RETAINED_DENIAL")
            ok = False
        for record, identity in zip(records, identities):
            if identity in RETAINED_DENIAL_DIGESTS and \
                    _retained_record_digest(record) != RETAINED_DENIAL_DIGESTS[identity]:
                reasons.append("TAMPERED_RETAINED_DENIAL")
                ok = False
    raw_refs = restrictions.get("raw_source_refs")
    if not isinstance(raw_refs, list) or not (1 <= len(raw_refs) <= MAX_ARRAY) or \
            not all(_is_ref(r, REF_KEYS_NO_REPO) for r in raw_refs):
        reasons.append("MISSING_OR_MALFORMED_RAW_SOURCE_REFS")
        ok = False
    return ok


def _check_request(obj: Mapping, reasons: list) -> None:
    requests = obj.get("requests")
    if not isinstance(requests, list) or len(requests) > FROZEN_LIMITS["stage_attempts"]:
        reasons.append("MULTIPLE_REQUESTS_NOT_PERMITTED")
        return
    if len(requests) != 1:
        reasons.append("MULTIPLE_REQUESTS_NOT_PERMITTED")
        return
    req = requests[0]
    _check_closed(req, REQUEST_KEYS, "requests[0]", reasons)
    if not isinstance(req, dict):
        return
    if req.get("purpose") != "INDEX_DISCOVERY_ONLY":
        reasons.append("ATTEMPTED_FIELD_OR_NONINDEX_REQUEST")
    if req.get("method") == "HEAD":
        reasons.append("ATTEMPTED_HEAD_REQUEST")
    if req.get("request_headers") != FROZEN_REQUEST_HEADERS:
        reasons.append("CHANGED_REQUEST_HEADERS")
    origin = req.get("origin")
    if origin != FROZEN_REQUEST["origin"]:
        if isinstance(origin, str) and "ecmwf" in origin.lower():
            reasons.append("ATTEMPTED_ECMWF_REQUEST")
        else:
            reasons.append("CHANGED_REQUEST_ORIGIN")
    if req.get("path") != FROZEN_REQUEST["path"]:
        reasons.append("CHANGED_REQUEST_PATH")
    for key, expected in FROZEN_REQUEST.items():
        if key in ("origin", "path"):
            continue
        actual = req.get(key)
        # Exact-type equality: ordinary ``!=`` would accept 443.0 == 443 or
        # True == 1 in place of the real frozen scalar type.
        if type(actual) is not type(expected) or actual != expected:
            reasons.append(f"CHANGED_REQUEST_FIELD:{key}")


def _safe_parse(raw: bytes, label: str, reasons: list) -> Any:
    if len(raw) > MAX_RAW_BYTES:
        reasons.append(f"OVERSIZED_RAW_BYTES:{label}")
        return None
    try:
        parsed = strict_json_loads(raw)
    except PreflightPackageCheckerError:
        reasons.append(f"INVALID_JSON:{label}")
        return None
    if not isinstance(parsed, dict):
        # Valid JSON of the wrong top-level type (null/array/number/...)
        # must be refused with a labeled reason, never treated as a
        # dict-shaped result with no finding to explain the refusal.
        reasons.append(f"NOT_AN_OBJECT:{label}")
        return None
    return parsed


def _check_review_terminal(review_present: bool, review_terminal: Optional[Mapping], reasons: list) -> None:
    if not review_present:
        return
    required = {"exit_code", "error", "initial_clean", "verdict"}
    valid = (
        isinstance(review_terminal, dict)
        and required <= set(review_terminal.keys())
        and _is_int(review_terminal.get("exit_code")) and review_terminal.get("exit_code") == 0
        and review_terminal.get("error") is None
        and review_terminal.get("initial_clean") is True
        and review_terminal.get("verdict") == "EXECUTABLE_PREFLIGHT_PASS"
    )
    if not valid:
        reasons.append("MISSING_OR_INVALID_REVIEW_TERMINAL")


def _check_byte_ref(ref: Any, raw: bytes, reason: str, reasons: list) -> None:
    if not _is_ref(ref, REF_KEYS_NO_REPO):
        reasons.append(reason)
        return
    if ref.get("sha256") != _sha256_hex(raw) or ref.get("byte_length") != len(raw):
        reasons.append(reason)


def check_evidence_preflight_package(
    *,
    package_raw: bytes,
    restrictions_raw: bytes,
    binding_raw: bytes,
    protocol_raw: bytes,
    clock: ClockObservation,
    resources: ResourceObservation,
    ledger: StateLedger,
    review_terminal: Optional[Mapping] = None,
    restart_requested: bool = False,
) -> CheckResult:
    """Pure offline evaluation of one preflight package/restriction/binding
    triple against the frozen P1 proposal. Never performs a request.

    Every mapping is parsed here, directly from its own raw bytes, so a
    caller can never evaluate a mapping that does not actually correspond to
    the hashed/hash-checked bytes. ``ledger`` must be the caller's actual
    durable replay/reset history (an empty :class:`StateLedger` for a
    genuinely fresh campaign); it has no implicit default so a real caller
    cannot silently omit it and have every package look unreplayed.
    """

    reasons: list = []

    # The public API accepts exact raw byte strings. In particular, json.loads
    # also accepts decoded text, but that cannot be a supplied byte artifact
    # with an unambiguous length/hash. Refuse all unsupported containers before
    # len(), parsing or sha256 can reinterpret or crash on them.
    for label, raw in (("package", package_raw), ("restrictions", restrictions_raw),
                       ("binding", binding_raw), ("protocol", protocol_raw)):
        if type(raw) is not bytes:
            reasons.append(f"INVALID_RAW_BYTES:{label}")
    if reasons:
        return CheckResult(SCHEMA, OUTCOME_REFUSED, ELIGIBILITY_LABEL, tuple(reasons))

    package = _safe_parse(package_raw, "package", reasons)
    restrictions = _safe_parse(restrictions_raw, "restrictions", reasons)
    binding = _safe_parse(binding_raw, "binding", reasons)
    if not isinstance(package, dict) or not isinstance(restrictions, dict) or not isinstance(binding, dict):
        return CheckResult(SCHEMA, OUTCOME_REFUSED, ELIGIBILITY_LABEL, tuple(sorted(set(reasons))))

    _check_closed(package, PACKAGE_KEYS, "package", reasons)
    _check_closed(restrictions, RESTRICTIONS_KEYS, "restrictions", reasons)
    _check_closed(binding, BINDING_KEYS, "binding", reasons)

    if restrictions.get("schema") != RESTRICTIONS_SCHEMA_NAME:
        reasons.append("UNSUPPORTED_RESTRICTIONS_SCHEMA_VERSION")
    if binding.get("schema") != BINDING_SCHEMA_NAME:
        reasons.append("UNSUPPORTED_BINDING_SCHEMA_VERSION")

    _check_frozen_scalars(package, FROZEN_SCALARS, "package", reasons)
    _check_frozen_scalars(binding, FROZEN_BINDING_SCALARS, "binding", reasons)
    verdicts = binding.get("allowed_review_verdicts")
    if not _is_closed_string_set(verdicts) or set(verdicts) != FROZEN_BINDING_VERDICTS:
        reasons.append("CHANGED_FIELD:binding.allowed_review_verdicts")

    window = package.get("window")
    _check_closed(window, WINDOW_KEYS, "window", reasons)
    if isinstance(window, dict):
        _check_frozen_scalars(window, FROZEN_WINDOW, "window", reasons)

    rc = package.get("response_contract")
    _check_closed(rc, RESPONSE_CONTRACT_KEYS, "response_contract", reasons)
    if isinstance(rc, dict):
        _check_frozen_scalars(rc, FROZEN_RESPONSE_CONTRACT, "response_contract", reasons)
        if rc.get("grib_decode") is not False:
            reasons.append("FORBIDDEN_NATIVE_DECODE_ATTEMPT")

    limits = package.get("limits")
    _check_closed(limits, frozenset(FROZEN_LIMITS), "limits", reasons)
    if isinstance(limits, dict):
        _check_frozen_scalars(limits, FROZEN_LIMITS, "limits", reasons)

    _check_disallowed(package, reasons)
    _check_request(package, reasons)

    if not _is_bounded_str(package.get("author_model")):
        reasons.append("MALFORMED_AUTHOR_MODEL")
    if not _is_closed_string_set(package.get("blocking_reasons")):
        reasons.append("MALFORMED_BLOCKING_REASONS")
    if not _is_closed_string_set(package.get("unknown_outputs_not_required_as_inputs")):
        reasons.append("MALFORMED_UNKNOWN_OUTPUTS_NOT_REQUIRED_AS_INPUTS")

    if not _is_ref(binding.get("owner_instruction_record"), REF_KEYS_NO_REPO):
        reasons.append("MALFORMED_BINDING_OWNER_INSTRUCTION_RECORD")
    missing_prereqs = binding.get("missing_prerequisites")
    if not _is_closed_string_set(missing_prereqs) or not all(m in PREREQ_KEYS for m in missing_prereqs):
        reasons.append("MALFORMED_BINDING_MISSING_PREREQUISITES")
    if not _is_bounded_str(binding.get("private_root")):
        reasons.append("MALFORMED_BINDING_PRIVATE_ROOT")
    if _parse_utc(binding.get("prepared_at_utc")) is None:
        reasons.append("MALFORMED_BINDING_PREPARED_AT_UTC")
    if not _is_ref_list(binding.get("source_inputs"), REF_KEYS):
        reasons.append("MALFORMED_BINDING_SOURCE_INPUTS")
    if not _is_ref_list(binding.get("raw_restriction_sources"), REF_KEYS_NO_REPO):
        reasons.append("MALFORMED_BINDING_RAW_RESTRICTION_SOURCES")

    directories = package.get("directories")
    if not isinstance(directories, list) or not 1 <= len(directories) <= MAX_ARRAY:
        reasons.append("MALFORMED_DIRECTORIES")
    else:
        for i, d in enumerate(directories):
            _check_closed(d, DIRECTORY_KEYS, f"directories[{i}]", reasons)
            if not isinstance(d, dict) or not all(
                _is_int(d.get(k)) and d[k] >= 0 for k in ("device", "inode", "uid")
            ) or not all(_is_bounded_str(d.get(k)) for k in ("mode", "path")) or \
                    d.get("qualification") != "OBSERVATION_ONLY":
                reasons.append(f"MALFORMED_DIRECTORY:{i}")
    input_refs = package.get("input_refs")
    if not isinstance(input_refs, list) or not 1 <= len(input_refs) <= MAX_ARRAY:
        reasons.append("MALFORMED_INPUT_REFS")
    else:
        for i, ref in enumerate(input_refs):
            _check_closed(ref, REF_KEYS, f"input_refs[{i}]", reasons)
            if not _is_ref(ref, REF_KEYS) or not _is_bounded_str(ref.get("repository_path")):
                reasons.append(f"MALFORMED_INPUT_REF:{i}")

    _check_prerequisites(package, reasons)
    _check_storage(package, resources, reasons)
    _check_clock(package, clock, reasons)
    review_present = _check_execution_review(package, reasons)
    _check_review_terminal(review_present, review_terminal, reasons)
    _check_restriction_domains(restrictions, reasons)

    # Request-level restriction_status must actually reflect the restriction
    # inventory's own resolved GEFS status, not an independently-asserted
    # value a package could drift away from it.
    reqs_for_status = package.get("requests")
    if isinstance(reqs_for_status, list) and len(reqs_for_status) == 1 and isinstance(reqs_for_status[0], dict):
        gefs_domain = (restrictions.get("known_control_domains") or {}).get("GEFS") \
            if isinstance(restrictions.get("known_control_domains"), dict) else None
        gefs_status = gefs_domain.get("status") if isinstance(gefs_domain, dict) else None
        if reqs_for_status[0].get("restriction_status") != gefs_status:
            reasons.append("INCONSISTENT_REQUEST_RESTRICTION_STATUS")

    # Changed/dirty private bytes; every declared byte reference must match
    # both the hash and the declared length of the bytes actually supplied.
    restrictions_ref = package.get("restrictions_ref")
    _check_byte_ref(restrictions_ref, restrictions_raw, "CHANGED_PRIVATE_RESTRICTIONS_BYTES", reasons)
    _check_byte_ref(binding.get("private_package"), package_raw, "CHANGED_PRIVATE_PACKAGE_BYTES", reasons)
    _check_byte_ref(binding.get("private_restrictions"), restrictions_raw, "CHANGED_PRIVATE_RESTRICTIONS_BYTES", reasons)
    _check_byte_ref(binding.get("protocol"), protocol_raw, "CHANGED_PROTOCOL_BYTES", reasons)

    # Replay / reset (state-machine) checks.
    campaign_id = package.get("campaign_id")
    request_id = None
    reqs = package.get("requests")
    if isinstance(reqs, list) and len(reqs) == 1 and isinstance(reqs[0], dict):
        request_id = reqs[0].get("request_id")
    ledger_valid = _valid_ledger(ledger)
    if not ledger_valid:
        reasons.append("MALFORMED_STATE_LEDGER")
    if not _is_bool(restart_requested):
        reasons.append("MALFORMED_RESTART_REQUESTED")
    if ledger_valid and _is_bool(restart_requested) and \
            isinstance(campaign_id, str) and isinstance(request_id, str):
        if ledger.has_attempted(campaign_id, request_id):
            reasons.append("REPLAYED_REQUEST_ID")
        if restart_requested and ledger.has_campaign(campaign_id):
            reasons.append("CAMPAIGN_BUDGET_RESET_NOT_PERMITTED")

    outcome = OUTCOME_REFUSED if reasons else OUTCOME_SATISFIED
    return CheckResult(SCHEMA, outcome, ELIGIBILITY_LABEL, tuple(sorted(set(reasons))))


def _sha256_hex(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()
