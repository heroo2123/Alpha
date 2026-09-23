from dataclasses import replace
from datetime import datetime,timezone

import pytest

from polymarket_scanner.v11.evidence import EvidenceError,EvidenceStore
from polymarket_scanner.v11.metar_features import parse_metar_physical
from polymarket_scanner.v11.nowcast_features import archive_nowcast_features,PHYSICAL_SCHEMA
from polymarket_scanner.v11.pws_quality import archive_neighborhood
from polymarket_scanner.v11.weather_sources import parse_awc_metar
from test_v11_pws_quality import official,policy


AT=datetime(2026,9,23,16,0,tzinfo=timezone.utc).timestamp()


def report(at=AT,wind='27010G20KT',temperature='20/10',cloud='BKN040',weather='-RA',remarks=''):
    stamp=datetime.fromtimestamp(at,timezone.utc).strftime('%d%H%MZ')
    return f'METAR KATL {stamp} AUTO {wind} 10SM {weather} {cloud} {temperature} A2992 {remarks}'.strip()


def physical(**kw):
    return parse_metar_physical(report(**kw),station='KATL',observed_at=AT)


def test_metar_body_units_and_rounding_remain_explicit_auxiliary_context():
    p=physical()
    assert p['wind_speed_mps']==pytest.approx(10*1852/3600)
    assert p['wind_gust_mps']==pytest.approx(20*1852/3600)
    assert p['wind_direction_degrees']==270
    assert p['body_temperature_c']==20 and p['body_dewpoint_c']==10
    assert p['cloud_layers']==[{'cover':'BKN','base_m':pytest.approx(1219.2),'kind':None}]
    assert p['reported_weather_codes']==['-RA']
    assert not p['settlement_authority']


@pytest.mark.parametrize('wind,expected',[('09010MPS',10.),('09036KMH',10.)])
def test_wind_units_come_from_report_not_station_country(wind,expected):
    assert physical(wind=wind)['wind_speed_mps']==pytest.approx(expected)


@pytest.mark.parametrize('wind',['VRB03KT','00000KT'])
def test_variable_or_calm_wind_does_not_invent_north_direction(wind):
    assert physical(wind=wind)['wind_direction_degrees'] is None


def test_negative_temperatures_and_remarks_do_not_replace_body():
    p=physical(temperature='M05/M10',remarks='RMK 09030KT 40/30 OVC001')
    assert p['body_temperature_c']==-5 and p['body_dewpoint_c']==-10
    assert p['wind_direction_degrees']==270
    assert p['cloud_layers'][0]['cover']=='BKN'


@pytest.mark.parametrize('raw', [None,'garbage',report().replace('KATL','KSFO'),report(at=AT-60)])
def test_missing_or_mismatched_raw_report_is_missing_physical_context(raw):
    p=parse_metar_physical(raw,station='KATL',observed_at=AT)
    assert p['status']=='UNAVAILABLE' and p['wind_speed_mps'] is None


def test_optional_physical_failure_keeps_valid_temperature_observation():
    p=parse_awc_metar([{'icaoId':'KATL','obsTime':AT,'temp':20,'rawOb':'invalid'}],station='KATL',
                       received_at=AT+1,max_age_seconds=60)
    assert len(p['observations'])==1 and p['observations'][0]['temperature_c']==20
    assert p['observations'][0]['physical_context']['status']=='UNAVAILABLE'


@pytest.fixture
def env(tmp_path):
    tmp_path.chmod(0o700)
    clock=[AT-1200]
    return EvidenceStore(tmp_path/'features.sqlite','V11_PAPER',clock=lambda:clock[0]),clock


def raw(store,clock,key,at,temp=20,source_class='SYNTHETIC'):
    clock[0]=at+1
    return store.capture(key,event_id='event',kind='OFFICIAL_OBSERVATION',provider='NOAA_AWC',
        source_identity='KATL',revision=key,payload={'response':[{'icaoId':'KATL','obsTime':at,'temp':temp,
           'rawOb':report(at=at,temperature=f'{int(temp):02d}/10')}]},evidence_class=source_class)


def build(store,key,captures,**kw):
    return archive_nowcast_features(store,key,event_id='event',official=official(),awc_capture_ids=captures,
        max_observation_age_seconds=600,trajectory_window_seconds=3600,maximum_gap_seconds=900,**kw)


def test_causal_trajectory_and_missing_pws_are_archived_without_probability(env):
    store,clock=env
    raw(store,clock,'one',AT-600,18)
    raw(store,clock,'two',AT-300,19)
    raw(store,clock,'three',AT,20)
    clock[0]=AT+10
    result=build(store,'features',('one','two','three'))
    values=result['body']['payload']['values']
    assert values['temperature_slope_c_per_hour']==pytest.approx(12.)
    assert values['temperature_acceleration_c_per_hour2']==0
    assert values['trajectory_span_seconds']==600
    assert values['dewpoint_spread_c']==10
    assert values['reported_broken_cloud']==1
    assert values['pws_weighted_median_c'] is None
    assert result['body']['payload']['feature_schema_sha256']==PHYSICAL_SCHEMA.sha256
    explanation=store.get('features:explanation')['body']['details']
    assert 'PWS_NOT_SUPPLIED' in explanation['reasons']
    assert explanation['calibrated_probability'] is False


def test_ablated_family_changes_only_its_research_features(env):
    store,clock=env
    raw(store,clock,'one',AT)
    clock[0]=AT+10
    a=build(store,'baseline',('one',))['body']['payload']['values']
    b=build(store,'ablation',('one',),ablated_families=frozenset({'WIND','CLOUD'}))['body']['payload']['values']
    assert a['wind_speed_mps'] is not None and b['wind_speed_mps'] is None
    assert b['reported_broken_cloud'] is None
    assert a['official_proxy_temperature_c']==b['official_proxy_temperature_c']


def test_future_receipt_and_availability_unknown_are_not_backdated(env):
    store,clock=env
    raw(store,clock,'future',AT)
    clock[0]=AT-1
    with pytest.raises(EvidenceError,match='NOT_CAUSAL'):
        build(store,'past',('future',))
    raw(store,clock,'backfill',AT+60,source_class='HISTORICAL_AVAILABILITY_UNKNOWN')
    with pytest.raises(EvidenceError,match='NOT_CAUSAL'):
        build(store,'unknown',('backfill',))


def test_unavailable_pws_does_not_remove_healthy_official_features(env):
    store,clock=env
    raw(store,clock,'one',AT)
    clock[0]=AT+10
    pws=archive_neighborhood(store,'pws-missing',event_id='event',capture_ids=(),official=official(),policy=policy())
    result=build(store,'features',('one',),pws_capture_id=pws['id'])['body']['payload']['values']
    assert result['official_proxy_temperature_c']==20
    assert result['pws_weighted_median_c'] is None


def test_daylight_is_forecast_context_with_causal_issue_and_exact_local_date(env):
    store,clock=env
    clock[0]=AT
    store.capture('daylight',event_id='event',kind='MODEL',provider='fixture',source_identity='sun-window',revision='1',
        issued_at=AT-3600,payload={'feature_type':'DAYLIGHT_WINDOW','station':'KATL','local_date':'2026-09-23',
            'source_role':'ASTRONOMICAL_FORECAST_NOT_SETTLEMENT','sunrise_at':AT-5*3600,'sunset_at':AT+6*3600},
        evidence_class='SYNTHETIC')
    clock[0]=AT+10
    result=build(store,'features',(),daylight_capture_id='daylight')
    assert result['body']['payload']['values']['daylight_remaining_seconds']==6*3600-10


def test_no_inputs_produce_explicit_missing_measurement(env):
    store,clock=env
    result=build(store,'no-inputs',())
    assert result['kind']=='MEASUREMENT'
    assert result['body']['details']['state']=='NO_SOURCE_INPUTS'
    assert all(v is None for v in result['body']['details']['values'].values())
