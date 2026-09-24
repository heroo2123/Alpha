from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.event_risk import _key
from polymarket_scanner.v11.book_inputs import PROVIDER
from polymarket_scanner.v11.microstructure import MicrostructurePolicy, STREAM_VERSION
from polymarket_scanner.v11.paper_runtime import PaperRuntime
from polymarket_scanner.v11.request_assembly import RelativeValueRequestFactory, TargetPlan
from polymarket_scanner.v11.risk_inputs import EventRiskInputs, RiskInputPolicy, RiskAwareEventAdapter, VERSION
from polymarket_scanner.v11.strategy_runtime import MultiStrategyEventAdapter, RelativeValueEventAdapter
from polymarket_scanner.v11.valuation import contract_target
from test_v11_basket_coordinator import rig
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_strategy_pipeline import factory
from test_v11_request_assembly import assembler
from test_v11_strategy_runtime import joined as relative_runtime, run_candidate
from test_v11_event_risk import policy as event_policy
from test_v11_runtime_health import advance


def components(r,monkeypatch):
    rt = relative_runtime(r,monkeypatch); a = assembler(r,rt.queue,provider=PROVIDER)
    p = RiskInputPolicy('fixture',MicrostructurePolicy('fixture','FIXTURE_COLLATERAL',30.,60.,5.,.01,2),event_policy())
    worker = EventRiskInputs(a,rt.coordinator,p)
    targets = tuple(TargetPlan(l.market_id,l.side,l.units,l.units,l.costs) for l in r['kw']['legs'])
    f = RelativeValueRequestFactory(a,targets,basket_policy=r['kw']['policy'])
    e = MultiStrategyEventAdapter(r['store'],(('relative',RelativeValueEventAdapter(r['store'],f)),))
    return rt,worker,RiskAwareEventAdapter(e,(worker,))


def books(r,suffix,*,sequence=None,previous=None,skip=(),bid='.1',ask='.2',size='20'):
    result=[]
    for bucket in r['rule'].payload['partition']:
        for side in ('YES','NO'):
            target=contract_target(r['rule'],bucket['market_id'],side)
            if target['token_id'] in skip:continue
            p=dict(target,rule_fingerprint=r['rule'].sha256,collateral_asset='FIXTURE_COLLATERAL',
                stream_healthy=True,snapshot_type='FULL',bids=[dict(price=bid,size=size)],asks=[dict(price=ask,size=size)])
            if sequence is not None:p['book_sequence']=dict(version=STREAM_VERSION,epoch='fixture',sequence=sequence,previous_sequence=previous)
            key=target['token_id']+':'+suffix
            result.append(r['store'].capture(key,event_id=r['context'].event_id,kind='BOOK',provider=PROVIDER,
                source_identity=target['token_id'],revision=suffix,observed_at=r['now'][0],payload=p,evidence_class='SYNTHETIC')['id'])
    return result


def evaluate(r,rt,worker,current,prefix='risk'):
    rt.queue.publish(prefix+':update',kind='BOOK',evidence_id=current[0])
    with rt.queue.work(prefix+':claim') as claim:
        measured,state=worker.evaluate(claim,prefix)
        assert worker.evaluate(claim,prefix)==(measured,state)
        rt.queue.finish(prefix+':done',claim_id=claim['claim_id'],result_ids=(measured['id'],))
    return measured['body']['details'],state['body']['details']


def test_finite_candidate_derives_risk_without_supplied_metrics_and_keeps_unknown_gates(rig,monkeypatch):
    rt,worker,adapter=components(rig,monkeypatch)
    candidate=PaperRuntime(rt.coordinator,rt.queue,rt.health,rt.policy,evaluator=adapter,worker_id='worker',generation='derived-risk')
    run_candidate(rig,candidate)
    rows=[r['body']['details'] for r in rig['store'].records(kind='MEASUREMENT',limit=1000)
          if r['body']['details'].get('version')==VERSION]
    assert rows and rows[-1]['full_book_coverage']; d=rows[-1]; m=d['metrics']
    assert m['spread']==pytest.approx(.1) and m['clock_healthy'] and m['sources_healthy']
    assert d['prediction_sha256'] and m['model_age_seconds']>0 and m['model_disagreement']==0
    assert m['price_velocity'] is m['depth_loss'] is m['cross_bucket_motion'] is None
    assert not m['websocket_synchronized']
    assert m['time_to_settlement_seconds'] is m['adverse_fills'] is m['recent_markout_per_share'] is None
    state=candidate.coordinator._state(candidate.coordinator._head())
    assert not state['intents'] and not state['lots'] and not state['financial_authority']
    assert not rig['store'].records(kind='TRADE')


def test_linked_book_changes_produce_bounded_units_but_never_invent_execution_or_settlement(rig,monkeypatch):
    rt,worker,_=components(rig,monkeypatch); books(rig,'old',sequence=1); advance(rig)
    current=books(rig,'new',sequence=2,previous=1,bid='.2',ask='.3',size='10')
    rt.health.sample('fresh-health')
    d,state=evaluate(rig,rt,worker,current); m=d['metrics']
    assert m['websocket_synchronized'] and m['price_velocity']==pytest.approx(.1)
    assert m['depth_loss']==pytest.approx(.5) and m['cross_bucket_motion']==pytest.approx(.1)
    assert m['spread']==pytest.approx(.1) and m['loss_utilization']==0
    assert state['state']=='EVENT' and 'EXECUTION_HEALTH_UNKNOWN' in state['reasons']
    assert not d['settlement_finality'] and d['declared_sequence_is_not_transport_commissioning']


@pytest.mark.parametrize('defect',['gap','skew','missing_book'])
def test_gaps_misalignment_or_missing_bucket_do_not_become_temporal_zeros(rig,monkeypatch,defect):
    rt,worker,_=components(rig,monkeypatch); books(rig,'old',sequence=1); advance(rig)
    skip=(rig['rule'].payload['partition'][-1]['no_token'],) if defect!='gap' else ()
    if defect=='skew':advance(rig,3)
    if defect=='missing_book':
        # Old book is now stale rather than removed from the append-only archive.
        advance(rig,31)
    current=books(rig,'new',sequence=3 if defect=='gap' else 2,previous=2 if defect=='gap' else 1,skip=skip)
    # Renew only the real test worker heartbeat, never the input timestamps.
    rt.health.heartbeat('hb-current',worker='worker',generation='strategies');rt.health.sample('health-current')
    d,state=evaluate(rig,rt,worker,current)
    assert d['metrics']['price_velocity'] is d['metrics']['depth_loss'] is d['metrics']['cross_bucket_motion'] is None
    assert not d['metrics']['websocket_synchronized'] and state['state']=='EVENT'
    if defect=='missing_book':assert not d['full_book_coverage']


def test_source_arrival_during_risk_inference_rejects_coherent_measurement_publication(rig,monkeypatch):
    import polymarket_scanner.v11.risk_inputs as module
    rt,worker,_=components(rig,monkeypatch); current=books(rig,'now'); store=rig['store']; original=module.predict_with_bundle
    before=store.latest(kind='COORDINATOR_EVENT',event_id=_key('event-risk',rig['context'].event_id))
    def race(*args,**kwargs):
        result=original(*args,**kwargs); b=store.get('model2')['body']
        store.capture('racing-model',event_id=rig['context'].event_id,kind='MODEL',provider=b['provider'],source_identity=b['source_identity'],
            revision='race',issued_at=b['issued_at'],observed_at=b['observed_at'],payload=b['payload'],evidence_class='SYNTHETIC')
        return result
    monkeypatch.setattr(module,'predict_with_bundle',race)
    rt.queue.publish('update',kind='BOOK',evidence_id=current[0])
    with rt.queue.work('claim') as claim:
        with pytest.raises(EvidenceError,match='AUDIT_GUARDED_STATE_CHANGED'):worker.evaluate(claim,'race')
    assert store.latest(kind='COORDINATOR_EVENT',event_id=before['event_id'])==before


def test_changed_risk_policy_cannot_silently_change_runtime_measurements(rig,monkeypatch):
    rt,worker,_=components(rig,monkeypatch); current=books(rig,'now')
    worker.policy=replace(worker.policy,maximum_book_skew_seconds=1.)
    rt.queue.publish('update',kind='BOOK',evidence_id=current[0])
    with rt.queue.work('claim') as claim:
        with pytest.raises(EvidenceError,match='CONFIGURATION_CHANGED'):worker.evaluate(claim,'changed')


def test_missing_health_remains_unknown_even_with_all_synthetic_sources_present(rig):
    from test_v11_paper_runtime import queue
    from test_v11_pws_admission import coordinator
    from types import SimpleNamespace
    q=queue(rig); c=coordinator(rig); a=assembler(rig,q,provider=PROVIDER)
    policy=RiskInputPolicy('fixture',MicrostructurePolicy('fixture','FIXTURE_COLLATERAL',30.,60.,5.,.01,2),event_policy())
    worker=EventRiskInputs(a,c,policy); current=books(rig,'current')
    d,state=evaluate(rig,SimpleNamespace(queue=q),worker,current)
    assert not d['metrics']['clock_healthy'] and not d['metrics']['sources_healthy']
    assert 'RISK_RUNTIME_HEALTH_REQUIRED' in d['errors'] and state['state']=='EVENT'


def test_risk_derivation_includes_reserved_partial_fill_downside_and_retains_account(rig,monkeypatch):
    from test_v11_basket_coordinator import reserve
    reserve(rig)
    rt,worker,_=components(rig,monkeypatch); current=books(rig,'current'); before=rt.coordinator._head()
    d,state=evaluate(rig,rt,worker,current)
    assert d['metrics']['loss_utilization']==pytest.approx(.08)
    assert rt.coordinator._head()==before and not d['financial_authority']


def test_partial_risk_publication_requires_new_claim_without_refreshing_old_measurement(rig,monkeypatch):
    from polymarket_scanner.v11.event_risk import EventRiskEngine
    rt,worker,_=components(rig,monkeypatch); current=books(rig,'now')
    rt.queue.publish('update',kind='BOOK',evidence_id=current[0])
    with rt.queue.work('claim') as claim:
        with monkeypatch.context() as m:
            m.setattr(EventRiskEngine,'step',lambda *a,**kw:(_ for _ in ()).throw(EvidenceError('SYNTHETIC_INTERRUPTION')))
            with pytest.raises(EvidenceError,match='SYNTHETIC_INTERRUPTION'):worker.evaluate(claim,'partial')
        tip=rig['store'].pin_read_view()
        with pytest.raises(EvidenceError,match='PARTIAL_MEASUREMENT_REQUIRES_NEW_CLAIM'):worker.evaluate(claim,'partial')
        assert rig['store'].pin_read_view()==tip


def test_whole_event_and_account_configuration_bounds_are_not_silently_truncated(rig,monkeypatch):
    rt,worker,_=components(rig,monkeypatch)
    with pytest.raises(EvidenceError,match='WHOLE_EVENT_TOKEN_BOUND'):
        EventRiskInputs(worker.assembler,rt.coordinator,replace(worker.policy,maximum_tokens=2))
    with pytest.raises(EvidenceError,match='DUPLICATE_EVENT'):
        RiskAwareEventAdapter(rt.evaluator,(worker,worker))
