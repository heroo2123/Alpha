from copy import deepcopy
from dataclasses import replace

import pytest

from polymarket_scanner.v11 import causal_replay as replay, model_registry
from polymarket_scanner.v11.evidence import EvidenceError, canonical, digest
from polymarket_scanner.v11.learning_sources import learning_source_view
from polymarket_scanner.v11.performance import PerformanceLab
from polymarket_scanner.v11.strategy_pipeline import TemperatureStrategies
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_model_governance import authority, new_bundle, promote, review
from test_v11_strategy_pipeline import factory, evaluate
from test_v11_pws_admission import coordinator


def run(r, key='evaluation', **kw):
    return PerformanceLab(coordinator(r)).replay_temperature(key, policy=replay.ReplayPolicy('fixture'), **kw)


@pytest.mark.parametrize('strategy,family', [('FUTURE_FORECAST','high'),('SAME_DAY_LATE_LOCK','high'),('SAME_DAY_LATE_LOCK','low')])
def test_original_prediction_and_valuation_recompute_read_only(factory,strategy,family):
    r=factory(strategy,family=family);original=evaluate(r)
    before=r['store'].pin_read_view();r['now'][0]+=1000;d=run(r)
    assert d['status']=='ECONOMICS_REPRODUCED',d
    assert d['recomputed_prediction']==original['prediction'] and canonical(d['recomputed_valuation'])==canonical(original['valuation'])
    assert d['account']['snapshot_ref'] is None and d['account']['risk_at_decision']['cash']=='10'
    assert not d['full_control_flow_replayed'] and not d['historical_executable_attested']
    assert r['store'].pin_read_view()==before and not d['new_economic_commands'] and not d['admission_authority']


def test_later_revisions_and_same_clock_receipts_cannot_change_original_inputs(factory):
    r=factory();evaluate(r);first=run(r);b=r['store'].get('model2')['body']
    payload=deepcopy(b['payload']);payload['temperature_input']['members']=[90.,91.,92.]
    later=r['store'].capture('later',event_id=r['context'].event_id,kind='MODEL',provider='fixture',
        source_identity='model-1',revision='late',observed_at=r['now'][0]-100,issued_at=r['now'][0]-30,
        payload=payload,evidence_class='SYNTHETIC')
    d=run(r);assert d==first
    with learning_source_view(r['store']) as v:
        h=replay.HistoricalView(v,through_seq=d['input_boundary_seq'],at=d['cutoff'])
        assert h.latest_source(kind='MODEL',event_id=r['context'].event_id,provider='fixture',source_identity='model-1')['id']=='model2'
        with pytest.raises(EvidenceError,match='NONCAUSAL'):h.get(later['id'])


def test_original_model_survives_promotion_demotion_and_rollback(factory,bundle):
    r=factory();evaluate(r);first=run(r);old=r['model_state'][0]
    newer=new_bundle(bundle);second=promote(newer,old,review_id='second')
    r['model_state'][0]=second
    assert run(r)==first and model_registry.ActiveModelRegistry().pin(scope_key=r['scope'].key,mode='V11_PAPER').bundle.sha256==newer[3]
    reduced=authority.transition(second,action='DEMOTE',expected_state_sha256=digest(second),now=21.,reason='SYNTHETIC',size_multiplier=.5)
    r['model_state'][0]=reduced
    assert run(r)==first
    # Historical replay cannot change current overlay or renew the old pin.
    assert model_registry.ActiveModelRegistry().pin(scope_key=r['scope'].key,mode='V11_PAPER').require_manual_review
    rolled=authority.transition(reduced,action='ROLLBACK',expected_state_sha256=digest(reduced),now=22.,reason='SYNTHETIC',
        review=review(bundle,reduced,action='ROLLBACK',review_id='rollback'),requested_bundle=bundle[3])
    r['model_state'][0]=rolled
    assert run(r)==first and r['model_state'][0]['overlay']['require_manual_review']


def test_history_or_bundle_unavailable_is_a_visible_gate(factory,monkeypatch):
    r=factory();evaluate(r)
    def denied(**kw):raise EvidenceError('PROTECTED_MODEL_STATE_UNAVAILABLE')
    monkeypatch.setattr(model_registry,'protected_state',denied)
    d=run(r);assert d['status']=='GATED' and d['reason']=='PROTECTED_MODEL_STATE_UNAVAILABLE' and not d['economic_match']


@pytest.mark.parametrize('damage',['previous_state_sha256','previous_event_sha256','review_sha256','active_bundle_sha256'])
def test_corrupt_retained_model_history_never_becomes_a_replay_match(factory,damage):
    r=factory();evaluate(r);r['model_state'][0]['events'][0][damage]='f'*64
    d=run(r);assert d['status']=='GATED' and not d['economic_match'] and 'recomputed_prediction' not in d


def test_changed_numeric_implementation_reports_mismatch_without_authorizing(factory,monkeypatch):
    r=factory();evaluate(r);original=replay.settlement_entry_details
    def changed(*a,**kw):return dict(original(*a,**kw),conservative_ev_total='999')
    monkeypatch.setattr(replay,'settlement_entry_details',changed)
    d=run(r);assert d['status']=='MISMATCH' and d['comparisons']['prediction'] and not d['comparisons']['valuation']
    assert not d['financial_authority'] and not d['economic_match']


def test_early_gate_and_budget_are_not_reported_as_success(factory):
    r=factory('FUTURE_FORECAST',offset=0);evaluate(r)
    d=run(r);assert d['status']=='GATED' and d['reason']=='REPLAY_EARLY_CONTROL_GATE_NOT_RECOMPUTED'
    calls=[0]
    def clock():calls[0]+=1;return float(calls[0])
    d=run(r,monotonic=clock);assert d['status']=='GATED' and not d['comparisons'] and d['account'] is None


def test_candidate_assembled_decision_replays_with_common_account_context(factory,monkeypatch):
    from polymarket_scanner.v11 import candidate_assembly as app
    from test_v11_candidate_assembly import scoped_plan, evaluate_built_lane
    from test_v11_request_assembly import inputs,target
    r=factory();c=coordinator(r);c.recover('original-account')
    lane=app.TemperatureLane('temperature',inputs(r),(target(r),),'fixture',r['request'].valuation_policy,10.)
    result=evaluate_built_lane(r,scoped_plan(r,lane),monkeypatch)
    key=result.result_ids[0];old=c._head();before=r['store'].pin_read_view();d=run(r,key)
    assert d['status']=='ECONOMICS_REPRODUCED',d
    assert d['account']['snapshot_ref']['id']==old['id'] and d['account']['recorded_risk_matches']
    assert d['account']['risk_at_decision']==c._risk(c._state(old)) and not result.proposals
    c.recover('later-account');again=run(r,key)
    assert again==d and c._head()['id']=='later-account'


def test_original_account_risk_mismatch_gates_the_whole_result(factory):
    r=factory();c=coordinator(r);head=c.recover('account')
    details=deepcopy(head['body']['details']);details['risk']['cash']='999'
    r['store'].audit('bad-account',event_id=head['event_id'],kind='COORDINATOR_EVENT',details=details)
    evaluate(r);d=run(r)
    assert d['status']=='GATED' and d['reason']=='REPLAY_ACCOUNT_RISK_MISMATCH'
    assert d['account'] is None and not d['comparisons'] and 'recomputed_prediction' not in d


def test_account_policy_cannot_be_substituted_for_historical_context(factory):
    r=factory();c=coordinator(r);c.recover('account');evaluate(r)
    c.policy=replace(c.policy,retained_cash='1')
    c=type(c)(r['store'],policy=c.policy,correlation=c.correlation,limits=c.limits)
    d=PerformanceLab(c).replay_temperature('evaluation',policy=replay.ReplayPolicy('fixture'))
    assert d['status']=='GATED' and d['reason']=='REPLAY_ACCOUNT_POLICY_OR_BOUND'


def test_unavailable_old_artifact_does_not_fall_back_to_current_bundle(factory,bundle,monkeypatch):
    r=factory();evaluate(r);newer=new_bundle(bundle);r['model_state'][0]=promote(newer,r['model_state'][0],review_id='later')
    reader=bundle[0];original=reader.pin
    def missing(key):
        if key==bundle[3]:raise EvidenceError('ORIGINAL_NUMERIC_ARTIFACT_UNAVAILABLE')
        return original(key)
    monkeypatch.setattr(reader,'pin',missing)
    d=run(r);assert d['status']=='GATED' and d['reason']=='ORIGINAL_NUMERIC_ARTIFACT_UNAVAILABLE'


def test_interrupted_evaluation_keeps_original_valuation_timestamp(factory,monkeypatch):
    r=factory();store=r['store'];audit=store.audit
    def interrupt(key,**kw):
        if key=='evaluation':raise OSError('SYNTHETIC_INTERRUPTION')
        return audit(key,**kw)
    monkeypatch.setattr(store,'audit',interrupt)
    with pytest.raises(OSError,match='SYNTHETIC_INTERRUPTION'):evaluate(r)
    at=store.get('evaluation:valuation')['body']['recorded_at'];r['now'][0]+=1
    monkeypatch.setattr(store,'audit',audit);evaluate(r);r['now'][0]+=1000
    d=run(r);assert d['status']=='ECONOMICS_REPRODUCED' and d['valuation_at']==at


def test_unsupported_pws_join_is_honest_and_never_relabelled_temperature(factory):
    r=factory();evaluate(r);store=r['store'];original=store.get('evaluation')
    details=deepcopy(original['body']['details']);details['strategy']='PWS_OBSERVATION_LEAD'
    store.audit('unsupported',event_id=original['event_id'],kind='MEASUREMENT',details=details)
    d=run(r,'unsupported')
    assert d['status']=='GATED' and d['reason']=='REPLAY_STRATEGY_JOIN_NOT_IMPLEMENTED' and not d['economic_match']
