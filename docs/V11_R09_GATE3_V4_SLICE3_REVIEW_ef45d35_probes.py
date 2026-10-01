"""Independent offline counterexamples. Passing test reproduces the named defect."""
import importlib.util
import sys
from pathlib import Path
import pytest
ROOT = Path('/tmp/alpha-v11-gate3-v4-slice3-repair-20261001')
sys.path.insert(0, str(ROOT))
spec = importlib.util.spec_from_file_location('fixtures', ROOT / 'tests/test_v11_r09_gate3_runtime.py')
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
from tools.v11_r09_gate3_runtime import SyntheticTransport, FakeClock, CapacityPlan, ReportSink
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_offline_io import SyntheticExchange, OfflineResponse


def test_elapsed_expiry_loses_observed_denial_and_prefetched_bytes(tmp_path):
    f._dirs(tmp_path)
    clock = f._clock()
    class Slow(SyntheticTransport):
        def dispatch(self, request_id, **kwargs):
            clock.advance(2)
            return super().dispatch(request_id, **kwargs)
    response = f._ok_response(b'abcd', status=503, headers=(('Retry-After','1200'),))
    with f._acquire(tmp_path) as (shared, session, budget, store):
        rt = f._runtime(shared,session,budget,store,SyntheticExchange({'req-1':response}),clock=clock,window=f._window(elapsed_cap_seconds=1))
        rt.transport = Slow(rt.transport._exchange)
        with pytest.raises(LaunchContractError,match='RUNTIME_ELAPSED_DEADLINE'):
            rt.run_attempt(f._request(reservation_bytes=4))
        assert not shared.denials
        assert budget.received == 0
        assert shared.open_intent is not None and budget.in_flight == 'req-1'


def test_bad_header_loses_all_eager_overdelivery_bytes(tmp_path):
    f._dirs(tmp_path)
    response=f._ok_response(b'a'*31,headers=(('ETag','"duplicate"'),))
    with f._acquire(tmp_path) as (shared,session,budget,store):
        rt=f._runtime(shared,session,budget,store,SyntheticExchange({'req-1':response}))
        with pytest.raises(LaunchContractError,match='RUNTIME_DUPLICATE_HEADER'):
            rt.run_attempt(f._request(reservation_bytes=10))
        assert budget.received == 0 and not budget.violated
        assert budget.in_flight == 'req-1'


def test_store_clocks_not_in_session_intersection(tmp_path):
    f._dirs(tmp_path)
    class ShiftSealClock(FakeClock):
        count=0
        def evidence(self,phase):
            self.count += 1
            if self.count == 6:
                self.set(utc=self.monotonic()+0.08)
            return super().evidence(phase)
    clock=ShiftSealClock(f.BOOT,utc=10,mono=10,uncertainty=.05)
    requests=(f._request(reservation_bytes=4),f._request(request_id='req-2',reservation_bytes=4))
    with f._acquire(tmp_path) as (shared,session,budget,store):
        rt=f._runtime(shared,session,budget,store,SyntheticExchange({'req-1':f._ok_response(b'abcd'),'req-2':f._ok_response(b'efgh')}),clock=clock,requests=requests)
        assert rt.run_attempt(requests[0])['outcome']=='SUCCESS'
        assert session.clock_offset_interval == (-.05,.05)
        clock.set(utc=11.92,mono=12,measured_mono=12)
        assert rt.run_attempt(requests[1])['outcome']=='SUCCESS'
        all_readings=[e.reading for r in store.receipts.values() for e in r.clocks]
        lo=max(r.utc_seconds-r.monotonic_seconds-r.uncertainty_seconds for r in all_readings)
        hi=min(r.utc_seconds-r.monotonic_seconds+r.uncertainty_seconds for r in all_readings)
        assert lo > hi
        assert rt.finalize_report()['classification']=='COMPLETE'


def test_successful_field_can_disappear_from_full_denominator(tmp_path):
    f._dirs(tmp_path)
    request=f._request(purpose='FIELD',provider='GEFS',slot_index=None,range_start=0,range_end=3,expected_object_bytes=4,reservation_bytes=4)
    response=f._ok_response(b'abcd',status=206,headers=(('Content-Range','bytes 0-3/4'),))
    with f._acquire(tmp_path) as journals:
        rt=f._runtime(*journals,SyntheticExchange({'req-1':response}),requests=(request,))
        assert rt.run_attempt(request)['outcome']=='SUCCESS'
        report=f.build_terminal_report(plan=rt.plan,session=journals[1],budget=journals[2],shared=journals[0],store=journals[3])
        assert report['raw_completed_count']==1
        assert all(r['request_id'] is None for r in report['rows'])
        assert report['outcome_counts']=={'NEVER_ATTEMPTED':2713}


def test_refused_entry_inflates_attempted_accounting(tmp_path):
    f._dirs(tmp_path)
    with f._acquire(tmp_path) as journals:
        rt=f._runtime(*journals,SyntheticExchange({}),clock=f._clock(utc=-1,mono=10))
        assert rt.run_attempt(f._request())['outcome']=='REFUSED'
        report=f.build_terminal_report(plan=rt.plan,session=journals[1],budget=journals[2],shared=journals[0],store=journals[3])
        assert journals[2].count==0
        assert report['global_accounting']['attempted_count']==1
        assert report['per_purpose']['INDEX']['attempted_count']==1


def test_tiny_chunks_exceed_frozen_capacity_estimate(tmp_path):
    f._dirs(tmp_path)
    request=f._request(reservation_bytes=32)
    capacity=CapacityPlan.for_requests((request,))
    with f._acquire(tmp_path) as journals:
        rt=f._runtime(*journals,SyntheticExchange({'req-1':f._ok_response(b'a'*32,chunks=(b'a',)*32)}),requests=(request,))
        before=len(journals[2].events)
        assert rt.run_attempt(request)['outcome']=='SUCCESS'
        assert len(journals[2].events)-before==34 > capacity.budget_records
        assert len(journals[1].events)>12+2


def test_sparse_report_reserve_is_accepted_as_preallocation(tmp_path):
    directory=tmp_path/'report'
    directory.mkdir(mode=0o700)
    reserve=directory/ReportSink.RESERVE_FILE_NAME
    with reserve.open('wb') as stream:
        stream.truncate(f.REPORT_RESERVE_BYTES)
    reserve.chmod(0o600)
    assert reserve.stat().st_blocks==0
    with ReportSink(directory) as sink:
        assert sink.reserved
        assert reserve.stat().st_blocks==0


def test_cooldown_dispatch_uses_nominal_time_not_receipt_lower_bound(tmp_path):
    from tools.v11_r09_gate3_runtime import _build_denial_record, _bounded_headers
    f._dirs(tmp_path)
    with f._acquire(tmp_path) as (shared,session,budget,store):
        shared.intent_open('old-req',purpose='INDEX',endpoint_id='c'*64,control_domain_id='d'*64,manifest_sha256='9'*64,max_reservation_bytes=4,now_utc=10)
        response=f._ok_response(b'bad!',status=503,headers=(('Retry-After','5'),))
        record=_build_denial_record('503',response,window=f._window(),receipt_evidence=f._clock().evidence('body_receipt'),headers=_bounded_headers(response),origin=f.ORIGIN)
        shared.denial_observed('old-req',denial=record)
        shared.intent_closed('old-req',outcome='DENIED',accounting_head='8'*64,total_delivered_bytes=4)
        clock=f._clock(utc=1000,mono=1000,uncertainty=.05)
        assert shared.is_blocked('d'*64,now_utc=999.95)
        rt=f._runtime(shared,session,budget,store,SyntheticExchange({'req-1':f._ok_response(b'abcd')}),clock=clock,window=f._window(start_utc=900,acquisition_end_utc=2000,decision_lower_utc=2100))
        assert rt.run_attempt(f._request(reservation_bytes=4))['outcome']=='SUCCESS'


def test_shared_journal_boot_is_not_bound_to_other_three(tmp_path):
    from tools.v11_r09_gate3_ledgers import SharedLedger
    f._dirs(tmp_path)
    (tmp_path/'alternate_shared').mkdir(mode=0o700)
    with f._acquire(tmp_path) as (_,session,budget,store):
        with SharedLedger(tmp_path/'alternate_shared',boot_id='different-boot',genesis_review_digest=f.GENESIS) as shared:
            rt=f._runtime(shared,session,budget,store,SyntheticExchange({'req-1':f._ok_response(b'abcd')}))
            assert rt.run_attempt(f._request(reservation_bytes=4))['outcome']=='SUCCESS'
            assert shared.events[0]['boot_id'] != session.boot_id
