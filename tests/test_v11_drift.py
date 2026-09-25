"""Synthetic declared policies; not operational threshold or calibration evidence."""
from dataclasses import replace
from math import log

import pytest

from polymarket_scanner.v11.drift import DriftPolicy, measure_window
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.forecast_learning import ForecastLabelJoin
from polymarket_scanner.v11.learning_capture import capture_forecast_vector
from polymarket_scanner.v11.strategy_pipeline import TemperatureStrategies
from test_v11_certification_rules import setup
from test_v11_learning_capture import bundle, labels, kwargs
from test_v11_strategy_pipeline import factory


@pytest.fixture
def sample(factory, request):
    strategy=getattr(request,'param','FUTURE_FORECAST')
    r=factory(strategy); row=TemperatureStrategies(r['store']).evaluate('forecast',r['request']); r['evaluation']=row
    r['capture']=r['store'].get(row['body']['details']['learning_capture']['capture_id'])
    r['labels']=labels(r,r['capture'])
    r['join']=ForecastLabelJoin(r['capture']['id'],tuple(r['labels'].items()),r['context'].city_id,
        r['scope'].horizon,r['scope'].season)
    r['policy']=DriftPolicy('SYNTHETIC_ONLY','FORECAST' if strategy=='FUTURE_FORECAST' else 'CONDITIONED_PAYOUT',
        'SYNTHETIC',86400.,1,1,0.,0.)
    return r


def measure(r, **kw):
    return measure_window(r['store'],**dict(scope=r['scope'],account_id='account',bundle_sha256=r['binding'].bundle_sha256,
        policy=r['policy'],joins=(r['join'],),as_of=r['now'][0],**kw))


@pytest.mark.parametrize('sample',['FUTURE_FORECAST','SAME_DAY_LATE_LOCK'],indirect=True)
def test_original_capture_and_exact_labels_produce_grouped_quality_without_authority(sample):
    r=sample; before=r['store'].pin_read_view(); result=measure(r); scores=result['scores']
    p=r['evaluation']['body']['details']['prediction']; vector=[b['point'] for b in p['buckets']]
    assert scores['brier']==pytest.approx(sum((p-int(i==1))**2 for i,p in enumerate(vector)))
    if vector[1]==0: assert scores['log_loss'] is None and scores['log_loss_infinite']
    else: assert scores['log_loss']==pytest.approx(-log(vector[1]))
    assert (scores['n_predictions'],scores['n_events'],scores['n_city_days'])==(1,1,1)
    assert scores['effective_independent_samples'] is None and len(scores['reliability'])==10
    assert result['rows'][0]['admission_ref']['id']=='pin' and result['rows'][0]['labels']
    assert result['outcome']=='DEGRADATION_CANDIDATE' and result['threshold_breaches']
    assert not result['financial_authority'] and not result['demotion_applied'] and not result['predeclared_policy_review_verified']
    assert not result['independent_label_attestation'] and not result['global_universe_coverage_verified']
    assert r['store'].pin_read_view()==before


def test_insufficient_city_days_are_not_cured_by_bucket_count(sample):
    result=measure_window(sample['store'],scope=sample['scope'],account_id='account',bundle_sha256=sample['binding'].bundle_sha256,
        policy=replace(sample['policy'],minimum_events=2,minimum_city_days=2),joins=(sample['join'],),as_of=sample['now'][0])
    assert result['outcome']=='INSUFFICIENT_COHORT' and result['scores']['n_city_days']==1


@pytest.mark.parametrize('fault',['scope','bundle','account','horizon','season','city','missing_label','class','target','exposure','duplicate'])
def test_other_slices_partial_labels_and_source_classes_cannot_be_generalized(sample,fault):
    r=sample; args=dict(scope=r['scope'],account_id='account',bundle_sha256=r['binding'].bundle_sha256,
        policy=r['policy'],joins=(r['join'],),as_of=r['now'][0])
    if fault=='scope':args['scope']=replace(r['scope'],season='WINTER')
    elif fault=='bundle':args['bundle_sha256']='f'*64
    elif fault=='account':args['account_id']='other'
    elif fault=='class':args['policy']=replace(r['policy'],evidence_class='PUBLIC_OBSERVED')
    elif fault=='target':args['policy']=replace(r['policy'],capture_family='CONDITIONED_PAYOUT')
    elif fault=='missing_label':args['joins']=(replace(r['join'],label_ids=r['join'].label_ids[:-1]),)
    elif fault=='exposure':args['joins']=(replace(r['join'],prior_exposure='UNINSPECTED'),)
    elif fault=='duplicate':args['joins']=(r['join'],r['join'])
    else:args['joins']=(replace(r['join'],**{fault:'OTHER'}),)
    with pytest.raises(EvidenceError):measure_window(r['store'],**args)


def test_label_revision_must_be_current_at_measurement_cutoff(sample):
    r=sample; old_at=r['now'][0]; original=measure(r)
    label=r['store'].get(next(iter(r['labels'].values()))); b=label['body']; r['now'][0]+=1
    payload=dict(b['payload'],label_version='revised')
    r['store'].capture('correction',event_id=label['event_id'],kind='LABEL',provider=b['provider'],
        source_identity=b['source_identity'],revision='revised',payload=payload,evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='SUPERSEDED'):measure(r)
    historical=measure_window(r['store'],scope=r['scope'],account_id='account',bundle_sha256=r['binding'].bundle_sha256,
        policy=r['policy'],joins=(r['join'],),as_of=old_at)
    assert historical['scores']==original['scores']


@pytest.mark.parametrize('fault',['future_cutoff','label_not_known','outside_window','deadline'])
def test_causal_cutoff_and_resource_bound(sample,fault):
    r=sample; args=dict(scope=r['scope'],account_id='account',bundle_sha256=r['binding'].bundle_sha256,
        policy=r['policy'],joins=(r['join'],),as_of=r['now'][0])
    if fault=='future_cutoff':args['as_of']+=1
    elif fault=='label_not_known':args['as_of']-=1
    elif fault=='outside_window':args['policy']=replace(r['policy'],window_seconds=1.)
    else:
        times=iter([0.,0.,10.]);args['monotonic']=lambda:next(times,10.)
    with pytest.raises(EvidenceError):measure_window(r['store'],**args)


def test_old_unscoped_capture_is_preserved_but_cannot_gain_scope_later(factory):
    r=factory(); r['evaluation']=TemperatureStrategies(r['store']).evaluate('forecast',r['request'])
    captured=capture_forecast_vector(r['store'],'legacy-unscoped',**kwargs(r)); before=captured
    ids=labels(r,captured)
    join=ForecastLabelJoin(captured['id'],tuple(ids.items()),r['context'].city_id,r['scope'].horizon,r['scope'].season)
    with pytest.raises(EvidenceError,match='ORIGINAL_SCOPED_CAPTURE'):
        measure_window(r['store'],scope=r['scope'],account_id='account',bundle_sha256=r['binding'].bundle_sha256,
            policy=DriftPolicy('test','FORECAST','SYNTHETIC',86400.,1,1,0.,0.),joins=(join,),as_of=r['now'][0])
    assert r['store'].get(captured['id'])==before


@pytest.mark.parametrize('changes',[dict(minimum_events=True),dict(minimum_city_days=129),dict(window_seconds=0),
    dict(maximum_brier=2.1),dict(maximum_log_loss=-1),dict(capture_family='NEXT_OBSERVATION'),dict(evidence_class='INDEPENDENT')])
def test_policy_has_no_invented_or_unbounded_targets(changes):
    with pytest.raises(EvidenceError):replace(DriftPolicy('test','FORECAST','SYNTHETIC',86400.,1,1,.5,1.),**changes)
