"""Independent synthetic counterexamples for exact candidate 8e446fd.

These tests characterize defects, so a pass reproduces the defect. Run with
ALPHA_GATE3_CANDIDATE pointing to the clean exact candidate worktree.
No network, provider, service, credentials or production data are used.
"""
import importlib.util
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(os.environ.get('ALPHA_GATE3_CANDIDATE',
    '/tmp/alpha-v11-gate3-v4-slice3-20261001'))
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('candidate_fixtures',
    ROOT / 'tests/test_v11_r09_gate3_runtime.py')
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
from tools.v11_r09_gate3_runtime import Transport, ReportSink, build_terminal_report
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_offline_io import OfflineResponse, SyntheticExchange


class DelayedTransport(Transport):
    def __init__(self, clock, response, delay=0):
        self.clock, self.response, self.delay = clock, response, delay
        self.starts = []

    def dispatch(self, request_id):
        self.starts.append(self.clock.monotonic())
        self.clock.advance(self.delay)
        return self.response


def test_retry_after_on_success_ignored(tmp_path):
    f._dirs(tmp_path)
    exchange = SyntheticExchange({'req-1': f._ok_response(headers=(('Retry-After', '1200'),))})
    with f._acquire(tmp_path) as (shared, session, budget, store):
        out = f._runtime(shared, session, budget, store, exchange).run_attempt(f._request())
        assert out['outcome'] == 'SUCCESS'
        assert not shared.is_blocked('d' * 64, now_utc=11)


def test_denial_expiry_uses_dispatch_instead_of_receipt(tmp_path):
    f._dirs(tmp_path)
    clock = f._clock()
    response = OfflineResponse(503, (('Retry-After', '1200'),), (b'x',), '8.8.8.8', True)
    with f._acquire(tmp_path) as (shared, session, budget, store):
        rt = f._runtime(shared, session, budget, store, SyntheticExchange({}), clock=clock)
        rt.transport = DelayedTransport(clock, response, delay=20)
        rt.run_attempt(f._request())
        assert clock.monotonic() == 30
        assert shared.denials['d' * 64]['cooldown_until'] == 1210.05
        assert not shared.is_blocked('d' * 64, now_utc=1220)  # correct expiry 1230.05


@pytest.mark.parametrize('clock_kwargs', [
    {'utc': 100, 'mono': 100, 'measured_mono': 0},
    {'boot_id': 'wrong-boot'},
])
def test_invalid_clock_dispatched_before_late_store_rejection(tmp_path, clock_kwargs):
    f._dirs(tmp_path)
    clock = f._clock(**clock_kwargs)
    transport = DelayedTransport(clock, f._ok_response())
    with f._acquire(tmp_path) as (shared, session, budget, store):
        rt = f._runtime(shared, session, budget, store, SyntheticExchange({}), clock=clock)
        rt.transport = transport
        with pytest.raises(LaunchContractError, match='CLOCK_'):
            rt.run_attempt(f._request())
        assert len(transport.starts) == 1
        assert session.attempt['state'] == 'ACCOUNTED'
        assert budget.in_flight is None


@pytest.mark.parametrize('delay', [31, 1000])
def test_request_deadline_and_acquisition_end_not_enforced(tmp_path, delay):
    f._dirs(tmp_path)
    clock = f._clock()
    with f._acquire(tmp_path) as (shared, session, budget, store):
        rt = f._runtime(shared, session, budget, store, SyntheticExchange({}), clock=clock)
        rt.transport = DelayedTransport(clock, f._ok_response(), delay=delay)
        out = rt.run_attempt(f._request())
        assert out['outcome'] == 'SUCCESS' and out['timely'] is True
        assert clock.monotonic() > 40  # frozen request deadline
        if delay == 1000:
            assert clock.monotonic() > rt.window.acquisition_end_utc


def test_post_close_pacing_missing(tmp_path):
    f._dirs(tmp_path)
    clock = f._clock()
    transport = DelayedTransport(clock, f._ok_response(b'first'), delay=3)
    with f._acquire(tmp_path) as (shared, session, budget, store):
        rt = f._runtime(shared, session, budget, store, SyntheticExchange({}), clock=clock)
        rt.transport = transport
        assert rt.run_attempt(f._request())['outcome'] == 'SUCCESS'
        closed_at = clock.monotonic()
        transport.response, transport.delay = f._ok_response(b'second'), 0
        assert rt.run_attempt(f._request(request_id='req-2'))['outcome'] == 'SUCCESS'
        assert transport.starts[-1] == closed_at  # zero post-close pause


def test_reservation_fsync_delay_allows_actual_starts_under_two_seconds(tmp_path):
    f._dirs(tmp_path)
    clock = f._clock()
    transport = DelayedTransport(clock, f._ok_response(b'first'))
    with f._acquire(tmp_path) as (shared, session, budget, store):
        rt = f._runtime(shared, session, budget, store, SyntheticExchange({}), clock=clock)
        rt.transport = transport
        original_reserve = budget.reserve

        def delayed_reserve(*args, **kwargs):
            original_reserve(*args, **kwargs)
            clock.advance(3)

        budget.reserve = delayed_reserve
        assert rt.run_attempt(f._request())['outcome'] == 'SUCCESS'
        budget.reserve = original_reserve
        transport.response = f._ok_response(b'second')
        assert rt.run_attempt(f._request(request_id='req-2'))['outcome'] == 'SUCCESS'
        assert transport.starts == [13, 13]


def test_elapsed_deadline_widens_after_reopen(tmp_path):
    f._dirs(tmp_path)
    clock = f._clock()
    window = f._window(elapsed_cap_seconds=1)
    with f._acquire(tmp_path) as journals:
        rt = f._runtime(*journals, SyntheticExchange({'req-1': f._ok_response(b'first')}),
                        clock=clock, window=window)
        assert rt.run_attempt(f._request())['outcome'] == 'SUCCESS'
        assert rt._elapsed_deadline_mono == 11
    clock.advance(2)
    with f._acquire(tmp_path) as journals:
        rt = f._runtime(*journals, SyntheticExchange({'req-2': f._ok_response(b'second')}),
                        clock=clock, window=window)
        assert rt.run_attempt(f._request(request_id='req-2'))['outcome'] == 'SUCCESS'
        assert rt._elapsed_deadline_mono == 13


def test_cross_attempt_clock_step_accepted(tmp_path):
    f._dirs(tmp_path)
    clock = f._clock()
    exchange = SyntheticExchange({'req-1': f._ok_response(b'first'),
                                  'req-2': f._ok_response(b'second')})
    with f._acquire(tmp_path) as journals:
        rt = f._runtime(*journals, exchange, clock=clock)
        assert rt.run_attempt(f._request())['outcome'] == 'SUCCESS'
        clock.set(utc=112, mono=12, measured_mono=12)  # +100s UTC step
        assert rt.run_attempt(f._request(request_id='req-2'))['outcome'] == 'SUCCESS'


def test_malformed_framing_releases_budget_and_shared_token(tmp_path):
    f._dirs(tmp_path)
    response = OfflineResponse(200, (('Content-Length', '99'), ('ETag', f.ETAG)),
                               (b'x',), '8.8.8.8', True)
    with f._acquire(tmp_path) as (shared, session, budget, store):
        out = f._runtime(shared, session, budget, store,
                        SyntheticExchange({'req-1': response})).run_attempt(f._request())
        assert out['reason'] == 'RESPONSE_LENGTH_ENCODING'
        assert budget.in_flight is None and shared.open_intent is None
        assert budget.reserved == 1  # releases 31 bytes without separate close proof


def test_prefetched_tuple_tail_not_counted_after_overdelivery(tmp_path):
    f._dirs(tmp_path)
    response = OfflineResponse(200, (), (b'x' * 11, b'y' * 20), '8.8.8.8', True)
    with f._acquire(tmp_path) as (shared, session, budget, store):
        out = f._runtime(shared, session, budget, store,
                        SyntheticExchange({'req-1': response})).run_attempt(
                            f._request(reservation_bytes=10))
        assert out['outcome'] == 'OVERDELIVERY_HELD'
        assert budget.received == 11 < sum(map(len, response.chunks))


def test_manifest_context_mismatch_not_checked(tmp_path):
    f._dirs(tmp_path)
    with f._acquire(tmp_path) as journals:
        rt = f._runtime(*journals, SyntheticExchange({'req-1': f._ok_response()}))
        # Equivalent to supplying this different digest at construction: the
        # constructor validates only digest shape, never equality to journals.
        from tools.v11_r09_gate3_runtime import GateRuntime
        mismatched = GateRuntime(shared=rt.shared, session=rt.session, budget=rt.budget,
            store=rt.store, transport=rt.transport, clock=rt.clock, resources=rt.resources,
            window=rt.window, allowed_peer_ips=rt.allowed_peer_ips, manifest_sha256='9' * 64)
        assert mismatched.run_attempt(f._request())['outcome'] == 'SUCCESS'


def test_oversized_index_reservation_dispatches(tmp_path):
    f._dirs(tmp_path)
    with f._acquire(tmp_path, max_bytes=8 * 1024**2) as journals:
        rt = f._runtime(*journals, SyntheticExchange({'req-1': f._ok_response()}))
        assert rt.run_attempt(f._request(reservation_bytes=4 * 1024**2))['outcome'] == 'SUCCESS'


def test_report_trusts_arbitrary_outcomes_and_missing_request_identity():
    report = build_terminal_report(attempted={0: {'outcome': 'INVENTED_SUCCESS', 'reason': 'x'}})
    assert report['outcome_counts']['INVENTED_SUCCESS'] == 1
    assert report['rows'][0]['request_id'] is None


def test_report_sink_has_no_preallocated_reserve_and_incomplete_unbounded(tmp_path):
    directory = tmp_path / 'report'
    directory.mkdir(mode=0o700)
    with ReportSink(directory) as sink:
        assert list(directory.iterdir()) == []
        result = sink.persist_incomplete('x' * (16 * 1024**2 + 1))
        assert result['classification'] == 'INCOMPLETE'
    assert (directory / ReportSink.INCOMPLETE_FILE_NAME).stat().st_size > 16 * 1024**2


def test_positive_denial_persisted_before_first_budget_consume(tmp_path):
    f._dirs(tmp_path)
    response = OfflineResponse(503, (('Retry-After', '30'),), (b'x',), '8.8.8.8', True)
    with f._acquire(tmp_path) as (shared, session, budget, store):
        original = budget.consume
        seen = []

        def consume(request_id, chunk):
            assert shared.is_blocked('d' * 64, now_utc=10)
            assert session.attempt['denial_observed'] is True
            seen.append(chunk)
            return original(request_id, chunk)

        budget.consume = consume
        out = f._runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': response})).run_attempt(f._request())
        assert seen == [b'x'] and out['outcome'] == 'FAILED'


def test_positive_denial_write_failure_retains_reservation_and_global_hold(tmp_path):
    f._dirs(tmp_path)
    response = OfflineResponse(503, (), (b'x',), '8.8.8.8', True)
    with f._acquire(tmp_path) as (shared, session, budget, store):
        def fail(*args, **kwargs):
            raise OSError('injected denial persistence failure')

        shared.denial_observed = fail
        with pytest.raises(OSError, match='injected'):
            f._runtime(shared, session, budget, store,
                SyntheticExchange({'req-1': response})).run_attempt(f._request())
        assert shared.open_intent is not None
        assert budget.in_flight == 'req-1' and budget.reserved == 32
        assert session.attempt['state'] == 'DISPATCHED'


def test_positive_pretransport_refusal_never_dispatches(tmp_path):
    f._dirs(tmp_path)
    clock = f._clock(utc=-10)
    transport = DelayedTransport(clock, f._ok_response())
    with f._acquire(tmp_path) as (shared, session, budget, store):
        rt = f._runtime(shared, session, budget, store, SyntheticExchange({}), clock=clock)
        rt.transport = transport
        out = rt.run_attempt(f._request())
        assert out['outcome'] == 'REFUSED' and transport.starts == []
        assert shared.open_count == 0 and budget.count == 0
