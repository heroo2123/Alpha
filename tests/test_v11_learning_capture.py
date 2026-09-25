from dataclasses import replace

import pytest

from polymarket_scanner.v11 import learning_capture as capture
from polymarket_scanner.v11.datasets import DatasetPlan,build_dataset,verify_dataset
from polymarket_scanner.v11.evidence import EvidenceError,digest,canonical
from polymarket_scanner.v11.forecast_features import ForecastFeatureContract,build_initial_forecast_bundle
from polymarket_scanner.v11.model_artifacts import ArtifactStore
from polymarket_scanner.v11.probability import BucketPrediction
from polymarket_scanner.v11.strategy_pipeline import TemperatureStrategies
from test_v11_certification_rules import setup
from test_v11_model_artifacts import make_artifacts,provenance
from test_v11_probability import rule
from test_v11_strategy_pipeline import factory


@pytest.fixture
def bundle(tmp_path):
    path=tmp_path/'forecast-objects';path.mkdir(mode=0o700)
    store=ArtifactStore(path);p=rule().payload
    contract=ForecastFeatureContract((('model-1',3),),p['unit'],p['family'])
    key=build_initial_forecast_bundle(store,contract=contract,
        probability_parameters=make_artifacts()['PROBABILITY']['parameters'],provenance=provenance(),quality_modifiers={})
    pinned=store.pin(key).payload
    return store,pinned['components'],pinned['bundle']['artifacts'],key


def evaluate(factory,strategy='FUTURE_FORECAST'):
    r=factory(strategy);r['coordinator_before']=r['store'].records(kind='COORDINATOR_EVENT')
    row=TemperatureStrategies(r['store']).evaluate('forecast',r['request'])
    r['evaluation']=row;return r


def kwargs(r):
    from polymarket_scanner.v11.model_registry import ActiveModelRegistry
    d=r['evaluation']['body']['details'];p=d['prediction']
    return dict(context=r['context'],rule=r['rule'],binding=r['binding'],prediction=BucketPrediction(canonical(p),digest(p)),
                pinned_bundle=ActiveModelRegistry().pin(scope_key=r['scope'].key,mode='V11_PAPER').bundle,
                model_input_ids=r['request'].model_input_ids,expires_at=r['request'].expires_at)


def labels(r,manifest,*,values=(0,1,0),evidence_type='SYNTHETIC',delay=30.):
    ids={};r['now'][0]+=delay;d=manifest['body']['details'];p=r['rule'].payload
    for i,row in enumerate(d['rows']):
        target=row['target_identity'];key='label:'+target['market_id'];ids[target['market_id']]=key
        r['store'].capture(key,event_id=p['event_id'],kind='LABEL',provider='TEST_ONLY',source_identity=target['token_id'],revision='test-v1',
            evidence_class='SYNTHETIC',payload=dict(context=dict(station=p['station'],city=r['context'].city_id,local_date=p['target_date'],
                target=capture.TARGET,rule_fingerprint=r['rule'].sha256),label_version='test-v1',decision_target=capture.TARGET,
                target_identity=target,knowable_at=r['now'][0],value=values[i],evidence_type=evidence_type))
    return ids


def test_pipeline_captures_all_buckets_before_economic_rejection_without_labels_or_training(factory):
    r=evaluate(factory);store=r['store'];d=r['evaluation']['body']['details']
    assert d['outcome']=='REJECT' and d['learning_capture']['status']=='EVENT_VECTOR_CAPTURED_LABELS_PENDING'
    learned=store.get(d['learning_capture']['capture_id'])['body']['details']
    assert len(learned['rows'])==3 and learned['complete_event_vector']
    assert {row['target_identity']['market_id'] for row in learned['rows']}=={b['market_id'] for b in r['rule'].payload['partition']}
    assert not store.records(kind='LABEL') and store.records(kind='COORDINATOR_EVENT')==r['coordinator_before']
    assert not learned['global_universe_coverage_verified'] and not learned['training_or_promotion_started']
    assert learned['parent_feature_contract_verified']
    assert learned['feature_schema_sha256']==kwargs(r)['pinned_bundle'].payload['bundle']['feature_schema_sha256']
    for row in learned['rows']:
        decision=store.get(row['decision_id'])['body'];feature=store.get(row['feature_id'])['body']
        assert decision['outcome']=='GATED' and not decision['explanation']['economic_qualification_evaluated']
        assert decision['explanation']['lower']==0 and decision['explanation']['upper']==1
        assert decision['feature_ready_at']==feature['available_at']<=decision['recorded_at']
        assert feature['payload']['dependencies'][0]['id']==r['request'].model_input_ids[0]
    ids=labels(r,store.get(d['learning_capture']['capture_id']))
    examples=capture.labeled_examples(store,d['learning_capture']['capture_id'],label_ids=ids,city=r['context'].city_id,horizon='DAY_AHEAD',season='AUTUMN')
    cutoff=r['now'][0]+1
    plan=DatasetPlan('fixture-plan',capture.TARGET,learned['feature_schema_sha256'],d['evaluation_started_at']-1,cutoff,cutoff+60,cutoff+120,'f'*64)
    dataset=build_dataset(examples,plan,as_of=r['now'][0])
    checked=verify_dataset(dataset)
    assert checked['counts']['TRAIN']['city_days']==1
    assert all(e.payload['selection']=='ALL_SUPPORTED_PREDICTIONS' and not e.payload['label_independently_attested'] for e in examples)


def test_late_capture_and_completed_replay_never_backdate_features_or_renew_receipts(factory):
    r=evaluate(factory);kw=kwargs(r);r['now'][0]+=1
    a=capture.capture_forecast_vector(r['store'],'secondary',**kw);d=a['body']['details']
    assert d['inference_cutoff']<r['store'].get(d['rows'][0]['feature_id'])['body']['available_at']
    pin=r['store'].pin_read_view();r['now'][0]+=100
    assert capture.capture_forecast_vector(r['store'],'secondary',**kw)==a and r['store'].pin_read_view()==pin
    with pytest.raises(EvidenceError,match='EXPIRED'):capture.capture_forecast_vector(r['store'],'late',**kw)


def test_partial_feature_commit_resumes_without_redating_prior_child(factory,monkeypatch):
    r=evaluate(factory);kw=kwargs(r);original=r['store'].decision;seen=[]
    def fail(key,**args):seen.append(args['evidence_ids'][0]);raise RuntimeError('INTERRUPTED')
    with monkeypatch.context() as patch:
        patch.setattr(r['store'],'decision',fail)
        with pytest.raises(RuntimeError):capture.capture_forecast_vector(r['store'],'partial',**kw)
    prior=r['store'].get(seen[0]);r['now'][0]+=1
    result=capture.capture_forecast_vector(r['store'],'partial',**kw)
    assert len(result['body']['details']['rows'])==3 and r['store'].get(seen[0])==prior


@pytest.mark.parametrize('fault',['members','issue','model_set','rule','bundle','city','target','conditioning',
                                'quantization','bias','member_width'])
def test_changed_source_or_target_cannot_be_exported_as_the_original_prediction(factory,fault):
    r=evaluate(factory);kw=kwargs(r);p=kw['prediction'].payload
    if fault=='members':p['model_inputs'][0]['members'][0]+=1
    if fault=='issue':p['model_inputs'][0]['issued_at']-=1
    if fault=='model_set':kw['model_input_ids']=('missing',)
    if fault=='rule':p['rule_fingerprint']='f'*64
    if fault=='bundle':p['bundle_sha256']='f'*64
    if fault=='city':kw['context']=replace(r['context'],event_id='another-event')
    if fault=='target':p['target']='NEXT_OFFICIAL_OBSERVATION'
    if fault=='conditioning':p['observed_constraint']={}
    if fault=='quantization':p['model_quantization_hypothesis']='CEILING'
    if fault=='bias':p['model_inputs'][0]['bias']=1.
    if fault=='member_width':p['model_inputs'][0]['members'].pop()
    kw['prediction']=BucketPrediction(canonical(p),digest(p));before=r['store'].pin_read_view()
    with pytest.raises(EvidenceError):capture.capture_forecast_vector(r['store'],'invalid',**kw)
    assert before==r['store'].pin_read_view()


@pytest.mark.parametrize('defect',['missing','contradictory','proxy','cross_city'])
def test_label_join_requires_all_exact_mutually_exclusive_targets(factory,defect):
    r=evaluate(factory);key=r['evaluation']['body']['details']['learning_capture']['capture_id'];manifest=r['store'].get(key)
    ids=labels(r,manifest,values=(1,1,0) if defect=='contradictory' else (0,1,0),
               evidence_type='OBSERVATION_LABEL' if defect=='proxy' else 'SYNTHETIC')
    if defect=='missing':ids.pop(next(iter(ids)))
    with pytest.raises(EvidenceError):
        capture.labeled_examples(r['store'],key,label_ids=ids,city='other' if defect=='cross_city' else r['context'].city_id,horizon='DAY_AHEAD',season='AUTUMN')


def test_capture_budget_failure_preserves_partial_evidence_and_economic_result(factory,monkeypatch):
    from polymarket_scanner.v11 import learning_capture
    with monkeypatch.context() as patch:
        patch.setattr(learning_capture,'capture_forecast_vector',lambda *a,**k:(_ for _ in ()).throw(EvidenceError('LEARNING_CAPTURE_FEATURE_BOUND')))
        r=evaluate(factory)
    d=r['evaluation']['body']['details']
    assert d['prediction'] is not None and d['outcome']=='REJECT'
    assert d['learning_capture']==dict(status='DATASET_CAPTURE_GATED',capture_id=None,reason='LEARNING_CAPTURE_FEATURE_BOUND')


def test_conditioned_pipeline_retains_separate_target_capture_before_economics(factory):
    r=evaluate(factory,'SAME_DAY_LATE_LOCK');d=r['evaluation']['body']['details']
    assert d['prediction']['observed_constraint'] is not None
    assert d['learning_capture']['status']=='CONDITIONED_VECTOR_CAPTURED_LABELS_PENDING'
    captured=r['store'].get(d['learning_capture']['capture_id'])['body']['details']
    assert captured['conditioning']['observed_constraint']==d['prediction']['observed_constraint']
    assert captured['conditioning']['remaining_coverage']==d['prediction']['remaining_coverage']
    assert captured['fit_status']=='TARGET_SPECIFIC_LEARNER_REQUIRED'
    assert len(r['store'].records(kind='DECISION'))==3


def test_undeclared_parent_schema_gates_capture_without_mutating_original_forecast(factory,bundle):
    r=evaluate(factory);kw=kwargs(r);store=bundle[0]
    values=make_artifacts();refs={key:store.put_artifact(value) for key,value in values.items()}
    key=store.put_bundle(artifacts=refs,target='FINAL_CONTRACT_PAYOUT',
                         feature_schema_sha256=values['FEATURES']['feature_schema_sha256'])
    kw['pinned_bundle']=store.pin(key);before=r['store'].pin_read_view()
    with pytest.raises(EvidenceError,match='PARENT_CONTRACT_MISMATCH'):
        capture.capture_forecast_vector(r['store'],'mismatch',**kw)
    assert r['store'].pin_read_view()==before


def test_completed_legacy_capture_replay_retains_original_version_and_timestamps(factory,monkeypatch):
    r=evaluate(factory);kw=kwargs(r)
    with monkeypatch.context() as patch:
        patch.setattr(capture,'VERSION',capture.LEGACY_VERSION)
        legacy=capture.capture_forecast_vector(r['store'],'legacy',**kw)
    before=r['store'].pin_read_view();r['now'][0]+=100
    assert capture.capture_forecast_vector(r['store'],'legacy',**kw)==legacy
    assert r['store'].pin_read_view()==before
