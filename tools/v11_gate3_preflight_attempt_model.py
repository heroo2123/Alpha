"""Pure, bounded Gate 3 P1 synthetic attempt reducer. No dispatch capability.

Acknowledgements in this module are data in a test script, never evidence of
physical persistence. All outcomes have zero execution and capture authority.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from fractions import Fraction
from hashlib import sha256
import json
from typing import Any

from tools.v11_gate3_evidence_preflight_checker import (
    ClockObservation, ResourceObservation, StateLedger,
    check_evidence_preflight_package, OUTCOME_SATISFIED, strict_json_loads,
)

SCHEMA = "ALPHA_V11_PREFLIGHT_SYNTHETIC_MODEL_V1"
MAX = (1 << 63) - 1
BODY_CAP = 3_145_728
STAGE_TIME = 60_000_000
BODY_CHUNK = 65_536
RAW_CAP = 1_048_576
EVENT_CAP = 256
SCRIPT_CAP = 4_194_304
PHASES = ("REFUSED_BEFORE_DISPATCH", "ADMITTED", "LOCKED", "INTENT_RECORDED",
          "RESERVED", "STARTED", "RECEIVING", "CLOSED", "ACCOUNTED",
          "RETAINED_UNQUALIFIED", "RETAINED_INVALID", "DENIED_HELD", "UNCERTAIN_HELD")
TERMINAL = frozenset(("REFUSED_BEFORE_DISPATCH", "RETAINED_UNQUALIFIED",
                      "RETAINED_INVALID", "DENIED_HELD", "UNCERTAIN_HELD"))
TAGS = frozenset(("LOCKS", "INTENT_ACK", "RESERVE_ACK", "START", "STATUS",
                  "HEADERS", "BODY", "CLOSE_ACK", "ACCOUNT_ACK", "SEAL_ACK", "FAULT"))
DENIAL = frozenset((401, 403, 429, 503))
WINDOW_LO = datetime(2026, 10, 2, 10, tzinfo=timezone.utc)
WINDOW_HI = datetime(2026, 10, 2, 13, 30, tzinfo=timezone.utc)
EVENT_KEYS = {
    "LOCKS": frozenset(("tag", "seq", "owner", "head", "locks")),
    "INTENT_ACK": frozenset(("tag", "seq", "owner", "head", "ref")),
    "RESERVE_ACK": frozenset(("tag", "seq", "owner", "head", "ref", "attempts", "body_bytes", "time_us", "report_bytes")),
    "START": frozenset(("tag", "seq", "owner", "head", "clock", "resources", "monotonic_us", "host", "boot")),
    "STATUS": frozenset(("tag", "seq", "owner", "head", "status", "explicit_denial")),
    "HEADERS": frozenset(("tag", "seq", "owner", "head", "headers")),
    "BODY": frozenset(("tag", "seq", "owner", "head", "data", "clock", "monotonic_us", "host", "boot")),
    "CLOSE_ACK": frozenset(("tag", "seq", "owner", "head", "ref", "clock", "monotonic_us", "host", "boot")),
    "ACCOUNT_ACK": frozenset(("tag", "seq", "owner", "head", "ref")),
    "SEAL_ACK": frozenset(("tag", "seq", "owner", "head", "ref", "clock", "monotonic_us", "host", "boot")),
    "FAULT": frozenset(("tag", "seq", "owner", "head", "kind")),
}
FAULTS = frozenset(("CRASH", "WRITE_FAILURE", "FSYNC_FAILURE", "DNS_FAILURE",
                    "TLS_FAILURE", "CLOCK_FAILURE", "TIMEOUT", "LOST_OWNER",
                    "LOST_HEAD", "JOURNAL_FULL", "WATCHDOG_KILL_REAP",
                    "RESOURCE_BREACH"))


def _integer(v: Any) -> bool:
    return type(v) is int and 0 <= v <= MAX


def _add(*values: int) -> int | None:
    if not all(_integer(x) for x in values):
        return None
    n = sum(values)
    return n if n <= MAX else None


def _sha(raw: bytes) -> str:
    return sha256(raw).hexdigest()


def _safe_ref(value: Any) -> bool:
    if type(value) is not str or not (1 <= len(value) <= 256):
        return False
    if not value.startswith("synthetic://"):
        return False
    rest = value[len("synthetic://"):]
    # Reject empty authority ("synthetic://"), empty-authority absolute paths
    # ("synthetic:///etc/passwd"), traversal (plain or percent-encoded, so any
    # "%" is refused outright since no legitimate fabricated ref ever needs
    # percent-encoding), and backslash/double-slash confusables.
    if not rest or rest.startswith("/") or ".." in rest or "\\" in rest or "//" in rest or "%" in rest:
        return False
    return all(ord(c) >= 32 for c in value)


def _walk_synthetic_paths(value: Any) -> bool:
    # Iterative (explicit stack), not recursive: a deeply nested JSON document
    # (tens of thousands of levels) must never raise RecursionError here.
    pending = [value]
    while pending:
        current = pending.pop()
        if type(current) is dict:
            for key, item in current.items():
                if type(key) is not str:
                    return False
                if key in ("requests", "records"):
                    # "requests" carries the protocol's frozen *public HTTP
                    # request path* (checked exactly against FROZEN_REQUEST by
                    # the checker); "records" carries the immutable public
                    # retained-denial descriptors (checked exactly against
                    # RETAINED_DENIAL_DIGESTS), which this handoff requires be
                    # preserved byte-for-byte, including their real original
                    # response "path"/"url" values. Neither is a fabricated
                    # filesystem evidence reference, so neither is subject to
                    # the synthetic:// walk below.
                    continue
                if key in ("path", "private_root") and not _safe_ref(item):
                    return False
                pending.append(item)
        elif type(current) is list:
            pending.extend(current)
    return True


@dataclass(frozen=True)
class SyntheticInputs:
    package_raw: bytes
    restrictions_raw: bytes
    protocol_raw: bytes
    binding_raw: bytes
    clock: ClockObservation
    resources: ResourceObservation
    ledger: StateLedger
    review_terminal: dict
    mode: str = "SYNTHETIC_ONLY"


@dataclass(frozen=True)
class Checkpoint:
    campaign_id: str
    pilot_id: str
    expected_history_head: str
    external_history_head: str
    owner: str
    used_attempts: int
    outstanding_attempts: int
    used_body_bytes: int
    outstanding_body_bytes: int
    used_time_us: int
    outstanding_time_us: int
    pilot_used_attempts: int
    pilot_outstanding_attempts: int
    pilot_used_body_bytes: int
    pilot_outstanding_body_bytes: int
    holds: tuple[str, ...]
    unfinished_intents: tuple[str, ...]
    last_start_us: int | None
    synthetic_genesis: bool


@dataclass(frozen=True)
class ModelState:
    phase: str
    fingerprint: str
    sequence: int
    head: str
    input_head: str
    owner: str
    checkpoint: Checkpoint
    starts: int = 0
    reserved_attempts: int = 0
    reserved_body_bytes: int = 0
    reserved_time_us: int = 0
    used_attempts: int = 0
    used_body_bytes: int = 0
    used_time_us: int = 0
    delivered_bytes: int = 0
    body: bytes = b""
    status: int | None = None
    headers: tuple[tuple[bytes, bytes], ...] = ()
    content_length: int | None = None
    framing_valid: bool = True
    denials: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()
    refs: tuple[tuple[str, str], ...] = ()
    start_us: int | None = None
    admission_us: int | None = None
    last_monotonic_us: int | None = None
    host: str | None = None
    boot: str | None = None
    last_clock: ClockObservation | None = None
    report_reserved: bool = False
    poisoned: bool = False


@dataclass(frozen=True)
class Transition:
    state: ModelState
    accepted: bool
    reason: str | None = None


@dataclass(frozen=True)
class ModelResult:
    schema: str
    synthetic: bool
    outcome: str
    attempt_state: str
    fingerprint: str
    input_history_head: str
    output_history_head: str
    starts: int
    used_attempts: int
    used_body_bytes: int
    used_time_us: int
    outstanding_attempts: int
    outstanding_body_bytes: int
    outstanding_time_us: int
    holds: tuple[str, ...]
    reasons: tuple[str, ...]
    refs: tuple[tuple[str, str], ...]
    execution_authority: bool = False
    provider_authority: bool = False
    capture_authority: bool = False
    qualification_credit: int = 0
    eligibility: str = "DISCOVERY_ONLY_NOT_G3E"
    parser_ref: None = None
    parse_result_ref: None = None
    parse_absence_reason: str = "NOT_IMPLEMENTED_IN_THIS_SLICE"
    decode_absence_reason: str = "NOT_PERFORMED_BYTES_ONLY"
    delivered_bytes: int = 0
    poisoned: bool = False

    def __post_init__(self) -> None:
        # Unconditional (not `assert`, which Python -O strips): every fixed
        # field, counter and promotion flag is locked to its one allowed
        # shape/value. Order matters only where it prevents a crash on a
        # malformed field (e.g. an unhashable `outcome`); every check below
        # is still fully evaluated via `and`, not a bare early return.
        counters = (self.starts, self.used_attempts, self.used_body_bytes, self.used_time_us,
                    self.outstanding_attempts, self.outstanding_body_bytes, self.outstanding_time_us,
                    self.delivered_bytes)
        ok = (
            self.schema == SCHEMA and self.synthetic is True
            # `outcome` must be a real phase name (rules out invented labels
            # like "CAPTURED"/"G3L_PASS"); `result()` is also legitimately
            # called on an in-flight (non-terminal) state (e.g. right after
            # START), so this cannot be narrowed to TERMINAL only. The
            # consistency that actually matters — that a result's
            # `attempt_state` was not silently changed out from under its
            # `outcome` (or vice versa) — is the equality check below.
            and type(self.outcome) is str and self.outcome in PHASES
            and type(self.attempt_state) is str and self.attempt_state == self.outcome
            and type(self.fingerprint) is str
            and type(self.input_history_head) is str and type(self.output_history_head) is str
            and all(type(x) is int and 0 <= x <= MAX for x in counters)
            and type(self.holds) is tuple and all(type(h) is str for h in self.holds)
            and type(self.reasons) is tuple and all(type(r) is str for r in self.reasons)
            and type(self.refs) is tuple and all(type(r) is tuple and len(r) == 2 for r in self.refs)
            and self.execution_authority is False and self.provider_authority is False
            and self.capture_authority is False
            and type(self.qualification_credit) is int and self.qualification_credit == 0
            and self.eligibility == "DISCOVERY_ONLY_NOT_G3E"
            and self.parser_ref is None and self.parse_result_ref is None
            and self.parse_absence_reason == "NOT_IMPLEMENTED_IN_THIS_SLICE"
            and self.decode_absence_reason == "NOT_PERFORMED_BYTES_ONLY"
            and type(self.poisoned) is bool
        )
        if not ok:
            raise ValueError("invalid synthetic result or promotion")

    @property
    def denials(self) -> tuple[str, ...]:
        return self.holds


def _refusal(checkpoint: Checkpoint, reasons: tuple[str, ...], fingerprint: str = "") -> ModelState:
    return ModelState("REFUSED_BEFORE_DISPATCH", fingerprint, 0,
                      checkpoint.expected_history_head, checkpoint.expected_history_head,
                      checkpoint.owner, checkpoint, reasons=tuple(sorted(set(reasons))),
                      denials=checkpoint.holds)


def _valid_checkpoint(cp: Any) -> bool:
    if type(cp) is not Checkpoint:
        return False
    if not all(_safe_ref(x) for x in (cp.campaign_id, cp.pilot_id, cp.owner)):
        return False
    if not all(type(x) is str and len(x) == 64 and all(c in "0123456789abcdef" for c in x)
               for x in (cp.expected_history_head, cp.external_history_head)):
        return False
    counters = (cp.used_attempts, cp.outstanding_attempts, cp.used_body_bytes,
                cp.outstanding_body_bytes, cp.used_time_us, cp.outstanding_time_us,
                cp.pilot_used_attempts, cp.pilot_outstanding_attempts,
                cp.pilot_used_body_bytes, cp.pilot_outstanding_body_bytes)
    return (all(_integer(x) for x in counters) and type(cp.holds) is tuple
            and all(_safe_ref(x) for x in cp.holds) and type(cp.unfinished_intents) is tuple
            and all(_safe_ref(x) for x in cp.unfinished_intents)
            and (cp.last_start_us is None or _integer(cp.last_start_us))
            and type(cp.synthetic_genesis) is bool)


def admit_synthetic(inputs: SyntheticInputs, checkpoint: Checkpoint) -> ModelState:
    if not _valid_checkpoint(checkpoint):
        raise ValueError("INVALID_CHECKPOINT")
    if type(inputs) is not SyntheticInputs or inputs.mode != "SYNTHETIC_ONLY":
        return _refusal(checkpoint, ("SYNTHETIC_ONLY_REQUIRED",))
    raws = (inputs.package_raw, inputs.restrictions_raw, inputs.protocol_raw, inputs.binding_raw)
    if any(type(x) is not bytes or len(x) > RAW_CAP for x in raws):
        return _refusal(checkpoint, ("INVALID_OR_OVERSIZED_RAW",))
    try:
        objects = [strict_json_loads(raw) for raw in (raws[0], raws[1], raws[3])]
    except Exception:
        objects = []
    if len(objects) != 3 or not all(_walk_synthetic_paths(o) for o in objects):
        return _refusal(checkpoint, ("NON_SYNTHETIC_PATH_OR_INVALID_JSON",))
    if (checkpoint.expected_history_head != checkpoint.external_history_head
            or checkpoint.unfinished_intents or not checkpoint.synthetic_genesis):
        return _refusal(checkpoint, ("UNRECONCILED_HISTORY_OR_INTENT",))
    if checkpoint.holds and any(not h.startswith("synthetic://retained-denial/") for h in checkpoint.holds):
        return _refusal(checkpoint, ("UNKNOWN_HOLD",))
    try:
        checked = check_evidence_preflight_package(
            package_raw=raws[0], restrictions_raw=raws[1], protocol_raw=raws[2],
            binding_raw=raws[3], clock=inputs.clock, resources=inputs.resources,
            ledger=inputs.ledger, review_terminal=inputs.review_terminal)
    except (TypeError, ValueError, AttributeError):
        return _refusal(checkpoint, ("INVALID_CHECKER_INPUT",))
    if checked.outcome != OUTCOME_SATISFIED:
        return _refusal(checkpoint, checked.refusal_reasons)
    # Bind exact supplied bytes and the immutable public P1 intent, not caller-owned objects.
    fp = _sha(b"\0".join(raws) + b"\0P1_GEFS_INDEX\0GET\0SYNTHETIC_ONLY")
    return ModelState("ADMITTED", fp, 0, checkpoint.expected_history_head,
                      checkpoint.expected_history_head, checkpoint.owner, checkpoint,
                      denials=tuple(checkpoint.holds), admission_us=0,
                      last_clock=inputs.clock)


def _clock_fields(clock: Any) -> tuple[datetime, int] | None:
    """Structural validation only: type, source, monotonic flag, bounded
    uncertainty/calibration age, and a parseable timestamp. Never a window
    (business-time) decision; see ``_window_ok`` for that."""

    if type(clock) is not ClockObservation or clock.monotonic_consistent is not True or clock.source != "LOCAL_AUTHORIZED_ONLY":
        return None
    u, age = clock.uncertainty_seconds, clock.calibration_age_seconds
    if type(u) not in (int, float) or type(age) not in (int, float) or isinstance(u, bool) or isinstance(age, bool):
        return None
    if not (0 <= u <= 1 and 0 <= age <= 60):
        return None
    from tools.v11_gate3_evidence_preflight_checker import _parse_utc
    stamp = _parse_utc(clock.measured_utc)
    if stamp is None:
        return None
    ticks = Fraction(u) * 1_000_000
    margin = (ticks.numerator + ticks.denominator - 1) // ticks.denominator
    return stamp, margin


def _window_ok(stamp: datetime, margin: int, phase: str) -> bool:
    try:
        if stamp + timedelta(microseconds=margin) >= WINDOW_HI:
            return False
        if phase == "START" and stamp - timedelta(microseconds=margin) < WINDOW_LO:
            return False
    except OverflowError:
        return False
    return True


def _resources(value: Any) -> bool:
    return (type(value) is ResourceObservation and
            all(_integer(x) for x in (value.free_disk_bytes_after_reservation,
                value.mem_available_bytes_after_reservation, value.physically_reserved_bytes))
            and value.physically_reserved_bytes >= 67_108_864
            and value.free_disk_bytes_after_reservation >= 2_147_483_648
            and value.mem_available_bytes_after_reservation >= 536_870_912)


def _event_valid(event: Any) -> bool:
    if type(event) is not dict:
        return False
    tag = event.get("tag")
    # Type-check before the frozenset membership test: an unhashable tag
    # value (a list/dict) must refuse cleanly, never raise TypeError.
    if type(tag) is not str or tag not in TAGS or frozenset(event) != EVENT_KEYS[tag]:
        return False
    if not _integer(event["seq"]) or not _safe_ref(event["owner"]):
        return False
    if type(event["head"]) is not str or len(event["head"]) != 64:
        return False
    if tag == "LOCKS":
        return type(event["locks"]) is tuple and event["locks"] == ("shared", "stage", "session", "budget", "store")
    if tag in ("INTENT_ACK", "RESERVE_ACK", "CLOSE_ACK", "ACCOUNT_ACK", "SEAL_ACK") and not _safe_ref(event["ref"]):
        return False
    if tag == "RESERVE_ACK":
        return all(_integer(event[x]) for x in ("attempts", "body_bytes", "time_us", "report_bytes"))
    if tag == "STATUS":
        return type(event["status"]) is int and 100 <= event["status"] <= 599 and type(event["explicit_denial"]) is bool
    if tag == "HEADERS":
        return type(event["headers"]) is tuple and len(event["headers"]) <= 33 and all(
            type(p) is tuple and len(p) == 2 and all(type(x) is bytes and len(x) <= 4097 for x in p)
            for p in event["headers"])
    if tag == "BODY":
        if type(event["data"]) is not bytes or len(event["data"]) > BODY_CHUNK:
            return False
    if tag in ("START", "BODY", "CLOSE_ACK", "SEAL_ACK"):
        if not _integer(event["monotonic_us"]) or not all(_safe_ref(event[x]) for x in ("host", "boot")):
            return False
        if tag == "START":
            # Pre-dispatch (structural) rejection is reserved for START alone:
            # budget is not yet irrevocably in play before the first START, so
            # a bad clock or short-of-floor resources can refuse cleanly with
            # zero charge rather than holding. Every later clocked event's
            # clock is only type-checked here; its full structural validity
            # (calibration age, uncertainty, monotonic_consistent, window) is
            # decided inside `step` as a hold, never a pre-dispatch escape
            # hatch for a caller retrying with a different clock.
            parsed = _clock_fields(event["clock"])
            if parsed is None:
                return False
            if not _window_ok(parsed[0], parsed[1], tag):
                return False
            if not _resources(event["resources"]):
                return False
        elif type(event["clock"]) is not ClockObservation:
            return False
    if tag == "FAULT" and event["kind"] not in FAULTS:
        return False
    return True


# Reservation and reference invariants a legitimately-admitted/stepped state
# must satisfy at each phase. Used to refuse a hand-built/forged ModelState
# that never actually passed through admission/reservation.
_PHASE_RESERVATION = {
    "ADMITTED": (0, 0, 0), "LOCKED": (0, 0, 0),
    "INTENT_RECORDED": (1, BODY_CAP, STAGE_TIME), "RESERVED": (1, BODY_CAP, STAGE_TIME),
    "STARTED": (0, BODY_CAP, STAGE_TIME), "RECEIVING": (0, BODY_CAP, STAGE_TIME),
    "CLOSED": (0, BODY_CAP, STAGE_TIME), "ACCOUNTED": (0, 0, 0),
}
_PHASE_REQUIRED_REFS = {
    "INTENT_RECORDED": ("intent",), "RESERVED": ("intent", "reservation"),
    "STARTED": ("intent", "reservation"), "RECEIVING": ("intent", "reservation"),
    "CLOSED": ("intent", "reservation", "closure"),
    "ACCOUNTED": ("intent", "reservation", "closure", "accounting"),
}


_PRE_START_PHASES = frozenset(("ADMITTED", "LOCKED", "INTENT_RECORDED", "RESERVED"))


def _state_consistent(state: ModelState) -> bool:
    fp = state.fingerprint
    if type(fp) is not str or len(fp) != 64 or any(c not in "0123456789abcdef" for c in fp):
        return False
    # A legitimately-admitted state's checkpoint is set once at admission and
    # never mutated by any transition in `step`, so it must still be valid
    # and still satisfy the same reconciliation/hold invariants admission
    # itself enforced. A pure reducer cannot prove full provenance without a
    # MAC, but this at least refuses a hand-built state whose checkpoint
    # could never have reached admission in the first place.
    cp = state.checkpoint
    if not _valid_checkpoint(cp):
        return False
    if (cp.expected_history_head != cp.external_history_head or cp.unfinished_intents
            or not cp.synthetic_genesis):
        return False
    if cp.holds and any(not h.startswith("synthetic://retained-denial/") for h in cp.holds):
        return False
    if not set(cp.holds) <= set(state.denials):
        return False
    expected = _PHASE_RESERVATION.get(state.phase)
    if expected is not None and (state.reserved_attempts, state.reserved_body_bytes, state.reserved_time_us) != expected:
        return False
    required = _PHASE_REQUIRED_REFS.get(state.phase, ())
    present = {r[0] for r in state.refs}
    if not set(required) <= present:
        return False
    # STARTED and every phase reachable only after it must show the one
    # stage attempt as actually started; every phase before it must show
    # none. `step` is only ever called on a non-terminal phase (terminal
    # phases are rejected before this function runs), so these are the only
    # two buckets that matter here.
    if state.phase in _PRE_START_PHASES:
        return state.starts == 0 and state.used_attempts == 0 and state.start_us is None
    return state.starts == 1 and state.used_attempts == 1 and state.start_us is not None


def _held(state: ModelState, reason: str | tuple[str, ...]) -> ModelState:
    added = (reason,) if type(reason) is str else tuple(reason)
    return replace(state, phase="UNCERTAIN_HELD", reasons=tuple(sorted(set(state.reasons + added))), report_reserved=True)


_HEADER_TOKEN_CHARS = frozenset(
    b"!#$%&'*+-.^_`|~0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
)


def _frame(headers: tuple[tuple[bytes, bytes], ...]) -> tuple[bool, int | None, bool]:
    valid = len(headers) <= 32 and sum(len(k) + len(v) for k, v in headers) <= 4096
    fields: dict[bytes, bytes] = {}
    for k, v in headers:
        if not (1 <= len(k) <= 64 and len(v) <= 1024
                and all(b in _HEADER_TOKEN_CHARS for b in k)
                and all(b >= 32 and b != 127 for b in v)):
            valid = False
        key = k.lower()
        if key in fields:
            valid = False
        fields[key] = v
    length = fields.get(b"content-length")
    parsed = int(length) if length is not None and length.isascii() and length.isdigit() and length == str(int(length)).encode() else None
    valid = (valid and parsed is not None and 1 <= parsed <= BODY_CAP
             and fields.get(b"content-type") == b"text/plain"
             and fields.get(b"content-encoding", b"identity") == b"identity"
             and b"transfer-encoding" not in fields)
    held = b"retry-after" in fields or b"x-denial" in fields
    return valid, parsed, held


def step(state: ModelState, event: dict) -> Transition:
    if type(state) is not ModelState or state.phase not in PHASES:
        raise ValueError("INVALID_STATE")
    if state.phase in TERMINAL:
        return Transition(state, False, "TERMINAL_STATE")
    if not _state_consistent(state):
        return Transition(state, False, "FORGED_OR_INCONSISTENT_STATE")
    if not _event_valid(event):
        return Transition(state, False, "INVALID_EVENT")
    if event["seq"] != state.sequence + 1 or event["owner"] != state.owner or event["head"] != state.head:
        return Transition(state, False, "SEQUENCE_OWNER_OR_HEAD_MISMATCH")
    tag = event["tag"]
    phase = state.phase
    allowed = {"ADMITTED": ("LOCKS", "FAULT"), "LOCKED": ("INTENT_ACK", "FAULT"),
               "INTENT_RECORDED": ("RESERVE_ACK", "FAULT"), "RESERVED": ("START", "FAULT"),
               "STARTED": ("STATUS", "FAULT"), "RECEIVING": ("HEADERS", "BODY", "CLOSE_ACK", "FAULT"),
               "CLOSED": ("ACCOUNT_ACK", "FAULT"), "ACCOUNTED": ("SEAL_ACK", "FAULT")}
    if tag not in allowed.get(phase, ()):
        return Transition(state, False, "OUT_OF_ORDER_EVENT")
    # Bind the full exact event content (not just seq/tag), so two otherwise
    # identical-looking events that differ in status/headers/body/refs/clocks
    # can never produce the same resulting head. `sorted(event.items())`
    # never needs to compare values (all keys are distinct strings), so this
    # is safe even though values include bytes/tuples/dataclass instances.
    new_head = _sha(bytes.fromhex(state.head) + repr((state.fingerprint, sorted(event.items()))).encode())
    s = replace(state, sequence=event["seq"], head=new_head)
    if tag == "FAULT":
        if phase == "ADMITTED":
            # Only a proven pre-intent fault (before locks are even taken)
            # can charge nothing: no intent write was ever attempted.
            return Transition(replace(s, phase="REFUSED_BEFORE_DISPATCH", reasons=(event["kind"],)), True)
        if phase == "LOCKED":
            # A fault exactly while the intent is being written is
            # ambiguous: the write may or may not have landed. Hold the
            # full reservation so the same checkpoint cannot be re-admitted.
            s = replace(s, reserved_attempts=1, reserved_body_bytes=BODY_CAP, reserved_time_us=STAGE_TIME)
        return Transition(_held(s, event["kind"]), True)
    if tag == "LOCKS":
        return Transition(replace(s, phase="LOCKED"), True)
    if tag == "INTENT_ACK":
        return Transition(replace(s, phase="INTENT_RECORDED", refs=s.refs + (("intent", event["ref"]),),
                                  reserved_attempts=1, reserved_body_bytes=BODY_CAP,
                                  reserved_time_us=STAGE_TIME, report_reserved=True), True)
    if tag == "RESERVE_ACK":
        cp = s.checkpoint
        if (event["attempts"], event["body_bytes"], event["time_us"], event["report_bytes"]) != (1, BODY_CAP, STAGE_TIME, 16_777_216):
            return Transition(_held(s, "INCOMPLETE_RESERVATION"), True)
        checks = ((_add(cp.used_attempts, cp.outstanding_attempts, 1), 1),
                  (_add(cp.used_attempts, cp.outstanding_attempts, 1), 8),
                  (_add(cp.used_body_bytes, cp.outstanding_body_bytes, BODY_CAP), 33_554_432),
                  (_add(cp.used_time_us, cp.outstanding_time_us, STAGE_TIME), 120_000_000),
                  (_add(cp.pilot_used_attempts, cp.pilot_outstanding_attempts, cp.used_attempts, cp.outstanding_attempts, 1), 3600),
                  (_add(cp.pilot_used_body_bytes, cp.pilot_outstanding_body_bytes, cp.used_body_bytes, cp.outstanding_body_bytes, BODY_CAP), 1_073_741_824))
        if any(value is None or value > cap for value, cap in checks):
            return Transition(_held(s, "BUDGET_EXCEEDED"), True)
        return Transition(replace(s, phase="RESERVED", refs=s.refs + (("reservation", event["ref"]),)), True)
    if tag in ("START", "BODY", "CLOSE_ACK", "SEAL_ACK"):
        mono = event["monotonic_us"]
        if (s.last_monotonic_us is not None and mono < s.last_monotonic_us) or (s.host is not None and (event["host"] != s.host or event["boot"] != s.boot)):
            return Transition(_held(s, "CLOCK_OR_BOOT_CHANGE"), True)
        if tag != "START":
            # START's window validity was already proven structurally by
            # `_event_valid`; for every later clocked event, a structurally
            # bad clock (calibration age, uncertainty, monotonic_consistent,
            # source, unparseable timestamp) or expiry past the window (and
            # UTC/monotonic divergence, including a backwards UTC step) is an
            # ambiguity that must hold the attempt, not silently leave the
            # state machine stuck in a retryable non-terminal phase or let a
            # caller retry with a different/backdated clock to reach a clean
            # outcome.
            parsed = _clock_fields(event["clock"])
            if parsed is None:
                return Transition(_held(s, "CLOCK_FAILURE"), True)
            stamp, margin = parsed
            if not _window_ok(stamp, margin, tag):
                return Transition(_held(s, "WINDOW_EXPIRED"), True)
            if s.last_clock is not None:
                last_parsed = _clock_fields(s.last_clock)
                if last_parsed is not None:
                    utc_delta = (stamp - last_parsed[0]).total_seconds()
                    mono_delta = (mono - s.last_monotonic_us) / 1_000_000 if s.last_monotonic_us is not None else 0.0
                    if utc_delta < -1 or (utc_delta - mono_delta) > 600:
                        return Transition(_held(s, "UTC_MONOTONIC_DIVERGENCE"), True)
        if s.start_us is not None and mono - s.start_us > (60_000_000 if tag in ("START", "SEAL_ACK") else 30_000_000):
            return Transition(_held(s, "DEADLINE_EXCEEDED"), True)
        s = replace(s, last_monotonic_us=mono, host=event["host"], boot=event["boot"], last_clock=event["clock"])
    if tag == "START":
        cp = s.checkpoint
        if s.starts or cp.used_attempts or cp.outstanding_attempts or (cp.last_start_us is not None and event["monotonic_us"] - cp.last_start_us < 2_000_000):
            return Transition(_held(s, "P1_REPLAY_OR_START_SPACING"), True)
        # Settlement moves the reservation into usage; it does not add
        # alongside it. The single stage-attempt slot is now consumed
        # (a fact that persists regardless of eventual outcome), so the
        # outstanding/reserved count drops to zero here, not at ACCOUNT_ACK.
        return Transition(replace(s, phase="STARTED", starts=1, used_attempts=1,
                                  reserved_attempts=0, start_us=event["monotonic_us"]), True)
    if tag == "STATUS":
        held = event["status"] in DENIAL or event["explicit_denial"]
        return Transition(replace(s, phase="RECEIVING", status=event["status"],
                                  denials=s.denials + ((f"synthetic://denial/status-{event['status']}",) if held else ())), True)
    if tag == "HEADERS":
        if s.headers:
            # Reject without having already advanced sequence/head: `state`,
            # not the locally-advanced `s`.
            return Transition(state, False, "DUPLICATE_HEADERS")
        good, length, held = _frame(event["headers"])
        return Transition(replace(s, headers=tuple(event["headers"]), content_length=length,
                                  framing_valid=good, denials=s.denials + (("synthetic://denial/header",) if held else ())), True)
    if tag == "BODY":
        total = _add(s.delivered_bytes, len(event["data"]))
        if total is None:
            return Transition(_held(s, "COUNTER_OVERFLOW"), True)
        retained = (s.body + event["data"])[:BODY_CAP + BODY_CHUNK]
        s = replace(s, delivered_bytes=total, body=retained)
        if total > BODY_CAP:
            # Known delivered excess is recorded in full (no refund): the
            # reservation grows to cover exactly what was actually
            # delivered, so used+outstanding can never under-report it.
            s = replace(s, reserved_body_bytes=max(s.reserved_body_bytes, total), poisoned=True)
            return Transition(_held(s, "BODY_OVERDELIVERY"), True)
        return Transition(s, True)
    if tag == "CLOSE_ACK":
        if s.start_us is None:
            return Transition(_held(s, "MISSING_START"), True)
        return Transition(replace(s, phase="CLOSED", refs=s.refs + (("closure", event["ref"]),)), True)
    if tag == "ACCOUNT_ACK":
        if s.start_us is None or s.last_monotonic_us is None:
            return Transition(_held(s, "MISSING_CLOSE_CLOCK"), True)
        elapsed = s.last_monotonic_us - s.start_us
        return Transition(replace(s, phase="ACCOUNTED", used_body_bytes=s.delivered_bytes,
                                  used_time_us=elapsed, reserved_body_bytes=0,
                                  reserved_time_us=0, reserved_attempts=0,
                                  refs=s.refs + (("accounting", event["ref"]),)), True)
    if tag == "SEAL_ACK":
        outcome = ("DENIED_HELD" if s.denials else "RETAINED_UNQUALIFIED" if
                   s.status == 200 and s.framing_valid and s.content_length == s.delivered_bytes and s.headers else "RETAINED_INVALID")
        return Transition(replace(s, phase=outcome, refs=s.refs + (("seal", event["ref"]),),
                                  report_reserved=False), True)
    return Transition(state, False, "UNREACHABLE")


def _sat_add(a: int, b: int) -> tuple[int, bool]:
    n = a + b
    return (n, False) if n <= MAX else (MAX, True)


def result(state: ModelState) -> ModelResult:
    if type(state) is not ModelState:
        raise ValueError("INVALID_STATE")
    cp = state.checkpoint
    # A checkpoint carried forward from an external ledger can in principle
    # already sit near the counter ceiling; saturate rather than let the sum
    # exceed MAX and fail `ModelResult.__post_init__`'s bound check with a
    # raise from what is meant to be a pure reporting function.
    used_attempts, o1 = _sat_add(cp.used_attempts, state.used_attempts)
    used_body_bytes, o2 = _sat_add(cp.used_body_bytes, state.used_body_bytes)
    used_time_us, o3 = _sat_add(cp.used_time_us, state.used_time_us)
    outstanding_attempts, o4 = _sat_add(cp.outstanding_attempts, state.reserved_attempts)
    outstanding_body_bytes, o5 = _sat_add(cp.outstanding_body_bytes, state.reserved_body_bytes)
    outstanding_time_us, o6 = _sat_add(cp.outstanding_time_us, state.reserved_time_us)
    reasons = state.reasons + (("COUNTER_SATURATED",) if any((o1, o2, o3, o4, o5, o6)) else ())
    return ModelResult(
        schema=SCHEMA, synthetic=True, outcome=state.phase, attempt_state=state.phase,
        fingerprint=state.fingerprint, input_history_head=state.input_head,
        output_history_head=state.head, starts=state.starts,
        used_attempts=used_attempts, used_body_bytes=used_body_bytes, used_time_us=used_time_us,
        outstanding_attempts=outstanding_attempts, outstanding_body_bytes=outstanding_body_bytes,
        outstanding_time_us=outstanding_time_us,
        holds=state.denials, reasons=reasons, refs=state.refs,
        delivered_bytes=state.delivered_bytes, poisoned=state.poisoned,
    )


_SNAPSHOT_KEYS = frozenset((
    "schema", "phase", "head", "fingerprint", "sequence", "owner",
    "used_attempts", "delivered_bytes", "denials",
))


def recover_synthetic(snapshot_raw: bytes, checkpoint: Checkpoint) -> Transition:
    if not _valid_checkpoint(checkpoint):
        raise ValueError("INVALID_CHECKPOINT")
    if type(snapshot_raw) is not bytes or len(snapshot_raw) > RAW_CAP:
        return Transition(_refusal(checkpoint, ("INVALID_SNAPSHOT",)), False, "INVALID_SNAPSHOT")
    try:
        doc = strict_json_loads(snapshot_raw)
    except Exception:
        doc = None
    if type(doc) is not dict or set(doc) != _SNAPSHOT_KEYS:
        return Transition(_refusal(checkpoint, ("INVALID_SNAPSHOT",)), False, "INVALID_SNAPSHOT")
    if (doc["schema"] != SCHEMA or doc["phase"] not in PHASES
            or type(doc["fingerprint"]) is not str or not (0 < len(doc["fingerprint"]) <= 256)
            or doc["head"] != checkpoint.expected_history_head or doc["owner"] != checkpoint.owner
            or not _integer(doc["sequence"]) or not _integer(doc["used_attempts"]) or doc["used_attempts"] > 1
            or not _integer(doc["delivered_bytes"]) or type(doc["denials"]) is not list
            or not all(_safe_ref(x) for x in doc["denials"])):
        return Transition(_refusal(checkpoint, ("TAMPERED_SNAPSHOT",)), False, "TAMPERED_SNAPSHOT")
    # Recovery must be at least as conservative as fresh admission: the same
    # checkpoint-binding invariants admission enforces (reconciled history,
    # no unfinished intent, genesis flag, known hold prefixes) apply here too.
    if (checkpoint.expected_history_head != checkpoint.external_history_head
            or checkpoint.unfinished_intents or not checkpoint.synthetic_genesis):
        return Transition(_refusal(checkpoint, ("UNRECONCILED_HISTORY_OR_INTENT",)), False, "UNRECONCILED_HISTORY_OR_INTENT")
    if checkpoint.holds and any(not h.startswith("synthetic://retained-denial/") for h in checkpoint.holds):
        return Transition(_refusal(checkpoint, ("UNKNOWN_HOLD",)), False, "UNKNOWN_HOLD")

    phase = doc["phase"]
    used_attempts = doc["used_attempts"]
    delivered = doc["delivered_bytes"]
    denials = tuple(sorted(set(checkpoint.holds + tuple(doc["denials"]))))
    poisoned = delivered > BODY_CAP

    if phase in TERMINAL and phase != "UNCERTAIN_HELD":
        # Idempotent replay of an already-sealed/refused outcome: preserve
        # exactly what was recorded (no refund, no re-interpretation as
        # uncertain), never resume it with a further event (TERMINAL
        # already blocks that in `step`). The snapshot carries no integrity
        # binding beyond head/owner/fingerprint-format, so a sealed phase is
        # only replayed if its own counters are consistent with what the live
        # state machine can actually produce for that phase; anything else is
        # tampered, not a trustworthy (even if conservative) terminal outcome.
        if phase == "REFUSED_BEFORE_DISPATCH":
            phase_consistent = used_attempts == 0 and delivered == 0
        else:
            # Every other terminal phase requires the one stage attempt to
            # have actually started, and the real state machine always holds
            # (never seals) once delivered bytes exceed BODY_CAP. `step`'s
            # SEAL_ACK branch picks DENIED_HELD exactly when `s.denials` is
            # non-empty and RETAINED_* exactly when it is empty, so a
            # snapshot claiming the other combination is an outcome the live
            # machine can never produce.
            phase_consistent = used_attempts == 1 and delivered <= BODY_CAP and (
                bool(denials) if phase == "DENIED_HELD" else not denials)
        if not phase_consistent:
            return Transition(_refusal(checkpoint, ("TAMPERED_SNAPSHOT",)), False, "TAMPERED_SNAPSHOT")
        used_body = delivered if phase != "REFUSED_BEFORE_DISPATCH" else 0
        # The snapshot schema carries no time field; charge the full stage
        # time allowance for any sealed non-refusal outcome rather than
        # silently reporting zero elapsed time, so recovery never under-
        # reports usage relative to a real ACCOUNT_ACK.
        used_time = STAGE_TIME if used_attempts == 1 else 0
        s = ModelState(phase, doc["fingerprint"], doc["sequence"], doc["head"],
                       checkpoint.expected_history_head, checkpoint.owner, checkpoint,
                       starts=used_attempts, used_attempts=used_attempts, used_body_bytes=used_body,
                       used_time_us=used_time, delivered_bytes=delivered, denials=denials,
                       reasons=("RECOVERED_SEALED_SNAPSHOT",), poisoned=poisoned)
        return Transition(s, True)

    # Any non-terminal phase (or an already-`UNCERTAIN_HELD` snapshot) means
    # the true fate of the in-flight attempt is unknown: this can never be
    # resumed, and the full stage reservation is held outstanding (at least
    # what was actually recorded as delivered, never less) so the same
    # checkpoint cannot be re-admitted.
    reserved_attempts = 0 if used_attempts >= 1 else 1
    reserved_body = max(BODY_CAP, delivered)
    s = ModelState("UNCERTAIN_HELD", doc["fingerprint"], doc["sequence"], doc["head"],
                   checkpoint.expected_history_head, checkpoint.owner, checkpoint,
                   starts=used_attempts, used_attempts=used_attempts, delivered_bytes=delivered,
                   reserved_attempts=reserved_attempts, reserved_body_bytes=reserved_body,
                   reserved_time_us=STAGE_TIME, denials=denials,
                   reasons=("RECOVERED_INCOMPLETE_INTENT",), report_reserved=True, poisoned=poisoned)
    return Transition(s, True)


def run_synthetic(inputs: SyntheticInputs, checkpoint: Checkpoint, events: tuple) -> ModelResult:
    if not _valid_checkpoint(checkpoint):
        # Any invalid checkpoint -- wrong type or a type-correct checkpoint
        # with invalid field values/types -- must refuse cleanly, never
        # raise: every other refusal path below builds its ModelResult from
        # checkpoint fields (via `_refusal`/`result`), which would crash on
        # a malformed field instead.
        return ModelResult(
            schema=SCHEMA, synthetic=True, outcome="REFUSED_BEFORE_DISPATCH",
            attempt_state="REFUSED_BEFORE_DISPATCH", fingerprint="",
            input_history_head="", output_history_head="", starts=0,
            used_attempts=0, used_body_bytes=0, used_time_us=0,
            outstanding_attempts=0, outstanding_body_bytes=0, outstanding_time_us=0,
            holds=(), reasons=("INVALID_CHECKPOINT",), refs=(),
        )
    if type(events) is not tuple or len(events) > EVENT_CAP:
        return result(_refusal(checkpoint, ("INVALID_SCRIPT",)))
    size = 0
    for e in events:
        if not _event_valid(e):
            return result(_refusal(checkpoint, ("INVALID_SCRIPT",)))
        size += len(e.get("data", b"")) + sum(len(a) + len(b) for a, b in e.get("headers", ())) + 1024
        if size > SCRIPT_CAP:
            return result(_refusal(checkpoint, ("OVERSIZED_SCRIPT",)))
    s = admit_synthetic(inputs, checkpoint)
    reject_reason = None
    for e in events:
        t = step(s, e)
        if not t.accepted:
            reject_reason = t.reason
            break
        s = t.state
    if s.phase not in TERMINAL:
        if s.phase == "LOCKED":
            # Same ambiguity as a FAULT at LOCKED (see `step`): the intent
            # write may or may not have landed, so ending or being rejected
            # here holds the full reservation rather than zero.
            s = replace(s, reserved_attempts=1, reserved_body_bytes=BODY_CAP, reserved_time_us=STAGE_TIME)
        # A script that ends before reaching a terminal event (or an empty
        # script) is a missing closure, never a clean non-terminal result.
        # Preserve the actual rejection reason alongside the generic one
        # when the script ended because an event was rejected mid-flight.
        reasons = ("SCRIPT_ENDED_EARLY",) if reject_reason is None else ("SCRIPT_ENDED_EARLY", reject_reason)
        s = _held(s, reasons)
    return result(s)
