"""Synthetic, offline tests for the mandatory attempt-model pre-dispatch gate
wired onto ``GateRuntime`` (tools/v11_r09_gate3_runtime.py's
``AttemptModelGuard``, bound to the independently reviewed offline Gate 3
attempt model in tools/v11_gate3_preflight_attempt_model.py).

No network calls, no real clock, no launch/transport entrypoint. Every
fixture is synthetic-only; nothing here grants or asserts
execution/provider/capture authority (those remain hardcoded ``False`` in
``ModelResult.__post_init__`` and are never touched by this wiring).

These tests prove, against the real ``GateRuntime.run_attempt`` entrypoint:
  * a runtime built without ``attempt_model`` is completely unaffected
    (backward compatibility for every existing caller/test);
  * a runtime built *with* a guard that the attempt model admits still
    dispatches normally;
  * a guard the model refuses blocks ``run_attempt`` before any durable
    session/shared/budget mutation -- even when every other precondition
    (resources, window, control-domain) would otherwise pass -- so the
    real launch path cannot bypass the attempt model;
  * the guard is single-shot: a second ``run_attempt`` call on a runtime
    whose guard already admitted once is refused outright, modeling the
    pilot's documented zero-retry contract ("duplicate starts are refused
    /held");
  * a request outside the model's bounded contract shape (not the
    ``INDEX``/``BODY_CAP`` pilot request) fails closed rather than
    silently skipping the gate.

The last section adds direct, non-runtime regression proof (using the
already-reviewed public ``tools.v11_gate3_preflight_attempt_model`` API and
the shared ``tests/v11_gate3_preflight_synthetic_cases.py`` fixtures) that
the two properties this guard's admission call depends on still hold:
clock/resource floor breaches refuse closed at admission, and a fault after
STARTED preserves the charged attempt (no refund). The guard itself only
calls ``admit_synthetic`` (a pre-dispatch admission check); it does not
drive the model's per-event ``step`` script, so these are checked against
the model directly rather than through ``GateRuntime``.
"""
from __future__ import annotations

import dataclasses
import hashlib

import pytest

from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_offline_io import OfflineResponse, SyntheticExchange
from tools.v11_r09_gate3_runtime import (
    AbsoluteWindow, AttemptModelGuard, AttemptRequest, FakeClock,
    FakeResourceProbe, FrozenPlan, GateRuntime, ReportSink, SyntheticTransport,
    acquire_runtime_journals,
)
from tools.v11_gate3_preflight_attempt_model import result as attempt_model_result
from tests.v11_gate3_preflight_synthetic_cases import (
    GOOD_CLOCK, GOOD_RESOURCES, drive, genesis_checkpoint, good_inputs,
)

MANIFEST = 'a' * 64
BOOT = 'boot-wire'
GENESIS = '7' * 64
ETAG = '"obj-1"'
ORIGIN = 'https://weather.example.invalid'


def _dirs(tmp_path):
    for name in ('shared', 'session', 'budget', 'store'):
        (tmp_path / name).mkdir(mode=0o700)
    (tmp_path / 'store' / 'objects').mkdir(mode=0o700)
    return tmp_path


def _acquire(tmp_path):
    return acquire_runtime_journals(
        shared_dir=tmp_path / 'shared', session_dir=tmp_path / 'session',
        budget_dir=tmp_path / 'budget', store_root=tmp_path / 'store',
        shared_kwargs=dict(boot_id=BOOT, genesis_review_digest=GENESIS),
        session_kwargs=dict(manifest_sha256=MANIFEST, boot_id=BOOT),
        budget_kwargs=dict(manifest_sha256=MANIFEST, max_bytes=1 << 20, boot_id=BOOT),
        store_kwargs=dict(manifest_sha256=MANIFEST, policy_sha256='b' * 64,
                           build_id='fixture', clock_method='synthetic',
                           max_clock_age_seconds=30, host_id='fixture-host',
                           boot_id=BOOT),
    )


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


def _frozen_plan(requests, window=None):
    window = window or _window()
    raw = canonical({'schema_version': 1, 'manifest_sha256': MANIFEST,
        'window_sha256': hashlib.sha256(canonical(dataclasses.asdict(window))).hexdigest(),
        'request_schedule_sha256': hashlib.sha256(
            canonical([dataclasses.asdict(r) for r in requests])).hexdigest(),
        'event_schedule_sha256': hashlib.sha256(canonical([])).hexdigest()})
    return FrozenPlan(MANIFEST, hashlib.sha256(raw).hexdigest(), window,
                      requests, raw, (), synthetic_fixture=True)


def _ok_response(body=b'0123456789012345678901234567890', etag=ETAG):
    chunks = (body[:10], body[10:]) if len(body) > 10 else (body,)
    return OfflineResponse(200, (('Content-Length', str(len(body))), ('ETag', etag)),
                            chunks, '8.8.8.8', True)


def _build_runtime(shared, session, budget, store, tmp_path, *, requests,
                    exchange, attempt_model=None, window=None):
    plan = _frozen_plan(requests, window)
    report_dir = tmp_path / 'report'
    report_dir.mkdir(mode=0o700, exist_ok=True)
    sink = ReportSink(report_dir)
    return GateRuntime(shared=shared, session=session, budget=budget, store=store,
        transport=SyntheticTransport(exchange), clock=_clock(), resources=_resources(),
        window=window or _window(), allowed_peer_ips=('8.8.8.8',),
        manifest_sha256=MANIFEST, plan=plan, expected_plan_sha256=plan.sha256,
        report_sink=sink, attempt_model=attempt_model)


# ---------------------------------------------------------------------------
# Backward compatibility: no guard configured.
# ---------------------------------------------------------------------------

def test_no_attempt_model_is_fully_backward_compatible(tmp_path):
    tmp_path = _dirs(tmp_path)
    body = b'0123456789012345678901234567890'
    exchange = SyntheticExchange({'req-1': _ok_response(body)})
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _build_runtime(shared, session, budget, store, tmp_path,
            requests=(_request(reservation_bytes=len(body)),), exchange=exchange)
        outcome = runtime.run_attempt(_request(reservation_bytes=len(body)))
        assert outcome['outcome'] == 'SUCCESS'
        assert runtime.attempt_model is None


# ---------------------------------------------------------------------------
# A guard the model admits does not block the real dispatch path.
# ---------------------------------------------------------------------------

def test_admitted_guard_allows_normal_dispatch(tmp_path):
    tmp_path = _dirs(tmp_path)
    body = b'0123456789012345678901234567890'
    exchange = SyntheticExchange({'req-1': _ok_response(body)})
    guard = AttemptModelGuard(good_inputs(), genesis_checkpoint())
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _build_runtime(shared, session, budget, store, tmp_path,
            requests=(_request(reservation_bytes=len(body)),), exchange=exchange,
            attempt_model=guard)
        outcome = runtime.run_attempt(_request(reservation_bytes=len(body)))
        assert outcome['outcome'] == 'SUCCESS'
        assert budget.received == len(body)


# ---------------------------------------------------------------------------
# A refused guard blocks dispatch before any durable mutation, even though
# every other precondition in this fixture is otherwise satisfied -- the
# real launch path cannot bypass the attempt model.
# ---------------------------------------------------------------------------

def test_refused_guard_blocks_before_any_durable_mutation(tmp_path):
    tmp_path = _dirs(tmp_path)
    exchange = SyntheticExchange({})  # .take() would raise: dispatch must never be reached
    # Identical to the admitting fixture above except for one unreconciled
    # checkpoint field (an open unfinished intent), which alone makes
    # admit_synthetic refuse (UNRECONCILED_HISTORY_OR_INTENT).
    guard = AttemptModelGuard(
        good_inputs(), genesis_checkpoint(unfinished_intents=('synthetic://intent/open',)))
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _build_runtime(shared, session, budget, store, tmp_path,
            requests=(_request(),), exchange=exchange, attempt_model=guard)
        outcome = runtime.run_attempt(_request())
        assert outcome['outcome'] == 'REFUSED'
        assert 'RUNTIME_ATTEMPT_MODEL_REFUSED' in outcome['reasons']
        # The only durable record is the refusal itself: no budget
        # reservation was ever made, so Step 1's shared-intent-open/budget-
        # reserve (and therefore transport.dispatch) was never reached.
        assert budget.in_flight is None
        assert budget.count == 0
        assert session.attempt['outcome'] == 'REFUSED'


def test_refused_guard_scope_mismatch_fails_closed_for_non_pilot_request(tmp_path):
    tmp_path = _dirs(tmp_path)
    exchange = SyntheticExchange({})
    guard = AttemptModelGuard(good_inputs(), genesis_checkpoint())
    with _acquire(tmp_path) as (shared, session, budget, store):
        field_request = _request(purpose='FIELD', provider='GEFS', reservation_bytes=32)
        runtime = _build_runtime(shared, session, budget, store, tmp_path,
            requests=(field_request,), exchange=exchange, attempt_model=guard)
        outcome = runtime.run_attempt(field_request)
        assert outcome['outcome'] == 'REFUSED'
        assert 'RUNTIME_ATTEMPT_MODEL_SCOPE_MISMATCH' in outcome['reasons']
        assert budget.in_flight is None and budget.count == 0


# ---------------------------------------------------------------------------
# Single-shot: a guard that already admitted once refuses every further
# call outright, regardless of request -- the pilot's zero-retry contract,
# and the mechanism behind "duplicate starts are refused/held."
# ---------------------------------------------------------------------------

def test_guard_is_single_shot_second_call_refused_even_if_otherwise_valid(tmp_path):
    tmp_path = _dirs(tmp_path)
    body = b'0123456789012345678901234567890'
    exchange = SyntheticExchange({'req-1': _ok_response(body), 'req-2': _ok_response(body)})
    guard = AttemptModelGuard(good_inputs(), genesis_checkpoint())
    requests = (_request(request_id='req-1', reservation_bytes=len(body)),
                _request(request_id='req-2', reservation_bytes=len(body)))
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _build_runtime(shared, session, budget, store, tmp_path,
            requests=requests, exchange=exchange, attempt_model=guard)
        first = runtime.run_attempt(requests[0])
        assert first['outcome'] == 'SUCCESS'
        budget_count_after_first = budget.count
        second = runtime.run_attempt(requests[1])
        assert second['outcome'] == 'REFUSED'
        assert 'RUNTIME_ATTEMPT_MODEL_ALREADY_CONSUMED' in second['reasons']
        # The second (refused) call made no further budget reservation.
        assert budget.count == budget_count_after_first


def test_guard_single_shot_consumes_even_on_first_refusal(tmp_path):
    """A guard that refuses on its first use is still consumed: no caller
    gets a second attempt against the same guard instance (no-refund
    ambiguity -- the slot is spent whether or not it was granted)."""
    tmp_path = _dirs(tmp_path)
    exchange = SyntheticExchange({})
    guard = AttemptModelGuard(
        good_inputs(), genesis_checkpoint(unfinished_intents=('synthetic://intent/open',)))
    requests = (_request(request_id='req-1'), _request(request_id='req-2'))
    with _acquire(tmp_path) as (shared, session, budget, store):
        runtime = _build_runtime(shared, session, budget, store, tmp_path,
            requests=requests, exchange=exchange, attempt_model=guard)
        first = runtime.run_attempt(requests[0])
        assert first['outcome'] == 'REFUSED'
        assert 'RUNTIME_ATTEMPT_MODEL_REFUSED' in first['reasons']
        second = runtime.run_attempt(requests[1])
        assert second['outcome'] == 'REFUSED'
        assert 'RUNTIME_ATTEMPT_MODEL_ALREADY_CONSUMED' in second['reasons']


# ---------------------------------------------------------------------------
# Construction-time shape guard.
# ---------------------------------------------------------------------------

def test_runtime_rejects_wrong_typed_attempt_model(tmp_path):
    tmp_path = _dirs(tmp_path)
    exchange = SyntheticExchange({})
    with _acquire(tmp_path) as (shared, session, budget, store):
        with pytest.raises(LaunchContractError, match='RUNTIME_COMPOSITION_SHAPE'):
            _build_runtime(shared, session, budget, store, tmp_path,
                requests=(_request(),), exchange=exchange, attempt_model=object())


# ---------------------------------------------------------------------------
# Direct attempt-model-level proof backing this guard's admission call:
# clock/resource floor breaches refuse closed, and a post-START fault
# preserves the charged attempt. (The guard only calls ``admit_synthetic``;
# it does not drive the full per-event script through GateRuntime, so these
# are exercised against the model's own public API, matching the already-
# reviewed suite's own fixtures.)
# ---------------------------------------------------------------------------

def test_admission_fails_closed_on_resource_floor_breach():
    bad_resources = dataclasses.replace(
        GOOD_RESOURCES, free_disk_bytes_after_reservation=0,
        mem_available_bytes_after_reservation=0, physically_reserved_bytes=0)
    inputs = good_inputs(resources=bad_resources)
    state = drive(inputs, genesis_checkpoint(), tags=())[0]
    assert state.phase == 'REFUSED_BEFORE_DISPATCH'
    result = attempt_model_result(state)
    assert result.used_attempts == 0 and result.used_body_bytes == 0


def test_admission_fails_closed_on_clock_floor_breach():
    bad_clock = dataclasses.replace(
        GOOD_CLOCK, uncertainty_seconds=10_000.0, calibration_age_seconds=10_000.0)
    inputs = good_inputs(clock=bad_clock)
    state = drive(inputs, genesis_checkpoint(), tags=())[0]
    assert state.phase == 'REFUSED_BEFORE_DISPATCH'


def test_post_start_fault_preserves_charge_no_refund():
    state, transitions = drive(good_inputs(), genesis_checkpoint(),
                                tags=('LOCKS', 'INTENT_ACK', 'RESERVE_ACK', 'START', 'FAULT'))
    assert transitions[-1].accepted
    assert state.phase == 'UNCERTAIN_HELD'
    result = attempt_model_result(state)
    # The one stage attempt was consumed by START and is never refunded by
    # the later fault: used + outstanding always sums to exactly one slot.
    assert result.used_attempts == 1
    assert result.used_attempts + result.outstanding_attempts == 1
