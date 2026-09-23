from dataclasses import replace

import pytest

from polymarket_scanner.v11.certification import StationMetadata
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, digest
from polymarket_scanner.v11.pws_quality import (
    PWSPolicy, PWSSample, PWSIdentityTracker, geometry, neighborhood, samples_from_capture, archive_neighborhood,
)


def policy(**changes):
    # Synthetic engineering fixture; not an approved production calibration.
    return replace(PWSPolicy('test-v1',((5.,1.),(20.,.5),(50.,.1)),600.,7200.,2,300.,900.,
        5.,30.,-80.,60.,600.,.01,3.,3,.5,50.,1000.,300.,.05),**changes)


def official():
    return StationMetadata('KATL','Atlanta','US',33.,-84.,300.,'America/New_York','NOAA_WRH',
                             ('NOAA',),('GEFS',),'a'*64,1.)


def samples(station='A',latitude=33.01,temperatures=(19.,19.5,20.),times=(1000.,1300.,1600.)):
    return tuple(PWSSample(station,'APRSWXNET',latitude,-84.,300.,t,t+1,temp,59,0,'b'*64,
                           digest({'station':station,'at':t,'temp':temp})) for t,temp in zip(times,temperatures))


def network():
    return samples('A',33.01)+samples('B',33.03)+samples('C',33.05)


def evaluate(rows=None,**kw):
    return neighborhood(network() if rows is None else rows,official=official(),as_of=1610.,policy=policy(),**kw)


def test_healthy_network_is_informational_not_settlement_or_lead():
    value=evaluate()
    assert value['health']=='HEALTHY' and value['usable_station_count']==3
    assert value['weighted_median_c']==20
    assert value['iqr_c']==0
    assert value['temperature_changes']['300']['change_c']==.5
    assert value['temperature_changes']['900']['change_c'] is None
    assert not value['settlement_authority'] and not value['trading_influence_permitted']
    assert not value['lead_advantage_verified']


def test_empty_pws_is_unavailable_without_fabrication():
    value=evaluate(())
    assert value['health']=='UNAVAILABLE' and value['weighted_median_c'] is None


def test_late_arrival_does_not_enter_replay_even_with_early_sensor_time():
    future=tuple(replace(s,received_at=1700.) for s in samples())
    value=evaluate(future)
    assert value['station_count']==0 and value['dropped']['NOT_YET_RECEIVED']==3


def test_duplicate_cache_responses_do_not_create_more_history_or_freshness():
    base=samples()
    duplicates=base+tuple(replace(s,received_at=1605.) for s in base)
    row=evaluate(duplicates)['stations'][0]
    assert row['sample_count']==3
    assert row['first_received_at']==1601.
    assert row['version_received_at']==1601.
    assert evaluate(duplicates)['dropped']['DUPLICATE_OBSERVATION_VERSION']==3


@pytest.mark.parametrize('reason,changed',[
    ('STALE',lambda rows:tuple(replace(s,observed_at=s.observed_at-1000,received_at=s.received_at-1000) for s in rows)),
    ('PHYSICAL_RANGE',lambda rows:rows[:-1]+(replace(rows[-1],temperature_c=70.),)),
    ('PROVIDER_QC_UNKNOWN_OR_FAILED',lambda rows:rows[:-1]+(replace(rows[-1],qc_results=1),)),
    ('JUMP_OR_IMPLAUSIBLE_RATE',lambda rows:rows[:-1]+(replace(rows[-1],temperature_c=30.),)),
    ('METADATA_DRIFT',lambda rows:rows[:-1]+(replace(rows[-1],latitude=33.1),)),
    ('ELEVATION_MISMATCH',lambda rows:tuple(replace(s,elevation_m=1800.) for s in rows)),
    ('OUTSIDE_DISTANCE_BANDS',lambda rows:tuple(replace(s,latitude=34.) for s in rows)),
])
def test_defensive_qc_failures_are_explicit_and_get_zero_weight(reason,changed):
    row=evaluate(changed(samples()))['stations'][0]
    assert reason in row['rejection_reasons']
    assert row['weight']==0


def test_insufficient_history_and_long_gap_are_not_hidden_by_old_samples():
    row=evaluate(samples(times=(100.,200.,1600.)))['stations'][0]
    assert 'COMMUNICATION_GAP' in row['rejection_reasons']
    assert 'INSUFFICIENT_RECENT_HISTORY' in row['rejection_reasons']


def test_flat_sensor_is_downweighted_without_inventing_indoor_certainty():
    flat=evaluate(samples(temperatures=(20.,20.,20.)))['stations'][0]
    changing=evaluate(samples())['stations'][0]
    assert flat['flat_sensor_suspected'] and flat['weight']<changing['weight']
    assert flat['indoor_exposure_status']=='NOT_INFERRED_WITHOUT_VALIDATED_HISTORY'


def test_colocated_ids_do_not_count_as_independent_station_support():
    value=evaluate(samples('A')+samples('B')+samples('C'))
    assert value['usable_station_count']==1 and value['health']=='DEGRADED'
    assert sum('COLOCATED_DEPENDENT_FEED' in r['rejection_reasons'] for r in value['stations'])==2


def test_independent_neighbors_reject_a_single_consistent_hot_outlier():
    rows=network()+samples('D',33.07,temperatures=(34.,34.5,35.))
    value=evaluate(rows)
    hot=next(r for r in value['stations'] if r['station_key'].endswith(':D'))
    assert 'INDEPENDENT_NEIGHBOR_DISAGREEMENT' in hot['rejection_reasons'] and hot['weight']==0
    assert value['weighted_median_c']==20.


def test_official_residual_retains_source_role_and_does_not_override_station():
    value=evaluate(official_temperature_c=22.,official_received_at=1600.,official_observed_at=1590.,official_source_role='METAR_PROXY')
    assert value['official_minus_neighborhood_c']==2.
    assert value['official_anchor_source_role']=='METAR_PROXY'
    assert not value['settlement_authority']
    with pytest.raises(EvidenceError,match='NOT_CAUSAL_OR_FRESH'):
        evaluate(official_temperature_c=22.,official_received_at=1700.,official_observed_at=1590.,official_source_role='METAR_PROXY')
    with pytest.raises(EvidenceError,match='NOT_CAUSAL_OR_FRESH'):
        evaluate(official_temperature_c=22.,official_received_at=1600.,official_observed_at=900.,official_source_role='METAR_PROXY')


def test_bearing_and_distance_retain_spatial_context():
    distance,bearing=geometry(0.,0.,1.,0.)
    assert distance==pytest.approx(111.195,abs=.01)
    assert bearing==0.
    row=evaluate()['stations'][0]
    assert row['distance_km']>0 and row['elevation_difference_m']==0


def test_policy_and_input_bounds_are_not_unbounded_fanout():
    with pytest.raises(EvidenceError,match='BAND_ORDER'):
        policy(distance_bands=((20.,.5),(10.,1.)))
    with pytest.raises(EvidenceError,match='STATION_FANOUT_BOUND'):
        evaluate(tuple(samples(str(i),33.+i*.001)[-1] for i in range(129)))


def test_relocation_quarantine_survives_reversion_and_process_restart(tmp_path):
    tmp_path.chmod(0o700)
    clock=[1700.]
    store=EvidenceStore(tmp_path/'pws.sqlite','V11_PAPER',clock=lambda:clock[0])
    tracker=PWSIdentityTracker(store,policy())
    def observe(label,sample):
        payload={'observations':[{**sample.metadata,'observation_identity':sample.observation_identity}]}
        raw=store.capture(label+':raw',event_id='event',kind='PWS_OBSERVATION',provider='fixture',
                          source_identity='PWS',revision=label,payload=payload,evidence_class='SYNTHETIC')
        bound=replace(sample,evidence_sha256=raw['sha256'])
        return tracker.observe(label,bound,raw_evidence_id=raw['id'])
    baseline=samples()[-1]
    assert not observe('original',baseline)['body']['details']['quarantined']
    assert observe('relocated',replace(baseline,latitude=33.1))['body']['details']['quarantined']
    assert observe('reverted',baseline)['body']['details']['quarantined']
    restarted=PWSIdentityTracker(EvidenceStore(store.path,'V11_PAPER',clock=lambda:clock[0]),policy())
    quarantined=restarted.quarantined((baseline.key,))
    row=evaluate(samples(),quarantined=quarantined)['stations'][0]
    assert 'PERSISTENT_METADATA_QUARANTINE' in row['rejection_reasons']


def test_spatial_gradient_needs_two_dimensional_station_support():
    assert evaluate()['spatial_gradient'] is None  # Collinear synthetic stations.
    rows=samples('A',33.01)+samples('B',33.03)
    third=tuple(replace(s,longitude=-83.97) for s in samples('C',33.01,temperatures=(20.,20.5,21.)))
    gradient=evaluate(rows+third)['spatial_gradient']
    assert gradient['east_c_per_km']>0
    assert gradient['supporting_stations']==3


def test_raw_normalized_qc_archive_path_is_hash_bound(tmp_path):
    from test_v11_weather_sources import XML,AT,request
    from polymarket_scanner.v11.weather_sources import normalize_weather_capture
    tmp_path.chmod(0o700)
    store=EvidenceStore(tmp_path/'weather.sqlite','V11_PAPER',clock=lambda:AT)
    raw=store.capture('raw',event_id='event',kind='PWS_OBSERVATION',provider='NOAA_MADIS_CWOP',
        source_identity='CWOP_NEAR:KATL',revision='1',payload={'response':XML,'request_params':dict(request().params)},
        evidence_class='SYNTHETIC')
    norm=normalize_weather_capture(store,'raw',record_id='normalized',station='KATL')
    parsed=samples_from_capture(store,norm['id'],as_of=AT)
    assert len(parsed)==1 and parsed[0].temperature_c==pytest.approx(30.)
    context=replace(official(),latitude=33.64,longitude=-84.42)
    archived=archive_neighborhood(store,'qc',event_id='event',capture_ids=(norm['id'],),official=context,policy=policy())
    assert archived['body']['payload']['health']=='UNAVAILABLE'  # One sample is insufficient history.
    assert archived['body']['payload']['source_captures'][0]['sha256']==norm['sha256']
    assert archived['body']['evidence_class']=='SYNTHETIC'
    with pytest.raises(EvidenceError,match='NOT_CAUSAL'):
        samples_from_capture(store,norm['id'],as_of=AT-1)
    with pytest.raises(EvidenceError,match='STATION_CONTEXT_MISMATCH'):
        archive_neighborhood(store,'wrong',event_id='event',capture_ids=(norm['id'],),official=replace(context,station='KSFO'),policy=policy())
    forged=norm['body']['payload']
    forged['observations'][0]['temperature_c']=10.
    bad=store.capture('forged',event_id='event',kind='PWS_OBSERVATION',provider='NOAA_MADIS_CWOP',
        source_identity='CWOP_NEAR:KATL',revision='2',payload=forged,evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='NORMALIZED_RAW_MISMATCH'):
        samples_from_capture(store,bad['id'],as_of=AT)


def test_no_pws_response_can_be_archived_as_unavailable_without_fake_sensor(tmp_path):
    tmp_path.chmod(0o700)
    store=EvidenceStore(tmp_path/'weather.sqlite','V11_PAPER',clock=lambda:1610.)
    result=archive_neighborhood(store,'missing',event_id='event',capture_ids=(),official=official(),policy=policy())
    assert result['body']['payload']['health']=='UNAVAILABLE'
    assert result['body']['payload']['stations']==[]


def test_relocation_inside_history_is_not_erased_by_latest_reversion():
    rows=samples()
    moved=(rows[0],replace(rows[1],latitude=33.1),rows[2])
    result=evaluate(moved)
    assert 'METADATA_DRIFT' in result['stations'][0]['rejection_reasons']
    assert result['stations'][0]['weight']==0
