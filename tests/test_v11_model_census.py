import asyncio
from dataclasses import replace

import httpx
import pytest

from polymarket_scanner.v11.book_inputs import BookPolicy
from polymarket_scanner.v11.census_worker import CensusWorker,CensusPlan,CensusPolicy
from polymarket_scanner.v11.evidence import EvidenceError,digest
from polymarket_scanner.v11.event_queue import EventQueue,_census_raw_receipt
from polymarket_scanner.v11.gefs_sources import assemble_path,current_path_heads
from polymarket_scanner.v11.model_census import ModelCensusStage,restore_plan
from test_v11_gefs_sources import gefs,worker,all_fields,raw
from test_v11_grib_fields import grib
from test_v11_book_inputs import response
from test_v11_paper_runtime import queue as make_queue
from test_v11_runtime_health import advance


def setup(r,monkeypatch,*,transport=None):
    r['rule']=r['plan'].rule;calls=[]
    def handle(req):
        calls.append(req)
        if req.url.host=='nomads.ncep.noaa.gov':
            file=req.url.params['file'];member=0 if file.startswith('gec') else int(file[3:5])
            return httpx.Response(200,content=grib(run=r['plan'].initialized_at,member=member,hour=int(file[-3:])))
        if req.url.host=='aviationweather.gov':
            return httpx.Response(200,json=[dict(icaoId='KATL',obsTime=r['now'][0]-1,temp=25)])
        return httpx.Response(200,json=response(r,req.url.params['token_id']))
    gw=worker(r,monkeypatch,transport or httpx.MockTransport(handle));old=make_queue(r)
    q=EventQueue(r['store'],routes=tuple(replace(p,required_source_kinds=('MODEL','OFFICIAL_OBSERVATION')) for p in old.routes.values()),
        policy=replace(old.policy,max_pending_age_seconds=1800.,max_rule_age_seconds=3600.,
            source_age_seconds=tuple((k,86400. if k=='MODEL' else v) for k,v in old.policy.source_age_seconds)))
    q.schedule_census('needed')
    r['store'].audit('rules',event_id=r['plan'].event_id,kind='RULE_STATE',details=dict(fingerprint=r['rule'].sha256,quarantined=False))
    cw=CensusWorker(gw.scheduled,q,gw.health,plans=(CensusPlan(r['rule'],'FIXTURE_COLLATERAL'),),
                   policy=CensusPolicy('fixture'),book_policy=BookPolicy('fixture'),gefs=gw)
    return q,cw,gw,calls


def begin(r,q,key='epoch'):
    row=q.begin_model_census(key,plan=r['plan'],expires_at=r['now'][0]+1200.)
    return row,row['body']['details']['result']['preparation']


def test_full_multistep_fresh_model_census_preserves_old_model_and_short_claim(gefs,monkeypatch):
    r=gefs;ids=all_fields(r);old=assemble_path(r['store'],plan=r['plan'],field_ids=ids,record_id='previous-model')
    q,cw,gw,calls=setup(r,monkeypatch);expected=31*len(r['plan'].hours)
    async def run():
        epoch=None
        for index in range(expected):
            result=await cw.step('field-'+str(index));d=result['body']['details']
            assert d['outcome']=='MODEL_CENSUS_COLLECTION_PENDING',d
            epoch=epoch or d['preparation_id'];assert d['preparation_id']==epoch
            assert q.snapshot()['active'] is None and q.preparing_model_events()==(r['plan'].event_id,)
            assert len(calls)==index+1 and calls[-1].url.host=='nomads.ncep.noaa.gov'
            assert await cw.step('field-'+str(index))==result and len(calls)==index+1
            advance(r)
        result=await cw.step('finish');d=result['body']['details']
        assert d['outcome']=='CENSUS_SOURCE_COVERAGE_ONLY',d
        assert len(calls)==expected+7 and not q.snapshot()['needs_census'] and not q.preparing_model_events()
        model=r['store'].get(d['source_ids'][-1]);barrier=r['store'].get(epoch)
        claim=r['store'].get('census-step:'+digest('finish')+':claim')
        assert barrier['seq']<claim['seq']<model['seq'] and model['body']['issued_at']==old['body']['issued_at']
        assert model['body']['received_at']<claim['body']['recorded_at']  # Original receipts, no renewal.
        assert claim['body']['details']['result']['claim']['deadline']-claim['body']['recorded_at']==q.policy.max_work_seconds
        for ref in model['body']['payload']['field_references']:
            field=r['store'].get(ref['id']);captured=r['store'].get(field['body']['payload']['raw_evidence_id'])
            assert barrier['seq']<captured['seq']<field['seq']<claim['seq']
        assert r['store'].get(old['id'])==old and not r['store'].records(kind='COORDINATOR_EVENT')
        with pytest.raises(EvidenceError,match='CONSTITUENT_CHANGED'):current_path_heads(r['store'],old)
        current_path_heads(r['store'],model)
        adopted=await gw.step('adopt-census-model')
        assert adopted['body']['details']['outcome']=='CURRENT_COMPLETE_RUN_ADOPTED_NO_REFETCH'
        assert adopted['body']['details']['model_id']==model['id'] and len(calls)==expected+7
        assert (await gw.step('already-complete'))['body']['details']['outcome']=='COMPLETED_RUN_NO_REFETCH_OR_RECEIPT_RENEWAL'
        assert r['store'].get(model['id'])==model
        await gw.scheduled.collector.client.aclose()
    asyncio.run(run())


@pytest.mark.parametrize('fault',['new_loss','expiry','clock_regression','wrong_id'])
def test_epoch_invalidation_cannot_clear_loss_or_reuse_pre_epoch_response(gefs,monkeypatch,fault):
    r=gefs;q,cw,gw,calls=setup(r,monkeypatch);barrier,p=begin(r,q)
    assert restore_plan(p)==r['plan']
    if fault=='new_loss':q.stream_gap('gap',event_id=r['plan'].event_id,reason='MODEL_SOURCE_GAP')
    if fault=='expiry':advance(r,1200.)
    if fault=='clock_regression':r['now'][0]-=1
    with pytest.raises(EvidenceError,match='EXPIRED_OR_LOSS_CHANGED'):
        q.model_preparation('wrong' if fault=='wrong_id' else barrier['id'],event_id=r['plan'].event_id)
    assert q.snapshot()['needs_census'] and not calls
    asyncio.run(gw.scheduled.collector.client.aclose())


def test_new_loss_restarts_fresh_collection_without_overwriting_partial_history(gefs,monkeypatch):
    r=gefs;q,cw,gw,calls=setup(r,monkeypatch)
    async def run():
        first=await cw.step('one');p=q.snapshot()['model_preparations'][r['plan'].event_id]
        old=r['store'].get(cw.model_stage.fields(p)[0]);advance(r)
        q.stream_gap('new-gap',event_id=r['plan'].event_id,reason='NETWORK_GAP')
        assert not q.preparing_model_events()
        second=await cw.step('two');fresh=q.snapshot()['model_preparations'][r['plan'].event_id]
        assert fresh['id']!=p['id'] and fresh['generation']>p['generation']
        assert cw.model_stage.fields(fresh)!=cw.model_stage.fields(p) and len(calls)==2
        assert r['store'].get(old['id'])==old and r['store'].get(first['id'])==first
        await gw.scheduled.collector.client.aclose()
    asyncio.run(run())


def test_staged_commit_recovery_does_not_duplicate_get_or_redate_field(gefs,monkeypatch):
    r=gefs;q,cw,gw,calls=setup(r,monkeypatch);barrier,p=begin(r,q);original=cw.model_stage._save
    def fail(key,prep,state,**details):
        if details.get('outcome')=='MODEL_FIELD_STAGED':raise RuntimeError('INTERRUPTED')
        return original(key,prep,state,**details)
    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(cw.model_stage,'_save',fail)
            with pytest.raises(RuntimeError):await cw.model_stage.step('first',preparation_id=barrier['id'],event_id=r['plan'].event_id)
        old=r['store'].latest(kind='MODEL',event_id=r['plan'].event_id);advance(r)
        recovered=await ModelCensusStage(q,gw.scheduled).step('recover',preparation_id=barrier['id'],event_id=r['plan'].event_id)
        assert recovered['body']['details']['normalized_id']==old['id'] and len(calls)==1
        assert r['store'].get(old['id'])==old
        await gw.scheduled.collector.client.aclose()
    asyncio.run(run())


def test_reserved_request_without_receipt_is_reconciled_without_retry(gefs,monkeypatch):
    r=gefs;q,cw,gw,calls=setup(r,monkeypatch);barrier,p=begin(r,q)
    async def interrupted(cid,requests):
        r['store'].audit(cid+':schedule:0:reserve',event_id='source-host:nomads.ncep.noaa.gov',kind='SOURCE_SCHEDULE',details={})
        raise RuntimeError('INTERRUPTED')
    async def run():
        with monkeypatch.context() as patch:
            patch.setattr(gw.scheduled,'cycle',interrupted)
            with pytest.raises(RuntimeError):await cw.model_stage.step('first',preparation_id=barrier['id'],event_id=r['plan'].event_id)
        result=await cw.model_stage.step('recover',preparation_id=barrier['id'],event_id=r['plan'].event_id)
        assert result['body']['details']['outcome']=='INTERRUPTED_MODEL_GET_NOT_RETRIED' and not calls
        await gw.scheduled.collector.client.aclose()
    asyncio.run(run())


def test_aggregate_cannot_repackage_pre_claim_fields_as_fresh_without_collection_epoch(gefs):
    r=gefs;ids=all_fields(r)
    claim=r['store'].audit('claim',event_id=r['plan'].event_id,kind='RUNTIME_STATUS',details={})
    model=assemble_path(r['store'],plan=r['plan'],field_ids=ids,record_id='late-model')
    with pytest.raises(EvidenceError,match='MODEL_FIELD_LINEAGE'):_census_raw_receipt(r['store'],model,claim)


def test_same_run_correction_requires_every_field_to_be_new_and_preserves_old_value(gefs):
    r=gefs;ids=all_fields(r);first=assemble_path(r['store'],plan=r['plan'],field_ids=ids,record_id='first-model')
    with pytest.raises(EvidenceError,match='SAME_RUN_REPLACEMENT_REQUIRES_ALL_NEW_FIELDS'):
        assemble_path(r['store'],plan=r['plan'],field_ids=ids,record_id='relabel-old')
    assert r['store'].get(first['id'])==first


def test_concurrent_stage_cannot_clear_or_duplicate_an_inflight_request(gefs,monkeypatch):
    r=gefs;entered=asyncio.Event();release=asyncio.Event();calls=[]
    async def blocked(req):
        calls.append(req);entered.set();await release.wait()
        return httpx.Response(200,content=grib(hour=r['plan'].hours[0]))
    q,cw,gw,_=setup(r,monkeypatch,transport=httpx.MockTransport(blocked));barrier,p=begin(r,q)
    async def run():
        task=asyncio.create_task(cw.model_stage.step('first',preparation_id=barrier['id'],event_id=r['plan'].event_id))
        await asyncio.wait_for(entered.wait(),2)
        with pytest.raises(EvidenceError,match='ALREADY_RUNNING'):
            await ModelCensusStage(q,gw.scheduled).step('second',preparation_id=barrier['id'],event_id=r['plan'].event_id)
        release.set();row=await task
        assert row['body']['details']['outcome']=='MODEL_FIELD_STAGED' and len(calls)==1
        await gw.scheduled.collector.client.aclose()
    asyncio.run(run())


def test_model_census_failure_does_not_bypass_shared_provider_backoff(gefs,monkeypatch):
    r=gefs;calls=[]
    q,cw,gw,_=setup(r,monkeypatch,transport=httpx.MockTransport(lambda req:calls.append(req) or httpx.Response(503)))
    async def run():
        first=await cw.step('outage');advance(r)
        second=await cw.step('cooldown')
        for row in (first,second):
            assert row['body']['details']['model_stage_outcome']=='MODEL_FIELD_SOURCE_PENDING'
        assert len(calls)==1 and q.snapshot()['needs_census'] and not r['store'].records(kind='MODEL')
        await gw.scheduled.collector.client.aclose()
    asyncio.run(run())
