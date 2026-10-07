#!/usr/bin/env python3
from __future__ import annotations
import asyncio,hashlib,json,math,os,time,traceback
from dataclasses import asdict
from pathlib import Path
import httpx

from polymarket_scanner.v11.certification import StationMetadata
from polymarket_scanner.v11.evidence import canonical,digest
from polymarket_scanner.v11.rules import fingerprint_event
from polymarket_scanner.v11.forecast_sources import ForecastPlan
from polymarket_scanner.v11.gefs_sources import GEFSPlan,field_request,linear_extreme,MODEL_ID
from polymarket_scanner.v11.grib_fields import decode_gefs_field
from polymarket_scanner.v11.pws_quality import geometry
from polymarket_scanner.v11.probability import ForecastComponent,FINAL_EXTREME,target_identity
from polymarket_scanner.v11.model_artifacts import ArtifactStore,predict_with_bundle

ROOT=Path('/home/alphaadmin/AlphaV11_BrainForwardUniverse')
RAW=ROOT/'raw'; PAIRS=ROOT/'pairs'; STATUS=ROOT/'status.json'; ERRORS=ROOT/'errors.log'
PARENT='fd9a32aa12ac7c8018ec524d1b74514bb57c42c0e60241672a4446b95908e641'
INIT=1791072000.0  # 2026-10-04 00Z, causally available before Oct 5/6 local days
STATIONS={
 'KATL':dict(city='atlanta',slug='atlanta',lat=33.64028,lon=-84.42694,tz='America/New_York'),
 'KAUS':dict(city='austin',slug='austin',lat=30.18304,lon=-97.67987,tz='America/Chicago'),
 'KBKF':dict(city='denver',slug='denver',lat=39.71331,lon=-104.75806,tz='America/Denver'),
 'KDAL':dict(city='dallas',slug='dallas',lat=32.85416,lon=-96.85506,tz='America/Chicago'),
 'KHOU':dict(city='houston',slug='houston',lat=29.6375,lon=-95.2825,tz='America/Chicago'),
 'KLAX':dict(city='los angeles',slug='los-angeles',lat=33.93806,lon=-118.38889,tz='America/Los_Angeles'),
 'KLGA':dict(city='nyc',slug='nyc',lat=40.77917,lon=-73.88,tz='America/New_York'),
 'KMIA':dict(city='miami',slug='miami',lat=25.79056,lon=-80.31639,tz='America/New_York'),
 'KORD':dict(city='chicago',slug='chicago',lat=41.97972,lon=-87.90444,tz='America/Chicago'),
 'KSEA':dict(city='seattle',slug='seattle',lat=47.44472,lon=-122.31361,tz='America/Los_Angeles'),
 'KSFO':dict(city='san francisco',slug='san-francisco',lat=37.61961,lon=-122.36558,tz='America/Los_Angeles'),
}
DAYS=(5,6)
CONCURRENCY=1
REQUEST_SPACING_SECONDS=2.0
MATURE_RUN_LAG_SECONDS=12*3600
SEM=asyncio.Semaphore(CONCURRENCY)

class ProviderRateLimited(RuntimeError):
    pass

def atomic_json(path,value):
    tmp=path.with_suffix(path.suffix+'.tmp')
    tmp.write_text(json.dumps(value,indent=2,sort_keys=True,default=str)+'\n')
    os.chmod(tmp,0o600);os.replace(tmp,path)

def station_metadata(station,cfg):
    source=dict(origin='reviewed_v11_brain_historical_backfill_plan_20260929',
                station=station,latitude=cfg['lat'],longitude=cfg['lon'],timezone=cfg['tz'])
    return StationMetadata(station,cfg['city'],'US',cfg['lat'],cfg['lon'],None,cfg['tz'],
        'NWS_WRH_TIMESERIES',('NWS_WRH_TIMESERIES',),('NOAA_GEFS_0P50',),
        digest(source),1790686365.562154)

def raw_path(sha): return RAW/(sha+'.grib2')

def selected_init(root):
    marker=root/'selected-init.json'
    if marker.exists():
        value=json.loads(marker.read_text())
        return float(value['gefs_initialization'])
    mature=time.time()-MATURE_RUN_LAG_SECONDS
    init=float(math.floor(mature/(6*3600))*(6*3600))
    atomic_json(marker,{'version':'alpha_v11_brain_forward_universe_init_v1',
        'gefs_initialization':init,'selected_at':time.time(),'financial_authority':False})
    return init

def live_shadow_busy():
    p=Path('/home/alphaadmin/AlphaV11_ForwardShadow/stable-shadow/2026-10-04/continuous-supervisor-status.json')
    try:
        d=json.loads(p.read_text())
        return d.get('state')=='CENSUS_SERVICE' or d.get('needs_census') is True
    except Exception:
        return True

async def get_bytes(client,url,params):
    last=None
    for attempt in range(5):
        try:
            while live_shadow_busy():
                await asyncio.sleep(15)
            async with SEM:
                r=await client.get(url,params=params)
            if r.status_code==200 and r.content.startswith(b'GRIB'):
                await asyncio.sleep(REQUEST_SPACING_SECONDS)
                return r.content
            if r.status_code==302 and b'Over Rate Limit' in r.content:
                raise ProviderRateLimited('NOAA_AKAMAI_OVER_RATE_LIMIT')
            last=RuntimeError(f'HTTP_OR_GRIB:{r.status_code}:{len(r.content)}')
        except ProviderRateLimited:
            raise
        except Exception as exc:last=exc
        await asyncio.sleep(min(8,1.0*(2**attempt)))
    raise last or RuntimeError('GEFS_FETCH_FAILED')

async def fetch_field(client,plan,metadata,member,hour):
    req=field_request(plan,member,hour)
    data=await get_bytes(client,req.url,dict(req.params))
    h=hashlib.sha256(data).hexdigest(); p=raw_path(h)
    if p.exists():
        if hashlib.sha256(p.read_bytes()).hexdigest()!=h: raise RuntimeError('RAW_HASH_CONFLICT:'+h)
    else:
        tmp=p.with_suffix('.pending');tmp.write_bytes(data);os.chmod(tmp,0o400);os.replace(tmp,p)
    f=decode_gefs_field(data)
    if (f.initialized_at,f.member,f.forecast_hour)!=(plan.initialized_at,member,hour):
        raise RuntimeError('GEFS_IDENTITY_MISMATCH')
    dist=[geometry(metadata.latitude,metadata.longitude,*pt)[0] for pt in f.grid]
    i=min(range(len(dist)),key=lambda j:(dist[j],f.grid[j]))
    if dist[i]>plan.forecast.maximum_grid_distance_km:raise RuntimeError('GEFS_GRID_DISTANCE')
    return {'member':member,'hour':hour,'raw_sha256':h,'bytes':len(data),
            'chosen_point':list(f.grid[i]),'grid_distance_km':dist[i],
            'value_kelvin':float(f.kelvin[i]),'received_at':time.time()}

async def fetch_gamma(client,slug):
    r=await client.get('https://gamma-api.polymarket.com/events',params={'slug':slug})
    if r.status_code!=200:raise RuntimeError('GAMMA_HTTP_'+str(r.status_code))
    rows=r.json()
    if not isinstance(rows,list) or len(rows)!=1:raise RuntimeError('GAMMA_EXACT_EVENT_REQUIRED')
    return rows[0]

async def pair(client,parent,station,cfg,day):
    day_s=f'2026-10-{day:02d}'; key=f'{station}-{day_s}'
    root=PAIRS/key;root.mkdir(mode=0o700,parents=True,exist_ok=True);os.chmod(root,0o700)
    capture=root/'capture.json'
    if capture.exists():
        return {'key':key,'state':'READY','capture':str(capture),'resumed':True}
    slug=f"highest-temperature-in-{cfg['slug']}-on-october-{day}-2026"
    event=await fetch_gamma(client,slug)
    atomic_json(root/'gamma.json',event)
    metadata=station_metadata(station,cfg)
    rule=fingerprint_event(event,station_timezone=cfg['tz'],metadata_fingerprint=metadata.fingerprint)
    rp=rule.payload
    if rp['station']!=station or rp['target_date']!=day_s or rp['family']!='daily_high_temperature' or rp['unit']!='F':
        raise RuntimeError('STRICT_RULE_SCOPE_MISMATCH')
    init=selected_init(root)
    plan=GEFSPlan(ForecastPlan(rule,metadata,50.,86400.),init,86400.)
    if time.time()>=plan.window[0]:
        raise RuntimeError('FORWARD_WINDOW_CLOSED')
    cp=root/f'checkpoint-{int(init)}.json'
    state=json.loads(cp.read_text()) if cp.exists() else {'fields':{}}
    slots=[(m,h) for m in range(31) for h in plan.hours]
    for start in range(0,len(slots),1):
        batch=[x for x in slots[start:start+1] if f'{x[0]}:{x[1]}' not in state['fields']]
        if batch:
            vals=await asyncio.gather(*(fetch_field(client,plan,metadata,m,h) for m,h in batch))
            for v in vals:state['fields'][f"{v['member']}:{v['hour']}"]=v
            atomic_json(cp,state)
        atomic_json(ROOT/'status.json',{'version':'alpha_v11_brain_forward_universe_v1',
            'active':key,'completed_fields':len(state['fields']),'required_fields':len(slots),
            'financial_authority':False,'automatic_promotion':False,'updated_at':time.time()})
    if len(state['fields'])!=len(slots):raise RuntimeError('FIELD_COVERAGE_INCOMPLETE')
    start_ts,end_ts=plan.window;points=[init+h*3600 for h in plan.hours]
    members=[]
    for m in range(31):
        vals=[state['fields'][f'{m}:{h}']['value_kelvin'] for h in plan.hours]
        k=linear_extreme(points,vals,((start_ts,end_ts),),high=True)
        c=k-273.15;members.append(c*1.8+32)
    ready=max(v['received_at'] for v in state['fields'].values())
    evidence_sha=digest({'rule':rule.sha256,'init':init,
        'raw':sorted(v['raw_sha256'] for v in state['fields'].values()),'members':members})
    component=ForecastComponent(MODEL_ID,'BUNDLE_CONFIGURED',target_identity(rule,FINAL_EXTREME),
        tuple(members),0.,1.,1.,evidence_sha,ready,ready,init)
    pred=predict_with_bundle(parent,rule,(component,),as_of=ready,max_source_age_seconds=86400.)
    out={'version':'alpha_v11_brain_forward_universe_capture_v1','station':station,'city':cfg['city'],
         'target_date':day_s,'event_id':rp['event_id'],'slug':slug,'rule_fingerprint':rule.sha256,
         'source_event_sha256':rule.source_event_sha256,'metadata':asdict(metadata),
         'metadata_fingerprint':metadata.fingerprint,'gefs_initialization':init,'forecast_hours':list(plan.hours),
         'field_count':len(slots),'field_evidence_sha256':evidence_sha,'member_extremes_f':members,
         'feature_ready_at':ready,'prediction':pred.payload,'prediction_sha256':pred.sha256,
         'parent_bundle_sha256':parent.sha256,'pre_day_capture':ready<start_ts,
         'day_start':start_ts,'day_end':end_ts,'financial_authority':False,'automatic_promotion':False}
    if not out['pre_day_capture']:raise RuntimeError('FORWARD_CAPTURE_NOT_PRE_DAY')
    atomic_json(capture,out)
    return {'key':key,'state':'READY','capture':str(capture),'resumed':False,
            'prediction_sha256':pred.sha256,'field_count':len(slots)}

async def main():
    parent=ArtifactStore(Path('/home/alphaadmin/AlphaV11_BrainForward/objects')).pin(PARENT)
    results=[]
    limits=httpx.Limits(max_connections=CONCURRENCY,max_keepalive_connections=CONCURRENCY)
    async with httpx.AsyncClient(timeout=15.,follow_redirects=False,limits=limits,
        headers={'User-Agent':'Alpha-V11-Brain-forward-universe/1'}) as client:
      for day in DAYS:
       for station,cfg in STATIONS.items():
        key=f'{station}-2026-10-{day:02d}'
        try:
            r=await pair(client,parent,station,cfg,day);results.append(r)
            print(json.dumps(r,sort_keys=True),flush=True)
        except ProviderRateLimited as exc:
            err={'key':key,'state':'PROVIDER_RATE_LIMITED','error_type':type(exc).__name__,'error':str(exc),
                 'financial_authority':False};results.append(err);print(json.dumps(err,sort_keys=True),flush=True)
            atomic_json(STATUS,{'version':'alpha_v11_brain_forward_universe_v1','state':'PROVIDER_RATE_LIMITED',
                'results':results,'ready':sum(x.get('state')=='READY' for x in results),
                'gated':sum(x.get('state')=='GATED' for x in results),'total_target_pairs':len(STATIONS)*len(DAYS),
                'financial_authority':False,'automatic_promotion':False,'updated_at':time.time()})
            return
        except Exception as exc:
            err={'key':key,'state':'GATED','error_type':type(exc).__name__,'error':str(exc),
                 'financial_authority':False};results.append(err);print(json.dumps(err,sort_keys=True),flush=True)
            with ERRORS.open('a') as f:
                f.write(f'\n[{time.time()}] {key} {type(exc).__name__}: {exc}\n');traceback.print_exc(file=f)
        atomic_json(STATUS,{'version':'alpha_v11_brain_forward_universe_v1','results':results,
            'ready':sum(x.get('state')=='READY' for x in results),'gated':sum(x.get('state')=='GATED' for x in results),
            'total_target_pairs':len(STATIONS)*len(DAYS),'financial_authority':False,
            'automatic_promotion':False,'updated_at':time.time()})
    print(json.dumps({'state':'DONE','ready':sum(x.get('state')=='READY' for x in results),
       'gated':sum(x.get('state')=='GATED' for x in results)},sort_keys=True))
asyncio.run(main())
