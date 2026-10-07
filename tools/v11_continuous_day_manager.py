#!/usr/bin/env python3
from __future__ import annotations
import asyncio,fcntl,importlib,json,os,sqlite3,time,traceback
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import httpx

from daily_evidence_rollover import (
    ROTATE_AT_BYTES, archive_bytes, perform_pending_rotation,
    request_rotation, rotation_requested,
)

BASE=Path('/home/alphaadmin/AlphaV11_ForwardShadow/continuous-shadow-v2')
STATUS=BASE/'daily-manager-status.json'
ERRORS=BASE/'daily-manager-errors.log'
LOCK=BASE/'daily-manager.lock'
ATL=ZoneInfo('America/New_York')
MIDDAY_ROTATION_ENABLED=False

def save(value):
    tmp=STATUS.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,indent=2,sort_keys=True,default=str)+'\n')
    os.chmod(tmp,0o600);os.replace(tmp,STATUS)

def log_error(exc,context):
    with ERRORS.open('a') as f:
        f.write(f'\n[{time.time()}] {context} {type(exc).__name__}: {exc}\n')
        traceback.print_exc(file=f)

def protected_review_ready(day):
    dbp=BASE/f'daily-{day}.sqlite'
    if not dbp.exists(): return False,None,None
    try:
        db=sqlite3.connect(dbp);db.row_factory=sqlite3.Row
        rows=db.execute("select body from v11_records where kind='RULE_STATE' order by seq desc").fetchall()
        chosen=None
        for row in rows:
            body=json.loads(row['body']);d=body.get('details',{});p=d.get('preimage',{})
            if p.get('target_date')==day and not d.get('quarantined') and not d.get('changed'):
                chosen=d;break
        db.close()
        if chosen is None:return False,None,None
        manifest=json.loads(Path('/etc/alpha-v11/approvals/station-capabilities.json').read_text())
        hits=[r for r in manifest.get('reviews',[]) if r.get('namespace')=='CHALLENGER:katl-shadow'
              and r.get('stage')=='SHADOW'
              and r.get('scope_key')=='f110f6d088bd72d5b72234787f613ee7f9140bb3d48c6319d493b575b8c4351f'
              and r.get('metadata_fingerprint')==chosen['preimage']['metadata_fingerprint']
              and r.get('rule_fingerprint')==chosen['fingerprint']
              and float(r.get('expires_at',0))>time.time()]
        return len(hits)==1,chosen['fingerprint'],hits[0].get('review_id') if len(hits)==1 else None
    except Exception:
        return False,None,None

async def refresh_rule_if_due(store,m,client,*,refresh_after_seconds=21600.):
    """Refresh public rule receipt without changing reviewed semantics.

    A changed fingerprint is durably observed by RuleGuard and remains fail-closed.
    Transient transport failures do not bypass preflight; the existing receipt is
    usable only until its normal freshness bound expires.
    """
    from polymarket_scanner.v11.evidence import EvidenceError,digest
    from polymarket_scanner.v11.rules import RuleGuard,fingerprint_event
    last=store.latest(kind='RULE_STATE',event_id=m.EVENT_ID)
    if last is None:return None
    d=last['body']['details'];now=store.clock()
    age=max(now-last['body']['recorded_at'],now-d.get('source_received_at',last['body']['recorded_at']))
    if age < refresh_after_seconds:return None
    metadata,expected_rule,scope=m.load_context(store)
    refs=last['body'].get('evidence',[])
    slug=None
    if refs:
        try:
            prior=store.get(refs[0]['id'])
            event=prior['body'].get('payload',{}).get('event')
            if isinstance(event,dict):slug=event.get('slug')
        except EvidenceError:
            pass
    if not slug:
        dt=datetime.fromisoformat(expected_rule.payload['target_date'])
        slug=f"highest-temperature-in-atlanta-on-{dt.strftime('%B').lower()}-{dt.day}-{dt.year}"
    response=await client.get('https://gamma-api.polymarket.com/events',params={'slug':slug})
    if response.status_code!=200:raise httpx.HTTPStatusError(
        'RULE_REFRESH_HTTP_'+str(response.status_code),request=response.request,response=response)
    rows=response.json()
    if type(rows) is not list or len(rows)!=1 or str(rows[0].get('id'))!=m.EVENT_ID:
        raise RuntimeError('RULE_REFRESH_EXACT_EVENT_REQUIRED')
    event=rows[0]
    if event.get('active') is not True or event.get('closed') is not False or event.get('archived',False) is not False:
        raise RuntimeError('RULE_REFRESH_EVENT_NOT_ACTIVE_OPEN')
    fresh=fingerprint_event(event,station_timezone=metadata.timezone,metadata_fingerprint=metadata.fingerprint)
    bucket=int(now//300);rows_sha=digest(rows);event_sha=digest(event)
    full_id=f'runtime-refresh:gamma-full:{m.EVENT_ID}:{bucket}:{rows_sha}'
    raw_id=f'runtime-refresh:gamma-event:{m.EVENT_ID}:{bucket}:{event_sha}'
    state_id=f'runtime-refresh:rule:{m.EVENT_ID}:{bucket}:{event_sha}'
    try:full=store.get(full_id)
    except EvidenceError as exc:
        if str(exc)!='EVIDENCE_MISSING':raise
        full=store.capture(full_id,event_id=m.EVENT_ID,kind='RULES',provider='GAMMA_EVENT_LIST',
            source_identity='event:'+m.EVENT_ID,revision='public-gamma:'+rows_sha,
            payload={'endpoint':'https://gamma-api.polymarket.com/events','request_params':{'slug':slug},
                     'http_status':200,'response':rows},evidence_class='PUBLIC_OBSERVED')
    try:raw=store.get(raw_id)
    except EvidenceError as exc:
        if str(exc)!='EVIDENCE_MISSING':raise
        raw=store.capture(raw_id,event_id=m.EVENT_ID,kind='RULES',provider='GAMMA_EVENT',
            source_identity='event:'+m.EVENT_ID,revision=full['sha256'],
            payload={'event':event,'source_capture_id':full['id'],'source_capture_sha256':full['sha256']},
            evidence_class='PUBLIC_OBSERVED')
    try:state=store.get(state_id)
    except EvidenceError as exc:
        if str(exc)!='EVIDENCE_MISSING':raise
        state=RuleGuard(store).observe(state_id,fresh,raw_evidence_id=raw['id'])
    sd=state['body']['details']
    if fresh.sha256!=expected_rule.sha256 or sd.get('quarantined') or sd.get('changed'):
        raise RuntimeError('RULE_REFRESH_CHANGED_OR_QUARANTINED')
    return state

async def serve_day(day,module_name):
    m=importlib.import_module(module_name)
    from polymarket_scanner.v11.shadow_commission import ShadowCommissionRunner,ShadowLifecyclePolicy
    async with httpx.AsyncClient(
        headers={'User-Agent':f'Alpha-V11-KATL-continuous-{day}/1'},
        timeout=10.0,follow_redirects=False) as client:
        store,plan,runner,shadow,gefs=m.build_runner(client)
        commission=ShadowCommissionRunner(
            shadow,runner,release_git_sha=m.RELEASE,
            lifecycle=ShadowLifecyclePolicy(f'katl-continuous-single-owner-{day}',1,60.))
        iteration=0
        while datetime.now(ATL).date().isoformat()==day:
            current_archive_bytes=archive_bytes(m.DB)
            if MIDDAY_ROTATION_ENABLED and (rotation_requested(day) or current_archive_bytes >= ROTATE_AT_BYTES):
                if not rotation_requested(day):
                    request_rotation(day,m.EVENT_ID,reason='ARCHIVE_ROTATION_THRESHOLD')
                save({'state':'EVIDENCE_ROTATION_REQUESTED','day':day,'event_id':m.EVENT_ID,
                      'iteration':iteration,'at':time.time(),'archive_bytes':current_archive_bytes,
                      'rotate_at_bytes':ROTATE_AT_BYTES,'financial_authority':False,'real_orders_sent':False})
                raise SystemExit(75)
            iteration+=1
            try:
                try:
                    await refresh_rule_if_due(store,m,client)
                except (httpx.HTTPError,OSError,TimeoutError) as exc:
                    log_error(exc,f'rule-refresh-transport day={day} iteration={iteration}')
                pre=commission.preflight()
                if not pre['passed']:
                    save({'state':'PREFLIGHT_GATED','day':day,'event_id':m.EVENT_ID,
                          'iteration':iteration,'at':time.time(),'preflight':pre,
                          'financial_authority':False,'real_orders_sent':False})
                    await asyncio.sleep(30);continue
                snap=runner.runtime.queue.snapshot()
                active=snap.get('active') or {}
                need=(m.EVENT_ID in snap.get('needs_census',{})
                      or m.EVENT_ID in snap.get('model_preparations',{})
                      or active.get('event_id')==m.EVENT_ID)
                if need:
                    cycle=f'daily-owner-census:{day}:{iteration}:{int(time.time())}'
                    row=await runner.census.step(cycle)
                    details=row['body']['details']
                    snap=runner.runtime.queue.snapshot()
                    prep=snap.get('model_preparations',{}).get(m.EVENT_ID)
                    count=None
                    if prep is not None:
                        try:count=len(runner.census.model_stage.fields(prep))
                        except Exception:count=None
                    save({'state':'CENSUS_SERVICE','day':day,'event_id':m.EVENT_ID,'iteration':iteration,
                          'at':time.time(),'outcome':details.get('outcome'),'record_id':row['id'],
                          'completed_fields':count,'required_fields':310,
                          'needs_census':m.EVENT_ID in snap.get('needs_census',{}),
                          'preparation_id':prep.get('id') if prep else None,
                          'errors':details.get('errors',[]),'financial_authority':False,'real_orders_sent':False})
                    await asyncio.sleep(1.05 if details.get('outcome')=='MODEL_CENSUS_COLLECTION_PENDING' else 2.)
                    continue
                run_id=f'daily-owner-run:{day}:{iteration}:{int(time.time())}'
                result=await commission.run_once(run_id)
                details=result['body']['details']
                snap=runner.runtime.queue.snapshot()
                decisions=store.records(kind='DECISION',event_id=m.EVENT_ID,limit=1000)
                save({'state':'CANDIDATE_SERVICE','day':day,'event_id':m.EVENT_ID,'iteration':iteration,
                      'at':time.time(),'candidate_result_id':result['id'],
                      'candidate_result_sha256':result['sha256'],'outcome':details.get('outcome'),
                      'end_reason':details.get('end_reason'),'errors':details.get('errors',[]),
                      'worker_results':details.get('worker_results',[]),
                      'runtime_outcome_counts':details.get('runtime_outcome_counts',{}),
                      'clock_healthy_at_finish':details.get('clock_healthy_at_finish'),
                      'all_async_jobs_drained':details.get('all_async_jobs_drained'),
                      'decision_count':len(decisions),
                      'needs_census':m.EVENT_ID in snap.get('needs_census',{}),
                      'financial_authority':False,'real_orders_sent':False})
                # Full candidate cycles do not need a two-second cadence. Fresh model-census
                # work above remains immediate, while bounded decision cycles run every five
                # minutes to preserve causal evidence without exhausting the daily archive.
                await asyncio.sleep(300.)
            except Exception as exc:
                log_error(exc,f'day={day} iteration={iteration}')
                save({'state':'ITERATION_GATED','day':day,'event_id':m.EVENT_ID,'iteration':iteration,
                      'at':time.time(),'error_type':type(exc).__name__,'error':str(exc),
                      'financial_authority':False,'real_orders_sent':False})
                await asyncio.sleep(10)
        save({'state':'DAY_ROTATION','completed_day':day,'at':time.time(),
              'financial_authority':False,'real_orders_sent':False})

async def main():
    fd=os.open(LOCK,os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('DAILY_SHADOW_MANAGER_ALREADY_RUNNING')
        while True:
            day=datetime.now(ATL).date().isoformat()
            module='katl_continuous_shadow_'+day.replace('-','')
            module_path=BASE/(module+'.py')
            if not module_path.exists():
                save({'state':'WAITING_PREPARED_DAY','day':day,'at':time.time(),
                      'expected_module':str(module_path),'financial_authority':False,'real_orders_sent':False})
                await asyncio.sleep(60)
                continue
            try:
                rotated=perform_pending_rotation(day) if MIDDAY_ROTATION_ENABLED else None
                if rotated is not None:
                    save({'state':'EVIDENCE_ROTATED','day':day,'event_id':rotated['event_id'],
                          'at':time.time(),'segment':rotated['sealed_segment'],
                          'segment_sha256':rotated['sealed_segment_sha256'],
                          'compact_records':rotated['compact_records'],
                          'financial_authority':False,'real_orders_sent':False})
            except Exception as exc:
                log_error(exc,f'evidence-rotation day={day}')
                save({'state':'EVIDENCE_ROTATION_GATED','day':day,'at':time.time(),
                      'error_type':type(exc).__name__,'error':str(exc),
                      'financial_authority':False,'real_orders_sent':False})
                await asyncio.sleep(30)
                continue
            ready,rule_fp,review_id=protected_review_ready(day)
            if not ready:
                save({'state':'WAITING_PROTECTED_REVIEW','day':day,'at':time.time(),
                      'rule_fingerprint':rule_fp,'financial_authority':False,'real_orders_sent':False})
                await asyncio.sleep(15)
                continue
            importlib.invalidate_caches()
            try:
                m=importlib.import_module(module)
                from polymarket_scanner.v11.evidence import EvidenceStore
                from polymarket_scanner.v11.certification import StationRegistry
                probe_store=EvidenceStore(m.DB,m.NAMESPACE)
                metadata,rule,scope=m.load_context(probe_store)
                review=StationRegistry(probe_store).assess(
                    scope,stage='SHADOW',metadata_fingerprint=metadata.fingerprint,
                    rule_fingerprint=rule.sha256)
                if not review['eligible'] or review['reason']!='REVIEWED_CAPABILITIES_MATCH':
                    save({'state':'WAITING_PROTECTED_REVIEW','day':day,'event_id':m.EVENT_ID,
                          'at':time.time(),'review':review,'financial_authority':False,'real_orders_sent':False})
                    await asyncio.sleep(10)
                    continue
                await serve_day(day,module)
            except Exception as exc:
                log_error(exc,f'manager-day={day}')
                save({'state':'DAY_GATED','day':day,'at':time.time(),
                      'error_type':type(exc).__name__,'error':str(exc),
                      'financial_authority':False,'real_orders_sent':False})
                await asyncio.sleep(15)
    finally:
        os.close(fd)

asyncio.run(main())
