"""Synthetic complete-vector evidence: never operational calibration or fills."""
from dataclasses import replace
from datetime import date, timedelta

import pytest

from polymarket_scanner.v11.datasets import DatasetPlan, ExperimentJournal
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, ReleaseBinding
from polymarket_scanner.v11.event_risk import EventContext
from polymarket_scanner.v11.forecast_features import ForecastFeatureContract, build_initial_forecast_bundle
from polymarket_scanner.v11.forecast_learning import ForecastLabelJoin, run_forecast_fit
from polymarket_scanner.v11.learning_capture import capture_forecast_vector
from polymarket_scanner.v11.model_artifacts import ArtifactStore, predict_with_bundle
from polymarket_scanner.v11.offline_learning import LearningEnvelope
from polymarket_scanner.v11.probability import FINAL_EXTREME, target_identity
from polymarket_scanner.v11.rules import fingerprint_event
from polymarket_scanner.v11.strategy_pipeline import INPUT_VERSION, _model_inputs
from test_v11_model_artifacts import make_artifacts, provenance
from test_v11_probability import T, rule
from test_weather_final_gpt6_exact_replays import _event


DAY = 86400.


def setup_job(tmp_path, *, unit='F', family='high', event_count=6):
    tmp_path.mkdir(exist_ok=True); tmp_path.chmod(0o700)
    objects=tmp_path/'objects'; objects.mkdir(mode=0o700)
    artifacts=ArtifactStore(objects)
    base=rule(family,unit)
    contract=ForecastFeatureContract((('model-1',31),),unit,base.payload['family'])
    parent=build_initial_forecast_bundle(artifacts,contract=contract,
        probability_parameters=make_artifacts()['PROBABILITY']['parameters'],
        provenance={**provenance(),'created_at':T-10},quality_modifiers={})
    envelope=LearningEnvelope('model-1',tuple(contract.mapping['model-1']),'lower_cut','upper_cut',
        (0.,1.),(.2,1.),'brier',2,2,2,0.,.01,10.,100_000,100,23)
    now=[T-2]
    source=EvidenceStore(tmp_path/'sources.sqlite','V11_PAPER',clock=lambda:now[0])
    research=EvidenceStore(tmp_path/'research.sqlite','CHALLENGER:forecast-test',clock=lambda:now[0])
    journal=ExperimentJournal(research,'forecast')
    plan=DatasetPlan('forecast-plan','FINAL_CONTRACT_PAYOUT',contract.schema.sha256,
                     T-1,T+6*DAY-1,T+12*DAY-1,T+max(18,3*event_count)*DAY,envelope.sha256)
    journal.register('plan',plan)
    joins=[]; inferences=[]
    for i in range(event_count):
        at=T+3*i*DAY; station='KATL' if i%2==0 else 'KLGA'; city='Atlanta' if i%2==0 else 'NewYork'
        r=fingerprint_event(_event(station=station,family=family,unit=unit,eid=f'event-{i}',
            target=date(2026,9,15)+timedelta(days=3*i)),station_timezone='America/New_York',metadata_fingerprint='a'*64)
        context=EventContext('paper',city,station,r.payload['event_id'])
        binding=ReleaseBinding('a'*40,'b'*40,'c'*64,parent,r.sha256)
        now[0]=at
        source.capture(f'model-{i}',event_id=context.event_id,kind='MODEL',provider='TEST_ONLY',source_identity='model-1',
            revision=str(i),issued_at=at-30,evidence_class='SYNTHETIC',payload=dict(
                {k:r.payload[k] for k in ('station','target_date','family','unit')},rule_fingerprint=r.sha256,
                temperature_input=dict(version=INPUT_VERSION,model_id='model-1',target_sha256=target_identity(r,FINAL_EXTREME),
                                       members=[70.4+j*.001 for j in range(31)])))
        now[0]=at+1
        components=_model_inputs(source,r,(f'model-{i}',),now[0],target=FINAL_EXTREME)
        prediction=predict_with_bundle(artifacts.pin(parent),r,components,as_of=now[0],max_source_age_seconds=120.)
        captured=capture_forecast_vector(source,f'capture-{i}',context=context,rule=r,binding=binding,
            prediction=prediction,pinned_bundle=artifacts.pin(parent),model_input_ids=(f'model-{i}',),expires_at=at+20)
        inferences.append((r,components,prediction,now[0]))
        now[0]=at+2*DAY
        ids=[]
        for j,row in enumerate(captured['body']['details']['rows']):
            target=row['target_identity']; key=f'label-{i}-{j}'; ids.append((target['market_id'],key))
            source.capture(key,event_id=context.event_id,kind='LABEL',provider='TEST_ONLY',source_identity=target['token_id'],
                revision='test-v1',evidence_class='SYNTHETIC',payload=dict(
                    context=dict(station=station,city=city,local_date=r.payload['target_date'],
                                 target='FINAL_CONTRACT_PAYOUT',rule_fingerprint=r.sha256),
                    target_identity=target,label_version='test-v1',decision_target='FINAL_CONTRACT_PAYOUT',
                    knowable_at=now[0],value=int(j==1),evidence_type='SYNTHETIC'))
        joins.append(ForecastLabelJoin(captured['id'],tuple(ids),city,'DAY_AHEAD','AUTUMN',
                                       'UNINSPECTED' if i>=4 else 'DEVELOPMENT'))
    now[0]=T+max(18,3*event_count)*DAY+1
    cfg=dict(source_store=source,artifacts=artifacts,journal=journal,plan_record_id='plan',joins=tuple(joins),
        parent_bundle_sha256=parent,envelope=envelope,provenance={**provenance(),'created_at':now[0],
            'run_id':'forecast-fit','comparison_policy_sha256':envelope.sha256},run_id='forecast-fit',as_of=now[0])
    return cfg,inferences,contract,now


@pytest.mark.parametrize('unit,family',[('F','high'),('F','low'),('C','high'),('C','low')])
def test_capture_dataset_fit_and_candidate_inference_share_exact_31_member_contract(tmp_path,unit,family):
    cfg,inferences,contract,now=setup_job(tmp_path,unit=unit,family=family)
    source=cfg['source_store']; before=source.pin_read_view()
    parent_before=cfg['artifacts'].pin(cfg['parent_bundle_sha256']).canonical_json
    outcome=run_forecast_fit(**cfg); result=outcome['result']
    assert result['status']=='NO_PROMOTION' and not result['financial_authority']
    assert result['selected_parameters']=={'bias':0.,'kernel_sigma':.2}
    assert result['confirmation_role']=='FIRST_REGISTERED_CONFIRMATION'
    assert result['comparisons']['CONFIRMATION']['challenger']['n_city_days']==2
    assert result['comparisons']['CONFIRMATION']['challenger']['n_events']==2
    assert result['comparisons']['CONFIRMATION']['challenger']['n_binary_targets']==6
    child=cfg['artifacts'].pin(result['candidate_bundle_sha256'])
    assert child.payload['bundle']['feature_schema_sha256']==contract.schema.sha256
    assert child.payload['components']['CALIBRATION']['parameters']['status']=='UNCALIBRATED'
    assert cfg['artifacts'].pin(cfg['parent_bundle_sha256']).canonical_json==parent_before
    assert source.pin_read_view()==before
    r,components,original,at=inferences[-1]
    predicted=predict_with_bundle(child,r,components,as_of=at,max_source_age_seconds=120.)
    assert predicted.payload['buckets'][1]['point']>original.payload['buckets'][1]['point']
    probabilities=[b['point'] for b in predicted.payload['buckets']]
    brier=sum(2*(p-int(j==1))**2 for j,p in enumerate(probabilities))/3
    assert result['comparisons']['CONFIRMATION']['challenger']['brier']==pytest.approx(brier)
    recipe=cfg['journal'].store.get('forecast-fit:dataset')['body']['details']
    assert recipe['counts']['TRAIN']['examples']==6 and len(recipe['captures'])==6
    assert recipe['dataset_sha256']==result['dataset_sha256']
    assert recipe['source_archive_mutated'] is False and not recipe['independent_label_attestation']
    assert not source.records(kind='MODEL_EVENT') and not source.records(kind='COORDINATOR_EVENT')
    # Completed recovery reads the immutable result; no refit, extra trials or timestamps.
    research_before=cfg['journal'].store.pin_read_view(); now[0]+=100
    assert run_forecast_fit(**cfg)==outcome and cfg['journal'].store.pin_read_view()==research_before


def test_identical_source_capture_and_plan_produce_identical_research_artifacts(tmp_path):
    a=setup_job(tmp_path/'a')[0]; b=setup_job(tmp_path/'b')[0]
    assert run_forecast_fit(**a)==run_forecast_fit(**b)


@pytest.mark.parametrize('change',[{'unit':'K'},{'family':'humidity'},{'quantization':'CEILING'},
    {'model_widths':(('model-1',True),)},{'model_widths':(('model-1',127),)},
    {'model_widths':(('model-1',1),('model-1',1))}])
def test_forecast_contract_rejects_ambiguous_or_unsupported_dimensions(change):
    kwargs=dict(model_widths=(('model-1',31),),unit='F',family=rule().payload['family'])
    with pytest.raises(EvidenceError): ForecastFeatureContract(**{**kwargs,**change})


def test_model_order_is_stable_but_member_count_unit_and_family_change_identity():
    a=ForecastFeatureContract((('z',2),('a',1)),'F',rule().payload['family'])
    assert a.schema.sha256==replace(a,model_widths=tuple(reversed(a.model_widths))).schema.sha256
    assert a.mapping=={'a':['model_0_member_000'],'z':['model_1_member_000','model_1_member_001']}
    assert len({a.schema.sha256,replace(a,unit='C').schema.sha256,
        replace(a,model_widths=(('a',2),('z',2))).schema.sha256,
        replace(a,family=rule('low').payload['family']).schema.sha256})==4


def test_mismatched_parent_cannot_silently_adapt_or_discard_forecast_members(tmp_path):
    cfg,inferences,_,_=setup_job(tmp_path)
    r,components,_,at=inferences[0]; parent=cfg['artifacts'].pin(cfg['parent_bundle_sha256'])
    with pytest.raises(EvidenceError,match='PARENT_CONTRACT_MISMATCH'):
        predict_with_bundle(parent,r,(replace(components[0],members=components[0].members[:-1]),),
                            as_of=at,max_source_age_seconds=120.)
    cfg['envelope']=replace(cfg['envelope'],member_features=cfg['envelope'].member_features[:-1])
    cfg['provenance']['comparison_policy_sha256']=cfg['envelope'].sha256
    with pytest.raises(EvidenceError,match='FROZEN_LEARNER_POLICY_MISMATCH'):run_forecast_fit(**cfg)


@pytest.mark.parametrize('fault',['duplicate_capture','missing_label','wrong_city','inspected_confirmation','journal'])
def test_cohort_identity_and_holdout_defects_fail_without_touching_sources(tmp_path,fault):
    cfg,_,_,_=setup_job(tmp_path); before=cfg['source_store'].pin_read_view()
    joins=list(cfg['joins'])
    if fault=='duplicate_capture':joins.append(joins[0])
    elif fault=='missing_label':joins[0]=replace(joins[0],label_ids=joins[0].label_ids[:-1])
    elif fault=='wrong_city':joins[0]=replace(joins[0],city='Other')
    elif fault=='inspected_confirmation':joins[-1]=replace(joins[-1],prior_exposure='DEVELOPMENT')
    elif fault=='journal':cfg['journal']=ExperimentJournal(cfg['source_store'],'forbidden')
    cfg['joins']=tuple(joins)
    with pytest.raises(EvidenceError):run_forecast_fit(**cfg)
    assert before==cfg['source_store'].pin_read_view()
    assert not cfg['source_store'].records(kind='MODEL_EVENT')


def test_incomplete_attempt_requires_review_and_never_implicitly_refits(tmp_path,monkeypatch):
    from polymarket_scanner.v11 import forecast_learning
    cfg,_,_,_=setup_job(tmp_path); before=cfg['source_store'].pin_read_view()
    with monkeypatch.context() as patch:
        patch.setattr(forecast_learning,'run_research_fit',lambda **k:(_ for _ in ()).throw(RuntimeError('INTERRUPTED')))
        with pytest.raises(RuntimeError):run_forecast_fit(**cfg)
    with pytest.raises(EvidenceError,match='INTERRUPTED_ATTEMPT_REVIEW_REQUIRED'):run_forecast_fit(**cfg)
    assert cfg['source_store'].pin_read_view()==before
    cfg['as_of']+=.1
    with pytest.raises(EvidenceError,match='FUTURE_CUTOFF'):run_forecast_fit(**cfg)


def test_assembly_budget_prevents_fitting_or_source_changes(tmp_path):
    cfg,_,_,_=setup_job(tmp_path); before=cfg['source_store'].pin_read_view(); ticks=iter([0.,11.,12.])
    with pytest.raises(EvidenceError,match='TIME_BOUND'):
        run_forecast_fit(**cfg,monotonic=lambda:next(ticks))
    assert cfg['source_store'].pin_read_view()==before
    assert len(cfg['journal'].store.records(kind='MODEL_EVENT'))==1


def test_completed_request_cannot_be_replayed_with_a_different_cohort(tmp_path):
    cfg,_,_,_=setup_job(tmp_path); run_forecast_fit(**cfg)
    cfg['joins']=tuple(reversed(cfg['joins']))
    with pytest.raises(EvidenceError,match='REPLAY_CONFLICT'):run_forecast_fit(**cfg)


def test_initial_bundle_is_not_a_way_to_relabel_a_fitted_parent(tmp_path):
    tmp_path.chmod(0o700); artifacts=ArtifactStore(tmp_path)
    contract=ForecastFeatureContract((('model-1',31),),'F',rule().payload['family'])
    with pytest.raises(EvidenceError,match='INITIAL_BUNDLE_HAS_NO_PARENT'):
        build_initial_forecast_bundle(artifacts,contract=contract,quality_modifiers={},
            probability_parameters=make_artifacts()['PROBABILITY']['parameters'],
            provenance={**provenance(),'parent_bundle_sha256':'a'*64})
    assert list(tmp_path.iterdir())==[]
