from copy import deepcopy
from dataclasses import replace

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, ReleaseBinding, digest
from polymarket_scanner.v11.model_artifacts import ArtifactStore
from polymarket_scanner.v11.probability import NEXT_OBSERVATION, FINAL_EXTREME, target_identity
from polymarket_scanner.v11.pws_lead import PWSObservationLead, LeadPolicy, REPORT_VERSION
from polymarket_scanner.v11.rules import RuleGuard
from polymarket_scanner.v11.strategy_pipeline import INPUT_VERSION
from test_v11_certification_rules import setup, observe_rule
from test_v11_model_artifacts import make_artifacts
from test_v11_probability import T
from test_weather_final_gpt6_exact_replays import _event


@pytest.fixture
def rig(setup,tmp_path):
    store,registry,scope,metadata,now=setup
    now[0]=T+100
    rule=observe_rule(store,RuleGuard(store),_event(station='KATL'),metadata,'rules')
    assets=tmp_path/'assets'; assets.mkdir(mode=0o700); artifacts=ArtifactStore(assets)
    values=make_artifacts()
    for artifact in values.values():
        artifact['target']=NEXT_OBSERVATION
    values['CALIBRATION']['parameters']['probability_artifact_sha256']=digest(values['PROBABILITY'])
    refs={k:artifacts.put_artifact(v) for k,v in values.items()}
    bundle=artifacts.pin(artifacts.put_bundle(artifacts=refs,target=NEXT_OBSERVATION,
                                            feature_schema_sha256=values['FEATURES']['feature_schema_sha256']))
    payload={k:rule.payload[k] for k in ('station','target_date','unit','observation_population','source_family')}
    payload.update(version=REPORT_VERSION,rule_fingerprint=rule.sha256,source_role='EXACT_CONTRACT_OBSERVATION',
                   reported_whole_degree=69)
    official=store.capture('official',event_id=rule.payload['event_id'],kind='OFFICIAL_OBSERVATION',provider='synthetic-official',
                source_identity='KATL',revision='anchor',observed_at=now[0]-10,payload=payload,evidence_class='SYNTHETIC')
    qc={'station':'KATL','official_metadata_fingerprint':metadata.fingerprint,'health':'HEALTHY','as_of':now[0],
        'observation_age_seconds':[2.,3.],'independence_status':'SPATIAL_DEDUPLICATION_NOT_STATISTICAL_CERTIFICATION'}
    pws=store.capture('pws',event_id=rule.payload['event_id'],kind='PWS_OBSERVATION',provider='ALPHA_PWS_QC',
                 source_identity='KATL',revision='1',payload=qc,evidence_class='SYNTHETIC')
    for key, members, parents in [('with-model',[72.],(official,pws)),('without-model',[69.],(official,))]:
        value={k:rule.payload[k] for k in ('station','target_date','family','unit')}
        value.update(rule_fingerprint=rule.sha256,temperature_input=dict(version=INPUT_VERSION,model_id='model-1',
                      target_sha256=target_identity(rule,NEXT_OBSERVATION),members=members),
                     observation_context=dict(official_anchor_id='official',official_anchor_sha256=official['sha256'],
                                              horizon_seconds=120.,window_clock='FIRST_ALPHA_RECEIPT'),
                     dependencies=[{'id':x['id'],'sha256':x['sha256']} for x in parents])
        store.capture(key,event_id=rule.payload['event_id'],kind='MODEL',provider='synthetic-model',source_identity=key,
                      revision='1',issued_at=now[0]-20,payload=value,evidence_class='SYNTHETIC')
    kw=dict(rule=rule,binding=ReleaseBinding('a'*40,'b'*40,'c'*64,bundle.sha256,rule.sha256),
            policy=LeadPolicy('fixture',120.,60.,30.,120.,60.),official_id='official',pws_id='pws',
            model_ids=('with-model',),without_pws_model_ids=('without-model',),bundle=bundle,without_pws_bundle=bundle)
    return dict(store=store,now=now,rule=rule,kw=kw,report_payload=payload,qc=qc)


def observe(rig,key='lead',**changes):
    return PWSObservationLead(rig['store']).observe(key,**{**rig['kw'],**changes})['body']['details']


def report(rig,key='next',*,value=72,observed=None,**change):
    return rig['store'].capture(key,event_id=rig['rule'].payload['event_id'],kind='OFFICIAL_OBSERVATION',provider='synthetic-official',
           source_identity='KATL',revision=key,observed_at=rig['now'][0] if observed is None else observed,
           payload=dict(rig['report_payload'],reported_whole_degree=value,**change),evidence_class='SYNTHETIC')


def change_model(rig,key,update):
    original=rig['store'].get(key); payload=deepcopy(original['body']['payload']); update(payload)
    new=key+'-changed'
    rig['store'].capture(new,event_id=original['event_id'],kind='MODEL',provider=original['body']['provider'],
                       source_identity=original['body']['source_identity'],revision='2',issued_at=original['body']['issued_at'],
                       payload=payload,evidence_class='SYNTHETIC')
    return (new,)


def test_pair_predicts_next_observation_without_payout_or_price_jump_assumptions(rig):
    d=observe(rig)
    assert d['with_pws']['target']==d['without_pws']['target']==NEXT_OBSERVATION
    assert d['with_pws']['buckets']!=d['without_pws']['buckets']
    assert d['settlement_prediction'] is None and d['executable_exit_proceeds'] is None and d['proposal'] is None
    assert d['outcome']=='GATED' and d['valuation_type']=='OBSERVATION_ONLY'
    assert not d['lead_advantage_verified'] and d['calibration_status']=='UNCALIBRATED'
    assert d['paired_provenance']['with_pws']['non_pws_leaves']==d['paired_provenance']['without_pws']['non_pws_leaves']
    assert not d['financial_authority']


def test_replayed_observation_does_not_refresh_horizon_or_become_permission(rig):
    d=observe(rig); rig['now'][0]+=100
    assert observe(rig)==d
    with pytest.raises(EvidenceError,match='REQUEST_ID_COLLISION'):
        observe(rig,policy=replace(rig['kw']['policy'],horizon_seconds=121.))


def test_final_payout_inputs_cannot_be_consumed_as_next_observation(rig):
    ids=change_model(rig,'with-model',lambda p:p['temperature_input'].update(target_sha256=target_identity(rig['rule'],FINAL_EXTREME)))
    with pytest.raises(EvidenceError,match='MODEL_INPUT_SCHEMA_OR_TARGET'):
        observe(rig,model_ids=ids)


def test_anchor_and_horizon_are_part_of_model_input_identity(rig):
    ids=change_model(rig,'with-model',lambda p:p['observation_context'].update(horizon_seconds=600.))
    with pytest.raises(EvidenceError,match='ANCHOR_OR_HORIZON'):
        observe(rig,model_ids=ids)


def test_ablation_cannot_keep_pws_as_a_direct_dependency(rig):
    pws=rig['store'].get('pws')
    ids=change_model(rig,'without-model',lambda p:p['dependencies'].append({'id':'pws','sha256':pws['sha256']}))
    with pytest.raises(EvidenceError,match='PAIRED_ABLATION'):
        observe(rig,without_pws_model_ids=ids)


def test_ablation_cannot_hide_pws_behind_an_intermediate_feature(rig):
    store=rig['store']; pws=store.get('pws')
    feature=store.capture('hidden-pws',event_id=rig['rule'].payload['event_id'],kind='FEATURES',provider='fixture',
                source_identity='feature',revision='1',payload={'dependencies':[{'id':'pws','sha256':pws['sha256']}]},evidence_class='SYNTHETIC')
    ids=change_model(rig,'without-model',lambda p:p['dependencies'].append({'id':feature['id'],'sha256':feature['sha256']}))
    with pytest.raises(EvidenceError,match='PAIRED_ABLATION'):
        observe(rig,without_pws_model_ids=ids)


def test_paired_models_cannot_use_different_non_pws_weather_evidence(rig):
    store=rig['store']
    extra=store.capture('extra',event_id=rig['rule'].payload['event_id'],kind='MODEL',provider='fixture',source_identity='raw-model',
                        revision='1',payload={},evidence_class='SYNTHETIC')
    ids=change_model(rig,'with-model',lambda p:p['dependencies'].append({'id':extra['id'],'sha256':extra['sha256']}))
    with pytest.raises(EvidenceError,match='PAIRED_ABLATION'):
        observe(rig,model_ids=ids)


def test_model_inputs_cannot_claim_unarchived_or_tampered_feature_lineage(rig):
    ids=change_model(rig,'with-model',lambda p:p['dependencies'][0].update(sha256='f'*64))
    with pytest.raises(EvidenceError,match='DERIVATION_BINDING'):
        observe(rig,model_ids=ids)


def test_current_official_arrival_invalidates_preconfirmation_anchor(rig):
    report(rig)
    with pytest.raises(EvidenceError,match='CURRENT_OFFICIAL'):
        observe(rig)


def test_new_pws_receipt_requires_new_qc_and_model_inputs(rig):
    rig['store'].capture('new-raw-pws',event_id=rig['rule'].payload['event_id'],kind='PWS_OBSERVATION',provider='raw',
                        source_identity='KATL',revision='new',payload={},evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='CURRENT_OFFICIAL_AND_PWS'):
        observe(rig)


def test_stale_sensors_are_not_refreshed_by_a_recent_neighborhood_timestamp(rig):
    rig['now'][0]+=28
    with pytest.raises(EvidenceError,match='SENSOR_AGE_STALE'):
        observe(rig)


def test_model_source_supersession_is_not_hidden_by_a_fresh_old_run(rig):
    change_model(rig,'with-model',lambda p:None)
    with pytest.raises(EvidenceError,match='REVISION_SUPERSEDED'):
        observe(rig)


def test_official_arrival_racing_prediction_append_is_rejected_atomically(rig,monkeypatch):
    store=rig['store']; original=store.audit
    def race(record_id,**kw):
        if record_id=='lead':
            report(rig)
        return original(record_id,**kw)
    monkeypatch.setattr(store,'audit',race)
    with pytest.raises(EvidenceError,match='AUDIT_GUARDED_STATE_CHANGED'):
        observe(rig)


def test_first_received_report_scores_observation_pair_but_not_settlement_or_true_lead(rig):
    observe(rig); rig['now'][0]+=10; report(rig,value=72)
    d=PWSObservationLead(rig['store']).score_first_received_report('score',observation_id='lead')['body']['details']
    assert d['status']=='MEASURED_FIRST_RECEIVED_REPORT'
    assert d['with_pws']['brier']<d['without_pws']['brier'] and d['brier_improvement']>0
    assert d['receipt_lead_seconds']==10 and d['provider_published_at'] is None
    assert d['settlement_label'] is None and d['executable_markout'] is None and d['trading_pnl'] is None
    assert not d['target_is_true_next_published_report'] and not d['collection_continuity_verified']
    assert not d['lead_advantage_verified'] and d['comparison_partition']=='DEVELOPMENT'
    assert d['label_evidence_class']=='SYNTHETIC'


def test_anchored_correction_invalidates_thesis_but_is_not_next_observation_label(rig):
    observe(rig); rig['now'][0]+=1
    report(rig,'correction',value=68,observed=rig['store'].get('official')['body']['observed_at'])
    rig['now'][0]+=1; report(rig,'new-reading',value=70)
    d=PWSObservationLead(rig['store']).score_first_received_report('score',observation_id='lead')['body']['details']
    assert d['first_official_update']=='correction' and d['official_id']=='new-reading'


def test_late_report_outside_declared_horizon_remains_unknown(rig):
    observe(rig); rig['now'][0]+=121; report(rig)
    d=PWSObservationLead(rig['store']).score_first_received_report('score',observation_id='lead')['body']['details']
    assert d['status']=='UNKNOWN' and d['settlement_label'] is None


def test_proxy_report_cannot_be_used_as_exact_source_observation_label(rig):
    observe(rig); rig['now'][0]+=10; report(rig,source_role='METAR_PROXY')
    d=PWSObservationLead(rig['store']).score_first_received_report('score',observation_id='lead')['body']['details']
    assert d['status']=='UNKNOWN'


def test_bad_pws_prediction_is_not_preserved_as_beneficial_by_definition(rig):
    observe(rig); rig['now'][0]+=10; report(rig,value=69)
    d=PWSObservationLead(rig['store']).score_first_received_report('score',observation_id='lead')['body']['details']
    assert d['brier_improvement']<0 and d['log_loss_improvement']<0
    assert d['lead_advantage_verified'] is False
