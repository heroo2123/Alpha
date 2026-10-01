"""Offline Gate 3 V4 slice-3 runtime and durable report composition.

The injected transport, clock, and resource interfaces have synthetic fixtures
only. Acquisition requires an exact reviewed frozen plan, a pinned expected
plan digest, the four acquired journals, and a physically reserved report sink.
The state machine binds request order, original clock evidence, denial history,
known transport closure, budget accounting, store receipts, and bounded capture
receipts. It does not grant provider access, G3-L, feature eligibility, or
financial/SHADOW authority. Real source mapping, decoding, clock measurement,
and network adapters require separate review.
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

    @classmethod
    def for_requests(cls, requests):
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
        # Object plus temporary copy, budget/three store/12 session/four
        # shared records, a bounded descriptor, and the full report reserve.
        records = chunks + 3 * n
        disk = (2 * body + (records + 19 * n) * 65536 +
                REPORT_RESERVE_BYTES + 4096)
        memory = (max((r.reservation_bytes for r in requests), default=0) * 2 +
                  65536 * 4)
        return cls(disk, memory, records, 3 * n)


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
    """Exact, ordered offline request schedule and independently pinned review.

    ``expected_sha256`` is supplied by the caller's reviewed artifact, separate
    from this object. The runtime recomputes it and binds it to the session.
    """
    manifest_sha256: str
    review_sha256: str
    window: AbsoluteWindow
    requests: tuple[AttemptRequest, ...]
    review_raw: bytes
    events: tuple[FrozenEvent, ...] = ()

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
        exact(review, ('schema_version', 'manifest_sha256', 'window_sha256',
                       'request_schedule_sha256', 'event_schedule_sha256'),
              'RUNTIME_PLAN_REVIEW_SCHEMA')
        check(review['schema_version'] == 1 and
              review['manifest_sha256'] == self.manifest_sha256 and
              review['window_sha256'] ==
              hashlib.sha256(canonical(asdict(self.window))).hexdigest() and
              review['request_schedule_sha256'] ==
              hashlib.sha256(canonical([asdict(r) for r in self.requests])).hexdigest() and
              review['event_schedule_sha256'] ==
              hashlib.sha256(canonical([asdict(e) for e in self.events])).hexdigest(),
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
            'events': [asdict(e) for e in self.events]})).hexdigest()

    @classmethod
    def from_validated_manifest(cls, manifest_raw, *, repo, object_root, now_utc,
                                window, review_sha256, review_raw, request_pins,
                                events=()):
        """The one sanctioned way to build a production ``FrozenPlan``: every
        request is derived from the schedule/endpoint table of a manifest
        that has itself just passed the full, independent
        ``validate_manifest_v4`` review -- never from a caller-asserted
        schedule merely claiming to match some opaque manifest digest (F4).

        ``request_pins`` supplies the handful of per-request fields this
        module has never claimed to validate and that the V4 manifest schema
        does not itself carry -- ``expected_etag``, ``expected_object_bytes``,
        ``source_pin``, ``decoder_pin``, ``clock_policy_sha256``,
        ``validator_sha256``, ``dependency_commit_hashes`` -- keyed by
        ``request_id`` (source/decoder qualification and real networking
        remain separate, explicit later work, exactly as this module's
        docstring already states).
        """
        manifest_sha256 = validate_manifest_v4(manifest_raw, repo=repo,
            object_root=object_root, now_utc=now_utc)
        payload = parse_canonical(manifest_raw)
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
            exact(pins, ('expected_etag', 'expected_object_bytes', 'source_pin',
                         'decoder_pin', 'clock_policy_sha256', 'validator_sha256',
                         'dependency_commit_hashes'), 'RUNTIME_PLAN_PIN_SCHEMA')
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
                source_pin=pins['source_pin'], decoder_pin=pins['decoder_pin'],
                clock_policy_sha256=pins['clock_policy_sha256'],
                validator_sha256=pins['validator_sha256'],
                dependency_commit_hashes=pins['dependency_commit_hashes'],
                prerequisite_request_ids=prerequisite_request_ids))
        return cls(manifest_sha256, review_sha256, window, tuple(requests),
                  review_raw, events)


def _bounded_headers(response):
    check(type(response) is OfflineResponse and type(response.status) is int and
          type(response.headers) is tuple and len(response.headers) <= 32,
          'RUNTIME_HEADER_SHAPE')
    headers = {}
    size = 0
    for key, value in response.headers:
        check(type(key) is str and type(value) is str and key.isascii() and
              0 < len(key.encode()) <= 64 and len(value.encode()) <= 1024 and
              '\r' not in value and '\n' not in value,
              'RUNTIME_HEADER_SHAPE')
        name = key.lower()
        check(name not in headers, 'RUNTIME_DUPLICATE_HEADER')
        headers[name] = value
        size += len(key.encode()) + len(value.encode()) + 4
    check(size <= 4096 and headers.get('transfer-encoding') is None and
          headers.get('content-encoding', 'identity').lower() == 'identity',
          'RUNTIME_HEADER_SHAPE')
    return headers


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
    if evidence_missing_cause is None:
        retry_raw = headers.get('retry-after')
        if retry_raw is not None:
            if retry_raw.isascii() and retry_raw.isdecimal():
                retry_after_seconds = float(retry_raw)
            else:
                try:
                    parsed = email.utils.parsedate_to_datetime(retry_raw)
                    if parsed.tzinfo is not None:
                        expiry = parsed.astimezone(datetime.timezone.utc).timestamp()
                        retry_after_seconds = max(0.0, expiry -
                            (receipt_reading.utc_seconds + receipt_reading.uncertainty_seconds))
                except (TypeError, ValueError, OverflowError):
                    pass
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
                 expected_plan_sha256: str, report_sink: 'ReportSink'):
        check(type(shared) is SharedLedger and type(session) is SessionLedger and
              type(budget) is DurableBudget and type(store) is VersionedImmutableObjectStore,
              'RUNTIME_COMPOSITION_SHAPE')
        check(isinstance(transport, Transport) and isinstance(clock, Clock) and
              isinstance(resources, ResourceProbe), 'RUNTIME_COMPOSITION_SHAPE')
        check(type(window) is AbsoluteWindow, 'RUNTIME_COMPOSITION_SHAPE')
        check(type(allowed_peer_ips) is tuple and allowed_peer_ips, 'RUNTIME_PEER_IPS')
        digest(manifest_sha256, 'RUNTIME_MANIFEST_DIGEST')
        digest(expected_plan_sha256, 'RUNTIME_EXPECTED_PLAN_DIGEST')
        check(type(plan) is FrozenPlan and plan.sha256 == expected_plan_sha256 and
              plan.manifest_sha256 == manifest_sha256 and plan.window == window,
              'RUNTIME_FROZEN_PLAN_MISMATCH')
        check(type(report_sink) is ReportSink and report_sink.dir_fd is not None and
              report_sink.reserved, 'RUNTIME_REPORT_RESERVE_REQUIRED')
        self.shared, self.session, self.budget, self.store = shared, session, budget, store
        self.transport, self.clock, self.resources = transport, clock, resources
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
        self.capacity = CapacityPlan.for_requests(remaining)
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
              len(store.receipts) + len(remaining) <= 4096 and
              len(session.events) + SESSION_EVENTS_PER_REQUEST * len(remaining) <= 32768 and
              session._journal_bytes +
              SESSION_EVENTS_PER_REQUEST * len(remaining) * 65536 <= LEDGER_MAX_BYTES and
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
                   'report_root': report_sink.directory_identity}
        self.session.bind_runtime_context(hashlib.sha256(canonical(context)).hexdigest())
        _check_prospective_resources(self.resources, self.capacity)

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
              self.store.context['max_clock_age'], 'RUNTIME_CLOCK_MEASUREMENT')
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
        self.session.attempt_intent(request.request_id, purpose=request.purpose,
            endpoint_id=request.endpoint_id, max_reservation_bytes=request.reservation_bytes,
            range_start=request.range_start, range_end=request.range_end,
            validator_sha256=request.validator_sha256, denial_head=self.shared.prev)
        self.session.refuse(request.request_id, reason=reason)
        self._capture(request, clocks=(evidence,))
        return {'request_id': request.request_id, 'outcome': 'REFUSED', 'reason': reason}

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
            headers = _bounded_headers(response)
            evidence_missing_cause = None
        except LaunchContractError as exc:
            headers = {}
            evidence_missing_cause = f'RUNTIME_HEADER_VALIDATION_FAILED:{exc}'
        if receipt is None:
            receipt = self.clock.evidence('body_receipt')
            self._observe_local_clock(receipt)
        denial_status = _DENIAL_STATUSES.get(response.status)
        if (denial_status is None and evidence_missing_cause is None and
                'retry-after' in headers):
            denial_status = 'OTHER'
        if denial_status is not None and not denial_recorded:
            record = _build_denial_record(denial_status, response,
                window=self.window, receipt_evidence=receipt,
                headers=headers, origin=request.origin,
                evidence_missing_cause=evidence_missing_cause)
            self.shared.denial_observed(request.request_id, denial=record)
            self.session.denial(request.request_id, reason=record['reason'],
                                 shared_denial_event_hash=self.shared.prev)
        delivered = sum(len(chunk) for chunk in response.chunks[:stream.index])
        for chunk in response.chunks[stream.index:]:
            check(type(chunk) is bytes and chunk, 'RUNTIME_PREFETCH_SHAPE')
            allowance = self.budget.next_read_limit(65536)
            try:
                self.budget.consume(request.request_id, chunk,
                    overdelivery_total_bytes=stream.prefetched_bytes-delivered,
                    permitted_bytes=allowance)
            except LaunchContractError as exc:
                if str(exc) != 'STREAM_ABORT_AT_ALLOWANCE':
                    raise
                break
            delivered += len(chunk)

    def run_attempt(self, request: AttemptRequest) -> dict:
        check(type(request) is AttemptRequest, 'RUNTIME_REQUEST_SHAPE')
        check(self.session.report_completed_sha256 is None,
              'RUNTIME_REPORT_ALREADY_COMPLETED')
        self._check_plan_request(request)

        pre = self.clock.evidence('request_start')
        try:
            resolved_dependencies = self._resolve_prerequisites(request)
            _check_prospective_resources(self.resources, self.capacity)
            self._enforce_window(pre)
            # F3: a cooldown has only provably expired once the *lower*
            # (conservative) bound of our own current-time uncertainty has
            # reached it; using the upper bound here would resume dispatch
            # while the true current time could still be within the hold.
            check(not self.shared.is_blocked(request.control_domain_id,
                  now_utc=pre.reading.utc_seconds - pre.reading.uncertainty_seconds),
                  'RUNTIME_CONTROL_DOMAIN_BLOCKED')
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
        if self.clock.monotonic() >= deadline_mono:
            self._account_prefetched_on_deadline(request, stream)
            raise LaunchContractError('RUNTIME_DISPATCH_DEADLINE')
        try:
            headers = _bounded_headers(response)
        except LaunchContractError:
            # F2: a header-shape failure must not itself discard the known
            # denial/already-delivered-bytes observations -- account them
            # (with an explicit missing-evidence cause, since the headers
            # cannot be trusted) before re-raising the original failure.
            self._account_prefetched_on_deadline(request, stream)
            raise
        header_receipt = self.clock.evidence('body_receipt')
        try:
            self._enforce_window(header_receipt, body=True)
        except LaunchContractError:
            # F2: any clock-validity/window failure here -- not only the
            # acquisition-end case -- must still account known bytes/denial.
            self._account_prefetched_on_deadline(request, stream,
                                                receipt=header_receipt)
            raise
        if (header_receipt.reading.monotonic_seconds > deadline_mono or
                self.clock.monotonic() >= deadline_mono):
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
            denial_record = _build_denial_record(denial_status, response,
                window=self.window, receipt_evidence=header_receipt,
                headers=headers, origin=request.origin)
            self.shared.denial_observed(request.request_id, denial=denial_record)
            self.session.denial(request.request_id, reason=denial_record['reason'],
                                 shared_denial_event_hash=self.shared.prev)

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
                remaining = deadline_mono-self.clock.monotonic()
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

        check(self.clock.monotonic() < deadline_mono, 'RUNTIME_BODY_DEADLINE')
        known_closed = stream.close(remaining_seconds=deadline_mono-self.clock.monotonic())
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
        _check_prospective_resources(self.resources, self.capacity)
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
        if attempt['reason'] != 'RUNTIME_PREREQUISITE_MISSING':
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
            status, outcome, reason = 'NEVER_ATTEMPTED', None, 'RUNTIME_SLOT_NOT_IN_SCHEDULE'
        elif attempt is None:
            failed_dependency = any(session.attempt_history.get(prior_id, {}).get(
                'outcome') != 'SUCCESS' for prior_id in request.prerequisite_request_ids)
            status, outcome = 'NEVER_ATTEMPTED', None
            reason = ('RUNTIME_FAILED_PREREQUISITE' if failed_dependency else
                      'RUNTIME_SCHEDULED_NOT_ATTEMPTED')
        else:
            outcome, reason = attempt['outcome'], attempt['reason']
            status = 'REFUSED' if outcome == 'REFUSED' else 'ATTEMPTED'
        row = {'slot_index': slot, 'status': status, 'outcome': outcome,
               'reason': reason, 'request_id': None if request is None else request.request_id,
               'provider': None if request is None else request.provider}
        rows.append(row)
        label = outcome or status
        counts[label] = counts.get(label, 0) + 1
    per_purpose = {}
    observed = {rid: 0 for rid in budget.attempts}
    for event in budget.events:
        if event['op'] in ('chunk', 'violation'):
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
            'primary_provider': event.primary_provider,
            'primary_outcomes': {r.request_id: outcomes[r.request_id]
                for r in field_requests if r.provider == event.primary_provider},
            'field_outcomes': outcomes,
            'classification': 'COMPLETE' if complete else 'FAILED_PREREQUISITE',
            'all_providers_planned': all_provider})
    return {'schema_version': 2, 'manifest_sha256': plan.manifest_sha256,
            'plan_sha256': plan.sha256, 'review_sha256': plan.review_sha256,
            'slot_count': SLOT_COUNT, 'rows': rows, 'outcome_counts': counts,
            'attempted_request_ids': seen_ids,
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
                'planned_event_count': sum(e['all_providers_planned'] for e in event_rows)}}


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
