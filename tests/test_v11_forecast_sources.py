from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from polymarket_scanner.v11 import forecast_sources as source
from polymarket_scanner.v11.forecast_runtime import ForecastNormalizationWorker
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from polymarket_scanner.v11.event_risk import EventContext
from polymarket_scanner.v11.probability import FINAL_EXTREME
from polymarket_scanner.v11.rules import fingerprint_event
from polymarket_scanner.v11.runtime_health import SourceNeed, admission_heads
from polymarket_scanner.v11.strategy_pipeline import _model_inputs
from test_v11_pws_quality import official
from test_v11_runtime_health import monitor, ready, advance
from test_weather_only_forecast import _payload
from test_weather_final_gpt6_exact_replays import _event


@pytest.fixture
def forecast(tmp_path):
    tmp_path.chmod(0o700);now=[datetime(2026,9,14,12,tzinfo=timezone.utc).timestamp()]
    metadata=official();rule=fingerprint_event(_event(station='KATL'),station_timezone=metadata.timezone,metadata_fingerprint=metadata.fingerprint)
    store=EvidenceStore(tmp_path/'forecast.sqlite','V11_PAPER',clock=lambda:now[0])
    return dict(store=store,now=now,context=EventContext('account','Atlanta','KATL',rule.payload['event_id']),
                plan=source.ForecastPlan(rule,metadata,50.,600.))


def raw(r,label='raw',*,payload=None,issued=None,evidence_class='SYNTHETIC'):
    p=r['plan'];response=_payload(family=p.rule.payload['family'])
    response['daily']['time']=[p.rule.payload['target_date']]
    response.update(latitude=p.metadata.latitude,longitude=p.metadata.longitude)
    data=dict(request_url=source.OPEN_METEO_ENSEMBLE,request_params=source.request_parameters(p),response=response)
    return r['store'].capture(label,event_id=p.event_id,kind='MODEL',provider=source.PROVIDER,source_identity=p.source_identity,
        revision=label,payload=data if payload is None else payload,issued_at=issued,evidence_class=evidence_class)


def worker(r,monkeypatch):
    p=r['plan'];m=monitor(r,monkeypatch,scopes={p.event_id:('FUTURE_FORECAST',)},
        sources=(SourceNeed(p.event_id,'FUTURE_FORECAST','MODEL',source.PROVIDER,p.source_identity,600.),))
    ready(r,m)
    return ForecastNormalizationWorker(r['store'],m,(p,))


def test_normalization_preserves_unquantized_members_grid_and_raw_receipt_but_gates_age(forecast):
    r=forecast;captured=raw(r);r['now'][0]+=2
    result=source.normalize_forecast_capture(r['store'],captured['id'],plan=r['plan'],record_id='normalized')
    b=result['body'];p=b['payload']
    assert b['received_at']==captured['body']['received_at']<b['available_at']
    assert len(p['temperature_input']['members'])==31 and p['temperature_input']['members'][0]==69.4
    assert p['raw_evidence_sha256']==captured['sha256'] and p['grid_distance_km']==0
    assert b['issued_at'] is None and b['published_at'] is None and not p['calibrated_probability']
    assert p['run_provenance']['status']=='EXACT_RUN_BINDING_UNAVAILABLE'
    with pytest.raises(EvidenceError,match='MODEL_RUN_AGE_UNKNOWN'):
        _model_inputs(r['store'],r['plan'].rule,('normalized',),r['now'][0],target=FINAL_EXTREME)


@pytest.mark.parametrize('claim',['body_issue','response_run','generation_time','availability_metadata'])
def test_unbound_timestamps_cannot_be_promoted_to_run_or_publication_time(forecast,claim):
    r=forecast;base=raw(r);data=deepcopy(base['body']['payload']);at=r['now'][0]
    if claim=='response_run':data['response']['run']=datetime.fromtimestamp(at-3600,timezone.utc).isoformat()
    if claim=='generation_time':data['response']['generationtime_ms']=at*1000
    if claim=='availability_metadata':data['response']['last_run_initialisation_time']=at-3600;data['response']['last_run_availability_time']=at-30
    captured=raw(r,'claimed',payload=data,issued=at-60 if claim=='body_issue' else None)
    row=source.normalize_forecast_capture(r['store'],captured['id'],plan=r['plan'],record_id='normalized')
    assert row['body']['issued_at'] is None and row['body']['published_at'] is None
    assert not row['body']['payload']['run_provenance']['raw_issue_claim_trusted']


@pytest.mark.parametrize('fault',['wrong_model','wrong_request_station','other_day','wrong_response_unit','different_grid','extra_member','daily_null'])
def test_exact_request_response_and_grid_binding_are_required(forecast,fault):
    r=forecast;data=raw(r)['body']['payload']
    if fault=='wrong_model':data['request_params']['models']='another-model'
    if fault=='wrong_request_station':data['request_params']['latitude']='1'
    if fault=='other_day':data['response']['daily']['time']=['2026-09-15']
    if fault=='wrong_response_unit':data['response']['daily_units']['temperature_2m_max']='°C'
    if fault=='different_grid':data['response']['latitude']=40.
    if fault=='extra_member':data['response']['daily']['temperature_2m_max_member31']=[70.]
    if fault=='daily_null':data['response']['daily']=None
    captured=raw(r,'bad',payload=data)
    with pytest.raises(EvidenceError):source.normalize_forecast_capture(r['store'],captured['id'],plan=r['plan'],record_id='rejected')
    assert r['store'].latest_source(kind='MODEL',event_id=r['plan'].event_id,provider=source.PROVIDER,source_identity=r['plan'].source_identity)==captured


@pytest.mark.parametrize('fault',['historical','stale','superseded'])
def test_backfill_stale_and_superseded_raw_cannot_gain_current_availability(forecast,fault):
    r=forecast;captured=raw(r,evidence_class='HISTORICAL_AVAILABILITY_UNKNOWN' if fault=='historical' else 'SYNTHETIC')
    if fault=='stale':r['now'][0]+=600
    if fault=='superseded':raw(r,'newer')
    with pytest.raises(EvidenceError):source.normalize_forecast_capture(r['store'],captured['id'],plan=r['plan'],record_id='rejected')


def test_completed_normalization_replays_without_receipt_renewal_and_rejects_changed_plan(forecast):
    r=forecast;captured=raw(r);kw=dict(plan=r['plan'],record_id='normalized')
    result=source.normalize_forecast_capture(r['store'],captured['id'],**kw);tip=r['store'].pin_read_view();r['now'][0]+=1000
    assert source.normalize_forecast_capture(r['store'],captured['id'],**kw)==result and r['store'].pin_read_view()==tip
    with pytest.raises(EvidenceError,match='REPLAY_CONFLICT'):
        source.normalize_forecast_capture(r['store'],captured['id'],**dict(kw,plan=replace(r['plan'],maximum_grid_distance_km=30.)))


def test_new_model_input_racing_normalization_cannot_be_overwritten(forecast,monkeypatch):
    r=forecast;captured=raw(r);append=r['store']._append
    def race(key,*a,**kw):
        if key=='normalized':raw(r,'racing')
        return append(key,*a,**kw)
    monkeypatch.setattr(r['store'],'_append',race)
    with pytest.raises(EvidenceError,match='STATE_CHANGED'):
        source.normalize_forecast_capture(r['store'],captured['id'],plan=r['plan'],record_id='normalized')
    assert r['store'].latest(kind='MODEL',event_id=r['plan'].event_id)['id']=='racing'


def test_worker_and_health_keep_unknown_run_gated_without_network_or_account_changes(forecast,monkeypatch):
    r=forecast;raw(r);w=worker(r,monkeypatch);d=w.step('one')['body']['details']
    assert d['outcome']=='FORECAST_MEMBERS_ARCHIVED_RUN_UNVERIFIED'
    w.health.sample('model-health')
    with pytest.raises(EvidenceError,match='REQUIRED_SOURCE'):
        admission_heads(r['store'],account_id='account',event_id=r['plan'].event_id,strategies=('FUTURE_FORECAST',))
    original=r['store'].get(d['normalized_id']);advance(r)
    again=w.step('another')['body']['details']
    assert again['outcome']=='NORMALIZED_SOURCE_RUN_STILL_UNVERIFIED' and r['store'].get(again['normalized_id'])==original
    assert not r['store'].records(kind='COORDINATOR_EVENT') and not r['store'].records(kind='TRADE')


def test_worker_recovers_committed_normalization_without_republishing(forecast,monkeypatch):
    r=forecast;raw(r);w=worker(r,monkeypatch);save=w._save
    def fail(key,state,**kw):
        if kw.get('outcome')=='FORECAST_MEMBERS_ARCHIVED_RUN_UNVERIFIED':raise RuntimeError('INTERRUPTED')
        return save(key,state,**kw)
    with monkeypatch.context() as patch:
        patch.setattr(w,'_save',fail)
        with pytest.raises(RuntimeError,match='INTERRUPTED'):w.step('one')
    prior=r['store'].latest(kind='MODEL',event_id=r['plan'].event_id);advance(r)
    result=ForecastNormalizationWorker(r['store'],w.health,(r['plan'],)).step('resume')
    assert r['store'].get(result['body']['details']['normalized_id'])==prior


def test_bad_clock_defers_forecast_normalization_and_preserves_raw(forecast,monkeypatch):
    r=forecast;captured=raw(r);w=worker(r,monkeypatch);r['sync'][0]=False
    assert w.step('bad-clock')['body']['details']['outcome']=='DEFERRED_CLOCK_UNHEALTHY'
    assert r['store'].latest(kind='MODEL',event_id=r['plan'].event_id)==captured


def test_first_worker_cannot_inherit_normalization_from_a_different_grid_policy(forecast,monkeypatch):
    r=forecast;captured=raw(r)
    original=source.normalize_forecast_capture(r['store'],captured['id'],plan=r['plan'],record_id='previous-plan')
    r['plan']=replace(r['plan'],maximum_grid_distance_km=30.)
    d=worker(r,monkeypatch).step('new-plan')['body']['details']
    assert d['outcome']=='FORECAST_NORMALIZATION_GATED' and d['reason']=='FORECAST_NORMALIZATION_REPLAY_CONFLICT'
    assert r['store'].get(original['id'])==original
