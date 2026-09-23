from dataclasses import asdict, replace
import copy
import json

import pytest

from polymarket_scanner.v11.datasets import (
    FeatureDefinition, FeatureSchema, DatasetPlan, ExperimentJournal, archive_features, build_example, build_dataset,
)
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, ReleaseBinding, canonical, digest
from polymarket_scanner.v11.model_artifacts import ArtifactStore
from polymarket_scanner.v11.offline_learning import LearningEnvelope, run_research_fit
from test_v11_model_artifacts import make_artifacts, provenance


def policy(**changes):
    return replace(LearningEnvelope('model-1',('member_0',),'lower_cut','upper_cut',(0.,1.),(.2,1.),'brier',
                  2,2,2,0.,.01,10.,100_000,100,23),**changes)


def prepare(tmp_path, *, envelope=None, confirmation_value=1, train_count=2):
    tmp_path.mkdir(exist_ok=True)
    tmp_path.chmod(0o700)
    objects=tmp_path/'objects'
    objects.mkdir(mode=0o700)
    artifacts=ArtifactStore(objects)
    schema=FeatureSchema('kernel-features-v1',(
        FeatureDefinition('member_0','C','FORECAST',-80,65,False),
        FeatureDefinition('lower_cut','C','RULE_CUT',-100,100,True),
        FeatureDefinition('upper_cut','C','RULE_CUT',-100,100,True)))
    values=make_artifacts()
    values['FEATURES']['parameters']=json.loads(canonical(asdict(schema)))
    for item in values.values(): item['feature_schema_sha256']=schema.sha256
    values['CALIBRATION']['parameters']['probability_artifact_sha256']=digest(values['PROBABILITY'])
    refs={k:artifacts.put_artifact(v) for k,v in values.items()}
    parent=artifacts.put_bundle(artifacts=refs,target='FINAL_CONTRACT_PAYOUT',feature_schema_sha256=schema.sha256)
    now=[1.]
    store=EvidenceStore(tmp_path/'evidence.sqlite','CHALLENGER:learner-test',clock=lambda:now[0])
    journal=ExperimentJournal(store,'kernel-temperature')
    envelope=envelope or policy()
    plan=DatasetPlan('fixed-plan','FINAL_CONTRACT_PAYOUT',schema.sha256,0.,30.,60.,90.,envelope.sha256)
    journal.register('plan',plan)
    examples=[]
    times=[10.,20.][:train_count]+[40.,50.,70.,80.]
    for i,at in enumerate(times):
        event=f'event:{i}'
        station='KATL' if i%2==0 else 'KSFO'
        city='Atlanta' if i%2==0 else 'SanFrancisco'
        local_date=f'2026-01-{i+1:02d}'
        now[0]=at-2
        raw=store.capture(f'raw:{i}',event_id=event,kind='MODEL',provider='fixture',source_identity='forecast',
                          revision='1',payload={'members':[20.]},evidence_class='SYNTHETIC')
        now[0]=at-1
        f=archive_features(store,f'feature:{i}',event_id=event,schema=schema,
            values={'member_0':20.,'lower_cut':19.5,'upper_cut':20.5},evidence_ids=(raw['id'],),source_versions={'fixture':'1'})
        target={'market_id':'m1','condition_id':'c1','token_id':'yes-token','side':'YES'}
        now[0]=at
        d=store.decision(f'decision:{i}',event_id=event,strategy='FUTURE_FORECAST',
            binding=ReleaseBinding('a'*40,'b'*40,'c'*64,parent,'e'*64),evidence_ids=(f['id'],),feature_ready_at=at-1,
            valuation_type='SETTLEMENT',target='FINAL_CONTRACT_PAYOUT',outcome='GATED',reason='TEST',
            explanation={'target_identity':target},expires_at=at+1)
        now[0]=at+1
        lab=store.capture(f'label:{i}',event_id=event,kind='LABEL',provider='fixture',source_identity='payout',revision='1',
            payload={'context':{'station':station,'city':city,'local_date':local_date,'target':'FINAL_CONTRACT_PAYOUT',
                                 'rule_fingerprint':'e'*64},'target_identity':target,'label_version':'1',
                     'decision_target':'FINAL_CONTRACT_PAYOUT','knowable_at':at+1,
                     'value':confirmation_value if at>=60 else 1,'evidence_type':'SYNTHETIC'},evidence_class='SYNTHETIC')
        examples.append(build_example(store,decision_id=d['id'],feature_id=f['id'],label_id=lab['id'],
            station=station,city=city,local_date=local_date,horizon='0_24H',season='WINTER',target='FINAL_CONTRACT_PAYOUT',
            selection='ALL_SUPPORTED_PREDICTIONS',prior_exposure='UNINSPECTED' if at>=60 else 'DEVELOPMENT'))
    now[0]=91.
    dataset=build_dataset(tuple(examples),plan,as_of=91.)
    prov={**provenance(),'created_at':91.,'run_id':'run-1','comparison_policy_sha256':envelope.sha256}
    return {'artifacts':artifacts,'journal':journal,'plan_record_id':'plan','dataset':dataset,
            'parent_bundle_sha256':parent,'envelope':envelope,'provenance':prov,'run_id':'run-1'}


def test_bounded_fit_produces_reproducible_data_only_candidate_and_no_promotion(tmp_path):
    first=prepare(tmp_path/'one')
    second=prepare(tmp_path/'two')
    a=run_research_fit(**first)
    b=run_research_fit(**second)
    assert a==b
    r=a['result']
    assert r['selected_parameters']=={'bias':0.,'kernel_sigma':.2}
    assert len(r['trials'])==4
    assert r['selection_partition']=='TRAIN_ONLY'
    assert r['status']=='NO_PROMOTION' and not r['financial_authority']
    assert r['confirmation_role']=='FIRST_REGISTERED_CONFIRMATION'
    assert r['calibration_status']=='FITTED_NOT_CALIBRATED'
    assert first['artifacts'].pin(r['candidate_bundle_sha256']).payload['components']['CALIBRATION']['parameters']['status']=='UNCALIBRATED'
    saved=first['journal'].store.get('run-1:result')['body']['details']
    assert saved['result']==r and saved['sha256']==a['sha256']
    assert not hasattr(first['artifacts'],'promote')


def test_confirmation_results_do_not_select_the_model(tmp_path):
    yes=run_research_fit(**prepare(tmp_path/'yes',confirmation_value=1))['result']
    no=run_research_fit(**prepare(tmp_path/'no',confirmation_value=0))['result']
    assert yes['selected_parameters']==no['selected_parameters']
    assert yes['comparisons']['CONFIRMATION']['challenger']['brier']!=no['comparisons']['CONFIRMATION']['challenger']['brier']
    assert no['status']=='NO_PROMOTION'


def test_sparse_fit_keeps_champion_and_does_not_create_a_candidate(tmp_path):
    cfg=prepare(tmp_path/'sparse',train_count=1)
    before=list(cfg['artifacts'].root.iterdir())
    r=run_research_fit(**cfg)['result']
    assert r['reason']=='INSUFFICIENT_TRAINING_GROUPS' and r['candidate_bundle_sha256'] is None
    assert list(cfg['artifacts'].root.iterdir())==before


@pytest.mark.parametrize('kind',['kernel','wall'])
def test_resource_failure_is_durable_and_parent_is_unchanged(tmp_path,kind):
    cfg=prepare(tmp_path/'bounded',envelope=policy(max_kernel_evaluations=1) if kind=='kernel' else policy())
    parent_before=cfg['artifacts'].pin(cfg['parent_bundle_sha256']).canonical_json
    if kind=='wall':
        clock=[0]
        def tick():
            clock[0]+=20
            return clock[0]
        cfg['monotonic']=tick
    with pytest.raises(EvidenceError,match='RESOURCE_BUDGET_EXHAUSTED'):
        run_research_fit(**cfg)
    assert cfg['artifacts'].pin(cfg['parent_bundle_sha256']).canonical_json==parent_before
    failure=cfg['journal'].store.get('run-1:failed')['body']['details']
    assert failure['status']=='FAILED' and failure['champion_unchanged']
    trial=cfg['journal'].store.get('run-1:trial:0:finished')['body']['details']
    assert trial['status']=='FAILED'


def test_policy_change_after_plan_registration_fails_before_training(tmp_path):
    cfg=prepare(tmp_path/'policy')
    cfg['envelope']=policy(bias_grid=(0.,2.))
    cfg['provenance']['comparison_policy_sha256']=cfg['envelope'].sha256
    with pytest.raises(EvidenceError,match='FROZEN_LEARNER_POLICY_MISMATCH'):
        run_research_fit(**cfg)


def test_trial_ledger_records_every_evaluated_parameter(tmp_path):
    cfg=prepare(tmp_path/'trials')
    run_research_fit(**cfg)
    rows=cfg['journal'].store.records(kind='MODEL_EVENT',event_id=cfg['journal'].event_id)
    started=[r['body']['details'] for r in rows if r['body']['details'].get('action')=='ATTEMPT_STARTED']
    assert len(started)==5  # Four frozen candidates and the selection run.
    finished=[r['body']['details'] for r in rows if r['body']['details'].get('action')=='ATTEMPT_FINISHED']
    assert len(finished)==5 and all(r['status']=='NO_PROMOTION' for r in finished)


def test_repeated_confirmation_is_logged_as_development(tmp_path):
    cfg=prepare(tmp_path/'reuse')
    run_research_fit(**cfg)
    cfg['run_id']='run-2'
    cfg['provenance']['run_id']='run-2'
    result=run_research_fit(**cfg)['result']
    assert result['confirmation_role']=='DEVELOPMENT'
    assert result['status']=='NO_PROMOTION'
