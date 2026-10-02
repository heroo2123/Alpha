"""Offline fresh-window Gate 3 evidence-preflight *preparation* readiness
planner (protocol section 7 follow-on: the missing glue between "the one
frozen October 2 proposal expired" and "someone proposes a new exact-byte
package for a different date").

``tools/v11_gate3_evidence_preflight_checker.py`` only ever validates the one
frozen October 2 ``P1_GEFS_INDEX`` proposal: every scalar, the window, and the
request are checked against hard-coded frozen values, so it refuses (by
design) any other date or window outright. Nothing in the repository today
tells a caller *whether it is even worth authoring* a new dated package --
i.e. whether the standing prerequisites, clock-calibration method and storage
reservation are actually in hand yet. This module is that narrow upstream
check. It is not a package author, not a schema relaxation of the checker,
and not a substitute for the fresh exact-byte independent review every new
package still requires (protocol section 7: "A later package version
requires fresh exact-byte review, even for a date change").

This module creates no socket, imports no HTTP/provider client, runs no
subprocess, and performs no DNS, decode or filesystem write. It never invents
a clock measurement, a storage reservation, a provider access grant or a
reviewed-identity reference: every one of those is an explicit input the
caller must already hold, and a missing one is reported as incomplete, never
synthesized. ``PREPARATION_CANDIDATE_*`` confers no execution authority,
provider right, or G3-L credit -- it only states that the inputs supplied to
*this* function are internally consistent and clear the standing floors;
authoring, implementing and independently reviewing an actual exact-byte
package for the proposed window remains a separate, later, required step.

Deliberately reused rather than re-derived from
``tools/v11_gate3_evidence_preflight_checker.py`` (that module is not
modified by this one): the frozen resource/clock/limit floors
(``FROZEN_LIMITS``), the frozen expired October 2 window
(``FROZEN_WINDOW``) used only for roll-forward detection, the prerequisite
key sets, the generic bounded-reference/string/closed-key validators, and
the already-reviewed clock-interval and storage-floor check functions.
Re-implementing that arithmetic here (in particular the clock-uncertainty
window-overlap rounding) would risk a silent behavioral drift between two
copies of the same safety-critical comparison.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional

from tools.v11_gate3_evidence_preflight_checker import (
    FROZEN_LIMITS,
    FROZEN_WINDOW,
    OWNER_REF_KEYS,
    PREREQ_KEYS,
    NULLABLE_PREREQS,
    REF_KEYS_NO_REPO,
    RESTRICTIONS_SCHEMA_NAME,
    RESTRICTIONS_KEYS,
    WINDOW_KEYS,
    ClockObservation,
    PreflightPackageCheckerError,
    ResourceObservation,
    _check_closed,
    _check_clock,
    _check_restriction_domains,
    _check_storage,
    _is_ref,
    _parse_utc,
    strict_json_loads,
)

SCHEMA = "R09_GATE3_FRESH_WINDOW_READINESS_V1"

OUTCOME_REFUSED = "FRESH_WINDOW_REFUSED_BEFORE_PREPARATION"
OUTCOME_INCOMPLETE = "PREPARATION_INCOMPLETE_PREREQUISITES_MISSING"
OUTCOME_CANDIDATE = "PREPARATION_CANDIDATE_ALL_INPUTS_PRESENT_NOT_EXECUTABLE"
ALLOWED_OUTCOMES = (OUTCOME_REFUSED, OUTCOME_INCOMPLETE, OUTCOME_CANDIDATE)
# Deliberately distinct from the checker's ``DISCOVERY_ONLY_NOT_G3E`` label:
# this module runs strictly before any request/discovery proposal exists.
ELIGIBILITY_LABEL = "PREPARATION_ONLY_NOT_DISCOVERY_NOT_G3E"

FORBIDDEN_OUTCOME_LABELS = frozenset({
    "CAPTURED", "READY", "QUALIFIED", "G3L_PASS", "EXECUTABLE_PREFLIGHT_PASS",
    "GO", "PASS",
})


@dataclass(frozen=True)
class FreshWindowReadinessResult:
    schema: str
    outcome: str
    eligibility: str
    window_is_fresh: bool
    clock_ready: bool
    storage_ready: bool
    restriction_history_preserved: bool
    missing_prerequisites: tuple
    refusal_reasons: tuple
    incompleteness_reasons: tuple

    def __post_init__(self) -> None:
        # Unconditional checks (not ``assert``, which ``python -O`` strips):
        # an inconsistent result must never be constructible either way.
        if self.outcome not in ALLOWED_OUTCOMES:
            raise ValueError(f"not an allowed outcome: {self.outcome!r}")
        if self.outcome in FORBIDDEN_OUTCOME_LABELS:
            raise ValueError("forbidden outcome label")
        if self.eligibility != ELIGIBILITY_LABEL:
            raise ValueError(f"not the fixed eligibility label: {self.eligibility!r}")
        if self.outcome == OUTCOME_REFUSED and not self.refusal_reasons:
            raise ValueError("REFUSED outcome requires a non-empty refusal reason")
        if self.outcome != OUTCOME_REFUSED and self.refusal_reasons:
            raise ValueError("refusal_reasons must be empty unless outcome is REFUSED")
        if self.outcome == OUTCOME_INCOMPLETE and not self.incompleteness_reasons:
            raise ValueError("INCOMPLETE outcome requires a non-empty incompleteness reason")
        if self.outcome == OUTCOME_CANDIDATE and (
            self.incompleteness_reasons
            or self.missing_prerequisites
            or not self.window_is_fresh
            or not self.clock_ready
            or not self.storage_ready
            or not self.restriction_history_preserved
        ):
            raise ValueError("CANDIDATE outcome requires every readiness input to be clean")

    def to_dict(self) -> dict:
        return {
            "schema": self.schema,
            "outcome": self.outcome,
            "eligibility": self.eligibility,
            "window_is_fresh": self.window_is_fresh,
            "clock_ready": self.clock_ready,
            "storage_ready": self.storage_ready,
            "restriction_history_preserved": self.restriction_history_preserved,
            "missing_prerequisites": sorted(self.missing_prerequisites),
            "refusal_reasons": sorted(self.refusal_reasons),
            "incompleteness_reasons": sorted(self.incompleteness_reasons),
        }


def _is_exactly_false(v: Any) -> bool:
    return type(v) is bool and v is False


def evaluate_fresh_window_readiness(
    *,
    proposed_window: Mapping[str, Any],
    prerequisites: Mapping[str, Any],
    restrictions_raw: bytes,
    storage_qualification: Optional[Mapping[str, Any]],
    now_utc: str,
    clock: Optional[ClockObservation] = None,
    resources: Optional[ResourceObservation] = None,
    expired_window: Mapping[str, Any] = FROZEN_WINDOW,
) -> FreshWindowReadinessResult:
    """Evaluate whether a *proposed* (not yet authored) fresh-date preflight
    window has its upstream preparation prerequisites in hand.

    Every argument is an explicit caller-supplied observation or a
    previously reviewed reference; nothing here is measured, allocated or
    requested. ``restrictions_raw`` must be the caller's actual current
    restriction-inventory bytes (the same private ``restriction-history.json``
    shape the checker validates) so that the three retained ECMWF denials and
    the GEFS lineage block are read from real retained evidence, never a
    freshly-constructed empty history. A result of
    ``PREPARATION_CANDIDATE_ALL_INPUTS_PRESENT_NOT_EXECUTABLE`` still confers
    no execution authority, provider right, or G3-L credit: it only means an
    actual exact-byte package for this window is now worth authoring and
    sending for its own required independent review.
    """

    refusal: list = []
    incomplete: list = []

    # -- Structural / safety-tier checks (any one forces REFUSED) -----------

    if not isinstance(proposed_window, Mapping):
        refusal.append("INVALID_PROPOSED_WINDOW_TYPE")
    else:
        _check_closed(dict(proposed_window), WINDOW_KEYS, "proposed_window", refusal)

    if not isinstance(prerequisites, Mapping):
        refusal.append("INVALID_PREREQUISITES_TYPE")
    else:
        _check_closed(dict(prerequisites), PREREQ_KEYS, "prerequisites", refusal)

    if type(restrictions_raw) is not bytes:
        refusal.append("INVALID_RESTRICTIONS_RAW_TYPE")

    now = _parse_utc(now_utc)
    if now is None:
        refusal.append("UNPARSEABLE_NOW_UTC")

    if clock is not None and type(clock) is not ClockObservation:
        refusal.append("INVALID_CLOCK_OBSERVATION_TYPE")
    if resources is not None and type(resources) is not ResourceObservation:
        refusal.append("INVALID_RESOURCE_OBSERVATION_TYPE")
    if storage_qualification is not None and not isinstance(storage_qualification, Mapping):
        refusal.append("INVALID_STORAGE_QUALIFICATION_TYPE")

    window_is_fresh = False
    dispatch_lo = expires_hi = None
    if isinstance(proposed_window, Mapping) and not refusal:
        roll_forward = proposed_window.get("automatic_roll_forward")
        if not _is_exactly_false(roll_forward):
            refusal.append("AUTOMATIC_ROLL_FORWARD_MUST_BE_FALSE")
        dispatch_lo = _parse_utc(proposed_window.get("dispatch_not_before_utc"))
        expires_hi = _parse_utc(proposed_window.get("expires_utc"))
        if dispatch_lo is None or expires_hi is None:
            refusal.append("UNPARSEABLE_PROPOSED_WINDOW")
        elif dispatch_lo >= expires_hi:
            refusal.append("PROPOSED_WINDOW_NOT_ORDERED")
        elif now is not None and dispatch_lo <= now:
            refusal.append("PROPOSED_WINDOW_NOT_IN_THE_FUTURE")
        else:
            expired_lo = _parse_utc(dict(expired_window).get("dispatch_not_before_utc"))
            expired_hi = _parse_utc(dict(expired_window).get("expires_utc"))
            if dispatch_lo == expired_lo and expires_hi == expired_hi:
                refusal.append("IDENTICAL_TO_EXPIRED_WINDOW_SILENT_ROLL_FORWARD")
            else:
                window_is_fresh = True

    if type(restrictions_raw) is bytes:
        try:
            restrictions = strict_json_loads(restrictions_raw)
        except PreflightPackageCheckerError:
            refusal.append("INVALID_RESTRICTIONS_JSON")
            restrictions = None
        if restrictions is not None and not isinstance(restrictions, dict):
            refusal.append("RESTRICTIONS_NOT_AN_OBJECT")
            restrictions = None
    else:
        restrictions = None

    if refusal:
        return FreshWindowReadinessResult(
            schema=SCHEMA,
            outcome=OUTCOME_REFUSED,
            eligibility=ELIGIBILITY_LABEL,
            window_is_fresh=window_is_fresh,
            clock_ready=False,
            storage_ready=False,
            restriction_history_preserved=False,
            missing_prerequisites=(),
            refusal_reasons=tuple(sorted(set(refusal))),
            incompleteness_reasons=(),
        )

    # -- Readiness-tier checks (missing evidence; never fatal by itself) ----

    missing_prerequisites: list = []
    for key in NULLABLE_PREREQS:
        val = prerequisites.get(key)
        if val is None:
            incomplete.append(f"NULL_PREREQUISITE:{key}")
            missing_prerequisites.append(key)
        elif not _is_ref(val, REF_KEYS_NO_REPO):
            incomplete.append(f"MALFORMED_PREREQUISITE_REFERENCE:{key}")
            missing_prerequisites.append(key)
    owner_ref = prerequisites.get("owner_directive_original_record")
    if owner_ref is None:
        incomplete.append("NULL_PREREQUISITE:owner_directive_original_record")
        missing_prerequisites.append("owner_directive_original_record")
    elif not _is_ref(owner_ref, OWNER_REF_KEYS) or owner_ref.get("qualification") != \
            "OWNER_INSTRUCTION_ONLY_NOT_PROVIDER_RIGHTS":
        incomplete.append("MALFORMED_PREREQUISITE_REFERENCE:owner_directive_original_record")
        missing_prerequisites.append("owner_directive_original_record")

    clock_reasons: list = []
    if clock is None:
        clock_reasons.append("CLOCK_OBSERVATION_NOT_SUPPLIED")
    else:
        _check_clock({"limits": dict(FROZEN_LIMITS), "window": dict(proposed_window)}, clock, clock_reasons)
    clock_ready = not clock_reasons
    incomplete.extend(clock_reasons)

    storage_reasons: list = []
    if resources is None:
        storage_reasons.append("RESOURCE_OBSERVATION_NOT_SUPPLIED")
    if storage_qualification is None:
        storage_reasons.append("STORAGE_QUALIFICATION_NOT_SUPPLIED")
    if resources is not None and storage_qualification is not None:
        _check_storage(
            {"limits": dict(FROZEN_LIMITS), "storage_qualification": dict(storage_qualification)},
            resources,
            storage_reasons,
        )
    storage_ready = not storage_reasons
    incomplete.extend(storage_reasons)

    restriction_reasons: list = []
    if restrictions is None:
        restriction_reasons.append("RESTRICTIONS_UNAVAILABLE")
    else:
        _check_closed(restrictions, RESTRICTIONS_KEYS, "restrictions", restriction_reasons)
        if restrictions.get("schema") != RESTRICTIONS_SCHEMA_NAME:
            restriction_reasons.append("UNSUPPORTED_RESTRICTIONS_SCHEMA_VERSION")
        _check_restriction_domains(restrictions, restriction_reasons)
    restriction_history_preserved = not restriction_reasons
    incomplete.extend(restriction_reasons)

    outcome = OUTCOME_INCOMPLETE if incomplete else OUTCOME_CANDIDATE
    return FreshWindowReadinessResult(
        schema=SCHEMA,
        outcome=outcome,
        eligibility=ELIGIBILITY_LABEL,
        window_is_fresh=window_is_fresh,
        clock_ready=clock_ready,
        storage_ready=storage_ready,
        restriction_history_preserved=restriction_history_preserved,
        missing_prerequisites=tuple(sorted(set(missing_prerequisites))),
        refusal_reasons=(),
        incompleteness_reasons=tuple(sorted(set(incomplete))),
    )
