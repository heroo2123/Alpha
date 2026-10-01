"""Independent offline review. defect_* tests PASS when a defect is reproduced.
closure_* tests PASS when a prior defect is now closed. No network adapters.
"""
import hashlib
import importlib.util
import os
import sys
from dataclasses import asdict
from pathlib import Path
import pytest

ROOT = Path(os.environ.get('ALPHA_REVIEW_CANDIDATE', '/tmp/alpha-v11-gate3-v4-slice3-repair-20261001'))
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('review_fixtures', ROOT / 'tests/test_v11_r09_gate3_runtime.py')
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
from tools.v11_multimodel_panel import canonical
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_runtime import (
    FrozenPlan, AttemptRequest, AbsoluteWindow, FakeClock, SyntheticTransport,
    CapacityPlan, ReportSink, _bounded_headers, _build_denial_record,
)
from tools.v11_r09_gate3_offline_io import SyntheticExchange


def test_defect_direct_unvalidated_field_plan_still_succeeds(tmp_path):
    f._dirs(tmp_path)
    request = f._request(purpose='FIELD', provider='GEFS', slot_index=0,
        range_start=0, range_end=3, expected_object_bytes=4, reservation_bytes=4)
    assert request.prerequisite_request_ids == ()
    response = f._ok_response(b'abcd', status=206,
        headers=(('Content-Range', 'bytes 0-3/4'),))
    with f._acquire(tmp_path) as journals:
        rt = f._runtime(*journals, SyntheticExchange({'req-1': response}), requests=(request,))
        assert rt.run_attempt(request)['outcome'] == 'SUCCESS'
        assert rt.finalize_report()['classification'] == 'COMPLETE'


def test_defect_factory_accepts_window_and_pins_unrelated_to_manifest(tmp_path, monkeypatch):
    from tests.test_v11_r09_gate3_launch_v4 import candidate
    payload, repo, root, start = candidate(tmp_path, monkeypatch)
    raw = canonical(payload)
    ids = [r['request_id'] for r in payload['schedule']['requests']]
    endpoints = {e['endpoint_id']: e for e in payload['network']['endpoints']}
    pins = {rid: dict(expected_etag=f.ETAG, expected_object_bytes=None,
        source_pin='e'*64, decoder_pin='f'*64, clock_policy_sha256='1'*64,
        validator_sha256=None, dependency_commit_hashes=()) for rid in ids}
    requests = tuple(AttemptRequest(
        request_id=r['request_id'], purpose=r['purpose'], endpoint_id=r['endpoint_id'],
        control_domain_id=endpoints[r['endpoint_id']]['control_domain_id'],
        origin=r['origin'], path=r['path'],
        provider=r['provider'] if r['purpose'] == 'FIELD' else None,
        slot_index=r['slot_index'], range_start=r['range_start'], range_end=r['range_end'],
        reservation_bytes=r['reservation_bytes'],
        prerequisite_request_ids=tuple(ids[i] for i in r['prerequisites']),
        **pins[r['request_id']]) for r in payload['schedule']['requests'])
    window = AbsoluteWindow(0, 2_000_000_000, 2_000_000_001)
    review = canonical(dict(schema_version=1,
        manifest_sha256=hashlib.sha256(raw).hexdigest(),
        window_sha256=hashlib.sha256(canonical(asdict(window))).hexdigest(),
        request_schedule_sha256=hashlib.sha256(canonical([asdict(r) for r in requests])).hexdigest(),
        event_schedule_sha256=hashlib.sha256(canonical([])).hexdigest()))
    plan = FrozenPlan.from_validated_manifest(raw, repo=repo, object_root=root,
        now_utc=start-4000, window=window, review_sha256=hashlib.sha256(review).hexdigest(),
        review_raw=review, request_pins=pins)
    assert plan.window.start_utc == 0 != payload['time']['window_start_utc']
    assert plan.window.acquisition_end_utc > payload['time']['last_acquisition_utc']
    assert plan.requests[0].clock_policy_sha256 != payload['runtime']['clock_policy']


@pytest.mark.parametrize('trigger', ['deadline', 'bad_header'])
def test_defect_recovery_bad_clock_discards_eager_denial_and_bytes(tmp_path, trigger):
    f._dirs(tmp_path)
    clock = f._clock()
    class BadClockTransport(SyntheticTransport):
        def dispatch(self, request, **kwargs):
            if trigger == 'deadline':
                clock.advance(2)
            clock.set(uncertainty=2)
            return super().dispatch(request, **kwargs)
    extra = (('Retry-After', '1200'),)
    if trigger == 'bad_header':
        extra += (('ETag', '"duplicate"'),)
    response = f._ok_response(b'abcd', status=503, headers=extra)
    with f._acquire(tmp_path) as (shared, session, budget, store):
        rt = f._runtime(shared, session, budget, store,
            SyntheticExchange({'req-1': response}), clock=clock,
            window=f._window(elapsed_cap_seconds=1))
        rt.transport = BadClockTransport(rt.transport._exchange)
        with pytest.raises(LaunchContractError, match='RUNTIME_CLOCK_UNCERTAINTY_EXCEEDED'):
            rt.run_attempt(f._request(reservation_bytes=4))
        assert budget.received == 0 and not shared.denials
        assert budget.in_flight == 'req-1' and shared.open_intent is not None


def test_defect_malformed_headers_hide_observed_retry_after(tmp_path):
    f._dirs(tmp_path)
    response = f._ok_response(b'abcd', status=200,
        headers=(('Retry-After', '1200'), ('ETag', '"duplicate"')))
    with f._acquire(tmp_path) as (shared, session, budget, store):
        rt = f._runtime(shared, session, budget, store, SyntheticExchange({'req-1': response}))
        with pytest.raises(LaunchContractError, match='RUNTIME_DUPLICATE_HEADER'):
            rt.run_attempt(f._request(reservation_bytes=4))
        assert budget.received == 4 and not shared.denials
        assert shared.open_intent is not None


def test_defect_large_numeric_retry_after_loses_denial_and_body(tmp_path):
    f._dirs(tmp_path)
    response = f._ok_response(b'abcd', status=503, headers=(('Retry-After', '9'*400),))
    with f._acquire(tmp_path) as (shared, session, budget, store):
        rt = f._runtime(shared, session, budget, store, SyntheticExchange({'req-1': response}))
        with pytest.raises(LaunchContractError):
            rt.run_attempt(f._request(reservation_bytes=4))
        assert budget.received == 0 and not shared.denials
        assert shared.open_intent is not None and budget.in_flight == 'req-1'


def test_defect_chunk_policy_error_path_exceeds_capacity(tmp_path):
    f._dirs(tmp_path)
    request = f._request(reservation_bytes=64)
    capacity = CapacityPlan.for_requests((request,))
    response = f._ok_response(b'a'*64, chunks=(b'a',)*64)
    with f._acquire(tmp_path) as journals:
        rt = f._runtime(*journals, SyntheticExchange({'req-1': response}), requests=(request,))
        before = len(journals[2].events)
        with pytest.raises(LaunchContractError, match='RUNTIME_CHUNK_COUNT_EXCEEDS_POLICY'):
            rt.run_attempt(request)
        assert len(journals[2].events)-before == 65 > capacity.budget_records == 35
        assert journals[2].received == 64 and journals[2].in_flight == 'req-1'


def test_defect_report_has_no_frozen_reason_precedence_or_all_reasons(tmp_path):
    f._dirs(tmp_path)
    # Both a failed prerequisite and a before-window clock hold apply.
    requests = (f._request(), f._request(request_id='req-2', purpose='FIELD',
        provider='GEFS', slot_index=0, range_start=0, range_end=3,
        expected_object_bytes=4, reservation_bytes=4, prerequisite_request_ids=('req-1',)))
    with f._acquire(tmp_path) as journals:
        rt = f._runtime(*journals, SyntheticExchange({}), clock=f._clock(utc=-1, mono=10), requests=requests)
        assert rt.run_attempt(requests[0])['outcome'] == 'REFUSED'
        assert rt.run_attempt(requests[1])['outcome'] == 'REFUSED'
        report = f.build_terminal_report(plan=rt.plan, session=journals[1], budget=journals[2], shared=journals[0], store=journals[3])
        row = report['rows'][0]
        assert row['reason'] == 'RUNTIME_PREREQUISITE_MISSING'
        assert 'reasons' not in row and 'terminal_precedence' not in report
        assert report['global_accounting']['attempted_count'] == 0
        assert report['attempted_request_ids'] == ['req-1', 'req-2']


def test_closure_store_clock_intersection_blocks_contradictory_next_request(tmp_path):
    f._dirs(tmp_path)
    class ShiftClock(FakeClock):
        count = 0
        def evidence(self, phase):
            self.count += 1
            if self.count == 6:
                self.set(utc=self.monotonic()+.08)
            return super().evidence(phase)
    clock = ShiftClock(f.BOOT, utc=10, mono=10, uncertainty=.05)
    requests = (f._request(reservation_bytes=4), f._request(request_id='req-2', reservation_bytes=4))
    with f._acquire(tmp_path) as journals:
        rt = f._runtime(*journals, SyntheticExchange({'req-1': f._ok_response(b'abcd'), 'req-2': f._ok_response(b'efgh')}), clock=clock, requests=requests)
        assert rt.run_attempt(requests[0])['outcome'] == 'SUCCESS'
        assert journals[1].clock_offset_interval[0] == pytest.approx(.03)
        rt.report_sink.close()
    with f._acquire(tmp_path) as journals:
        assert journals[1].clock_offset_interval[0] == pytest.approx(.03)
        rt = f._runtime(*journals, SyntheticExchange({'req-2': f._ok_response(b'efgh')}), clock=clock, requests=requests)
        clock.set(utc=11.92, mono=12, measured_mono=12)
        assert rt.run_attempt(requests[1])['outcome'] == 'REFUSED'
        assert journals[2].count == 1


def test_closure_sparse_reserve_is_physically_allocated(tmp_path):
    path = tmp_path/'report'
    path.mkdir(mode=0o700)
    reserve = path/ReportSink.RESERVE_FILE_NAME
    with reserve.open('wb') as stream:
        stream.truncate(f.REPORT_RESERVE_BYTES)
    reserve.chmod(0o600)
    assert reserve.stat().st_blocks == 0
    with ReportSink(path) as sink:
        assert sink.reserved and reserve.stat().st_blocks*512 >= f.REPORT_RESERVE_BYTES


def test_closure_cooldown_uses_conservative_lower_bound(tmp_path):
    f._dirs(tmp_path)
    with f._acquire(tmp_path) as (shared, session, budget, store):
        shared.intent_open('old-req', purpose='INDEX', endpoint_id='c'*64,
            control_domain_id='d'*64, manifest_sha256='9'*64,
            max_reservation_bytes=4, now_utc=10)
        response = f._ok_response(b'bad!', status=503, headers=(('Retry-After','5'),))
        shared.denial_observed('old-req', denial=_build_denial_record('503', response,
            window=f._window(), receipt_evidence=f._clock().evidence('body_receipt'),
            headers=_bounded_headers(response), origin=f.ORIGIN))
        shared.intent_closed('old-req', outcome='DENIED', accounting_head='8'*64, total_delivered_bytes=4)
        rt = f._runtime(shared, session, budget, store, SyntheticExchange({}),
            clock=f._clock(utc=1000, mono=1000, uncertainty=.05),
            window=f._window(start_utc=900, acquisition_end_utc=2000, decision_lower_utc=2100))
        assert rt.run_attempt(f._request(reservation_bytes=4))['outcome'] == 'REFUSED'
        assert budget.count == 0
