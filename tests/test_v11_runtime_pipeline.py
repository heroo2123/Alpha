from dataclasses import replace

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
