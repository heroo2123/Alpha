from copy import deepcopy
from dataclasses import replace
from decimal import Decimal
import fcntl
import os

import pytest

from polymarket_scanner.v11 import paper_runtime as runtime
from polymarket_scanner.v11.event_queue import EventQueue, EventRoute, TriggerPolicy, KINDS
from polymarket_scanner.v11.event_risk import SafetyReductions
from polymarket_scanner.v11.evidence import EvidenceError, digest
from test_v11_paper_coordinator import rig, coordinator, proposal, fill, proof
from test_v11_runtime_health import monitor, ready, advance


def queue(rig):
    p=rig['rule'].payload
    route=EventRoute(p['event_id'],p['station'],p['target_date'],p['family'],rig['rule'].sha256,
        tuple(t for b in p['partition'] for t in (b['yes_token'],b['no_token'])),rig['now'][0]+3600,('OFFICIAL_OBSERVATION',))
    policy=TriggerPolicy('runtime-fixture',4,4,8,32,30.,10.,200_000,60.,
        tuple((k,60.) for k in sorted(KINDS)),((p['station'],30.),))
    return EventQueue(rig['store'],routes=(route,),policy=policy)


class GatedEvaluator:
    def __init__(self,store): self.store=store; self.calls=0
    def evaluate(self,claim,prefix):
        self.calls+=1
        key='result:'+digest(prefix)
        self.store.audit(key,event_id=claim['event_id'],kind='MEASUREMENT',
                         details=dict(outcome='GATED',reason='SYNTHETIC_UNCALIBRATED_FIXTURE'))
        return runtime.Evaluation((key,))


def census_fixture(rig):
    """Explicit synthetic receipt fixture, never refetched historical live data."""
    def census(claim,prefix):
        store=rig['store'];rule=rig['rule'];p=rule.payload;books=[]
        for bucket in p['partition']:
            for token in (bucket['yes_token'],bucket['no_token']):
                key='full:'+digest([prefix,token]);books.append(key)
                store.capture(key,event_id=claim['event_id'],kind='BOOK',provider='fixture',source_identity=token,
                    revision=prefix,observed_at=rig['now'][0],evidence_class='SYNTHETIC',payload=dict(
                    token_id=token,rule_fingerprint=rule.sha256,snapshot_type='FULL',stream_healthy=True,
                    bids=[dict(price='.3',size='100')],asks=[dict(price='.4',size='100')]))
        key='official:'+digest(prefix)
        store.capture(key,event_id=claim['event_id'],kind='OFFICIAL_OBSERVATION',provider='fixture',
            source_identity=rig['context'].station_id,revision=prefix,observed_at=rig['now'][0],
            payload=dict(station=p['station']),evidence_class='SYNTHETIC')
        guard=store.audit('rule:'+digest(prefix),event_id=claim['event_id'],kind='RULE_STATE',
                         details=dict(fingerprint=rule.sha256,quarantined=False))
        return dict(book_ids=tuple(books),source_ids=(key,),rule_state_id=guard['id'])
    return census


def assembled(rig,monkeypatch,*,census=True,evaluator=None,policy=None):
    c=coordinator(rig);q=queue(rig);m=monitor(rig,monkeypatch);ready(rig,m)
    rt=runtime.PaperRuntime(c,q,m,policy or runtime.RuntimePolicy('fixture'),
        evaluator=evaluator or GatedEvaluator(rig['store']),census=census_fixture(rig) if census else None)
    return rt


def test_periodic_census_and_evaluation_are_integrated_and_never_infer_fills(rig,monkeypatch):
    rt=assembled(rig,monkeypatch);row=rt.tick('one');d=row['body']['details']
    assert d['outcome']=='TICK_COMPLETED',d
    assert len(d['evaluation_ids'])==1 and not rt.queue.snapshot()['needs_census']
    assert not d['account_batch_ids'] and not d['real_orders_sent']
    assert not rig['store'].records(kind='TRADE') and rt.coordinator.snapshot()['reserved_cash']=='0'
    assert rt.evaluator.calls==1 and rt.tick('one')==row and rt.evaluator.calls==1
    with pytest.raises(EvidenceError,match='REPLAY_CONFIG_CONFLICT'):rt.tick('one',updates=(('BOOK','different'),))


def test_missing_census_adapter_stays_gated_and_bounded_retry_survives_restart(rig,monkeypatch):
    rt=assembled(rig,monkeypatch,census=False);d=rt.tick('one')['body']['details']
    assert d['outcome']=='DEGRADED' and d['errors'][0]['reason']=='RUNTIME_FRESH_CENSUS_ADAPTER_REQUIRED'
    assert rt.queue.snapshot()['needs_census'] and rt.evaluator.calls==0
    resumed=runtime.PaperRuntime(rt.coordinator,rt.queue,rt.health,rt.policy,evaluator=rt.evaluator)
    d=resumed.tick('two')['body']['details']
    assert not d['evaluation_ids'] and rt.queue.snapshot()['needs_census']
    assert resumed.evaluator.calls==0


def test_one_operator_request_is_consumed_once_and_cancel_ambiguity_keeps_cash(rig,monkeypatch):
    c=coordinator(rig);p=proposal(rig,units='2');c.coordinate('reserve',(p,))
    rt=assembled(rig,monkeypatch,census=False)
    SafetyReductions(rig['store']).apply('operator-halt',scope='ACCOUNT',scope_id='account',action='CANCEL_AND_HALT',actor='fixture',reason='TEST')
    first=rt.tick('one')['body']['details'];assert c._state(c._head())['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED'
    assert Decimal(c.snapshot()['reserved_cash'])==Decimal('.8')
    assert first['state']['operator_cursor']==rig['store'].get('operator-halt')['seq']
    head=c._head();second=rt.tick('two')['body']['details'];assert c._head()==head
    assert second['state']['active_plans'] and not second['real_orders_sent']


def test_heartbeat_loss_triggers_cancellation_without_new_opening_permissions(rig,monkeypatch):
    c=coordinator(rig);p=proposal(rig,units='2');c.coordinate('reserve',(p,))
    rt=assembled(rig,monkeypatch);advance(rig,6)
    d=rt.tick('lost-worker')['body']['details']
    assert d['outcome']=='DEGRADED' and not d['evaluation_ids']
    assert c._state(c._head())['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED'
    assert Decimal(c.snapshot()['reserved_cash'])==Decimal('.8')


def test_backward_clock_tick_records_raw_time_and_still_cancels(rig,monkeypatch):
    c=coordinator(rig);p=proposal(rig,units='2');c.coordinate('reserve',(p,))
    rt=assembled(rig,monkeypatch);old=rig['now'][0];rig['now'][0]-=10;rig['mono'][0]+=1
    row=rt.tick('clock-bad',updates=(('BOOK','not-consumed'),));d=row['body']['details']
    assert row['body']['recorded_at']==old-10 and d['outcome']=='DEGRADED'
    assert not d['updates_consumed'] and c._state(c._head())['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED'
    assert Decimal(c.snapshot()['reserved_cash'])==Decimal('.8')


def test_account_fault_drives_cancellation_and_is_never_cleared_by_runtime(rig,monkeypatch):
    c=coordinator(rig);p=proposal(rig,units='2');c.coordinate('reserve',(p,));rt=assembled(rig,monkeypatch)
    head=c._head();state=c._state(head);state['faults']=['RECONCILIATION_REQUIRED']
    c._commit('fault',dict(action='SYNTHETIC_FAULT'),head,state,{})
    rt.tick('fault-tick');state=c._state(c._head())
    assert state['faults']==['RECONCILIATION_REQUIRED'] and state['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED'


def test_restart_after_registered_but_uncreated_health_plan_recovers_current_trigger(rig,monkeypatch):
    c=coordinator(rig);p=proposal(rig,units='2');c.coordinate('reserve',(p,));rt=assembled(rig,monkeypatch)
    rig['sync'][0]=False
    real=rt.cancellation.plan
    monkeypatch.setattr(rt.cancellation,'plan',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('POWER_LOSS')))
    with pytest.raises(RuntimeError,match='POWER_LOSS'):rt.tick('crashed')
    monkeypatch.setattr(rt.cancellation,'plan',real)
    d=rt.tick('resume')['body']['details']
    assert c._state(c._head())['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED'
    assert len(d['state']['active_plans'])==1


def test_terminal_receipt_reconciled_elsewhere_removes_active_plan_without_selling_inventory(rig,monkeypatch):
    c=coordinator(rig);p=proposal(rig,units='2');c.coordinate('reserve',(p,));rt=assembled(rig,monkeypatch,census=False)
    rig['sync'][0]=False;rt.tick('cancel')
    fill(rig,p.proposal_id,key='late',units='1',collateral='.4')
    key=proof(rig,p.proposal_id,'terminal','PAPER_TERMINAL',status='CANCELED',cumulative_fill_units='1',
              all_fills_reconciled=True,terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL')
    c.reconcile_terminal('reconcile',key);d=rt.tick('observe')['body']['details']
    assert not d['state']['active_plans'] and Decimal(c.snapshot()['reserved_cash'])==0
    assert Decimal(c.snapshot()['held_cost_basis'])==Decimal('.4')


def test_runtime_lock_and_ingestion_backpressure_are_bounded(rig,monkeypatch):
    rt=assembled(rig,monkeypatch)
    with pytest.raises(EvidenceError,match='BACKPRESSURE'):rt.tick('over',updates=tuple(('BOOK',str(i)) for i in range(33)))
    path=rt.store.path.with_name(rt.store.path.name+'.runtime.lock');fd=os.open(path,os.O_CREAT|os.O_WRONLY,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(EvidenceError,match='ALREADY_RUNNING'):rt.tick('locked')
    finally:os.close(fd)


def test_old_periodic_schedule_cannot_suppress_census_after_boot_change(rig,monkeypatch):
    rt=assembled(rig,monkeypatch);rt.tick('one');rig['boot'][0]='b'*36
    d=rt.tick('new-boot')['body']['details'];assert d['state']['next_census_monotonic']==0
    assert not d['evaluation_ids'] and d['outcome']=='DEGRADED'


def test_bad_update_is_reported_without_erasing_other_durable_sources(rig,monkeypatch):
    rt=assembled(rig,monkeypatch);before=rig['store'].get(rig['source_id'])
    d=rt.tick('bad',updates=(('BOOK','missing'),))['body']['details']
    assert any(e['stage']=='INGEST' for e in d['errors']) and not d['updates_consumed']
    assert rig['store'].get(rig['source_id'])==before and d['evaluation_ids']


def test_monotonic_tick_budget_defers_work_without_claiming_completion(rig,monkeypatch):
    rt=assembled(rig,monkeypatch);times=iter([0.,100.,100.,100.,100.,100.,100.,100.,100.,100.])
    monkeypatch.setattr(runtime.time,'monotonic',lambda:next(times,100.))
    d=rt.tick('budget')['body']['details']
    assert d['budget_exhausted'] and not d['evaluation_ids'] and not d['account_batch_ids']


def test_queue_periodic_census_does_not_clear_existing_gap(rig,monkeypatch):
    rt=assembled(rig,monkeypatch);event=rig['context'].event_id
    rt.queue.stream_gap('gap',event_id=event,reason='RECONNECT_REQUIRED')
    rt.queue.schedule_census('periodic')
    assert rt.queue.snapshot()['needs_census'][event]=='STREAM_GAP:RECONNECT_REQUIRED'


def test_process_generation_restart_keeps_ambiguous_submission_and_reserved_cash(rig,monkeypatch):
    c=coordinator(rig);p=proposal(rig,units='2');c.coordinate('reserve',(p,))
    c.transition('started',intent_id=p.proposal_id,status='SUBMITTING')
    rt=assembled(rig,monkeypatch,census=False);rt.tick('restart')
    assert c._state(c._head())['intents'][p.proposal_id]['status']=='UNKNOWN'
    assert Decimal(c.snapshot()['reserved_cash'])==Decimal('.8')


def test_runtime_reaches_common_account_using_explicit_synthetic_economic_fixture(rig,monkeypatch):
    # This fixture overrides economic qualification only to exercise downstream
    # queue/account plumbing. The real pipeline test retains vacuous bounds.
    p=proposal(rig,units='2');base=rig['store'].get(p.valuation_id)['body']['details']
    class PositiveMechanicsFixture:
        def evaluate(self,claim,prefix):
            data=deepcopy(base);token=data['target']['token_id']
            book=rig['store'].latest_source(kind='BOOK',event_id=claim['event_id'],provider='fixture',source_identity=token)
            data['book'].update(book_id=book['id'],book_sha256=book['sha256'])
            key='mechanics-value:'+digest(prefix)
            rig['store'].audit(key,event_id=claim['event_id'],kind='MEASUREMENT',details=data,evidence_ids=(book['id'],))
            return runtime.Evaluation((key,),(replace(p,valuation_id=key),))
    rt=assembled(rig,monkeypatch,evaluator=PositiveMechanicsFixture());row=rt.tick('reserve')
    d=row['body']['details'];assert len(d['account_batch_ids'])==1,d
    account=rig['store'].get(d['account_batch_ids'][0])['body']['details']
    assert account['reserved_intent_ids']==[p.proposal_id],account
    assert Decimal(rt.coordinator.snapshot()['reserved_cash'])==Decimal('.8')
    assert not rig['store'].records(kind='TRADE')
    head=rt.coordinator._head();assert rt.tick('reserve')==row and rt.coordinator._head()==head


@pytest.mark.parametrize('bad', [dict(maximum_events=0),dict(maximum_updates=65),dict(maximum_cancel_plans=9),dict(maximum_tick_seconds=61)])
def test_runtime_policy_rejects_unbounded_or_disabled_limits(bad):
    with pytest.raises(EvidenceError,match='POLICY_BOUND'):runtime.RuntimePolicy('bad',**bad)


def test_minimum_cancel_budget_does_not_starve_rule_quarantine_behind_healthy_or_busy_operator_channel(rig,monkeypatch):
    from polymarket_scanner.v11.rules import RuleGuard
    from test_weather_final_gpt6_exact_replays import _event
    rt=assembled(rig,monkeypatch,census=False,policy=runtime.RuntimePolicy('one',maximum_cancel_plans=1))
    event=rig['context'].event_id
    original=rig['store'].capture('original-rule',event_id=event,kind='RULES',provider='fixture',source_identity=event,
        revision='original',payload={'event':_event(station='KATL')},evidence_class='SYNTHETIC')
    RuleGuard(rig['store']).observe('original-guard',rig['rule'],raw_evidence_id=original['id'])
    raw=rig['store'].capture('quarantine-raw',event_id=event,kind='RULES',provider='fixture',source_identity=event,
        revision='changed',payload={'event':{'id':event,'description':'Unsupported rule'}},evidence_class='SYNTHETIC')
    guard=RuleGuard(rig['store']).invalidate('quarantine',event_id=event,raw_evidence_id=raw['id'],reason='UNSUPPORTED')
    safety=SafetyReductions(rig['store'])
    safety.apply('halt-one',scope='ACCOUNT',scope_id='account',action='CANCEL_AND_HALT',actor='fixture',reason='TEST')
    one=rt.tick('one')['body']['details']
    assert one['state']['operator_cursor']==rig['store'].get('halt-one')['seq']
    assert one['state']['rule_cursor']<guard['seq']
    safety.apply('halt-two',scope='ACCOUNT',scope_id='account',action='CANCEL_AND_HALT',actor='fixture',reason='TEST')
    two=rt.tick('two')['body']['details']
    assert two['state']['rule_cursor']==guard['seq']
    assert len(two['cancellation_report_ids'])<=3  # one old + one health + one intake
    assert not two['account_batch_ids']
