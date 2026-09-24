from dataclasses import replace
import asyncio

import httpx
import pytest

from polymarket_scanner.v11 import paper_runtime as runtime
from polymarket_scanner.v11.runtime_health import SourceNeed
from polymarket_scanner.v11.paper_coordinator import PaperAccountPolicy, PaperCoordinator
from polymarket_scanner.v11.strategy_admission import StrategyAdmission, SourceLease
from polymarket_scanner.v11.event_risk import EventRiskEngine
from polymarket_scanner.v11.valuation import contract_target
from test_v11_strategy_pipeline import factory, setup, bundle
from test_v11_event_risk import policy as event_policy, metrics
from test_v11_runtime_health import monitor, ready
from test_v11_paper_runtime import queue
from test_v11_scenario_risk import mapping, limits
from test_v11_book_inputs import response


def test_real_protected_temperature_pipeline_runs_through_periodic_census_and_runtime_without_economic_stub(factory,monkeypatch):
    r=factory();store=r['store'];rule=r['rule'];event=r['context'].event_id
    account=PaperAccountPolicy('fixture','account','FIXTURE_COLLATERAL','10','10','10','10','.01','0',60.,10)
    c=PaperCoordinator(store,policy=account,correlation=mapping(rule),limits=limits())
    strategy=r['scope'].strategy
    m=monitor(r,monkeypatch,sources=(SourceNeed(event,strategy,'MODEL','fixture','model-1',120.),),scopes={event:(strategy,)})
    ready(r,m);q=queue(r);requests=[]
    def census(claim,prefix):
        books=[];selected=None
        for bucket in rule.payload['partition']:
            for side in ('yes','no'):
                token=bucket[side+'_token'];key=prefix+':'+str(len(books));books.append(key)
                row=store.capture(key,event_id=event,kind='BOOK',provider='fixture',source_identity=token,
                    revision=prefix,observed_at=r['now'][0],evidence_class='SYNTHETIC',payload=dict(
                    contract_target(rule,bucket['market_id'],side.upper()),rule_fingerprint=rule.sha256,snapshot_type='FULL',stream_healthy=True,
                    collateral_asset='FIXTURE_COLLATERAL',bids=[dict(price='.1',size='20')],asks=[dict(price='.2',size='20')]))
                if token==rule.payload['partition'][0]['yes_token']:selected=row['id']
        old=store.get('model2')['body'];model=prefix+':model'
        store.capture(model,event_id=event,kind='MODEL',provider='fixture',source_identity='model-1',revision=prefix,
                      observed_at=r['now'][0],issued_at=r['now'][0],payload=old['payload'],evidence_class='SYNTHETIC')
        old=store.get('official2')['body'];official=prefix+':official'
        store.capture(official,event_id=event,kind='OFFICIAL_OBSERVATION',provider='fixture',source_identity='KATL',revision=prefix,
                      observed_at=r['now'][0],payload=old['payload'],evidence_class='SYNTHETIC')
        state=EventRiskEngine(store).step(prefix+':risk',context=r['context'],policy=event_policy(),binding=r['binding'],
             metrics=metrics(r['now'][0]),book_ids=(selected,),source_ids=(model,official))
        kw=dict(r['admission_kw'],source_leases=(SourceLease(model,'MODEL',120.),))
        pin=StrategyAdmission(store).pin(prefix+':pin',**kw)
        requests[:]=[replace(r['request'],admission_id=pin['id'],book_id=selected,model_input_ids=(model,),event_state_id=state['id'])]
        guard=store.latest(kind='RULE_STATE',event_id=event)
        return dict(book_ids=tuple(books),source_ids=(model,official),rule_state_id=guard['id'])
    adapter=runtime.TemperatureEventAdapter(store,lambda claim:tuple(requests))
    rt=runtime.PaperRuntime(c,q,m,runtime.RuntimePolicy('fixture'),evaluator=adapter,census=census)
    d=rt.tick('integrated')['body']['details']
    assert d['outcome']=='TICK_COMPLETED',d
    assert len(d['evaluation_ids'])==1 and not q.snapshot()['needs_census']
    completion=store.get(d['evaluation_ids'][0])['body']['details']
    result=store.get(completion['request']['result_ids'][0])['body']['details']
    assert result['reason']=='CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD'
    assert result['outcome']=='REJECT'
    assert result['prediction']['calibration_status']=='UNCALIBRATED'
    assert result['artifact_refs'] and result['model_epoch']==1
    assert not d['account_batch_ids'] and c.snapshot()['reserved_cash']=='0'
    assert not store.records(kind='TRADE') and not d['forward_or_live_acceptance']


@pytest.mark.parametrize('use_runner',[False,True])
def test_http_books_and_official_proxy_feed_real_protected_temperature_evaluation(factory,monkeypatch,use_runner):
    from polymarket_scanner.v11.book_inputs import BookPolicy, PROVIDER
    from polymarket_scanner.v11.census_worker import CensusWorker, CensusPlan, CensusPolicy
    from polymarket_scanner.v11.collection import PublicCollector
    from polymarket_scanner.v11.observation_runtime import ScheduledCollector
    r=factory();store=r['store'];rule=r['rule'];event=r['context'].event_id
    account=PaperAccountPolicy('fixture','account','FIXTURE_COLLATERAL','10','10','10','10','.01','0',60.,10)
    c=PaperCoordinator(store,policy=account,correlation=mapping(rule),limits=limits());q=queue(r)
    strategy=r['scope'].strategy
    m=monitor(r,monkeypatch,sources=(SourceNeed(event,strategy,'MODEL','fixture','model-1',120.),),scopes={event:(strategy,)})
    ready(r,m)
    def requests(claim):
        book=store.latest_source(kind='BOOK',event_id=event,provider=PROVIDER,source_identity=rule.payload['partition'][0]['yes_token'])
        official=store.latest_source(kind='OFFICIAL_OBSERVATION',event_id=event,provider='NOAA_AWC',source_identity=rule.payload['station'])
        prefix=claim['claim_id']
        state=EventRiskEngine(store).step(prefix+':risk',context=r['context'],policy=event_policy(),binding=r['binding'],
            metrics=metrics(r['now'][0]),book_ids=(book['id'],),source_ids=('model2',official['id']))
        pin=StrategyAdmission(store).pin(prefix+':pin',**r['admission_kw'])
        return (replace(r['request'],admission_id=pin['id'],book_id=book['id'],event_state_id=state['id']),)
    rt=runtime.PaperRuntime(c,q,m,runtime.RuntimePolicy('fixture'),evaluator=runtime.TemperatureEventAdapter(store,requests),
        worker_id='worker',generation='http-integration')
    rt.tick('schedule')
    async def run():
        def transport(req):
            if req.url.host=='aviationweather.gov':
                return httpx.Response(200,json=[dict(icaoId=rule.payload['station'],obsTime=r['now'][0]-1,temp=24)])
            if req.url.host=='gamma-api.polymarket.com':
                return httpx.Response(200,json=dict(events=[],next_cursor=None))
            return httpx.Response(200,json=response(r,req.url.params['token_id']))
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            scheduled=ScheduledCollector(PublicCollector(store,client,attempts=1))
            worker=CensusWorker(scheduled,q,m,
                plans=(CensusPlan(rule,'FIXTURE_COLLATERAL'),),policy=CensusPolicy('fixture'),book_policy=BookPolicy('fixture'))
            if not use_runner:return await worker.step('http-census'),None
            from polymarket_scanner.v11.candidate_runner import CandidateRunner,CandidatePolicy
            from polymarket_scanner.v11.discovery import MarketDiscovery,DiscoveryPolicy
            from polymarket_scanner.v11.audit_reports import AuditWorker
            runner=CandidateRunner(rt,CandidatePolicy('pipeline',maximum_seconds=5,maximum_jobs=3,
                safety_interval_seconds=.05,minimum_job_spacing_seconds=.05),census=worker,
                discovery=MarketDiscovery(scheduled,m,DiscoveryPolicy('fixture')),
                audits=AuditWorker(c,rt.audits.policy))
            result=await runner.run('http-candidate');d=result['body']['details']
            assert [j['kind'] for j in d['worker_results']]==['CENSUS','DISCOVERY','AUDIT'],d
            assert d['all_async_jobs_drained'] and not d['forward_acceptance']
            evaluated=[store.get(key)['body']['details'] for key in d['runtime_ids']
                if key and store.get(key)['body']['details'].get('evaluation_ids')]
            return store.get(d['worker_results'][0]['record_id']),evaluated
    covered,evaluated=asyncio.run(run());coverage=covered['body']['details']
    assert coverage['outcome']=='CENSUS_SOURCE_COVERAGE_ONLY',coverage
    d=evaluated[-1] if use_runner else rt.tick('evaluate-real')['body']['details']
    assert d['outcome']=='TICK_COMPLETED',d
    completion=store.get(d['evaluation_ids'][0])['body']['details']
    result=store.get(completion['request']['result_ids'][0])['body']['details']
    assert result['outcome']=='REJECT' and result['reason']=='CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD', result['reason']
    assert result['prediction']['calibration_status']=='UNCALIBRATED' and result['model_epoch']==1
    assert result['artifact_refs'] and not d['account_batch_ids'] and not store.records(kind='TRADE')
    assert c.snapshot()['reserved_cash']=='0' and not d['forward_or_live_acceptance']
