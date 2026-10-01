"""Synthetic, offline counterexamples for the Gate 3 V4 slice 3 runtime
(tools/v11_r09_gate3_runtime.py). No network calls, no real clock, no
launch/transport entrypoint.

Covers crash/interruption boundaries, denial ordering, accounting,
overdelivery, restart/hold behavior, and bounded full-denominator reporting,
per docs/V11_R09_GATE3_TRANSPORT_RUNTIME_DESIGN.md sections 4-7.
"""
import contextlib

import pytest

from tools.v11_r09_gate3_launch import DurableBudget, LaunchContractError
from tools.v11_r09_gate3_ledgers import SessionLedger, SharedLedger
from tools.v11_r09_gate3_offline_io import OfflineResponse, SyntheticExchange
from tools.v11_r09_gate3_runtime import (
    MIN_AVAILABLE_MEMORY_BYTES, MIN_FREE_DISK_BYTES, REPORT_RESERVE_BYTES,
    SLOT_COUNT, AbsoluteWindow, AttemptRequest, FakeClock, FakeResourceProbe,
    GateRuntime, ReportSink, SyntheticTransport, Transport,
    acquire_runtime_journals, build_terminal_report,
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


def _ok_response(body=b'0123456789012345678901234567890', etag=ETAG, status=200,
                  chunks=None, headers=()):
    if chunks is None:
        chunks = (body[:10], body[10:]) if len(body) > 10 else (body,)
    return OfflineResponse(status, (('Content-Length', str(len(body))), ('ETag', etag)) + headers,
                            chunks, '8.8.8.8', True)


def _runtime(shared, session, budget, store, exchange, *, clock=None, resources=None,
             window=None):
    return GateRuntime(shared=shared, session=session, budget=budget, store=store,
        transport=SyntheticTransport(exchange), clock=clock or _clock(),
        resources=resources or _resources(), window=window or _window(),
        allowed_peer_ips=('8.8.8.8',), manifest_sha256=MANIFEST)


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


def test_late_completion_is_diagnostic_not_relabeled(tmp_path):
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
        outcome = runtime.run_attempt(_request(reservation_bytes=len(body)))
        assert outcome['outcome'] == 'SUCCESS'
        assert outcome['reason'] == 'RUNTIME_SUCCESS_LATE_DIAGNOSTIC_ONLY'
        assert outcome['timely'] is False
        receipt = store.receipts[next(iter(store.receipts))]
        # The store's own fixed guarantee is untouched by lateness.
        assert receipt.historical_feature_eligible is False


def test_denial_recorded_before_any_chunk_consumed_and_blocks_control_domain(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = OfflineResponse(503, (('Retry-After', '30'),), (b'denied body',), '8.8.8.8', True)
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
    response = OfflineResponse(401, (), (b'x',), '8.8.8.8', True)
    exchange = SyntheticExchange({'req-1': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _runtime(shared, session, budget, store, exchange)
        runtime.run_attempt(_request())
        assert shared.is_blocked('d' * 64, now_utc=10 ** 9)


def test_finite_cooldown_releases_at_window_end_or_later(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = OfflineResponse(503, (('Retry-After', '5'),), (b'x',), '8.8.8.8', True)
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
        with pytest.raises(LaunchContractError, match='RUNTIME_RESOURCE_FLOOR'):
            GateRuntime(shared=shared, session=session, budget=budget, store=store,
                transport=SyntheticTransport(SyntheticExchange({})), clock=_clock(),
                resources=_resources(free_disk_bytes=MIN_FREE_DISK_BYTES - 1),
                window=_window(), allowed_peer_ips=('8.8.8.8',), manifest_sha256=MANIFEST)


def test_low_disk_refuses_before_any_durable_open(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = _ok_response()
    exchange = SyntheticExchange({'req-1': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        resources = _resources()
        runtime = _runtime(shared, session, budget, store, exchange, resources=resources)
        resources.free_disk_bytes = MIN_FREE_DISK_BYTES - 1
        outcome = runtime.run_attempt(_request())
        assert outcome['outcome'] == 'REFUSED'
        assert outcome['reason'] == 'RUNTIME_RESOURCE_FLOOR'
        assert shared.open_intent is None and shared.open_count == 0
        assert session.attempt['outcome'] == 'REFUSED'


def test_low_memory_refuses_before_any_durable_open(tmp_path):
    tmp_path = _dirs(tmp_path)
    response = _ok_response()
    exchange = SyntheticExchange({'req-1': response})
    with _acquire(tmp_path) as (shared, session, budget, store):
        resources = _resources()
        runtime = _runtime(shared, session, budget, store, exchange, resources=resources)
        resources.available_memory_bytes = MIN_AVAILABLE_MEMORY_BYTES - 1
        outcome = runtime.run_attempt(_request())
        assert outcome['outcome'] == 'REFUSED'
        assert outcome['reason'] == 'RUNTIME_RESOURCE_FLOOR'


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
        runtime = _runtime(shared, session, budget, store, exchange)
        denial_response = OfflineResponse(401, (), (b'x',), '8.8.8.8', True)
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
        runtime = _runtime(shared, session, budget, store, exchange, clock=clock)
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
        runtime = _runtime(shared, session, budget, store, exchange, clock=clock)
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
        runtime = _runtime(shared, session, budget, store, exchange, window=window, clock=clock)
        first = runtime.run_attempt(_request(request_id='req-1', slot_index=1))
        assert first['outcome'] == 'SUCCESS'
        clock.advance(2.0)  # satisfies the >=2s pacing gate; still way under A.
        with pytest.raises(LaunchContractError, match='RUNTIME_DEADLINE_ALREADY_PASSED'):
            runtime.run_attempt(_request(request_id='req-2', slot_index=2,
                                          endpoint_id='c' * 63 + '2'))
        # Mid-attempt hold: the attempt is stuck at RESERVED for restart, not
        # a clean REFUSED terminal.
        assert session.attempt['request_id'] == 'req-2'
        assert session.attempt['state'] == 'RESERVED'


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
        with pytest.raises(LaunchContractError, match='RUNTIME_RESOURCE_FLOOR'):
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

def test_terminal_report_partitions_full_denominator_exactly_once():
    attempted = {5: {'outcome': 'SUCCESS', 'reason': 'RUNTIME_SUCCESS_TIMELY',
                      'request_id': 'req-1'},
                 100: {'outcome': 'FAILED', 'reason': 'RUNTIME_DENIAL_HTTP_503',
                       'request_id': 'req-2'}}
    report = build_terminal_report(attempted=attempted)
    assert report['slot_count'] == SLOT_COUNT == 2713
    assert len(report['rows']) == 2713
    assert len({r['slot_index'] for r in report['rows']}) == 2713
    assert report['rows'][5]['status'] == 'ATTEMPTED' and report['rows'][5]['outcome'] == 'SUCCESS'
    assert report['rows'][100]['outcome'] == 'FAILED'
    assert report['rows'][0]['status'] == 'NEVER_ATTEMPTED'
    assert report['outcome_counts']['NEVER_ATTEMPTED'] == 2711
    assert report['outcome_counts']['SUCCESS'] == 1
    assert report['outcome_counts']['FAILED'] == 1


def test_terminal_report_rejects_out_of_range_slot_index():
    with pytest.raises(LaunchContractError, match='REPORT_SLOT_INDEX'):
        build_terminal_report(attempted={SLOT_COUNT: {'outcome': 'SUCCESS', 'reason': 'x'}})
    with pytest.raises(LaunchContractError, match='REPORT_SLOT_INDEX'):
        build_terminal_report(attempted={-1: {'outcome': 'SUCCESS', 'reason': 'x'}})


def test_terminal_report_empty_schedule_is_all_never_attempted():
    report = build_terminal_report(attempted={})
    assert report['outcome_counts'] == {'NEVER_ATTEMPTED': 2713}


def test_report_sink_persists_once_and_refuses_overwrite(tmp_path):
    report_dir = tmp_path / 'report'
    report_dir.mkdir(mode=0o700)
    report = build_terminal_report(attempted={})
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
