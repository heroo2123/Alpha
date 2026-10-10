import asyncio
from dataclasses import replace

import httpx
import pytest

from polymarket_scanner.v11.book_inputs import BookPolicy
from polymarket_scanner.v11.census_worker import CensusWorker,CensusPlan,CensusPolicy
from polymarket_scanner.v11.evidence import EvidenceError,canonical,digest
from polymarket_scanner.v11.event_queue import EventQueue,_census_raw_receipt
from polymarket_scanner.v11.gefs_sources import assemble_path,current_path_heads
from polymarket_scanner.v11.model_census import ModelCensusStage,restore_plan
from test_v11_gefs_sources import gefs,worker,all_fields,raw
from test_v11_grib_fields import grib
from test_v11_book_inputs import response
from test_v11_paper_runtime import queue as make_queue
from test_v11_runtime_health import advance


def setup(r,monkeypatch,*,transport=None,pending_age=1800.):
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
        policy=replace(old.policy,max_pending_age_seconds=pending_age,max_rule_age_seconds=3600.,
            source_age_seconds=tuple((k,86400. if k=='MODEL' else v) for k,v in old.policy.source_age_seconds)))
    q.schedule_census('needed')
    r['store'].audit('rules',event_id=r['plan'].event_id,kind='RULE_STATE',details=dict(fingerprint=r['rule'].sha256,quarantined=False))
    cw=CensusWorker(gw.scheduled,q,gw.health,plans=(CensusPlan(r['rule'],'FIXTURE_COLLATERAL'),),
                   policy=CensusPolicy('fixture'),book_policy=BookPolicy('fixture'),gefs=gw)
    return q,cw,gw,calls


def begin(r,q,key='epoch'):
    row=q.begin_model_census(key,plan=r['plan'],expires_at=r['now'][0]+1200.)
    return row,row['body']['details']['result']['preparation']


def test_model_epoch_survives_short_event_notice_ttl_without_extending_market_freshness(gefs,monkeypatch):
    r=gefs
    q,cw,gw,calls=setup(r,monkeypatch,pending_age=300.)
    assert q.policy.max_pending_age_seconds==300.
    async def run():
        first=await cw.step('ttl-first')
        assert first['body']['details']['outcome']=='MODEL_CENSUS_COLLECTION_PENDING'
        p=q.snapshot()['model_preparations'][r['plan'].event_id]
        # Old source allocated 300 seconds, which expired before 31-member
        # GEFS collection could complete and repeatedly restarted the epoch.
        assert p['expires_at']-p['began_at']==1800.
        assert len(cw.model_stage.fields(p))==1 and len(calls)==1
        # Exercise the actual market-notice admission/expiry functions, not
        # merely the stored policy constant. This temporary local snapshot
        # does not manufacture source coverage or commit a live notice.
        _,notice_state=q._read()
        received=r['now'][0]
        event=r['plan'].event_id
        notice={'channel':'MODEL:unit-test','received_at':received,
                'valid_until':received+1000.,'priority':1}
        assert q._enqueue(notice_state,q.routes[event],notice,received)
        assert notice_state['pending'][event]['expires_at']==received+300.
        advance(r,350.)
        q._expire(notice_state,r['now'][0])
        assert event not in notice_state['pending']
        assert notice_state['metrics']['expired']==1
        # The independent MODEL epoch survives, while the isolated market
        # event notice has correctly expired under its unchanged TTL.
        recovered=q.model_preparation(p['id'],event_id=event)[1]
        assert recovered['id']==p['id'] and q.policy.max_pending_age_seconds==300.
        await gw.scheduled.collector.client.aclose()
    asyncio.run(run())


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


def test_census_persistence_bytes_are_linear_not_quadratic(gefs,monkeypatch):
    """Guard the O(n^2)->O(n) fix: total persisted bytes for a full pass must
    stay within a small constant factor of a delta-only payload, and far below
    what the old full-list-per-row format would have cost for the same run."""
    r=gefs;q,cw,gw,calls=setup(r,monkeypatch);expected=31*len(r['plan'].hours)
    async def run():
        for index in range(expected):
            result=await cw.step('bytes-'+str(index));d=result['body']['details']
            assert d['outcome']=='MODEL_CENSUS_COLLECTION_PENDING',d
            advance(r)
        finished=await cw.step('bytes-finish')
        assert finished['body']['details']['outcome']=='CENSUS_SOURCE_COVERAGE_ONLY',finished['body']['details']
        await gw.scheduled.collector.client.aclose()
    asyncio.run(run())
    rows=r['store'].records(kind='RUNTIME_STATUS',event_id='model-census:'+r['plan'].event_id,limit=1000)
    assert len(rows)>=2*expected
    total_new=sum(len(canonical(row['body']).encode()) for row in rows)
    minimal_total=0;legacy_total=0;cumulative=[]
    for row in rows:
        details=dict(row['body']['details']);c=details.pop('census',None)
        if c is None:
            size=len(canonical(row['body']).encode());minimal_total+=size;legacy_total+=size;continue
        field=c.get('field_id')
        minimal_details=dict(details,census=dict(field_id=field))
        minimal_total+=len(canonical(dict(row['body'],details=minimal_details)).encode())
        if field:cumulative.append(field)
        legacy_details=dict(details,state=dict(field_ids=list(cumulative),active=c.get('active')))
        legacy_total+=len(canonical(dict(row['body'],details=legacy_details)).encode())
    print(f'CENSUS_BYTES slots={expected} rows={len(rows)} new_total_bytes={total_new} '
          f'minimal_delta_only_bytes={minimal_total} legacy_full_list_bytes={legacy_total} '
          f'new_over_minimal={total_new/minimal_total:.2f}x legacy_over_new={legacy_total/total_new:.2f}x')
    assert total_new<2*minimal_total,(total_new,minimal_total)
    assert total_new<legacy_total/5,(total_new,legacy_total)


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


def _staged(r,monkeypatch,count):
    q,cw,gw,calls=setup(r,monkeypatch);barrier,_=begin(r,q);stage=cw.model_stage
    async def run():
        for index in range(count):
            row=await stage.step('stage-'+str(index),preparation_id=barrier['id'],event_id=r['plan'].event_id)
            assert row['body']['details']['outcome']=='MODEL_FIELD_STAGED',row['body']['details'];advance(r)
    asyncio.run(run())
    _,p=q.model_preparation(barrier['id'],event_id=r['plan'].event_id)
    return q,gw,stage,barrier,p


def _forge(r,stage,p,key,**changes):
    head=stage._head(p['event_id']);census=dict(head['body']['details']['census'])
    census.update(chain_previous_id=head['id'],inherited_field_ids=None,field_id=None,active=None);census.update(changes)
    return r['store'].audit(key,event_id='model-census:'+p['event_id'],kind='RUNTIME_STATUS',
        details=dict(version='forged',preparation_id=p['id'],census=census,financial_authority=False),
        evidence_ids=(p['id'],),expected_previous_seq=head['seq'])


@pytest.mark.parametrize('case,code',[
    ('digest','MODEL_CENSUS_CHAIN_DIGEST_MISMATCH'),('missing_previous','MODEL_CENSUS_CHAIN_GAP'),
    ('count','MODEL_CENSUS_CHAIN_GAP'),('duplicate','MODEL_CENSUS_CHAIN_DUPLICATE_FIELD'),
    ('foreign','MODEL_CENSUS_CHAIN_FOREIGN_ROW'),('foreign_preparation','MODEL_CENSUS_CHAIN_FOREIGN_ROW'),('format','MODEL_CENSUS_CHAIN_FORMAT_INVALID'),
    ('inherited_after_genesis','MODEL_CENSUS_CHAIN_FORMAT_INVALID'),
    ('non_advancing_middle','MODEL_CENSUS_CHAIN_FORMAT_INVALID'),('legacy_invalid','MODEL_CENSUS_LEGACY_STATE_INVALID')])
def test_corrupted_census_chain_fails_closed(gefs,monkeypatch,case,code):
    from polymarket_scanner.v11.model_census import _chain_digest
    r=gefs;q,gw,stage,barrier,p=_staged(r,monkeypatch,2);before=stage.fields(p);assert len(before)==2
    c=stage._head(p['event_id'])['body']['details']['census']
    if case=='digest':_forge(r,stage,p,'f',field_id='forged-field',count=c['count']+1,digest='0'*64)
    elif case=='missing_previous':_forge(r,stage,p,'f',chain_previous_id='model-census-missing-row')
    elif case=='count':_forge(r,stage,p,'f',count=c['count']+1)
    elif case=='duplicate':_forge(r,stage,p,'f',field_id=before[0],count=c['count']+1,digest=_chain_digest(c['digest'],before[0]))
    elif case=='foreign':_forge(r,stage,p,'f',chain_previous_id=barrier['id'])
    elif case=='foreign_preparation':
        head=stage._head(p['event_id'])
        other=r['store'].audit('other-prep',event_id='model-census:'+p['event_id'],kind='RUNTIME_STATUS',
            details=dict(head['body']['details'],preparation_id='other-preparation'),
            evidence_ids=(p['id'],),expected_previous_seq=head['seq'])
        _forge(r,stage,p,'f',chain_previous_id=other['id'])
    elif case=='format':_forge(r,stage,p,'f',format='alpha_v11_model_census_chain_v1')
    elif case=='inherited_after_genesis':_forge(r,stage,p,'f',inherited_field_ids=[])
    elif case=='non_advancing_middle':_forge(r,stage,p,'f1');_forge(r,stage,p,'f2')
    else:
        head=stage._head(p['event_id'])
        r['store'].audit('legacy',event_id='model-census:'+p['event_id'],kind='RUNTIME_STATUS',
            details=dict(version='legacy',preparation_id=p['id'],state=dict(field_ids='not-a-list',active=None),financial_authority=False),
            evidence_ids=(p['id'],),expected_previous_seq=head['seq'])
    with pytest.raises(EvidenceError,match=code) as caught:stage.fields(p)
    assert str(caught.value)==code
    with pytest.raises(EvidenceError) as caught:
        asyncio.run(stage.step('after-'+case,preparation_id=barrier['id'],event_id=r['plan'].event_id))
    assert str(caught.value)==code
    asyncio.run(gw.scheduled.collector.client.aclose())


def test_legacy_full_list_head_resumes_once_then_writes_deltas(gefs,monkeypatch):
    r=gefs;q,gw,stage,barrier,p=_staged(r,monkeypatch,1);first=stage.fields(p);head=stage._head(p['event_id'])
    r['store'].audit('legacy',event_id='model-census:'+p['event_id'],kind='RUNTIME_STATUS',
        details=dict(version='alpha_v11_model_census_stage_v1',preparation_id=p['id'],
                     state=dict(field_ids=list(first),active=None),financial_authority=False),
        evidence_ids=(p['id'],),expected_previous_seq=head['seq'])
    assert stage.fields(p)==first
    async def run():
        for index in range(2):
            row=await stage.step('resume-'+str(index),preparation_id=barrier['id'],event_id=r['plan'].event_id)
            assert row['body']['details']['outcome']=='MODEL_FIELD_STAGED';advance(r)
        await gw.scheduled.collector.client.aclose()
    asyncio.run(run())
    fields=stage.fields(p);assert len(fields)==3 and fields[0]==first[0] and len(set(fields))==3
    rows=r['store'].records(kind='RUNTIME_STATUS',event_id='model-census:'+p['event_id'],limit=100)
    after=[row['body']['details']['census'] for row in rows if row['seq']>r['store'].get('legacy')['seq']]
    assert [c['inherited_field_ids'] for c in after]==[list(first)]+[None]*(len(after)-1)


def test_pending_rows_never_lengthen_the_replay_walk(gefs,monkeypatch):
    r=gefs;q,gw,stage,barrier,p=_staged(r,monkeypatch,1);first=stage.fields(p)
    async def pending(cid,requests):return dict(sources=[dict(state='PENDING')],omitted=[])
    monkeypatch.setattr(gw.scheduled,'cycle',pending)
    async def run():
        for index in range(40):
            row=await stage.step('pending-'+str(index),preparation_id=barrier['id'],event_id=r['plan'].event_id)
            assert row['body']['details']['outcome']=='MODEL_FIELD_SOURCE_PENDING'
    asyncio.run(run())
    gets=[];real=stage.store.get
    monkeypatch.setattr(stage.store,'get',lambda key:gets.append(key) or real(key))
    assert stage.fields(p)==first and len(gets)<=2,len(gets)
    monkeypatch.undo();asyncio.run(gw.scheduled.collector.client.aclose())
