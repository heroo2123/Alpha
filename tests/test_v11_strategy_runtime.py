import asyncio
from dataclasses import replace
from decimal import Decimal

import httpx
import pytest

from polymarket_scanner.v11.audit_reports import AuditWorker
from polymarket_scanner.v11.book_inputs import BookPolicy, PROVIDER
from polymarket_scanner.v11.candidate_runner import CandidateRunner, CandidatePolicy
from polymarket_scanner.v11.census_worker import CensusWorker, CensusPolicy, CensusPlan
from polymarket_scanner.v11.collection import PublicCollector
from polymarket_scanner.v11.discovery import MarketDiscovery, DiscoveryPolicy
from polymarket_scanner.v11.event_risk import EventRiskEngine
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.observation_runtime import ScheduledCollector
from polymarket_scanner.v11.paper_runtime import PaperRuntime, RuntimePolicy, TemperatureEventAdapter, Evaluation
from polymarket_scanner.v11.runtime_health import SourceNeed
from polymarket_scanner.v11.strategy_admission import StrategyAdmission
from polymarket_scanner.v11.strategy_runtime import RelativeValueEventAdapter, MultiStrategyEventAdapter
from polymarket_scanner.v11.valuation import contract_target
from test_v11_basket_coordinator import rig
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_strategy_pipeline import factory
from test_v11_relative_value import request
from test_v11_pws_admission import coordinator
from test_v11_paper_runtime import queue
from test_v11_runtime_health import monitor, ready
from test_v11_event_risk import policy as event_policy, metrics
from test_v11_book_inputs import response
from test_v11_valuation import costs


def joined(rig,monkeypatch,*,unknown_cost=False,gap_before_reserve=False):
    store=rig['store'];event=rig['context'].event_id;strategy=rig['scope'].strategy
    c=coordinator(rig);q=queue(rig)
    health=monitor(rig,monkeypatch,scopes={event:(strategy,)},sources=(SourceNeed(event,strategy,'MODEL','fixture','model-1',120.),))
    ready(rig,health)
    def requests(claim):
        prefix=claim['claim_id'];legs=[]
        for old in rig['kw']['legs']:
            token=contract_target(rig['rule'],old.market_id,old.side)['token_id']
            book=store.latest_source(kind='BOOK',event_id=event,provider=PROVIDER,source_identity=token)
            legs.append(replace(old,book_id=book['id']))
        if unknown_cost:legs[1]=replace(legs[1],costs=costs(ACQUISITION_FEES=None))
        official=store.latest_source(kind='OFFICIAL_OBSERVATION',event_id=event,provider='NOAA_AWC',source_identity=rig['context'].station_id)
        state=EventRiskEngine(store).step(prefix+':state',context=rig['context'],policy=event_policy(),binding=rig['binding'],
            metrics=metrics(rig['now'][0]),book_ids=tuple(l.book_id for l in legs),source_ids=('model2',official['id']))
        pin=StrategyAdmission(store).pin(prefix+':pin',**rig['admission_kw'])
        return (request(rig,admission_id=pin['id'],event_state_id=state['id'],instruments=tuple(legs)),)
    def missing(claim):raise EvidenceError('TEMPERATURE_PROVIDER_ADAPTER_UNAVAILABLE')
    adapter=MultiStrategyEventAdapter(store,(('unavailable_temperature',TemperatureEventAdapter(store,missing)),
        ('relative_value',RelativeValueEventAdapter(store,requests))))
    rt=PaperRuntime(c,q,health,RuntimePolicy('strategy-fixture'),evaluator=adapter,worker_id='worker',generation='strategies')
    if gap_before_reserve:
        original=c.coordinate
        def race(*args,**kw):
            q.stream_gap('gap-before-account',event_id=event,reason='SYNTHETIC_RACE')
            return original(*args,**kw)
        monkeypatch.setattr(c,'coordinate',race)
    return rt


def run_candidate(rig,rt):
    calls=[]
    def transport(req):
        calls.append(req)
        if req.url.host=='gamma-api.polymarket.com':return httpx.Response(200,json=dict(events=[],next_cursor=None))
        if req.url.host=='aviationweather.gov':return httpx.Response(200,json=[dict(icaoId=rig['context'].station_id,obsTime=rig['now'][0]-1,temp=25)])
        book=response(rig,req.url.params['token_id'])
        book.update(asks=[dict(price='.2',size='20')],bids=[dict(price='.1',size='20')],min_order_size='1')
        return httpx.Response(200,json=book)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            scheduled=ScheduledCollector(PublicCollector(rig['store'],client,attempts=1))
            cw=CensusWorker(scheduled,rt.queue,rt.health,plans=(CensusPlan(rig['rule'],'FIXTURE_COLLATERAL'),),
                policy=CensusPolicy('fixture'),book_policy=BookPolicy('fixture'))
            runner=CandidateRunner(rt,CandidatePolicy('strategy-fixture',maximum_jobs=1,maximum_seconds=5,safety_interval_seconds=.05),
                census=cw,discovery=MarketDiscovery(scheduled,rt.health,DiscoveryPolicy('fixture')),
                audits=AuditWorker(rt.coordinator,rt.audits.policy))
            result=await runner.run('joined-strategies')
            before=rt.coordinator._head()
            assert await runner.run('joined-strategies')==result and rt.coordinator._head()==before
            return result
    result=asyncio.run(run())['body']['details']
    assert len(calls)==7 and result['all_async_jobs_drained'] and not result['financial_authority']
    return result


@pytest.mark.parametrize('rig',['CROSS_TEMP_RELATIVE_VALUE','STRUCTURAL'],indirect=True)
def test_public_mock_census_through_real_protected_basket_pipeline_and_common_account(rig,monkeypatch):
    rt=joined(rig,monkeypatch);result=run_candidate(rig,rt);store=rig['store']
    state=rt.coordinator._state(rt.coordinator._head())
    assert state.get('baskets'),[(row['id'],row['body'].get('details',{}).get('outcome'),
        row['body'].get('details',{}).get('reason')) for kind in ('MEASUREMENT','RUNTIME_STATUS')
        for row in store.records(kind=kind,limit=500) if row['body'].get('details',{}).get('reason')]
    assert len(state['baskets'])==1 and len(state['intents'])==3
    assert Decimal(rt.coordinator.snapshot()['reserved_cash'])==Decimal('1.2')
    assert not store.records(kind='TRADE') and not state['financial_authority']
    intents=list(state['intents'].values())
    assert all(i['joint_ev_only'] and i['conservative_ev_total'] is None for i in intents)
    completion=store.get(intents[0]['event_queue_completion_id'])['body']['details']
    assert completion['result']['outcome']=='RESEARCH_EVALUATED'
    outputs=[store.get(key) for key in completion['request']['result_ids']]
    assert any(x['body']['details'].get('reason')=='TEMPERATURE_PROVIDER_ADAPTER_UNAVAILABLE' for x in outputs)
    assert any(x['body']['details'].get('proposal',{}).get('valuation_id')==intents[0]['valuation_id'] for x in outputs)
    assert not result['forward_acceptance']


@pytest.mark.parametrize('fault',['unknown_cost','gap_before_reserve'])
def test_unknown_fee_or_gap_after_evaluation_cannot_reserve_partial_basket(rig,monkeypatch,fault):
    rt=joined(rig,monkeypatch,**{fault:True});run_candidate(rig,rt)
    state=rt.coordinator._state(rt.coordinator._head())
    assert not state['intents'] and not state.get('baskets')
    assert Decimal(rt.coordinator.snapshot()['reserved_cash'])==0
    if fault=='gap_before_reserve':assert rt.queue.snapshot()['needs_census']


def test_overlarge_combined_result_gates_without_selecting_a_favored_prefix(rig,monkeypatch):
    store=rig['store'];event=rig['context'].event_id
    rows=[]
    for i in range(6):rows.append(store.audit('output'+str(i),event_id=event,kind='MEASUREMENT',details={'outcome':'SYNTHETIC_BOUND_FIXTURE'})['id'])
    a=TemperatureEventAdapter(store,lambda claim:())
    monkeypatch.setattr(a,'evaluate',lambda *args:Evaluation(tuple(rows),(rig['proposal'],)*6))
    b=RelativeValueEventAdapter(store,lambda claim:())
    monkeypatch.setattr(b,'evaluate',lambda *args:Evaluation((rows[0],),(rig['proposal'],)))
    adapter=MultiStrategyEventAdapter(store,(('a',a),('b',b)))
    result=adapter.evaluate({'event_id':event},'bound')
    assert not result.proposals and len(result.result_ids)==1
    assert store.get(result.result_ids[0])['body']['details']['reason']=='RUNTIME_COMBINED_STRATEGY_BOUND'
    assert all(store.get(key) for key in rows) and coordinator(rig)._head() is None


def test_wrong_event_child_output_cannot_reach_queue_or_common_account(rig,monkeypatch):
    store=rig['store'];row=store.audit('foreign-output',event_id='other',kind='MEASUREMENT',details={'outcome':'SYNTHETIC_SCOPE_FIXTURE'})
    a=TemperatureEventAdapter(store,lambda claim:())
    monkeypatch.setattr(a,'evaluate',lambda *args:Evaluation((row['id'],),(rig['proposal'],)))
    result=MultiStrategyEventAdapter(store,(('a',a),)).evaluate({'event_id':rig['context'].event_id},'wrong-event')
    assert not result.proposals and store.get(result.result_ids[0])['body']['details']['reason']=='RUNTIME_EVALUATION_EVENT_MISMATCH'


def test_relative_request_budget_gates_before_unbounded_strategy_work(rig):
    req=request(rig)
    adapter=RelativeValueEventAdapter(rig['store'],lambda claim:(req,req))
    with pytest.raises(EvidenceError,match='REQUEST_BOUND'):adapter.evaluate({'event_id':rig['context'].event_id},'many')
    assert coordinator(rig)._head() is None


def test_duplicate_adapter_identity_is_not_silently_overwritten(rig):
    a=TemperatureEventAdapter(rig['store'],lambda claim:())
    with pytest.raises(EvidenceError,match='DUPLICATE'):
        MultiStrategyEventAdapter(rig['store'],(('lane',a),('lane',a)))
