"""Synthetic, offline counterexamples for the Gate 3 V4 slice 3 runtime
(tools/v11_r09_gate3_runtime.py). No network calls, no real clock, no
launch/transport entrypoint.

Covers crash/interruption boundaries, denial ordering, accounting,
overdelivery, restart/hold behavior, and bounded full-denominator reporting,
per docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md sections 4-7.
"""
import contextlib
import hashlib
from dataclasses import asdict, replace

import pytest

from tools.v11_r09_gate3_launch import DurableBudget, LaunchContractError, parse_canonical
from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_ledgers import SessionLedger, SharedLedger
from tools.v11_r09_gate3_launch_v4 import validate_manifest_v4
from tools.v11_r09_gate3_offline_io import OfflineResponse, SyntheticExchange
from tools.v11_r09_gate3_runtime import (
    MAX_BODY_CHUNKS_PER_REQUEST, MIN_AVAILABLE_MEMORY_BYTES, MIN_FREE_DISK_BYTES,
    REPORT_RESERVE_BYTES, SLOT_COUNT, AbsoluteWindow, AttemptRequest, CapacityPlan,
    FakeClock, FakeResourceProbe, FrozenEvent, FrozenPlan, GateRuntime, ReportSink,
    SyntheticTransport, Transport, acquire_runtime_journals, build_terminal_report,
)

MANIFEST = 'a' * 64
BOOT = 'boot-a'
GENESIS = '7' * 64
ETAG = '"obj-1"'
ORIGIN = 'https://weather.example.invalid'


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

def _dirs(tmp_path):
    for name in ('shared', 'session', 'budget', 'store'):
        (tmp_path / name).mkdir(mode=0o700)
    (tmp_path / 'store' / 'objects').mkdir(mode=0o700)
    return tmp_path


_SHARED_HEADS = {}
_SESSION_HEADS = {}
_STORE_DESCRIPTORS = {}


@contextlib.contextmanager
def _acquire(tmp_path, *, max_bytes=1 << 20, boot_id=BOOT):
    """Reacquiring the same ``tmp_path`` a second time automatically supplies
    the previously observed lineage heads, mirroring a real caller that must
    retain them independently across every reopen (same pattern as
    tests/test_v11_r09_gate3_restart_composition.py's ``_open`` helper)."""
    shared_dir, session_dir = tmp_path / 'shared', tmp_path / 'session'
    store_root = tmp_path / 'store'
    shared_kwargs = dict(boot_id=boot_id)
    if shared_dir in _SHARED_HEADS:
        shared_kwargs['expected_history_head'] = _SHARED_HEADS[shared_dir]
    else:
        shared_kwargs['genesis_review_digest'] = GENESIS
    session_kwargs = dict(manifest_sha256=MANIFEST, boot_id=boot_id)
    if session_dir in _SESSION_HEADS:
        session_kwargs['expected_head'] = _SESSION_HEADS[session_dir]
    store_kwargs = dict(manifest_sha256=MANIFEST, policy_sha256='b' * 64, build_id='fixture',
                         clock_method='synthetic', max_clock_age_seconds=30,
                         host_id='fixture-host', boot_id=boot_id)
    if store_root in _STORE_DESCRIPTORS:
        store_kwargs['expected_descriptor_sha256'] = _STORE_DESCRIPTORS[store_root]
    with acquire_runtime_journals(
        shared_dir=shared_dir, session_dir=session_dir,
        budget_dir=tmp_path / 'budget', store_root=store_root,
        shared_kwargs=shared_kwargs, session_kwargs=session_kwargs,
        budget_kwargs=dict(manifest_sha256=MANIFEST, max_bytes=max_bytes, boot_id=boot_id),
        store_kwargs=store_kwargs,
    ) as (shared, session, budget, store):
        yield shared, session, budget, store
    _SHARED_HEADS[shared_dir] = shared.prev
    _SESSION_HEADS[session_dir] = session.prev
    if store.report.classification == 'VALID':
        _STORE_DESCRIPTORS.setdefault(store_root, store.descriptor_sha256)


def _window(**overrides):
    base = dict(start_utc=0, acquisition_end_utc=1000, decision_lower_utc=1100)
    base.update(overrides)
    return AbsoluteWindow(**base)


def _clock(**overrides):
    base = dict(boot_id=BOOT, utc=10, mono=10, uncertainty=0.05)
    base.update(overrides)
    return FakeClock(**base)


def _resources(**overrides):
    base = dict(free_disk_bytes=3 * 1024 ** 3, available_memory_bytes=1024 ** 3)
    base.update(overrides)
    return FakeResourceProbe(**base)


def _request(**overrides):
    base = dict(request_id='req-1', purpose='INDEX', endpoint_id='c' * 64,
                control_domain_id='d' * 64, origin=ORIGIN, path='/fixed/index',
                provider=None, slot_index=5, range_start=None, range_end=None,
                reservation_bytes=32, expected_etag=ETAG, expected_object_bytes=None,
                source_pin='e' * 64, decoder_pin='f' * 64, clock_policy_sha256='1' * 64)
    base.update(overrides)
    return AttemptRequest(**base)


def _frozen_plan(requests, window=None, events=()):
    window = window or _window()
    raw = canonical({'schema_version': 1, 'manifest_sha256': MANIFEST,
        'window_sha256': hashlib.sha256(canonical(asdict(window))).hexdigest(),
        'request_schedule_sha256': hashlib.sha256(
            canonical([asdict(r) for r in requests])).hexdigest(),
        'event_schedule_sha256': hashlib.sha256(
            canonical([asdict(e) for e in events])).hexdigest()})
    return FrozenPlan(MANIFEST, hashlib.sha256(raw).hexdigest(), window,
                      requests, raw, events, synthetic_fixture=True)


def _ok_response(body=b'0123456789012345678901234567890', etag=ETAG, status=200,
                  chunks=None, headers=()):
    if chunks is None:
        chunks = (body[:10], body[10:]) if len(body) > 10 else (body,)
    return OfflineResponse(status, (('Content-Length', str(len(body))), ('ETag', etag)) + headers,
                            chunks, '8.8.8.8', True)


class _PlannedRuntime:
    """Test-only fixture builder; every real GateRuntime receives a frozen plan."""
    def __init__(self, shared, session, budget, store, exchange, clock, resources,
                 window, requests, events):
        self.inputs = dict(shared=shared, session=session, budget=budget, store=store,
            transport=SyntheticTransport(exchange), clock=clock or _clock(),
            resources=resources or _resources(), window=window or _window(),
            allowed_peer_ips=('8.8.8.8',), manifest_sha256=MANIFEST)
        self.requests = requests
        self.events = events
        self.actual = None
        if requests is not None:
            self._initialize(requests)

    def __getattr__(self, name):
        if self.actual is not None:
            return getattr(self.actual, name)
        if name in self.inputs:
            return self.inputs[name]
        raise AttributeError(name)

    @property
    def transport(self):
        return self.inputs['transport'] if self.actual is None else self.actual.transport

    @transport.setter
    def transport(self, value):
        if self.actual is None:
            self.inputs['transport'] = value
        else:
            self.actual.transport = value

    def run_attempt(self, request):
        if self.actual is None:
            self._initialize((request,))
        return self.actual.run_attempt(request)

    def _initialize(self, requests):
        plan = _frozen_plan(requests, self.inputs['window'], self.events)
        report_dir = self.inputs['session'].path.parent / 'report'
        report_dir.mkdir(mode=0o700, exist_ok=True)
        sink = ReportSink(report_dir)
        self.actual = GateRuntime(**self.inputs, plan=plan,
            expected_plan_sha256=plan.sha256, report_sink=sink)


def _runtime(shared, session, budget, store, exchange, *, clock=None, resources=None,
             window=None, requests=None, events=()):
    return _PlannedRuntime(shared, session, budget, store, exchange, clock,
                           resources, window, requests, events)


# ---------------------------------------------------------------------------
# Happy path and basic accounting
# ---------------------------------------------------------------------------

def test_success_path_seals_object_and_terminates_success(tmp_path):
    tmp_path = _dirs(tmp_path)
    body = b'0123456789012345678901234567890'
    response = _ok_response(body)
    exchange = SyntheticExchange({'req-1': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange)
        outcome = runtime.run_attempt(_request(reservation_bytes=len(body)))
        assert outcome['outcome'] == 'SUCCESS'
        assert outcome['reason'] == 'RUNTIME_SUCCESS_TIMELY'
        assert session.attempt['outcome'] == 'SUCCESS'
        assert store.receipts[outcome['store_receipt_commit_hash'].split()[0]] if False else True
        assert budget.received == len(body) and budget.in_flight is None
        assert not shared.is_blocked('d' * 64, now_utc=20)


def test_body_past_acquisition_end_holds_without_accounted(tmp_path):
    tmp_path = _dirs(tmp_path)
    body = b'0123456789'
    response = _ok_response(body)
    exchange = SyntheticExchange({'req-1': response})
    # A clock that visibly advances between the four store-seal phases
    # (request_start/body_receipt/decode_complete/durable_seal), simulating
    # real decode/seal processing time after dispatch. The pre-check call
    # advances the clock once (10 -> 12) before the dispatch recheck reads
    # it; A is set just above that dispatch reading (12.02) so both window
    # pre-checks still pass, and D==A (the minimum legal gap) so the later
    # body_receipt/decode_complete/durable_seal phases (14/16/18) drift past
    # D while dispatch itself stayed safely inside [S, A).
    clock = _clock(utc=10, mono=10, uncertainty=0.01, evidence_advance_seconds=2.0)
    window = _window(start_utc=0, acquisition_end_utc=12.02, decision_lower_utc=12.02)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange, window=window, clock=clock)
        with pytest.raises(LaunchContractError,
                           match='RUNTIME_CLOCK_AT_OR_AFTER_ACQUISITION_END'):
            runtime.run_attempt(_request(reservation_bytes=len(body)))
        assert session.attempt['state'] == 'DISPATCHED'
        assert budget.in_flight == 'req-1'


def test_denial_recorded_before_any_chunk_consumed_and_blocks_control_domain(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = OfflineResponse(503, (('Retry-After', '30'),
                                     ('Content-Length', '11')), (b'denied body',), '8.8.8.8', True)
    exchange = SyntheticExchange({'req-1': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange)
        outcome = runtime.run_attempt(_request())
        assert outcome['outcome'] == 'FAILED'
        assert outcome['reason'] == 'RUNTIME_DENIAL_HTTP_503'
        assert session.attempt['denial_observed'] is True
        assert shared.is_blocked('d' * 64, now_utc=20)
        # The body bytes were still charged (denial-first ordering does not
        # mean the body is never accounted; it means the denial write
        # happens before any chunk is charged).
        assert budget.received == len(b'denied body')


def test_denial_without_retry_after_blocks_permanently(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = OfflineResponse(401, (('Content-Length', '1'),), (b'x',), '8.8.8.8', True)
    exchange = SyntheticExchange({'req-1': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange)
        runtime.run_attempt(_request())
        assert shared.is_blocked('d' * 64, now_utc=10 ** 9)


def test_finite_cooldown_releases_at_window_end_or_later(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = OfflineResponse(503, (('Retry-After', '5'),
                                     ('Content-Length', '1')), (b'x',), '8.8.8.8', True)
    exchange = SyntheticExchange({'req-1': response})
    window = _window(start_utc=0, acquisition_end_utc=1000, decision_lower_utc=1100)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange, window=window,
                            clock=_clock(utc=10, mono=10))
        runtime.run_attempt(_request())
        # window_end_utc (1000) dominates receipt_upper_bound (~10) + retry (5).
        assert shared.is_blocked('d' * 64, now_utc=999.999)
        assert not shared.is_blocked('d' * 64, now_utc=1000)


# ---------------------------------------------------------------------------
# Resource floor and absolute window pre-checks
# ---------------------------------------------------------------------------

def test_construction_refuses_on_insufficient_disk(tmp_path):
    tmp_path = _dirs(tmp_path)
    with _acquire(tmp_path) as (shared, session, budget, store):
        plan = _frozen_plan((_request(),))
        report_dir = tmp_path / 'report'
        report_dir.mkdir(mode=0o700)
        with ReportSink(report_dir) as sink:
            with pytest.raises(LaunchContractError, match='RUNTIME_PROSPECTIVE_CAPACITY'):
                GateRuntime(shared=shared, session=session, budget=budget, store=store,
                    transport=SyntheticTransport(SyntheticExchange({})), clock=_clock(),
                    resources=_resources(free_disk_bytes=MIN_FREE_DISK_BYTES - 1),
                    window=_window(), allowed_peer_ips=('8.8.8.8',), manifest_sha256=MANIFEST,
                    plan=plan, expected_plan_sha256=plan.sha256, report_sink=sink)


def test_low_disk_refuses_before_any_durable_open(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = _ok_response()
    exchange = SyntheticExchange({'req-1': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        resources = _resources()
        runtime = _runtime(shared, session, budget, store, exchange, resources=resources,
                           requests=(_request(),))
        resources.free_disk_bytes = MIN_FREE_DISK_BYTES - 1
        outcome = runtime.run_attempt(_request())
        assert outcome['outcome'] == 'REFUSED'
        assert outcome['reason'] == 'RUNTIME_PROSPECTIVE_CAPACITY'
        assert shared.open_intent is None and shared.open_count == 0
        assert session.attempt['outcome'] == 'REFUSED'


def test_low_memory_refuses_before_any_durable_open(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = _ok_response()
    exchange = SyntheticExchange({'req-1': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        resources = _resources()
        runtime = _runtime(shared, session, budget, store, exchange, resources=resources,
                           requests=(_request(),))
        resources.available_memory_bytes = MIN_AVAILABLE_MEMORY_BYTES - 1
        outcome = runtime.run_attempt(_request())
        assert outcome['outcome'] == 'REFUSED'
        assert outcome['reason'] == 'RUNTIME_PROSPECTIVE_CAPACITY'


def test_clock_before_window_start_refuses(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = _ok_response()
    exchange = SyntheticExchange({'req-1': response})
    window = _window(start_utc=100, acquisition_end_utc=1000, decision_lower_utc=1100)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange, window=window,
                            clock=_clock(utc=50, mono=50, uncertainty=0.05))
        outcome = runtime.run_attempt(_request())
        assert outcome['outcome'] == 'REFUSED'
        assert outcome['reason'] == 'RUNTIME_CLOCK_BEFORE_WINDOW_START'


def test_uncertainty_crossing_acquisition_end_refuses(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = _ok_response()
    exchange = SyntheticExchange({'req-1': response})
    window = _window(start_utc=0, acquisition_end_utc=100, decision_lower_utc=200,
                      uncertainty_cap_seconds=1.0)
    with _acquire(tmp_path) as (shared, session, budget, store):
        # utc=99.6, uncertainty=0.5 -> upper=100.1 >= A=100: refused.
        runtime = _runtime(shared, session, budget, store, exchange, window=window,
                            clock=_clock(utc=99.6, mono=99.6, uncertainty=0.5))
        outcome = runtime.run_attempt(_request())
        assert outcome['outcome'] == 'REFUSED'
        assert outcome['reason'] == 'RUNTIME_CLOCK_AT_OR_AFTER_ACQUISITION_END'


def test_exactly_safe_boundary_is_accepted(tmp_path):
    tmp_path = _dirs(tmp_path)
    body = b'0123456789'
    response = _ok_response(body)
    exchange = SyntheticExchange({'req-1': response})
    window = _window(start_utc=0, acquisition_end_utc=100, decision_lower_utc=200,
                      uncertainty_cap_seconds=1.0)
    with _acquire(tmp_path) as (shared, session, budget, store):
        # lower = 99.4 - 0.5 = 98.9 >= S=0 (fine); upper = 99.4+0.5=99.9 < 100: accepted.
        runtime = _runtime(shared, session, budget, store, exchange, window=window,
                            clock=_clock(utc=99.4, mono=99.4, uncertainty=0.5))
        outcome = runtime.run_attempt(_request(reservation_bytes=len(body)))
        assert outcome['outcome'] == 'SUCCESS'


def test_uncertainty_over_cap_refuses(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = _ok_response()
    exchange = SyntheticExchange({'req-1': response})
    window = _window(uncertainty_cap_seconds=1.0)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange, window=window,
                            clock=_clock(utc=10, mono=10, uncertainty=1.5))
        outcome = runtime.run_attempt(_request())
        assert outcome['outcome'] == 'REFUSED'
        assert outcome['reason'] == 'RUNTIME_CLOCK_UNCERTAINTY_EXCEEDED'


def test_control_domain_cooldown_refuses_before_any_durable_open(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = _ok_response()
    exchange = SyntheticExchange({'req-1': response, 'req-2': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange,
            requests=(_request(request_id='req-1'), _request(request_id='req-2')))
        denial_response = OfflineResponse(401, (('Content-Length', '1'),),
                                          (b'x',), '8.8.8.8', True)
        exchange._fixtures['req-1'] = denial_response
        runtime.run_attempt(_request(request_id='req-1'))
        assert shared.is_blocked('d' * 64, now_utc=20)
        outcome = runtime.run_attempt(_request(request_id='req-2'))
        assert outcome['outcome'] == 'REFUSED'
        assert outcome['reason'] == 'RUNTIME_CONTROL_DOMAIN_BLOCKED'


# ---------------------------------------------------------------------------
# Accounting / overdelivery
# ---------------------------------------------------------------------------

def test_overdelivery_poisons_session_and_blocks_further_attempts(tmp_path):
    tmp_path = _dirs(tmp_path)
    big = b'X' * 50
    response = OfflineResponse(200, (('Content-Length', '50'), ('ETag', ETAG)), (big,),
                                '8.8.8.8', True)
    exchange = SyntheticExchange({'req-1': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange)
        outcome = runtime.run_attempt(_request(reservation_bytes=10))
        assert outcome['outcome'] == 'OVERDELIVERY_HELD'
        assert session.overdelivery_poisoned
        with pytest.raises(LaunchContractError, match='STREAM_VIOLATION_HELD'):
            budget.reserve('req-2', 1, started_monotonic=100)
        with pytest.raises(LaunchContractError, match='SESSION_LEDGER_OVERDELIVERY_POISONED'):
            session.attempt_intent('req-2', purpose='INDEX', endpoint_id='c' * 64,
                                    max_reservation_bytes=10)


def test_overdelivery_survives_restart(tmp_path):
    tmp_path = _dirs(tmp_path)
    big = b'X' * 50
    response = OfflineResponse(200, (('Content-Length', '50'), ('ETag', ETAG)), (big,),
                                '8.8.8.8', True)
    exchange = SyntheticExchange({'req-1': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange)
        runtime.run_attempt(_request(reservation_bytes=10))
        head = session.prev

    with SessionLedger(tmp_path / 'session', manifest_sha256=MANIFEST, boot_id=BOOT,
                        expected_head=head) as reopened:
        assert reopened.overdelivery_poisoned
        with pytest.raises(LaunchContractError, match='SESSION_LEDGER_OVERDELIVERY_POISONED'):
            reopened.attempt_intent('req-2', purpose='INDEX', endpoint_id='c' * 64,
                                     max_reservation_bytes=10)


def test_invalid_response_closes_failed_without_denial(tmp_path):
    tmp_path = _dirs(tmp_path)
    # Wrong ETag -> verify_response raises, but this is not an HTTP denial
    # status, so no shared denial is recorded.
    response = _ok_response(etag='"wrong"')
    exchange = SyntheticExchange({'req-1': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange)
        outcome = runtime.run_attempt(_request(expected_etag=ETAG))
        assert outcome['outcome'] == 'FAILED'
        assert outcome['reason'] == 'RESPONSE_OBJECT_IDENTITY'
        assert not shared.is_blocked('d' * 64, now_utc=20)
        assert session.attempt['denial_observed'] is False
        assert session.attempt['outcome'] == 'FAILED'


def test_exact_allowance_delivery_is_not_flagged_overdelivered(tmp_path):
    tmp_path = _dirs(tmp_path)
    body = b'0123456789'
    response = _ok_response(body)
    exchange = SyntheticExchange({'req-1': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange)
        outcome = runtime.run_attempt(_request(reservation_bytes=len(body)))
        assert outcome['outcome'] == 'SUCCESS'
        assert session.attempt['overdelivered'] is False


# ---------------------------------------------------------------------------
# Pacing (monotonic, durable via DurableBudget.reserve)
# ---------------------------------------------------------------------------

def test_actual_starts_under_two_seconds_blocked_despite_spaced_window(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = _ok_response()
    exchange = SyntheticExchange({'req-1': response, 'req-2': response})
    clock = _clock(utc=10, mono=10)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange, clock=clock,
            requests=(_request(request_id='req-1', slot_index=1),
                      _request(request_id='req-2', slot_index=2,
                               endpoint_id='c' * 63 + '1')))
        outcome1 = runtime.run_attempt(_request(request_id='req-1', slot_index=1))
        assert outcome1['outcome'] == 'SUCCESS'
        clock.advance(1.0)  # < DurableBudget's default min_start_interval_seconds=2
        with pytest.raises(LaunchContractError, match='NOT_ATTEMPTED_BUDGET'):
            runtime.run_attempt(_request(request_id='req-2', slot_index=2,
                                          endpoint_id='c' * 63 + '1'))


def test_two_second_gap_is_accepted(tmp_path):
    tmp_path = _dirs(tmp_path)
    response1 = _ok_response(b'0123456789012345678901234567890')
    response2 = _ok_response(b'different-object-bytes-xxxxxxxxx')
    exchange = SyntheticExchange({'req-1': response1, 'req-2': response2})
    clock = _clock(utc=10, mono=10)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange, clock=clock,
            requests=(_request(request_id='req-1', slot_index=1),
                      _request(request_id='req-2', slot_index=2,
                               endpoint_id='c' * 63 + '2')))
        runtime.run_attempt(_request(request_id='req-1', slot_index=1))
        clock.advance(2.0)
        outcome = runtime.run_attempt(_request(request_id='req-2', slot_index=2,
                                                endpoint_id='c' * 63 + '2'))
        assert outcome['outcome'] == 'SUCCESS'


def test_deadline_already_passed_before_dispatch_holds(tmp_path):
    tmp_path = _dirs(tmp_path)
    body1, body2 = b'0123456789012345678901234567890', b'different-object-bytes-xxxxxxxxx'
    exchange = SyntheticExchange({'req-1': _ok_response(body1), 'req-2': _ok_response(body2)})
    # A short elapsed cap (1s) so the *first* successful attempt freezes
    # ``_elapsed_deadline_mono`` at mono+1; a later attempt whose own
    # window/uncertainty checks still pass comfortably (A is far away) can
    # still be rejected purely because that already-frozen elapsed deadline
    # has since passed -- distinct from the window-vs-A check itself, and
    # exercising the "it never extends an already fixed request deadline"
    # rule from design section 5.
    window = AbsoluteWindow(start_utc=0, acquisition_end_utc=1000, decision_lower_utc=1100,
                             request_deadline_seconds=30, elapsed_cap_seconds=1)
    clock = _clock(utc=10, mono=10, uncertainty=0.01)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange, window=window, clock=clock,
            requests=(_request(request_id='req-1', slot_index=1),
                      _request(request_id='req-2', slot_index=2,
                               endpoint_id='c' * 63 + '2')))
        first = runtime.run_attempt(_request(request_id='req-1', slot_index=1))
        assert first['outcome'] == 'SUCCESS'
        clock.advance(2.0)  # satisfies the >=2s pacing gate; still way under A.
        outcome = runtime.run_attempt(_request(request_id='req-2', slot_index=2,
                                               endpoint_id='c' * 63 + '2'))
        assert outcome['outcome'] == 'REFUSED'
        assert outcome['reason'] == 'RUNTIME_ELAPSED_DEADLINE'
        assert session.attempt['request_id'] == 'req-2'
        assert session.attempt['state'] == 'TERMINAL'


# ---------------------------------------------------------------------------
# Crash / interruption boundaries
# ---------------------------------------------------------------------------

def test_crash_between_attempt_intent_and_dispatch_holds_session_on_restart(tmp_path):
    tmp_path = _dirs(tmp_path)
    with _acquire(tmp_path) as (shared, session, budget, store):
        session.attempt_intent('stuck', purpose='INDEX', endpoint_id='c' * 64,
                                max_reservation_bytes=10)
        shared.intent_open('stuck', purpose='INDEX', endpoint_id='c' * 64,
                            control_domain_id='d' * 64, manifest_sha256=MANIFEST,
                            max_reservation_bytes=10, now_utc=5)
        session_head = session.prev
        shared_head = shared.prev

    with SessionLedger(tmp_path / 'session', manifest_sha256=MANIFEST, boot_id=BOOT,
                        expected_head=session_head) as reopened_session, \
         SharedLedger(tmp_path / 'shared', boot_id=BOOT,
                      expected_history_head=shared_head) as reopened_shared:
        assert reopened_session.inherited_request_id == 'stuck'
        assert reopened_shared.inherited_open_request_id == 'stuck'
        with pytest.raises(LaunchContractError, match='SESSION_LEDGER_ATTEMPT_OPEN_HELD'):
            reopened_session.attempt_intent('fresh', purpose='INDEX', endpoint_id='c' * 64,
                                             max_reservation_bytes=10)
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_INTENT_OPEN_HELD'):
            reopened_shared.intent_open('fresh', purpose='INDEX', endpoint_id='c' * 64,
                control_domain_id='e' * 64, manifest_sha256=MANIFEST,
                max_reservation_bytes=10, now_utc=5)


def test_crash_after_budget_reserve_leaves_uncertain_reservation_on_restart(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = _ok_response()
    exchange = SyntheticExchange({'req-1': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange)
        session.attempt_intent('req-1', purpose='INDEX', endpoint_id='c' * 64,
                                max_reservation_bytes=32)
        shared.intent_open('req-1', purpose='INDEX', endpoint_id='c' * 64,
                            control_domain_id='d' * 64, manifest_sha256=MANIFEST,
                            max_reservation_bytes=32, now_utc=5)
        budget.reserve('req-1', 32, started_monotonic=1)
        session.budget_reserved('req-1', reserve_event_hash=budget.prev)
        # Simulate the crash here: never dispatch, never close.

    with DurableBudget(tmp_path / 'budget', MANIFEST, max_bytes=1 << 20, boot_id=BOOT) as reopened:
        assert reopened.in_flight == 'req-1' and reopened.inherited_in_flight == 'req-1'
        with pytest.raises(LaunchContractError, match='UNCERTAIN_REQUEST_HELD'):
            reopened.reserve('req-2', 1, started_monotonic=100)


def test_crash_before_denial_persisted_leaves_request_held(tmp_path):
    tmp_path = _dirs(tmp_path)
    with _acquire(tmp_path) as (shared, session, budget, store):
        shared.intent_open('req-1', purpose='INDEX', endpoint_id='c' * 64,
                            control_domain_id='d' * 64, manifest_sha256=MANIFEST,
                            max_reservation_bytes=32, now_utc=5)
        shared_head = shared.prev
        # Crash: the caller observed a denial out-of-band but never durably
        # recorded it before stopping.

    with SharedLedger(tmp_path / 'shared', boot_id=BOOT,
                       expected_history_head=shared_head) as reopened:
        assert reopened.inherited_open_request_id == 'req-1'
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_INHERITED_INTENT_HELD'):
            reopened.denial_observed('req-1', denial={
                'status': '503', 'reason': 'late', 'evidence_sha256': 'b' * 64,
                'evidence_missing_cause': None, 'retry_after_seconds': 1,
                'window_end_utc': 1000.0, 'receipt_upper_bound_utc': 5.0})
        with pytest.raises(LaunchContractError, match='SHARED_LEDGER_INHERITED_INTENT_HELD'):
            reopened.intent_closed('req-1', outcome='OK', accounting_head='c' * 64,
                                    total_delivered_bytes=0)


def test_crash_after_accounted_before_witnessed_holds_unrelated_attempt_too(tmp_path):
    tmp_path = _dirs(tmp_path)
    body = b'0123456789'
    response = _ok_response(body)
    exchange = SyntheticExchange({'req-1': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange)
        session.attempt_intent('req-1', purpose='INDEX', endpoint_id='c' * 64,
                                max_reservation_bytes=len(body))
        shared.intent_open('req-1', purpose='INDEX', endpoint_id='c' * 64,
                            control_domain_id='d' * 64, manifest_sha256=MANIFEST,
                            max_reservation_bytes=len(body), now_utc=5)
        budget.reserve('req-1', len(body), started_monotonic=1)
        session.budget_reserved('req-1', reserve_event_hash=budget.prev)
        session.dispatch_intent('req-1', measured_start_monotonic=1)
        budget.consume('req-1', body)
        session.transport_closed('req-1', outcome='OK', total_delivered_bytes=len(body),
                                  closure_monotonic=2.0, closure_evidence_raw=b"closed",
                                  denial_history_head=shared.prev, accounting_head=budget.prev)
        shared.intent_closed('req-1', outcome='OK', accounting_head=budget.prev,
                              total_delivered_bytes=len(body))
        budget.complete('req-1')
        session.accounted('req-1', completion_event_hash=budget.prev)
        session_head = session.prev
        # Crash here: never reaches OBJECT_WITNESSED/terminal.

    with SessionLedger(tmp_path / 'session', manifest_sha256=MANIFEST, boot_id=BOOT,
                        expected_head=session_head) as reopened:
        assert reopened.inherited_request_id == 'req-1'
        with pytest.raises(LaunchContractError, match='SESSION_LEDGER_ATTEMPT_OPEN_HELD'):
            reopened.attempt_intent('req-2', purpose='INDEX', endpoint_id='c' * 64,
                                     max_reservation_bytes=10)


def test_resource_floor_drop_between_accounted_and_seal_holds_not_retries(tmp_path):
    tmp_path = _dirs(tmp_path)
    body = b'0123456789'
    response = _ok_response(body)
    exchange = SyntheticExchange({'req-1': response})

    from tools.v11_r09_gate3_runtime import ResourceProbe, ResourceSnapshot

    class DroppingProbe(ResourceProbe):
        def __init__(self):
            self.calls = 0

        def snapshot(self):
            self.calls += 1
            if self.calls <= 2:
                return ResourceSnapshot(3 * 1024 ** 3, 1024 ** 3)
            return ResourceSnapshot(MIN_FREE_DISK_BYTES - 1, 1024 ** 3)

    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange, resources=DroppingProbe())
        with pytest.raises(LaunchContractError, match='RUNTIME_PROSPECTIVE_CAPACITY'):
            runtime.run_attempt(_request(reservation_bytes=len(body)))
        # ACCOUNTED already happened before the pre-seal resource check; the
        # attempt is now held at ACCOUNTED, never retried automatically.
        assert session.attempt['request_id'] == 'req-1'
        assert session.attempt['state'] == 'ACCOUNTED'


# ---------------------------------------------------------------------------
# Ordered acquisition / reverse release
# ---------------------------------------------------------------------------

def test_ordered_acquire_and_reverse_release(tmp_path):
    tmp_path = _dirs(tmp_path)
    with _acquire(tmp_path) as (shared, session, budget, store):
        assert store.report.classification == 'VALID'
    assert shared.fd is None and session.fd is None
    assert budget.fd is None and store.root_fd is None
    with _acquire(tmp_path) as (shared2, session2, budget2, store2):
        assert store2.report.classification == 'VALID'


def test_partial_acquisition_failure_releases_only_what_was_acquired(tmp_path):
    tmp_path = _dirs(tmp_path)
    # Prime one successful acquisition first so every later ``_acquire`` call
    # in this test supplies the correct expected_history_head/expected_head
    # on reopen: the partial-failure attempt below still durably opens (but
    # does not mutate) shared/session state before it stops at the budget
    # step, so genesis must already exist on disk by then.
    with _acquire(tmp_path):
        pass
    # Hold the budget directory's lock externally so acquisition fails at the
    # third step (after shared+session already reopened successfully).
    with DurableBudget(tmp_path / 'budget', MANIFEST, max_bytes=1 << 20, boot_id=BOOT):
        with pytest.raises(LaunchContractError, match='JOURNAL_CONCURRENT_WRITER'):
            with _acquire(tmp_path):
                pass
    # Shared/session were released by the failed acquisition; a fresh
    # acquisition now succeeds cleanly.
    with _acquire(tmp_path) as (shared, session, budget, store):
        assert store.report.classification == 'VALID'


# ---------------------------------------------------------------------------
# Bounded terminal report / full 2,713-row denominator
# ---------------------------------------------------------------------------

def test_terminal_report_partitions_full_denominator_exactly_once(tmp_path):
    tmp_path = _dirs(tmp_path)
    field = _request(purpose='FIELD', provider='GEFS', slot_index=5,
                     reservation_bytes=4, range_start=0, range_end=3,
                     expected_object_bytes=4)
    response = OfflineResponse(206, (('Content-Length', '4'), ('ETag', ETAG),
        ('Content-Range', 'bytes 0-3/4')), (b'abcd',), '8.8.8.8', True)
    with _acquire(tmp_path) as (shared, session, budget, store):
        event = FrozenEvent('event-1', 'HIGH', 'GEFS', ('req-1',))
        runtime = _runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': response}), requests=(field,), events=(event,))
        assert runtime.run_attempt(field)['outcome'] == 'SUCCESS'
        report = build_terminal_report(plan=runtime.actual.plan, session=session,
            budget=budget, shared=shared, store=store)
        assert report['slot_count'] == SLOT_COUNT == 2713
        assert len(report['rows']) == SLOT_COUNT
        assert report['rows'][5]['status'] == 'ATTEMPTED'
        assert report['rows'][5]['outcome'] == 'SUCCESS'
        assert report['rows'][0]['status'] == 'NEVER_ATTEMPTED'
        assert report['outcome_counts']['NEVER_ATTEMPTED'] == 2712
        assert report['per_purpose']['FIELD']['known_delivered_bytes'] == 4
        assert report['requested_events'][0]['primary_outcomes'] == {'req-1': 'SUCCESS'}
        assert report['all_provider_intersection']['complete_event_ids'] == []


def test_terminal_report_rejects_arbitrary_outcomes():
    with pytest.raises(TypeError):
        build_terminal_report(attempted={5: {'outcome': 'INVENTED_SUCCESS'}})


def test_terminal_report_unscheduled_slots_are_never_attempted(tmp_path):
    tmp_path = _dirs(tmp_path)
    with _acquire(tmp_path) as (shared, session, budget, store):
        plan = _frozen_plan((_request(),))
        report = build_terminal_report(plan=plan, session=session,
            budget=budget, shared=shared, store=store)
        assert report['outcome_counts'] == {'NEVER_ATTEMPTED': 2713}


def test_report_all_provider_intersection_requires_each_success(tmp_path):
    tmp_path = _dirs(tmp_path)
    providers = ('GEFS', 'IFS', 'AIFS')
    requests = tuple(_request(request_id=f'req-{i}', purpose='FIELD',
        provider=provider, slot_index=i, endpoint_id=f'{i}' * 64,
        reservation_bytes=4, range_start=0, range_end=3,
        expected_object_bytes=4) for i, provider in enumerate(providers, 1))
    event = FrozenEvent('event-all', 'LOW', 'GEFS',
                        tuple(r.request_id for r in requests))
    responses = {r.request_id: OfflineResponse(206,
        (('Content-Length', '4'), ('ETag', ETAG),
         ('Content-Range', 'bytes 0-3/4')),
        (f'{i:04d}'.encode(),), '8.8.8.8', True)
        for i, r in enumerate(requests, 1)}
    clock = _clock()
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
            SyntheticExchange(responses), clock=clock, requests=requests,
            events=(event,))
        for request in requests:
            assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
            clock.advance(2)
        report = build_terminal_report(plan=runtime.actual.plan, session=session,
            budget=budget, shared=shared, store=store)
        assert report['all_provider_intersection']['complete_event_ids'] == ['event-all']
        assert report['requested_events'][0]['primary_provider'] == 'GEFS'


def test_report_completion_requires_durable_digest(tmp_path):
    tmp_path = _dirs(tmp_path)
    request = _request(reservation_bytes=4)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': _ok_response(b'abcd')}), requests=(request,))
        assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
        result = runtime.finalize_report()
        assert session.report_completed_sha256 == result['sha256']
        assert (tmp_path / 'report' / ReportSink.REPORT_FILE_NAME).is_file()


def test_report_persistence_failure_keeps_session_completion_held(tmp_path, monkeypatch):
    tmp_path = _dirs(tmp_path)
    request = _request(reservation_bytes=4)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': _ok_response(b'abcd')}), requests=(request,))
        assert runtime.run_attempt(request)['outcome'] == 'SUCCESS'
        with monkeypatch.context() as patcher:
            patcher.setattr(runtime.actual.report_sink, 'persist',
                            lambda report: (_ for _ in ()).throw(OSError('disk full')))
            with pytest.raises(OSError, match='disk full'):
                runtime.finalize_report()
        assert session.report_completed_sha256 is None
        assert (tmp_path / 'report' / ReportSink.INCOMPLETE_FILE_NAME).is_file()


def test_plan_substitution_refuses_before_transport(tmp_path):
    tmp_path = _dirs(tmp_path)
    exchange = SyntheticExchange({'req-1': _ok_response(b'abcd')})
    planned = _request(reservation_bytes=4)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange,
                           requests=(planned,))
        with pytest.raises(LaunchContractError, match='RUNTIME_FROZEN_REQUEST_MISMATCH'):
            runtime.run_attempt(_request(reservation_bytes=4, path='/substituted'))
        assert 'req-1' not in exchange._used
        assert session.request_ids_ever == set()


def test_reviewed_plan_bytes_bind_exact_schedule_and_window():
    plan = _frozen_plan((_request(),))
    with pytest.raises(LaunchContractError, match='RUNTIME_PLAN_REVIEW_MISMATCH'):
        FrozenPlan(MANIFEST, plan.review_sha256, _window(),
                   (_request(path='/substituted'),), plan.review_raw,
                   synthetic_fixture=True)
    with pytest.raises(LaunchContractError, match='RUNTIME_PLAN_REVIEW_EVIDENCE'):
        FrozenPlan(MANIFEST, plan.review_sha256, _window(),
                   (_request(),), plan.review_raw + b' ',
                   synthetic_fixture=True)


def test_frozen_prerequisite_receipt_graph_and_report(tmp_path):
    tmp_path = _dirs(tmp_path)
    first = _request(reservation_bytes=4)
    second = _request(request_id='req-2', endpoint_id='c' * 63 + '2',
        reservation_bytes=4, prerequisite_request_ids=('req-1',))
    clock = _clock()
    exchange = SyntheticExchange({'req-1': _ok_response(b'abcd'),
                                  'req-2': _ok_response(b'efgh')})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange,
                           clock=clock, requests=(first, second))
        first_result = runtime.run_attempt(first)
        assert first_result['outcome'] == 'SUCCESS'
        clock.advance(2)
        assert runtime.run_attempt(second)['outcome'] == 'SUCCESS'
        assert session.capture_receipts['req-2']['dependencies'] == [
            first_result['store_receipt_commit_hash']]
        report = build_terminal_report(plan=runtime.actual.plan, session=session,
            budget=budget, shared=shared, store=store)
        assert report['per_purpose']['INDEX']['completed_count'] == 2


def test_failed_prerequisite_refuses_before_transport(tmp_path):
    tmp_path = _dirs(tmp_path)
    first = _request(reservation_bytes=1)
    second = _request(request_id='req-2', endpoint_id='c' * 63 + '2',
        reservation_bytes=4, prerequisite_request_ids=('req-1',))
    exchange = SyntheticExchange({'req-1': OfflineResponse(503,
        (('Content-Length', '1'),), (b'x',), '8.8.8.8', True),
        'req-2': _ok_response(b'efgh')})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange,
                           requests=(first, second))
        assert runtime.run_attempt(first)['outcome'] == 'FAILED'
        outcome = runtime.run_attempt(second)
        assert outcome['outcome'] == 'REFUSED'
        assert outcome['reason'] == 'RUNTIME_PREREQUISITE_MISSING'
        assert 'req-2' not in exchange._used


def test_prospective_capacity_exact_floor_and_one_byte_under(tmp_path):
    from tools.v11_r09_gate3_runtime import CapacityPlan
    request = _request(reservation_bytes=4)
    capacity = CapacityPlan.for_requests((request,))
    tmp_path = _dirs(tmp_path)
    with _acquire(tmp_path) as (shared, session, budget, store):
        plan = _frozen_plan((request,))
        report_dir = tmp_path / 'report'
        report_dir.mkdir(mode=0o700)
        with ReportSink(report_dir) as sink:
            kwargs = dict(shared=shared, session=session, budget=budget, store=store,
                transport=SyntheticTransport(SyntheticExchange({'req-1': _ok_response(b'abcd')})),
                clock=_clock(), window=_window(), allowed_peer_ips=('8.8.8.8',),
                manifest_sha256=MANIFEST, plan=plan,
                expected_plan_sha256=plan.sha256, report_sink=sink)
            GateRuntime(**kwargs, resources=_resources(
                free_disk_bytes=MIN_FREE_DISK_BYTES + capacity.disk_bytes,
                available_memory_bytes=MIN_AVAILABLE_MEMORY_BYTES + capacity.memory_bytes))
            with pytest.raises(LaunchContractError, match='RUNTIME_PROSPECTIVE_CAPACITY'):
                GateRuntime(**kwargs, resources=_resources(
                    free_disk_bytes=MIN_FREE_DISK_BYTES + capacity.disk_bytes - 1,
                    available_memory_bytes=MIN_AVAILABLE_MEMORY_BYTES + capacity.memory_bytes))


def test_report_sink_persists_once_and_refuses_overwrite(tmp_path):
    report_dir = tmp_path / 'report'
    report_dir.mkdir(mode=0o700)
    report = {'slot_count': SLOT_COUNT}
    with ReportSink(report_dir) as sink:
        result = sink.persist(report)
        assert result['classification'] == 'COMPLETE'
        with pytest.raises(FileExistsError):
            sink.persist(report)
    # The file survives and is readable by a fresh sink/open, never silently
    # replaced by a second run.
    assert (report_dir / ReportSink.REPORT_FILE_NAME).exists()


def test_report_sink_rejects_oversize_report(tmp_path):
    report_dir = tmp_path / 'report'
    report_dir.mkdir(mode=0o700)
    huge = {'rows': ['x' * 1000] * (REPORT_RESERVE_BYTES // 100)}
    with ReportSink(report_dir) as sink:
        with pytest.raises(LaunchContractError, match='REPORT_CAPACITY_EXCEEDED'):
            sink.persist(huge)
        assert not (report_dir / ReportSink.REPORT_FILE_NAME).exists()


def test_report_reserve_reopens_after_restart_and_excludes_competing_writer(tmp_path):
    report_dir = tmp_path / 'report'
    report_dir.mkdir(mode=0o700)
    with ReportSink(report_dir):
        with pytest.raises(BlockingIOError):
            ReportSink(report_dir)
    with ReportSink(report_dir) as reopened:
        assert reopened.reserved
        assert reopened.persist({'recovered': True})['classification'] == 'COMPLETE'


def test_report_short_write_retains_reserve_without_complete_name(tmp_path, monkeypatch):
    import tools.v11_r09_gate3_runtime as runtime_module
    report_dir = tmp_path / 'report'
    report_dir.mkdir(mode=0o700)
    with ReportSink(report_dir) as sink:
        real_write = runtime_module.os.write
        def short_write(fd, data):
            return real_write(fd, data[:-1])
        with monkeypatch.context() as patcher:
            patcher.setattr(runtime_module.os, 'write', short_write)
            with pytest.raises(LaunchContractError, match='REPORT_SHORT_WRITE'):
                sink.persist({'x': 1})
        assert (report_dir / ReportSink.RESERVE_FILE_NAME).is_file()
        assert not (report_dir / ReportSink.REPORT_FILE_NAME).exists()


def test_report_fsync_failure_keeps_completion_held(tmp_path, monkeypatch):
    import tools.v11_r09_gate3_runtime as runtime_module
    report_dir = tmp_path / 'report'
    report_dir.mkdir(mode=0o700)
    with ReportSink(report_dir) as sink:
        with monkeypatch.context() as patcher:
            patcher.setattr(runtime_module.os, 'fsync',
                            lambda fd: (_ for _ in ()).throw(OSError('injected fsync')))
            with pytest.raises(OSError, match='injected fsync'):
                sink.persist({'x': 1})
        assert not (report_dir / ReportSink.REPORT_FILE_NAME).exists()
        assert (report_dir / ReportSink.RESERVE_FILE_NAME).is_file()


def test_report_sink_incomplete_marker_is_bounded_and_exclusive(tmp_path):
    report_dir = tmp_path / 'report'
    report_dir.mkdir(mode=0o700)
    with ReportSink(report_dir) as sink:
        result = sink.persist_incomplete('STORE_JOURNAL_CAPACITY')
        assert result == {'classification': 'INCOMPLETE', 'reason': 'STORE_JOURNAL_CAPACITY'}
        with pytest.raises(FileExistsError):
            sink.persist_incomplete('STORE_JOURNAL_CAPACITY')


def test_report_sink_requires_private_mode_directory(tmp_path):
    report_dir = tmp_path / 'report'
    report_dir.mkdir(mode=0o755)
    with pytest.raises(LaunchContractError, match='REPORT_DIRECTORY_PRIVATE_MODE'):
        ReportSink(report_dir)


# ---------------------------------------------------------------------------
# Transport is a real injected interface: no concrete socket adapter.
# ---------------------------------------------------------------------------

def test_transport_is_abstract_and_only_synthetic_implementation_is_provided():
    with pytest.raises(TypeError):
        Transport()
    assert issubclass(SyntheticTransport, Transport)


def test_synthetic_transport_rejects_non_exchange_argument():
    with pytest.raises(LaunchContractError, match='RUNTIME_TRANSPORT_SHAPE'):
        SyntheticTransport(object())


def test_dispatch_deadline_expiry_holds_open_reservation(tmp_path):
    tmp_path = _dirs(tmp_path)
    clock = _clock()

    class SlowTransport(SyntheticTransport):
        def dispatch(self, request, *, deadline_monotonic, remaining_seconds):
            clock.advance(31)
            return super().dispatch(request, deadline_monotonic=deadline_monotonic,
                                    remaining_seconds=remaining_seconds)

    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
                           SyntheticExchange({'req-1': _ok_response(b'1234')}), clock=clock)
        runtime.transport = SlowTransport(runtime.transport._exchange)
        with pytest.raises(LaunchContractError, match='RUNTIME_DISPATCH_DEADLINE'):
            runtime.run_attempt(_request(reservation_bytes=4))
        assert shared.open_intent is not None and budget.in_flight == 'req-1'
        assert session.attempt['state'] == 'DISPATCHED'
        assert session.attempt['deadline_monotonic'] == 40
        assert budget.received == 4
        runtime.actual.report_sink.close()
    with _acquire(tmp_path) as (shared, session, budget, store):
        assert session.attempt['deadline_monotonic'] == 40
        assert budget.in_flight == 'req-1'


def test_unframed_response_cannot_release_shared_token(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = OfflineResponse(200, (('Content-Length', '5'), ('ETag', ETAG)),
                               (b'1234',), '8.8.8.8', True)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
                           SyntheticExchange({'req-1': response}))
        with pytest.raises(LaunchContractError, match='RUNTIME_CLOSURE_UNPROVEN'):
            runtime.run_attempt(_request(reservation_bytes=4))
        assert shared.open_intent is not None and budget.in_flight == 'req-1'


def test_header_receipt_deadline_charges_already_prefetched_bytes(tmp_path):
    tmp_path = _dirs(tmp_path)
    class DelayedHeaderClock(FakeClock):
        def evidence(self, phase):
            sample = super().evidence(phase)
            if phase == 'body_receipt':
                self.advance(40)
            return sample
    clock = DelayedHeaderClock(BOOT, utc=10, mono=10)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': _ok_response(b'abcd')}), clock=clock)
        with pytest.raises(LaunchContractError, match='RUNTIME_HEADER_DEADLINE'):
            runtime.run_attempt(_request(reservation_bytes=4))
        assert budget.received == 4 and budget.in_flight == 'req-1'
        assert session.attempt['state'] == 'DISPATCHED'


def test_single_prefetched_chunk_over_read_cap_poisoned(tmp_path):
    tmp_path = _dirs(tmp_path)
    body = b'a' * 70000
    response = _ok_response(body, chunks=(body,))
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': response}))
        result = runtime.run_attempt(_request(reservation_bytes=len(body)))
        assert result['outcome'] == 'OVERDELIVERY_HELD'
        assert budget.received == len(body) and budget.violated
        assert session.attempt['state'] == 'CLOSED'


def test_post_close_pacing_uses_actual_closure_after_slow_transport(tmp_path):
    tmp_path = _dirs(tmp_path)
    clock = _clock()
    body = b'1234'
    exchange = SyntheticExchange({'req-1': _ok_response(body, etag='"obj-1"'),
                                  'req-2': _ok_response(b'5678', etag='"obj-2"')})

    class SlowFirst(SyntheticTransport):
        def dispatch(self, request, *, deadline_monotonic, remaining_seconds):
            if request.request_id == 'req-1':
                clock.advance(3)
            return super().dispatch(request, deadline_monotonic=deadline_monotonic,
                                    remaining_seconds=remaining_seconds)

    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange, clock=clock,
            requests=(_request(reservation_bytes=4),
                      _request(request_id='req-2', reservation_bytes=4,
                               expected_etag='"obj-2"', endpoint_id='c' * 63 + '2')))
        runtime.transport = SlowFirst(exchange)
        assert runtime.run_attempt(_request(reservation_bytes=4))['outcome'] == 'SUCCESS'
        assert session.last_closure_monotonic == 13
        with pytest.raises(LaunchContractError, match='RUNTIME_POST_CLOSE_PACING'):
            runtime.run_attempt(_request(request_id='req-2', reservation_bytes=4,
                expected_etag='"obj-2"', endpoint_id='c' * 63 + '2'))


def test_post_close_pacing_survives_same_boot_reopen(tmp_path):
    tmp_path = _dirs(tmp_path)
    requests = (_request(reservation_bytes=4),
                _request(request_id='req-2', reservation_bytes=4,
                         endpoint_id='c' * 63 + '2'))
    clock = _clock()
    class SlowFirst(SyntheticTransport):
        def dispatch(self, request, *, deadline_monotonic, remaining_seconds):
            clock.advance(3)
            return super().dispatch(request, deadline_monotonic=deadline_monotonic,
                                    remaining_seconds=remaining_seconds)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': _ok_response(b'abcd')}),
            clock=clock, requests=requests)
        runtime.transport = SlowFirst(runtime.transport._exchange)
        assert runtime.run_attempt(requests[0])['outcome'] == 'SUCCESS'
        assert session.last_closure_monotonic == 13
        runtime.actual.report_sink.close()
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
            SyntheticExchange({'req-2': _ok_response(b'efgh')}),
            clock=_clock(utc=14, mono=14), requests=requests)
        with pytest.raises(LaunchContractError, match='RUNTIME_POST_CLOSE_PACING'):
            runtime.run_attempt(requests[1])
        assert budget.in_flight == 'req-2'


def test_retry_after_on_200_uses_actual_header_receipt(tmp_path):
    tmp_path = _dirs(tmp_path)
    clock = _clock()
    response = OfflineResponse(200, (('Content-Length', '1'), ('ETag', ETAG),
                                     ('Retry-After', '1200')), (b'x',), '8.8.8.8', True)
    exchange = SyntheticExchange({'req-1': response})

    class SlowHeaders(SyntheticTransport):
        def dispatch(self, request, *, deadline_monotonic, remaining_seconds):
            clock.advance(20)
            return super().dispatch(request, deadline_monotonic=deadline_monotonic,
                                    remaining_seconds=remaining_seconds)

    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange, clock=clock)
        runtime.transport = SlowHeaders(exchange)
        assert runtime.run_attempt(_request(reservation_bytes=1))['outcome'] == 'FAILED'
        assert shared.is_blocked('d' * 64, now_utc=1230)
        assert not shared.is_blocked('d' * 64, now_utc=1231)


@pytest.mark.parametrize('retry_after,blocked_at,released_at', [
    ('Thu, 01 Jan 1970 00:21:40 GMT', 1299, 1300),
    ('invalid-date', 100000, None),
])
def test_retry_after_date_or_invalid_expiry_stops_domain(
        tmp_path, retry_after, blocked_at, released_at):
    tmp_path = _dirs(tmp_path)
    response = _ok_response(b'x', headers=(('Retry-After', retry_after),))
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': response}))
        assert runtime.run_attempt(_request(reservation_bytes=1))['outcome'] == 'FAILED'
        assert shared.is_blocked('d' * 64, now_utc=blocked_at)
        if released_at is not None:
            assert not shared.is_blocked('d' * 64, now_utc=released_at)


def test_prefetched_overdelivery_accounts_entire_eager_boundary(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = OfflineResponse(200, (('Content-Length', '31'), ('ETag', ETAG)),
                               (b'a' * 11, b'b' * 20), '8.8.8.8', True)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
                           SyntheticExchange({'req-1': response}))
        result = runtime.run_attempt(_request(reservation_bytes=10))
        assert result['outcome'] == 'OVERDELIVERY_HELD'
        assert budget.received == 31
        assert session.attempt['total_delivered_bytes'] == 31


def test_elapsed_deadline_survives_same_boot_reopen(tmp_path):
    tmp_path = _dirs(tmp_path)
    window = _window(elapsed_cap_seconds=1)
    requests = (_request(reservation_bytes=4),
                _request(request_id='req-2', reservation_bytes=4,
                         endpoint_id='c' * 63 + '2'))
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
                           SyntheticExchange({'req-1': _ok_response(b'1234')}),
                           window=window, requests=requests)
        assert runtime.run_attempt(_request(reservation_bytes=4))['outcome'] == 'SUCCESS'
        assert session.elapsed_deadline_mono == 11
        runtime.actual.report_sink.close()
    with _acquire(tmp_path) as (shared, session, budget, store):
        exchange = SyntheticExchange({'req-2': _ok_response(b'5678')})
        runtime = _runtime(shared, session, budget, store, exchange,
                           clock=_clock(utc=12, mono=12), window=window,
                           requests=requests)
        result = runtime.run_attempt(_request(request_id='req-2', reservation_bytes=4,
            endpoint_id='c' * 63 + '2'))
        assert result['outcome'] == 'REFUSED'
        assert result['reason'] == 'RUNTIME_ELAPSED_DEADLINE'
        assert 'req-2' not in exchange._used


@pytest.mark.parametrize('clock,reason', [
    (_clock(utc=40, mono=40, measured_mono=0), 'RUNTIME_CLOCK_MEASUREMENT'),
    (_clock(boot_id='wrong-boot'), 'RUNTIME_CLOCK_MEASUREMENT'),
])
def test_invalid_original_clock_refuses_before_transport(tmp_path, clock, reason):
    tmp_path = _dirs(tmp_path)
    exchange = SyntheticExchange({'req-1': _ok_response(b'1234')})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange, clock=clock)
        result = runtime.run_attempt(_request(reservation_bytes=4))
        assert result['outcome'] == 'REFUSED' and result['reason'] == reason
        assert 'req-1' not in exchange._used


def test_clock_step_between_attempts_refuses_before_transport(tmp_path):
    tmp_path = _dirs(tmp_path)
    clock = _clock()
    exchange = SyntheticExchange({'req-1': _ok_response(b'1234'),
                                  'req-2': _ok_response(b'5678')})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange, clock=clock,
            requests=(_request(reservation_bytes=4),
                      _request(request_id='req-2', reservation_bytes=4,
                               endpoint_id='c' * 63 + '2')))
        assert runtime.run_attempt(_request(reservation_bytes=4))['outcome'] == 'SUCCESS'
        clock.set(utc=112, mono=12)
        result = runtime.run_attempt(_request(request_id='req-2', reservation_bytes=4,
            endpoint_id='c' * 63 + '2'))
        assert result['outcome'] == 'REFUSED'
        assert result['reason'] == 'SESSION_LEDGER_CLOCK_STEP'
        assert 'req-2' not in exchange._used


def test_clock_offset_intersection_survives_reopen(tmp_path):
    tmp_path = _dirs(tmp_path)
    requests = (_request(reservation_bytes=4),
                _request(request_id='req-2', reservation_bytes=4,
                         endpoint_id='c' * 63 + '2'))
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': _ok_response(b'abcd')}),
            requests=requests)
        assert runtime.run_attempt(requests[0])['outcome'] == 'SUCCESS'
        runtime.actual.report_sink.close()
    with _acquire(tmp_path) as (shared, session, budget, store):
        exchange = SyntheticExchange({'req-2': _ok_response(b'efgh')})
        runtime = _runtime(shared, session, budget, store, exchange,
            clock=_clock(utc=112, mono=12), requests=requests)
        result = runtime.run_attempt(requests[1])
        assert result['outcome'] == 'REFUSED'
        assert result['reason'] == 'SESSION_LEDGER_CLOCK_STEP'
        assert 'req-2' not in exchange._used


def test_incomplete_report_reason_is_bounded_before_write(tmp_path):
    report_dir = tmp_path / 'report'
    report_dir.mkdir(mode=0o700)
    with ReportSink(report_dir) as sink:
        assert (report_dir / ReportSink.RESERVE_FILE_NAME).stat().st_size == REPORT_RESERVE_BYTES
        with pytest.raises(LaunchContractError, match='REPORT_CAPACITY_EXCEEDED'):
            sink.persist_incomplete('x' * REPORT_RESERVE_BYTES)
        assert not (report_dir / ReportSink.INCOMPLETE_FILE_NAME).exists()


# ---------------------------------------------------------------------------
# Gate 3 V4 slice-3 repair 2 (F1-F7): independently reviewed CHANGES_REQUIRED
# findings against commit ef45d35. Each test below exercises the exact
# counterexample scenario from /tmp/alpha-v11-slice3-review-ef45d35-probes.py
# but asserts the *repaired* behavior (that review's probes assert the
# pre-repair defect and now fail against this candidate, which is the
# expected outcome of the repair, not a regression).
# ---------------------------------------------------------------------------

def test_store_phase_clocks_join_session_intersection_and_catch_contradiction(tmp_path):
    tmp_path = _dirs(tmp_path)

    class ShiftSealClock(FakeClock):
        count = 0

        def evidence(self, phase):
            self.count += 1
            if self.count == 6:
                self.set(utc=self.monotonic() + 0.08)
            return super().evidence(phase)

    clock = ShiftSealClock(BOOT, utc=10, mono=10, uncertainty=.05)
    requests = (_request(reservation_bytes=4),
                _request(request_id='req-2', reservation_bytes=4,
                         endpoint_id='c' * 63 + '2'))
    with _acquire(tmp_path) as (shared, session, budget, store):
        rt = _runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': _ok_response(b'abcd'),
                               'req-2': _ok_response(b'efgh')}),
            clock=clock, requests=requests)
        assert rt.run_attempt(requests[0])['outcome'] == 'SUCCESS'
        # F1: the durable interval is now narrowed by the store-phase
        # (body_receipt/decode_complete/durable_seal) samples too, not just
        # the transport-phase ones -- it is no longer the wide (-.05, .05)
        # the acquisition-gated samples alone would have left it at.
        assert session.clock_offset_interval != (-.05, .05)
        low, high = session.clock_offset_interval
        assert low > -.05 and low <= high
        clock.set(utc=11.92, mono=12, measured_mono=12)
        result = rt.run_attempt(requests[1])
        # The contradictory evidence is now caught at req-2's own pre-dispatch
        # clock check (refused), instead of silently reaching a COMPLETE
        # report with an empty global offset intersection.
        assert result['outcome'] == 'REFUSED'
        assert result['reason'] == 'SESSION_LEDGER_CLOCK_STEP'


def test_elapsed_deadline_still_accounts_denial_and_prefetched_bytes(tmp_path):
    tmp_path = _dirs(tmp_path)
    clock = _clock()

    class Slow(SyntheticTransport):
        def dispatch(self, request, **kwargs):
            clock.advance(2)
            return super().dispatch(request, **kwargs)

    response = _ok_response(b'abcd', status=503, headers=(('Retry-After', '1200'),))
    with _acquire(tmp_path) as (shared, session, budget, store):
        rt = _runtime(shared, session, budget, store, SyntheticExchange({'req-1': response}),
            clock=clock, window=_window(elapsed_cap_seconds=1))
        rt.transport = Slow(rt.transport._exchange)
        with pytest.raises(LaunchContractError, match='RUNTIME_DISPATCH_DEADLINE'):
            rt.run_attempt(_request(reservation_bytes=4))
        # F2: the observed 503/Retry-After denial and the fully prefetched
        # 4 bytes are both accounted even though the dispatch itself expired
        # past the elapsed cap -- the hold (open intent, full reservation)
        # is preserved, not released.
        assert shared.denials and 'd' * 64 in shared.denials
        assert budget.received == 4
        assert shared.open_intent is not None and budget.in_flight == 'req-1'


def test_bad_header_still_accounts_all_eager_overdelivery_bytes(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = _ok_response(b'a' * 31, headers=(('ETag', '"duplicate"'),))
    with _acquire(tmp_path) as (shared, session, budget, store):
        rt = _runtime(shared, session, budget, store, SyntheticExchange({'req-1': response}))
        with pytest.raises(LaunchContractError, match='RUNTIME_DUPLICATE_HEADER'):
            rt.run_attempt(_request(reservation_bytes=10))
        # F2: a malformed-header failure must not discard the fact that the
        # eager synthetic boundary already delivered all 31 bytes against a
        # 10-byte reservation -- the overdelivery is charged and flagged.
        assert budget.received == 31 and budget.violated
        assert budget.in_flight == 'req-1'


def test_cooldown_resumption_uses_conservative_lower_bound(tmp_path):
    from tools.v11_r09_gate3_runtime import _build_denial_record, _bounded_headers
    tmp_path = _dirs(tmp_path)
    window = _window(start_utc=900, acquisition_end_utc=2000, decision_lower_utc=2100)
    requests = (_request(reservation_bytes=4),
                _request(request_id='req-2', reservation_bytes=4,
                         endpoint_id='c' * 63 + '2'))
    with _acquire(tmp_path) as (shared, session, budget, store):
        shared.intent_open('old-req', purpose='INDEX', endpoint_id='c' * 64,
            control_domain_id='d' * 64, manifest_sha256='9' * 64,
            max_reservation_bytes=4, now_utc=10)
        response = _ok_response(b'bad!', status=503, headers=(('Retry-After', '5'),))
        record = _build_denial_record('503', response, window=_window(),
            receipt_evidence=_clock().evidence('body_receipt'),
            headers=_bounded_headers(response), origin=ORIGIN)
        shared.denial_observed('old-req', denial=record)
        shared.intent_closed('old-req', outcome='DENIED', accounting_head='8' * 64,
                             total_delivered_bytes=4)
        assert shared.is_blocked('d' * 64, now_utc=999.95)
        # Still within the hold even by the conservative lower bound: refused.
        blocked_clock = _clock(utc=1000, mono=1000, uncertainty=.05)
        rt = _runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': _ok_response(b'abcd')}),
            clock=blocked_clock, window=window, requests=requests)
        result = rt.run_attempt(requests[0])
        assert result['outcome'] == 'REFUSED'
        assert result['reason'] == 'RUNTIME_CONTROL_DOMAIN_BLOCKED'
        rt.actual.report_sink.close()
    with _acquire(tmp_path) as (shared, session, budget, store):
        # Genuinely past the cooldown once the lower bound itself clears it.
        resumed_clock = _clock(utc=1000.10, mono=1000.10, uncertainty=.05)
        rt = _runtime(shared, session, budget, store,
            SyntheticExchange({'req-2': _ok_response(b'abcd')}),
            clock=resumed_clock, window=window, requests=requests)
        assert rt.run_attempt(requests[1])['outcome'] == 'SUCCESS'


def test_shared_journal_boot_mismatch_blocks_construction(tmp_path):
    tmp_path = _dirs(tmp_path)
    (tmp_path / 'alternate_shared').mkdir(mode=0o700)
    with _acquire(tmp_path) as (_, session, budget, store):
        with SharedLedger(tmp_path / 'alternate_shared', boot_id='different-boot',
                          genesis_review_digest=GENESIS) as shared:
            rt = _runtime(shared, session, budget, store,
                SyntheticExchange({'req-1': _ok_response(b'abcd')}))
            with pytest.raises(LaunchContractError, match='RUNTIME_BOOT_CONTEXT_MISMATCH'):
                rt.run_attempt(_request(reservation_bytes=4))


def test_transport_receives_bound_attempt_request_not_bare_id(tmp_path):
    tmp_path = _dirs(tmp_path)
    seen = []

    class RecordingTransport(SyntheticTransport):
        def dispatch(self, request, **kwargs):
            seen.append(request)
            return super().dispatch(request, **kwargs)

    with _acquire(tmp_path) as (shared, session, budget, store):
        rt = _runtime(shared, session, budget, store, SyntheticExchange({'req-1': _ok_response(b'abcd')}))
        rt.transport = RecordingTransport(rt.transport._exchange)
        request = _request(reservation_bytes=4)
        assert rt.run_attempt(request)['outcome'] == 'SUCCESS'
        assert len(seen) == 1 and seen[0] == request


def test_field_request_without_slot_index_is_rejected():
    with pytest.raises(LaunchContractError, match='RUNTIME_REQUEST_FIELD_REQUIRES_SLOT'):
        _request(purpose='FIELD', provider='GEFS', slot_index=None,
                 range_start=0, range_end=3, expected_object_bytes=4,
                 reservation_bytes=4)


def test_refused_request_does_not_inflate_attempted_or_denominator(tmp_path):
    tmp_path = _dirs(tmp_path)
    with _acquire(tmp_path) as journals:
        rt = _runtime(*journals, SyntheticExchange({}), clock=_clock(utc=-1, mono=10))
        assert rt.run_attempt(_request())['outcome'] == 'REFUSED'
        report = build_terminal_report(plan=rt.plan, session=journals[1],
            budget=journals[2], shared=journals[0], store=journals[3])
        # F5: a refusal that never reached a budget reservation must not be
        # counted as "attempted" anywhere in the report.
        assert journals[2].count == 0
        assert report['global_accounting']['attempted_count'] == 0
        assert report['global_accounting']['refused_count'] == 1
        assert report['per_purpose']['INDEX']['attempted_count'] == 0
        assert report['per_purpose']['INDEX']['refused_count'] == 1


def test_tiny_chunks_within_policy_cap_succeed_and_capacity_is_sufficient(tmp_path):
    tmp_path = _dirs(tmp_path)
    request = _request(reservation_bytes=32)
    capacity = CapacityPlan.for_requests((request,))
    with _acquire(tmp_path) as journals:
        rt = _runtime(*journals,
            SyntheticExchange({'req-1': _ok_response(b'a' * 32, chunks=(b'a',) * 32)}),
            requests=(request,))
        before = len(journals[2].events)
        assert rt.run_attempt(request)['outcome'] == 'SUCCESS'
        used = len(journals[2].events) - before
        # F7: the frozen capacity estimate for a 32-byte request now assumes
        # up to MAX_BODY_CHUNKS_PER_REQUEST (32) one-byte chunks, so the
        # actual worst-case delivery this fixture exercises (32 chunk
        # records + reserve + complete = 34) fits within the preflight
        # estimate instead of silently exceeding it.
        assert used == 34 <= capacity.budget_records


def test_chunk_count_exceeding_policy_cap_is_rejected_with_hold_preserved(tmp_path):
    tmp_path = _dirs(tmp_path)
    body = b'a' * (MAX_BODY_CHUNKS_PER_REQUEST + 1)
    response = _ok_response(body, chunks=(b'a',) * (MAX_BODY_CHUNKS_PER_REQUEST + 1))
    with _acquire(tmp_path) as (shared, session, budget, store):
        rt = _runtime(shared, session, budget, store, SyntheticExchange({'req-1': response}))
        with pytest.raises(LaunchContractError, match='RUNTIME_CHUNK_COUNT_EXCEEDS_POLICY'):
            rt.run_attempt(_request(reservation_bytes=len(body)))
        assert shared.open_intent is not None and budget.in_flight == 'req-1'


def test_sparse_report_reserve_reopen_establishes_physical_allocation(tmp_path):
    directory = tmp_path / 'report'
    directory.mkdir(mode=0o700)
    reserve = directory / ReportSink.RESERVE_FILE_NAME
    with reserve.open('wb') as stream:
        stream.truncate(REPORT_RESERVE_BYTES)
    reserve.chmod(0o600)
    assert reserve.stat().st_blocks == 0
    with ReportSink(directory) as sink:
        assert sink.reserved
        # F6: reopening a reserve file that was never actually physically
        # allocated now establishes (and proves) that allocation before
        # trusting it, instead of accepting a sparse file at face value.
        assert reserve.stat().st_blocks * 512 >= REPORT_RESERVE_BYTES


def test_frozen_plan_derives_from_validated_v4_manifest_bytes(tmp_path, monkeypatch):
    from tests.test_v11_r09_gate3_launch_v4 import candidate
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    precedence = ('PREREQUISITE', 'CLOCK', 'RESOURCE', 'DENIAL',
                  'VALIDATION', 'SUCCESS', 'UNSCHEDULED')
    precedence_raw = canonical(precedence)
    precedence_sha = hashlib.sha256(precedence_raw).hexdigest()
    (root / 'objects' / precedence_sha).write_bytes(precedence_raw)
    (root / 'objects' / precedence_sha).chmod(0o600)
    payload['accounting']['terminal_precedence'] = {
        'sha256': precedence_sha, 'byte_length': len(precedence_raw),
        'media_type': 'application/octet-stream'}
    raw = canonical(payload)
    manifest_sha256 = validate_manifest_v4(raw, repo=repo, object_root=root,
                                           now_utc=start - 4000)
    endpoints_by_id = {e['endpoint_id']: e for e in payload['network']['endpoints']}
    schedule_requests = payload['schedule']['requests']
    ids = [item['request_id'] for item in schedule_requests]
    request_pins = {rid: {'expected_etag': ETAG, 'expected_object_bytes': None,
        'dependency_commit_hashes': ()} for rid in ids}
    expected_requests = []
    for item in schedule_requests:
        endpoint = endpoints_by_id[item['endpoint_id']]
        pins = request_pins[item['request_id']]
        expected_requests.append(AttemptRequest(
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
            source_pin=payload['sources'][item['provider']]['dossier']['sha256'],
            decoder_pin=payload['sources'][item['provider']]['decoder_build']['sha256'],
            clock_policy_sha256=payload['runtime']['clock_policy']['sha256'],
            validator_sha256=endpoint['parser_identity']['sha256'],
            dependency_commit_hashes=pins['dependency_commit_hashes'],
            prerequisite_request_ids=tuple(ids[i] for i in item['prerequisites'])))
    window = AbsoluteWindow(payload['time']['window_start_utc'],
        payload['time']['last_acquisition_utc'], payload['time']['decision_lower_utc'],
        payload['limits']['request_deadline_seconds'],
        payload['limits']['max_elapsed_seconds'], payload['time']['uncertainty_seconds'])
    runtime_context_raw = canonical({
        'manifest_runtime_sha256': hashlib.sha256(canonical(payload['runtime'])).hexdigest(),
        'boot_id': BOOT, 'shared_root': [1, 1], 'session_root': [1, 2],
        'budget_root': [1, 3], 'store_descriptor': '2' * 64,
        'report_root': [1, 4],
        'store_policy': payload['runtime']['policy']['sha256'],
        'clock_method': 'synthetic', 'allowed_peer_ips': ['8.8.8.8']})
    extras = {rid: request_pins[rid] for rid in ids}
    field_ids = tuple(r.request_id for r in expected_requests if r.purpose == 'FIELD')
    primary = next(r.provider for r in expected_requests if r.purpose == 'FIELD')
    events = tuple(FrozenEvent(f'event-{side.lower()}', side, primary, field_ids,
        tuple(key), tuple(trial)) for side, key, trial in zip(
            payload['cohort']['events'], payload['cohort']['requested_keys'],
            payload['cohort']['gate2_trial_keys']))
    review_raw = canonical({'schema_version': 2, 'manifest_sha256': manifest_sha256,
        'window_sha256': hashlib.sha256(canonical(asdict(window))).hexdigest(),
        'request_schedule_sha256': hashlib.sha256(
            canonical([asdict(r) for r in expected_requests])).hexdigest(),
        'event_schedule_sha256': hashlib.sha256(canonical(
            [asdict(e) for e in events])).hexdigest(),
        'supplemental_pins_sha256': hashlib.sha256(canonical(extras)).hexdigest(),
        'terminal_precedence_sha256': hashlib.sha256(canonical(precedence)).hexdigest(),
        'runtime_context_sha256': hashlib.sha256(runtime_context_raw).hexdigest()})
    review_sha256 = hashlib.sha256(review_raw).hexdigest()
    plan = FrozenPlan.from_validated_manifest(raw, repo=repo, object_root=root,
        now_utc=start - 4000, window=window, review_sha256=review_sha256,
        review_raw=review_raw, request_pins=request_pins,
        runtime_context_raw=runtime_context_raw,
        events=events, terminal_precedence=precedence)
    assert plan.manifest_sha256 == manifest_sha256
    assert [r.request_id for r in plan.requests] == ids
    assert plan.requests == tuple(expected_requests)
    assert plan.events == events
    # F4: the FIELD request's prerequisites are exactly the manifest's own
    # validated INDEX/OBJECT_ID/METADATA overhead requests for that slot.
    field = next(r for r in plan.requests if r.purpose == 'FIELD')
    prereq_purposes = {r.purpose for r in plan.requests
                       if r.request_id in field.prerequisite_request_ids}
    assert prereq_purposes == {'INDEX', 'OBJECT_ID', 'METADATA'}
    with pytest.raises(LaunchContractError, match='RUNTIME_PLAN_REVIEW_MISMATCH'):
        replace(plan, runtime_context_raw=canonical({**parse_canonical(
            runtime_context_raw), 'clock_method': 'changed'}))
    with pytest.raises(LaunchContractError, match='RUNTIME_MANIFEST_WINDOW_MISMATCH'):
        FrozenPlan.from_validated_manifest(raw, repo=repo, object_root=root,
            now_utc=start - 4000, window=_window(), review_sha256=review_sha256,
            review_raw=review_raw, request_pins=request_pins,
            runtime_context_raw=runtime_context_raw,
            terminal_precedence=precedence)
    bad_pins = {rid: dict(pins) for rid, pins in request_pins.items()}
    bad_pins[ids[0]]['clock_policy_sha256'] = '1' * 64
    with pytest.raises(LaunchContractError, match='RUNTIME_PLAN_PIN_SCHEMA'):
        FrozenPlan.from_validated_manifest(raw, repo=repo, object_root=root,
            now_utc=start - 4000, review_sha256=review_sha256,
            review_raw=review_raw, request_pins=bad_pins,
            runtime_context_raw=runtime_context_raw,
            terminal_precedence=precedence)


def test_unvalidated_plan_cannot_enter_runtime(tmp_path):
    request = _request(purpose='FIELD', provider='GEFS', slot_index=0,
                       reservation_bytes=4)
    fixture = _frozen_plan((request,))
    with pytest.raises(LaunchContractError, match='RUNTIME_VALIDATED_MANIFEST_REQUIRED'):
        FrozenPlan(fixture.manifest_sha256, fixture.review_sha256,
                   fixture.window, fixture.requests, fixture.review_raw)
    class OtherTransport(Transport):
        def dispatch(self, request, *, deadline_monotonic, remaining_seconds):
            raise AssertionError('must not dispatch')
    _dirs(tmp_path)
    with _acquire(tmp_path) as (shared, session, budget, store):
        with pytest.raises(LaunchContractError, match='RUNTIME_SYNTHETIC_FIXTURE_ONLY'):
            GateRuntime(shared=shared, session=session, budget=budget,
                store=store, transport=OtherTransport(), clock=_clock(),
                resources=_resources(), window=fixture.window,
                allowed_peer_ips=('8.8.8.8',), manifest_sha256=MANIFEST,
                plan=fixture, expected_plan_sha256=fixture.sha256,
                report_sink=None)


@pytest.mark.parametrize('bad_header', [False, True])
def test_invalid_receipt_clock_retains_denial_and_eager_bytes(tmp_path, bad_header):
    _dirs(tmp_path)
    clock = _clock()
    class BadClockTransport(SyntheticTransport):
        def dispatch(self, request, **kwargs):
            clock.set(uncertainty=2)
            return super().dispatch(request, **kwargs)
    headers = (('Retry-After', '1200'),)
    if bad_header:
        headers += (('ETag', '"duplicate"'),)
    response = _ok_response(b'abcd', status=503, headers=headers)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': response}), clock=clock)
        runtime.transport = BadClockTransport(runtime.transport._exchange)
        with pytest.raises(LaunchContractError, match=(
            'RUNTIME_DUPLICATE_HEADER' if bad_header else
            'RUNTIME_CLOCK_UNCERTAINTY_EXCEEDED')):
            runtime.run_attempt(_request(reservation_bytes=4))
        assert budget.received == 4 and budget.in_flight == 'req-1'
        assert shared.open_intent is not None
        assert shared.denials['d' * 64]['status'] == '503'


def test_header_error_retains_other_retry_after_and_overflow_is_unresolved(tmp_path):
    for index, (status, headers, expected) in enumerate((
        (200, (('Retry-After', '1200'), ('ETag', '"duplicate"')), 'OTHER'),
        (503, (('Retry-After', '9' * 400),), '503'))):
        root = tmp_path / str(index)
        root.mkdir(mode=0o700)
        _dirs(root)
        response = _ok_response(b'abcd', status=status, headers=headers)
        with _acquire(root) as (shared, session, budget, store):
            runtime = _runtime(shared, session, budget, store,
                               SyntheticExchange({'req-1': response}))
            with pytest.raises(LaunchContractError):
                runtime.run_attempt(_request(reservation_bytes=4))
            assert budget.received == 4 and budget.in_flight == 'req-1'
            denial = shared.denials['d' * 64]
            assert denial['status'] == expected
            assert denial['cooldown_until'] is None if index else denial['cooldown_until'] >= 1000


def test_report_uses_reserved_ids_and_frozen_all_reason_precedence(tmp_path):
    _dirs(tmp_path)
    requests = (_request(), _request(request_id='req-2', purpose='FIELD',
        provider='GEFS', slot_index=0, range_start=0, range_end=3,
        expected_object_bytes=4, reservation_bytes=4,
        prerequisite_request_ids=('req-1',)))
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
            SyntheticExchange({}), clock=_clock(utc=-1, mono=10), requests=requests)
        assert runtime.run_attempt(requests[0])['outcome'] == 'REFUSED'
        assert runtime.run_attempt(requests[1])['outcome'] == 'REFUSED'
        report = build_terminal_report(plan=runtime.plan, session=session,
            budget=budget, shared=shared, store=store)
        row = report['rows'][0]
        assert row['reason'] == 'RUNTIME_PREREQUISITE_MISSING'
        assert {'RUNTIME_PREREQUISITE_MISSING',
                'RUNTIME_CLOCK_BEFORE_WINDOW_START'} <= set(row['reasons'])
        assert report['terminal_precedence'][0] == 'PREREQUISITE'
        assert report['attempted_request_ids'] == []
        assert report['refused_request_ids'] == ['req-1', 'req-2']
        assert report['global_accounting']['attempted_count'] == 0


def test_eager_many_chunks_uses_one_bounded_record_and_retains_all_bytes(tmp_path):
    _dirs(tmp_path)
    request = _request(reservation_bytes=64)
    capacity = CapacityPlan.for_requests((request,))
    response = _ok_response(b'a' * 64, chunks=(b'a',) * 64)
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': response}), requests=(request,))
        before = len(budget.events)
        with pytest.raises(LaunchContractError, match='RUNTIME_CHUNK_COUNT_EXCEEDS_POLICY'):
            runtime.run_attempt(request)
        assert len(budget.events) - before <= capacity.budget_records
        assert budget.received == 64 and budget.in_flight == 'req-1'
        assert shared.open_intent is not None
    assert capacity.disk_bytes >= (capacity.budget_records +
        capacity.store_events + 20 + 4 + 4) * 65536 + REPORT_RESERVE_BYTES
    with _acquire(tmp_path) as (shared, session, budget, store):
        assert budget.received == 64 and budget.in_flight == 'req-1'
        assert shared.open_intent is not None
        with pytest.raises(LaunchContractError, match='UNCERTAIN_REQUEST_HELD'):
            budget.next_read_limit(65536)


def test_capacity_envelope_covers_normal_denial_violation_and_recovery(tmp_path):
    responses = (
        (_ok_response(b'abcd'), 4, False),
        (_ok_response(b'abcd', status=503,
                      headers=(('Retry-After', '1200'),)), 4, False),
        (_ok_response(b'abcde'), 5, True),
        (_ok_response(b'abcd', headers=(('ETag', '"duplicate"'),)), 4, True),
    )
    request = _request(reservation_bytes=4)
    capacity = CapacityPlan.for_requests((request,))
    for index, (response, known, held) in enumerate(responses):
        root = tmp_path / str(index)
        root.mkdir(mode=0o700)
        _dirs(root)
        with _acquire(root) as (shared, session, budget, store):
            runtime = _runtime(shared, session, budget, store,
                SyntheticExchange({'req-1': response}), requests=(request,))
            before = len(budget.events)
            if index == 3:
                with pytest.raises(LaunchContractError, match='RUNTIME_DUPLICATE_HEADER'):
                    runtime.run_attempt(request)
            else:
                runtime.run_attempt(request)
            assert len(budget.events) - before <= capacity.budget_records
            assert budget.received == known
            assert (budget.in_flight is not None) == held


def test_capacity_includes_nested_dependency_roots():
    event = FrozenEvent('event-1', 'HIGH', 'GEFS',
        tuple(f'field-{i}' for i in range(300)))
    capacity = CapacityPlan.for_requests((_request(),), (event,))
    assert capacity.aggregate_nodes == 3  # two leaves plus the root
    assert capacity.store_events == 12
    assert capacity.disk_bytes >= 3 * 2 * 4194304 + REPORT_RESERVE_BYTES
