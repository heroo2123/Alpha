"""Synthetic safety policy fixtures; no operational statistical acceptance."""
import asyncio
from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal
import fcntl
import os

import httpx
import pytest

from polymarket_scanner.v11 import certification, drift_runtime as drift
from polymarket_scanner.v11.drift import DriftPolicy
from polymarket_scanner.v11.evidence import EvidenceError, canonical, digest
from polymarket_scanner.v11.forecast_learning import ForecastLabelJoin
from polymarket_scanner.v11.paper_coordinator import Proposal
from polymarket_scanner.v11.scenario_risk import Attribution
from polymarket_scanner.v11.strategy_admission import StrategyAdmission
from polymarket_scanner.v11.strategy_pipeline import TemperatureStrategies
from test_v11_certification_rules import setup, approve_fixture
from test_v11_drift import sample
from test_v11_learning_capture import bundle, labels
from test_v11_strategy_pipeline import factory
from test_v11_model_governance import authority
from test_v11_pws_admission import coordinator
from test_v11_lifecycle_runtime import runtime, state, checks


def build(r, monkeypatch, policy=None, c=None):
    plan = drift.DriftPlan(r['scope'], r['binding'].bundle_sha256, policy or r['policy'])
    w = drift.DriftWorker(c or coordinator(r), (plan,))
    review = dict(plan_key=plan.key, account_id='account', namespace='V11_PAPER', review_id='SYNTHETIC',
        reviewer='TEST_ONLY', approved_at=r['now'][0]-plan.policy.window_seconds-1, expires_at=r['now'][0]+120,
        model_state_sha256=digest(r['model_state'][0]), maximum_measurement_age_seconds=60.,
        selection='EXPLICIT_CAPTURE_COHORT', action='SAFETY_REDUCTION_ONLY', financial_authority=False)
    manifest = dict(version='alpha_v11_drift_reviews_v1', reviews=[review])
    monkeypatch.setattr(drift, 'protected_reviews', lambda:deepcopy(manifest))
    return w, plan, manifest


def enqueue(w, r, plan, key='cohort'):
    return w.request(key, plan_key=plan.key, joins=(r['join'],), as_of=r['now'][0])


@pytest.mark.parametrize('sample',['FUTURE_FORECAST','SAME_DAY_LATE_LOCK'],indirect=True)
def test_reviewed_original_model_scope_demotes_once_without_mutating_model_or_account(sample, monkeypatch):
    r=sample; w,p,_=build(r,monkeypatch); original=deepcopy(r['model_state'][0]); prior=w.coordinator._head()
    submitted=enqueue(w,r,p); row=w.step('work'); d=row['body']['details']
    assert d['outcome']=='SCOPED_SAFETY_REDUCTION_APPLIED' and d['demotion_applied']
    measurement=r['store'].get(d['measurement_id'])['body']['details']['result']
    assert measurement['outcome']=='DEGRADATION_CANDIDATE' and not measurement['demotion_applied']
    reduction=r['store'].get(d['demotion_id'])
    assert reduction['body']['details']['scope']==measurement['request']['scope']
    assert reduction['body']['details']['state']=='CALIBRATION_DEGRADED'
    assert r['model_state'][0]==original and w.coordinator._head()==prior
    with pytest.raises(EvidenceError,match='QUARANTINE_REQUIRES_NEW_REVIEW'):
        StrategyAdmission(r['store']).revalidate('pin',context=r['context'],rule=r['rule'],
            binding=asdict(r['binding']),strategies=(r['scope'].strategy,))
    r['now'][0]+=200
    assert w.step('work')==row
    assert w.request('cohort',plan_key=p.key,joins=(r['join'],),as_of=measurement['request']['as_of'])==submitted
    assert not d['financial_authority'] and not d['automatic_restoration']


@pytest.mark.parametrize('which',['insufficient','healthy'])
def test_sufficient_or_healthy_measurement_never_restores_prior_station_demotion(sample,monkeypatch,which):
    policy=replace(sample['policy'],minimum_events=2,minimum_city_days=2) if which=='insufficient' else replace(
        sample['policy'],maximum_brier=2.,maximum_log_loss=1000.)
    w,p,_=build(sample,monkeypatch,policy)
    certification.StationRegistry(w.store).demote('manual-reduction',sample['scope'],state='CALIBRATION_DEGRADED',
        reason='PRIOR_FAILURE',evidence_ids=('model2',))
    before=w.store.latest(kind='REGISTRY',event_id='station:'+sample['scope'].station)
    enqueue(w,sample,p); d=w.step('check')['body']['details']
    assert d['outcome']==('INSUFFICIENT_COHORT' if which=='insufficient' else 'NO_DECLARED_BREACH')
    assert not d['demotion_applied'] and w.store.latest(kind='REGISTRY',event_id=before['event_id'])==before


@pytest.mark.parametrize('fault',['absent','ambiguous','plan','account','namespace','late','expired','stale','action',
                                  'authority','selection','schema','model_epoch','unavailable'])
def test_unreviewed_or_changed_scope_is_measured_but_cannot_apply_reduction(sample,monkeypatch,fault):
    r=sample; w,p,m=build(r,monkeypatch); review=m['reviews'][0]; enqueue(w,r,p)
    if fault=='absent': m['reviews']=[]
    elif fault=='ambiguous': m['reviews'].append(deepcopy(review))
    elif fault in {'plan','account','namespace'}: review[{'plan':'plan_key','account':'account_id','namespace':'namespace'}[fault]]='wrong'
    elif fault=='late': review['approved_at']=r['now'][0]-1
    elif fault=='expired': review['expires_at']=r['now'][0]
    elif fault=='stale': r['now'][0]+=61
    elif fault=='action': review['action']='PROMOTE'
    elif fault=='authority': review['financial_authority']=True
    elif fault=='selection': review['selection']='ALL_UNIVERSE'
    elif fault=='schema': review['invented']=1
    elif fault=='unavailable': monkeypatch.setattr(drift,'protected_reviews',lambda:(_ for _ in ()).throw(EvidenceError('UNAVAILABLE')))
    else:
        s=r['model_state'][0];r['model_state'][0]=authority.transition(s,action='DEMOTE',expected_state_sha256=digest(s),
            now=21.,reason='SYNTHETIC_NEW_EPOCH',size_multiplier=.5)
    d=w.step('gated')['body']['details']
    assert d['outcome']=='REDUCTION_GATED' and d['reason'] and not d['demotion_applied']
    assert r['store'].get(d['measurement_id'])['body']['details']['result']['outcome']=='DEGRADATION_CANDIDATE'
    assert not [row for row in r['store'].records(kind='REGISTRY') if row['body']['details'].get('action')=='DEMOTION']


@pytest.mark.parametrize('boundary',['before_reduction','after_reduction'])
def test_power_loss_preserves_measurement_and_recovers_exact_demotion_once(sample,monkeypatch,boundary):
    r=sample;w,p,_=build(r,monkeypatch);enqueue(w,r,p)
    original=w._save
    with monkeypatch.context() as patch:
        if boundary=='before_reduction':
            patch.setattr(certification.StationRegistry,'demote',lambda *a,**kw:(_ for _ in ()).throw(RuntimeError('POWER_LOSS')))
        else:
            def fail(*a,**kw):
                if kw['action']=='DRIFT_RESULT':raise RuntimeError('POWER_LOSS')
                return original(*a,**kw)
            patch.setattr(w,'_save',fail)
        with pytest.raises(RuntimeError,match='POWER_LOSS'):w.step('interrupted')
    first=[row for row in w.store.records(kind='MODEL_EVENT') if row['body']['details'].get('action')=='DRIFT_MEASUREMENT']
    assert len(first)==1 and w._head()['body']['details']['state']['active']
    fresh=drift.DriftWorker(w.coordinator,(p,));d=fresh.step('recovered')['body']['details']
    assert d['outcome']=='SCOPED_SAFETY_REDUCTION_APPLIED' and d['policy_review_sha256']
    assert len([row for row in w.store.records(kind='REGISTRY') if row['body']['details'].get('action')=='DEMOTION'])==1
    assert w.store.get(first[0]['id'])==first[0] and not fresh._head()['body']['details']['state']['active']


def revise(r):
    row=r['store'].get(next(iter(r['labels'].values()))); b=row['body']
    r['store'].capture('later-label',event_id=row['event_id'],kind='LABEL',provider=b['provider'],
        source_identity=b['source_identity'],revision='correction',payload=dict(b['payload'],label_version='correction'),
        evidence_class='SYNTHETIC')


def test_revision_during_recovery_does_not_rewrite_measurement_or_apply_old_reduction(sample,monkeypatch):
    r=sample;w,p,_=build(r,monkeypatch);enqueue(w,r,p)
    with monkeypatch.context() as patch:
        patch.setattr(w,'_review',lambda *a:(_ for _ in ()).throw(RuntimeError('POWER_LOSS')))
        with pytest.raises(RuntimeError):w.step('partial')
    old=[row for row in w.store.records(kind='MODEL_EVENT') if row['body']['details'].get('action')=='DRIFT_MEASUREMENT'][0]
    r['now'][0]+=1;revise(r);d=w.step('resume')['body']['details']
    assert d['reason']=='DRIFT_LABEL_CHANGED_BEFORE_REDUCTION' and not d['demotion_applied']
    assert w.store.get(old['id'])==old


def test_recovery_after_reviewed_station_restoration_never_reapplies_old_demotion(sample,setup,monkeypatch):
    r=sample;w,p,_=build(r,monkeypatch);enqueue(w,r,p);original=w._save
    with monkeypatch.context() as patch:
        def fail(*a,**kw):
            if kw['action']=='DRIFT_RESULT':raise RuntimeError('POWER_LOSS')
            return original(*a,**kw)
        patch.setattr(w,'_save',fail)
        with pytest.raises(RuntimeError):w.step('interrupted')
    r['now'][0]+=1
    approve_fixture(monkeypatch,(r['store'],certification.StationRegistry(w.store),r['scope'],setup[3],r['now']),
        stage='PAPER',fingerprint=r['rule'].sha256,prefix='reviewed-recovery:')
    latest=w.store.latest(kind='REGISTRY',event_id='station:'+r['scope'].station)
    assert w.step('recovered')['body']['details']['demotion_applied']  # reports the original action
    assert w.store.latest(kind='REGISTRY',event_id=latest['event_id'])==latest
    assessment=certification.StationRegistry(w.store).assess(r['scope'],stage='PAPER',
        metadata_fingerprint=setup[3].fingerprint,rule_fingerprint=r['rule'].sha256)
    assert assessment['eligible'] and len([row for row in w.store.records(kind='REGISTRY')
        if row['body']['details'].get('action')=='DEMOTION'])==1


def test_changed_protected_review_between_pin_and_action_gates_reduction(sample,monkeypatch):
    r=sample;w,p,manifest=build(r,monkeypatch);enqueue(w,r,p);audit=w.store.audit
    def changed(key,**kw):
        row=audit(key,**kw)
        if key.startswith('drift-review:'):manifest['reviews'][0]['review_id']='REPLACED_REVIEW'
        return row
    monkeypatch.setattr(w.store,'audit',changed)
    d=w.step('changed-review')['body']['details']
    assert d['reason']=='DRIFT_REVIEW_CHANGED_BEFORE_REDUCTION' and not d['demotion_applied']


@pytest.mark.parametrize('race',['label','station'])
def test_reduction_cas_rejects_concurrent_label_or_station_change(sample,monkeypatch,race):
    r=sample;w,p,_=build(r,monkeypatch);enqueue(w,r,p);real=certification.StationRegistry.demote
    def change(self,*a,**kw):
        if race=='label':revise(r)
        else:real(self,'other-reduction',r['scope'],state='CALIBRATION_DEGRADED',reason='OTHER_FAILURE',evidence_ids=('model2',))
        return real(self,*a,**kw)
    monkeypatch.setattr(certification.StationRegistry,'demote',change)
    d=w.step('raced')['body']['details']
    assert d['outcome']=='REDUCTION_GATED' and d['reason'] in {'AUDIT_GUARDED_STATE_CHANGED','AUDIT_STATE_CHANGED'}
    assert not d['demotion_applied']


def test_missing_label_is_a_durable_gated_result_and_other_jobs_can_continue(sample,monkeypatch):
    w,p,_=build(sample,monkeypatch)
    w.request('bad',plan_key=p.key,joins=(replace(sample['join'],label_ids=(('absent','unknown'),)),),as_of=sample['now'][0])
    d=w.step('bad-job')['body']['details'];assert d['outcome']=='MEASUREMENT_GATED'
    sample['now'][0]+=1;enqueue(w,sample,p,'good');assert w.step('good-job')['body']['details']['demotion_applied']


def test_single_pending_slot_replay_config_lock_and_new_cohort_guards(sample,monkeypatch):
    w,p,_=build(sample,monkeypatch);saved=enqueue(w,sample,p)
    assert enqueue(w,sample,p)==saved
    with pytest.raises(EvidenceError,match='PENDING'):enqueue(w,sample,p,'second')
    with pytest.raises(EvidenceError,match='REPLAY_CONFLICT'):
        w.request('cohort',plan_key=p.key,joins=(sample['join'],),as_of=sample['now'][0]-1)
    with pytest.raises(EvidenceError,match='CONFIGURATION_CHANGED'):
        drift.DriftWorker(w.coordinator,(replace(p,policy=replace(p.policy,version='changed')),)).step('other')
    fd=os.open(w.store.path.with_name(w.store.path.name+'.drift.lock'),os.O_RDWR)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(EvidenceError,match='ALREADY_RUNNING'):w.step('concurrent')
    finally:os.close(fd)
    w.step('complete');sample['now'][0]+=1
    with pytest.raises(EvidenceError,match='NEW_COHORT'):enqueue(w,sample,p,'reused')
    assert w.step('idle')['body']['details']['outcome']=='NO_EXPLICIT_COHORT'


@pytest.mark.parametrize('fault',['duplicate_keys','too_large','bad_schema','symlink','missing'])
def test_fixed_review_reader_is_bounded_and_fails_closed(tmp_path,monkeypatch,fault):
    path=tmp_path/'review.json'
    if fault=='duplicate_keys':raw=b'{"version":"alpha_v11_drift_reviews_v1","reviews":[],"reviews":[]}'
    elif fault=='too_large':raw=b' '* (1024*1024+1)
    else:raw=b'{}'
    if fault!='missing':path.write_bytes(raw)
    if fault=='symlink':
        link=tmp_path/'link.json';link.symlink_to(path);path=link
    monkeypatch.setattr(drift,'REVIEW_PATH',path)
    monkeypatch.setattr(drift,'_root_custody',lambda p:p.lstat())
    with pytest.raises(EvidenceError):drift.protected_reviews()


def test_protected_review_reader_checks_real_parent_custody(tmp_path,monkeypatch):
    path=tmp_path/'unsafe'/'reviews.json';path.parent.mkdir(mode=0o777);path.parent.chmod(0o777)
    path.write_text(canonical(dict(version='alpha_v11_drift_reviews_v1',reviews=[])))
    monkeypatch.setattr(drift,'REVIEW_PATH',path)
    with pytest.raises(EvidenceError,match='CUSTODY'):drift.protected_reviews()


@pytest.mark.parametrize('calibration',[False,True])
def test_captured_quality_through_reduction_withdrawal_terminal_reconciliation_and_daily_audit(factory,setup,monkeypatch,calibration):
    from polymarket_scanner.v11.audit_reports import AuditScheduler, AuditWorker
    from test_v11_audit_reports import finish
    from test_v11_basket_coordinator import proof
    r=factory();evaluated=TemperatureStrategies(r['store']).evaluate('forecast',r['request'])
    r['capture']=r['store'].get(evaluated['body']['details']['learning_capture']['capture_id'])
    value=deepcopy(r['store'].get('forecast:valuation')['body']['details'])
    assert value['outcome']=='REJECT'  # Economic admission is deliberately a fixture.
    value.update(outcome='ACCEPT_RESEARCH',conservative_ev_per_share='.2',conservative_ev_total='.4',synthetic_downstream_test_fixture=True)
    r['store'].audit('fixture-value',event_id=r['context'].event_id,kind='MEASUREMENT',details=value)
    proposal=Proposal('intent','fixture-thesis',r['context'],r['rule'],'fixture-value',r['request'].event_state_id,
        (Attribution('FUTURE_FORECAST','1'),),r['now'][0]+90,'2',('pin',))
    c=coordinator(r);assert c.coordinate('reserve',(proposal,))['body']['details']['reserved_intent_ids']==['intent']
    r['labels']=labels(r,r['capture'],delay=1.);r['join']=ForecastLabelJoin(r['capture']['id'],tuple(r['labels'].items()),r['context'].city_id,r['scope'].horizon,r['scope'].season)
    r['policy']=DriftPolicy('SYNTHETIC_ONLY','FORECAST','SYNTHETIC',86400.,1,1,0.,0.)
    if calibration:
        from polymarket_scanner.v11.drift import CalibrationDriftPolicy
        from polymarket_scanner.v11.probability import CALIBRATION_ERROR_METHOD
        r['policy']=CalibrationDriftPolicy('SYNTHETIC_CALIBRATION','FORECAST','SYNTHETIC',86400.,1,1,2.,1000.,
            CALIBRATION_ERROR_METHOD,0.)
    rt=runtime(r,monkeypatch);healthy=rt.tick('before')
    assert all(c['passed'] for c in checks(rt,healthy)),[c['reason'] for c in checks(rt,healthy)]
    w,p,_=build(r,monkeypatch,c=rt.coordinator);enqueue(w,r,p);result=w.step('quality');rt.tick('withdraw')
    if calibration:
        measurement=rt.store.get(result['body']['details']['measurement_id'])['body']['details']['result']
        assert measurement['threshold_breaches']==['CALIBRATION_ERROR_ABOVE_DECLARED_MAXIMUM']
    assert state(rt)['intents']['intent']['status']=='CANCEL_REQUESTED'
    assert Decimal(rt.coordinator.snapshot()['reserved_cash'])==Decimal('.4')
    terminal=proof(r,'intent','terminal','PAPER_TERMINAL',status='CANCELED',cumulative_fill_units='0',
        all_fills_reconciled=True,terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL')
    rt.coordinator.reconcile_terminal('reconcile',terminal);assert Decimal(rt.coordinator.snapshot()['reserved_cash'])==0
    assert state(rt)['cash']=='10' and not state(rt)['lots']
    r['now'][0]=(int(r['now'][0]//86400)+1)*86400+1;AuditScheduler(rt.store,rt.audits.policy).request_due()
    auditor=AuditWorker(rt.coordinator,rt.audits.policy)
    for _ in range(3):
        report=finish(auditor)['body']['details']
        if report['operations']['drift_outcomes']:break
    assert report['operations']['drift_outcomes']=={'SCOPED_SAFETY_REDUCTION_APPLIED':1}
    assert report['operations']['resting_admission_checks']['QUARANTINE_REQUIRES_NEW_REVIEW']==1
    assert report['coverage']['archive_scan_complete'] and not report['financial_authority']


def test_typed_candidate_runs_queued_drift_job_with_existing_safety_ticks(sample,monkeypatch):
    from test_v11_candidate_assembly import app, scoped_plan, inputs, target, synthetic_clock, transport
    r=sample;lane=app.TemperatureLane('temperature',inputs(r),(target(r),),'fixture',r['request'].valuation_policy,10.)
    cfg=scoped_plan(r,lane);plan=drift.DriftPlan(r['scope'],r['binding'].bundle_sha256,r['policy'])
    cfg=replace(cfg,drift=(plan,),candidate=replace(cfg.candidate,maximum_jobs=4))
    synthetic_clock(r,monkeypatch);calls=[]
    from test_v11_runtime_health import ready
    async def run():
        async with httpx.AsyncClient(transport=transport(r,calls)) as client:
            candidate=app.assemble_candidate(r['store'],client,cfg,generation='drift-candidate')
            _,_,manifest=build(r,monkeypatch,c=candidate.runtime.coordinator)
            enqueue(candidate.drift,r,plan);ready(r,candidate.runtime.health)
            assert candidate.drift.coordinator is candidate.runtime.coordinator
            return await candidate.run('finite-drift')
    d=asyncio.run(run())['body']['details'];jobs=[j for j in d['worker_results'] if j['kind']=='DRIFT']
    assert len(jobs)==1 and jobs[0]['outcome']=='SCOPED_SAFETY_REDUCTION_APPLIED',d
    assert len(d['runtime_ids'])>=4 and d['all_async_jobs_drained'] and not d['real_orders_sent']
    with pytest.raises(EvidenceError,match='DRIFT_PLAN_SCOPE'):
        replace(cfg,drift=(replace(plan,bundle_sha256='f'*64),))
