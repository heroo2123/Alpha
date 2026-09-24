from dataclasses import replace

import pytest

from polymarket_scanner.v11 import candidate_assembly as app
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.maker_runtime import MakerTarget, MakerRequestFactory, MakerEventAdapter
from polymarket_scanner.v11.maker_telemetry import MakerTelemetryPolicy
from polymarket_scanner.v11.request_assembly import RequestAssembler
from polymarket_scanner.v11.runtime_health import SourceNeed
from polymarket_scanner.v11.strategy_runtime import MultiStrategyEventAdapter
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_strategy_pipeline import factory
from test_v11_maker_research import rig, maker, book
from test_v11_maker_context import same_day
from test_v11_request_assembly import inputs, target, evaluate, state_for
from test_v11_paper_runtime import queue
from test_v11_runtime_health import monitor, ready, advance
from test_v11_candidate_assembly import scoped_plan, evaluate_built_lane


def terms(r,**changes):
    return replace(MakerTarget(r['request'].market_id,'YES','BUY','.1','2','.01'),**changes)


def adapter(r,monkeypatch,*,payout=None,targets=None):
    scope=inputs(r)
    if r['scope'].strategy=='SAME_DAY_LATE_LOCK':scope=replace(scope,scope=replace(scope.scope,strategy='MAKER_RESEARCH'))
    strategies=('MAKER_RESEARCH',)+(('SAME_DAY_LATE_LOCK',) if payout is not None else ())
    health=monitor(r,monkeypatch,scopes={r['context'].event_id:strategies},sources=tuple(
        SourceNeed(r['context'].event_id,s,'MODEL','fixture','model-1',120.) for s in strategies))
    ready(r,health);q=queue(r);research=maker(r)
    state_for(r,('initial',),('model2','official2'))
    a=RequestAssembler(q,scope,book_provider='fixture',valuation_policy=r['request'].valuation_policy,lifetime_seconds=10.)
    f=MakerRequestFactory(a,research,targets or (terms(r),),microstructure=r['micro_policy'],payout_inputs=payout)
    return q,research,f,MakerEventAdapter(research,f)


def test_current_maker_factory_computes_protected_context_and_never_returns_an_account_proposal(rig,monkeypatch):
    q,research,f,a=adapter(rig,monkeypatch)
    result=evaluate(rig,q,MultiStrategyEventAdapter(rig['store'],(('maker',a),)),book='initial')
    d=rig['store'].get(result.result_ids[0])['body']['details']
    assert d['outcome']=='OBSERVING_RESEARCH_QUOTE' and d['context_outcome']=='MEASURED_RESEARCH_CONTEXT',d
    context=rig['store'].get(d['context_record_id'])['body']['details']
    assert context['fair_value']['calibration_status']=='UNCALIBRATED'
    assert not result.proposals and not d['account_proposal']
    assert research.coordinator._head() is None and not rig['store'].records(kind='TRADE')
    quote=research._state(research._head())[d['quote_id']]
    assert quote['request']['admission_id']!='pin' and quote['request']['event_state_id']=='assembled-risk'
    assert quote['fill_status']=='UNKNOWN_NOT_AN_ORDER'


@pytest.mark.parametrize('change,reason',[(dict(limit_price='.2'),'POST_ONLY_BOUNDARY'),
    (dict(direction='SELL',limit_price='.2'),'PENDING_SALES_EXCEED_HELD_INVENTORY')])
def test_quote_terms_cannot_cross_or_invent_inventory(rig,monkeypatch,change,reason):
    q,research,f,a=adapter(rig,monkeypatch,targets=(terms(rig,**change),))
    result=evaluate(rig,q,a,book='initial')
    d=rig['store'].get(result.result_ids[0])['body']['details']
    assert d['outcome']=='GATED' and reason in d['reason']
    assert not research._state(research._head()) and research.coordinator._head() is None


def test_next_claim_cannot_duplicate_an_observing_token_or_refresh_its_expiry(rig,monkeypatch):
    q,research,f,a=adapter(rig,monkeypatch)
    evaluate(rig,q,a,book='initial');quotes=research._state(research._head())
    advance(rig,1);book(rig,'next-book')
    state_for(rig,('next-book',),('model2','official2'),key='next-risk')
    result=evaluate(rig,q,a,book='next-book',key='next')
    d=rig['store'].get(result.result_ids[0])['body']['details']
    assert d['outcome']=='GATED' and 'EXISTING_THESIS_OR_TOKEN' in d['reason']
    assert research._state(research._head())==quotes and not result.proposals


@pytest.mark.parametrize('reviewed',[False,True])
def test_same_day_maker_context_needs_its_separately_reviewed_payout_scope(same_day,monkeypatch,reviewed):
    r=same_day;maker(r).retire('prior-retired',quote_id='quote',reason='RESEARCH_STOP')
    payout=inputs(r) if reviewed else None
    q,research,f,a=adapter(r,monkeypatch,payout=payout)
    result=evaluate(r,q,a,book='initial')
    d=r['store'].get(result.result_ids[0])['body']['details']
    assert d['outcome']=='OBSERVING_RESEARCH_QUOTE' and not result.proposals,d
    context=r['store'].get(d['context_record_id'])['body']['details']
    assert context['outcome']==('MEASURED_RESEARCH_CONTEXT' if reviewed else 'GATED'),context
    if not reviewed:assert context['reason']=='MAKER_SAME_DAY_REVIEWED_PAYOUT_PIN_REQUIRED'
    assert research.coordinator._head() is None


def test_current_factory_requires_held_claim_and_rejects_changed_bound_plan(rig,monkeypatch):
    q,research,f,a=adapter(rig,monkeypatch);before=rig['store'].pin_read_view()
    for claim in (None,dict(claim_id='invented',event_id=rig['context'].event_id)):
        with pytest.raises(EvidenceError,match='HELD_EVENT_CLAIM_REQUIRED'):f(claim)
    assert rig['store'].pin_read_view()==before
    f.targets=(terms(rig,units='3'),)
    with pytest.raises(EvidenceError,match='REQUEST_PLAN_CHANGED'):a.evaluate({},'changed')


def test_interrupted_context_does_not_duplicate_the_committed_research_quote(rig,monkeypatch):
    q,research,f,a=adapter(rig,monkeypatch);original=research.context
    monkeypatch.setattr(research,'context',lambda *a,**k:(_ for _ in ()).throw(OSError('SYNTHETIC_INTERRUPTION')))
    q.publish('update',kind='BOOK',evidence_id='initial')
    with q.work('claim') as claim:
        with pytest.raises(OSError):a.evaluate(claim,'partial')
        head=research._head();quotes=research._state(head)
        monkeypatch.setattr(research,'context',original)
        result=a.evaluate(claim,'partial')
        assert research._head()==head and research._state(research._head())==quotes
        assert not result.proposals
        q.finish('done',claim_id=claim['claim_id'],result_ids=result.result_ids)


def test_typed_maker_lane_shares_research_and_safety_objects_and_links_outputs_to_queue(rig,monkeypatch):
    scope=inputs(rig);state_for(rig,('initial',),('model2','official2'))
    ordinary=app.TemperatureLane('temperature',replace(scope,scope=replace(scope.scope,strategy='FUTURE_FORECAST')),
        (target(rig),),'fixture',rig['request'].valuation_policy,10.)
    cfg=scoped_plan(rig,ordinary)
    lane=app.MakerLane('maker',scope,(terms(rig),),'fixture',rig['request'].valuation_policy,10.,rig['micro_policy'])
    event=replace(cfg.events[0],risk_inputs=scope,lanes=(lane,))
    with pytest.raises(EvidenceError,match='REQUIRES_SHARED_TELEMETRY'):replace(cfg,events=(event,))
    cfg=replace(cfg,events=(event,),maker=app.MakerTelemetryPlan(rig['policy'],MakerTelemetryPolicy('fixture'),(scope,)))
    result=evaluate_built_lane(rig,cfg,monkeypatch)
    d=rig['store'].get(result.result_ids[0])['body']['details']
    assert d['outcome']=='OBSERVING_RESEARCH_QUOTE' and d['context_outcome']=='MEASURED_RESEARCH_CONTEXT',d
    assert not result.proposals and maker(rig).coordinator._head() is None
