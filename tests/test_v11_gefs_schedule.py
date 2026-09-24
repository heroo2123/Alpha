import asyncio
from dataclasses import replace
from datetime import date

import httpx
import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.gefs_runtime import GEFSWorker
from polymarket_scanner.v11.gefs_schedule import GEFSRunPolicy, requested_plan
from polymarket_scanner.v11.gefs_sources import GEFSPlan
from polymarket_scanner.v11.rules import fingerprint_event
from test_v11_gefs_sources import gefs, worker
from test_v11_grib_fields import grib, RUN
from test_v11_runtime_health import advance
from test_weather_final_gpt6_exact_replays import _event


@pytest.fixture
def future(gefs):
    r=gefs;m=r['plan'].forecast.metadata
    rule=fingerprint_event(_event(station='KATL',target=date(2026,9,15)),station_timezone=m.timezone,metadata_fingerprint=m.fingerprint)
    r['plan']=GEFSPlan(replace(r['plan'].forecast,rule=rule),RUN)
    return r


@pytest.mark.parametrize('lag',[None,True,0.,3599.,21601.,float('inf')])
def test_rollover_requires_explicit_bounded_request_lag(lag):
    with pytest.raises(EvidenceError):GEFSRunPolicy('fixture',lag)


@pytest.mark.parametrize('offset,expected',[(6*3600-1,0),(7*3600-1,0),(7*3600,6*3600),(13*3600,12*3600),(25*3600,24*3600)])
def test_request_schedule_selects_six_hour_run_after_lag_without_claiming_availability(future,offset,expected):
    p=requested_plan(future['plan'],GEFSRunPolicy('fixture',3600.),now=RUN+offset)
    assert p.initialized_at==RUN+expected and p.forecast==future['plan'].forecast
    assert p.initialized_at<=p.window[0] and len(p.hours)*31<=341


def test_day_start_cap_keeps_full_forecast_contract_and_stale_or_ended_day_gates(future):
    p=future['plan'];policy=GEFSRunPolicy('fixture',3600.)
    limited=requested_plan(p,policy,now=RUN+40*3600)
    assert limited.initialized_at==RUN+24*3600  # Midnight UTC precedes local midnight.
    with pytest.raises(EvidenceError,match='TARGET_WINDOW_ENDED'):
        requested_plan(p,policy,now=p.window[1])
    with pytest.raises(EvidenceError,match='STALE_OR_FUTURE'):
        requested_plan(replace(p,maximum_run_age_seconds=60.),policy,now=RUN+25*3600)


def test_schedule_never_rolls_back_recovered_run_or_changes_seed_scope(future):
    p=future['plan'];policy=GEFSRunPolicy('fixture',3600.)
    assert requested_plan(p,policy,now=RUN+7*3600,previous_initialization=RUN+6*3600).initialized_at==RUN+6*3600
    with pytest.raises(EvidenceError,match='HISTORY_BEFORE_SEED'):
        requested_plan(p,policy,now=RUN+7*3600,previous_initialization=RUN-6*3600)
    with pytest.raises(EvidenceError,match='EXACT_INITIALIZATION'):
        requested_plan(p,policy,now=RUN+7*3600,previous_initialization=RUN+10)


def rolling(r,monkeypatch,transport):
    w=worker(r,monkeypatch,transport)
    return GEFSWorker(w.scheduled,w.health,(r['plan'],),rollover=GEFSRunPolicy('fixture',3600.))


def handler(r,calls):
    def request(req):
        calls.append(req)
        p=req.url.params;cycle=int(p['file'].split('.t')[1][:2]);hour=int(p['file'][-3:])
        member=0 if p['file'].startswith('gec') else int(p['file'][3:5])
        return httpx.Response(200,content=grib(run=RUN+cycle*3600,member=member,hour=hour))
    return request


def test_partial_run_rollover_preserves_original_objects_and_uses_distinct_run_inputs(future,monkeypatch):
    r=future;calls=[];w=rolling(r,monkeypatch,httpx.MockTransport(handler(r,calls)))
    async def run():
        first=await w.step('first');old=r['store'].get(first['body']['details']['normalized_id'])
        advance(r,6*3600)
        transition=await w.step('new-run');d=transition['body']['details']
        assert d['outcome']=='GEFS_REQUESTED_RUN_ADVANCED' and d['previous_field_count']==1
        assert not d['provider_availability_verified'] and len(calls)==1
        assert r['store'].get(d['previous_state_id'])==first
        assert await w.step('new-run')==transition and len(calls)==1
        resumed=GEFSWorker(w.scheduled,w.health,(r['plan'],),rollover=w.rollover)
        second=await resumed.step('next-field');new=r['store'].get(second['body']['details']['normalized_id'])
        assert new['body']['issued_at']==RUN+6*3600 and new['id']!=old['id']
        assert old==r['store'].get(old['id']) and new['body']['received_at']>old['body']['received_at']
        assert len(calls)==2 and not r['store'].records(kind='COORDINATOR_EVENT')
        await w.scheduled.collector.client.aclose()
    asyncio.run(run())


def test_interrupted_old_run_commit_is_reconciled_before_advancing_schedule(future,monkeypatch):
    r=future;calls=[];w=rolling(r,monkeypatch,httpx.MockTransport(handler(r,calls)));save=w._save
    def interrupted(key,state,**details):
        if details.get('outcome')=='GEFS_FIELD_ARCHIVED_PATH_INCOMPLETE':raise RuntimeError('INTERRUPTED')
        return save(key,state,**details)
    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(w,'_save',interrupted)
            with pytest.raises(RuntimeError):await w.step('interrupted')
        original=r['store'].latest(kind='MODEL',event_id=r['plan'].event_id)
        advance(r,6*3600)
        recovered=await w.step('recover')
        assert recovered['body']['details']['normalized_id']==original['id'] and len(calls)==1
        assert (await w.step('advance'))['body']['details']['outcome']=='GEFS_REQUESTED_RUN_ADVANCED'
        assert len(calls)==1 and r['store'].get(original['id'])==original
        await w.scheduled.collector.client.aclose()
    asyncio.run(run())


def test_unavailable_requested_run_respects_existing_provider_cooldown(future,monkeypatch):
    r=future;calls=[];w=rolling(r,monkeypatch,httpx.MockTransport(lambda req:calls.append(req) or httpx.Response(404)))
    advance(r,6*3600)
    async def run():
        transition=await w.step('select');assert transition['body']['details']['outcome']=='GEFS_REQUESTED_RUN_ADVANCED'
        first=await w.step('missing');second=await w.step('still-missing')
        assert first['body']['details']['outcome']==second['body']['details']['outcome']=='GEFS_SOURCE_PENDING'
        assert len(calls)==1 and second['body']['details']['omitted']==1
        assert not r['store'].records(kind='MODEL')
        await w.scheduled.collector.client.aclose()
    asyncio.run(run())


def test_changed_rollover_policy_cannot_replace_existing_runtime_identity(future,monkeypatch):
    r=future;w=rolling(r,monkeypatch,httpx.MockTransport(handler(r,[])))
    async def run():
        await w.step('seed');pin=r['store'].pin_read_view()
        changed=GEFSWorker(w.scheduled,w.health,(r['plan'],),rollover=GEFSRunPolicy('changed',7200.))
        with pytest.raises(EvidenceError,match='CONFIG_CHANGED_REVIEW_REQUIRED'):await changed.step('changed')
        assert r['store'].pin_read_view()==pin
        await w.scheduled.collector.client.aclose()
    asyncio.run(run())
