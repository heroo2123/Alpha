from dataclasses import replace
from datetime import datetime, timezone
import math
import random

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.probability import (
    FINAL_EXTREME, NEXT_OBSERVATION, UNRESOLVED_EXTREME, ForecastComponent,
    ObservedConstraint, RemainingPathCoverage, ProbabilityInterval, predict_buckets, score_vectors, target_identity,
)
from polymarket_scanner.v11.rules import fingerprint_event
from test_weather_final_gpt6_exact_replays import _event


T=datetime(2026,9,14,12,tzinfo=timezone.utc).timestamp()

def rule(family='high', unit='F', labels=None):
    return fingerprint_event(_event(station='KATL', family=family, unit=unit, labels=labels),
                             station_timezone='America/New_York', metadata_fingerprint='a'*64)


def component(r, *, target=FINAL_EXTREME, model='model-1', group='NCEP', members=(70., 71.), **changes):
    result = ForecastComponent(model, group, target_identity(r, target), members,
                               0., 1., 1., 'b'*64, T+100., T+101., T+90.)
    return replace(result, **changes)


def predict(r, components=None, **kw):
    if kw.get('observed') is not None and 'remaining_coverage' not in kw:
        kw['remaining_coverage']=coverage(r)
    return predict_buckets(r, components or (component(r),), group_weights=kw.pop('group_weights', (('NCEP', 1.),)),
                           as_of=T+110., bundle_sha256='c'*64, max_source_age_seconds=120., **kw)


def coverage(r):
    return RemainingPathCoverage(r.sha256,T+110.,((T-8*3600,T+110.),),
                                 ((T+110.,T+16*3600),),('b'*64,),'e'*64)

def observed(r, value):
    p=r.payload
    return ObservedConstraint(r.sha256, p['station'], p['observation_population'], p['unit'], p['family'],
                              value, T+100., T+101., 'revision-1', 'd'*64)


def test_coherent_vectors_and_vacuous_bounds_are_not_normalized():
    r=rule()
    result=predict(r).payload
    assert sum(b['point'] for b in result['buckets']) == pytest.approx(1)
    assert sum(b['lower'] for b in result['buckets']) == 0
    assert sum(b['upper'] for b in result['buckets']) == 3
    assert result['calibration_status']=='UNCALIBRATED'
    assert result['executable_exit_value'] is None
    assert not result['settlement_authority'] and not result['financial_authority']


def test_no_conservative_probability_is_one_minus_yes_upper():
    p=ProbabilityInterval(.7,.5,.9).for_side('NO')
    assert p.point == pytest.approx(.3)
    assert p.lower == pytest.approx(.1)
    assert p.upper == pytest.approx(.5)


@pytest.mark.parametrize('args',[(.5,.6,.9),(.7,.2,.6),(.5,-.1,.9),(.5,.1,1.1),(math.nan,0,1)])
def test_invalid_probability_intervals_refused(args):
    with pytest.raises(EvidenceError): ProbabilityInterval(*args)


def test_normal_cdf_bucket_values_and_negative_lattice_boundaries():
    r=rule(unit='C',labels=['-2°C or lower','-1-0°C','1°C or higher'])
    c=component(r,members=(-.5,),kernel_sigma=1.)
    result=predict(r,(c,)).payload
    vals=[b['point'] for b in result['buckets']]
    assert vals == pytest.approx([.15865525393145707,.6826894921370859,.15865525393145707])


def test_member_replication_does_not_increase_confidence_or_model_weight():
    r=rule()
    c=component(r)
    a=predict(r,(c,)).payload
    b=predict(r,(replace(c,members=c.members*100),)).payload
    assert a['buckets']==b['buckets']
    assert b['independent_sample_count'] is None
    assert b['member_count_is_effective_sample_size'] is False


def test_related_models_share_group_budget_instead_of_extra_votes():
    r=rule()
    cold=component(r,members=(65.,))
    warm=component(r,model='independent',group='OTHER',members=(80.,))
    a=predict(r,(cold,warm),group_weights=(('NCEP',.5),('OTHER',.5))).payload
    b=predict(r,(cold,replace(cold,model_id='related-copy'),warm),group_weights=(('NCEP',.5),('OTHER',.5))).payload
    assert a['buckets']==b['buckets']
    assert b['model_weights']=={'model-1':.25,'related-copy':.25,'independent':.5}


@pytest.mark.parametrize('family,value,impossible', [('high',72, [0,1]),('low',69,[1,2])])
def test_exact_observed_extreme_truncates_remaining_path(family,value,impossible):
    r=rule(family)
    c=component(r,target=UNRESOLVED_EXTREME)
    p=predict(r,(c,),observed=observed(r,value)).payload
    assert all(p['buckets'][i]['point']==0 for i in impossible)
    assert p['revision_risk']=='CONDITIONAL_ON_ACCEPTED_REVISION_UNMODELED'
    # No certainty is fabricated by ignoring possible official revisions/fallback.
    assert all(b['lower']==0 and b['upper']==1 for b in p['buckets'])


def test_whole_day_model_cannot_be_mislabeled_as_remaining_path():
    r=rule()
    with pytest.raises(EvidenceError,match='MODEL_TARGET_MISMATCH'):
        predict(r,observed=observed(r,70))


@pytest.mark.parametrize('field,value',[('source_role','PWS_OBSERVATION'),('source_role','METAR_PROXY'),
                                      ('station','KSFO'),('unit','C'),('population','OTHER'),
                                      ('feature_ready_at',T+111.),('whole_degree_value',70.5)])
def test_proxy_wrong_identity_or_late_constraint_refused(field,value):
    r=rule()
    c=component(r,target=UNRESOLVED_EXTREME)
    with pytest.raises(EvidenceError):
        predict(r,(c,),observed=replace(observed(r,70),**{field:value}))


def test_next_observation_probability_cannot_be_consumed_as_payout_or_exit():
    r=rule()
    p=predict(r,(component(r,target=NEXT_OBSERVATION),),target=NEXT_OBSERVATION)
    for target in (FINAL_EXTREME,'EXECUTABLE_EARLY_EXIT'):
        with pytest.raises(EvidenceError,match='OBSERVATION_IS_NOT_SETTLEMENT_OR_EXIT'):
            p.binary('m1','YES',required_target=target)
    assert p.binary('m1','YES',required_target=NEXT_OBSERVATION).upper==1


@pytest.mark.parametrize('changes',[{'feature_ready_at':T+111.},{'issued_at':T}])
def test_future_or_stale_model_refused(changes):
    r=rule()
    c=component(r,**changes)
    # The run at time zero is too old only under this explicit 100-second policy.
    with pytest.raises(EvidenceError):
        predict_buckets(r,(c,),group_weights=(('NCEP',1.),),as_of=T+110.,bundle_sha256='c'*64,
                        max_source_age_seconds=100.)


def test_unknown_run_time_remains_unknown_not_receipt_time():
    r=rule()
    p=predict(r,(component(r,issued_at=None),)).payload
    assert p['model_run_age_known'] is False
    assert p['model_inputs'][0]['issued_at'] is None


def test_changed_rule_or_wrong_event_requires_recomputation():
    r=rule()
    c=component(r)
    changed=rule(unit='C')
    with pytest.raises(EvidenceError,match='MODEL_TARGET_MISMATCH'): predict(changed,(c,))


def test_bounded_random_mixtures_obey_probability_invariants():
    rng=random.Random(23)
    r=rule()
    for _ in range(100):
        values=tuple(rng.uniform(-50,120) for _ in range(rng.randint(1,40)))
        c=component(r,members=values,kernel_sigma=rng.uniform(.01,30))
        p=predict(r,(c,))
        rows=p.payload['buckets']
        assert sum(b['point'] for b in rows)==pytest.approx(1,abs=1e-12)
        assert all(0<=b['point']<=1 for b in rows)
        for b in rows:
            y=p.binary(b['market_id'],'YES',required_target=FINAL_EXTREME)
            n=p.binary(b['market_id'],'NO',required_target=FINAL_EXTREME)
            assert y.point+n.point==pytest.approx(1)


def test_group_weighted_scoring_does_not_reward_duplicate_outcomes():
    a=score_vectors([[.9,.1],[.2,.8]],[0,0],event_ids=['a','b'],city_days=['ATL:1','SEA:2'])
    b=score_vectors([[.9,.1]]*100+[[.2,.8]],[0]*101,event_ids=['a']*100+['b'],
                    city_days=['ATL:1']*100+['SEA:2'])
    assert a['brier']==pytest.approx(b['brier'])
    assert a['log_loss']==pytest.approx(b['log_loss'])
    assert b['n_events']==2 and b['n_city_days']==2
    assert b['effective_independent_samples'] is None


def test_infinite_log_loss_is_not_hidden_by_clipping():
    p=score_vectors([[0.,1.]],[0],event_ids=['a'],city_days=['ATL:1'])
    assert p['log_loss_infinite'] and p['log_loss'] is None
    assert p['calibration_status']=='SCORED_NOT_CALIBRATED'


def test_event_cannot_claim_two_independent_city_days():
    with pytest.raises(EvidenceError,match='EVENT_CITY_DAY_CONFLICT'):
        score_vectors([[.5,.5]]*2,[0,0],event_ids=['same']*2,city_days=['ATL:1','ATL:2'])


def test_missing_elapsed_gap_is_not_silently_dropped_from_remaining_model():
    r=rule()
    c=component(r,target=UNRESOLVED_EXTREME)
    missing=replace(coverage(r),accepted_intervals=((T-8*3600,T),))
    with pytest.raises(EvidenceError,match='UNRESOLVED_DAY_GAP'):
        predict(r,(c,),observed=observed(r,70),remaining_coverage=missing)
    complete=replace(missing,unresolved_intervals=((T,T+110.),(T+110.,T+16*3600)))
    assert predict(r,(c,),observed=observed(r,70),remaining_coverage=complete).payload['fair_probability_sum']==1


def test_same_event_label_revision_must_not_be_mixed_in_scoring():
    with pytest.raises(EvidenceError,match='EVENT_OUTCOME_CONFLICT'):
        score_vectors([[.5,.5]]*2,[0,1],event_ids=['same']*2,city_days=['ATL:1']*2)
