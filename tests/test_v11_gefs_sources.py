import asyncio
import base64
from dataclasses import replace
from datetime import date, datetime, timezone
import hashlib

import httpx
import pytest

from polymarket_scanner.v11 import gefs_sources as source
from polymarket_scanner.v11.collection import PublicCollector
from polymarket_scanner.v11.gefs_runtime import GEFSWorker
from polymarket_scanner.v11.observation_runtime import ScheduledCollector
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from polymarket_scanner.v11.event_risk import EventContext
from polymarket_scanner.v11.forecast_sources import ForecastPlan
from polymarket_scanner.v11.probability import FINAL_EXTREME
from polymarket_scanner.v11.runtime_feed import EvidenceFeed
from polymarket_scanner.v11.runtime_health import SourceNeed, admission_heads
from polymarket_scanner.v11.rules import fingerprint_event
from polymarket_scanner.v11.strategy_pipeline import _model_inputs
from test_v11_grib_fields import grib, RUN, mutate, u
from test_v11_pws_quality import official
from test_v11_runtime_health import monitor,ready,advance
from test_weather_final_gpt6_exact_replays import _event


@pytest.fixture
def gefs(tmp_path):
    tmp_path.chmod(0o700);now=[RUN+3600];metadata=official()
    rule=fingerprint_event(_event(station='KATL'),station_timezone=metadata.timezone,metadata_fingerprint=metadata.fingerprint)
    plan=source.GEFSPlan(ForecastPlan(rule,metadata,50.,3600.),RUN)
    store=EvidenceStore(tmp_path/'gefs.sqlite','V11_PAPER',clock=lambda:now[0])
    return dict(store=store,now=now,plan=plan,context=EventContext('account','Atlanta','KATL',plan.event_id))


def raw(r,member=0,hour=None,*,label=None,data=None,evidence_class='SYNTHETIC',issued=None):
    plan=r['plan'];hour=plan.hours[0] if hour is None else hour;req=source.field_request(plan,member,hour)
    data=grib(member=member,hour=hour,run=plan.initialized_at) if data is None else data
    return r['store'].capture(label or f'raw-{member}-{hour}',event_id=plan.event_id,kind='MODEL',provider=source.PROVIDER,
        source_identity=req.source_identity,revision=req.revision,issued_at=issued,evidence_class=evidence_class,
        payload=dict(endpoint=req.url,request_params=dict(req.params),http_status=200,response_format='GEFS_GRIB2',
            response_base64=base64.b64encode(data).decode(),response_sha256=hashlib.sha256(data).hexdigest(),source_time_status='NOT_YET_NORMALIZED'))


def field(r,member,hour,**kwargs):
    row=raw(r,member,hour,**kwargs)
    return source.normalize_field(r['store'],row['id'],plan=r['plan'],member=member,hour=hour,record_id=f'field-{member}-{hour}')


def all_fields(r):
    return tuple(field(r,m,h,data=grib(member=m,hour=h,values=(280+h//3,290.,291.,292.)))['id'] for m in range(31) for h in r['plan'].hours)


def worker(r,monkeypatch,transport):
    p=r['plan'];m=monitor(r,monkeypatch,scopes={p.event_id:('FUTURE_FORECAST',)},
        sources=(SourceNeed(p.event_id,'FUTURE_FORECAST','MODEL',source.PROVIDER,p.source_identity,86400.),))
    ready(r,m);client=httpx.AsyncClient(transport=transport)
    return GEFSWorker(ScheduledCollector(PublicCollector(r['store'],client,attempts=1)),m,(p,))


def test_full_31_member_path_reaches_model_input_with_original_run_and_unknown_publication(gefs):
    r=gefs;ids=all_fields(r);r['now'][0]+=2
    row=source.assemble_path(r['store'],plan=r['plan'],field_ids=ids,record_id='model');b=row['body'];p=b['payload']
    assert p['coverage']['local_day_end']-p['coverage']['local_day_start']==86400
    assert p['coverage']['forecast_hours']==list(range(3,31,3))
    assert len(p['field_references'])==310 and not p['coverage']['unobserved_intrastep_extrema_known']
    assert b['issued_at']==RUN and b['published_at'] is None and b['received_at']==RUN+3600
    components=_model_inputs(r['store'],r['plan'].rule,('model',),r['now'][0],target=FINAL_EXTREME)
    assert components[0].model_id==source.MODEL_ID and len(components[0].members)==31
    # Interpolate the 04:00 UTC boundary, excluding the 30h sample outside the day.
    assert components[0].members[0]==pytest.approx(((280+28/3)-273.15)*1.8+32)
    assert not p['calibrated_probability'] and not p['settlement_authority']
    assert source.current_path_heads(r['store'],row)==(('MODEL',r['plan'].event_id,row['seq']),)
    pin=r['store'].pin_read_view();r['now'][0]+=4000
    assert source.assemble_path(r['store'],plan=r['plan'],field_ids=ids,record_id='model')==row
    assert pin==r['store'].pin_read_view()


def test_grib_path_flows_into_immutable_probability_bundle_without_calibration_or_activation(gefs):
    from test_v11_model_artifacts import make_artifacts
    from polymarket_scanner.v11.model_artifacts import ArtifactStore,predict_with_bundle
    from polymarket_scanner.v11.evidence import digest
    r=gefs;ids=all_fields(r);row=source.assemble_path(r['store'],plan=r['plan'],field_ids=ids,record_id='model')
    components=_model_inputs(r['store'],r['plan'].rule,(row['id'],),r['now'][0],target=FINAL_EXTREME)
    directory=r['store'].path.parent/'immutable-artifacts';directory.mkdir(mode=0o700)
    artifacts=ArtifactStore(directory);values=make_artifacts()
    values['PROBABILITY']['parameters']['models'][0]['model_id']=source.MODEL_ID
    values['CALIBRATION']['parameters']['probability_artifact_sha256']=digest(values['PROBABILITY'])
    refs={kind:artifacts.put_artifact(value) for kind,value in values.items()}
    key=artifacts.put_bundle(artifacts=refs,target='FINAL_CONTRACT_PAYOUT',feature_schema_sha256=values['FEATURES']['feature_schema_sha256'])
    prediction=predict_with_bundle(artifacts.pin(key),r['plan'].rule,components,as_of=r['now'][0],max_source_age_seconds=86400.)
    payload=prediction.payload
    assert payload['calibration_status']=='UNCALIBRATED' and payload['bundle_sha256']==key
    assert payload['model_inputs'][0]['model_id']==source.MODEL_ID
    assert not r['store'].records(kind='COORDINATOR_EVENT')
    assert not list(directory.glob('*active*'))


@pytest.mark.parametrize('day,hours',[(date(2026,3,8),23),(date(2026,11,1),25)])
def test_dst_local_days_are_bracketed_by_forecast_points_without_assuming_24_hours(day,hours):
    metadata=official();rule=fingerprint_event(_event(target=day,station='KATL'),station_timezone=metadata.timezone,metadata_fingerprint=metadata.fingerprint)
    run=datetime.combine(day,datetime.min.time(),timezone.utc).timestamp()
    p=source.GEFSPlan(ForecastPlan(rule,metadata,50.,600.),run)
    assert p.window[1]-p.window[0]==hours*3600
    assert p.initialized_at+p.hours[0]*3600<=p.window[0] and p.initialized_at+p.hours[-1]*3600>=p.window[1]


@pytest.mark.parametrize('fault',['run','member','hour','request','raw_hash','stale','historical','new_head'])
def test_normalization_rejects_mismatched_bytes_or_source_lineage(gefs,fault):
    r=gefs;h=r['plan'].hours[0];data=grib(hour=h)
    if fault=='run':data=grib(hour=h,run=RUN-21600)
    if fault=='member':data=grib(hour=h,member=1)
    if fault=='hour':data=grib(hour=h+3)
    captured=raw(r,data=data,evidence_class='HISTORICAL_AVAILABILITY_UNKNOWN' if fault=='historical' else 'SYNTHETIC',issued=RUN-3600)
    if fault in {'request','raw_hash'}:
        p=captured['body']['payload']
        if fault=='request':p['request_params']['dir']=p['request_params']['dir'].replace('20260914','20260913')
        else:p['response_sha256']='f'*64
        captured=r['store'].capture('changed',event_id=r['plan'].event_id,kind='MODEL',provider=source.PROVIDER,
            source_identity=captured['body']['source_identity'],revision='changed',payload=p)
    if fault=='stale':r['now'][0]+=3600
    if fault=='new_head':raw(r,label='replacement')
    with pytest.raises(EvidenceError):source.normalize_field(r['store'],captured['id'],plan=r['plan'],member=0,hour=h,record_id='no')
    assert r['store'].latest_source(kind='MODEL',event_id=r['plan'].event_id,provider=source.PROVIDER,source_identity=r['plan'].source_identity) is None


def test_source_issue_claim_cannot_override_grib_initialization_and_raw_stays_unchanged(gefs):
    r=gefs;h=r['plan'].hours[0];capture=raw(r,issued=RUN-21600);r['now'][0]+=1
    row=source.normalize_field(r['store'],capture['id'],plan=r['plan'],member=0,hour=h,record_id='normalized')
    assert row['body']['issued_at']==RUN and row['body']['published_at'] is None
    assert row['body']['received_at']==capture['body']['received_at']<row['body']['available_at']
    assert r['store'].get(capture['id'])==capture
    assert EvidenceFeed._classification(capture)=='RAW_NORMALIZATION_REQUIRED'
    assert EvidenceFeed._classification(row)=='FORECAST_PATH_ASSEMBLY_REQUIRED'


def test_partial_ensemble_is_not_a_model_and_does_not_silently_drop_members(gefs):
    r=gefs;row=field(r,0,r['plan'].hours[0])
    with pytest.raises(EvidenceError,match='ALL_MEMBERS'):source.assemble_path(r['store'],plan=r['plan'],field_ids=(row['id'],),record_id='partial')


def test_response_cannot_select_a_grid_outside_its_requested_tile(gefs):
    r=gefs;h=r['plan'].hours[0];row=raw(r,data=grib(hour=h,lat=34.))
    with pytest.raises(EvidenceError,match='OUTSIDE_REQUESTED_TILE'):
        source.normalize_field(r['store'],row['id'],plan=r['plan'],member=0,hour=h,record_id='outside')


@pytest.mark.parametrize('fault',['scope','global','multiple_variables','different_endpoint','hour','cycle'])
def test_public_request_allowlist_stays_exact_and_small(gefs,fault):
    req=source.field_request(gefs['plan'],0,gefs['plan'].hours[0]);p=dict(req.params)
    with pytest.raises(EvidenceError):
        if fault=='scope':replace(req,kind='BOOK')
        elif fault=='different_endpoint':replace(req,url='https://nomads.ncep.noaa.gov/pub/data/file')
        else:
            if fault=='global':p['rightlon']='360'
            if fault=='multiple_variables':p['var_TMAX']='on'
            if fault=='hour':p['file']=p['file'].replace('f003','f004')
            if fault=='cycle':p['dir']=p['dir'].replace('/00/','/06/')
            replace(req,params=tuple(p.items()))


def test_transport_worker_archives_one_file_then_respects_shared_cooldown(gefs,monkeypatch):
    r=gefs;calls=[]
    def handle(req):
        calls.append(req);assert req.method=='GET' and 'authorization' not in req.headers
        return httpx.Response(200,content=grib(hour=r['plan'].hours[0]))
    w=worker(r,monkeypatch,httpx.MockTransport(handle))
    async def run():
        first=await w.step('one');again=await w.step('one');later=await w.step('two');await w.scheduled.collector.client.aclose()
        return first,again,later
    first,again,later=asyncio.run(run())
    assert first==again and len(calls)==1
    assert first['body']['details']['completed_fields']==1
    assert later['body']['details']['outcome']=='GEFS_SOURCE_PENDING' and later['body']['details']['omitted']==1
    assert not r['store'].records(kind='COORDINATOR_EVENT')


@pytest.mark.parametrize('response_kind',['oversized','html','compressed','rate_limit'])
def test_invalid_transport_or_rate_limit_never_enters_model_path(gefs,monkeypatch,response_kind):
    r=gefs;calls=[]
    def handle(req):
        calls.append(req)
        if response_kind=='rate_limit':return httpx.Response(429,headers={'Retry-After':'120'})
        if response_kind=='compressed':return httpx.Response(200,headers={'Content-Encoding':'gzip'},content=b'')
        return httpx.Response(200,content=b'GRIB'+b'x'*65536+b'7777' if response_kind=='oversized' else b'<html>busy</html>')
    w=worker(r,monkeypatch,httpx.MockTransport(handle))
    async def run():
        result=await w.step('bad');await w.scheduled.collector.client.aclose();return result
    d=asyncio.run(run())['body']['details']
    assert d['outcome']=='GEFS_SOURCE_PENDING' and len(calls)==1
    assert not r['store'].records(kind='MODEL')


def test_interruption_after_field_commit_resumes_without_duplicate_request(gefs,monkeypatch):
    r=gefs;calls=[]
    def handle(req):calls.append(req);return httpx.Response(200,content=grib(hour=r['plan'].hours[0]))
    w=worker(r,monkeypatch,httpx.MockTransport(handle));save=w._save
    def fail(key,state,**details):
        if details.get('outcome')=='GEFS_FIELD_ARCHIVED_PATH_INCOMPLETE':raise RuntimeError('INTERRUPTED')
        return save(key,state,**details)
    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(w,'_save',fail)
            with pytest.raises(RuntimeError):await w.step('one')
        head=r['store'].latest(kind='MODEL',event_id=r['plan'].event_id);advance(r)
        result=await GEFSWorker(w.scheduled,w.health,(r['plan'],)).step('resume')
        assert r['store'].get(result['body']['details']['normalized_id'])==head
        await w.scheduled.collector.client.aclose()
    asyncio.run(run());assert len(calls)==1


def test_complete_archived_run_worker_integrates_model_health_and_constituent_change_guard(gefs,monkeypatch):
    r=gefs;ids=all_fields(r);calls=[]
    def reject(req):calls.append(req);raise AssertionError('Existing fields must not be refetched')
    w=worker(r,monkeypatch,httpx.MockTransport(reject))
    async def run():
        for i in range(len(ids)):
            d=(await w.step('archive-'+str(i)))['body']['details']
            assert d['completed_fields']==i+1
        model=(await w.step('assemble'))['body']['details']
        assert model['outcome']=='RUN_BOUND_LINEAR_PATH_ARCHIVED_UNCALIBRATED'
        w.health.sample('complete-model-health')
        assert admission_heads(r['store'],account_id='account',event_id=r['plan'].event_id,strategies=('FUTURE_FORECAST',))
        previous=r['store'].get(model['model_id']);advance(r)
        assert (await w.step('again'))['body']['details']['outcome']=='COMPLETED_RUN_NO_REFETCH_OR_RECEIPT_RENEWAL'
        raw(r,label='new-member-receipt')
        with pytest.raises(EvidenceError,match='CONSTITUENT_CHANGED'):source.current_path_heads(r['store'],previous)
        w.health.sample('changed-model-health')
        with pytest.raises(EvidenceError,match='REQUIRED_SOURCE'):
            admission_heads(r['store'],account_id='account',event_id=r['plan'].event_id,strategies=('FUTURE_FORECAST',))
        assert (await w.step('changed'))['body']['details']['outcome']=='GEFS_SOURCE_GATED'
        assert r['store'].get(previous['id'])==previous
        await w.scheduled.collector.client.aclose()
    asyncio.run(run());assert not calls and not r['store'].records(kind='COORDINATOR_EVENT')


def test_whole_path_publication_is_atomic_against_a_new_model_receipt(gefs,monkeypatch):
    r=gefs;ids=all_fields(r);append=r['store']._append
    def race(key,*args,**kwargs):
        if key=='model':raw(r,label='racing')
        return append(key,*args,**kwargs)
    monkeypatch.setattr(r['store'],'_append',race)
    with pytest.raises(EvidenceError,match='STATE_CHANGED'):
        source.assemble_path(r['store'],plan=r['plan'],field_ids=ids,record_id='model')
    with pytest.raises(EvidenceError,match='EVIDENCE_MISSING'):r['store'].get('model')


def test_stale_pinned_run_does_not_trigger_useless_collection(gefs,monkeypatch):
    r=gefs;calls=[]
    def handle(req):calls.append(req);return httpx.Response(404)
    w=worker(r,monkeypatch,httpx.MockTransport(handle));advance(r,86400)
    # Reestablish only the synthetic clock after this explicit time advance.
    w.health.heartbeat('hb-late',worker='worker',generation='one')
    async def run():
        row=await w.step('old-run');await w.scheduled.collector.client.aclose();return row
    d=asyncio.run(run())['body']['details']
    assert d['reason']=='GEFS_RUN_PLAN_STALE_OR_FUTURE' and not calls


def test_interrupted_reserved_get_without_receipt_is_not_duplicated(gefs,monkeypatch):
    r=gefs;calls=[]
    w=worker(r,monkeypatch,httpx.MockTransport(lambda req:calls.append(req) or httpx.Response(404)))
    async def interrupted(cid,requests):
        r['store'].audit(cid+':schedule:0:reserve',event_id='source-host:nomads.ncep.noaa.gov',kind='SOURCE_SCHEDULE',details={})
        raise RuntimeError('INTERRUPTED')
    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(w.scheduled,'cycle',interrupted)
            with pytest.raises(RuntimeError):await w.step('reserved')
        row=await w.step('resume');await w.scheduled.collector.client.aclose();return row
    assert asyncio.run(run())['body']['details']['outcome']=='INTERRUPTED_COLLECTION_NOT_RETRIED'
    assert not calls


def test_assembly_deadline_never_publishes_a_partial_result(gefs):
    r=gefs;row=field(r,0,r['plan'].hours[0])
    with pytest.raises(EvidenceError,match='ASSEMBLY_TIME_BOUND'):
        source.assemble_path(r['store'],plan=r['plan'],field_ids=(row['id'],),record_id='expired',deadline=0.)
