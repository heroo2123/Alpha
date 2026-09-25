import pytest

from polymarket_scanner.v11 import causal_replay as replay, source_release
from polymarket_scanner.v11.evidence import EvidenceError, canonical
from polymarket_scanner.v11.performance import PerformanceLab
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_model_governance import new_bundle, promote
from test_v11_strategy_pipeline import factory
from test_v11_source_release import release_factory, pin, evaluate
from test_v11_pws_admission import coordinator
from test_v11_causal_replay import audit_job
from test_v11_audit_reports import finish


def run(r,key='evaluation'):
    return PerformanceLab(coordinator(r)).replay_temperature(key,policy=replay.ReplayPolicy('release-replay'))


@pytest.mark.parametrize('strategy',['SOURCE_SHOCK','RELEASE_OPPORTUNITY'])
@pytest.mark.parametrize('revision',[False,True])
def test_original_received_change_and_payout_replay_without_event_or_finality_authority(release_factory,strategy,revision):
    r=release_factory(strategy,revision=revision);c=coordinator(r);c.recover('original-account');pin(r);original=evaluate(r)
    assert original['outcome']=='REJECT' and not original['proposal']
    before=r['store'].pin_read_view();r['now'][0]+=3600;d=run(r)
    assert d['status']=='ECONOMICS_REPRODUCED',d
    released=d['source_release'];assert released['recomputed']['change_type']==('OFFICIAL_REVISION' if revision else 'NEW_OFFICIAL_OBSERVATION')
    assert released['recomputed']['reported_temperature_change']==1
    assert d['recomputed_prediction']==original['prediction'] and canonical(d['recomputed_valuation'])==canonical(original['valuation'])
    assert d['account']['recorded_risk_matches'] and released['recomputed']['schedule']['proves_actual_release'] is False
    assert not released['event_guard_replayed'] and not released['directional_permission_reissued'] and not released['settlement_finality']
    assert r['store'].pin_read_view()==before and not d['financial_authority'] and not d['new_economic_commands']


def test_new_champion_does_not_replace_original_release_model(release_factory,bundle):
    r=release_factory();pin(r);evaluate(r);original=run(r)
    r['model_state'][0]=promote(new_bundle(bundle),r['model_state'][0],review_id='later')
    assert run(r)==original


def test_later_reports_cannot_overflow_original_predecessor_scan(release_factory):
    r=release_factory();pin(r);evaluate(r);original=run(r);row=r['store'].get('received-release');b=row['body']
    # More than the receipt-pair scan limit arrive after the original decision.
    # A live records() read would gate the old pair instead of reproducing it.
    for i in range(1000):
        r['store'].capture('late-'+str(i),event_id=row['event_id'],kind=row['kind'],provider=b['provider'],
            source_identity=b['source_identity'],revision='late-'+str(i),observed_at=b['observed_at'],
            payload=b['payload'],evidence_class='SYNTHETIC')
    assert run(r)==original and not original['source_release']['later_reports_used']


@pytest.mark.parametrize('missing',['previous-exact','received-release','post-release-book','schedule','release-event','release-pin'])
def test_original_release_evidence_cannot_be_filled_by_a_later_record(release_factory,monkeypatch,missing):
    r=release_factory();pin(r);evaluate(r);original=replay.HistoricalView.get
    def get(self,key):
        if key==missing:raise EvidenceError('ORIGINAL_RELEASE_INPUT_UNAVAILABLE')
        return original(self,key)
    monkeypatch.setattr(replay.HistoricalView,'get',get);d=run(r)
    assert d['status']=='GATED' and d['reason']=='ORIGINAL_RELEASE_INPUT_UNAVAILABLE' and not d['economic_match']
    assert 'source_release' not in d and not d['comparisons']


def test_changed_received_change_calculation_is_mismatch_without_favorable_payout_override(release_factory,monkeypatch):
    r=release_factory();pin(r);evaluate(r)
    monkeypatch.setattr(source_release,'received_change_type',lambda *args:'OFFICIAL_REVISION')
    d=run(r)
    assert d['status']=='MISMATCH' and d['comparisons']['prediction'] and d['comparisons']['valuation']
    assert not d['comparisons']['release_change_type'] and not d['economic_match'] and not d['financial_authority']


def test_expired_received_release_control_gate_is_still_visible(release_factory):
    r=release_factory();pin(r);r['now'][0]+=200;assert evaluate(r)['outcome']=='GATED'
    assert run(r)['status']=='GATED'


@pytest.mark.parametrize('strategy',['SOURCE_SHOCK','RELEASE_OPPORTUNITY'])
def test_assembled_reaction_lane_reaches_candidate_audit_with_original_receipt_pair(release_factory,monkeypatch,strategy):
    from polymarket_scanner.v11 import candidate_assembly as app
    from polymarket_scanner.v11.strategy_admission import SourceLease
    from test_v11_request_assembly import inputs,target
    from test_v11_candidate_assembly import scoped_plan,evaluate_built_lane
    r=release_factory(strategy);coordinator(r).recover('account')
    kw=dict(r['admission_kw'],scope=r['scope'],source_leases=(SourceLease('recomputed-model','MODEL',120.),
        SourceLease('received-release','OFFICIAL',120.),SourceLease('release-coverage','FEATURES',120.)))
    lane=app.SourceReleaseLane('release',inputs(r,kw),(target(r),),'fixture',r['request'].valuation_policy,10.)
    result=evaluate_built_lane(r,scoped_plan(r,lane),monkeypatch);assert result.result_ids and not result.proposals
    report=finish(audit_job(r,records_per_step=256))['body']['details'];audit=report['economic_replay']
    assert audit['status']=='ECONOMICS_REPRODUCED' and audit['retained_decision_count']==1,audit
    row=audit['rows'][0];assert row['decision_ref']['id']==result.result_ids[0]
    assert row['source_release']['previous_official_ref']['id']=='previous-exact'
    assert row['source_release']['current_official_ref']['id']=='received-release'
    assert row['source_release']['comparisons']['schedule'] and not row['source_release']['settlement_finality']
    assert not audit['full_control_flow_replayed'] and not report['acceptance_granted'] and not r['store'].records(kind='TRADE')
