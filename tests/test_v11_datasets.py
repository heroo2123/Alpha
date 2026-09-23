import copy
from dataclasses import replace

import pytest

from polymarket_scanner.v11.datasets import (
    FeatureDefinition, FeatureSchema, DatasetPlan, ExperimentJournal,
    archive_features, build_example, build_dataset, verify_dataset,
)
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, ReleaseBinding, digest


@pytest.fixture
def env(tmp_path):
    tmp_path.chmod(0o700)
    clock=[1.]
    store=EvidenceStore(tmp_path/'data.sqlite','CHALLENGER:test',clock=lambda:clock[0])
    schema=FeatureSchema('test-v1',(FeatureDefinition('temperature','C','weather',-80,65,False),
                                    FeatureDefinition('pws_count','count','PWS',0,100,True)))
    return store,schema,clock


def example(env, key='a', *, decision_at=10., label_at=15., day='2026-01-01', event=None,
            exposure='DEVELOPMENT', target='FINAL_CONTRACT_PAYOUT', selection='ALL_SUPPORTED_PREDICTIONS',
            evidence_type='SYNTHETIC', source_class='SYNTHETIC'):
    store,schema,clock=env
    event=event or 'event:'+key
    clock[0]=decision_at-2
    raw=store.capture(key+':raw',event_id=event,kind='MODEL',provider='test',source_identity='temperature',
                      revision='original',payload={'temperature':20},observed_at=clock[0]-1,
                      evidence_class=source_class)
    clock[0]=decision_at-1
    feature=archive_features(store,key+':features',event_id=event,schema=schema,
                             values={'temperature':20,'pws_count':None},evidence_ids=(raw['id'],),
                             source_versions={'test':'source-v1'})
    clock[0]=decision_at
    target_identity={'market_id':'m1','condition_id':'c1','token_id':'yes-token','side':'YES'}
    if target=='REAL_EXECUTION_COST':
        target_identity={'token_id':'yes-token','units':1.,'horizon_seconds':0.,'measurement_class':'RECONCILED_LIVE_EXECUTION'}
    decision=store.decision(key+':decision',event_id=event,strategy='FUTURE_FORECAST',
        binding=ReleaseBinding('a'*40,'b'*40,'c'*64,'d'*64,'e'*64),evidence_ids=(feature['id'],),
        feature_ready_at=decision_at-1,valuation_type='SETTLEMENT',target=target,outcome='GATED',
        reason='RESEARCH_ONLY',explanation={'point':.5,'target_identity':target_identity},expires_at=decision_at+1)
    clock[0]=label_at
    label=store.capture(key+':label',event_id=event,kind='LABEL',provider='test-label',source_identity='outcome',
        revision='1',payload={'context':{'station':'KATL','city':'Atlanta','local_date':day,'target':target,
                                       'rule_fingerprint':'e'*64},'label_version':'1','decision_target':target,
                              'knowable_at':label_at,'value':1,'evidence_type':evidence_type,
                              'target_identity':target_identity},evidence_class=source_class)
    return build_example(store,decision_id=decision['id'],feature_id=feature['id'],label_id=label['id'],
            station='KATL',city='Atlanta',local_date=day,horizon='0_24H',season='WINTER',target=target,
            selection=selection,prior_exposure=exposure)


def plan(env):
    return DatasetPlan('plan-1','FINAL_CONTRACT_PAYOUT',env[1].sha256,0.,20.,40.,60.,'f'*64)


def test_archived_feature_schema_and_complete_provenance(env):
    x=example(env).payload
    assert x['feature_ready_at'] < x['decision_at'] < x['label_available_at']
    assert x['feature_schema_sha256']==env[1].sha256
    assert x['values']['pws_count'] is None
    assert {r['kind'] for r in x['provenance']}=={'MODEL','FEATURES'}
    assert x['binding']['code_commit']=='a'*40
    assert not x['label_independently_attested']


@pytest.mark.parametrize('values',[{'temperature':20}, {'temperature':None,'pws_count':1},
                                  {'temperature':100,'pws_count':1},{'temperature':20,'pws_count':True}])
def test_schema_missing_or_invalid_values_fail(env,values):
    with pytest.raises(EvidenceError): env[1].validate(values)


def test_labels_and_unknown_historical_availability_cannot_be_features(env):
    store,schema,clock=env
    x=example(env)
    with pytest.raises(EvidenceError,match='NONCAUSAL_FEATURE_INPUT'):
        archive_features(store,'bad',event_id='event:a',schema=schema,values={'temperature':20,'pws_count':None},
                          evidence_ids=('a:label',),source_versions={'test':'1'})
    clock[0]=30
    store.capture('backfill',event_id='event:a',kind='MODEL',provider='test',source_identity='x',revision='2',
                   payload={'temperature':30},evidence_class='HISTORICAL_AVAILABILITY_UNKNOWN')
    with pytest.raises(EvidenceError,match='NONCAUSAL_FEATURE_INPUT'):
        archive_features(store,'bad2',event_id='event:a',schema=schema,values={'temperature':20,'pws_count':None},
                          evidence_ids=('backfill',),source_versions={'test':'1'})


def test_label_available_after_fit_cutoff_refused(env):
    x=example(env,label_at=21.)
    with pytest.raises(EvidenceError,match='LABEL_UNAVAILABLE_AT_TRAINING_CUTOFF'):
        build_dataset((x,),plan(env),as_of=22.)


def test_temporal_disjoint_manifest_is_reproducible(env):
    a=example(env)
    b=example(env,'b',decision_at=30.,label_at=35.,day='2026-01-02')
    c=example(env,'c',decision_at=50.,label_at=55.,day='2026-01-03',exposure='UNINSPECTED')
    dataset=build_dataset((c,a,b),plan(env),as_of=56.)
    assert verify_dataset(dataset)==dataset['manifest']
    assert build_dataset((a,b,c),plan(env),as_of=56.)==dataset
    assert {k:v['city_days'] for k,v in dataset['manifest']['counts'].items()}=={'TRAIN':1,'DEVELOPMENT':1,'CONFIRMATION':1}


def test_all_inspected_v10_data_must_remain_development(env):
    x=example(env,decision_at=50.,label_at=55.)
    with pytest.raises(EvidenceError,match='INSPECTED_DATA_IS_DEVELOPMENT'):
        build_dataset((x,),plan(env),as_of=56.)


@pytest.mark.parametrize('same_event',[False,True])
def test_city_day_and_event_cannot_leak_across_time_splits(env,same_event):
    a=example(env)
    b=example(env,'b',decision_at=30.,label_at=35.,day='2026-01-02' if same_event else '2026-01-01',
              event='event:a' if same_event else None)
    with pytest.raises(EvidenceError,match='SPLIT_LEAKAGE'):
        build_dataset((a,b),plan(env),as_of=36.)


def test_duplicate_prediction_or_correction_cannot_inflate_sample_count(env):
    x=example(env)
    with pytest.raises(EvidenceError,match='DUPLICATE_DECISION_OR_LABEL_REVISION'):
        build_dataset((x,x),plan(env),as_of=16.)


def test_source_label_proxy_is_not_final_settlement(env):
    with pytest.raises(EvidenceError,match='PROXY_IS_NOT_SETTLEMENT_LABEL'):
        example(env,evidence_type='OBSERVATION_LABEL')


def test_rejected_counterfactual_cannot_supply_real_execution_cost(env):
    with pytest.raises(EvidenceError,match='HYPOTHETICAL_IS_NOT_REAL_EXECUTION'):
        example(env,target='REAL_EXECUTION_COST',selection='REJECTED_COUNTERFACTUALS',
                 evidence_type='DEPTH_COUNTERFACTUAL')


def test_late_correction_preserves_original_feature_and_label_identity(env):
    store,_,clock=env
    x=example(env)
    original=x.payload
    clock[0]=30
    label=store.get('a:label')['body']['payload']
    label.update(value=0,label_version='2',knowable_at=30)
    store.capture('a:correction',event_id='event:a',kind='LABEL',provider='test-label',source_identity='outcome',
                   revision='2',payload=label,evidence_class='SYNTHETIC')
    y=build_example(store,decision_id='a:decision',feature_id='a:features',label_id='a:correction',station='KATL',
            city='Atlanta',local_date='2026-01-01',horizon='0_24H',season='WINTER',target='FINAL_CONTRACT_PAYOUT',
            selection='ALL_SUPPORTED_PREDICTIONS',prior_exposure='DEVELOPMENT')
    assert x.payload==original and y.payload['label_value']==0
    assert x.sha256!=y.sha256
    assert x.payload['feature_sha256']==y.payload['feature_sha256']
    with pytest.raises(EvidenceError,match='TRAINING_CUTOFF'):
        build_dataset((y,),plan(env),as_of=31)


def test_rehashing_a_tampered_partition_does_not_bypass_validation(env):
    dataset=build_dataset((example(env),),plan(env),as_of=16.)
    changed=copy.deepcopy(dataset)
    changed['manifest']['partitions']['CONFIRMATION']=changed['manifest']['partitions'].pop('TRAIN')
    changed['sha256']=digest(changed['manifest'])
    with pytest.raises(EvidenceError,match='DATASET_INTEGRITY'):
        verify_dataset(changed)


def test_repeated_confirmation_is_development_even_with_new_candidate(env):
    store,_,clock=env
    journal=ExperimentJournal(store,'temperature')
    registered=journal.register('plan',plan(env))
    journal.attempt('attempt-1',plan_record_id=registered['id'],candidate_sha256='1'*64,
                     parent_champion_sha256='2'*64,hyperparameters={'bias':0})
    x=example(env,decision_at=50,label_at=55,day='2026-01-03',exposure='UNINSPECTED')
    data=build_dataset((x,),plan(env),as_of=55)
    first=journal.reveal_confirmation('reveal-1',attempt_id='attempt-1',dataset=data)
    assert first['body']['details']['evidence_role']=='FIRST_REGISTERED_CONFIRMATION'
    clock[0]=56
    journal.finish('finish-1',attempt_id='attempt-1',status='NO_PROMOTION',reason='TOO_SPARSE',result_sha256=data['sha256'])
    journal.attempt('attempt-2',plan_record_id=registered['id'],candidate_sha256='3'*64,
                     parent_champion_sha256='2'*64,hyperparameters={'bias':1})
    reused=journal.reveal_confirmation('reveal-2',attempt_id='attempt-2',dataset=data)
    assert reused['body']['details']['evidence_role']=='DEVELOPMENT'
    assert reused['body']['details']['reused_confirmation']
    assert not reused['body']['details']['promotion_authorized']


def test_failed_attempt_is_durable_and_cannot_be_finished_twice_or_promote(env):
    store,_,_=env
    journal=ExperimentJournal(store,'temperature')
    journal.register('plan',plan(env))
    journal.attempt('attempt',plan_record_id='plan',candidate_sha256='1'*64,
                     parent_champion_sha256='2'*64,hyperparameters={'bias':0})
    with pytest.raises(EvidenceError,match='CANNOT_PROMOTE'):
        journal.finish('bad',attempt_id='attempt',status='PROMOTED',reason='NOT_ALLOWED',result_sha256=None)
    result=journal.finish('failed',attempt_id='attempt',status='FAILED',reason='RESOURCE_BUDGET',result_sha256=None)
    assert result['body']['details']['champion_unchanged']
    with pytest.raises(EvidenceError,match='ALREADY_FINISHED'):
        journal.finish('retry',attempt_id='attempt',status='PROPOSAL_ONLY',reason='RETRY',result_sha256=None)


def test_retrospective_registration_never_claims_untouched_confirmation(env):
    store,_,clock=env
    x=example(env,decision_at=50,label_at=55,day='2026-01-03',exposure='UNINSPECTED')
    journal=ExperimentJournal(store,'temperature')
    journal.register('late-plan',plan(env))
    journal.attempt('late-attempt',plan_record_id='late-plan',candidate_sha256='1'*64,
                     parent_champion_sha256='2'*64,hyperparameters={})
    result=journal.reveal_confirmation('late-reveal',attempt_id='late-attempt',
                                      dataset=build_dataset((x,),plan(env),as_of=55))
    assert result['body']['details']['evidence_role']=='DEVELOPMENT'


def test_same_event_wrong_token_label_cannot_cross_target_boundary(env):
    store,_,clock=env
    example(env)
    clock[0]=20
    payload=store.get('a:label')['body']['payload']
    payload['target_identity']['token_id']='different-token'
    payload['label_version']='2'
    store.capture('wrong-token',event_id='event:a',kind='LABEL',provider='test-label',source_identity='outcome',
                  revision='2',payload=payload,evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='LABEL_EXACT_TARGET_MISMATCH'):
        build_example(store,decision_id='a:decision',feature_id='a:features',label_id='wrong-token',station='KATL',
            city='Atlanta',local_date='2026-01-01',horizon='0_24H',season='WINTER',target='FINAL_CONTRACT_PAYOUT',
            selection='ALL_SUPPORTED_PREDICTIONS',prior_exposure='DEVELOPMENT')
