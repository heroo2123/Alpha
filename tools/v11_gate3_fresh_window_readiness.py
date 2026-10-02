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
(``FROZEN_WINDOW``) used only for roll-forward detection -- hard-bound
internally and never caller-overridable, since an overridable comparison
here would let the real expired window be laundered through as "fresh" --
the prerequisite key sets, the generic bounded-reference/string/closed-key
validators, and the already-reviewed storage-floor check function.
Re-implementing that arithmetic here would risk a silent behavioral drift
between two copies of the same safety-critical comparison.

The one check deliberately *not* reused verbatim is the checker's
``_check_clock``: that function also enforces that the measured reading
already sits inside the proposed dispatch window, which is a dispatch-time
window-*overlap* check, not a preparation-time clock-*quality* check, and
requiring it here would make an honest reading taken before a future
window opens impossible to satisfy (see ``_check_clock_quality`` below for
the preparation-appropriate floor checks this module actually runs:
source, monotonic-consistency, uncertainty, calibration-age, and agreement
with the caller's own ``now_utc`` within the reading's stated uncertainty).
That agreement check only demonstrates *internal consistency* between two
caller-supplied, freely editable values -- it does not, by itself, prove
the reading is authentic or current: a consistently stale pair (e.g. both
timestamps a day old) or a consistently future-forged pair passes it just
as an honest pair does. See ``_check_clock_quality`` for why this remains
an explicit, standing blocker rather than a claimed anti-forgery guarantee.

Three independent-review findings remain explicit, standing blockers
(reported as named incompleteness reasons, never silently treated as
satisfied) rather than invented thresholds, because no reviewed policy or
trust boundary for any of them currently exists anywhere in this
repository's reviewed protocol or frozen limits:

1. A window duration/horizon ceiling: there is no reviewed frozen constant
   bounding how long a proposed window may span or how far into the future
   it may start, so every structurally valid window -- including an
   ordinary few-hour one -- reports ``NO_REVIEWED_WINDOW_DURATION_HORIZON_POLICY``
   until a reviewed ceiling is added. Inventing a numeric threshold here
   would itself be the kind of unsupported value this module exists to
   avoid.
2. A resource/reservation magnitude ceiling: the reused ``_check_storage``
   enforces exact integer typing, standing floors, and reservation
   agreement, but no reviewed upper bound on a plausible disk/memory/
   reservation byte count exists, so every evaluated storage observation
   additionally reports ``NO_REVIEWED_RESOURCE_MAGNITUDE_CEILING``, even an
   entirely ordinary one, rather than silently accepting an
   arbitrary-precision impossible integer as genuine.
3. A trusted clock-observation provenance boundary: agreement with the
   caller's own ``now_utc`` is consistency, not authentication, so every
   evaluated clock observation additionally reports
   ``NO_REVIEWED_CLOCK_PROVENANCE_BOUNDARY``. Binding the
   ``clock_method_and_calibration_review`` prerequisite reference to actual
   reviewed bytes would require filesystem I/O this module deliberately
   never performs, and no other reviewed trust boundary exists today.

Each of these three keeps ``PREPARATION_CANDIDATE_ALL_INPUTS_PRESENT_NOT_EXECUTABLE``
unreachable until a later, separately reviewed change actually establishes
the missing policy or trust boundary; this module does not invent one just
to make its own positive outcome reachable.

Every mapping a caller supplies directly as a keyword argument
(``proposed_window``, ``prerequisites``, ``storage_qualification``) and every
direct reference reached through one of those (any of the twelve nullable
prerequisite references, the owner reference, and
``storage_qualification["persistence_review"]`` -- fourteen paths in total)
must be an exact built-in ``dict`` (``type(v) is dict``), never a ``dict``
subclass or another ``collections.abc.Mapping`` implementation. This is a
deliberate, fail-closed supported-input boundary, not an incidental
restriction: only for the exact built-in type are ``len()``, ``keys()`` and
``__iter__`` guaranteed consistent with each other and with the object's
real contents. Anything else can override one of those protocols (e.g.
reporting a small ``len()`` while a different, unchecked view yields
arbitrarily many additional entries) and would make a `len()`-based bound
bypassable while still reaching an unbounded copy or the frozen checker's
own unbounded ``_is_ref``/``_check_closed``. A caller holding an ordinary
``dict`` is unaffected; anything else is refused generically (as a
structural type problem for the three outer surfaces, as a malformed
reference for the fourteen direct-reference paths), never silently
accepted and never partially trusted.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from tools.v11_gate3_evidence_preflight_checker import (
    ECMWF_DOMAIN_KEYS,
    FROZEN_LIMITS,
    FROZEN_WINDOW,
    GEFS_DOMAIN_KEYS,
    MAX_STR,
    OWNER_REF_KEYS,
    PREREQ_KEYS,
    NULLABLE_PREREQS,
    REF_KEYS_NO_REPO,
    RESTRICTIONS_SCHEMA_NAME,
    RESTRICTIONS_KEYS,
    STORAGE_QUALIFICATION_KEYS,
    WINDOW_KEYS,
    ClockObservation,
    ResourceObservation,
    _check_restriction_domains,
    _check_storage,
    _is_bounded_str,
    _is_finite_nonneg,
    _is_ref,
    _limit_int,
    _parse_utc,
    _safe_parse,
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
        if type(self.schema) is not str or self.schema != SCHEMA:
            raise ValueError(f"not the fixed schema: {self.schema!r}")
        for flag_name in (
            "window_is_fresh", "clock_ready", "storage_ready",
            "restriction_history_preserved",
        ):
            flag_value = getattr(self, flag_name)
            if type(flag_value) is not bool:
                raise ValueError(f"{flag_name} must be an exact bool, not {flag_value!r}")
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


def _unsafe_reference_candidate(v: Any, keys: frozenset) -> bool:
    """True if ``v`` must be refused before ever being handed to the frozen
    ``_is_ref``, whose first step is an unbounded ``set(v.keys())`` copy and
    which itself only checks ``isinstance(v, dict)`` -- a check a ``dict``
    *subclass* passes trivially while still being free to override
    ``__len__``, ``keys()`` or ``__iter__`` so that a cheap ``len()`` guard
    based on those overridable protocols no longer reflects the object's
    real underlying storage (e.g. reporting length 3 while actually holding
    100,000 entries). Requiring the exact built-in type (``type(v) is
    dict``, not ``isinstance``) is what makes the ``len()`` comparison below
    trustworthy: nothing can intercept ``len()``, ``keys()`` or ``__iter__``
    for the exact ``dict`` type itself. Anything that is not an exact
    ``dict``, or an exact ``dict`` strictly larger than its own reference
    schema, is refused here -- the latter can never be a valid reference
    anyway, since ``_is_ref`` requires exact key-set equality, so this
    changes no accepted case.
    """
    return type(v) is not dict or len(v) > len(keys)


def _has_unsafe_key(obj: Mapping, label: str, reasons: list) -> bool:
    """True (with a bounded, non-echoing reason appended) if any key of
    ``obj`` is not a plain, size-bounded, UTF-8-encodable string.

    Catches two independent problems an oversized-only length check would
    miss: a key so large it would balloon a reason string that embeds it
    verbatim, and a short-but-invalid key (e.g. a lone UTF-16 surrogate
    code point, which ``len()`` happily accepts) that would later make the
    result impossible to encode as UTF-8 regardless of its length.
    """
    for k in obj:
        if not isinstance(k, str) or not _is_bounded_str(k, MAX_STR):
            reasons.append(f"OVERSIZED_OR_INVALID_KEY:{label}")
            return True
        try:
            k.encode("utf-8")
        except UnicodeEncodeError:
            reasons.append(f"OVERSIZED_OR_INVALID_KEY:{label}")
            return True
    return False


def _check_closed_bounded(obj: Any, keys: frozenset, label: str, reasons: list) -> None:
    """Like the checker's ``_check_closed``, but bounds mapping cardinality
    before doing any per-key work, and never embeds a caller-supplied key
    (oversized, non-UTF-8-encodable, or simply not a recognized schema key)
    verbatim into a reason string.

    The checker's own ``_check_closed`` has no such bound because every one
    of its callers supplies schema-fixed keys already bounded elsewhere.
    This module's ``proposed_window``, ``prerequisites`` and (post-parse)
    ``restrictions`` mappings are not byte- or count-capped before reaching
    here, so cardinality and unsafe/unknown keys must be refused
    generically rather than iterated/echoed without bound.

    ``obj`` is accepted and measured as the original object the caller
    supplied -- never a ``dict(obj)`` copy of it: ``len()`` is the only
    operation performed before the cardinality comparison below, so an
    oversized mapping is refused without first copying or fully iterating
    its entries. ``obj`` must additionally be an exact built-in ``dict``
    (``type(obj) is dict``), not merely ``isinstance(obj, Mapping)``: for
    anything else, ``len()``, ``keys()`` and ``__iter__`` are overridable
    and can disagree with each other (a declared length that does not
    match what iteration actually yields, or a ``keys()`` view that differs
    from the ``__iter__`` view), which would make every guard below -- and
    any later ``dict(obj)`` copy a caller of this function performs once it
    returns cleanly -- bypassable. For an exact ``dict`` these cannot
    diverge, so the ``len()`` check immediately below is actually
    trustworthy.
    """
    if type(obj) is not dict:
        reasons.append(f"NOT_AN_OBJECT:{label}")
        return
    if len(obj) > len(keys):
        # The schema is closed: a conforming object can never hold more
        # entries than its own fixed key set. Refuse on count alone, before
        # touching a single key, so a caller cannot force unbounded
        # aggregate work or output merely by supplying many admissible keys.
        reasons.append(f"TOO_MANY_KEYS:{label}")
        return
    if _has_unsafe_key(obj, label, reasons):
        return
    if any(k not in keys for k in obj):
        # A caller-controlled key that is itself short, valid UTF-8 and
        # within the ordinary length bound (so `_has_unsafe_key` above does
        # not catch it) must still never be embedded verbatim in a reason
        # string: it could just as easily be a short private sentinel value
        # as an ordinary typo.
        reasons.append(f"UNKNOWN_KEY:{label}")
    for k in keys:
        if k not in obj:
            reasons.append(f"MISSING_KEY:{label}.{k}")


def _unsafe_untrusted_submapping(obj: Any, keys: frozenset, label: str, reasons: list) -> bool:
    """True (with a bounded, non-echoing reason appended) if ``obj`` must
    not be handed to a reused (unmodifiable) closed-key checker function
    that embeds raw unknown-key text verbatim and enforces no cardinality
    bound of its own (the checker's own ``_check_closed``, reached
    internally by ``_check_storage`` and ``_check_restriction_domains``).

    Unlike ``_check_closed_bounded``, this does not also report missing
    keys: callers use this purely as a go/no-go gate before delegating to a
    reused function that will report missing keys itself once it is safe to
    call.

    As with ``_check_closed_bounded``, ``obj`` is measured as the original
    object the caller supplied; cardinality is checked via ``len()`` before
    any copy or full iteration is performed, and ``obj`` must be an exact
    built-in ``dict`` (``type(obj) is dict``), not merely a ``Mapping``, for
    the same reason: only for the exact type are ``len()``, ``keys()`` and
    ``__iter__`` guaranteed not to diverge from each other or from the
    object's real contents.
    """
    if type(obj) is not dict:
        return False  # let the reused function itself report the type problem
    if len(obj) > len(keys):
        reasons.append(f"TOO_MANY_KEYS:{label}")
        return True
    if _has_unsafe_key(obj, label, reasons):
        return True
    if any(k not in keys for k in obj):
        reasons.append(f"UNKNOWN_KEY:{label}")
        return True
    return False


def _check_nested_domain_bounded(restrictions: Mapping, reasons: list) -> bool:
    """True only if both ``known_control_domains.ECMWF`` and ``.GEFS`` are
    safe to pass into the reused ``_check_restriction_domains``, which
    validates each with the checker's own unbounded, verbatim-echoing
    ``_check_closed``. A caller-controlled nested key (oversized, unsafe, or
    simply unknown but admissible-length, e.g. a short private sentinel)
    must be refused generically here first, before that reused call.
    """
    domains = restrictions.get("known_control_domains")
    if type(domains) is not dict:
        return True  # malformed-but-not-oversized; the reused checker reports this safely
    ok = True
    for domain_label, domain_keys in (("ECMWF", ECMWF_DOMAIN_KEYS), ("GEFS", GEFS_DOMAIN_KEYS)):
        sub = domains.get(domain_label)
        if type(sub) is dict and _unsafe_untrusted_submapping(
            sub, domain_keys, f"known_control_domains.{domain_label}", reasons
        ):
            ok = False
    return ok


def _bound_diagnostic_output(reasons: list) -> tuple:
    """Deduplicate and sort ``reasons`` as before, but additionally enforce
    the standing, already-reviewed ``diagnostic_output_bytes`` ceiling
    (``FROZEN_LIMITS``) on their *actual serialized* representation,
    collapsing to one fixed label if it is ever exceeded.

    Every individual reason appended throughout this module is already
    bounded (schema-fixed label, or a generic marker that never embeds
    caller-supplied text), and mapping cardinality is bounded before any
    per-key reason is even considered -- so this is a final, defense-in-depth
    budget, not the mechanism redaction relies on. It must not depend on the
    raw-input byte cap: that cap bounds input, not the output this function
    returns. The budget is measured against ``json.dumps`` of the actual
    deduplicated list, not a bare sum of each reason's own UTF-8 length: the
    latter omits the JSON array's quoting, comma and bracket overhead and
    can under-count the bytes a caller serializing ``to_dict()`` actually
    receives.
    """
    deduped = tuple(sorted(set(reasons)))
    budget = _limit_int(FROZEN_LIMITS, "diagnostic_output_bytes")
    serialized_bytes = len(json.dumps(list(deduped)).encode("utf-8"))
    if serialized_bytes > budget:
        return ("DIAGNOSTIC_OUTPUT_BUDGET_EXCEEDED",)
    return deduped


def _check_clock_quality(clock: ClockObservation, limits: Mapping, now: Any, reasons: list) -> bool:
    """Preparation-time clock-*quality* check.

    This is deliberately **not** the checker's ``_check_clock``: that
    function also enforces that ``measured_utc`` already sits inside the
    proposed dispatch window (plus its uncertainty margin), which is a
    dispatch-time overlap check -- appropriate once a window has actually
    opened, meaningless before it has even started. Requiring it here would
    make an honest reading (``measured_utc == now_utc``, taken before any
    future window begins) impossible to satisfy, and the only way to pass
    would be to supply a clock reading that is itself already in the future
    relative to the caller's own ``now_utc`` -- i.e. a fabricated reading,
    which this module's own docstring forbids inventing.

    What *is* appropriate at preparation time, and is checked here, is
    whether the clock observation itself meets the standing calibration
    quality floors: an authorized local source, a monotonic-consistent
    reading, bounded uncertainty, a non-stale calibration, a parseable
    timestamp, and -- in place of the dispatch-time window-overlap check --
    agreement between the reading and the caller's own ``now_utc`` within
    the reading's own stated uncertainty. That last check is load-bearing
    for *consistency*: without it, any clock reading that merely parses
    would pass regardless of how far it diverges from the caller's claimed
    present moment. Whether the (now quality-checked) reading's timestamp
    falls inside some future dispatch window is re-checked by the checker
    itself at actual dispatch time.

    Agreement is **not**, by itself, an anti-forgery guarantee: both
    ``measured_utc`` and ``now_utc`` are plain caller-supplied values, so a
    consistently stale pair (both timestamps hours or days old) or a
    consistently future-forged pair satisfies every check above exactly as
    an honest pair does -- nothing here reads a real clock, a provider, or
    verified retained evidence to tell them apart. Establishing that would
    require a reviewed trusted observation/provenance boundary (e.g.
    binding the ``clock_method_and_calibration_review`` prerequisite
    reference to actual reviewed bytes), which does not exist anywhere in
    this repository's reviewed protocol today and would require filesystem
    I/O this module deliberately never performs. Until it exists, this
    function unconditionally reports that absence as a standing
    ``NO_REVIEWED_CLOCK_PROVENANCE_BOUNDARY`` blocker alongside whatever
    quality floors it also finds -- the contract here is internal
    consistency of trusted-caller assertions only, never authenticity or
    currentness.
    """
    ok = True
    if type(clock) is not ClockObservation:
        reasons.append("INVALID_CLOCK_OBSERVATION")
        return False
    if type(clock.source) is not str or clock.source != "LOCAL_AUTHORIZED_ONLY":
        reasons.append("INVALID_CLOCK_SOURCE")
        ok = False
    if clock.monotonic_consistent is not True:
        # Exact-type check: a truthy non-bool must never be accepted in
        # place of the real boolean flag.
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
    if measured is None:
        reasons.append("UNPARSEABLE_CLOCK")
        ok = False
    elif uncertainty_valid and abs((measured - now).total_seconds()) > clock.uncertainty_seconds:
        reasons.append("CLOCK_DISAGREES_WITH_NOW_UTC")
        ok = False
    # No reviewed trusted observation/provenance boundary for a clock
    # reading exists anywhere in this repository's reviewed protocol (see
    # above): agreement between two freely editable caller-supplied values
    # is consistency, not authenticity. Report that absence unconditionally
    # rather than silently treating consistency alone as proof of a genuine,
    # current reading.
    reasons.append("NO_REVIEWED_CLOCK_PROVENANCE_BOUNDARY")
    return False


def evaluate_fresh_window_readiness(
    *,
    proposed_window: dict,
    prerequisites: dict,
    restrictions_raw: bytes,
    storage_qualification: Optional[dict],
    now_utc: str,
    clock: Optional[ClockObservation] = None,
    resources: Optional[ResourceObservation] = None,
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

    ``proposed_window``, ``prerequisites`` and (if not ``None``)
    ``storage_qualification`` must each be an exact built-in ``dict``
    (``type(v) is dict``) -- a ``dict`` subclass or any other
    ``collections.abc.Mapping`` implementation is refused as a structural
    type problem, never partially trusted, because only the exact type
    guarantees ``len()``, ``keys()`` and ``__iter__`` cannot have been
    overridden to disagree with each other or with the object's real
    contents.
    """

    refusal: list = []
    incomplete: list = []

    # -- Structural / safety-tier checks (any one forces REFUSED) -----------

    # Each of these three mappings must be an exact built-in ``dict``, never
    # a ``dict`` subclass or other ``Mapping`` implementation: only the
    # exact type guarantees ``len()``/``keys()``/``__iter__`` cannot have
    # been overridden to disagree with each other or with the object's real
    # contents (see module docstring). ``type(...) is not dict`` therefore
    # replaces the looser ``not isinstance(..., Mapping)`` check this
    # candidate previously used.
    if type(proposed_window) is not dict:
        refusal.append("INVALID_PROPOSED_WINDOW_TYPE")
    else:
        _check_closed_bounded(proposed_window, WINDOW_KEYS, "proposed_window", refusal)

    if type(prerequisites) is not dict:
        refusal.append("INVALID_PREREQUISITES_TYPE")
    else:
        _check_closed_bounded(prerequisites, PREREQ_KEYS, "prerequisites", refusal)

    if type(restrictions_raw) is not bytes:
        refusal.append("INVALID_RESTRICTIONS_RAW_TYPE")

    now = _parse_utc(now_utc)
    if now is None:
        refusal.append("UNPARSEABLE_NOW_UTC")

    if clock is not None and type(clock) is not ClockObservation:
        refusal.append("INVALID_CLOCK_OBSERVATION_TYPE")
    if resources is not None and type(resources) is not ResourceObservation:
        refusal.append("INVALID_RESOURCE_OBSERVATION_TYPE")
    if storage_qualification is not None and type(storage_qualification) is not dict:
        refusal.append("INVALID_STORAGE_QUALIFICATION_TYPE")

    window_is_fresh = False
    dispatch_lo = expires_hi = None
    if type(proposed_window) is dict and not refusal:
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
            # Hard-bound to the module's own imported ``FROZEN_WINDOW``
            # (the checker's one real, frozen, expired October 2 window).
            # This is deliberately not a parameter: a caller-overridable
            # comparison here would let the exact real expired window pass
            # through as "fresh" simply by supplying a different
            # ``expired_window`` (or bypass the check entirely via a
            # malformed one), defeating the whole roll-forward refusal.
            expired_lo = _parse_utc(FROZEN_WINDOW["dispatch_not_before_utc"])
            expired_hi = _parse_utc(FROZEN_WINDOW["expires_utc"])
            if dispatch_lo == expired_lo and expires_hi == expired_hi:
                refusal.append("IDENTICAL_TO_EXPIRED_WINDOW_SILENT_ROLL_FORWARD")
            elif dispatch_lo < expired_hi and expires_hi > expired_lo:
                # Not an exact match, but it overlaps the one real expired
                # window -- still a roll-forward of the same proposal, just
                # shifted rather than copied verbatim.
                refusal.append("OVERLAPS_EXPIRED_WINDOW_SILENT_ROLL_FORWARD")
            else:
                # Ordering, future-check and expired-window overlap all
                # passed. Calling this window "fresh" would also require a
                # reviewed duration/horizon ceiling bounding how long it may
                # span or how far into the future it may start; no such
                # reviewed frozen constant exists anywhere in this
                # repository's protocol or ``FROZEN_LIMITS`` (see module
                # docstring). Report that absence as a standing,
                # readiness-tier blocker -- not a structural refusal, since
                # it is not a property of this specific window's values --
                # rather than silently treating an unbounded duration or
                # horizon as fresh. ``window_is_fresh`` intentionally stays
                # ``False`` until a reviewed ceiling actually exists.
                incomplete.append("NO_REVIEWED_WINDOW_DURATION_HORIZON_POLICY")

    if type(restrictions_raw) is bytes:
        restrictions = _safe_parse(restrictions_raw, "restrictions", refusal)
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
            refusal_reasons=_bound_diagnostic_output(refusal),
            incompleteness_reasons=(),
        )

    # -- Readiness-tier checks (missing evidence; never fatal by itself) ----

    missing_prerequisites: list = []
    for key in NULLABLE_PREREQS:
        val = prerequisites.get(key)
        if val is None:
            incomplete.append(f"NULL_PREREQUISITE:{key}")
            missing_prerequisites.append(key)
        # Type and cardinality are checked first (cheap, via type()/len())
        # so neither a dict subclass that lies about its own length nor an
        # arbitrarily large exact dict supplied as a direct reference ever
        # reaches _is_ref's internal set(val.keys()) (F2).
        elif _unsafe_reference_candidate(val, REF_KEYS_NO_REPO) or not _is_ref(val, REF_KEYS_NO_REPO):
            incomplete.append(f"MALFORMED_PREREQUISITE_REFERENCE:{key}")
            missing_prerequisites.append(key)
    owner_ref = prerequisites.get("owner_directive_original_record")
    if owner_ref is None:
        incomplete.append("NULL_PREREQUISITE:owner_directive_original_record")
        missing_prerequisites.append("owner_directive_original_record")
    elif _unsafe_reference_candidate(owner_ref, OWNER_REF_KEYS) or not _is_ref(owner_ref, OWNER_REF_KEYS) or \
            owner_ref.get("qualification") != "OWNER_INSTRUCTION_ONLY_NOT_PROVIDER_RIGHTS":
        incomplete.append("MALFORMED_PREREQUISITE_REFERENCE:owner_directive_original_record")
        missing_prerequisites.append("owner_directive_original_record")

    clock_reasons: list = []
    if clock is None:
        clock_reasons.append("CLOCK_OBSERVATION_NOT_SUPPLIED")
    else:
        _check_clock_quality(clock, FROZEN_LIMITS, now, clock_reasons)
    clock_ready = not clock_reasons
    incomplete.extend(clock_reasons)

    storage_reasons: list = []
    if resources is None:
        storage_reasons.append("RESOURCE_OBSERVATION_NOT_SUPPLIED")
    if storage_qualification is None:
        storage_reasons.append("STORAGE_QUALIFICATION_NOT_SUPPLIED")
    if resources is not None and storage_qualification is not None:
        # _check_storage below calls the checker's raw, unbounded
        # _check_closed internally -- guard cardinality and keys on the
        # original Mapping here first (before any dict() copy), so an
        # oversized mapping, an oversized/UTF-8-unsafe key, or a
        # short-but-unknown (e.g. private sentinel) key is refused
        # generically instead of being copied/iterated/echoed verbatim by
        # that inner call (F1).
        if not _unsafe_untrusted_submapping(
            storage_qualification, STORAGE_QUALIFICATION_KEYS, "storage_qualification", storage_reasons
        ):
            # storage_qualification is now confirmed no larger than its own
            # schema (<= len(STORAGE_QUALIFICATION_KEYS) entries), so this
            # copy is bounded.
            sq_dict = dict(storage_qualification)
            # _check_storage internally calls the frozen _is_ref on
            # persistence_review, whose first step is an unbounded
            # set(v.keys()) copy, and which itself only checks
            # isinstance(v, dict) -- a dict subclass that lies about its
            # own length would pass that check trivially. Guard its type
            # and cardinality here first and substitute a cheap sentinel if
            # either is unsafe, so that internal call never sees anything
            # but an exact, schema-sized dict or None (F2); an oversized or
            # wrong-type value can never be a valid reference anyway
            # (_is_ref requires an exact dict with exact key-set equality),
            # so this changes no accepted case.
            persistence_review = sq_dict.get("persistence_review")
            if _unsafe_reference_candidate(persistence_review, REF_KEYS_NO_REPO):
                storage_reasons.append(
                    "OVERSIZED_PREREQUISITE_REFERENCE:storage_qualification.persistence_review"
                )
                sq_dict["persistence_review"] = None
            _check_storage(
                {"limits": dict(FROZEN_LIMITS), "storage_qualification": sq_dict},
                resources,
                storage_reasons,
            )
            # _check_storage enforces exact integer typing, the standing
            # floors, and reservation agreement, but no reviewed upper
            # bound on a plausible disk/memory/reservation byte count
            # exists anywhere in this repository's reviewed protocol or
            # ``FROZEN_LIMITS`` (see module docstring). Report that absence
            # unconditionally rather than silently accepting an
            # arbitrary-precision impossible integer (e.g. 2**64) as a
            # genuine observation.
            storage_reasons.append("NO_REVIEWED_RESOURCE_MAGNITUDE_CEILING")
    storage_ready = not storage_reasons
    incomplete.extend(storage_reasons)

    restriction_reasons: list = []
    if restrictions is None:
        restriction_reasons.append("RESTRICTIONS_UNAVAILABLE")
    else:
        _check_closed_bounded(restrictions, RESTRICTIONS_KEYS, "restrictions", restriction_reasons)
        if restrictions.get("schema") != RESTRICTIONS_SCHEMA_NAME:
            restriction_reasons.append("UNSUPPORTED_RESTRICTIONS_SCHEMA_VERSION")
        # _check_restriction_domains below validates the nested ECMWF/GEFS
        # domain mappings with the checker's own unbounded, verbatim-echoing
        # _check_closed internally -- guard those nested mappings here first,
        # same as storage_qualification above, so an oversized/unsafe or
        # short-but-unknown nested key is refused generically instead of
        # being echoed verbatim by that inner call.
        if _check_nested_domain_bounded(restrictions, restriction_reasons):
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
        incompleteness_reasons=_bound_diagnostic_output(incomplete),
    )
