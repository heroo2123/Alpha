import copy
from dataclasses import asdict
import json
from pathlib import Path

import pytest

from polymarket_scanner.v11.datasets import FeatureDefinition, FeatureSchema
from polymarket_scanner.v11.evidence import EvidenceError, canonical, digest
from polymarket_scanner.v11.model_artifacts import (
    ARTIFACT_VERSION, ArtifactStore, PinnedBundle, parse_data, predict_with_bundle, validate_artifact,
)
from test_v11_probability import rule, component, T


def provenance():
    return {'code_commit':'a'*40,'code_tree':'b'*40,'config_sha256':'c'*64,'dataset_sha256':None,
            'causal_watermark':None,'dependency_sha256':'d'*64,'runtime_sha256':'e'*64,'seed':23,
            'created_at':100.,'parent_bundle_sha256':None,'run_id':'initial-test','model_family':'KERNEL',
            'model_version':'test-v1','training_metrics_sha256':None,'holdout_metrics_sha256':None,
            'comparison_policy_sha256':'f'*64,'training_status':'INITIAL_NO_FIT'}


def make_artifacts():
    schema=FeatureSchema('test-v1',(FeatureDefinition('temperature','C','weather',-80,65,False),))
    params={
        'FEATURES':json.loads(canonical(asdict(schema))),
        'PROBABILITY':{'family':'GAUSSIAN_MEMBER_MIXTURE','models':[{'model_id':'model-1',
            'dependence_group':'NCEP','bias':0.,'kernel_sigma':1.,'within_group_weight':1.}],
            'group_weights':{'NCEP':1.}},
        'EXECUTION_COST':{'method':'NO_EMPIRICAL_EXECUTION_MODEL','additional_cost_per_share':None,'evidence_class':'UNKNOWN'},
        'STRATEGY_QUALITY':{'method':'FIXED_REDUCTION_ONLY','modifiers':{'a'*64:1.}},
    }
    values={kind:{'version':ARTIFACT_VERSION,'kind':kind,'target':'FINAL_CONTRACT_PAYOUT',
                  'feature_schema_sha256':schema.sha256,'parameters':p,'provenance':provenance()} for kind,p in params.items()}
    values['CALIBRATION']={'version':ARTIFACT_VERSION,'kind':'CALIBRATION','target':'FINAL_CONTRACT_PAYOUT',
        'feature_schema_sha256':schema.sha256,'parameters':{'method':'VACUOUS_BOUNDS','status':'UNCALIBRATED',
        'probability_artifact_sha256':digest(values['PROBABILITY'])},'provenance':provenance()}
    return values


@pytest.fixture
def bundle(tmp_path):
    tmp_path.chmod(0o700)
    store=ArtifactStore(tmp_path)
    values=make_artifacts()
    refs={kind:store.put_artifact(value) for kind,value in values.items()}
    key=store.put_bundle(artifacts=refs,target='FINAL_CONTRACT_PAYOUT',
                         feature_schema_sha256=values['FEATURES']['feature_schema_sha256'])
    return store,values,refs,key


def test_data_only_bundle_is_deterministic_private_and_immutable(bundle):
    store,values,refs,key=bundle
    assert store.put_artifact(values['PROBABILITY'])==refs['PROBABILITY']
    assert all(p.stat().st_mode & 0o777 == 0o400 for p in store.root.iterdir())
    pinned=store.pin(key)
    assert pinned.sha256==key
    changed=pinned.payload
    changed['components']['PROBABILITY']['parameters']['models'][0]['bias']=2
    assert pinned.payload['components']['PROBABILITY']['parameters']['models'][0]['bias']==0
    assert not pinned.payload['bundle']['financial_authority']


@pytest.mark.parametrize('field,value',[('loader','pickle.loads'),('python_code','print(1)'),
                                      ('wallet','x'),('max_loss',100),('settlement_source','PWS')])
def test_artifact_cannot_supply_code_authority_or_semantics(field,value):
    artifact=make_artifacts()['PROBABILITY']
    artifact['parameters'][field]=value
    with pytest.raises(EvidenceError,match='PROBABILITY_ARTIFACT_SCHEMA'):
        validate_artifact(artifact)


@pytest.mark.parametrize('raw',[b'{"x":1,"x":2}',b'{"x":NaN}',b'not json',b'[]',b'{"api_key":"never"}'])
def test_unsafe_or_ambiguous_json_rejected(raw):
    with pytest.raises(EvidenceError): parse_data(raw)


def test_fitted_flag_does_not_create_calibration():
    artifact=make_artifacts()['CALIBRATION']
    artifact['parameters']['status']='CALIBRATED'
    with pytest.raises(EvidenceError,match='CALIBRATION_EVIDENCE_NOT_IMPLEMENTED'):
        validate_artifact(artifact)


def test_strategy_quality_cannot_increase_protected_size():
    artifact=make_artifacts()['STRATEGY_QUALITY']
    artifact['parameters']['modifiers']['a'*64]=1.01
    with pytest.raises(EvidenceError,match='CANNOT_INCREASE'):
        validate_artifact(artifact)


def test_empirical_fill_model_cannot_be_invented_without_supported_evidence():
    artifact=make_artifacts()['EXECUTION_COST']
    artifact['parameters']['additional_cost_per_share']=.01
    with pytest.raises(EvidenceError,match='EMPIRICAL_EXECUTION_EVIDENCE_REQUIRED'):
        validate_artifact(artifact)


def test_incomplete_or_future_model_provenance_refused():
    artifact=make_artifacts()['PROBABILITY']
    del artifact['provenance']['code_tree']
    with pytest.raises(EvidenceError,match='MODEL_PROVENANCE_SCHEMA'):
        validate_artifact(artifact)
    artifact=make_artifacts()['PROBABILITY']
    artifact['provenance'].update(training_status='FITTED_NOT_CALIBRATED',dataset_sha256='1'*64,
        training_metrics_sha256='2'*64,causal_watermark=101.)
    with pytest.raises(EvidenceError,match='LOOKAHEAD'):
        validate_artifact(artifact)


def test_mixed_feature_schema_and_calibration_probability_refused(bundle):
    store,values,refs,key=bundle
    changed=copy.deepcopy(values['PROBABILITY'])
    changed['parameters']['models'][0]['bias']=1.
    refs2={**refs,'PROBABILITY':store.put_artifact(changed)}
    with pytest.raises(EvidenceError,match='CALIBRATION_PROBABILITY_BINDING'):
        store.put_bundle(artifacts=refs2,target='FINAL_CONTRACT_PAYOUT',feature_schema_sha256=values['FEATURES']['feature_schema_sha256'])
    changed['feature_schema_sha256']='1'*64
    refs2['PROBABILITY']=store.put_artifact(changed)
    with pytest.raises(EvidenceError,match='BUNDLE_COMPONENT_MISMATCH'):
        store.put_bundle(artifacts=refs2,target='FINAL_CONTRACT_PAYOUT',feature_schema_sha256=values['FEATURES']['feature_schema_sha256'])


def test_modified_bytes_and_symlinks_are_refused(bundle,tmp_path):
    store,_,refs,key=bundle
    path=store.root/(refs['PROBABILITY']+'.json')
    path.chmod(0o600)
    path.write_bytes(b'{}')
    with pytest.raises(EvidenceError,match='HASH_OR_CANONICAL'):
        store.pin(key)
    link=store.root/('0'*64+'.json')
    link.symlink_to(path)
    with pytest.raises(OSError): store.read('0'*64)


def test_forged_pinned_component_is_not_hidden_by_valid_bundle_hash(bundle):
    store,_,_,key=bundle
    p=store.pin(key).payload
    p['components']['PROBABILITY']['parameters']['models'][0]['bias']=1.
    with pytest.raises(EvidenceError,match='PINNED_COMPONENT_INTEGRITY'):
        PinnedBundle(canonical(p),key).payload


def test_prediction_uses_frozen_bundle_parameters_not_callers_fit(bundle):
    store,_,_,key=bundle
    r=rule()
    c=component(r,bias=20.)
    p=predict_with_bundle(store.pin(key),r,(c,),as_of=T+110,max_source_age_seconds=120)
    assert p.payload['bundle_sha256']==key
    assert p.payload['model_inputs'][0]['bias']==0
    assert p.payload['calibration_status']=='UNCALIBRATED'
    with pytest.raises(EvidenceError,match='INPUT_SET_MISMATCH'):
        predict_with_bundle(store.pin(key),r,(component(r,model='other'),),as_of=T+110,max_source_age_seconds=120)
