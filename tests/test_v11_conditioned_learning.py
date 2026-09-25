"""Explicit synthetic same-day cohorts; no real labels, calibration or promotion."""
from copy import deepcopy
from dataclasses import asdict, replace
from datetime import date, timedelta

import pytest

from polymarket_scanner.v11 import offline_learning as learner
from polymarket_scanner.v11.datasets import DatasetPlan, ExperimentJournal
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, ReleaseBinding, digest
from polymarket_scanner.v11.event_risk import EventContext
from polymarket_scanner.v11.forecast_features import ForecastFeatureContract, build_initial_forecast_bundle
from polymarket_scanner.v11.forecast_learning import ForecastLabelJoin, run_forecast_fit
from polymarket_scanner.v11.learning_worker import ForecastLearningWorker, LearningTriggerPolicy
from polymarket_scanner.v11.model_artifacts import ArtifactStore, predict_with_bundle
from polymarket_scanner.v11.probability import UNRESOLVED_EXTREME, FINAL_EXTREME, target_identity
from polymarket_scanner.v11.rules import fingerprint_event
from polymarket_scanner.v11.strategy_pipeline import INPUT_VERSION, CONDITION_VERSION, COVERAGE_VERSION, _model_inputs, _condition
from polymarket_scanner.v11.target_learning import capture_conditioned_vector, labeled_target_examples
from test_v11_forecast_learning import setup_job
from test_v11_model_artifacts import make_artifacts, provenance
from test_v11_probability import T
from test_weather_final_gpt6_exact_replays import _event


def setup_conditioned_job(root, *, family='high', unit='F', confirmation_value=1):
    root.mkdir(exist_ok=True);root.chmod(0o700);objects=root/'objects';objects.mkdir(mode=0o700)
    artifacts=ArtifactStore(objects)
    first=fingerprint_event(_event(station='KATL',family=family,unit=unit),station_timezone='America/New_York',metadata_fingerprint='a'*64)
    contract=ForecastFeatureContract((('model-1',3),),unit,first.payload['family'])
    parent=build_initial_forecast_bundle(artifacts,contract=contract,probability_parameters=make_artifacts()['PROBABILITY']['parameters'],
        provenance=provenance(),quality_modifiers={})
    base=learner.LearningEnvelope('model-1',tuple(contract.mapping['model-1']),'lower_cut','upper_cut',
        (0.,1.),(.2,1.),'brier',2,2,2,0.,.01,10.,100_000,100,23)
    envelope=learner.ConditionedLearningEnvelope(**asdict(base))
    now=[T-10];source=EvidenceStore(root/'source.sqlite','V11_PAPER',clock=lambda:now[0])
    research=EvidenceStore(root/'research.sqlite','CHALLENGER:same-day',clock=lambda:now[0]);journal=ExperimentJournal(research,'same-day')
    plan=DatasetPlan('conditioned-plan','FINAL_CONTRACT_PAYOUT',contract.schema.sha256,T-2,T+6*86400-1,T+12*86400-1,T+18*86400,envelope.sha256)
    journal.register('plan',plan);joins=[];predictions=[]
    for i in range(6):
        at=T+i*3*86400;now[0]=at
        station='KATL' if i%2==0 else 'KLGA';city='Atlanta' if i%2==0 else 'NewYork'
        rule=fingerprint_event(_event(station=station,family=family,unit=unit,target=date(2026,9,14)+timedelta(days=3*i),eid=f'same-{i}'),
            station_timezone='America/New_York',metadata_fingerprint='a'*64)
        rp=rule.payload;context=EventContext('paper',city,station,rp['event_id']);binding=ReleaseBinding('a'*40,'b'*40,'c'*64,parent,rule.sha256)
        fields={k:rp[k] for k in ('station','target_date','family','unit')}
        model=source.capture(f'model-{i}',event_id=context.event_id,kind='MODEL',provider='TEST_ONLY',source_identity='remaining',revision='1',
            issued_at=at-30,payload=dict(fields,rule_fingerprint=rule.sha256,temperature_input=dict(version=INPUT_VERSION,model_id='model-1',
                target_sha256=target_identity(rule,UNRESOLVED_EXTREME),members=[70.4,70.5,70.6])),evidence_class='SYNTHETIC')
        obs=source.capture(f'observation-{i}',event_id=context.event_id,kind='OFFICIAL_OBSERVATION',provider='TEST_ONLY',source_identity=station,
            revision='accepted',observed_at=at-1,evidence_class='SYNTHETIC',payload=dict(fields,rule_fingerprint=rule.sha256,
                observation_population=rp['observation_population'],source_family=rp['source_family'],
                exact_extreme=dict(version=CONDITION_VERSION,whole_degree_value=71 if family=='high' else 70,source_role='EXACT_CONTRACT_OBSERVATION')))
        cp=source.capture(f'coverage-{i}',event_id=context.event_id,kind='FEATURES',provider='TEST_ONLY',source_identity='coverage',revision='1',
            observed_at=at,evidence_class='SYNTHETIC',payload=dict(version=COVERAGE_VERSION,rule_fingerprint=rule.sha256,as_of=at,
                accepted_intervals=[[at-8*3600,at-100]],unresolved_intervals=[[at-100,at+16*3600]],
                model_evidence_sha256=[model['sha256']],observation_id=obs['id'],observation_sha256=obs['sha256']))
        from types import SimpleNamespace
        request=SimpleNamespace(model_input_ids=(model['id'],),observed_input_id=obs['id'],coverage_input_id=cp['id'])
        components=_model_inputs(source,rule,request.model_input_ids,at,target=UNRESOLVED_EXTREME)
        observed,coverage=_condition(source,rule,request,at,components,available_cutoff=at)
        prediction=predict_with_bundle(artifacts.pin(parent),rule,components,as_of=at,max_source_age_seconds=120.,observed=observed,remaining_coverage=coverage)
        now[0]=at+.1
        capture=capture_conditioned_vector(source,f'capture-{i}',context=context,strategy='SAME_DAY_LATE_LOCK',rule=rule,binding=binding,
            prediction=prediction,pinned_bundle=artifacts.pin(parent),model_input_ids=request.model_input_ids,
            observed_input_id=obs['id'],coverage_input_id=cp['id'],expires_at=at+20)
        now[0]=at+2*86400;label_ids=[]
        for j,row in enumerate(capture['body']['details']['rows']):
            target=row['target_identity'];key=f'label-{i}-{j}';label_ids.append((target['market_id'],key))
            source.capture(key,event_id=context.event_id,kind='LABEL',provider='TEST_ONLY',source_identity=target['token_id'],revision='test',
                evidence_class='SYNTHETIC',payload=dict(context=dict(station=station,city=city,local_date=rp['target_date'],
                    target='FINAL_CONTRACT_PAYOUT',rule_fingerprint=rule.sha256),target_identity=target,decision_target='FINAL_CONTRACT_PAYOUT',
                    label_version='test',knowable_at=now[0],value=int(j==(confirmation_value if i>=4 else 1)),evidence_type='SYNTHETIC'))
        joins.append(ForecastLabelJoin(capture['id'],tuple(label_ids),city,'SAME_DAY','AUTUMN','UNINSPECTED' if i>=4 else 'DEVELOPMENT'))
        predictions.append((rule,components,observed,coverage,at))
    now[0]=T+18*86400+1
    cfg=dict(source_store=source,artifacts=artifacts,journal=journal,plan_record_id='plan',joins=tuple(joins),parent_bundle_sha256=parent,
        envelope=envelope,provenance=dict(provenance(),created_at=now[0],run_id='conditioned-fit',comparison_policy_sha256=envelope.sha256),
        run_id='conditioned-fit',as_of=now[0])
    return cfg,predictions,now


def examples(cfg,join=None):
    join=join or cfg['joins'][0]
    return labeled_target_examples(cfg['source_store'],join.capture_id,label_ids=dict(join.label_ids),city=join.city,
        horizon=join.horizon,season=join.season,prior_exposure=join.prior_exposure)


@pytest.mark.parametrize('family,unit',[('high','C'),('high','F'),('low','C'),('low','F')])
def test_conditioned_capture_to_fit_reproduces_same_day_inference_and_retains_parent(tmp_path,family,unit):
    cfg,predictions,now=setup_conditioned_job(tmp_path,family=family,unit=unit)
    before=cfg['source_store'].pin_read_view();parent=cfg['artifacts'].pin(cfg['parent_bundle_sha256'])
    result=run_forecast_fit(**cfg)['result'];candidate=cfg['artifacts'].pin(result['candidate_bundle_sha256'])
    assert result['status']=='NO_PROMOTION' and result['conditioning_contract']==cfg['envelope'].conditioning_contract
    assert result['confirmation_role']=='FIRST_REGISTERED_CONFIRMATION' and not result['financial_authority']
    assert cfg['source_store'].pin_read_view()==before and cfg['artifacts'].pin(parent.sha256)==parent
    assert candidate.payload['components']['CALIBRATION']['parameters']['status']=='UNCALIBRATED'
    for join,(rule,components,observed,coverage,at) in zip(cfg['joins'],predictions):
        predicted=predict_with_bundle(candidate,rule,components,as_of=at,max_source_age_seconds=120.,observed=observed,remaining_coverage=coverage)
        params=result['selected_parameters'];rows=[e.payload for e in examples(cfg,join)]
        fitted=learner._predict(rows,cfg['envelope'],params['bias'],params['kernel_sigma'],learner._Budget(cfg['envelope'],lambda:0.))
        assert fitted==pytest.approx([predicted.binary(r['target_identity']['market_id'],'YES',required_target=FINAL_EXTREME).point for r in rows])


def test_new_conditioning_policy_is_distinct_and_original_policy_serialization_is_unchanged(tmp_path):
    cfg,_,_=setup_conditioned_job(tmp_path);new=cfg['envelope'];fields=asdict(new);fields.pop('conditioning_contract')
    old=learner.LearningEnvelope(**fields)
    assert asdict(old)==fields and old.sha256==digest(fields) and old.sha256!=new.sha256
    with pytest.raises(EvidenceError,match='CONDITIONING_POLICY'):
        replace(new,conditioning_contract='ignore_observed_extreme')


def test_conditioned_confirmation_does_not_choose_parameters(tmp_path):
    first,_,_=setup_conditioned_job(tmp_path/'first');other,_,_=setup_conditioned_job(tmp_path/'other',confirmation_value=2)
    a=run_forecast_fit(**first)['result'];b=run_forecast_fit(**other)['result']
    assert a['selected_parameters']==b['selected_parameters'] and a['trials']==b['trials']
    assert a['comparisons']['CONFIRMATION']['challenger']['brier']!=b['comparisons']['CONFIRMATION']['challenger']['brier']


def test_worker_runs_conditioned_cohort_once_and_keeps_evidence_backoff(tmp_path):
    cfg,_,now=setup_conditioned_job(tmp_path)
    worker=ForecastLearningWorker(source_store=cfg.pop('source_store'),artifacts=cfg.pop('artifacts'),journal=cfg.pop('journal'),
        policy=LearningTriggerPolicy('same-day-worker',2,60.,2))
    completed=worker.step('first',**cfg);d=completed['body']['details']
    assert d['outcome']=='RESEARCH_COMPLETED_NO_PROMOTION' and len(d['state']['used_city_days'])==6
    before=worker.store.pin_read_view();assert worker.step('first',**cfg)==completed and before==worker.store.pin_read_view()
    now[0]+=61;next_cfg=dict(cfg,run_id='another-fit',provenance=dict(cfg['provenance'],run_id='another-fit',created_at=now[0]))
    assert worker.step('same-cohort',**next_cfg)['body']['details']['outcome']=='DEFERRED_NEW_RESOLVED_EVIDENCE'
    assert worker._get('another-fit:selection') is None


def test_conditioned_recipe_replay_never_repeats_a_fit(tmp_path,monkeypatch):
    import polymarket_scanner.v11.forecast_learning as jobs
    cfg,_,_=setup_conditioned_job(tmp_path);a=run_forecast_fit(**cfg);before=cfg['journal'].store.pin_read_view()
    monkeypatch.setattr(jobs,'run_research_fit',lambda **k:(_ for _ in ()).throw(AssertionError('duplicate fit')))
    assert run_forecast_fit(**cfg)==a and before==cfg['journal'].store.pin_read_view()


@pytest.mark.parametrize('fault',['source_role','observation_sha','coverage_sha','coverage_gap','model_sha','cutoff','family','missing_rule'])
def test_conditioned_fit_requires_exact_rule_sources_intervals_and_target(tmp_path,fault):
    cfg,_,_=setup_conditioned_job(tmp_path);p=deepcopy(examples(cfg)[0].payload);c=p['conditioning']
    if fault=='source_role':c['observed_constraint']['source_role']='PWS_PROXY'
    if fault=='observation_sha':c['observed_constraint']['evidence_sha256']='f'*64
    if fault=='coverage_sha':c['remaining_coverage']['source_coverage_sha256']='f'*64
    if fault=='coverage_gap':c['remaining_coverage']['unresolved_intervals'][0][0]+=1
    if fault=='model_sha':c['remaining_coverage']['model_evidence_sha256']=['f'*64]
    if fault=='cutoff':c['inference_cutoff']+=1
    if fault=='family':c['observed_constraint']['family']='LOW'
    if fault=='missing_rule':p.pop('conditioning_rule')
    with pytest.raises(EvidenceError):learner.validate_conditioned_example(p,cfg['envelope'],'F')


def test_conditioned_policy_cannot_adopt_unconditioned_captures(tmp_path):
    cfg,_,_,_=setup_job(tmp_path);cfg['envelope']=learner.ConditionedLearningEnvelope(**asdict(cfg['envelope']))
    old=cfg['journal'].store.get('plan')['body']['details']['plan'];old['comparison_policy_sha256']=cfg['envelope'].sha256
    cfg['journal'].register('conditioned-plan',DatasetPlan(**old));cfg['plan_record_id']='conditioned-plan'
    cfg['provenance']['comparison_policy_sha256']=cfg['envelope'].sha256
    with pytest.raises(EvidenceError,match='VERIFIED_CAPTURE_REQUIRED'):run_forecast_fit(**cfg)
    assert not cfg['journal'].store.records(kind='MODEL_EVENT',event_id='another-program')


def test_ordinary_policy_cannot_adopt_conditioned_captures(tmp_path):
    cfg,_,_=setup_conditioned_job(tmp_path);fields=asdict(cfg['envelope']);fields.pop('conditioning_contract')
    cfg['envelope']=learner.LearningEnvelope(**fields);old=cfg['journal'].store.get('plan')['body']['details']['plan']
    old['comparison_policy_sha256']=cfg['envelope'].sha256;cfg['journal'].register('ordinary-plan',DatasetPlan(**old));cfg['plan_record_id']='ordinary-plan'
    cfg['provenance']['comparison_policy_sha256']=cfg['envelope'].sha256
    with pytest.raises(EvidenceError,match='VERIFIED_CAPTURE_REQUIRED'):run_forecast_fit(**cfg)
