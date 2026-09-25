from copy import deepcopy
from dataclasses import replace

import pytest

from polymarket_scanner.v11 import causal_replay as replay, model_registry, pws_lead
from polymarket_scanner.v11.evidence import EvidenceError, canonical, digest
from polymarket_scanner.v11.performance import PerformanceLab
from polymarket_scanner.v11.probability import NEXT_OBSERVATION, FINAL_EXTREME
from polymarket_scanner.v11.pws_admission import PWSPreconfirmation
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_model_governance import authority, promote, review
from test_v11_strategy_pipeline import factory
from test_v11_pws_admission import joined, coordinator, evaluate, arrival
from test_v11_causal_replay import audit_job
from test_v11_audit_reports import finish


def run(r, key='entry', **kw):
    return PerformanceLab(coordinator(r)).replay_temperature(key,policy=replay.ReplayPolicy('pws-replay'),**kw)


def revised_bundle(objects, pinned):
    data=pinned.payload; values=data['components']; values['PROBABILITY']['parameters']['models'][0]['bias']=.5
    refs={k:objects.put_artifact(v) for k,v in values.items() if k!='CALIBRATION'}
    values['CALIBRATION']['parameters']['probability_artifact_sha256']=refs['PROBABILITY']
    refs['CALIBRATION']=objects.put_artifact(values['CALIBRATION'])
    key=objects.put_bundle(artifacts=refs,target=data['bundle']['target'],feature_schema_sha256=data['bundle']['feature_schema_sha256'])
    return objects,values,refs,key


def test_pws_pair_and_separate_payout_recompute_with_original_account_context(joined):
    r=joined;c=coordinator(r);head=c.recover('original-account');original=evaluate(r)
    assert original['outcome']=='REJECT' and not original['proposal']
    before=r['store'].pin_read_view();r['now'][0]+=3600;result=run(r)
    assert result['status']=='ECONOMICS_REPRODUCED',result
    pair=result['pws_observation'];lead=r['store'].get('lead')['body']['details']
    assert pair['recomputed_with_pws']==lead['with_pws'] and pair['recomputed_without_pws']==lead['without_pws']
    assert pair['recomputed_with_pws']['target']==NEXT_OBSERVATION
    assert result['recomputed_prediction']['target']==FINAL_EXTREME
    assert result['model']['bundle_sha256']!=pair['observation_model']['bundle_sha256']
    assert canonical(result['recomputed_valuation'])==canonical(original['valuation'])
    assert result['account']['snapshot_ref']['id']==head['id'] and result['account']['recorded_risk_matches']
    assert not pair['observation_is_payout_or_exit'] and not pair['lead_advantage_verified']
    assert not result['full_control_flow_replayed'] and not result['financial_authority']
    assert r['store'].pin_read_view()==before and not c._state(c._head())['intents']


@pytest.mark.parametrize('which',['observation_scope','payout_scope'])
def test_both_original_models_survive_later_promotion_demotion_and_rollback(joined,bundle,which):
    r=joined;evaluate(r);before=run(r);scope=r[which].key;objects=bundle[0]
    state=r['states'][scope];old_key=state['active_bundle_sha256'];old=objects.pin(old_key)
    original=(objects,old.payload['components'],old.payload['bundle']['artifacts'],old_key)
    newer=revised_bundle(objects,old);second=promote(newer,state,review_id='later')
    r['states'][scope]=second;assert run(r)==before
    reduced=authority.transition(second,action='DEMOTE',expected_state_sha256=digest(second),now=21.,reason='SYNTHETIC',size_multiplier=.25)
    r['states'][scope]=reduced;assert run(r)==before
    rolled=authority.transition(reduced,action='ROLLBACK',expected_state_sha256=digest(reduced),now=22.,reason='SYNTHETIC',
        review=review(original,reduced,action='ROLLBACK',review_id='rollback'),requested_bundle=old_key)
    r['states'][scope]=rolled;assert run(r)==before
    assert model_registry.ActiveModelRegistry().pin(scope_key=scope,mode='V11_PAPER').require_manual_review


def test_later_official_label_and_same_clock_model_revisions_cannot_enter_original_pair(joined):
    r=joined;evaluate(r);before=run(r)
    for key in ('lead-model','ablation-model'):
        row=r['store'].get(key);b=row['body'];p=deepcopy(b['payload']);p['temperature_input']['members']=[99.]
        r['store'].capture(key+'-later',event_id=row['event_id'],kind='MODEL',provider=b['provider'],
            source_identity=b['source_identity'],revision='later',issued_at=b['issued_at'],
            observed_at=r['now'][0]-100,payload=p,evidence_class='SYNTHETIC')
    r['now'][0]+=10;arrival(r)
    score=pws_lead.PWSObservationLead(r['store']).score_first_received_report('later-label',observation_id='lead')['body']['details']
    assert score['status']=='MEASURED_FIRST_RECEIVED_REPORT'
    result=run(r);assert result==before
    pair=result['pws_observation'];assert not pair['later_official_reports_used'] and not pair['label_or_outcome_replayed']
    assert pair['input_boundary_seq']<r['store'].get('lead')['seq']<result['input_boundary_seq']


def test_exact_distinct_research_ablation_is_required_without_promoting_it(joined,bundle,monkeypatch):
    r=joined;objects=bundle[0];ablation=revised_bundle(objects,r['lead_kw']['bundle'])
    pws_lead.PWSObservationLead(r['store']).observe('distinct-lead',**dict(r['lead_kw'],without_pws_bundle=objects.pin(ablation[3])))
    PWSPreconfirmation(r['store']).pin('distinct-pair',**dict(r['pin_kw'],lead_id='distinct-lead'))
    evaluate(r,preconfirmation_id='distinct-pair');d=run(r)
    assert d['status']=='ECONOMICS_REPRODUCED',d
    pair=d['pws_observation'];assert pair['without_pws_bundle_sha256']==ablation[3]
    assert pair['without_pws_bundle_sha256']!=pair['observation_model']['bundle_sha256']
    assert pair['without_pws_bundle_role']=='RESEARCH_ABLATION_NOT_CHAMPION_APPROVAL'
    original=objects.pin
    def missing(key):
        if key==ablation[3]:raise EvidenceError('ORIGINAL_RESEARCH_ABLATION_UNAVAILABLE')
        return original(key)
    monkeypatch.setattr(objects,'pin',missing);missing_result=run(r)
    assert missing_result['status']=='GATED' and missing_result['reason']=='ORIGINAL_RESEARCH_ABLATION_UNAVAILABLE'
    assert 'pws_observation' not in missing_result and not missing_result['economic_match']


@pytest.mark.parametrize('which',['observation_scope','payout_scope'])
def test_missing_history_in_either_target_gates_the_whole_replay(joined,monkeypatch,which):
    r=joined;evaluate(r);original=model_registry.protected_state
    def missing(*,scope_key,mode):
        if scope_key==r[which].key:raise EvidenceError('ORIGINAL_SCOPE_HISTORY_UNAVAILABLE')
        return original(scope_key=scope_key,mode=mode)
    monkeypatch.setattr(model_registry,'protected_state',missing);d=run(r)
    assert d['status']=='GATED' and not d['economic_match'] and not d['comparisons']
    assert 'pws_observation' not in d and d['reason']=='ORIGINAL_SCOPE_HISTORY_UNAVAILABLE'


@pytest.mark.parametrize('missing',['pws-raw','ablation-model','observation-pin','paired-pin'])
def test_original_raw_pair_or_pin_missing_is_not_filled_from_current_data(joined,monkeypatch,missing):
    r=joined;evaluate(r);original=replay.HistoricalView.get
    def get(self,key):
        if key==missing:raise EvidenceError('ORIGINAL_PWS_INPUT_UNAVAILABLE')
        return original(self,key)
    monkeypatch.setattr(replay.HistoricalView,'get',get);d=run(r)
    assert d['status']=='GATED' and d['reason']=='ORIGINAL_PWS_INPUT_UNAVAILABLE' and not d['economic_match']


def test_changed_observation_calculation_is_mismatch_even_when_payout_still_matches(joined,monkeypatch):
    r=joined;evaluate(r);original=pws_lead.paired_inference
    def changed(*args,**kw):
        result=original(*args,**kw);result['without_pws']['buckets'][0]['point']+=.01
        return result
    monkeypatch.setattr(pws_lead,'paired_inference',changed);d=run(r)
    assert d['status']=='MISMATCH' and d['comparisons']['prediction'] and d['comparisons']['valuation']
    assert d['comparisons']['pws_with_pws'] and not d['comparisons']['pws_without_pws']
    assert not d['economic_match'] and not d['financial_authority']


def test_pws_model_cannot_be_replayed_after_an_early_source_control_gate(joined):
    r=joined;arrival(r);assert evaluate(r)['outcome']=='GATED'
    d=run(r);assert d['status']=='GATED' and not d['economic_match']


def test_candidate_pws_lane_reaches_scheduled_audit_with_both_original_targets(joined,monkeypatch):
    from polymarket_scanner.v11 import candidate_assembly as app
    from polymarket_scanner.v11.request_assembly import SourceSelector
    from test_v11_request_assembly import inputs,target,state_for
    from test_v11_candidate_assembly import scoped_plan,evaluate_built_lane
    r=joined;c=coordinator(r);c.recover('account');state_for(r,('book2',),('model2','anchor','pws'))
    lane=app.PWSLeadLane('pws',inputs(r,r['payout_kw']),(target(r),),'fixture',r['request'].valuation_policy,10.,
        inputs(r,r['observation_kw']),(SourceSelector('MODEL','fixture','ablation-model',120.),),r['lead_kw']['policy'])
    result=evaluate_built_lane(r,scoped_plan(r,lane),monkeypatch);assert result.result_ids and not result.proposals
    d=finish(audit_job(r,records_per_step=256))['body']['details'];audit=d['economic_replay']
    assert audit['status']=='ECONOMICS_REPRODUCED' and audit['retained_decision_count']==1,audit
    row=audit['rows'][0];assert row['decision_ref']['id']==result.result_ids[0]
    assert row['pws_observation']['observation_model']['bundle_sha256']!=row['model']['bundle_sha256']
    assert row['pws_observation']['comparisons']['without_pws'] and not row['pws_observation']['later_official_reports_used']
    assert not d['acceptance_granted'] and not audit['full_control_flow_replayed'] and not r['store'].records(kind='TRADE')


def test_pws_audit_restart_keeps_original_model_history_and_published_report(joined,monkeypatch):
    from polymarket_scanner.v11.audit_reports import AuditWorker
    r=joined;evaluate(r);worker=audit_job(r,records_per_step=256);store=r['store'];save=worker._save
    def interrupt(head,state,**kw):
        if kw.get('outcome')=='AUDIT_COMPLETE':raise OSError('SYNTHETIC_REPORT_BEFORE_CURSOR')
        return save(head,state,**kw)
    monkeypatch.setattr(worker,'_save',interrupt)
    # The existing report recovery protocol is shared by temperature and PWS.
    with pytest.raises(OSError,match='SYNTHETIC_REPORT_BEFORE_CURSOR'):finish(worker)
    published=store.latest(kind='RUNTIME_STATUS',event_id='v11-audit-report:DAILY')
    assert published and published['body']['details']['economic_replay']['economic_matches']==1
    def denied(**kw):raise EvidenceError('HISTORY_NOW_UNAVAILABLE')
    monkeypatch.setattr(model_registry,'protected_state',denied)
    recovered=finish(AuditWorker(coordinator(r),worker.policy))
    assert recovered['id']==published['id'] and recovered['sha256']==published['sha256']
