import asyncio
from dataclasses import replace
import fcntl
import os

import httpx
import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.maker_telemetry import MakerTelemetryPolicy, MakerTelemetryWorker
from polymarket_scanner.v11.runtime_health import SourceNeed, KEY as HEALTH_KEY
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_strategy_pipeline import factory
from test_v11_maker_research import rig, maker, book, features
from test_v11_runtime_health import monitor, ready, advance


def worker(r,monkeypatch,*,maximum_actions=4,fee='.01'):
    event=r['context'].event_id
    health=monitor(r,monkeypatch,scopes={event:('MAKER_RESEARCH',)},sources=(
        SourceNeed(event,'MAKER_RESEARCH','MODEL','fixture','model-1',120.),))
    ready(r,health)
    research=maker(r)
    assert research.propose('retained-proposal',r['quote'])['body']['details']['outcome']=='OBSERVING_RESEARCH_QUOTE'
    p=MakerTelemetryPolicy('fixture',maximum_actions=maximum_actions,fee_per_share=fee)
    return MakerTelemetryWorker(research,health,p,event_ids=(event,))


def sample(r,w):
    head=w.store.latest(kind='RUNTIME_STATUS',event_id=HEALTH_KEY)
    # Unique heartbeat/sample IDs; source freshness and clock progression still
    # run through the actual health monitor, never an invented passed record.
    key='later:'+str(r['now'][0])+':'+str(head['seq'] if head else 0)
    w.health.heartbeat(key,worker='worker',generation='one')
    w.health.sample(key+':sample')


def results(w,key='step'):
    return w.step(key)['body']['details']['results']


def marks(w):
    return [r['body']['details'] for r in w.store.records(kind='MEASUREMENT',limit=1000)
            if r['body']['details'].get('request',{}).get('action')=='MARKOUT']


def test_fresh_book_updates_retained_quote_without_fills_or_duplicate_sample(rig,monkeypatch):
    w=worker(rig,monkeypatch);advance(rig,1);book(rig,'next')
    rows=results(w);q=w.research._state(w.research._head())['quote']
    assert [x['action'] for x in rows]==['OBSERVE']
    assert q['distinct_book_samples']==2 and q['sampled_span_seconds']==1
    head=w.research._head();saved=w.step('step')
    assert w.step('step')==saved and w.research._head()==head
    assert results(w,'unchanged')==[] and not marks(w)
    assert w.research.coordinator._head() is None and not rig['store'].records(kind='TRADE')


@pytest.mark.parametrize('change',[dict(previous=0),dict(epoch='reconnected')])
def test_sequence_loss_resets_sample_span_without_claiming_survival(rig,monkeypatch,change):
    w=worker(rig,monkeypatch);advance(rig,1);book(rig,'gap',**change);results(w)
    q=w.research._state(w.research._head())['quote']
    assert q['gap_count']==1 and q['sampled_span_seconds']==0
    assert q['fill_status']=='UNKNOWN_NOT_AN_ORDER'


def test_first_horizon_book_is_used_after_window_closes_and_later_price_cannot_replace_it(rig,monkeypatch):
    w=worker(rig,monkeypatch);advance(rig,1)
    book(rig,'first',bids=[dict(price='.12',size='20')]);results(w,'before-close')
    assert not marks(w)
    advance(rig,1);book(rig,'later',sequence=3,previous=2,bids=[dict(price='.19',size='20')])
    advance(rig,1);results(w,'closed')
    m=marks(w);assert len(m)==1 and m[0]['book_id']=='first'
    assert m[0]['status']=='MEASURED' and m[0]['markout_per_share']=='0.00'
    assert m[0]['measurement_class']=='HYPOTHETICAL_QUOTE_ENTRY_NOT_FILL_OR_TRADING_PNL'
    results(w,'repeat');assert marks(w)==m and w.research.coordinator._head() is None


def test_no_horizon_book_remains_unknown_and_is_not_rewritten_after_late_arrival(rig,monkeypatch):
    w=worker(rig,monkeypatch);advance(rig,3);results(w)
    m=marks(w);assert len(m)==1 and m[0]['status']=='UNKNOWN'
    advance(rig,.1);book(rig,'too-late');results(w,'late')
    assert marks(w)==m


def test_unknown_fee_never_becomes_zero_cost_markout(rig,monkeypatch):
    w=worker(rig,monkeypatch,fee=None);advance(rig,1);book(rig,'first')
    advance(rig,2);results(w)
    m=marks(w)[0]
    assert m['status']=='UNKNOWN' and m['reason']=='HORIZON_FEES_UNKNOWN' and m['markout_per_share'] is None


def test_lost_source_retires_locally_and_does_not_change_account(rig,monkeypatch):
    w=worker(rig,monkeypatch);b=rig['store'].get('model2')['body']
    rig['store'].capture('changed-model',event_id=rig['context'].event_id,kind='MODEL',provider=b['provider'],
        source_identity=b['source_identity'],revision='changed',issued_at=rig['now'][0],payload=b['payload'],evidence_class='SYNTHETIC')
    rows=results(w)
    assert rows[0]['action']=='RETIRE'
    assert w.research._state(w.research._head())['quote']['status']=='RETIRED'
    assert w.research.coordinator._head() is None


def test_clock_loss_refuses_new_measurement_without_refreshing_state(rig,monkeypatch):
    w=worker(rig,monkeypatch);rig['sync'][0]=False;sample(rig,w)
    before=rig['store'].pin_read_view()
    with pytest.raises(EvidenceError,match='CLOCK_UNVERIFIED'):w.step('lost-clock')
    assert rig['store'].pin_read_view()==before


def test_interruption_after_child_commit_reuses_exact_action_without_duplicate_observation(rig,monkeypatch):
    w=worker(rig,monkeypatch);advance(rig,1);book(rig,'next');original=w._execute
    def interrupt(command):
        original(command)
        raise OSError('SYNTHETIC_INTERRUPTION')
    monkeypatch.setattr(w,'_execute',interrupt)
    with pytest.raises(OSError):w.step('interrupted')
    pending=w._head()['body']['details']['state']['pending'];head=w.research._head()
    restarted=MakerTelemetryWorker(w.research,w.health,w.policy,event_ids=w.event_ids)
    rows=results(restarted,'recovered')
    assert rows[0]['record_id']==pending['id'] and restarted.research._head()==head
    assert restarted._head()['body']['details']['state']['pending'] is None


def test_action_budget_preserves_due_work_and_configuration_change_cannot_replace_history(rig,monkeypatch):
    w=worker(rig,monkeypatch,maximum_actions=1);advance(rig,1);book(rig,'first')
    advance(rig,2)
    assert results(w,'one')[0]['action']=='MARKOUT' and len(marks(w))==1
    before=w._head()
    changed=MakerTelemetryWorker(w.research,w.health,replace(w.policy,fee_per_share='.02'),event_ids=w.event_ids)
    with pytest.raises(EvidenceError,match='CONFIGURATION_CHANGED'):changed.step('changed')
    assert w._head()==before
    assert results(w,'two')[0]['action']=='OBSERVE' and len(marks(w))==1


def test_continuous_book_stream_cannot_starve_a_due_markout_at_minimum_action_budget(rig,monkeypatch):
    w=worker(rig,monkeypatch,maximum_actions=1)
    for i in range(1,4):
        advance(rig,1);book(rig,'update-'+str(i),sequence=i+1,previous=i)
        rows=results(w,'step-'+str(i))
        assert rows[0]['action']==('MARKOUT' if i==3 else 'OBSERVE')
    assert marks(w)[0]['book_id']=='update-1'


def test_all_five_horizons_remain_observable_after_quote_retirement(rig,monkeypatch):
    w=worker(rig,monkeypatch,maximum_actions=1)
    created=w.research._state(w.research._head())['quote']['created_at']
    w.research.retire('withdraw',quote_id='quote',reason='RESEARCH_STOP')
    for horizon in (1,5,30,120,600):
        advance(rig,created+horizon-rig['now'][0]);book(rig,'horizon-'+str(horizon))
        advance(rig,2);sample(rig,w)
        rows=results(w,'closed-'+str(horizon))
        assert rows[0]['action']=='MARKOUT'
    recorded=marks(w)
    assert [m['request']['horizon_seconds'] for m in recorded]==[1,5,30,120,600]
    assert all(m['status']=='MEASURED' for m in recorded)
    assert w.research._state(w.research._head())['quote']['status']=='RETIRED'
    assert w.research.coordinator._head() is None


def test_existing_worker_lock_refuses_duplicate_work(rig,monkeypatch):
    w=worker(rig,monkeypatch)
    fd=os.open(w.store.path.with_name(w.store.path.name+'.maker-telemetry.lock'),os.O_CREAT|os.O_WRONLY,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(EvidenceError,match='ALREADY_RUNNING'):w.step('duplicate')
    finally:os.close(fd)
    assert w._head() is None


def test_typed_candidate_dispatches_maker_telemetry_with_shared_safety_and_account(rig,monkeypatch):
    from polymarket_scanner.v11 import candidate_assembly as app
    from test_v11_candidate_assembly import scoped_plan, synthetic_clock, transport
    from test_v11_request_assembly import inputs, target
    scope=inputs(rig)
    # The ordinary lane lacks a reviewed temperature scope and must stay gated;
    # the retained maker fixture has its own real admission checks.
    lane=app.TemperatureLane('temperature',replace(scope,scope=replace(scope.scope,strategy='FUTURE_FORECAST')),
        (target(rig),),'fixture',rig['request'].valuation_policy,10.)
    cfg=scoped_plan(rig,lane)
    cfg=replace(cfg,candidate=replace(cfg.candidate,maximum_jobs=4),maker=app.MakerTelemetryPlan(
        rig['policy'],MakerTelemetryPolicy('fixture',fee_per_share='.01'),(scope,)))
    synthetic_clock(rig,monkeypatch);calls=[]
    async def run():
        async with httpx.AsyncClient(transport=transport(rig,calls)) as client:
            candidate=app.assemble_candidate(rig['store'],client,cfg,generation='maker-integrated')
            ready(rig,candidate.runtime.health)
            research=candidate.runtime.maker
            assert research.propose('retained',rig['quote'])['body']['details']['outcome']=='OBSERVING_RESEARCH_QUOTE'
            advance(rig,1);book(rig,'horizon');advance(rig,2)
            row=await candidate.run('maker-candidate')
            return candidate,row
    candidate,row=asyncio.run(run());d=row['body']['details']
    assert [j['kind'] for j in d['worker_results']]==['CENSUS','DISCOVERY','AUDIT','MAKER_TELEMETRY']
    assert d['worker_results'][-1]['outcome']=='BOUNDED_MAKER_TELEMETRY',d
    assert candidate.maker_telemetry.research is candidate.runtime.maker
    assert marks(candidate.maker_telemetry)[0]['status']=='MEASURED'
    assert candidate.runtime.coordinator.snapshot()['reserved_cash']=='0' and not rig['store'].records(kind='TRADE')
    assert d['all_async_jobs_drained'] and not d['forward_acceptance']
