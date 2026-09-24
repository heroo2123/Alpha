import asyncio
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import fcntl
import os

import httpx
import pytest

from polymarket_scanner.v11.audit_reports import AuditPolicy, AuditScheduler, AuditWorker
from polymarket_scanner.v11.book_inputs import BookPolicy
from polymarket_scanner.v11.candidate_runner import CandidatePolicy, CandidateRunner, ObservationBatch
from polymarket_scanner.v11.census_worker import CensusPlan, CensusPolicy, CensusWorker
from polymarket_scanner.v11.collection import PublicCollector, SourceRequest
from polymarket_scanner.v11.discovery import MarketDiscovery, DiscoveryPolicy
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.event_risk import SafetyReductions
from polymarket_scanner.v11.observation_pump import ObservationPump
from polymarket_scanner.v11.observation_runtime import ObservationRuntime, ScheduledCollector
from polymarket_scanner.v11.paper_runtime import PaperRuntime
from test_v11_paper_coordinator import rig, coordinator, proposal
from test_v11_paper_runtime import assembled
from test_v11_census_worker import prepare, transport_for


def built(rig,monkeypatch,client,*,policy=None,observations=False):
    old=assembled(rig,monkeypatch,census=False)
    audits=AuditScheduler(rig['store'],AuditPolicy('candidate-fixture',records_per_step=256))
    rt=PaperRuntime(old.coordinator,old.queue,old.health,old.policy,evaluator=old.evaluator,
        worker_id='worker',generation='candidate-fixture',audits=audits)
    prepare(rig,rt)
    scheduled=ScheduledCollector(PublicCollector(rig['store'],client,attempts=1))
    census=CensusWorker(scheduled,rt.queue,rt.health,plans=(CensusPlan(rig['rule'],'FIXTURE_COLLATERAL'),),
        policy=CensusPolicy('candidate-fixture'),book_policy=BookPolicy('candidate-fixture'))
    discovery=MarketDiscovery(scheduled,rt.health,DiscoveryPolicy('candidate-fixture'))
    kw={}
    if observations:
        request=SourceRequest('NOAA_AWC','https://aviationweather.gov/api/data/metar',rig['context'].event_id,
            'OFFICIAL_OBSERVATION',rig['context'].station_id,'planned',(('ids',rig['context'].station_id),('format','json')))
        batch=ObservationBatch((request,),((rig['context'].event_id,rig['context'].station_id),),('fixture',),(('fixture',('NOAA_AWC',)),))
        kw=dict(observation=ObservationPump(ObservationRuntime(scheduled),rt),observation_batch=batch)
    return CandidateRunner(rt,policy or CandidatePolicy('fixture',maximum_seconds=5,maximum_jobs=3,
        safety_interval_seconds=.05,minimum_job_spacing_seconds=.05),census=census,discovery=discovery,
        audits=AuditWorker(rt.coordinator,audits.policy),**kw)


def transport(rig,calls):
    books=transport_for(rig,calls)
    def receive(req):
        if req.url.host=='gamma-api.polymarket.com':
            calls.append(req)
            return httpx.Response(200,json=dict(events=[dict(id='rain',title='Will it rain?',tags=[dict(slug='weather')],markets=[])],next_cursor=None))
        return books(req)
    return receive


def test_real_workers_run_in_one_bounded_candidate_and_completed_replay_is_read_only(rig,monkeypatch):
    calls=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport(rig,calls))) as client:
            runner=built(rig,monkeypatch,client);row=await runner.run('one')
            count=len(calls);head=runner.runtime._head()
            assert await runner.run('one')==row and len(calls)==count and runner.runtime._head()==head
            return runner,row
    runner,row=asyncio.run(run());d=row['body']['details']
    assert [j['kind'] for j in d['worker_results']]==['CENSUS','DISCOVERY','AUDIT'],d
    assert d['worker_results'][0]['outcome']=='CENSUS_SOURCE_COVERAGE_ONLY',d
    assert d['worker_results'][2]['outcome']=='AUDIT_COMPLETE',d
    assert runner.runtime.evaluator.calls>=1 and len(calls)==8
    assert d['all_async_jobs_drained'] and not d['active_command_requires_recovery'] and not d['financial_authority']
    assert not rig['store'].records(kind='TRADE') and runner.runtime.coordinator.snapshot()['reserved_cash']=='0'
    summary=runner.discovery._head()['body']['details']['summary']
    assert summary['unsupported_events']==1 and not summary['current_universe_verified']
    assert not d['forward_acceptance'] and not d['independent_guardian_commissioned']


def test_operator_cancellation_is_automatically_serviced_while_public_http_waits(rig,monkeypatch):
    c=coordinator(rig);p=proposal(rig,units='2');c.coordinate('reserve',(p,));calls=[]
    async def run():
        entered=asyncio.Event();release=asyncio.Event();base=transport(rig,calls)
        async def blocked(req):entered.set();await release.wait();return base(req)
        async with httpx.AsyncClient(transport=httpx.MockTransport(blocked)) as client:
            runner=built(rig,monkeypatch,client,policy=CandidatePolicy('fixture',maximum_seconds=5,maximum_jobs=1,safety_interval_seconds=.05))
            task=asyncio.create_task(runner.run('while-http'))
            try:
                await asyncio.wait_for(entered.wait(),2)
                SafetyReductions(rig['store']).apply('operator',scope='ACCOUNT',scope_id='account',action='CANCEL_AND_HALT',actor='fixture',reason='TEST')
                for _ in range(50):
                    if c._state(c._head())['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED':break
                    await asyncio.sleep(.02)
                assert c._state(c._head())['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED'
                assert Decimal(c.snapshot()['reserved_cash'])==Decimal('.8') and not task.done()
            finally:release.set()
            return await task
    d=asyncio.run(run())['body']['details']
    assert len(d['runtime_ids'])>=3 and d['all_async_jobs_drained'] and not d['real_orders_sent']


def test_timeout_preserves_exact_pending_command_then_recovers_without_repeating_http(rig,monkeypatch):
    calls=[]
    async def blocked(req):calls.append(req);await asyncio.Event().wait()
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(blocked)) as client:
            runner=built(rig,monkeypatch,client,policy=CandidatePolicy('timeout',maximum_seconds=2,maximum_jobs=1,
                job_timeout_seconds=.1,safety_interval_seconds=.05))
            first=await runner.run('timeout');pending=first['body']['details']['state']['active']
            assert pending and first['body']['details']['all_async_jobs_drained']
            assert await runner.run('timeout')==first
            resumed=CandidateRunner(runner.runtime,runner.policy,census=runner.census,discovery=runner.discovery,audits=runner.audits)
            second=await resumed.run('resume');return first,second,pending
    first,second,pending=asyncio.run(run());d=second['body']['details']
    assert len(calls)==1 and d['worker_results'][0]['command_id']==pending['id']
    assert d['worker_results'][0]['outcome']=='INTERRUPTED_COLLECTION_NOT_RETRIED'
    assert not d['active_command_requires_recovery']
    assert not rig['store'].records(kind='TRADE')


def test_caller_interruption_drains_only_owned_coroutine_and_keeps_worker_recovery(rig,monkeypatch):
    calls=[]
    async def run():
        entered=asyncio.Event()
        async def blocked(req):calls.append(req);entered.set();await asyncio.Event().wait()
        async with httpx.AsyncClient(transport=httpx.MockTransport(blocked)) as client:
            runner=built(rig,monkeypatch,client,policy=CandidatePolicy('interrupt',maximum_jobs=1))
            task=asyncio.create_task(runner.run('interrupt'))
            await asyncio.wait_for(entered.wait(),2);task.cancel()
            with pytest.raises(asyncio.CancelledError):await task
            assert runner._head()['body']['details']['state']['active']
            row=await runner.run('interrupt')
            return row
    d=asyncio.run(run())['body']['details']
    assert len(calls)==1 and d['worker_results'][0]['outcome']=='INTERRUPTED_COLLECTION_NOT_RETRIED'
    assert d['all_async_jobs_drained']


def test_clock_failure_suppresses_collection_but_keeps_cancellation(rig,monkeypatch):
    c=coordinator(rig);p=proposal(rig,units='2');c.coordinate('reserve',(p,));calls=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport(rig,calls))) as client:
            runner=built(rig,monkeypatch,client,policy=CandidatePolicy('clock',maximum_seconds=.2,safety_interval_seconds=.05))
            rig['sync'][0]=False
            return await runner.run('clock')
    d=asyncio.run(run())['body']['details']
    assert not calls and not d['clock_healthy_at_finish'] and not d['worker_results']
    assert c._state(c._head())['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED'
    assert Decimal(c.snapshot()['reserved_cash'])==Decimal('.8')


def test_completed_discovery_job_recovery_does_not_start_another_scan(rig,monkeypatch):
    calls=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport(rig,calls))) as client:
            runner=built(rig,monkeypatch,client,policy=CandidatePolicy('discovery',maximum_jobs=1,maximum_seconds=2))
            state=dict(sequence=0,next_kind=1,active=None,discovery_not_before=0.)
            runner._save('fixture-next-discovery',state,outcome='SYNTHETIC_INTERRUPTION_SETUP')
            one=await runner.run('one');old=runner.discovery._head()
            state=deepcopy(one['body']['details']['state']);job=one['body']['details']['worker_results'][0]
            state['active']=dict(kind=job['kind'],id=job['command_id'])
            runner._save('fixture-before-result-commit',state,outcome='SYNTHETIC_INTERRUPTION_SETUP')
            two=await runner.run('recover')
            assert runner.discovery._head()==old
            return two
    d=asyncio.run(run())['body']['details']
    assert len(calls)==1 and d['worker_results'][0]['kind']=='DISCOVERY'


def test_optional_observation_pump_joins_source_feed_without_duplicate_collector_workers(rig,monkeypatch):
    calls=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport(rig,calls))) as client:
            runner=built(rig,monkeypatch,client,observations=True,
                policy=CandidatePolicy('obs',maximum_jobs=1,maximum_seconds=2,safety_interval_seconds=.05))
            runner._save('fixture-next-observation',dict(sequence=0,next_kind=3,active=None,discovery_not_before=0.),outcome='SYNTHETIC_SCHEDULING_SETUP')
            return await runner.run('one')
    d=asyncio.run(run())['body']['details']
    assert len(calls)==1 and d['worker_results'][0]['outcome']=='OBSERVATION_CYCLE_RECORDED'
    assert d['all_async_jobs_drained'] and not d['financial_authority']
    row=rig['store'].latest_source(kind='OFFICIAL_OBSERVATION',event_id=rig['context'].event_id,provider='NOAA_AWC',source_identity=rig['context'].station_id)
    assert row['body']['payload']['raw_evidence_id']


def test_unexpected_optional_worker_failure_is_redacted_and_does_not_prevent_other_workers(rig,monkeypatch):
    calls=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport(rig,calls))) as client:
            runner=built(rig,monkeypatch,client)
            async def broken(*args):raise RuntimeError('PRIVATE_DETAIL_MUST_NOT_ESCAPE')
            monkeypatch.setattr(runner.discovery,'step',broken)
            return await runner.run('one')
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='DEGRADED' and d['worker_results'][-1]['kind']=='AUDIT'
    assert d['errors'][0]['reason']=='RuntimeError' and 'PRIVATE_DETAIL' not in str(d)
    assert d['all_async_jobs_drained']


def test_runner_lock_and_configuration_identity_are_preserved(rig,monkeypatch):
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport(rig,[]))) as client:
            runner=built(rig,monkeypatch,client,policy=CandidatePolicy('lock',maximum_jobs=1))
            fd=os.open(runner.store.path.with_name(runner.store.path.name+'.candidate.lock'),os.O_CREAT|os.O_WRONLY,0o600)
            try:
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
                with pytest.raises(EvidenceError,match='ALREADY_RUNNING'):await runner.run('locked')
                assert runner._head() is None
            finally:os.close(fd)
            await runner.run('one')
            other=CandidateRunner(runner.runtime,replace(runner.policy,version='changed'),census=runner.census,discovery=runner.discovery,audits=runner.audits)
            with pytest.raises(EvidenceError,match='CONFIGURATION_CHANGED'):await other.run('two')
            with pytest.raises(EvidenceError,match='REPLAY_CONFIG'):await other.run('one')
    asyncio.run(run())


@pytest.mark.parametrize('bad',[dict(maximum_seconds=301),dict(maximum_jobs=33),dict(maximum_safety_ticks=1),
    dict(safety_interval_seconds=0),dict(job_timeout_seconds=61),dict(minimum_job_spacing_seconds=0)])
def test_policy_requires_finite_explicit_limits(bad):
    with pytest.raises(EvidenceError,match='BOUND'):CandidatePolicy('bad',**bad)
