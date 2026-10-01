"""Offline Gate 3 V4 slice 3: injected transport/clock/resource state machine
and cross-journal composition (docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md,
section 4 "Session state machine and accounting composition", section 5
"Absolute window, pacing and measured time", section 6 "Budget, store, clock
and runtime-use receipts", and section 7 slice (3)).

This module has no transport, decoder, credential or launch entrypoint. It
composes the four already-reviewed durable primitives -- ``SharedLedger`` and
``SessionLedger`` (tools/v11_r09_gate3_ledgers.py, slice 2, integrated),
``DurableBudget`` (tools/v11_r09_gate3_launch.py), and
``VersionedImmutableObjectStore`` (tools/v11_r09_gate3_store_v1.py) -- into one
ordered per-attempt state machine. It deliberately does not modify any of
those three already-accepted modules; this module is pure composition on top
of their existing public APIs. Real transport, a real clock recorder, real
decoding (GRIB) and resource measurement are injected interfaces only:
``Transport``, ``Clock`` and ``ResourceProbe``. Only the synthetic
implementations below (``SyntheticTransport``, ``FakeClock``,
``FakeResourceProbe``) are used or tested here; wiring a real adapter behind
these interfaces is explicit later work the design (section 7, after the
acceptance-slice table) places outside every offline slice, gated on its own
review. GRIB decoding is likewise out of scope: a successful attempt seals
the verified raw response bytes with caller-supplied source/decoder pin
*references* (digests), never decodes them, consistent with
tools/v11_r09_gate3_offline_io.py's own decoder being reviewed separately.

Design section 4's durable order for one attempt, exactly as implemented by
``GateRuntime.run_attempt``:

1. Resource floor (``ResourceProbe``) and absolute-window/denial pre-checks
   run first, entirely in memory. If any fails, the attempt is opened and
   immediately refused (``SessionLedger.attempt_intent`` then ``.refuse``);
   no shared ``INTENT_OPEN`` is recorded and no transport call is ever made,
   matching "Refused entries acquire terminal reasons without DNS or
   transport calls." (This module reads that sentence as: the shared global
   token is only ever claimed for an attempt that will actually proceed to
   transport; a refused entry's own session record is sufficient evidence
   that it never dispatched. See the handoff note for why this interpretation
   was chosen over the alternative reading.)
2. Otherwise: ``SessionLedger.attempt_intent`` then ``SharedLedger.
   intent_open`` are persisted before any reservation.
3. ``DurableBudget.reserve`` persists before transport; ``SessionLedger.
   budget_reserved`` binds the actual reserve event hash. A failure here
   (including the existing durable pacing check already enforced by
   ``DurableBudget.reserve``'s ``min_start_interval_seconds``/``started_
   monotonic`` arguments) propagates and leaves the attempt held at
   ``RESERVED`` for restart/inspection -- this module adds no recovery path
   around that failure, per section 4: "Do not add a recovery path that
   calls complete() on an old incomplete reservation."
4. Immediately before dispatch: the window/denial pre-checks and the
   absolute dispatch deadline (section 5) are rechecked against a fresh
   clock sample; ``SessionLedger.dispatch_intent`` is persisted; only then
   is the injected ``Transport`` called.
5. The response status is classified for denial *before* any response chunk
   is charged to the budget ("record denials first"): a denial is persisted
   to both ledgers immediately, ahead of any ``budget.consume`` call. Every
   chunk is then charged via ``budget.consume`` in order; an adapter
   overdelivery (``STREAM_ABORT_AT_ALLOWANCE``) is caught only long enough to
   still close the session/shared records with the true delivered-byte
   count (so ``SessionLedger``'s own R4 overdelivery/poisoning logic from
   slice 2 fires correctly), then the attempt returns ``OVERDELIVERY_HELD``
   without ever reaching ``ACCOUNTED`` -- the caller must stop issuing
   further attempts against this budget/session once that outcome is seen,
   because ``DurableBudget`` itself is now permanently violated.
6. ``SessionLedger.transport_closed`` and ``SharedLedger.intent_closed`` are
   persisted (binding the denial-history head and the budget's own current
   head as the accounting head) before ``budget.complete``.
7. ``SessionLedger.accounted`` is persisted, binding the budget completion
   event hash. A denied or otherwise-failed attempt terminates ``FAILED``
   here. A successful, verified body is resource-floor-rechecked (section 3:
   "before each one-field decode batch") and sealed into the store as a RAW
   object, carrying the caller-supplied source/decoder/clock-policy pin
   references and dependency receipt hashes; ``SessionLedger.
   object_witnessed`` then ``.terminal`` (SUCCESS) follow. A late completion
   (section 5: upper bound after D) is recorded as a diagnostic-only
   terminal reason; ``ObjectReceipt.historical_feature_eligible`` is always
   ``False`` regardless, exactly as the store itself already fixes.

Monotonic pacing: section 5 additionally wants the next dispatch sample
bounded against the *previous attempt's actual transport-closure* monotonic
sample, which the already-accepted ``SessionLedger.transport_closed`` does
not record (carried forward from the slice-2 review as a P3 note: "TRANSPORT_
CLOSED has no closure monotonic sample"). This module does not add that field
to the already-reviewed ``SessionLedger`` schema. Instead it relies on
``DurableBudget.reserve``'s existing, already-accepted, durable, restart-safe
``min_start_interval_seconds`` gate on successive ``started_monotonic``
values (anchored to each attempt's *reservation* time, which happens just
before dispatch in this module's own ordering) to provide real
cross-restart pacing enforcement. This is a deliberate, documented
substitution, not a claim that the P3 note is resolved; it remains open and
is restated in the slice-3 handoff.

No credentials, no root-custodied authority, no service actions, no V10,
AxiomTrade, financial execution or real orders. No G3-L approval or SHADOW
evidence is claimed by anything in this module. No provider mappings, source
dossiers, decoder qualification, or clock/storage qualification are supplied
here: ``AttemptRequest.source_pin``/``decoder_pin``/``clock_policy_sha256``
are caller-supplied opaque digest *references* only.
"""
from __future__ import annotations

import abc
import contextlib
import hashlib
import math
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import (
    FIELD_LIMITS, MAX_BYTES, SLOT_COUNT, DurableBudget, LaunchContractError,
    _canonical_request_path, _public_https_origin, check, digest, integer,
)
from tools.v11_r09_gate3_launch_v4 import PURPOSES
from tools.v11_r09_gate3_ledgers import SessionLedger, SharedLedger
from tools.v11_r09_gate3_offline_io import (
    ClockEvidence, ClockSequence, MeasuredClock, ObjectProvenance,
    OfflineResponse, SyntheticExchange, VersionedImmutableObjectStore,
    verify_response,
)

# Design section 3: ">=2 GiB disk and >=512 MiB available memory after
# prospective allocation, at startup, before each request and each
# one-field decode batch." Same literal values tools/v11_r09_gate3_launch_v4.py
# requires a manifest's own ``limits`` group to declare (``RESOURCE_MINIMUM``);
# redefined here rather than imported, consistent with this module family's
# established practice of duplicating small fixed primitives across files so a
# change in one module can never silently loosen another's already-reviewed
# bound (see tools/v11_r09_gate3_ledgers.py's module docstring).
MIN_FREE_DISK_BYTES = 2 * 1024 ** 3
MIN_AVAILABLE_MEMORY_BYTES = 512 * 1024 ** 2
REQUEST_ID_RE = re.compile(r'[A-Za-z0-9_-]{1,80}')
REPORT_RESERVE_BYTES = 16 * 1024 ** 2
_DENIAL_STATUSES = {401: '401', 403: '403', 429: '429', 503: '503'}


def _strong_etag(value):
    return (type(value) is str and 2 <= len(value) <= 256 and
            value[0] == value[-1] == '"' and
            all(ord(char) == 0x21 or 0x23 <= ord(char) <= 0x7e or
                0x80 <= ord(char) <= 0xff for char in value[1:-1]))


# ---------------------------------------------------------------------------
# Injected interfaces: transport, clock, resource probe.
# ---------------------------------------------------------------------------

class Transport(abc.ABC):
    """Injected transport boundary. A real adapter is explicit later work
    (section 7); this module never imports or instantiates one."""

    @abc.abstractmethod
    def dispatch(self, request_id: str) -> OfflineResponse:
        raise NotImplementedError


class SyntheticTransport(Transport):
    """Offline-only transport: exact request IDs map to preloaded fixture
    responses via the already-reviewed ``SyntheticExchange``. No socket."""

    def __init__(self, exchange: SyntheticExchange):
        check(type(exchange) is SyntheticExchange, 'RUNTIME_TRANSPORT_SHAPE')
        self._exchange = exchange

    def dispatch(self, request_id):
        return self._exchange.take(request_id)


class Clock(abc.ABC):
    """Injected clock boundary. A real measured-clock recorder is explicit
    later work (section 7); this module never reads the wall clock itself."""

    @abc.abstractmethod
    def monotonic(self) -> float:
        raise NotImplementedError

    @abc.abstractmethod
    def evidence(self, phase: str) -> ClockEvidence:
        raise NotImplementedError


class FakeClock(Clock):
    """Fully scripted, offline-only clock. Monotonic and UTC advance
    independently so tests can script uncertainty-window crossings, clock
    steps and stale measurements without any real time source."""

    def __init__(self, boot_id, *, utc=0.0, mono=0.0, uncertainty=0.05,
                 measured_mono=None, method='synthetic', evidence_advance_seconds=0.0):
        check(type(boot_id) is str and boot_id, 'RUNTIME_CLOCK_BOOT_ID')
        self.boot_id = boot_id
        self.method = method
        self._utc = float(utc)
        self._mono = float(mono)
        self._measured_mono = float(mono if measured_mono is None else measured_mono)
        self._uncertainty = float(uncertainty)
        # Offline-only convenience for exercising multi-phase clock sequences
        # (request_start/body_receipt/decode_complete/durable_seal) within a
        # single ``GateRuntime.run_attempt`` call, where the module itself
        # never advances the injected clock. Zero by default: every other
        # fixture in this test family gets a frozen clock across one call.
        self._evidence_advance_seconds = float(evidence_advance_seconds)

    def advance(self, seconds):
        self._utc += seconds
        self._mono += seconds
        self._measured_mono += seconds

    def set(self, *, utc=None, mono=None, measured_mono=None, uncertainty=None):
        if utc is not None:
            self._utc = float(utc)
        if mono is not None:
            self._mono = float(mono)
        if measured_mono is not None:
            self._measured_mono = float(measured_mono)
        if uncertainty is not None:
            self._uncertainty = float(uncertainty)

    def monotonic(self):
        return self._mono

    def evidence(self, phase):
        raw = f'{phase}:{self._utc}:{self._mono}:{self._measured_mono}:{self.boot_id}'.encode()
        reading = MeasuredClock(self._utc, self._mono, self._uncertainty,
                                 self._measured_mono, self.boot_id,
                                 hashlib.sha256(raw).hexdigest())
        if self._evidence_advance_seconds:
            self.advance(self._evidence_advance_seconds)
        return ClockEvidence(phase, reading, raw, self.method)


@dataclass(frozen=True)
class ResourceSnapshot:
    free_disk_bytes: int
    available_memory_bytes: int


class ResourceProbe(abc.ABC):
    """Injected resource boundary. A real host-resource probe is explicit
    later work; this module never calls ``os.statvfs``/``/proc`` itself for
    this check (the store's own disk-reserve check at seal time is separate,
    already-reviewed, real-filesystem code in tools/v11_r09_gate3_store_v1.py)."""

    @abc.abstractmethod
    def snapshot(self) -> ResourceSnapshot:
        raise NotImplementedError


class FakeResourceProbe(ResourceProbe):
    """Offline-only, mutable resource snapshot for scripting floor crossings."""

    def __init__(self, *, free_disk_bytes, available_memory_bytes):
        self.free_disk_bytes = free_disk_bytes
        self.available_memory_bytes = available_memory_bytes

    def snapshot(self):
        return ResourceSnapshot(self.free_disk_bytes, self.available_memory_bytes)


def _check_resources(probe):
    snapshot = probe.snapshot()
    check(type(snapshot) is ResourceSnapshot, 'RUNTIME_RESOURCE_PROBE_SHAPE')
    check(type(snapshot.free_disk_bytes) is int and
          type(snapshot.available_memory_bytes) is int, 'RUNTIME_RESOURCE_PROBE_SHAPE')
    check(snapshot.free_disk_bytes >= MIN_FREE_DISK_BYTES and
          snapshot.available_memory_bytes >= MIN_AVAILABLE_MEMORY_BYTES,
          'RUNTIME_RESOURCE_FLOOR')


# ---------------------------------------------------------------------------
# Absolute window (design section 5: S, A, D) and the per-attempt request.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AbsoluteWindow:
    """S (acquisition start), A (acquisition end), D (decision lower bound)."""
    start_utc: float
    acquisition_end_utc: float
    decision_lower_utc: float
    request_deadline_seconds: int = 30
    elapsed_cap_seconds: int = 10800
    uncertainty_cap_seconds: float = 1.0

    def __post_init__(self):
        check(all(type(v) in (int, float) and math.isfinite(v) for v in
              (self.start_utc, self.acquisition_end_utc, self.decision_lower_utc)) and
              self.start_utc < self.acquisition_end_utc <= self.decision_lower_utc,
              'RUNTIME_WINDOW_ORDER')
        check(type(self.request_deadline_seconds) is int and
              0 < self.request_deadline_seconds <= 30, 'RUNTIME_WINDOW_REQUEST_DEADLINE')
        check(type(self.elapsed_cap_seconds) is int and
              0 < self.elapsed_cap_seconds <= 10800, 'RUNTIME_WINDOW_ELAPSED_CAP')
        check(type(self.uncertainty_cap_seconds) in (int, float) and
              0 < self.uncertainty_cap_seconds <= 1, 'RUNTIME_WINDOW_UNCERTAINTY_CAP')


@dataclass(frozen=True)
class AttemptRequest:
    """One frozen, caller-prepared request. Does not itself assert a real
    provider endpoint mapping, source dossier, or decoder qualification --
    ``source_pin``/``decoder_pin``/``clock_policy_sha256`` are opaque digest
    references the caller is responsible for, exactly like
    ``ObjectProvenance`` already requires."""
    request_id: str
    purpose: str
    endpoint_id: str
    control_domain_id: str
    origin: str
    path: str
    provider: str | None
    slot_index: int | None
    range_start: int | None
    range_end: int | None
    reservation_bytes: int
    expected_etag: str
    expected_object_bytes: int | None
    source_pin: str
    decoder_pin: str
    clock_policy_sha256: str
    validator_sha256: str | None = None
    dependency_commit_hashes: tuple = field(default_factory=tuple)

    def __post_init__(self):
        check(type(self.request_id) is str and REQUEST_ID_RE.fullmatch(self.request_id),
              'RUNTIME_REQUEST_ID')
        check(self.purpose in PURPOSES, 'RUNTIME_REQUEST_PURPOSE')
        for value in (self.endpoint_id, self.control_domain_id, self.source_pin,
                      self.decoder_pin, self.clock_policy_sha256):
            digest(value, 'RUNTIME_REQUEST_DIGEST')
        if self.validator_sha256 is not None:
            digest(self.validator_sha256, 'RUNTIME_REQUEST_DIGEST')
        check(_public_https_origin(self.origin) and
              _canonical_request_path(self.origin, self.path), 'RUNTIME_REQUEST_ORIGIN_PATH')
        check(self.provider is None or self.provider in FIELD_LIMITS,
              'RUNTIME_REQUEST_PROVIDER')
        check((self.purpose == 'FIELD') == (self.provider is not None),
              'RUNTIME_REQUEST_PROVIDER')
        check(self.slot_index is None or
              (type(self.slot_index) is int and 0 <= self.slot_index < SLOT_COUNT),
              'RUNTIME_REQUEST_SLOT_INDEX')
        check((self.range_start is None) == (self.range_end is None), 'RUNTIME_REQUEST_RANGE')
        if self.range_start is not None:
            integer(self.range_start, 0, MAX_BYTES, 'RUNTIME_REQUEST_RANGE')
            integer(self.range_end, self.range_start, MAX_BYTES, 'RUNTIME_REQUEST_RANGE')
        integer(self.reservation_bytes, 1, MAX_BYTES, 'RUNTIME_REQUEST_RESERVATION')
        check(_strong_etag(self.expected_etag), 'RUNTIME_REQUEST_ETAG')
        check(self.expected_object_bytes is None or
              (type(self.expected_object_bytes) is int and self.expected_object_bytes > 0),
              'RUNTIME_REQUEST_OBJECT_BYTES')
        check(type(self.dependency_commit_hashes) is tuple and
              all(type(v) is str for v in self.dependency_commit_hashes),
              'RUNTIME_REQUEST_DEPENDENCIES')


def _request_dict(request):
    return {'origin': request.origin, 'path': request.path, 'purpose': request.purpose,
            'reservation_bytes': request.reservation_bytes, 'range_start': request.range_start,
            'range_end': request.range_end, 'provider': request.provider}


def _build_denial_record(status_str, response, *, window, receipt_reading):
    headers = {k.lower(): v for k, v in response.headers}
    retry_raw = headers.get('retry-after')
    retry_after_seconds = None
    if retry_raw is not None:
        try:
            parsed = float(retry_raw)
        except ValueError:
            parsed = None
        if parsed is not None and math.isfinite(parsed) and parsed >= 0:
            retry_after_seconds = parsed
    evidence_raw = canonical({'status': response.status, 'headers': list(response.headers)})
    return {
        'status': status_str,
        'reason': f'RUNTIME_DENIAL_HTTP_{response.status}',
        'evidence_sha256': hashlib.sha256(evidence_raw).hexdigest(),
        'evidence_missing_cause': None,
        'retry_after_seconds': retry_after_seconds,
        'window_end_utc': window.acquisition_end_utc,
        'receipt_upper_bound_utc': receipt_reading.utc_seconds + receipt_reading.uncertainty_seconds,
    }


# ---------------------------------------------------------------------------
# Ordered cross-journal acquisition (design section 3's lock order).
# ---------------------------------------------------------------------------

@contextlib.contextmanager
def acquire_runtime_journals(*, shared_dir, session_dir, budget_dir, store_root,
                              shared_kwargs, session_kwargs, budget_kwargs, store_kwargs):
    """Acquire the four durable primitives in design section 3's exact lock
    order (shared denial root, session root, DurableBudget, store v1) and
    release in reverse order on exit, including on partial-acquisition
    failure (only what was actually acquired is released)."""
    shared = SharedLedger(shared_dir, **shared_kwargs)
    try:
        session = SessionLedger(session_dir, **session_kwargs)
        try:
            budget = DurableBudget(budget_dir, **budget_kwargs)
            try:
                store = VersionedImmutableObjectStore(store_root, **store_kwargs)
            except BaseException:
                budget.close()
                raise
        except BaseException:
            session.close()
            raise
    except BaseException:
        shared.close()
        raise
    try:
        yield shared, session, budget, store
    finally:
        store.close()
        budget.close()
        session.close()
        shared.close()


# ---------------------------------------------------------------------------
# The per-attempt runtime driver.
# ---------------------------------------------------------------------------

class GateRuntime:
    """Drives one ``AttemptRequest`` at a time through the design section 4
    durable order over already-open ``SharedLedger``/``SessionLedger``/
    ``DurableBudget``/``VersionedImmutableObjectStore`` instances (acquire
    them with ``acquire_runtime_journals``). Does not own their lifecycle."""

    def __init__(self, *, shared: SharedLedger, session: SessionLedger,
                 budget: DurableBudget, store: VersionedImmutableObjectStore,
                 transport: Transport, clock: Clock, resources: ResourceProbe,
                 window: AbsoluteWindow, allowed_peer_ips: tuple,
                 manifest_sha256: str):
        check(type(shared) is SharedLedger and type(session) is SessionLedger and
              type(budget) is DurableBudget and type(store) is VersionedImmutableObjectStore,
              'RUNTIME_COMPOSITION_SHAPE')
        check(isinstance(transport, Transport) and isinstance(clock, Clock) and
              isinstance(resources, ResourceProbe), 'RUNTIME_COMPOSITION_SHAPE')
        check(type(window) is AbsoluteWindow, 'RUNTIME_COMPOSITION_SHAPE')
        check(type(allowed_peer_ips) is tuple and allowed_peer_ips, 'RUNTIME_PEER_IPS')
        digest(manifest_sha256, 'RUNTIME_MANIFEST_DIGEST')
        self.shared, self.session, self.budget, self.store = shared, session, budget, store
        self.transport, self.clock, self.resources = transport, clock, resources
        self.window = window
        self.allowed_peer_ips = allowed_peer_ips
        self.manifest_sha256 = manifest_sha256
        # Section 5: "Persist that elapsed deadline from the first valid
        # session clock plus <=10,800 seconds". Frozen once, in this process,
        # on the first sample that passes the window check; never recomputed
        # or widened afterward, matching "it never extends an already fixed
        # request deadline."
        self._elapsed_deadline_mono = None
        _check_resources(self.resources)

    def _enforce_window(self, reading):
        check(0 <= reading.uncertainty_seconds <= self.window.uncertainty_cap_seconds,
              'RUNTIME_CLOCK_UNCERTAINTY_EXCEEDED')
        lower = reading.utc_seconds - reading.uncertainty_seconds
        upper = reading.utc_seconds + reading.uncertainty_seconds
        check(lower >= self.window.start_utc, 'RUNTIME_CLOCK_BEFORE_WINDOW_START')
        check(upper < self.window.acquisition_end_utc, 'RUNTIME_CLOCK_AT_OR_AFTER_ACQUISITION_END')
        if self._elapsed_deadline_mono is None:
            self._elapsed_deadline_mono = reading.monotonic_seconds + self.window.elapsed_cap_seconds

    def _dispatch_deadline(self, reading):
        offset_hi = (reading.utc_seconds - reading.monotonic_seconds) + reading.uncertainty_seconds
        candidates = [reading.monotonic_seconds + self.window.request_deadline_seconds,
                      self.window.acquisition_end_utc - offset_hi]
        if self._elapsed_deadline_mono is not None:
            candidates.append(self._elapsed_deadline_mono)
        return min(candidates)

    def _session_refuse(self, request, reason):
        self.session.attempt_intent(request.request_id, purpose=request.purpose,
            endpoint_id=request.endpoint_id, max_reservation_bytes=request.reservation_bytes,
            range_start=request.range_start, range_end=request.range_end,
            validator_sha256=request.validator_sha256, denial_head=self.shared.prev)
        self.session.refuse(request.request_id, reason=reason)
        return {'request_id': request.request_id, 'outcome': 'REFUSED', 'reason': reason}

    def run_attempt(self, request: AttemptRequest) -> dict:
        check(type(request) is AttemptRequest, 'RUNTIME_REQUEST_SHAPE')

        pre = self.clock.evidence('request_start')
        try:
            _check_resources(self.resources)
            self._enforce_window(pre.reading)
            check(not self.shared.is_blocked(request.control_domain_id,
                  now_utc=pre.reading.utc_seconds), 'RUNTIME_CONTROL_DOMAIN_BLOCKED')
        except LaunchContractError as exc:
            return self._session_refuse(request, str(exc))

        # Step 1 (design section 4): durable opens, no transport yet.
        self.session.attempt_intent(request.request_id, purpose=request.purpose,
            endpoint_id=request.endpoint_id, max_reservation_bytes=request.reservation_bytes,
            range_start=request.range_start, range_end=request.range_end,
            validator_sha256=request.validator_sha256, denial_head=self.shared.prev)
        self.shared.intent_open(request.request_id, purpose=request.purpose,
            endpoint_id=request.endpoint_id, control_domain_id=request.control_domain_id,
            manifest_sha256=self.manifest_sha256, max_reservation_bytes=request.reservation_bytes,
            now_utc=pre.reading.utc_seconds)

        # Step 2: reservation persists before transport.
        self.budget.reserve(request.request_id, request.reservation_bytes,
                             started_monotonic=self.clock.monotonic())
        self.session.budget_reserved(request.request_id, reserve_event_hash=self.budget.prev)

        # Step 3: recheck immediately before dispatch; only then call transport.
        dispatch = self.clock.evidence('request_start')
        self._enforce_window(dispatch.reading)
        check(not self.shared.is_blocked(request.control_domain_id,
              now_utc=dispatch.reading.utc_seconds), 'RUNTIME_CONTROL_DOMAIN_BLOCKED')
        deadline_mono = self._dispatch_deadline(dispatch.reading)
        check(deadline_mono > dispatch.reading.monotonic_seconds,
              'RUNTIME_DEADLINE_ALREADY_PASSED')
        self.session.dispatch_intent(request.request_id,
            measured_start_monotonic=dispatch.reading.monotonic_seconds)

        response = self.transport.dispatch(request.request_id)
        check(type(response) is OfflineResponse, 'RUNTIME_TRANSPORT_RESPONSE_SHAPE')

        # Step 4: denial is classified and persisted *before* any chunk is
        # charged or parsed -- "record denials first".
        denial_status = _DENIAL_STATUSES.get(response.status)
        denial_record = None
        if denial_status is not None:
            denial_record = _build_denial_record(denial_status, response,
                window=self.window, receipt_reading=dispatch.reading)
            self.shared.denial_observed(request.request_id, denial=denial_record)
            self.session.denial(request.request_id, reason=denial_record['reason'],
                                 shared_denial_event_hash=self.shared.prev)

        delivered = 0
        overdelivered = False
        chunk = b''
        try:
            for chunk in response.chunks:
                self.budget.consume(request.request_id, chunk)
                delivered += len(chunk)
        except LaunchContractError as exc:
            if str(exc) != 'STREAM_ABORT_AT_ALLOWANCE':
                raise
            overdelivered = True
            delivered += len(chunk)

        verified_body = None
        verify_error = None
        if denial_record is None and not overdelivered:
            try:
                verified_body = verify_response(_request_dict(request), response,
                    allowed_peer_ips=self.allowed_peer_ips, expected_etag=request.expected_etag,
                    expected_object_bytes=request.expected_object_bytes)
            except LaunchContractError as exc:
                verify_error = str(exc)

        # Step 5/6: close session + shared records before budget.complete.
        denial_head_snapshot = self.shared.prev
        session_outcome = 'OK' if verified_body is not None else 'FAILED'
        self.session.transport_closed(request.request_id, outcome=session_outcome,
            total_delivered_bytes=delivered, denial_history_head=denial_head_snapshot,
            accounting_head=self.budget.prev)
        shared_outcome = ('DENIED' if denial_record is not None else
                          ('OK' if verified_body is not None else 'FAILED'))
        self.shared.intent_closed(request.request_id, outcome=shared_outcome,
            accounting_head=self.budget.prev, total_delivered_bytes=delivered)

        if self.session.attempt['overdelivered']:
            # R4 (slice 2): permanently poisoned. No ACCOUNTED/terminal is
            # reachable; the caller must stop issuing further attempts
            # against this session/budget.
            return {'request_id': request.request_id, 'outcome': 'OVERDELIVERY_HELD',
                    'reason': 'RUNTIME_OVERDELIVERY_POISONED'}

        self.budget.complete(request.request_id)
        self.session.accounted(request.request_id, completion_event_hash=self.budget.prev)

        if verified_body is None:
            reason = denial_record['reason'] if denial_record is not None else verify_error
            self.session.terminal(request.request_id, outcome='FAILED', reason=reason,
                report_reserved_bytes=self.session.report_reserve_bytes)
            return {'request_id': request.request_id, 'outcome': 'FAILED', 'reason': reason}

        # Step 7: successful, verified body -- resource floor again before
        # this one-field decode/seal batch, then seal as a RAW object.
        _check_resources(self.resources)
        provenance = ObjectProvenance('RAW', request.request_id, request.request_id,
            request.source_pin, request.decoder_pin, request.clock_policy_sha256,
            request.dependency_commit_hashes)
        body_receipt = self.clock.evidence('body_receipt')
        decode_complete = self.clock.evidence('decode_complete')
        prefix = (dispatch, body_receipt, decode_complete)
        receipt = self.store.seal_with_provenance(verified_body, provenance, prefix,
            lambda: self.clock.evidence('durable_seal'))
        self.session.object_witnessed(request.request_id,
            store_receipt_commit_hash=receipt.commit_hash)

        timely = True
        try:
            sequence = ClockSequence(boot_id=dispatch.reading.boot_id,
                max_measurement_age_seconds=self.store.context['max_clock_age'])
            for evidence in receipt.clocks:
                sequence.record(evidence.phase, evidence.reading)
            timely = sequence.causal_before(self.window.decision_lower_utc)
        except LaunchContractError:
            # Diagnostic recomputation only: the object is already sealed and
            # witnessed above either way. A late/uncertain completion stays
            # diagnostic, never relabeled, never retried.
            timely = False

        reason = 'RUNTIME_SUCCESS_TIMELY' if timely else 'RUNTIME_SUCCESS_LATE_DIAGNOSTIC_ONLY'
        self.session.terminal(request.request_id, outcome='SUCCESS', reason=reason,
            report_reserved_bytes=self.session.report_reserve_bytes)
        return {'request_id': request.request_id, 'outcome': 'SUCCESS', 'reason': reason,
                'store_receipt_commit_hash': receipt.commit_hash, 'timely': timely}


# ---------------------------------------------------------------------------
# Bounded terminal report: full 2,713-row denominator (design section 6).
# ---------------------------------------------------------------------------

def build_terminal_report(*, attempted: dict) -> dict:
    """Partition all ``SLOT_COUNT`` (2,713) original raw slots exactly once.

    ``attempted`` maps slot_index -> an outcome record (as returned by
    ``GateRuntime.run_attempt``, keyed by the caller using the originating
    ``AttemptRequest.slot_index``) for every slot that was scheduled this
    run. Every slot absent from ``attempted`` is reported NEVER_ATTEMPTED --
    this never shrinks or claims an alternate denominator, matching section
    6: "Terminal report partitions all 2,713 original raw slots exactly
    once ... including never-attempted slots and failed prerequisites."
    This module has no real provider/schedule mapping, so a slot's provider/
    run/member/hour identity is deliberately not claimed here; only its
    integer index and its attempt outcome are reported.
    """
    check(type(attempted) is dict, 'REPORT_ATTEMPTED_SHAPE')
    check(all(type(k) is int and 0 <= k < SLOT_COUNT for k in attempted),
          'REPORT_SLOT_INDEX')
    rows = []
    counts = {}
    for slot in range(SLOT_COUNT):
        if slot in attempted:
            record = attempted[slot]
            check(type(record) is dict and type(record.get('outcome')) is str and
                  type(record.get('reason')) is str, 'REPORT_ATTEMPT_RECORD_SHAPE')
            label = record['outcome']
            row = {'slot_index': slot, 'status': 'ATTEMPTED', 'outcome': record['outcome'],
                   'reason': record['reason'], 'request_id': record.get('request_id')}
        else:
            label = 'NEVER_ATTEMPTED'
            row = {'slot_index': slot, 'status': 'NEVER_ATTEMPTED', 'outcome': None,
                   'reason': 'RUNTIME_SLOT_NOT_IN_SCHEDULE', 'request_id': None}
        rows.append(row)
        counts[label] = counts.get(label, 0) + 1
    check(len(rows) == SLOT_COUNT and
          len({r['slot_index'] for r in rows}) == SLOT_COUNT, 'REPORT_PARTITION_INVARIANT')
    return {'slot_count': SLOT_COUNT, 'rows': rows, 'outcome_counts': counts}


class ReportSink:
    """Pre-reserved report sink (design section 3: "Reserve a separate
    16 MiB report area before acquisition"). Exclusive creation only: a
    persisted report (complete or incomplete) is never silently overwritten
    by a later rerun, matching section 6: "Report completion requires actual
    durable report evidence; if capacity/persistence fails record INCOMPLETE
    where possible and retain the hold."
    """
    REPORT_FILE_NAME = 'gate3_terminal_report.json'
    INCOMPLETE_FILE_NAME = 'gate3_terminal_report.incomplete.json'

    def __init__(self, directory):
        path = Path(directory)
        check(path.is_absolute() and path == path.resolve(), 'REPORT_DIRECTORY')
        for ancestor in (path, *path.parents):
            check(not stat.S_ISLNK(os.lstat(ancestor).st_mode), 'REPORT_PATH_SYMLINK')
        self.path = path
        self.dir_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            directory_stat = os.fstat(self.dir_fd)
            check(stat.S_ISDIR(directory_stat.st_mode) and
                  directory_stat.st_uid == os.getuid() and
                  stat.S_IMODE(directory_stat.st_mode) == 0o700 and
                  os.stat(path).st_ino == directory_stat.st_ino and
                  os.stat(path).st_dev == directory_stat.st_dev,
                  'REPORT_DIRECTORY_PRIVATE_MODE')
            fs = os.fstatvfs(self.dir_fd)
            check(fs.f_bavail * fs.f_frsize >= REPORT_RESERVE_BYTES,
                  'REPORT_RESERVE_UNAVAILABLE')
        except BaseException:
            os.close(self.dir_fd)
            raise

    def persist(self, report):
        data = canonical(report) + b'\n'
        check(len(data) <= REPORT_RESERVE_BYTES, 'REPORT_CAPACITY_EXCEEDED')
        fs = os.fstatvfs(self.dir_fd)
        check(fs.f_bavail * fs.f_frsize >= len(data), 'REPORT_DISK_RESERVE')
        fd = os.open(self.REPORT_FILE_NAME, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                     os.O_NOFOLLOW, 0o600, dir_fd=self.dir_fd)
        try:
            written = os.write(fd, data)
            check(written == len(data), 'REPORT_SHORT_WRITE')
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(self.dir_fd)
        return {'classification': 'COMPLETE', 'bytes': len(data)}

    def persist_incomplete(self, reason):
        check(type(reason) is str and reason, 'REPORT_INCOMPLETE_REASON')
        data = canonical({'classification': 'INCOMPLETE', 'reason': reason}) + b'\n'
        fd = os.open(self.INCOMPLETE_FILE_NAME, os.O_CREAT | os.O_EXCL | os.O_WRONLY |
                     os.O_NOFOLLOW, 0o600, dir_fd=self.dir_fd)
        try:
            written = os.write(fd, data)
            check(written == len(data), 'REPORT_SHORT_WRITE')
            os.fsync(fd)
        finally:
            os.close(fd)
        os.fsync(self.dir_fd)
        return {'classification': 'INCOMPLETE', 'reason': reason}

    def close(self):
        if self.dir_fd is not None:
            os.close(self.dir_fd)
            self.dir_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
