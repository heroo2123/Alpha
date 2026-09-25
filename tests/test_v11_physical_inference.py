from copy import deepcopy
from dataclasses import asdict,replace

import pytest

from polymarket_scanner.v11 import physical_inference as physical
from polymarket_scanner.v11.evidence import EvidenceError,ReleaseBinding,digest
from polymarket_scanner.v11.gefs_sources import current_path_heads
from polymarket_scanner.v11.model_artifacts import ArtifactStore,predict_with_bundle,validate_artifact
from polymarket_scanner.v11.nowcast_features import archive_nowcast_features,PHYSICAL_SCHEMA
from polymarket_scanner.v11.probability import NEXT_OBSERVATION,UNRESOLVED_EXTREME,FINAL_EXTREME,target_identity
from polymarket_scanner.v11.pws_lead import PWSObservationLead
from polymarket_scanner.v11.pws_quality import archive_neighborhood
from polymarket_scanner.v11.strategy_pipeline import _model_inputs,INPUT_VERSION
from polymarket_scanner.v11.weather_sources import madis_request,normalize_weather_capture
from test_v11_certification_rules import setup
from test_v11_model_artifacts import provenance,bundle
from test_v11_pws_lead import rig as lead_rig
from test_v11_nowcast_features import report
from test_v11_pws_quality import policy as qc_policy
from test_v11_pws_runtime import xml
from test_v11_pws_admission import joined
from test_v11_strategy_pipeline import factory


def parameters(*,name='model-1',coefficient=2.):
    return dict(family=physical.FAMILY,models=[dict(model_id=name,dependence_group='NCEP',bias=0.,kernel_sigma=1.,
        within_group_weight=1.,missing_kernel_sigma=3.,feature_terms=[
            dict(feature='pws_weighted_median_c',center=19.,scale=1.,coefficient=coefficient),
            dict(feature='wind_speed_mps',center=0.,scale=1.,coefficient=.1)])],group_weights={'NCEP':1.})


def inputs(r,metadata,*,official_key,base_key):
    s=r['store'];now=r['now'][0];rule=r['rule'];event=rule.payload['event_id']
    s.capture('aux-awc',event_id=event,kind='OFFICIAL_OBSERVATION',provider='NOAA_AWC',source_identity='KATL',revision='1',
        observed_at=now-10,payload=dict(response=[dict(icaoId='KATL',obsTime=now-10,temp=20.,rawOb=report(at=now-10))]),
        evidence_class='SYNTHETIC')
    req=madis_request(event_id=event,station=metadata.station,latitude=metadata.latitude,longitude=metadata.longitude)
    response=xml(now)
    for old,new in [('33.01',metadata.latitude+.01),('33.03',metadata.latitude+.03),('33.05',metadata.latitude+.05)]:
        response=response.replace('lat="'+old+'"','lat="'+str(new)+'"')
    response=response.replace('lon="-84"','lon="'+str(metadata.longitude)+'"')
    raw=s.capture('aux-madis',event_id=event,kind='PWS_OBSERVATION',provider=req.provider,source_identity=req.source_identity,
        revision='1',payload=dict(response=response,request_params=dict(req.params)),evidence_class='SYNTHETIC')
    normalized=normalize_weather_capture(s,raw['id'],record_id='aux-normalized',station=metadata.station)
    qc=archive_neighborhood(s,'aux-qc',event_id=event,capture_ids=(normalized['id'],),official=metadata,policy=qc_policy())
    assert qc['body']['payload']['health']=='HEALTHY'
    old=s.get(official_key);b=old['body']
    anchor=s.capture('aux-anchor',event_id=event,kind='OFFICIAL_OBSERVATION',provider=b['provider'],source_identity=b['source_identity'],
        revision='aux-anchor',observed_at=now-1,payload=b['payload'],evidence_class='SYNTHETIC')
    base=s.get(base_key);p=deepcopy(base['body']['payload'])
    p['observation_context'].update(official_anchor_id=anchor['id'],official_anchor_sha256=anchor['sha256'])
    p['dependencies']=[dict(id=anchor['id'],sha256=anchor['sha256'])]
    baseline=s.capture('aux-base',event_id=event,kind='MODEL',provider='fixture-persistence',source_identity='next-base',revision='1',
        issued_at=now-20,payload=p,evidence_class='SYNTHETIC')
    features=[];models=[]
    for key,ablated in [('with',frozenset()),('without',frozenset({'PWS'}))]:
        f=archive_nowcast_features(s,'physical-'+key,event_id=event,official=metadata,awc_capture_ids=('aux-awc',),
            max_observation_age_seconds=300.,trajectory_window_seconds=3600.,maximum_gap_seconds=900.,
            pws_capture_id=qc['id'],ablated_families=ablated)
        models.append(physical.archive_physical_model(s,'physical-model-'+key,rule=rule,base_model_id=baseline['id'],
                      feature_id=f['id'],input_target=NEXT_OBSERVATION)['model'])
        features.append(f)
    return dict(anchor=anchor,qc=qc,base=baseline,features=features,models=models)


def make_bundle(root,rule,width=1,*,target=NEXT_OBSERVATION,version='test-v1',params=None):
    root.mkdir(mode=0o700);store=ArtifactStore(root)
    contract=physical.PhysicalFeatureContract((('model-1',width),),rule.payload['unit'],rule.payload['family'],target)
    p=provenance();p['model_version']=version
    key=physical.build_initial_physical_bundle(store,contract=contract,probability_parameters=params or parameters(),
        provenance=p,quality_modifiers={})
    return store.pin(key),store


@pytest.fixture
def rig(lead_rig,setup,tmp_path):
    r=lead_rig;r.update(inputs(r,setup[3],official_key='official',base_key='without-model'))
    r['pinned'],r['artifacts']=make_bundle(tmp_path/'physical-artifacts',r['rule'])
    return r


def predict(r,which=0,*,pinned=None):
    c=_model_inputs(r['store'],r['rule'],(r['models'][which]['id'],),r['now'][0],target=NEXT_OBSERVATION)
    return predict_with_bundle(pinned or r['pinned'],r['rule'],c,as_of=r['now'][0],max_source_age_seconds=300.).payload


def observe(r,key='physical-lead'):
    return PWSObservationLead(r['store']).observe(key,rule=r['rule'],binding=replace(r['kw']['binding'],bundle_sha256=r['pinned'].sha256),
        policy=replace(r['kw']['policy'],max_pws_age_seconds=300.),official_id=r['anchor']['id'],pws_id=r['qc']['id'],
        model_ids=(r['models'][0]['id'],),without_pws_model_ids=(r['models'][1]['id'],),bundle=r['pinned'],without_pws_bundle=r['pinned'])['body']['details']


def test_raw_madis_qc_and_awc_feed_frozen_paired_observation_inference(rig):
    r=rig;d=observe(r);a=d['with_pws'];b=d['without_pws']
    assert a['target']==b['target']==NEXT_OBSERVATION and a['buckets']!=b['buckets']
    assert a['model_inputs'][0]['bias']-b['model_inputs'][0]['bias']==pytest.approx(2.)
    assert a['model_inputs'][0]['kernel_sigma']==1 and b['model_inputs'][0]['kernel_sigma']==3
    assert a['model_inputs'][0]['auxiliary']['evidence_sha256']==r['features'][0]['sha256']
    assert d['paired_provenance']['without_pws']['pws_ids']==[]
    assert d['paired_provenance']['with_pws']['pws_ids']==[r['qc']['id']]
    assert d['paired_provenance']['with_pws']['non_pws_leaves']==d['paired_provenance']['without_pws']['non_pws_leaves']
    assert all(b['lower']==0 and b['upper']==1 for b in a['buckets'])
    assert d['settlement_prediction'] is None and d['executable_exit_proceeds'] is None and d['proposal'] is None
    assert not d['lead_advantage_verified'] and not d['financial_authority']


def test_pws_ablation_removes_data_dependency_and_preserves_other_feature_values(rig):
    with_p,without=(f['body']['payload'] for f in rig['features'])
    for f in PHYSICAL_SCHEMA.features:
        assert without['values'][f.name] is None if f.family=='PWS' else without['values'][f.name]==with_p['values'][f.name]
    assert rig['qc']['id'] not in [ref['id'] for ref in without['dependencies']]
    assert current_path_heads(rig['store'],rig['models'][0])
    assert current_path_heads(rig['store'],rig['models'][1])


def test_only_frozen_coefficients_control_adjustment_and_artifacts_stay_immutable(rig,tmp_path):
    r=rig;before=r['pinned'].payload;pinned,_=make_bundle(tmp_path/'different-artifacts',r['rule'],params=parameters(coefficient=0.))
    assert predict(r)['model_inputs'][0]['bias']-predict(r,pinned=pinned)['model_inputs'][0]['bias']==pytest.approx(2.)
    assert r['pinned'].payload==before
    assert not list(r['artifacts'].root.glob('*active*'))


@pytest.mark.parametrize('fault',['coefficient','scale','feature','missing_sigma','unchanged_missing_sigma','duplicate','code'])
def test_feature_parameters_cannot_escape_numeric_schema_or_narrow_missing_uncertainty(fault):
    from test_v11_model_artifacts import make_artifacts
    artifact=make_artifacts()['PROBABILITY'];artifact['parameters']=parameters();m=artifact['parameters']['models'][0]
    if fault=='coefficient':m['feature_terms'][0]['coefficient']=31
    if fault=='scale':m['feature_terms'][0]['scale']=0
    if fault=='feature':m['feature_terms'][0]['feature']='unreviewed_feature'
    if fault=='missing_sigma':m['missing_kernel_sigma']=.5
    if fault=='unchanged_missing_sigma':m['missing_kernel_sigma']=1.
    if fault=='duplicate':m['feature_terms'].append(m['feature_terms'][0])
    if fault=='code':m['feature_terms'][0]['python']='unexpected'
    with pytest.raises(EvidenceError):validate_artifact(artifact)


def test_physical_input_cannot_use_unmodified_legacy_bundle_or_other_units(rig,tmp_path):
    r=rig
    with pytest.raises(EvidenceError,match='DECLARED_PHYSICAL_MODEL'):predict(r,pinned=r['kw']['bundle'])
    contract=physical.PhysicalFeatureContract((('model-1',1),),'C',r['rule'].payload['family'],NEXT_OBSERVATION)
    key=physical.build_initial_physical_bundle(r['artifacts'],contract=contract,probability_parameters=parameters(),provenance=provenance(),quality_modifiers={})
    with pytest.raises(EvidenceError,match='CONTRACT_MISMATCH'):predict(r,pinned=r['artifacts'].pin(key))


def test_feature_value_beyond_effective_bias_bound_gates_without_clipping(rig,tmp_path):
    pinned,_=make_bundle(tmp_path/'wide-coefficient',rig['rule'],params=parameters(coefficient=30.))
    with pytest.raises(EvidenceError,match='ADJUSTMENT_OUTSIDE_PROTECTED_BOUND'):predict(rig,pinned=pinned)


def test_current_feature_receipt_or_metadata_revision_requires_recomputation(rig):
    r=rig;s=r['store'];old=s.get('aux-awc');b=old['body']
    s.capture('new-awc',event_id=old['event_id'],kind='OFFICIAL_OBSERVATION',provider=b['provider'],source_identity=b['source_identity'],
        revision='2',payload=b['payload'],evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='SOURCE_SUPERSEDED'):current_path_heads(s,r['models'][0])


def test_cross_event_pws_metadata_drift_is_inherited_by_physical_model_guard(rig):
    r=rig;s=r['store'];key=r['qc']['body']['payload']['metadata_heads'][0][0]
    s.audit('drift',event_id='pws:'+key,kind='REGISTRY',details=dict(quarantined=True))
    with pytest.raises(EvidenceError,match='METADATA_CHANGED'):current_path_heads(s,r['models'][0])
    assert current_path_heads(s,r['models'][1])  # No PWS-derived branch remains in the ablation.


def test_feature_expiry_cannot_be_refreshed_by_model_replay(rig):
    r=rig;s=r['store'];before=s.pin_read_view();r['now'][0]=r['features'][0]['body']['payload']['context']['valid_until']
    replay=physical.archive_physical_model(s,r['models'][0]['id'],rule=r['rule'],base_model_id=r['base']['id'],
        feature_id=r['features'][0]['id'],input_target=NEXT_OBSERVATION)
    assert replay['model']==r['models'][0] and s.pin_read_view()==before
    with pytest.raises(EvidenceError,match='CONTEXT_OR_FRESHNESS'):predict(r)


def test_new_model_append_is_atomic_against_source_arrival(rig,monkeypatch):
    r=rig;s=r['store'];original=s._append
    def race(key,*args,**kw):
        if key=='racing-model':
            s.capture('racing-awc',event_id=r['rule'].payload['event_id'],kind='OFFICIAL_OBSERVATION',provider='fixture',
                source_identity='arrived',revision='2',payload={},evidence_class='SYNTHETIC')
        return original(key,*args,**kw)
    monkeypatch.setattr(s,'_append',race)
    with pytest.raises(EvidenceError,match='GUARDED_STATE_CHANGED'):
        physical.archive_physical_model(s,'racing-model',rule=r['rule'],base_model_id=r['base']['id'],
            feature_id=r['features'][0]['id'],input_target=NEXT_OBSERVATION)


def test_changed_feature_value_cannot_be_substituted_under_same_model_id(rig):
    r=rig
    with pytest.raises(EvidenceError,match='REPLAY_CONFLICT'):
        physical.archive_physical_model(r['store'],r['models'][0]['id'],rule=r['rule'],base_model_id=r['base']['id'],
            feature_id=r['features'][1]['id'],input_target=NEXT_OBSERVATION)


@pytest.mark.parametrize('fault',['members','hash','metadata','future','missing_values','unknown_context'])
def test_archived_derived_input_requires_exact_causal_values_and_metadata(rig,fault):
    r=rig;s=r['store'];original=r['models'][0];b=original['body'];p=deepcopy(b['payload'])
    if fault=='members':p['temperature_input']['members'][0]+=1
    elif fault=='hash':p['dependencies'][1]['sha256']='f'*64
    else:
        f=r['features'][0];payload=deepcopy(f['body']['payload'])
        if fault=='metadata':payload['context']['metadata_fingerprint']='f'*64
        if fault=='future':payload['context']['as_of']=r['now'][0]+1
        if fault=='missing_values':payload['values'].pop('wind_speed_mps')
        if fault=='unknown_context':payload.pop('context')
        new=s.capture('changed-features',event_id=r['rule'].payload['event_id'],kind='FEATURES',provider='fixture',
            source_identity='changed',revision='2',payload=payload,evidence_class='SYNTHETIC')
        p['dependencies'][1]=dict(id=new['id'],sha256=new['sha256'])
    s.capture('changed-model',event_id=r['rule'].payload['event_id'],kind='MODEL',provider=b['provider'],
        source_identity=b['source_identity'],revision='2',issued_at=b['issued_at'],payload=p,evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError):_model_inputs(s,r['rule'],('changed-model',),r['now'][0],target=NEXT_OBSERVATION)


def test_absent_pws_keeps_causal_official_features_and_uses_wider_model_fallback(rig,setup):
    r=rig;s=r['store'];event=r['rule'].payload['event_id']
    missing=archive_neighborhood(s,'pws-outage',event_id=event,capture_ids=(),official=setup[3],policy=qc_policy())
    feature=archive_nowcast_features(s,'outage-features',event_id=event,official=setup[3],awc_capture_ids=('aux-awc',),
        max_observation_age_seconds=300.,trajectory_window_seconds=3600.,maximum_gap_seconds=900.,pws_capture_id=missing['id'])
    row=physical.archive_physical_model(s,'outage-model',rule=r['rule'],base_model_id=r['base']['id'],feature_id=feature['id'],input_target=NEXT_OBSERVATION)
    c=_model_inputs(s,r['rule'],(row['model']['id'],),r['now'][0],target=NEXT_OBSERVATION)
    p=predict_with_bundle(r['pinned'],r['rule'],c,as_of=r['now'][0],max_source_age_seconds=300.).payload
    assert feature['body']['payload']['values']['official_proxy_temperature_c']==20.
    assert feature['body']['payload']['values']['pws_weighted_median_c'] is None
    assert p['model_inputs'][0]['kernel_sigma']==3. and p['calibration_status']=='UNCALIBRATED'


def test_legacy_prediction_bytes_have_no_new_null_feature_field(lead_rig):
    r=lead_rig;c=_model_inputs(r['store'],r['rule'],('without-model',),r['now'][0],target=NEXT_OBSERVATION)
    p=predict_with_bundle(r['kw']['bundle'],r['rule'],c,as_of=r['now'][0],max_source_age_seconds=120.).payload
    assert 'auxiliary' not in p['model_inputs'][0]


def test_physical_pws_pair_reaches_protected_separate_payout_and_shared_economics(joined,setup,tmp_path,monkeypatch):
    from polymarket_scanner.v11 import certification,model_registry
    from polymarket_scanner.v11.strategy_admission import StrategyAdmission,SourceLease
    from polymarket_scanner.v11.strategy_pipeline import TemperatureStrategies
    from polymarket_scanner.v11.pws_admission import PWSPreconfirmation
    from test_v11_certification_rules import approve_fixture
    from test_v11_model_governance import promote
    r=joined;s=r['store'];r.update(inputs(r,setup[3],official_key='anchor',base_key='ablation-model'))
    previous=s.get('payout-coverage')['body']['payload'];cp=deepcopy(previous)
    cp.update(observation_id=r['anchor']['id'],observation_sha256=r['anchor']['sha256'],as_of=r['now'][0])
    c=s.capture('aux-base-coverage',event_id=r['rule'].payload['event_id'],kind='FEATURES',provider='fixture',source_identity='coverage',
        revision='3',payload=cp,evidence_class='SYNTHETIC')
    pair=physical.archive_physical_model(s,'physical-payout',rule=r['rule'],base_model_id='model2',feature_id=r['features'][0]['id'],
        input_target=UNRESOLVED_EXTREME,coverage_id=c['id'])
    observation,obs_store=make_bundle(tmp_path/'protected-obs',r['rule'],version='lead-v1')
    payout,pay_store=make_bundle(tmp_path/'protected-pay',r['rule'],width=3,target=UNRESOLVED_EXTREME)
    class Reader:
        def pin(self,key):return observation if key==observation.sha256 else payout if key==payout.sha256 else None
    reviews=[]
    for prefix,scope,pinned,store in [('aux-obs:',r['observation_scope'],observation,obs_store),('aux-pay:',r['payout_scope'],payout,pay_store)]:
        review=approve_fixture(monkeypatch,(s,setup[1],scope,setup[3],r['now']),stage='PAPER',fingerprint=r['rule'].sha256,prefix=prefix)
        reviews+=review['reviews'];value=pinned.payload
        r['states'][scope.key]=promote((store,value['components'],value['bundle']['artifacts'],pinned.sha256),r['states'][scope.key],
                                     review_id=prefix+'physical-review')
    monkeypatch.setattr(certification,'protected_reviews',lambda:dict(reviews=reviews))
    monkeypatch.setattr(model_registry,'ApprovedArtifactReader',Reader)
    shared=(SourceLease(r['anchor']['id'],'OFFICIAL',120.),SourceLease(r['qc']['id'],'PWS',300.))
    obs_kw=dict(r['observation_kw'],binding=replace(r['observation_kw']['binding'],bundle_sha256=observation.sha256),
        source_leases=(SourceLease(r['models'][0]['id'],'MODEL',120.),*shared))
    pay_kw=dict(r['payout_kw'],binding=replace(r['binding'],bundle_sha256=payout.sha256),source_leases=(
        SourceLease(pair['model']['id'],'MODEL',120.),SourceLease(pair['coverage']['id'],'FEATURES',120.),*shared))
    StrategyAdmission(s).pin('aux-observation-pin',**obs_kw);StrategyAdmission(s).pin('aux-payout-pin',**pay_kw)
    lead=PWSObservationLead(s).observe('aux-lead',rule=r['rule'],binding=obs_kw['binding'],policy=replace(r['lead_kw']['policy'],max_pws_age_seconds=300.),
        official_id=r['anchor']['id'],pws_id=r['qc']['id'],model_ids=(r['models'][0]['id'],),without_pws_model_ids=(r['models'][1]['id'],),
        bundle=observation,without_pws_bundle=observation)
    confirmation=PWSPreconfirmation(s).pin('aux-confirmation',lead_id=lead['id'],observation_admission_id='aux-observation-pin',payout_admission_id='aux-payout-pin')
    # Event risk must bind the same payout release; no old bundle is substituted.
    from polymarket_scanner.v11.event_risk import EventRiskEngine
    from test_v11_event_risk import policy,metrics
    event=EventRiskEngine(s).step('physical-risk',context=r['context'],policy=policy(),binding=pay_kw['binding'],metrics=metrics(r['now'][0]),
        book_ids=(r['request'].book_id,),source_ids=(pair['model']['id'],r['anchor']['id']))
    request=replace(r['request'],admission_id='aux-payout-pin',event_state_id=event['id'],model_input_ids=(pair['model']['id'],),
        observed_input_id=r['anchor']['id'],coverage_input_id=pair['coverage']['id'],preconfirmation_id=confirmation['id'])
    result=TemperatureStrategies(s).evaluate('physical-entry',request)['body']['details']
    assert result['prediction']['target']==FINAL_EXTREME and result['prediction']['observed_constraint'] is not None
    assert result['reason']=='CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD' and result['proposal'] is None
    assert result['executable_exit_value'] is None and not result['financial_authority']
