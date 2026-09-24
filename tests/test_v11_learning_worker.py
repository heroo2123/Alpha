from dataclasses import replace
import fcntl
import os

import pytest

from polymarket_scanner.v11 import learning_worker
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.learning_worker import ForecastLearningWorker, LearningTriggerPolicy
from test_v11_forecast_learning import setup_job


def setup_worker(tmp_path, *, events=6, policy=None, kernel=None):
    cfg,_,_,now=setup_job(tmp_path,event_count=events)
    if kernel is not None:
        from polymarket_scanner.v11.datasets import DatasetPlan
        original=cfg['journal'].store.get('plan')['body']['details']['plan']
        cfg['envelope']=replace(cfg['envelope'],max_kernel_evaluations=kernel)
        original['comparison_policy_sha256']=cfg['envelope'].sha256
        cfg['journal'].register('bounded-plan',DatasetPlan(**original))
        cfg['plan_record_id']='bounded-plan';cfg['provenance']['comparison_policy_sha256']=cfg['envelope'].sha256
    worker=ForecastLearningWorker(source_store=cfg.pop('source_store'),artifacts=cfg.pop('artifacts'),
        journal=cfg.pop('journal'),policy=policy or LearningTriggerPolicy('test',2,600.,2))
    return worker,cfg,now


def next_run(cfg,name,now):
    return {**cfg,'run_id':name,'provenance':{**cfg['provenance'],'run_id':name,'created_at':now}}


def test_worker_joins_the_actual_fit_preserves_parent_and_replays_without_new_trials(tmp_path):
    worker,cfg,now=setup_worker(tmp_path)
    source=worker.source.pin_read_view();parent=worker.artifacts.pin(cfg['parent_bundle_sha256']).canonical_json
    completed=worker.step('job-one',**cfg);d=completed['body']['details']
    assert d['outcome']=='RESEARCH_COMPLETED_NO_PROMOTION'
    assert len(d['state']['used_city_days'])==6 and d['state']['active'] is None
    assert not d['automatic_promotion'] and not d['os_isolation_verified'] and not d['financial_authority']
    assert worker.artifacts.pin(d['candidate_bundle_sha256']).payload['components']['CALIBRATION']['parameters']['status']=='UNCALIBRATED'
    before=worker.store.pin_read_view();now[0]+=1000
    assert worker.step('job-one',**cfg)==completed and worker.store.pin_read_view()==before
    assert worker.source.pin_read_view()==source and worker.artifacts.pin(cfg['parent_bundle_sha256']).canonical_json==parent


def test_time_backoff_does_not_refit_and_elapsed_time_does_not_create_new_evidence(tmp_path):
    worker,cfg,now=setup_worker(tmp_path);worker.step('first',**cfg)
    second=next_run(cfg,'second-run',now[0])
    assert worker.step('too-soon',**second)['body']['details']['outcome']=='DEFERRED_LEARNING_BACKOFF'
    now[0]+=601
    second=next_run(cfg,'second-run',now[0])
    result=worker.step('no-new-evidence',**second)['body']['details']
    assert result['outcome']=='DEFERRED_NEW_RESOLVED_EVIDENCE' and result['new_city_days']==0
    assert worker._get('second-run:selection') is None


def test_new_city_days_enable_next_fit_but_confirmation_reuse_stays_development(tmp_path):
    worker,cfg,now=setup_worker(tmp_path,events=8)
    first={**cfg,'joins':cfg['joins'][:6]}
    worker.step('first',**first);now[0]+=601
    second=next_run(cfg,'new-evidence-run',now[0]);completed=worker.step('new-evidence',**second)['body']['details']
    assert completed['outcome']=='RESEARCH_COMPLETED_NO_PROMOTION'
    assert len(completed['state']['used_city_days'])==8 and completed['state']['day_attempts']==2
    result=worker.store.get('new-evidence-run:result')['body']['details']['result']
    assert result['confirmation_role']=='DEVELOPMENT'
    assert result['status']=='NO_PROMOTION'


def test_daily_budget_is_distinct_from_interval_and_not_bypassed_by_a_new_command(tmp_path):
    worker,cfg,now=setup_worker(tmp_path,events=8,policy=LearningTriggerPolicy('limited',2,60.,1))
    worker.step('first',**{**cfg,'joins':cfg['joins'][:6]});now[0]+=61
    result=worker.step('budget',**next_run(cfg,'new-run',now[0]))['body']['details']
    assert result['outcome']=='DEFERRED_LEARNING_DAILY_BUDGET'
    assert worker._get('new-run:selection') is None


def test_less_than_two_independent_city_days_never_starts_a_fit(tmp_path):
    worker,cfg,now=setup_worker(tmp_path)
    result=worker.step('sparse',**{**cfg,'joins':cfg['joins'][:1]})['body']['details']
    assert result['outcome']=='DEFERRED_NEW_RESOLVED_EVIDENCE' and result['new_city_days']==1
    assert worker._get(cfg['run_id']+':selection') is None


def test_nonblocking_lock_prevents_a_second_worker_before_any_fit(tmp_path):
    worker,cfg,_=setup_worker(tmp_path)
    fd=os.open(worker.store.path.with_name(worker.store.path.name+'.learning.lock'),os.O_CREAT|os.O_WRONLY,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        before=worker.store.pin_read_view()
        with pytest.raises(EvidenceError,match='ALREADY_RUNNING'):worker.step('busy',**cfg)
        assert worker.store.pin_read_view()==before
    finally:os.close(fd)


def test_finished_fit_is_reconciled_after_worker_crash_without_second_fit(tmp_path,monkeypatch):
    worker,cfg,_=setup_worker(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(worker,'_recover',lambda *a:(_ for _ in ()).throw(RuntimeError('CRASH_AFTER_FIT')))
        with pytest.raises(RuntimeError):worker.step('recover',**cfg)
    old_result=worker.store.get(cfg['run_id']+':result')
    with monkeypatch.context() as patch:
        patch.setattr(learning_worker,'run_forecast_fit',lambda **k:(_ for _ in ()).throw(AssertionError('duplicate fit')))
        result=worker.step('recover',**cfg)['body']['details']
    assert result['outcome']=='RESEARCH_COMPLETED_NO_PROMOTION'
    assert worker.store.get(cfg['run_id']+':result')==old_result


def test_uncertain_interruption_is_durable_and_never_implicitly_retried(tmp_path,monkeypatch):
    worker,cfg,_=setup_worker(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(learning_worker,'run_forecast_fit',lambda **k:(_ for _ in ()).throw(RuntimeError('INTERRUPTED')))
        with pytest.raises(RuntimeError):worker.step('uncertain',**cfg)
    with monkeypatch.context() as patch:
        patch.setattr(learning_worker,'run_forecast_fit',lambda **k:(_ for _ in ()).throw(AssertionError('duplicate fit')))
        a=worker.step('uncertain',**cfg);b=worker.step('another-command',**cfg)
    assert a==b and a['body']['details']['outcome']=='INTERRUPTED_FIT_REVIEW_REQUIRED'
    assert a['body']['details']['state']['active'] is not None


def test_numerical_failure_is_recovered_and_backoff_remains_in_force(tmp_path):
    worker,cfg,now=setup_worker(tmp_path,kernel=1)
    parent=worker.artifacts.pin(cfg['parent_bundle_sha256']).canonical_json
    with pytest.raises(EvidenceError,match='RESOURCE_BUDGET_EXHAUSTED'):worker.step('failed',**cfg)
    result=worker.step('failed',**cfg)['body']['details']
    assert result['outcome']=='RESEARCH_FAILED_PARENT_UNCHANGED'
    assert result['state']['active'] is None and result['state']['day_attempts']==1
    assert worker.artifacts.pin(cfg['parent_bundle_sha256']).canonical_json==parent
    assert worker.step('backoff',**next_run(cfg,'try-next',now[0]))['body']['details']['outcome']=='DEFERRED_LEARNING_BACKOFF'


def test_reusing_run_identity_cannot_adopt_an_old_result_for_a_new_cohort(tmp_path):
    worker,cfg,now=setup_worker(tmp_path,events=8)
    worker.step('first',**{**cfg,'joins':cfg['joins'][:6]});now[0]+=601
    result=worker.step('same-run-new-data',**cfg)['body']['details']
    assert result['outcome']=='LEARNING_RUN_ID_ALREADY_USED'
    assert len(result['state']['used_city_days'])==6


def test_policy_changes_and_command_rebinding_require_review(tmp_path):
    worker,cfg,now=setup_worker(tmp_path);worker.step('first',**cfg)
    with pytest.raises(EvidenceError,match='REPLAY_CONFLICT'):
        worker.step('first',**next_run(cfg,'different-run',now[0]))
    changed=ForecastLearningWorker(source_store=worker.source,artifacts=worker.artifacts,journal=worker.journal,
        policy=replace(worker.policy,maximum_daily_attempts=3))
    with pytest.raises(EvidenceError,match='POLICY_CHANGED_REVIEW_REQUIRED'):changed.step('second',**cfg)


def test_recovery_cannot_adopt_a_result_from_a_different_dataset_recipe(tmp_path,monkeypatch):
    worker,cfg,_=setup_worker(tmp_path)
    with monkeypatch.context() as patch:
        patch.setattr(worker,'_recover',lambda *a:(_ for _ in ()).throw(RuntimeError('CRASH_AFTER_FIT')))
        with pytest.raises(RuntimeError):worker.step('recover',**cfg)
    original=worker._get
    def altered(key):
        row=original(key)
        if key==cfg['run_id']+':dataset':row['body']['details']['request']['as_of']-=1
        return row
    with monkeypatch.context() as patch:
        patch.setattr(worker,'_get',altered)
        with pytest.raises(EvidenceError,match='RESEARCH_RECIPE_BINDING'):worker.step('recover',**cfg)


def test_backward_clock_cannot_erase_a_reserved_attempt_or_backoff(tmp_path):
    worker,cfg,now=setup_worker(tmp_path);worker.step('first',**cfg)
    before=worker.store.pin_read_view();now[0]-=1
    with pytest.raises(EvidenceError,match='CLOCK_OR_CUTOFF'):worker.step('clock',**cfg)
    assert worker.store.pin_read_view()==before


@pytest.mark.parametrize('fault',['missing','proxy','future','wrong_city'])
def test_trigger_requires_exact_available_complete_labels(tmp_path,fault):
    worker,cfg,_=setup_worker(tmp_path);joins=list(cfg['joins'])
    if fault=='missing':joins[0]=replace(joins[0],label_ids=joins[0].label_ids[:-1])
    elif fault=='wrong_city':joins[0]=replace(joins[0],city='Other')
    elif fault=='future':cfg['as_of']-=30*86400
    else:
        label=worker.source.get(joins[0].label_ids[0][1]);p=label['body']['payload'];p['evidence_type']='OBSERVATION_LABEL'
        changed=worker.source.capture('proxy',event_id=label['event_id'],kind='LABEL',provider='TEST_ONLY',source_identity='proxy',
            revision=p['label_version'],payload=p,evidence_class='SYNTHETIC')
        joins[0]=replace(joins[0],label_ids=((joins[0].label_ids[0][0],changed['id']),*joins[0].label_ids[1:]))
    cfg['joins']=tuple(joins)
    with pytest.raises(EvidenceError):worker.step('invalid',**cfg)
    assert worker._get(cfg['run_id']+':selection') is None
