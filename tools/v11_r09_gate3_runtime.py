"""Offline Gate 3 V4 slice-3 runtime and durable report composition.

The injected transport, clock, and resource interfaces have synthetic fixtures
only. Acquisition requires an exact validated V4 projection, a pinned expected
plan digest, the four acquired journals, and a physically reserved report sink.
The state machine binds request order, original clock evidence, denial history,
known transport closure, budget accounting, store receipts, and bounded capture
receipts. It does not grant provider access, G3-L, feature eligibility, or
financial/SHADOW authority. A caller-supplied review digest or constructor
value is data-integrity evidence, not external launch approval. Real source
mapping, decoding, clock measurement, and network adapters require separate
review.
"""
from __future__ import annotations

import abc
import base64
import datetime
import email.utils
import contextlib
import fcntl
import hashlib
import math
import os
import re
import stat
from dataclasses import asdict, dataclass, field
from pathlib import Path

from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import (
    FIELD_LIMITS, JOURNAL_MAX_BYTES, MAX_BYTES, SLOT_COUNT, DurableBudget, LaunchContractError,
    _canonical_request_path, _public_https_origin, check, digest, exact, integer,
    parse_canonical,
)
from tools.v11_r09_gate3_launch_v4 import PURPOSES, validate_manifest_v4
from tools.v11_r09_gate3_ledgers import LEDGER_MAX_BYTES, SessionLedger, SharedLedger
from tools.v11_r09_gate3_store_v1 import MAX_JOURNAL as STORE_MAX_JOURNAL
from tools.v11_r09_gate3_offline_io import (
    ClockEvidence, ClockSequence, MeasuredClock, ObjectProvenance,
    OfflineResponse, SyntheticExchange, VersionedImmutableObjectStore,
    verify_response,
)
from tools.v11_gate3_preflight_attempt_model import (
    BODY_CAP as ATTEMPT_MODEL_BODY_CAP,
    Checkpoint as AttemptCheckpoint,
    ModelState as AttemptModelState,
    SyntheticInputs as AttemptSyntheticInputs,
    admit_synthetic as attempt_model_admit_synthetic,
)
from tools.v11_gate3_evidence_intake_guard import EvidenceIntakeGuard
from tools.v11_gate3_evidence_preflight_checker import (
    FROZEN_REQUEST as _ATTEMPT_MODEL_FROZEN_REQUEST,
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
# tools/v11_r09_gate3_launch_v4.py's own fixed ``max_body_chunks_per_request``
# runtime resource-bound constant (part of a manifest's validated,
# never-caller-widened ``resource_bounds``): redefined here rather than
# imported, consistent with this module's own ``MIN_FREE_DISK_BYTES`` above
# and the module-family rationale in tools/v11_r09_gate3_ledgers.py's
# docstring. Gate 3 V4 slice-3 repair (F7): the synthetic transport interface
# itself never capped the number of chunks a fixture could return, so a
# prospective capacity estimate built from this constant was previously
# unenforced and therefore not actually conservative. It is now also checked
# against every dispatched stream below, so the preflight bound is real.
MAX_BODY_CHUNKS_PER_REQUEST = 32
# F7: the worst-case (longest, successful) per-request session-ledger event
# count: attempt_intent, budget_reserved, request_deadline_fixed,
# dispatch_intent, transport_closed, accounted, object_witnessed, terminal,
# capture_receipt (9), plus one ``clock_observed`` for each of the up to
# eight original clock samples one successful attempt now takes (request
# start x3 recheck samples, body_receipt, closure, and the three local
# decode/seal phases bound into the durable intersection by F1's
# ``_observe_local_clock``). 12 (the pre-repair estimate) already undercounted
# the pre-F1 path (14); a fixed, generously rounded constant is used rather
# than re-deriving the exact figure from the current implementation, so this
# bound cannot silently track a future change in how many samples one
# attempt happens to take.
SESSION_EVENTS_PER_REQUEST = 20
# F2: the exact origin/path the attempt model's own admission fingerprint is
# bound to (``P1_GEFS_INDEX``/``GET``, tools/v11_gate3_preflight_attempt_model.py's
# ``admit_synthetic``) -- the one reviewed pilot request this guard may ever
# cover. Imported from the evidence preflight checker's own frozen scalars
# (the single source of truth for this pilot's identity) rather than
# re-literalled here, so a change to the reviewed pilot scope cannot silently
# desync from what this guard actually enforces.
_ATTEMPT_MODEL_PILOT_ORIGIN = _ATTEMPT_MODEL_FROZEN_REQUEST['origin']
_ATTEMPT_MODEL_PILOT_PATH = _ATTEMPT_MODEL_FROZEN_REQUEST['path']


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
    (section 7); this module never imports or instantiates one.

    Gate 3 V4 slice-3 repair (F4): the boundary receives the whole frozen,
    already-validated ``AttemptRequest`` -- not merely its ``request_id`` --
    so an adapter is contractually bound to the exact immutable origin/path/
    range/provider intent this runtime decided to dispatch, not just an
    opaque label it could reinterpret against different request metadata.
    """

    @abc.abstractmethod
    def dispatch(self, request: 'AttemptRequest', *, deadline_monotonic: float,
                 remaining_seconds: float):
        raise NotImplementedError


class SyntheticResponseStream:
    """One eager fixture boundary. Every chunk is already delivered when the
    fixture is taken; read() exposes it in order, but the complete prefetched
    byte count is available for violation accounting. Closure is explicit and
    requires checked framing and consumption of every prefetched chunk."""

    def __init__(self, response):
        self.response = response
        self.index = 0
        self.prefetched_bytes = sum(len(chunk) for chunk in response.chunks)

    def read(self, *, maximum_bytes, remaining_seconds):
        check(remaining_seconds > 0 and maximum_bytes > 0,
              'RUNTIME_READ_DEADLINE_OR_ALLOWANCE')
        if self.index == len(self.response.chunks):
            return None
        chunk = self.response.chunks[self.index]
        self.index += 1
        return chunk

    def close(self, *, remaining_seconds):
        check(remaining_seconds > 0, 'RUNTIME_CLOSE_DEADLINE')
        headers = _bounded_headers(self.response)
        return (self.index == len(self.response.chunks) and
                headers.get('content-length') == str(self.prefetched_bytes))


class SyntheticTransport(Transport):
    """Offline-only transport: exact request IDs map to preloaded fixture
    responses via the already-reviewed ``SyntheticExchange``. No socket."""

    def __init__(self, exchange: SyntheticExchange):
        check(type(exchange) is SyntheticExchange, 'RUNTIME_TRANSPORT_SHAPE')
        self._exchange = exchange

    def dispatch(self, request, *, deadline_monotonic, remaining_seconds):
        check(type(request) is AttemptRequest, 'RUNTIME_TRANSPORT_REQUEST_SHAPE')
        check(remaining_seconds > 0 and math.isfinite(deadline_monotonic),
              'RUNTIME_DISPATCH_DEADLINE')
        return SyntheticResponseStream(self._exchange.take(request.request_id))


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


@dataclass(frozen=True)
class CapacityPlan:
    """Conservative allocation for one immutable subset, including scratch.

    The caller supplies measured free resources, never an allocation estimate.
    These bounds are derived from the frozen requests and journal record caps.
    """
    disk_bytes: int
    memory_bytes: int
    budget_records: int
    store_events: int
    aggregate_nodes: int = 0

    @classmethod
    def for_requests(cls, requests, events=()):
        n = len(requests)
        body = sum(r.reservation_bytes for r in requests)
        # F7: a one-byte chunk is as admissible as a 64 KiB one on this
        # synthetic interface, so the worst case per request is the *most*
        # chunks it could possibly produce, not the fewest. That is bounded
        # above both by the body itself (a chunk is never empty) and by the
        # fixed, enforced ``MAX_BODY_CHUNKS_PER_REQUEST`` policy (checked
        # against every dispatched stream in ``GateRuntime.run_attempt``),
        # never by ``ceil(reservation / 65536)`` (which assumes maximum-size
        # chunks and silently undercounts every smaller-chunked response).
        chunks = sum(min(r.reservation_bytes, MAX_BODY_CHUNKS_PER_REQUEST)
                     for r in requests)
        # A tree with fanout 256 needs a bounded number of evidence nodes if
        # requested events later require dependency aggregation. Reserve them
        # even when this RAW-only slice never seals such a node.
        aggregate_nodes = 0
        for event in events:
            width = len(event.field_request_ids)
            aggregate_nodes += 1  # the event's root manifest
            while width > 256:
                width = (width + 255) // 256
                aggregate_nodes += width
        # Budget: reserve + at most 32 regular chunks + completion, or
        # reserve + at most 32 regular chunks + one aggregate eager recovery
        # or violation. Store: three records per RAW/object. Session: 20
        # events per request. Shared: four. Four fixed journal/report closure
        # records include initialization/finalization headroom on fresh and
        # reopened roots. Objects include a temporary copy.
        records = chunks + 3 * n
        store_events = 3 * (n + aggregate_nodes)
        disk = (2 * body + 2 * aggregate_nodes * 4194304 +
                (records + store_events + 20 * n + 4 * n + 4) * 65536 +
                REPORT_RESERVE_BYTES + 4096)
        memory = (max((r.reservation_bytes for r in requests), default=0) * 2 +
                  65536 * 4)
        return cls(disk, memory, records, store_events, aggregate_nodes)


def _check_prospective_resources(probe, capacity):
    snapshot = probe.snapshot()
    check(type(snapshot) is ResourceSnapshot and
          type(snapshot.free_disk_bytes) is int and
          type(snapshot.available_memory_bytes) is int,
          'RUNTIME_RESOURCE_PROBE_SHAPE')
    check(snapshot.free_disk_bytes - capacity.disk_bytes >= MIN_FREE_DISK_BYTES and
          snapshot.available_memory_bytes - capacity.memory_bytes >=
          MIN_AVAILABLE_MEMORY_BYTES, 'RUNTIME_PROSPECTIVE_CAPACITY')


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
              0 <= self.uncertainty_cap_seconds <= 1, 'RUNTIME_WINDOW_UNCERTAINTY_CAP')


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
    prerequisite_request_ids: tuple[str, ...] = field(default_factory=tuple)

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
        # F4/F5: a FIELD request with no original slot identity has no frozen
        # denominator row to be charged to or counted against -- it can
        # succeed and then silently vanish from the full 2,713-row report
        # (every row reporting NEVER_ATTEMPTED) instead of ever appearing as
        # ATTEMPTED/SUCCESS. Every FIELD request must bind its own slot.
        check(self.purpose != 'FIELD' or self.slot_index is not None,
              'RUNTIME_REQUEST_FIELD_REQUIRES_SLOT')
        check((self.range_start is None) == (self.range_end is None), 'RUNTIME_REQUEST_RANGE')
        if self.range_start is not None:
            integer(self.range_start, 0, MAX_BYTES, 'RUNTIME_REQUEST_RANGE')
            integer(self.range_end, self.range_start, MAX_BYTES, 'RUNTIME_REQUEST_RANGE')
        integer(self.reservation_bytes, 1, MAX_BYTES, 'RUNTIME_REQUEST_RESERVATION')
        purpose_cap = (FIELD_LIMITS[self.provider] if self.purpose == 'FIELD' else
                       3145728 if self.purpose == 'INDEX' else 4194304)
        check(self.reservation_bytes <= purpose_cap, 'RUNTIME_PURPOSE_CAP')
        check(_strong_etag(self.expected_etag), 'RUNTIME_REQUEST_ETAG')
        check(self.expected_object_bytes is None or
              (type(self.expected_object_bytes) is int and self.expected_object_bytes > 0),
              'RUNTIME_REQUEST_OBJECT_BYTES')
        check(type(self.dependency_commit_hashes) is tuple and
              all(type(v) is str for v in self.dependency_commit_hashes),
              'RUNTIME_REQUEST_DEPENDENCIES')
        check(type(self.prerequisite_request_ids) is tuple and
              len(set(self.prerequisite_request_ids)) ==
              len(self.prerequisite_request_ids) and
              all(type(v) is str and REQUEST_ID_RE.fullmatch(v)
                  for v in self.prerequisite_request_ids),
              'RUNTIME_REQUEST_PREREQUISITES')


def _request_dict(request):
    return {'origin': request.origin, 'path': request.path, 'purpose': request.purpose,
            'reservation_bytes': request.reservation_bytes, 'range_start': request.range_start,
            'range_end': request.range_end, 'provider': request.provider}


@dataclass(frozen=True)
class FrozenEvent:
    event_id: str
    side: str
    primary_provider: str
    field_request_ids: tuple[str, ...]
    requested_key: tuple[str, ...] | None = None
    gate2_trial_key: tuple[str, ...] | None = None

    def __post_init__(self):
        check(type(self.event_id) is str and REQUEST_ID_RE.fullmatch(self.event_id) and
              self.side in ('HIGH', 'LOW') and
              self.primary_provider in FIELD_LIMITS and
              type(self.field_request_ids) is tuple and self.field_request_ids and
              len(set(self.field_request_ids)) == len(self.field_request_ids) and
              all(type(v) is str and REQUEST_ID_RE.fullmatch(v)
                  for v in self.field_request_ids),
              'RUNTIME_EVENT_PLAN')


@dataclass(frozen=True)
class FrozenPlan:
    """Exact ordered V4 projection with an explicit synthetic-fixture mode.

    ``expected_sha256`` is supplied separately and recomputed by the runtime.
    Neither that hash nor this Python object grants external launch authority.
    """
    manifest_sha256: str
    review_sha256: str
    window: AbsoluteWindow
    requests: tuple[AttemptRequest, ...]
    review_raw: bytes
    events: tuple[FrozenEvent, ...] = ()
    manifest_raw: bytes | None = None
    validation_repo: Path | None = None
    validation_object_root: Path | None = None
    validation_now_utc: int | None = None
    synthetic_fixture: bool = False
    terminal_precedence: tuple[str, ...] = ('PREREQUISITE', 'CLOCK', 'RESOURCE',
        'DENIAL', 'VALIDATION', 'SUCCESS', 'UNSCHEDULED')
    runtime_context_raw: bytes | None = None

    def __post_init__(self):
        digest(self.manifest_sha256, 'RUNTIME_PLAN_MANIFEST')
        digest(self.review_sha256, 'RUNTIME_PLAN_REVIEW')
        check(type(self.window) is AbsoluteWindow and type(self.requests) is tuple and
              0 < len(self.requests) <= 3600 and
              all(type(r) is AttemptRequest for r in self.requests),
              'RUNTIME_PLAN_SHAPE')
        check(type(self.review_raw) is bytes and 0 < len(self.review_raw) <= 4096 and
              hashlib.sha256(self.review_raw).hexdigest() == self.review_sha256,
              'RUNTIME_PLAN_REVIEW_EVIDENCE')
        check(type(self.synthetic_fixture) is bool and
              type(self.terminal_precedence) is tuple and
              set(self.terminal_precedence) == {'PREREQUISITE', 'CLOCK', 'RESOURCE',
                  'DENIAL', 'VALIDATION', 'SUCCESS', 'UNSCHEDULED'} and
              len(self.terminal_precedence) == 7,
              'RUNTIME_TERMINAL_PRECEDENCE')
        if not self.synthetic_fixture:
            check(type(self.manifest_raw) is bytes and
                  isinstance(self.validation_repo, Path) and
                  isinstance(self.validation_object_root, Path) and
                  type(self.validation_now_utc) in (int, float) and
                  type(self.runtime_context_raw) is bytes and
                  len(self.runtime_context_raw) <= 4096,
                  'RUNTIME_VALIDATED_MANIFEST_REQUIRED')
            self.verify_validated_projection()
        else:
            check(self.manifest_raw is None and self.validation_repo is None and
                  self.validation_object_root is None and
                  self.runtime_context_raw is None,
                  'RUNTIME_SYNTHETIC_FIXTURE_ONLY')
        ids = [r.request_id for r in self.requests]
        check(len(set(ids)) == len(ids), 'RUNTIME_PLAN_REQUEST_REUSE')
        slots = [r.slot_index for r in self.requests
                 if r.purpose == 'FIELD' and r.slot_index is not None]
        check(len(set(slots)) == len(slots), 'RUNTIME_PLAN_SLOT_REUSE')
        check(type(self.events) is tuple and
              all(type(event) is FrozenEvent for event in self.events) and
              len(self.events) <= SLOT_COUNT and
              sum(len(event.field_request_ids) for event in self.events) <= 8192 and
              len(set(event.event_id for event in self.events)) == len(self.events),
              'RUNTIME_EVENT_PLAN')
        requests_by_id = {r.request_id: r for r in self.requests}
        for event in self.events:
            check(all(rid in requests_by_id and
                      requests_by_id[rid].purpose == 'FIELD'
                      for rid in event.field_request_ids),
                  'RUNTIME_EVENT_FIELD_BINDING')
            check(event.primary_provider in
                  {requests_by_id[rid].provider for rid in event.field_request_ids},
                  'RUNTIME_EVENT_PRIMARY')
        review = parse_canonical(self.review_raw)
        review_keys = ('schema_version', 'manifest_sha256', 'window_sha256',
                       'request_schedule_sha256', 'event_schedule_sha256')
        if not self.synthetic_fixture:
            review_keys += ('supplemental_pins_sha256',
                            'terminal_precedence_sha256', 'runtime_context_sha256')
        exact(review, review_keys, 'RUNTIME_PLAN_REVIEW_SCHEMA')
        check(review['schema_version'] == (1 if self.synthetic_fixture else 2) and
              review['manifest_sha256'] == self.manifest_sha256 and
              review['window_sha256'] ==
              hashlib.sha256(canonical(asdict(self.window))).hexdigest() and
              review['request_schedule_sha256'] ==
              hashlib.sha256(canonical([asdict(r) for r in self.requests])).hexdigest() and
              review['event_schedule_sha256'] ==
              hashlib.sha256(canonical([asdict(e) for e in self.events])).hexdigest(),
              'RUNTIME_PLAN_REVIEW_MISMATCH')
        if not self.synthetic_fixture:
            extras = {r.request_id: {'expected_etag': r.expected_etag,
                'expected_object_bytes': r.expected_object_bytes,
                'dependency_commit_hashes': r.dependency_commit_hashes}
                for r in self.requests}
            check(review['supplemental_pins_sha256'] ==
                  hashlib.sha256(canonical(extras)).hexdigest() and
                  review['terminal_precedence_sha256'] ==
                  hashlib.sha256(canonical(self.terminal_precedence)).hexdigest() and
                  review['runtime_context_sha256'] ==
                  hashlib.sha256(self.runtime_context_raw).hexdigest(),
                  'RUNTIME_PLAN_REVIEW_MISMATCH')
        prior_ids = set()
        for request in self.requests:
            check(len(set(request.dependency_commit_hashes)) ==
                  len(request.dependency_commit_hashes) and
                  all(type(h) is str and re.fullmatch(r'[0-9a-f]{64}', h)
                      for h in request.dependency_commit_hashes),
                  'RUNTIME_PLAN_PREREQUISITES')
            check(set(request.prerequisite_request_ids) <= prior_ids,
                  'RUNTIME_PLAN_PREREQUISITE_ORDER')
            prior_ids.add(request.request_id)

    @property
    def sha256(self):
        return hashlib.sha256(canonical({
            'manifest': self.manifest_sha256, 'review': self.review_sha256,
            'window': asdict(self.window),
            'requests': [asdict(r) for r in self.requests],
            'events': [asdict(e) for e in self.events],
            'synthetic_fixture': self.synthetic_fixture,
            'terminal_precedence': self.terminal_precedence,
            'runtime_context_sha256': (None if self.runtime_context_raw is None else
                hashlib.sha256(self.runtime_context_raw).hexdigest())})).hexdigest()

    def verify_validated_projection(self):
        """Revalidate the exact manifest at the runtime boundary as well as
        at factory construction; a caller-built dataclass has no authority.
        """
        check(validate_manifest_v4(self.manifest_raw, repo=self.validation_repo,
            object_root=self.validation_object_root,
            now_utc=self.validation_now_utc) == self.manifest_sha256,
            'RUNTIME_VALIDATED_MANIFEST_MISMATCH')
        payload = parse_canonical(self.manifest_raw)
        times, limits = payload['time'], payload['limits']
        expected_window = AbsoluteWindow(times['window_start_utc'],
            times['last_acquisition_utc'], times['decision_lower_utc'],
            limits['request_deadline_seconds'], limits['max_elapsed_seconds'],
            times['uncertainty_seconds'])
        check(self.window == expected_window and
              len(self.requests) == len(payload['schedule']['requests']),
              'RUNTIME_MANIFEST_WINDOW_OR_SCHEDULE_MISMATCH')
        precedence_ref = payload['accounting']['terminal_precedence']
        precedence_raw = (self.validation_object_root / 'objects' /
                          precedence_ref['sha256']).read_bytes()
        check(hashlib.sha256(precedence_raw).hexdigest() == precedence_ref['sha256'] and
              len(precedence_raw) == precedence_ref['byte_length'] and
              parse_canonical(precedence_raw) == list(self.terminal_precedence),
              'RUNTIME_TERMINAL_PRECEDENCE_EVIDENCE')
        ids = [item['request_id'] for item in payload['schedule']['requests']]
        endpoints = {e['endpoint_id']: e for e in payload['network']['endpoints']}
        for request, item in zip(self.requests, payload['schedule']['requests']):
            endpoint = endpoints[item['endpoint_id']]
            source = payload['sources'][item['provider']]
            check((request.request_id, request.purpose, request.endpoint_id,
                   request.control_domain_id, request.origin, request.path,
                   request.provider, request.slot_index, request.range_start,
                   request.range_end, request.reservation_bytes,
                   request.prerequisite_request_ids) ==
                  (item['request_id'], item['purpose'], item['endpoint_id'],
                   endpoint['control_domain_id'], item['origin'], item['path'],
                   item['provider'] if item['purpose'] == 'FIELD' else None,
                   item['slot_index'], item['range_start'], item['range_end'],
                   item['reservation_bytes'],
                   tuple(ids[i] for i in item['prerequisites'])) and
                  request.source_pin == source['dossier']['sha256'] and
                  request.decoder_pin == source['decoder_build']['sha256'] and
                  request.clock_policy_sha256 ==
                  payload['runtime']['clock_policy']['sha256'] and
                  request.validator_sha256 == endpoint['parser_identity']['sha256'],
                  'RUNTIME_MANIFEST_REQUEST_PROJECTION')
            check(payload['runs_and_slots']['slots'][request.slot_index][0] ==
                  item['provider'], 'RUNTIME_MANIFEST_ORIGINAL_SLOT')
        cohort = payload['cohort']
        check(tuple(event.side for event in self.events) == tuple(cohort['events']),
              'RUNTIME_MANIFEST_EVENT_COHORT')
        field_ids = tuple(request.request_id for request in self.requests
                          if request.purpose == 'FIELD')
        for event, key, trial in zip(self.events, cohort['requested_keys'],
                                      cohort['gate2_trial_keys']):
            check(event.requested_key == tuple(key) and
                  event.gate2_trial_key == tuple(trial) and
                  event.field_request_ids == field_ids,
                  'RUNTIME_MANIFEST_EVENT_KEYS')
        context = parse_canonical(self.runtime_context_raw)
        exact(context, ('manifest_runtime_sha256', 'boot_id', 'shared_root',
            'session_root', 'budget_root', 'store_descriptor', 'report_root',
            'store_policy', 'clock_method', 'allowed_peer_ips'),
            'RUNTIME_CONTEXT_EVIDENCE_SCHEMA')
        check(context['manifest_runtime_sha256'] ==
              hashlib.sha256(canonical(payload['runtime'])).hexdigest() and
              context['store_policy'] == payload['runtime']['policy']['sha256'] and
              all(type(context[key]) is list and len(context[key]) == 2 and
                  all(type(v) is int and v >= 0 for v in context[key])
                  for key in ('shared_root', 'session_root', 'budget_root',
                              'report_root')) and
              type(context['allowed_peer_ips']) is list and
              all(type(value) is str for value in context['allowed_peer_ips']),
              'RUNTIME_CONTEXT_EVIDENCE_VALUE')
        return payload

    @classmethod
    def from_validated_manifest(cls, manifest_raw, *, repo, object_root, now_utc,
                                review_sha256, review_raw, request_pins,
                                runtime_context_raw, events=(), window=None,
                                terminal_precedence=None):
        """The one sanctioned way to build a production ``FrozenPlan``: every
        request is derived from the schedule/endpoint table of a manifest
        that has itself just passed the full, independent
        ``validate_manifest_v4`` review -- never from a caller-asserted
        schedule merely claiming to match some opaque manifest digest (F4).

        Source, decoder, parser and clock pins come from the validated V4
        bytes. ``request_pins`` contains only expected ETag/object size and
        external dependency commits; the review bytes bind that exact
        supplemental map. These pins are evidence references, not source or
        decoder qualification and not launch authority.
        """
        manifest_sha256 = validate_manifest_v4(manifest_raw, repo=repo,
            object_root=object_root, now_utc=now_utc)
        payload = parse_canonical(manifest_raw)
        times, limits = payload['time'], payload['limits']
        projected_window = AbsoluteWindow(times['window_start_utc'],
            times['last_acquisition_utc'], times['decision_lower_utc'],
            limits['request_deadline_seconds'], limits['max_elapsed_seconds'],
            times['uncertainty_seconds'])
        check(window is None or window == projected_window,
              'RUNTIME_MANIFEST_WINDOW_MISMATCH')
        window = projected_window
        precedence = terminal_precedence
        check(type(precedence) is tuple, 'RUNTIME_TERMINAL_PRECEDENCE_REQUIRED')
        precedence_ref = payload['accounting']['terminal_precedence']
        precedence_path = Path(object_root) / 'objects' / precedence_ref['sha256']
        precedence_raw = precedence_path.read_bytes()
        check(hashlib.sha256(precedence_raw).hexdigest() == precedence_ref['sha256'] and
              parse_canonical(precedence_raw) == list(precedence),
              'RUNTIME_TERMINAL_PRECEDENCE_EVIDENCE')
        schedule_requests = payload['schedule']['requests']
        endpoints_by_id = {e['endpoint_id']: e for e in payload['network']['endpoints']}
        ids = [item['request_id'] for item in schedule_requests]
        check(type(request_pins) is dict and set(request_pins) == set(ids),
              'RUNTIME_PLAN_PIN_SET')
        requests = []
        for item in schedule_requests:
            endpoint = endpoints_by_id.get(item['endpoint_id'])
            check(endpoint is not None and endpoint['provider'] == item['provider'] and
                  endpoint['purpose'] == item['purpose'] and
                  endpoint['origin'] == item['origin'], 'RUNTIME_PLAN_ENDPOINT_BINDING')
            pins = request_pins[item['request_id']]
            exact(pins, ('expected_etag', 'expected_object_bytes',
                         'dependency_commit_hashes'), 'RUNTIME_PLAN_PIN_SCHEMA')
            source = payload['sources'][item['provider']]
            prerequisite_request_ids = tuple(ids[i] for i in item['prerequisites'])
            requests.append(AttemptRequest(
                request_id=item['request_id'], purpose=item['purpose'],
                endpoint_id=item['endpoint_id'],
                control_domain_id=endpoint['control_domain_id'],
                origin=item['origin'], path=item['path'],
                provider=item['provider'] if item['purpose'] == 'FIELD' else None,
                slot_index=item['slot_index'],
                range_start=item['range_start'], range_end=item['range_end'],
                reservation_bytes=item['reservation_bytes'],
                expected_etag=pins['expected_etag'],
                expected_object_bytes=pins['expected_object_bytes'],
                source_pin=source['dossier']['sha256'],
                decoder_pin=source['decoder_build']['sha256'],
                clock_policy_sha256=payload['runtime']['clock_policy']['sha256'],
                validator_sha256=endpoint['parser_identity']['sha256'],
                dependency_commit_hashes=pins['dependency_commit_hashes'],
                prerequisite_request_ids=prerequisite_request_ids))
        return cls(manifest_sha256, review_sha256, window, tuple(requests),
                  review_raw, events, manifest_raw, Path(repo), Path(object_root),
                  now_utc, False, precedence, runtime_context_raw)


def _bounded_headers(response, *, max_header_bytes=4096):
    check(type(response) is OfflineResponse and type(response.status) is int and
          type(response.headers) is tuple and len(response.headers) <= 32,
          'RUNTIME_HEADER_SHAPE')
    headers = {}
    size = 0
    for pair in response.headers:
        check(type(pair) is tuple and len(pair) == 2,
              'RUNTIME_HEADER_SHAPE')
        key, value = pair
        check(type(key) is str and type(value) is str and key.isascii() and
              0 < len(key.encode()) <= 64 and len(value.encode()) <= 1024 and
              '\r' not in value and '\n' not in value,
              'RUNTIME_HEADER_SHAPE')
        name = key.lower()
        check(name not in headers, 'RUNTIME_DUPLICATE_HEADER')
        headers[name] = value
        size += len(key.encode()) + len(value.encode()) + 4
    check(size <= max_header_bytes and headers.get('transfer-encoding') is None and
          headers.get('content-encoding', 'identity').lower() == 'identity',
          'RUNTIME_HEADER_SHAPE')
    return headers


def _observable_retry_after(response):
    """Retain an unambiguous bounded restriction even if another header fails."""
    values = []
    seen = 0
    if type(response.headers) is not tuple:
        return {}
    for pair in response.headers:
        if (type(pair) is tuple and len(pair) == 2 and
                type(pair[0]) is str and pair[0].lower() == 'retry-after'):
            seen += 1
            value = pair[1]
            if (type(value) is str and value.isascii() and
                    len(value.encode()) <= 1024 and '\r' not in value and
                    '\n' not in value):
                values.append(value)
    # Multiple values prove the restriction exists, while its expiry cannot
    # be selected honestly. Keep the key and leave expiry unresolved.
    return {'retry-after': values[0] if seen == 1 and len(values) == 1 else None} if seen else {}


def _unresolved_restriction(status, response, origin, cause, header_cause=None):
    raw = None
    if header_cause is None:
        try:
            candidate = canonical({'status': response.status,
                                   'headers': list(response.headers)})
            if 0 < len(candidate) <= 8192:
                raw = candidate
        except (TypeError, ValueError):
            pass
    return {'status': status, 'reason': f'RUNTIME_DENIAL_HTTP_{response.status}',
            'origin': origin,
            'evidence_sha256': None if raw is None else hashlib.sha256(raw).hexdigest(),
            'evidence_raw_b64': None if raw is None else base64.b64encode(raw).decode('ascii'),
            'evidence_missing_cause': header_cause or
                (None if raw is not None else 'RUNTIME_HEADER_EVIDENCE_UNAVAILABLE'),
            'receipt_evidence_cause': cause[:512]}


def _build_denial_record(status_str, response, *, window, receipt_evidence,
                         headers, origin, evidence_missing_cause=None):
    """Build one durable denial record.

    F2: when the headers that would normally support the evidence/retry
    computation could not themselves be trusted (bounded-shape validation
    failed), ``evidence_missing_cause`` records *why* instead of fabricating
    evidence from data already known to be malformed -- the status code
    itself (never header-derived) is still known and still recorded, and
    ``retry_after_seconds`` stays ``None`` (the safest, most conservative
    cooldown: never resume early from an untrustworthy retry value).
    """
    receipt_reading = receipt_evidence.reading
    retry_after_seconds = None
    evidence_sha256 = None
    evidence_raw_b64 = None
    retry_raw = headers.get('retry-after')
    if retry_raw is not None:
        try:
            if retry_raw.isascii() and retry_raw.isdecimal():
                candidate = float(retry_raw)
            else:
                parsed = email.utils.parsedate_to_datetime(retry_raw)
                candidate = (None if parsed.tzinfo is None else max(0.0,
                    parsed.astimezone(datetime.timezone.utc).timestamp() -
                    (receipt_reading.utc_seconds + receipt_reading.uncertainty_seconds)))
            if candidate is not None and math.isfinite(candidate):
                retry_after_seconds = candidate
        except (TypeError, ValueError, OverflowError):
            pass
    if evidence_missing_cause is None:
        evidence_raw = canonical({'status': response.status, 'headers': list(response.headers)})
        evidence_sha256 = hashlib.sha256(evidence_raw).hexdigest()
        evidence_raw_b64 = base64.b64encode(evidence_raw).decode('ascii')
    return {
        'status': status_str,
        'reason': f'RUNTIME_DENIAL_HTTP_{response.status}',
        'origin': origin,
        'evidence_sha256': evidence_sha256,
        'evidence_raw_b64': evidence_raw_b64,
        'evidence_missing_cause': evidence_missing_cause,
        'receipt_clock_sha256': receipt_reading.evidence_sha256,
        'receipt_clock_raw_b64': base64.b64encode(receipt_evidence.raw).decode('ascii'),
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
# Optional pre-dispatch gate onto the independently reviewed offline attempt
# model (tools/v11_gate3_preflight_attempt_model.py). Opt-in only -- see
# ``AttemptModelGuard`` below; nothing here is mandatory for any existing or
# future ``GateRuntime`` caller that omits ``attempt_model``.
# ---------------------------------------------------------------------------

class AttemptModelGuard:
    """Binds one ``GateRuntime`` to the independently reviewed offline Gate 3
    attempt model, for the one bounded single-pilot-request plan that model
    was actually reviewed against. Optional on ``GateRuntime`` -- every
    existing caller that omits it is completely unaffected -- but once
    supplied, ``run_attempt``'s one call for that plan's one request is
    refused before Step 1 (the first durable session/shared mutation) unless
    the model admits. It can only ever add a refusal on top of
    ``run_attempt``'s own existing checks, never remove one: it is consulted
    inside the same pre-Step-1 ``try`` block as the prerequisite/capacity/
    window/control-domain checks, and a refusal there is reported through the
    same ``_session_refuse`` path (durable, exactly-once, no-refund-ambiguity
    accounting unchanged).

    The attempt model's own fixed contract (its ``PHASES``, its
    ``WINDOW_LO``/``WINDOW_HI`` business window, its 3,145,728-byte/
    60-second single stage reservation, and its fingerprint's literal
    ``P1_GEFS_INDEX``/``GET`` binding) represents exactly one bounded pilot
    attempt against one fixed origin/path, not an arbitrary
    ``AttemptRequest`` and not a generic per-request policy engine for
    ``GateRuntime``'s up-to-3,600-request ``FrozenPlan``. Composition
    therefore refuses outright (``GateRuntime.__init__``, F1/F2) unless the
    plan this guard is bound to is itself a synthetic fixture with exactly
    one not-yet-attempted ``INDEX`` request at the model's own reviewed
    origin/path (``tools.v11_gate3_evidence_preflight_checker.FROZEN_REQUEST``);
    a misconfigured guard can never be attached to -- and so can never
    silently consume -- a real multi-request plan row. ``require_admission``
    re-checks that same scope against the exact request it is handed, as
    defense in depth against a future caller that stops routing it through
    ``GateRuntime``.

    Single-shot: ``admit_synthetic`` is consulted at most once per guard
    instance (in-process -- every call after the first refuses outright,
    matching the pilot's documented zero-retry contract), *and* at most once
    per durable session history -- ``require_admission`` also refuses if the
    exact request it is handed already appears in the session ledger's own
    durable ``attempt_history`` (populated by the existing, already-reviewed
    ``refuse``/``accounted``/terminal path, replayed on every reopen; no new
    journal). Because F1 binds this guard to a plan with exactly one request,
    that request's ``request_id`` *is* this pilot's stable admission
    identity, so no separate fingerprint record needs to be invented: a
    ``GateRuntime``/``AttemptModelGuard`` pair reconstructed after a restart
    against the *same* still-durable session directory cannot get a second,
    silently-fresh admission for the same pilot slot, even though the
    in-memory ``_consumed`` flag on the newly-constructed guard starts out
    ``False``.

    Documented residual: this durability is scoped to *session-directory
    reuse*. A reconstruction against a genuinely fresh session directory (no
    durable record of the prior attempt anywhere this guard can see) cannot
    be distinguished from a first attempt by this guard alone -- the
    checkpoint's own ``used_attempts``/``outstanding_attempts`` counters
    remain the ultimate source of truth for whether the real external pilot
    slot was already consumed, and supplying those honestly across any such
    reconstruction remains the caller's responsibility, exactly as it is for
    ``admit_synthetic`` itself.
    """

    def __init__(self, inputs: AttemptSyntheticInputs, checkpoint: AttemptCheckpoint):
        check(type(inputs) is AttemptSyntheticInputs and type(checkpoint) is AttemptCheckpoint,
              'RUNTIME_ATTEMPT_MODEL_GUARD_SHAPE')
        self._inputs = inputs
        self._checkpoint = checkpoint
        self._consumed = False

    def require_admission(self, request: 'AttemptRequest', *, session: SessionLedger) -> None:
        check(type(session) is SessionLedger, 'RUNTIME_ATTEMPT_MODEL_GUARD_SHAPE')
        # F3: durable across a guard/runtime reconstruction that reopens the
        # same session directory -- checked before touching the in-process
        # flag below, and before ever consulting the model.
        durably_consumed = request.request_id in session.attempt_history
        already_consumed, self._consumed = self._consumed, True
        check(not already_consumed and not durably_consumed,
              'RUNTIME_ATTEMPT_MODEL_ALREADY_CONSUMED')
        # F2: bound to the exact reviewed P1_GEFS_INDEX/GET scope, not merely
        # the request's coarse ``purpose`` label.
        check(request.purpose == 'INDEX' and
              request.reservation_bytes <= ATTEMPT_MODEL_BODY_CAP and
              request.origin == _ATTEMPT_MODEL_PILOT_ORIGIN and
              request.path == _ATTEMPT_MODEL_PILOT_PATH,
              'RUNTIME_ATTEMPT_MODEL_SCOPE_MISMATCH')
        try:
            admitted = attempt_model_admit_synthetic(self._inputs, self._checkpoint)
            # F4: only a genuine ``ModelState`` in the one admitting phase
            # counts; a same-shaped-but-wrong-type object (which
            # ``admit_synthetic`` itself can never actually return, but a
            # future refactor of this guard's call site could still pass
            # through here) must never be treated as an admission.
            ok = type(admitted) is AttemptModelState and admitted.phase == 'ADMITTED'
        except ValueError:
            # admit_synthetic raises only for a structurally invalid
            # checkpoint; that is a refusal, not a crash this guard should
            # ever propagate. Any other exception type is a genuine
            # programming-contract violation elsewhere and must still
            # propagate rather than be silently treated as a refusal.
            ok = False
        check(ok, 'RUNTIME_ATTEMPT_MODEL_REFUSED')


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
                 manifest_sha256: str, plan: FrozenPlan,
                 expected_plan_sha256: str, report_sink: 'ReportSink',
                 attempt_model: AttemptModelGuard | None = None,
                 evidence_intake: EvidenceIntakeGuard | None = None):
        check(type(shared) is SharedLedger and type(session) is SessionLedger and
              type(budget) is DurableBudget and type(store) is VersionedImmutableObjectStore,
              'RUNTIME_COMPOSITION_SHAPE')
        check(isinstance(transport, Transport) and isinstance(clock, Clock) and
              isinstance(resources, ResourceProbe), 'RUNTIME_COMPOSITION_SHAPE')
        check(attempt_model is None or type(attempt_model) is AttemptModelGuard,
              'RUNTIME_COMPOSITION_SHAPE')
        check(evidence_intake is None or type(evidence_intake) is EvidenceIntakeGuard,
              'RUNTIME_COMPOSITION_SHAPE')
        check(type(window) is AbsoluteWindow, 'RUNTIME_COMPOSITION_SHAPE')
        check(type(allowed_peer_ips) is tuple and allowed_peer_ips, 'RUNTIME_PEER_IPS')
        digest(manifest_sha256, 'RUNTIME_MANIFEST_DIGEST')
        digest(expected_plan_sha256, 'RUNTIME_EXPECTED_PLAN_DIGEST')
        check(type(plan) is FrozenPlan and plan.sha256 == expected_plan_sha256 and
              plan.manifest_sha256 == manifest_sha256 and plan.window == window,
              'RUNTIME_FROZEN_PLAN_MISMATCH')
        if plan.synthetic_fixture:
            check(isinstance(transport, SyntheticTransport) and
                  isinstance(clock, FakeClock),
                  'RUNTIME_SYNTHETIC_FIXTURE_ONLY')
            self.min_free_disk_bytes = MIN_FREE_DISK_BYTES
            self.min_available_memory_bytes = MIN_AVAILABLE_MEMORY_BYTES
            self.max_header_bytes = 4096
            self.max_clock_age = store.context['max_clock_age']
            self.observation_cutoff_utc = window.decision_lower_utc
        else:
            payload = plan.verify_validated_projection()
            limits = payload['limits']
            self.min_free_disk_bytes = limits['min_free_disk_bytes']
            self.min_available_memory_bytes = limits['min_available_memory_bytes']
            self.max_header_bytes = limits['headers']
            self.max_clock_age = min(store.context['max_clock_age'],
                payload['clocks_and_receipts']['preregistration']
                    ['max_measurement_age_seconds'])
            self.observation_cutoff_utc = min(
                payload['clocks_and_receipts']['observation_schema']['cutoff_utc'],
                window.decision_lower_utc)
            check(budget.max_requests == limits['max_requests'] and
                  budget.max_bytes == limits['max_received_bytes'] and
                  budget.max_elapsed_seconds == limits['max_elapsed_seconds'] and
                  budget.min_start_interval_seconds ==
                  limits['min_start_interval_seconds'] and
                  session.max_requests == shared.max_requests == limits['max_requests'] and
                  limits['report_storage'] == REPORT_RESERVE_BYTES and
                  store.context['policy'] == payload['runtime']['policy']['sha256'] and
                  store.context['max_clock_age'] <= payload['clocks_and_receipts']
                      ['preregistration']['max_measurement_age_seconds'],
                  'RUNTIME_MANIFEST_ACCOUNTING_CONTEXT')
            history_head = payload['runtime']['denial_root']['expected_history_head']
            shared_heads = _journal_event_hashes(shared.events)
            check(shared_heads and shared_heads[-1] == shared.prev and
                  (shared.events[0].get('op') == 'init' if history_head == '0' * 64
                   else history_head in shared_heads) and
                  (len(shared.events) == 1 or
                   shared.expected_history_head == shared.prev),
                  'RUNTIME_MANIFEST_DENIAL_LINEAGE')
        check(type(report_sink) is ReportSink and report_sink.dir_fd is not None and
              report_sink.reserved, 'RUNTIME_REPORT_RESERVE_REQUIRED')
        if not plan.synthetic_fixture:
            expected_context = parse_canonical(plan.runtime_context_raw)
            actual_context = {'manifest_runtime_sha256':
                hashlib.sha256(canonical(payload['runtime'])).hexdigest(),
                'boot_id': session.boot_id,
                'shared_root': list(shared.directory_identity),
                'session_root': list(session.directory_identity),
                'budget_root': list(budget.directory_identity),
                'store_descriptor': store.descriptor_sha256,
                'report_root': list(report_sink.directory_identity),
                'store_policy': store.context['policy'],
                'clock_method': store.context['clock_method'],
                'allowed_peer_ips': list(allowed_peer_ips)}
            check(expected_context == actual_context,
                  'RUNTIME_REVIEWED_CONTEXT_MISMATCH')
        self.shared, self.session, self.budget, self.store = shared, session, budget, store
        self.transport, self.clock, self.resources = transport, clock, resources
        self.attempt_model = attempt_model
        self.evidence_intake = evidence_intake
        self.window = window
        self.allowed_peer_ips = allowed_peer_ips
        self.manifest_sha256 = manifest_sha256
        self.plan = plan
        self.report_sink = report_sink
        observed_ids = [e['request_id'] for e in session.events
                        if e['op'] == 'attempt_intent']
        check(observed_ids == [r.request_id for r in plan.requests[:len(observed_ids)]],
              'RUNTIME_FROZEN_REQUEST_ORDER')
        remaining = plan.requests[len(observed_ids):]
        remaining_ids = {r.request_id for r in remaining}
        remaining_events = tuple(e for e in plan.events
                                 if any(rid in remaining_ids for rid in e.field_request_ids))
        if attempt_model is not None:
            # F1/F2: a guard may only ever be bound to the exact single
            # bounded pilot shape the offline attempt model was reviewed
            # against -- a synthetic-fixture plan with exactly one
            # not-yet-attempted INDEX request at the model's own reviewed
            # P1_GEFS_INDEX/GET origin/path. This is checked against
            # ``remaining`` (not ``plan.requests``), so a runtime
            # reconstructed mid-plan after a partial prior run is judged by
            # what is actually left to attempt, never by the plan's original
            # full shape. Refuses composition outright rather than letting a
            # misconfigured guard silently consume more than the one real
            # plan row it was reviewed for.
            check(plan.synthetic_fixture and len(remaining) == 1 and
                  remaining[0].purpose == 'INDEX' and
                  remaining[0].origin == _ATTEMPT_MODEL_PILOT_ORIGIN and
                  remaining[0].path == _ATTEMPT_MODEL_PILOT_PATH,
                  'RUNTIME_ATTEMPT_MODEL_PLAN_SCOPE')
        self.capacity = CapacityPlan.for_requests(remaining, remaining_events)
        check(manifest_sha256 == session.manifest == budget.manifest ==
              store.context['manifest'], 'RUNTIME_MANIFEST_CONTEXT_MISMATCH')
        # F4: the shared denial root is deliberately not bound to one
        # caller manifest (it may legitimately be reused across jobs), but
        # it must still be the *same boot* as the other three journals this
        # runtime composes -- a shared root opened under a different boot
        # was previously accepted outright.
        check(shared.boot_id == session.boot_id == budget.boot_id == store.boot_id,
              'RUNTIME_BOOT_CONTEXT_MISMATCH')
        check(len(plan.requests) <= min(shared.max_requests, session.max_requests,
              budget.max_requests) and
              sum(r.reservation_bytes for r in plan.requests) <= budget.max_bytes and
              window.elapsed_cap_seconds <= budget.max_elapsed_seconds and
              budget.count + len(remaining) <= budget.max_requests and
              shared.open_count + len(remaining) <= shared.max_requests and
              session.completed_count + len(remaining) <= session.max_requests and
              self.capacity.budget_records + len(budget.events) <= 131072 and
              budget._journal_bytes + self.capacity.budget_records * 65536 <=
              JOURNAL_MAX_BYTES and
              self.capacity.store_events + store._seq <= 10000 and
              os.fstat(store.journal_fd).st_size +
              self.capacity.store_events * 65536 + REPORT_RESERVE_BYTES <=
              STORE_MAX_JOURNAL and
              len(store.receipts) + len(remaining) + self.capacity.aggregate_nodes <= 4096 and
              len(session.events) + SESSION_EVENTS_PER_REQUEST * len(remaining) + 1 <= 32768 and
              session._journal_bytes +
              (SESSION_EVENTS_PER_REQUEST * len(remaining) + 1) * 65536 <= LEDGER_MAX_BYTES and
              len(shared.events) + 4 * len(remaining) <= 32768 and
              shared._journal_bytes + 4 * len(remaining) * 65536 <=
              LEDGER_MAX_BYTES,
              'RUNTIME_FROZEN_CAPACITY')
        context = {'manifest': manifest_sha256, 'window': asdict(window),
                   'plan': expected_plan_sha256, 'review': plan.review_sha256,
                   'budget_min_start_interval': budget.min_start_interval_seconds,
                   'store_policy': store.context['policy'],
                   'clock_method': store.context['clock_method'],
                   'boot_id': session.boot_id,
                   'shared_root': shared.directory_identity,
                   'session_root': session.directory_identity,
                   'budget_root': budget.directory_identity,
                   'store_descriptor': store.descriptor_sha256,
                   'report_root': report_sink.directory_identity,
                   'runtime_context_evidence_sha256':
                   (None if plan.runtime_context_raw is None else
                    hashlib.sha256(plan.runtime_context_raw).hexdigest())}
        self.session.bind_runtime_context(hashlib.sha256(canonical(context)).hexdigest())
        self._check_capacity_resources()

    def _check_capacity_resources(self):
        snapshot = self.resources.snapshot()
        check(type(snapshot) is ResourceSnapshot and
              type(snapshot.free_disk_bytes) is int and
              type(snapshot.available_memory_bytes) is int,
              'RUNTIME_RESOURCE_PROBE_SHAPE')
        check(snapshot.free_disk_bytes - self.capacity.disk_bytes >=
              self.min_free_disk_bytes and
              snapshot.available_memory_bytes - self.capacity.memory_bytes >=
              self.min_available_memory_bytes, 'RUNTIME_PROSPECTIVE_CAPACITY')

    def _check_plan_request(self, request):
        index = len(self.session.request_ids_ever)
        check(index < len(self.plan.requests) and
              self.plan.requests[index] == request,
              'RUNTIME_FROZEN_REQUEST_MISMATCH')

    def _resolve_prerequisites(self, request):
        resolved = list(request.dependency_commit_hashes)
        for dependency in request.dependency_commit_hashes:
            matching = [r for r in self.store.receipts.values()
                        if r.commit_hash == dependency]
            check(len(matching) == 1 and
                  matching[0].provenance.request_id in self.session.attempt_history and
                  self.session.attempt_history[matching[0].provenance.request_id]
                  ['outcome'] == 'SUCCESS', 'RUNTIME_PREREQUISITE_MISSING')
        for prior_id in request.prerequisite_request_ids:
            prior = self.session.attempt_history.get(prior_id)
            capture = self.session.capture_receipts.get(prior_id)
            check(prior is not None and prior['outcome'] == 'SUCCESS' and
                  capture is not None and
                  capture['store_receipt_commit_hash'] is not None,
                  'RUNTIME_PREREQUISITE_MISSING')
            resolved.append(capture['store_receipt_commit_hash'])
        check(len(set(resolved)) == len(resolved), 'RUNTIME_PREREQUISITE_DUPLICATE')
        return tuple(resolved)

    def finalize_report(self):
        """A run completes only after the report bytes and digest are durable."""
        check(self.session.report_completed_sha256 is None,
              'RUNTIME_REPORT_ALREADY_COMPLETED')
        check(self.session.attempt is None or
              self.session.attempt['state'] == 'TERMINAL',
              'RUNTIME_REPORT_ATTEMPT_HELD')
        check(self.budget.in_flight is None and
              not self.session.overdelivery_poisoned,
              'RUNTIME_REPORT_ACCOUNTING_HELD')
        try:
            report = build_terminal_report(plan=self.plan, session=self.session,
                budget=self.budget, shared=self.shared, store=self.store)
            result = self.report_sink.persist(report)
        except (LaunchContractError, OSError) as exc:
            try:
                self.report_sink.persist_incomplete(str(exc)[:1024])
            except (LaunchContractError, OSError):
                pass
            raise
        self.session.report_completed(report_sha256=result['sha256'])
        return result

    def _record_clock(self, evidence):
        """Validate one clock sample and durably intersect it into the
        session-wide offset interval. Every original sample this runtime
        ever takes -- acquisition-window-gated or purely local -- goes
        through this one path (F1: "Persist/check every original session
        sample, including local decode and store seal, against the same
        durable interval"). Returns the checked ``MeasuredClock`` reading.
        """
        check(type(evidence) is ClockEvidence and type(evidence.reading) is MeasuredClock and
              type(evidence.raw) is bytes and 0 < len(evidence.raw) <= 16384 and
              hashlib.sha256(evidence.raw).hexdigest() == evidence.reading.evidence_sha256 and
              evidence.method == self.store.context['clock_method'],
              'RUNTIME_CLOCK_EVIDENCE')
        reading = evidence.reading
        values = (reading.utc_seconds, reading.monotonic_seconds,
                  reading.uncertainty_seconds, reading.measured_monotonic_seconds)
        check(all(type(v) in (int, float) and math.isfinite(v) for v in values) and
              reading.boot_id == self.session.boot_id and
              0 <= reading.measured_monotonic_seconds <= reading.monotonic_seconds and
              reading.monotonic_seconds - reading.measured_monotonic_seconds <=
              self.max_clock_age, 'RUNTIME_CLOCK_MEASUREMENT')
        check(0 <= reading.uncertainty_seconds <= self.window.uncertainty_cap_seconds,
              'RUNTIME_CLOCK_UNCERTAINTY_EXCEEDED')
        offset = reading.utc_seconds - reading.monotonic_seconds
        self.session.observe_clock(monotonic=reading.monotonic_seconds,
            offset_low=offset-reading.uncertainty_seconds,
            offset_high=offset+reading.uncertainty_seconds,
            raw=evidence.raw, evidence_sha256=reading.evidence_sha256)
        return reading

    def _enforce_window(self, evidence, *, body=False):
        """Acquisition-window/elapsed-cap gating, layered on top of
        ``_record_clock``'s clock-validity/intersection check (F1: these are
        kept separate so legitimately late *local* work -- decode, seal --
        can stay a diagnostic-only signal via ``_observe_local_clock`` below,
        without ever skipping the durable intersection itself).
        """
        reading = self._record_clock(evidence)
        lower = reading.utc_seconds - reading.uncertainty_seconds
        upper = reading.utc_seconds + reading.uncertainty_seconds
        check(lower >= self.window.start_utc, 'RUNTIME_CLOCK_BEFORE_WINDOW_START')
        check(upper <= self.window.acquisition_end_utc if body else
              upper < self.window.acquisition_end_utc,
              'RUNTIME_CLOCK_AT_OR_AFTER_ACQUISITION_END')
        if self.session.elapsed_deadline_mono is None:
            self.session.fix_elapsed_deadline(
                reading.monotonic_seconds + self.window.elapsed_cap_seconds)
        check(reading.monotonic_seconds < self.session.elapsed_deadline_mono,
              'RUNTIME_ELAPSED_DEADLINE')
        return reading

    def _observe_local_clock(self, evidence):
        """Record a purely local post-transport sample (decode/seal, or a
        best-effort recovery-path sample) into the same durable session
        intersection as every acquisition-gated sample, without applying the
        acquisition-window/elapsed-deadline gate itself. Local processing
        time legitimately runs past those bounds (the existing ``timely``
        diagnostic in ``run_attempt`` already handles that); it must never
        be a reason to skip intersecting the sample into the durable clock
        constraint the way the ungated store-phase evidence previously was.
        """
        return self._record_clock(evidence)

    def _dispatch_deadline(self, reading):
        offset_hi = self.session.clock_offset_interval[1]
        candidates = [reading.monotonic_seconds + self.window.request_deadline_seconds,
                      self.window.acquisition_end_utc - offset_hi]
        candidates.append(self.session.elapsed_deadline_mono)
        return min(candidates)

    def _capture(self, request, *, clocks=(), raw=None, store_receipt=None,
                 dependencies=None):
        attempt = self.session.attempt_history[request.request_id]
        record = {'version': 1, 'manifest': self.manifest_sha256,
            'plan_sha256': self.plan.sha256,
            'request_sha256': hashlib.sha256(canonical(asdict(request))).hexdigest(),
            'request_id': request.request_id, 'purpose': request.purpose,
            'endpoint_id': request.endpoint_id, 'source_pin': request.source_pin,
            'decoder_pin': request.decoder_pin, 'outcome': attempt['outcome'],
            'session_terminal_head': self.session.prev,
            'shared_head': self.shared.prev, 'budget_head': self.budget.prev,
            'store_receipt_commit_hash': store_receipt,
            'raw_sha256': None if raw is None else hashlib.sha256(raw).hexdigest(),
            'clock_evidence_sha256': [e.reading.evidence_sha256 for e in clocks],
            'dependencies': list(request.dependency_commit_hashes if dependencies is None
                                 else dependencies),
            'prerequisite_request_ids': list(request.prerequisite_request_ids),
            'known_delivered_bytes': attempt.get('total_delivered_bytes', 0),
            'deadline_monotonic': attempt.get('deadline_monotonic'),
            'closure_monotonic': attempt.get('closure_monotonic'),
            'closure_evidence_sha256': attempt.get('closure_evidence_sha256')}
        self.session.capture_receipt(record)

    def _session_refuse(self, request, reason, evidence):
        reasons = [reason]
        def add(value):
            if value not in reasons:
                reasons.append(value)
        try:
            self._resolve_prerequisites(request)
        except LaunchContractError as exc:
            add(str(exc))
        try:
            self._check_capacity_resources()
        except LaunchContractError as exc:
            add(str(exc))
        reading = evidence.reading
        if (type(evidence.raw) is not bytes or not 0 < len(evidence.raw) <= 16384 or
                hashlib.sha256(evidence.raw).hexdigest() != reading.evidence_sha256 or
                evidence.method != self.store.context['clock_method']):
            add('RUNTIME_CLOCK_EVIDENCE')
        values = (reading.utc_seconds, reading.monotonic_seconds,
                  reading.uncertainty_seconds, reading.measured_monotonic_seconds)
        if (not all(type(v) in (int, float) and math.isfinite(v) for v in values) or
                reading.boot_id != self.session.boot_id or
                not 0 <= reading.measured_monotonic_seconds <= reading.monotonic_seconds or
                reading.monotonic_seconds - reading.measured_monotonic_seconds >
                    self.max_clock_age):
            add('RUNTIME_CLOCK_MEASUREMENT')
        if (all(type(v) in (int, float) and math.isfinite(v) for v in values) and
                self.session.clock_offset_interval is not None):
            offset = reading.utc_seconds - reading.monotonic_seconds
            old_low, old_high = self.session.clock_offset_interval
            if (offset + reading.uncertainty_seconds < old_low or
                    offset - reading.uncertainty_seconds > old_high):
                add('SESSION_LEDGER_CLOCK_STEP')
        if (self.session.last_clock_monotonic is not None and
                type(reading.monotonic_seconds) in (int, float) and
                math.isfinite(reading.monotonic_seconds) and
                reading.monotonic_seconds < self.session.last_clock_monotonic):
            add('SESSION_LEDGER_CLOCK_REVERSAL')
        if (type(reading.uncertainty_seconds) in (int, float) and
                math.isfinite(reading.uncertainty_seconds) and
                not 0 <= reading.uncertainty_seconds <= self.window.uncertainty_cap_seconds):
            add('RUNTIME_CLOCK_UNCERTAINTY_EXCEEDED')
        permanent_restriction = self.shared.denials.get(request.control_domain_id)
        if (permanent_restriction is not None and
                permanent_restriction['cooldown_until'] is None):
            add('RUNTIME_CONTROL_DOMAIN_BLOCKED')
        if (type(reading.utc_seconds) in (int, float) and
                type(reading.uncertainty_seconds) in (int, float) and
                math.isfinite(reading.utc_seconds) and
                math.isfinite(reading.uncertainty_seconds)):
            if reading.utc_seconds - reading.uncertainty_seconds < self.window.start_utc:
                add('RUNTIME_CLOCK_BEFORE_WINDOW_START')
            if reading.utc_seconds + reading.uncertainty_seconds >= self.window.acquisition_end_utc:
                add('RUNTIME_CLOCK_AT_OR_AFTER_ACQUISITION_END')
            try:
                if self.shared.is_blocked(request.control_domain_id,
                        now_utc=reading.utc_seconds - reading.uncertainty_seconds):
                    add('RUNTIME_CONTROL_DOMAIN_BLOCKED')
            except LaunchContractError:
                pass
        if (self.session.elapsed_deadline_mono is not None and
                type(reading.monotonic_seconds) in (int, float) and
                math.isfinite(reading.monotonic_seconds) and
                reading.monotonic_seconds >= self.session.elapsed_deadline_mono):
            add('RUNTIME_ELAPSED_DEADLINE')
        precedence = {category: i for i, category in enumerate(self.plan.terminal_precedence)}
        reason = min(reasons, key=lambda value: precedence[_reason_category(value)])
        self.session.attempt_intent(request.request_id, purpose=request.purpose,
            endpoint_id=request.endpoint_id, max_reservation_bytes=request.reservation_bytes,
            range_start=request.range_start, range_end=request.range_end,
            validator_sha256=request.validator_sha256, denial_head=self.shared.prev)
        self.session.refuse(request.request_id, reason=reason, reasons=reasons)
        self._capture(request, clocks=(evidence,))
        return {'request_id': request.request_id, 'outcome': 'REFUSED',
                'reason': reason, 'reasons': reasons}

    def _account_prefetched_on_deadline(self, request, stream, *, receipt=None,
                                        denial_recorded=False):
        """Charge eager bytes already returned by an expired dispatch, and
        record any observable denial -- even when header or clock validation
        itself fails (F2: "error paths discard observed denial and already
        delivered body bytes"). This path never reads or closes the stream
        and never releases the reservation; it must never itself raise for a
        reason that would abandon that accounting (any genuine shape defect
        in an already-delivered chunk is the one exception: that is a
        transport-fixture contract violation, not a recoverable observation
        gap).

        Header-shape failure degrades to an explicit missing-evidence denial
        cause (never silently dropped, never fabricated from untrusted
        bytes); a clock sample that fails the acquisition-window/elapsed-cap
        gate is still durably intersected into the session clock constraint
        via ``_observe_local_clock`` and still used as receipt evidence --
        only the *gate* is inapplicable here, since we are already on an
        error/recovery path by construction.
        """
        response = stream.response
        try:
            headers = _bounded_headers(response, max_header_bytes=self.max_header_bytes)
            evidence_missing_cause = None
        except LaunchContractError as exc:
            headers = _observable_retry_after(response)
            evidence_missing_cause = f'RUNTIME_HEADER_VALIDATION_FAILED:{exc}'
        denial_status = (_DENIAL_STATUSES.get(response.status)
                         if type(response.status) is int else None)
        if denial_status is None and 'retry-after' in headers:
            denial_status = 'OTHER'
        try:
            clock_cause = None
            if receipt is None:
                try:
                    receipt = self.clock.evidence('body_receipt')
                # J1: recovery itself can hit the same ordinary clock-source
                # failure that brought us onto this path (any ordinary
                # exception -- the same boundary ``_postdispatch_monotonic``
                # uses, not a finite enumerated list); it must degrade to an
                # explicit unresolved cause here too, not propagate and skip
                # restriction preservation below.
                except Exception as exc:
                    clock_cause = f'RUNTIME_RECEIPT_CLOCK_UNAVAILABLE:{exc}'
            if receipt is not None:
                try:
                    self._observe_local_clock(receipt)
                except (LaunchContractError, OSError) as exc:
                    clock_cause = f'RUNTIME_RECEIPT_CLOCK_INVALID:{exc}'
            if denial_status is not None and not denial_recorded and not self.shared.open_intent['denial_recorded']:
                if clock_cause is None:
                    try:
                        record = _build_denial_record(denial_status, response,
                            window=self.window, receipt_evidence=receipt,
                            headers=headers, origin=request.origin,
                            evidence_missing_cause=evidence_missing_cause)
                        self.shared.denial_observed(request.request_id, denial=record)
                    except (LaunchContractError, OSError, ValueError) as exc:
                        clock_cause = f'RUNTIME_DENIAL_EVIDENCE_OR_WRITE_FAILED:{exc}'
                if clock_cause is not None and not self.shared.open_intent['denial_recorded']:
                    self.shared.restriction_unresolved(request.request_id,
                        restriction=_unresolved_restriction(denial_status, response,
                            request.origin, clock_cause, evidence_missing_cause))
                self.session.denial(request.request_id,
                    reason=f'RUNTIME_DENIAL_HTTP_{response.status}',
                    shared_denial_event_hash=self.shared.prev)
        finally:
            remaining = response.chunks[stream.index:]
            check(all(type(chunk) is bytes and chunk for chunk in remaining),
                  'RUNTIME_PREFETCH_SHAPE')
            known_bytes = sum(map(len, remaining))
            if known_bytes:
                # One durable aggregate record bounds recovery even when an
                # eager fixture supplies more than 32 tiny chunks.
                self.budget.record_eager_delivery(request.request_id, known_bytes)

    def _postdispatch_monotonic(self, request, stream, *, receipt=None,
                                denial_recorded=False):
        """Preserve already delivered observations if the local clock fails."""
        try:
            value = self.clock.monotonic()
            check(type(value) in (int, float) and math.isfinite(value),
                  'RUNTIME_MONOTONIC_UNUSABLE')
            return value
        except Exception:
            self._account_prefetched_on_deadline(request, stream, receipt=receipt,
                                                denial_recorded=denial_recorded)
            raise

    def run_attempt(self, request: AttemptRequest) -> dict:
        check(type(request) is AttemptRequest, 'RUNTIME_REQUEST_SHAPE')
        check(self.session.report_completed_sha256 is None,
              'RUNTIME_REPORT_ALREADY_COMPLETED')
        self._check_plan_request(request)

        pre = self.clock.evidence('request_start')
        try:
            if self.evidence_intake is not None:
                self.evidence_intake.require_admission()
            resolved_dependencies = self._resolve_prerequisites(request)
            self._check_capacity_resources()
            self._enforce_window(pre)
            # F3: a cooldown has only provably expired once the *lower*
            # (conservative) bound of our own current-time uncertainty has
            # reached it; using the upper bound here would resume dispatch
            # while the true current time could still be within the hold.
            check(not self.shared.is_blocked(request.control_domain_id,
                  now_utc=pre.reading.utc_seconds - pre.reading.uncertainty_seconds),
                  'RUNTIME_CONTROL_DOMAIN_BLOCKED')
            if self.attempt_model is not None:
                self.attempt_model.require_admission(request, session=self.session)
        except LaunchContractError as exc:
            return self._session_refuse(request, str(exc), pre)

        # Step 1 (design section 4): durable opens, no transport yet.
        self.session.attempt_intent(request.request_id, purpose=request.purpose,
            endpoint_id=request.endpoint_id, max_reservation_bytes=request.reservation_bytes,
            range_start=request.range_start, range_end=request.range_end,
            validator_sha256=request.validator_sha256, denial_head=self.shared.prev)
        self.shared.intent_open(request.request_id, purpose=request.purpose,
            endpoint_id=request.endpoint_id, control_domain_id=request.control_domain_id,
            manifest_sha256=self.manifest_sha256, max_reservation_bytes=request.reservation_bytes,
            now_utc=pre.reading.utc_seconds - pre.reading.uncertainty_seconds)

        # Step 2: reservation persists before transport.
        self.budget.reserve(request.request_id, request.reservation_bytes,
                             started_monotonic=self.clock.monotonic())
        self.session.budget_reserved(request.request_id, reserve_event_hash=self.budget.prev)

        # Step 3: recheck immediately before dispatch; only then call transport.
        dispatch = self.clock.evidence('request_start')
        self._enforce_window(dispatch)
        check(not self.shared.is_blocked(request.control_domain_id,
              now_utc=dispatch.reading.utc_seconds - dispatch.reading.uncertainty_seconds),
              'RUNTIME_CONTROL_DOMAIN_BLOCKED')
        deadline_mono = self._dispatch_deadline(dispatch.reading)
        check(deadline_mono > dispatch.reading.monotonic_seconds,
              'RUNTIME_DEADLINE_ALREADY_PASSED')
        if self.session.last_closure_monotonic is not None:
            check(dispatch.reading.monotonic_seconds >=
                  self.session.last_closure_monotonic +
                  self.budget.min_start_interval_seconds,
                  'RUNTIME_POST_CLOSE_PACING')
        self.session.fix_request_deadline(request.request_id, deadline_mono)
        self.session.dispatch_intent(request.request_id,
            measured_start_monotonic=dispatch.reading.monotonic_seconds)
        # Persistence can itself spend the allowance. Sample again after the
        # DISPATCH_INTENT fsync, immediately before the injected boundary.
        after_intent = self.clock.evidence('request_start')
        self._enforce_window(after_intent)
        actual_start = self.clock.monotonic()
        check(type(actual_start) in (int, float) and math.isfinite(actual_start) and
              actual_start >= after_intent.reading.monotonic_seconds,
              'RUNTIME_DISPATCH_CLOCK_REVERSAL')
        check(self.session.attempt['deadline_monotonic'] == deadline_mono and
              actual_start < deadline_mono,
              'RUNTIME_DEADLINE_ALREADY_PASSED')
        if self.session.last_closure_monotonic is not None:
            check(actual_start >=
                  self.session.last_closure_monotonic +
                  self.budget.min_start_interval_seconds,
                  'RUNTIME_POST_CLOSE_PACING')
        stream = self.transport.dispatch(request,
            deadline_monotonic=deadline_mono,
            remaining_seconds=deadline_mono-actual_start)
        check(type(stream) is SyntheticResponseStream,
              'RUNTIME_TRANSPORT_STREAM_REQUIRED')
        response = stream.response
        # F7: the eager synthetic boundary never capped how many chunks a
        # fixture could return; enforce the same fixed worst-case chunk
        # count the prospective ``CapacityPlan`` preflight assumed, so that
        # assumption is actually true rather than merely hoped.
        if len(response.chunks) > MAX_BODY_CHUNKS_PER_REQUEST:
            self._account_prefetched_on_deadline(request, stream)
            raise LaunchContractError('RUNTIME_CHUNK_COUNT_EXCEEDS_POLICY')
        if self._postdispatch_monotonic(request, stream) >= deadline_mono:
            self._account_prefetched_on_deadline(request, stream)
            raise LaunchContractError('RUNTIME_DISPATCH_DEADLINE')
        try:
            headers = _bounded_headers(response, max_header_bytes=self.max_header_bytes)
        except LaunchContractError:
            # F2: a header-shape failure must not itself discard the known
            # denial/already-delivered-bytes observations -- account them
            # (with an explicit missing-evidence cause, since the headers
            # cannot be trusted) before re-raising the original failure.
            self._account_prefetched_on_deadline(request, stream)
            raise
        try:
            header_receipt = self.clock.evidence('body_receipt')
        except Exception:
            # J1: the same clock source can fail here as it did at the first
            # post-dispatch monotonic sample; recovery must preserve known
            # bytes/restriction for any ordinary exception -- the same
            # boundary ``_postdispatch_monotonic`` uses -- not only the
            # previously enumerated LaunchContractError/OSError/RuntimeError.
            self._account_prefetched_on_deadline(request, stream)
            raise
        try:
            self._enforce_window(header_receipt, body=True)
        except LaunchContractError:
            # F2: any clock-validity/window failure here -- not only the
            # acquisition-end case -- must still account known bytes/denial.
            self._account_prefetched_on_deadline(request, stream,
                                                receipt=header_receipt)
            raise
        if (header_receipt.reading.monotonic_seconds > deadline_mono or
                self._postdispatch_monotonic(request, stream,
                    receipt=header_receipt) >= deadline_mono):
            self._account_prefetched_on_deadline(request, stream,
                                                receipt=header_receipt)
            raise LaunchContractError('RUNTIME_HEADER_DEADLINE')

        # Step 4: denial is classified and persisted *before* any chunk is
        # charged or parsed -- "record denials first".
        denial_status = _DENIAL_STATUSES.get(response.status)
        if denial_status is None and 'retry-after' in headers:
            denial_status = 'OTHER'
        denial_record = None
        if denial_status is not None:
            try:
                denial_record = _build_denial_record(denial_status, response,
                    window=self.window, receipt_evidence=header_receipt,
                    headers=headers, origin=request.origin)
                self.shared.denial_observed(request.request_id, denial=denial_record)
                self.session.denial(request.request_id, reason=denial_record['reason'],
                                     shared_denial_event_hash=self.shared.prev)
            except (LaunchContractError, OSError, ValueError):
                self._account_prefetched_on_deadline(request, stream,
                    receipt=header_receipt)
                raise
            retry_raw = headers.get('retry-after')
            if (retry_raw is not None and retry_raw.isascii() and
                    retry_raw.isdecimal() and
                    denial_record['retry_after_seconds'] is None):
                self._account_prefetched_on_deadline(request, stream,
                    receipt=header_receipt, denial_recorded=True)
                raise LaunchContractError('RUNTIME_RETRY_AFTER_UNREPRESENTABLE')

        delivered = 0
        overdelivered = False
        chunk = b''
        try:
            while stream.index < len(response.chunks):
                allowance = self.budget.next_read_limit(65536)
                if allowance == 0:
                    # The eager synthetic boundary has already delivered the
                    # remaining tuple. Charge it as one violation; do not
                    # make another transport read merely to discover EOF.
                    chunk = response.chunks[stream.index]
                    stream.index = len(response.chunks)
                    self.budget.consume(request.request_id, chunk,
                        overdelivery_total_bytes=stream.prefetched_bytes-delivered,
                        permitted_bytes=0)
                    raise LaunchContractError('RUNTIME_NO_READ_ALLOWANCE')
                remaining = deadline_mono-self._postdispatch_monotonic(
                    request, stream, receipt=header_receipt,
                    denial_recorded=denial_record is not None)
                if remaining <= 0:
                    self._account_prefetched_on_deadline(request, stream,
                        receipt=header_receipt, denial_recorded=denial_record is not None)
                    raise LaunchContractError('RUNTIME_BODY_DEADLINE')
                chunk = stream.read(maximum_bytes=allowance,
                                    remaining_seconds=remaining)
                self.budget.consume(request.request_id, chunk,
                    overdelivery_total_bytes=stream.prefetched_bytes-delivered,
                    permitted_bytes=allowance)
                delivered += len(chunk)
        except LaunchContractError as exc:
            if str(exc) != 'STREAM_ABORT_AT_ALLOWANCE':
                raise
            overdelivered = True
            delivered = stream.prefetched_bytes
            stream.index = len(response.chunks)

        check(self._postdispatch_monotonic(request, stream,
            receipt=header_receipt, denial_recorded=denial_record is not None) <
            deadline_mono, 'RUNTIME_BODY_DEADLINE')
        known_closed = stream.close(remaining_seconds=deadline_mono-
            self._postdispatch_monotonic(request, stream,
                receipt=header_receipt, denial_recorded=denial_record is not None))
        closure = self.clock.evidence('body_receipt')
        self._enforce_window(closure, body=True)
        check(closure.reading.monotonic_seconds <= deadline_mono,
              'RUNTIME_CLOSE_DEADLINE')
        check(known_closed, 'RUNTIME_CLOSURE_UNPROVEN')
        closure_evidence_raw = canonical({
            'adapter': 'SyntheticResponseStream', 'known_closed': True,
            'status': response.status, 'headers': list(response.headers),
            'prefetched_bytes': stream.prefetched_bytes,
            'delivered_bytes': delivered, 'chunks_consumed': stream.index,
            'chunk_count': len(response.chunks),
            'deadline_monotonic': deadline_mono,
            'closure_clock_sha256': closure.reading.evidence_sha256})
        check(len(closure_evidence_raw) <= 8192, 'RUNTIME_CLOSURE_EVIDENCE_BOUND')

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
            total_delivered_bytes=delivered,
            closure_monotonic=closure.reading.monotonic_seconds,
            closure_evidence_raw=closure_evidence_raw,
            denial_history_head=denial_head_snapshot,
            accounting_head=self.budget.prev)
        shared_outcome = ('DENIED' if denial_record is not None else
                          ('OK' if verified_body is not None else 'FAILED'))
        self.shared.intent_closed(request.request_id, outcome=shared_outcome,
            accounting_head=self.budget.prev, total_delivered_bytes=delivered)

        if overdelivered or self.session.attempt['overdelivered']:
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
            self._capture(request, clocks=(dispatch, header_receipt, closure),
                          raw=b''.join(response.chunks),
                          dependencies=resolved_dependencies)
            return {'request_id': request.request_id, 'outcome': 'FAILED', 'reason': reason}

        # Step 7: successful, verified body -- resource floor again before
        # this one-field decode/seal batch, then seal as a RAW object.
        self._check_capacity_resources()
        provenance = ObjectProvenance('RAW', request.request_id, request.request_id,
            request.source_pin, request.decoder_pin, request.clock_policy_sha256,
            ())
        # F1: these local decode/seal phases are the store-phase samples the
        # durable session clock intersection previously never saw -- record
        # each one (diagnostic-only gating, same as ``timely`` below) before
        # it is handed to the store, so a later attempt's acquisition-gated
        # evidence cannot silently contradict them.
        body_receipt = self.clock.evidence('body_receipt')
        self._observe_local_clock(body_receipt)
        decode_complete = self.clock.evidence('decode_complete')
        self._observe_local_clock(decode_complete)
        prefix = (dispatch, body_receipt, decode_complete)

        def _record_seal_evidence():
            evidence = self.clock.evidence('durable_seal')
            self._observe_local_clock(evidence)
            return evidence

        receipt = self.store.seal_with_provenance(verified_body, provenance, prefix,
            _record_seal_evidence)
        self.session.object_witnessed(request.request_id,
            store_receipt_commit_hash=receipt.commit_hash)

        timely = True
        try:
            sequence = ClockSequence(boot_id=dispatch.reading.boot_id,
                max_measurement_age_seconds=self.max_clock_age)
            for evidence in receipt.clocks:
                sequence.record(evidence.phase, evidence.reading)
            timely = sequence.causal_before(self.observation_cutoff_utc)
        except LaunchContractError:
            # Diagnostic recomputation only: the object is already sealed and
            # witnessed above either way. A late/uncertain completion stays
            # diagnostic, never relabeled, never retried.
            timely = False

        reason = 'RUNTIME_SUCCESS_TIMELY' if timely else 'RUNTIME_SUCCESS_LATE_DIAGNOSTIC_ONLY'
        self.session.terminal(request.request_id, outcome='SUCCESS', reason=reason,
            report_reserved_bytes=self.session.report_reserve_bytes)
        self._capture(request, clocks=receipt.clocks, raw=verified_body,
                      store_receipt=receipt.commit_hash,
                      dependencies=resolved_dependencies)
        return {'request_id': request.request_id, 'outcome': 'SUCCESS', 'reason': reason,
                'store_receipt_commit_hash': receipt.commit_hash, 'timely': timely}


# ---------------------------------------------------------------------------
# Bounded terminal report: full 2,713-row denominator (design section 6).
# ---------------------------------------------------------------------------

def _journal_event_hashes(events):
    previous = '0' * 64
    result = []
    for seq, event in enumerate(events):
        previous = hashlib.sha256(canonical({
            'seq': seq, 'prev': previous, 'event': event})).hexdigest()
        result.append(previous)
    return result


def _reason_category(reason):
    if 'PREREQUISITE' in reason:
        return 'PREREQUISITE'
    if 'CLOCK' in reason or 'WINDOW' in reason or 'DEADLINE' in reason:
        return 'CLOCK'
    if 'RESOURCE' in reason or 'CAPACITY' in reason:
        return 'RESOURCE'
    if 'DENIAL' in reason or 'CONTROL_DOMAIN' in reason:
        return 'DENIAL'
    if 'SUCCESS' in reason:
        return 'SUCCESS'
    if 'SCHEDULE' in reason or 'SLOT_NOT' in reason:
        return 'UNSCHEDULED'
    return 'VALIDATION'


def build_terminal_report(*, plan: FrozenPlan, session: SessionLedger,
                          budget: DurableBudget, shared: SharedLedger,
                          store: VersionedImmutableObjectStore) -> dict:
    """Compose the fixed denominator solely from the pinned plan and journals."""
    check(type(plan) is FrozenPlan and type(session) is SessionLedger and
          type(budget) is DurableBudget and type(shared) is SharedLedger and
          type(store) is VersionedImmutableObjectStore,
          'REPORT_COMPOSITION_SHAPE')
    check(plan.manifest_sha256 == session.manifest == budget.manifest ==
          store.context['manifest'], 'REPORT_CONTEXT_MISMATCH')
    check((session.attempt is None or session.attempt['state'] == 'TERMINAL') and
          budget.in_flight is None and not budget.violated and
          not session.overdelivery_poisoned,
          'REPORT_UNSETTLED_ATTEMPT')
    planned = {r.request_id: r for r in plan.requests}
    request_order = {r.request_id: i for i, r in enumerate(plan.requests)}
    seen_ids = [e['request_id'] for e in session.events if e['op'] == 'attempt_intent']
    check(seen_ids == [r.request_id for r in plan.requests[:len(seen_ids)]],
          'REPORT_REQUEST_ORDER')
    check(set(session.attempt_history) <= set(planned) and
          set(budget.attempts) <= set(planned), 'REPORT_UNPLANNED_ATTEMPT')
    reserved_ids = [r.request_id for r in plan.requests if r.request_id in budget.attempts]
    refused_ids = [r.request_id for r in plan.requests if
                   session.attempt_history.get(r.request_id, {}).get('outcome') == 'REFUSED']
    check(set(reserved_ids).isdisjoint(refused_ids), 'REPORT_ACCOUNTING_MISMATCH')
    receipts_by_commit = {r.commit_hash: r for r in store.receipts.values()}
    session_hashes = _journal_event_hashes(session.events)
    shared_hashes = _journal_event_hashes(shared.events)
    budget_hashes = _journal_event_hashes(budget.events)
    check(session_hashes[-1] == session.prev and
          shared_hashes[-1] == shared.prev and
          budget_hashes[-1] == budget.prev,
          'REPORT_JOURNAL_HEAD_MISMATCH')
    terminal_heads = {e['request_id']: h for e, h in
        zip(session.events, session_hashes)
        if e['op'] in ('terminal', 'refuse')}
    shared_close_heads = {e['request_id']: h for e, h in
        zip(shared.events, shared_hashes) if e['op'] == 'intent_closed'}
    budget_complete_heads = {e['key']: h for e, h in
        zip(budget.events, budget_hashes) if e['op'] == 'complete'}
    for rid, attempt in session.attempt_history.items():
        request = planned[rid]
        capture = session.capture_receipts.get(rid)
        resolved_deps = list(request.dependency_commit_hashes)
        if 'RUNTIME_PREREQUISITE_MISSING' not in attempt.get('reasons',
                                                             [attempt['reason']]):
            for prior_id in request.prerequisite_request_ids:
                prior_capture = session.capture_receipts.get(prior_id)
                check(prior_capture is not None and
                      prior_capture['store_receipt_commit_hash'] is not None and
                      request_order[prior_id] < request_order[rid],
                      'REPORT_DEPENDENCY_GRAPH')
                resolved_deps.append(prior_capture['store_receipt_commit_hash'])
        check(capture is not None and capture['plan_sha256'] == plan.sha256 and
              capture['request_sha256'] ==
              hashlib.sha256(canonical(asdict(request))).hexdigest() and
              capture['purpose'] == request.purpose and
              capture['endpoint_id'] == request.endpoint_id and
              capture['dependencies'] == resolved_deps and
              capture['prerequisite_request_ids'] ==
              list(request.prerequisite_request_ids) and
              capture['outcome'] == attempt['outcome'] and
              capture['known_delivered_bytes'] == attempt.get('total_delivered_bytes', 0) and
              capture['deadline_monotonic'] == attempt.get('deadline_monotonic') and
              capture['closure_evidence_sha256'] ==
              attempt.get('closure_evidence_sha256') and
              capture['session_terminal_head'] == terminal_heads.get(rid) and
              capture['shared_head'] in shared_hashes and
              capture['budget_head'] in budget_hashes,
              'REPORT_CAPTURE_MISMATCH')
        budget_attempt = budget.attempts.get(rid)
        check((attempt['outcome'] == 'REFUSED') == (budget_attempt is None),
              'REPORT_ACCOUNTING_MISMATCH')
        if budget_attempt is not None:
            check(budget_attempt['reserved'] == request.reservation_bytes and
                  budget_attempt['finished'] and
                  budget_attempt['received'] == capture['known_delivered_bytes'] and
                  capture['budget_head'] == budget_complete_heads.get(rid) and
                  capture['shared_head'] == shared_close_heads.get(rid),
                  'REPORT_ACCOUNTING_MISMATCH')
        if attempt['outcome'] == 'SUCCESS':
            receipt = receipts_by_commit.get(capture['store_receipt_commit_hash'])
            check(receipt is not None and receipt.provenance.request_id == rid and
                  receipt.provenance.source_pin == request.source_pin and
                  receipt.provenance.decoder_pin == request.decoder_pin and
                  receipt.provenance.kind == 'RAW' and
                  receipt.provenance.dependencies == () and
                  capture['raw_sha256'] == receipt.object_sha256 and
                  capture['clock_evidence_sha256'] ==
                  [e.reading.evidence_sha256 for e in receipt.clocks],
                  'REPORT_STORE_RECEIPT_MISMATCH')
            for dependency in resolved_deps:
                prior = receipts_by_commit.get(dependency)
                check(prior is not None and
                      prior.provenance.request_id in session.attempt_history and
                      session.attempt_history[prior.provenance.request_id]
                      ['outcome'] == 'SUCCESS' and
                      request_order[prior.provenance.request_id] < request_order[rid],
                      'REPORT_DEPENDENCY_GRAPH')
        else:
            check(capture['store_receipt_commit_hash'] is None,
                  'REPORT_STORE_RECEIPT_MISMATCH')
    # F5: every FIELD request must map exactly to its own frozen original
    # slot -- ``AttemptRequest`` already requires a FIELD request to carry a
    # non-``None`` slot and ``FrozenPlan`` already forbids two FIELD requests
    # sharing one slot, so this is a durable re-proof of that mapping against
    # the actual plan the report is built from, not a new rule.
    all_field_requests = [r for r in plan.requests if r.purpose == 'FIELD']
    by_slot = {r.slot_index: r for r in all_field_requests}
    check(all(r.slot_index is not None for r in all_field_requests) and
          len(by_slot) == len(all_field_requests), 'REPORT_FIELD_SLOT_COVERAGE')
    rows, counts = [], {}
    for slot in range(SLOT_COUNT):
        request = by_slot.get(slot)
        attempt = None if request is None else session.attempt_history.get(request.request_id)
        if request is None:
            status, outcome = 'NEVER_ATTEMPTED', None
            reasons = ['RUNTIME_SLOT_NOT_IN_SCHEDULE']
        elif attempt is None:
            failed_dependency = any(session.attempt_history.get(prior_id, {}).get(
                'outcome') != 'SUCCESS' for prior_id in request.prerequisite_request_ids)
            status, outcome = 'NEVER_ATTEMPTED', None
            reasons = (['RUNTIME_FAILED_PREREQUISITE',
                        'RUNTIME_SCHEDULED_NOT_ATTEMPTED'] if failed_dependency else
                       ['RUNTIME_SCHEDULED_NOT_ATTEMPTED'])
        else:
            outcome = attempt['outcome']
            reasons = list(dict.fromkeys(attempt.get('reasons', [attempt['reason']])))
            status = 'REFUSED' if outcome == 'REFUSED' else 'ATTEMPTED'
        precedence = {category: i for i, category in enumerate(plan.terminal_precedence)}
        reason = min(reasons, key=lambda value: (precedence[_reason_category(value)],
                                                  reasons.index(value)))
        row = {'slot_index': slot, 'status': status, 'outcome': outcome,
               'reason': reason, 'reasons': reasons,
               'request_id': None if request is None else request.request_id,
               'provider': None if request is None else request.provider}
        rows.append(row)
        label = outcome or status
        counts[label] = counts.get(label, 0) + 1
    per_purpose = {}
    observed = {rid: 0 for rid in budget.attempts}
    for event in budget.events:
        if event['op'] in ('chunk', 'eager_delivery', 'violation'):
            observed[event['key']] += event['bytes']
    for purpose in PURPOSES:
        requests = [r for r in plan.requests if r.purpose == purpose]
        # F5: "attempted" means an actual durable budget reservation was
        # made for this request -- a request the session refused before
        # ever reserving (control-domain cooldown, elapsed deadline, a
        # missing prerequisite) was never attempted against the provider,
        # and must not inflate this count merely for having an
        # ``attempt_intent``/``refuse`` pair in the session journal.
        attempts = [r for r in requests if r.request_id in budget.attempts]
        refused = [r for r in requests if session.attempt_history.get(
            r.request_id, {}).get('outcome') == 'REFUSED']
        known = sum(observed[r.request_id] for r in attempts)
        charged = sum((a['received'] if a['finished'] else a['reserved'])
                      for r in attempts if (a := budget.attempts.get(r.request_id)))
        per_purpose[purpose] = {'planned_count': len(requests),
            'planned_reservation_bytes': sum(r.reservation_bytes for r in requests),
            'attempted_count': len(attempts), 'refused_count': len(refused),
            'completed_count': sum(bool(budget.attempts[r.request_id]['finished'])
                                   for r in attempts if r.request_id in budget.attempts),
            'known_delivered_bytes': known, 'charged_bytes': charged,
            'outstanding_reserved_ceiling': sum(a['reserved'] for r in attempts
                 if (a := budget.attempts.get(r.request_id)) and not a['finished'])}
    check(sum(x['known_delivered_bytes'] for x in per_purpose.values()) == budget.received,
          'REPORT_BUDGET_RECONCILIATION')
    providers = {provider: {'planned': 0, 'success': 0, 'failed': 0}
                 for provider in FIELD_LIMITS}
    for r in plan.requests:
        if r.provider is not None:
            trajectory = providers[r.provider]
            trajectory['planned'] += 1
            outcome = session.attempt_history.get(r.request_id, {}).get('outcome')
            trajectory['success'] += outcome == 'SUCCESS'
            trajectory['failed'] += outcome in ('FAILED', 'REFUSED')
    event_rows = []
    all_provider_ids = []
    for event in plan.events:
        field_requests = [planned[rid] for rid in event.field_request_ids]
        outcomes = {r.request_id: session.attempt_history.get(r.request_id, {}).get(
            'outcome', 'NEVER_ATTEMPTED') for r in field_requests}
        complete = all(value == 'SUCCESS' for value in outcomes.values())
        providers_present = {r.provider for r in field_requests}
        all_provider = providers_present == set(FIELD_LIMITS)
        if complete and all_provider:
            all_provider_ids.append(event.event_id)
        event_rows.append({'event_id': event.event_id, 'side': event.side,
            'requested_key': event.requested_key,
            'gate2_trial_key': event.gate2_trial_key,
            'primary_provider': event.primary_provider,
            'primary_outcomes': {r.request_id: outcomes[r.request_id]
                for r in field_requests if r.provider == event.primary_provider},
            'field_outcomes': outcomes,
            'classification': 'COMPLETE' if complete else 'FAILED_PREREQUISITE',
            'all_providers_planned': all_provider})
    return {'schema_version': 2, 'manifest_sha256': plan.manifest_sha256,
            'plan_sha256': plan.sha256, 'review_sha256': plan.review_sha256,
            'slot_count': SLOT_COUNT, 'rows': rows, 'outcome_counts': counts,
            'terminal_precedence': list(plan.terminal_precedence),
            'attempted_request_ids': reserved_ids,
            'refused_request_ids': refused_ids,
            'raw_completed_count': sum(r.purpose == 'FIELD' and
                session.attempt_history.get(r.request_id, {}).get('outcome') == 'SUCCESS'
                for r in plan.requests),
            'per_purpose': per_purpose,
            'global_accounting': {
                'known_delivered_bytes': budget.received,
                'charged_bytes': sum(p['charged_bytes'] for p in per_purpose.values()),
                'outstanding_reserved_ceiling': sum(
                    p['outstanding_reserved_ceiling'] for p in per_purpose.values()),
                # F5: derive from actual budget reservations, not every
                # session-journal intent (which also counts refusals that
                # never reserved anything).
                'attempted_count': sum(p['attempted_count'] for p in per_purpose.values()),
                'refused_count': sum(p['refused_count'] for p in per_purpose.values())},
            'denial_lineage': {'shared_head': shared.prev,
                               'control_domains': shared.denials},
            'provider_trajectories': providers,
            'requested_events': event_rows,
            'all_provider_intersection': {
                'complete_event_ids': all_provider_ids,
                'planned_event_count': len(event_rows)}}


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
    RESERVE_FILE_NAME = 'gate3_terminal_report.reserve'

    def __init__(self, directory):
        path = Path(directory)
        check(path.is_absolute() and path == path.resolve(), 'REPORT_DIRECTORY')
        for ancestor in (path, *path.parents):
            check(not stat.S_ISLNK(os.lstat(ancestor).st_mode), 'REPORT_PATH_SYMLINK')
        self.path = path
        self.dir_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        self.reserve_fd = None
        try:
            directory_stat = os.fstat(self.dir_fd)
            check(stat.S_ISDIR(directory_stat.st_mode) and
                  directory_stat.st_uid == os.getuid() and
                  stat.S_IMODE(directory_stat.st_mode) == 0o700 and
                  os.stat(path).st_ino == directory_stat.st_ino and
                  os.stat(path).st_dev == directory_stat.st_dev,
                  'REPORT_DIRECTORY_PRIVATE_MODE')
            self.directory_identity = (directory_stat.st_dev, directory_stat.st_ino)
            fs = os.fstatvfs(self.dir_fd)
            check(fs.f_bavail * fs.f_frsize >= REPORT_RESERVE_BYTES,
                  'REPORT_RESERVE_UNAVAILABLE')
            existing = set(os.listdir(path))
            if self.RESERVE_FILE_NAME in existing:
                self.reserve_fd = os.open(self.RESERVE_FILE_NAME,
                    os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=self.dir_fd)
                st = os.fstat(self.reserve_fd)
                check(stat.S_ISREG(st.st_mode) and st.st_uid == os.getuid() and
                      stat.S_IMODE(st.st_mode) == 0o600 and
                      st.st_size == REPORT_RESERVE_BYTES and st.st_nlink == 1,
                      'REPORT_RESERVE_IDENTITY')
                check(self.REPORT_FILE_NAME not in existing and
                      self.INCOMPLETE_FILE_NAME not in existing,
                      'REPORT_COMPLETION_ALREADY_PRESENT')
            if self.REPORT_FILE_NAME not in existing and self.INCOMPLETE_FILE_NAME not in existing:
                if self.RESERVE_FILE_NAME not in existing:
                    self.reserve_fd = os.open(self.RESERVE_FILE_NAME,
                        os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW,
                        0o600, dir_fd=self.dir_fd)
                fcntl.flock(self.reserve_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # F6: a pre-existing reserve file's logical size and link
                # count are never evidence that it was ever actually
                # physically allocated (a sparse file with the right name
                # can be reopened just as easily as a genuinely reserved
                # one). Re-verify its identity under the lock -- it may have
                # changed in the gap between the ``os.listdir``/``os.open``
                # above and acquiring the lock -- then (re-)establish its
                # physical allocation before trusting it as the mandatory
                # acquisition prerequisite. A freshly created file is still
                # size 0 at this point (``O_CREAT`` alone never allocates);
                # ``posix_fallocate`` both extends and allocates it, and over
                # a range that is already fully allocated it is a safe,
                # idempotent no-op that never truncates or zeroes existing
                # bytes -- so identity (never size) is checked first, then
                # size and physical allocation are checked only afterward.
                named = os.stat(self.RESERVE_FILE_NAME, dir_fd=self.dir_fd,
                                follow_symlinks=False)
                held = os.fstat(self.reserve_fd)
                check((named.st_dev, named.st_ino) == (held.st_dev, held.st_ino) and
                      stat.S_ISREG(held.st_mode) and held.st_uid == os.getuid() and
                      stat.S_IMODE(held.st_mode) == 0o600 and held.st_nlink == 1 and
                      held.st_size in (0, REPORT_RESERVE_BYTES),
                      'REPORT_RESERVE_IDENTITY')
                os.posix_fallocate(self.reserve_fd, 0, REPORT_RESERVE_BYTES)
                os.fsync(self.reserve_fd)
                os.fsync(self.dir_fd)
                held = os.fstat(self.reserve_fd)
                check(held.st_size == REPORT_RESERVE_BYTES and
                      held.st_blocks * 512 >= REPORT_RESERVE_BYTES,
                      'REPORT_RESERVE_NOT_PHYSICALLY_ALLOCATED')
                self.reserved = True
            else:
                raise LaunchContractError('REPORT_COMPLETION_ALREADY_PRESENT')
        except BaseException:
            if self.reserve_fd is not None:
                os.close(self.reserve_fd)
            os.close(self.dir_fd)
            raise

    def persist(self, report):
        data = canonical(report) + b'\n'
        check(len(data) <= REPORT_RESERVE_BYTES, 'REPORT_CAPACITY_EXCEEDED')
        self._commit(data, self.REPORT_FILE_NAME)
        return {'classification': 'COMPLETE', 'bytes': len(data),
                'sha256': hashlib.sha256(data).hexdigest()}

    def persist_incomplete(self, reason):
        check(type(reason) is str and reason, 'REPORT_INCOMPLETE_REASON')
        data = canonical({'classification': 'INCOMPLETE', 'reason': reason}) + b'\n'
        check(len(data) <= REPORT_RESERVE_BYTES, 'REPORT_CAPACITY_EXCEEDED')
        self._commit(data, self.INCOMPLETE_FILE_NAME)
        return {'classification': 'INCOMPLETE', 'reason': reason}

    def _commit(self, data, filename):
        if os.path.exists(self.path / filename):
            raise FileExistsError(filename)
        named = os.stat(self.RESERVE_FILE_NAME, dir_fd=self.dir_fd,
                        follow_symlinks=False)
        held = os.fstat(self.reserve_fd)
        check((named.st_dev, named.st_ino) == (held.st_dev, held.st_ino) and
              stat.S_ISREG(named.st_mode), 'REPORT_RESERVE_IDENTITY')
        os.lseek(self.reserve_fd, 0, os.SEEK_SET)
        written = os.write(self.reserve_fd, data)
        check(written == len(data), 'REPORT_SHORT_WRITE')
        os.ftruncate(self.reserve_fd, len(data))
        os.fsync(self.reserve_fd)
        os.link(self.RESERVE_FILE_NAME, filename, src_dir_fd=self.dir_fd,
                dst_dir_fd=self.dir_fd, follow_symlinks=False)
        os.fsync(self.dir_fd)
        os.unlink(self.RESERVE_FILE_NAME, dir_fd=self.dir_fd)
        os.fsync(self.dir_fd)

    def close(self):
        if self.reserve_fd is not None:
            os.close(self.reserve_fd)
            self.reserve_fd = None
        if self.dir_fd is not None:
            os.close(self.dir_fd)
            self.dir_fd = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
