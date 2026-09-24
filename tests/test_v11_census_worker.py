import asyncio
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from polymarket_scanner.v11.book_inputs import BookPolicy, normalize_book_capture
from polymarket_scanner.v11.census_worker import CensusPlan, CensusPolicy, CensusWorker
from polymarket_scanner.v11.collection import PublicCollector
from polymarket_scanner.v11.event_queue import EventQueue
from polymarket_scanner.v11.event_risk import SafetyReductions
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.observation_runtime import ScheduledCollector
from test_v11_book_inputs import response, raw, normalize, books
from test_v11_paper_coordinator import rig, coordinator, proposal
from test_v11_paper_runtime import assembled
from test_v11_runtime_health import advance
from test_v11_probability import rule as make_rule


def transport_for(rig, calls, *, fail_awc=False):
    def transport(req):
        calls.append(req)
        if req.url.host == 'aviationweather.gov':
            return httpx.Response(503) if fail_awc else httpx.Response(200,json=[
                dict(icaoId=rig['rule'].payload['station'],obsTime=rig['now'][0]-1,temp=25)])
        return httpx.Response(200,json=response(rig, req.url.params['token_id']))
    return transport


def worker(rig, rt, client, *, policy=None, plan=None, sleeper=asyncio.sleep):
    return CensusWorker(ScheduledCollector(PublicCollector(rig['store'],client,attempts=1)), rt.queue, rt.health,
        plans=(plan or CensusPlan(rig['rule'],'FIXTURE_COLLATERAL'),), policy=policy or CensusPolicy('fixture'),
        book_policy=BookPolicy('fixture'), sleeper=sleeper)


def prepare(rig, rt):
    rt.queue.schedule_census('needed')
    rig['store'].audit('rules',event_id=rig['rule'].payload['event_id'],kind='RULE_STATE',
        details=dict(fingerprint=rig['rule'].sha256,quarantined=False))


def test_fresh_public_mock_receipts_recover_census_then_runtime_evaluates_without_waiting_old_retry(rig, monkeypatch):
    rt = assembled(rig, monkeypatch, census=False); calls=[]; prepare(rig,rt)
    rt.tick('needs-adapter')
    assert rt.queue.snapshot()['needs_census']
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport_for(rig,calls))) as client:
            cw=worker(rig,rt,client); row=await cw.step('one')
            assert await cw.step('one') == row
            return row
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='CENSUS_SOURCE_COVERAGE_ONLY',d
    assert len(calls)==7 and len(d['book_ids'])==6 and len(d['source_ids'])==1
    assert not rt.queue.snapshot()['needs_census'] and rt.queue.snapshot()['pending']
    claim=rig['store'].get('census-step:'+digest('one')+':claim')
    for key in d['book_ids']+d['source_ids']:
        row=rig['store'].get(key); raw=rig['store'].get(row['body']['payload']['raw_evidence_id'])
        assert claim['seq'] < raw['seq'] < row['seq']
    result=rt.tick('evaluate-after-census')['body']['details']
    assert result['evaluation_ids'] and rt.evaluator.calls==1 and not result['account_batch_ids'],result
    assert not rig['store'].records(kind='TRADE') and not d['financial_authority']


def test_partial_provider_failure_keeps_successful_books_and_retries_do_not_bypass_cooldown(rig,monkeypatch):
    rt=assembled(rig,monkeypatch,census=False);prepare(rig,rt);calls=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport_for(rig,calls,fail_awc=True))) as client:
            cw=worker(rig,rt,client);one=await cw.step('one');two=await cw.step('two')
            return one,two
    one,two=asyncio.run(run());d=one['body']['details']
    assert d['outcome']=='GATED' and len(d['book_ids'])==6 and not d['source_ids']
    assert 'TRANSPORT_FAILURE' in d['errors'] and rt.queue.snapshot()['needs_census']
    assert two['body']['details']['outcome']=='IDLE_OR_COOLDOWN' and len(calls)==7


def test_cancellation_runtime_runs_while_census_waits_for_network_and_keeps_reservation(rig,monkeypatch):
    c=coordinator(rig);p=proposal(rig,units='2');c.coordinate('reserve',(p,))
    rt=assembled(rig,monkeypatch,census=False);prepare(rig,rt);calls=[]
    async def run():
        entered=asyncio.Event();release=asyncio.Event();base=transport_for(rig,calls)
        async def blocked(req):
            entered.set();await release.wait();return base(req)
        async with httpx.AsyncClient(transport=httpx.MockTransport(blocked)) as client:
            task=asyncio.create_task(worker(rig,rt,client).step('one'))
            await asyncio.wait_for(entered.wait(),2)
            SafetyReductions(rig['store']).apply('halt',scope='ACCOUNT',scope_id='account',action='CANCEL_AND_HALT',actor='fixture',reason='TEST')
            result=rt.tick('during-network')['body']['details']
            assert c._state(c._head())['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED'
            assert Decimal(c.snapshot()['reserved_cash'])==Decimal('.8')
            assert not task.done() and not result['real_orders_sent']
            release.set();return await task
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='CENSUS_SOURCE_COVERAGE_ONLY',d
    assert not rig['store'].records(kind='TRADE')


@pytest.mark.parametrize('bad', ['unsynchronized','backward'])
def test_bad_clock_makes_no_public_requests_and_preserves_need(rig,monkeypatch,bad):
    rt=assembled(rig,monkeypatch,census=False);prepare(rig,rt);calls=[]
    if bad=='unsynchronized':rig['sync'][0]=False
    else:rig['now'][0]-=10;rig['mono'][0]+=1
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport_for(rig,calls))) as client:
            return await worker(rig,rt,client).step('bad')
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='DEFERRED_CLOCK_UNHEALTHY' and not calls and rt.queue.snapshot()['needs_census']


def test_missing_actual_forecast_adapter_is_not_replaced_with_receipt_time_or_observation(rig,monkeypatch):
    rt=assembled(rig,monkeypatch,census=False)
    rt.queue=EventQueue(rig['store'],routes=tuple(replace(r,required_source_kinds=('MODEL',)) for r in rt.queue.routes.values()),policy=rt.queue.policy)
    prepare(rig,rt);calls=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport_for(rig,calls))) as client:
            return await worker(rig,rt,client).step('no-model')
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='GATED' and d['errors']==['CENSUS_REQUIRED_SOURCE_ADAPTER_UNAVAILABLE']
    assert not calls and rt.queue.snapshot()['needs_census']


def test_interrupted_collection_is_not_repeated_under_same_identity(rig,monkeypatch):
    rt=assembled(rig,monkeypatch,census=False);prepare(rig,rt);calls=[]
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport_for(rig,calls))) as client:
            cw=worker(rig,rt,client)
            async def crash(*args):raise RuntimeError('POWER_LOSS')
            monkeypatch.setattr(cw.scheduled,'cycle',crash)
            with pytest.raises(RuntimeError,match='POWER_LOSS'):await cw.step('one')
            monkeypatch.undo()
            return await cw.step('one')
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='INTERRUPTED_COLLECTION_NOT_RETRIED' and not calls
    assert rt.queue.snapshot()['active']


def test_total_network_budget_leaves_incomplete_census_gated(rig,monkeypatch):
    rt=assembled(rig,monkeypatch,census=False);prepare(rig,rt)
    async def blocked(req):await asyncio.sleep(2);return httpx.Response(503)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(blocked)) as client:
            return await worker(rig,rt,client,policy=CensusPolicy('fixture',maximum_seconds=.02)).step('timeout')
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='GATED' and 'CENSUS_COLLECTION_DEADLINE' in d['errors'] and not d['coverage_id']
    assert rt.queue.snapshot()['needs_census']


def test_batched_event_census_respects_durable_cooldown_and_full_token_coverage(rig,monkeypatch):
    rig['rule']=make_rule(labels=['60°F or lower']+[f'{t}°F' for t in range(61,70)]+['70°F or higher'])
    rt=assembled(rig,monkeypatch,census=False);prepare(rig,rt);calls=[];waits=[]
    async def wait(seconds):waits.append(seconds);advance(rig,seconds)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport_for(rig,calls))) as client:
            return await worker(rig,rt,client,sleeper=wait).step('batch')
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='CENSUS_SOURCE_COVERAGE_ONLY',d
    assert len(calls)==23 and len(d['book_ids'])==22 and len(d['collections'])==2 and waits==[1.]


def test_delayed_normalization_of_pre_claim_raw_cannot_satisfy_fresh_census(books):
    from polymarket_scanner.v11.event_queue import EventRoute, TriggerPolicy, KINDS
    r=books['rule'];p=r.payload
    route=EventRoute(p['event_id'],p['station'],p['target_date'],p['family'],r.sha256,
        tuple(b[s+'_token'] for b in p['partition'] for s in ('yes','no')),books['now'][0]+100,())
    policy=TriggerPolicy('fixture',4,4,8,32,30.,10.,200_000,60.,tuple((k,60.) for k in sorted(KINDS)),())
    q=EventQueue(books['store'],routes=(route,),policy=policy)
    raw(books);q.schedule_census('need')
    with q.work('claim'):
        keys=[normalize(books)['id']]
        for i,token in enumerate(route.tokens):
            if token==books['token']:continue
            row=books['store'].capture('direct'+str(i),event_id=p['event_id'],kind='BOOK',provider='fixture',source_identity=token,
                revision='one',observed_at=books['now'][0],evidence_class='SYNTHETIC',payload=dict(token_id=token,
                rule_fingerprint=r.sha256,snapshot_type='FULL',stream_healthy=True))
            keys.append(row['id'])
        books['store'].audit('rules',event_id=p['event_id'],kind='RULE_STATE',details=dict(fingerprint=r.sha256,quarantined=False))
        with pytest.raises(EvidenceError,match='FRESH_RAW_RECEIPT_LINEAGE'):
            q.complete_census('coverage',claim_id='claim',book_ids=tuple(keys),source_ids=(),rule_state_id='rules')
        assert q.snapshot()['needs_census']


def test_new_gap_during_collection_cannot_be_cleared_by_census_started_before_it(rig,monkeypatch):
    rt=assembled(rig,monkeypatch,census=False);prepare(rig,rt);calls=[]
    base=transport_for(rig,calls)
    def gap(req):
        result=base(req)
        if len(calls)==3:
            rt.queue.stream_gap('later-gap',event_id=rig['context'].event_id,reason='DISCONNECT')
        return result
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(gap)) as client:
            return await worker(rig,rt,client).step('racing-gap')
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='GATED' and 'CENSUS_LOSS_AFTER_CLAIM_REQUIRES_RESTART' in d['errors']
    assert len(d['book_ids'])==6 and rt.queue.snapshot()['needs_census']


def test_gap_after_coverage_before_finish_keeps_worker_gated_and_retry_retained(rig,monkeypatch):
    rt=assembled(rig,monkeypatch,census=False);prepare(rig,rt);calls=[]
    original=rt.queue.finish
    def race(*args,**kw):
        rt.queue.stream_gap('after-coverage',event_id=rig['context'].event_id,reason='DISCONNECT')
        return original(*args,**kw)
    monkeypatch.setattr(rt.queue,'finish',race)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport_for(rig,calls))) as client:
            return await worker(rig,rt,client).step('late-gap')
    d=asyncio.run(run())['body']['details']
    assert d['coverage_id'] and d['outcome']=='GATED'
    assert 'CENSUS_COVERAGE_INVALIDATED_BEFORE_COMPLETION' in d['errors']
    assert rig['context'].event_id in d['state']['retry_at'] and rt.queue.snapshot()['needs_census']
