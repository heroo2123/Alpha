"""Fixed ECE definition and reviewed safety integration; synthetic labels only."""
from dataclasses import asdict, replace

import pytest

from polymarket_scanner.v11.drift import CalibrationDriftPolicy, DriftPolicy, measure_window
from polymarket_scanner.v11.drift_runtime import DriftPlan
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.probability import CALIBRATION_ERROR_METHOD, score_vectors
from test_v11_certification_rules import setup
from test_v11_learning_capture import bundle
from test_v11_strategy_pipeline import factory
from test_v11_drift import sample
from test_v11_drift_runtime import build, enqueue


def extended(policy, maximum=0.):
    return CalibrationDriftPolicy(**dict(asdict(policy), maximum_brier=2., maximum_log_loss=1000.),
        calibration_error_method=CALIBRATION_ERROR_METHOD, maximum_calibration_error=maximum)


def score(vectors,outcomes,events,days):
    return score_vectors(vectors,outcomes,event_ids=events,city_days=days,
        calibration_error_method=CALIBRATION_ERROR_METHOD)


def test_old_serialized_scores_and_policy_keep_frozen_identities():
    old=score_vectors([[.9,.1],[.2,.8]],[0,0],event_ids=['a','b'],city_days=['ATL:1','SEA:2'])
    assert digest(old)=='18fac6d8b42ce6f44a1a2e4c6bd443b4e0f628dd8ef8ebab80184e30f18bf1ef'
    policy=DriftPolicy('frozen-v1','FORECAST','SYNTHETIC',86400.,2,2,.5,1.)
    assert policy.sha256=='a07653b0f0a1e2d6f1a083a8fbc3265e702a8f02ee9844c4eade94ff31864a1b'
    assert 'calibration_error' not in old and 'maximum_calibration_error' not in asdict(policy)
    new=score([[.9,.1],[.2,.8]],[0,0],['a','b'],['ATL:1','SEA:2'])
    assert {k:v for k,v in new.items() if k!='calibration_error'}==old
    assert extended(policy).sha256 != policy.sha256


def test_hand_computed_calibration_error_and_repeated_snapshots_preserve_group_weight():
    a=score([[.9,.1],[.2,.8]],[0,0],['a','b'],['ATL:1','SEA:2'])
    b=score([[.9,.1]]*100+[[.2,.8]],[0]*101,['a']*100+['b'],['ATL:1']*100+['SEA:2'])
    assert a['calibration_error']['value']==pytest.approx(.45)
    assert b['calibration_error']['value']==pytest.approx(.45)
    assert a['n_city_days']==b['n_city_days']==2 and b['effective_independent_samples'] is None
    assert sum(x['weight'] for x in b['calibration_error']['bins'])==pytest.approx(1.)
    assert a['calibration_error']['confidence_interval'] is None
    assert not a['calibration_error']['independent_sample_claim']


def test_different_partition_sizes_do_not_give_larger_events_extra_weight():
    d=score([[1.,0.],[1.,0.,0.]],[1,0],['a','b'],['ATL:1','SEA:2'])['calibration_error']
    assert d['value']==pytest.approx(.5)
    assert d['bins'][0]['weight']==pytest.approx(7/12)
    assert d['bins'][-1]['weight']==pytest.approx(5/12) and d['bins'][-1]['upper_inclusive']
    assert d['bins'][1]['weight']==0 and d['bins'][1]['absolute_gap'] is None


def test_city_day_then_event_weighting_and_exact_bin_boundary():
    d=score([[1.,0.],[1.,0.],[1.,0.]],[1,0,1],['a','b','c'],['ATL:1','ATL:1','SEA:2'])
    assert d['calibration_error']['value']==pytest.approx(.75)
    bins=score([[.1,.9]],[0],['a'],['A:1'])['calibration_error']['bins']
    assert bins[0]['n_bucket_predictions']==0 and bins[1]['n_bucket_predictions']==1


@pytest.mark.parametrize('changes',[dict(maximum_calibration_error=-.1),dict(maximum_calibration_error=1.1),
    dict(maximum_calibration_error=True),dict(maximum_calibration_error=float('nan')),dict(calibration_error_method='ADAPTIVE_BINS')])
def test_policy_refuses_unreviewed_method_and_invalid_threshold(sample,changes):
    with pytest.raises(EvidenceError):replace(extended(sample['policy']),**changes)


def test_scorer_requires_known_explicit_method():
    with pytest.raises(EvidenceError,match='METHOD_UNSUPPORTED'):
        score_vectors([[.5,.5]],[0],event_ids=['a'],city_days=['A:1'],calibration_error_method='AUTO')


def test_calibration_alone_can_trigger_existing_reviewed_station_reduction(sample,monkeypatch):
    w,p,_=build(sample,monkeypatch,extended(sample['policy']));enqueue(w,sample,p)
    d=w.step('calibration')['body']['details'];m=w.store.get(d['measurement_id'])['body']['details']['result']
    assert m['threshold_breaches']==['CALIBRATION_ERROR_ABOVE_DECLARED_MAXIMUM']
    assert d['demotion_applied'] and 'SCALAR_CALIBRATION_ERROR' not in m['unsupported_metrics']
    assert not m['calibration_acceptance'] and not m['independent_label_attestation']


def test_old_policy_review_cannot_authorize_added_calibration_threshold(sample,monkeypatch):
    w,p,m=build(sample,monkeypatch,extended(sample['policy']))
    m['reviews'][0]['plan_key']=DriftPlan(sample['scope'],sample['binding'].bundle_sha256,sample['policy']).key
    enqueue(w,sample,p);d=w.step('old-review')['body']['details']
    assert d['reason']=='DRIFT_EXACT_POLICY_REVIEW_REQUIRED' and not d['demotion_applied']


def test_calibration_threshold_equality_is_not_a_breach_and_insufficiency_is_not_a_pass(sample):
    args=dict(scope=sample['scope'],account_id='account',bundle_sha256=sample['binding'].bundle_sha256,
        joins=(sample['join'],),as_of=sample['now'][0])
    policy=extended(sample['policy']);m=measure_window(sample['store'],policy=policy,**args)
    at=replace(policy,maximum_calibration_error=m['scores']['calibration_error']['value'])
    assert measure_window(sample['store'],policy=at,**args)['outcome']=='NO_DECLARED_BREACH'
    small=measure_window(sample['store'],policy=replace(policy,minimum_events=2,minimum_city_days=2),**args)
    assert small['outcome']=='INSUFFICIENT_COHORT' and small['threshold_breaches']
