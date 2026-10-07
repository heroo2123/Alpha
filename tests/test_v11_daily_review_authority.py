from __future__ import annotations
import copy,hashlib,importlib.util,json,os,sqlite3,time
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest

P=Path('/tmp/alpha-v11-perpetual-rollforward-20261004/host_trust/v11-daily-review-authority/authority.py')
s=importlib.util.spec_from_file_location('auth',P);a=importlib.util.module_from_spec(s);s.loader.exec_module(a)
CHECKER='ALPHA_V11_PERPETUAL_DAY_PREP_0DD809E_V1'
SCOPE={'family':'HIGH','horizon':'D0','model_version':'v11-exact-day-historical-v2','season':'FALL',
'source_rule_family':'NWS_WRH_TIMESERIES','station':'KATL','strategy':'FUTURE_FORECAST','time_of_day':'ALL_DAY'}

def payload(target,event_id='1129999'):
    month=target.strftime('%B');day=target.day;abbr=target.strftime('%b').lower();yy=target.year%100
    part=[]
    for i in range(11):
        lo=None if i==0 else 70+(i-1)*2;hi=69 if i==0 else None if i==10 else 71+(i-1)*2
        if lo is None:q=f'Will the highest temperature in Atlanta be {int(hi)}°F or below on {month} {day}?'
        elif hi is None:q=f'Will the highest temperature in Atlanta be {int(lo)}°F or higher on {month} {day}?'
        else:q=f'Will the highest temperature in Atlanta be between {int(lo)}-{int(hi)}°F on {month} {day}?'
        part.append({'market_id':str(5000+i),'condition_id':'0x'+format(i+1,'064x'),'question':q,
          'lower':lo,'upper':hi,'unit':'F','yes_token':str(10**40+i*2+1),'no_token':str(10**40+i*2+2)})
    rules=f"this market uses fixed reviewed semantics in atlanta on {day} {abbr} '{yy:02d} with unchanged suffix"
    sc={'version':'weather_contract_strict_v9_reviewed_city_station_binding','event_id':event_id,
        'family':'daily_high_temperature','location':'atlanta','operative_rules':rules,
        'operative_source':'https://www.weather.gov/wrh/timeseries?site=katl',
        'questions':[a.norm(x['question']) for x in part],'station':'KATL','target_date':target.isoformat(),'unit':'F'}
    sc['sha256']=a.digest(sc)
    return {'version':'alpha_v11_universal_rule_v1','event_id':event_id,
      'title':f'Highest temperature in Atlanta on {month} {day}?','strict_contract':sc,'station':'KATL','city':'atlanta',
      'target_date':target.isoformat(),'timezone':'America/New_York','unit':'F','family':'daily_high_temperature',
      'statistic':'DAILY_HIGHEST_TEMP','observation_population':'WRH_HOURLY_DATA','precision_rounding':'WHOLE_DEGREE_F',
      'primary_source':'https://www.weather.gov/wrh/timeseries?site=katl','source_family':'NWS_WRH_TIMESERIES',
      'fallback_policy':'WEATHER_UNDERGROUND_IF_WRH_UNAVAILABLE_BY_NEXT_DAY_2359_ET',
      'correction_policy':'ACCEPT_REVISIONS_UNTIL_FIRST_FOLLOWING_DATE_DATAPOINT',
      'finality_and_deadline_policy':'FIRST_FOLLOWING_DATE_DATAPOINT_OR_NEXT_DAY_2359_ET',
      'no_data_outcome':'LOWEST_BRACKET','partition':part,'metadata_fingerprint':'e'*64,
      'compiler_version':'weather_only_contract_compiler_v1_inventory_no_financial_authority',
      'semantic_profile_version':'weather_temperature_rule_authority_v2_hko_decimal_fail_closed','financial_authority':False}

def event_for(p):
    markets=[{'id':r['market_id'],'conditionId':r['condition_id'],'question':r['question'],
      'description':p['strict_contract']['operative_rules'],'resolutionSource':p['strict_contract']['operative_source'],
      'clobTokenIds':json.dumps([r['yes_token'],r['no_token']]),'outcomes':json.dumps(['Yes','No']),
      'active':True,'closed':False,'enableOrderBook':True} for r in p['partition']]
    return {'id':p['event_id'],'slug':a.gamma_slug(p['target_date']),'title':p['title'],'eventDate':p['target_date'],
      'description':p['strict_contract']['operative_rules'],'active':True,'closed':False,'archived':False,'markets':markets}

def append(db,seq,rid,kind,event,extra):
    at=time.time();body={'record_id':rid,'kind':kind,'event_id':event,'recorded_at':at,'available_at':at,
      'namespace':'CHALLENGER:katl-shadow','financial_authority':False,**extra};h=a.digest(body)
    db.execute('insert into v11_records values(?,?,?,?,?,?,?,?)',(seq,rid,kind,event,at,at,a.canonical(body),h))
    return {'id':rid,'sha256':h}

def setup(tmp_path,*,mutate=None,proof_fail=None,bad_live_token=False):
    today=datetime.now(ZoneInfo('America/New_York')).date();target=today+timedelta(days=1)
    anchor=payload(today,'1118070');cand=payload(target)
    if mutate:mutate(cand)
    event=event_for(cand);rule_sha=a.digest(cand);source_sha=a.digest(event);scope_key=a.digest(SCOPE)
    dbp=tmp_path/f'daily-{target.isoformat()}.sqlite';db=sqlite3.connect(dbp)
    db.executescript('create table v11_meta(key text primary key,value text);'
      'create table v11_records(seq integer primary key,record_id text unique,kind text,event_id text,recorded_at real,available_at real,body text,body_sha256 text);')
    db.executemany('insert into v11_meta values(?,?)',[('version','alpha_v11_evidence_v1'),('namespace','CHALLENGER:katl-shadow')])
    sr=append(db,1,'station-raw','STATION_METADATA','station:KATL',{'payload':{'station':'KATL'},'evidence_class':'PUBLIC_OBSERVED'})
    sm=append(db,2,'station-meta','REGISTRY','station:KATL',{'details':{'action':'METADATA','metadata_fingerprint':'e'*64,'material_changed':False},'evidence':[sr]})
    ready=append(db,3,'readiness','MEASUREMENT','station:KATL',{'details':{'financial_authority':False,'real_orders':False}})
    raw=append(db,4,'raw','RULES',cand['event_id'],{'evidence_class':'PUBLIC_OBSERVED','payload':{'event':event}})
    rule=append(db,5,'rule','RULE_STATE',cand['event_id'],{'details':{'quarantined':False,'changed':False,'fingerprint':rule_sha,
      'preimage':cand,'source_event_sha256':source_sha,'source_received_at':time.time()},'evidence':[raw]})
    fixed={'station_raw':sr,'station_metadata':sm,'technical_readiness':ready}
    for i,cap in enumerate(a.REQUIRED_CAPS,6):
        refs=[ready]
        if cap=='IDENTITY':refs=[sm,rule]
        elif cap=='RULE_SEMANTICS':refs=[rule,ready]
        elif cap=='SOURCE_INTEGRITY':refs=[sr,raw,ready]
        append(db,i,f'rollforward:capability:{cand["event_id"]}:5:{cap.lower()}','REGISTRY','station:KATL',
          {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
           'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'FAIL' if cap==proof_fail else 'PASS',
           'checker_version':CHECKER},'evidence':refs})
    db.commit();db.close();os.chmod(dbp,0o444)
    live=copy.deepcopy(event)
    if bad_live_token:live['markets'][0]['clobTokenIds']=json.dumps(['1','2'])
    a.live_event=lambda _d,e=live:copy.deepcopy(e)
    manifest=tmp_path/'station.json';manifest.write_text(a.canonical({'version':a.MANIFEST_VERSION,'reviews':[]}))
    policy={'version':a.POLICY_VERSION,'namespace':'CHALLENGER:katl-shadow','stage':'SHADOW','scope':SCOPE,'scope_key':scope_key,
      'metadata_fingerprint':'e'*64,'anchor_rule_payload':anchor,'envelope_sha256':a.digest(a.normalized_rule(anchor)),
      'required_capabilities':list(a.REQUIRED_CAPS),'checker_version':CHECKER,'fixed_evidence':fixed,
      'reviewer':'ROOT_DAILY_ROLLFORWARD_V2','maximum_target_days_ahead':3,'review_ttl_seconds':259200,
      'maximum_request_age_seconds':900,'allowed_request_root':str(tmp_path),'request_owner_uid':os.getuid(),
      'allowed_snapshot_root':str(tmp_path),'snapshot_owner_uid':os.getuid(),'review_manifest':str(manifest),
      'lock_path':'/var/lock/alpha-v11/test.lock'}
    pp=tmp_path/'policy.json';pp.write_text(json.dumps(policy))
    req={'version':a.REQUEST_VERSION,'target_date':target.isoformat(),'evidence_snapshot':str(dbp),
      'evidence_snapshot_sha256':hashlib.sha256(dbp.read_bytes()).hexdigest(),'rule_record_id':'rule',
      'expected_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),'prepared_at':time.time()}
    rp=tmp_path/'request.json';rp.write_text(json.dumps(req));return pp,rp

def test_valid(tmp_path):
    pp,rp=setup(tmp_path);out=a.verify(pp,rp);assert set(out['review']['capability_proofs'])==set(a.REQUIRED_CAPS);assert out['financial_authority'] is False
def test_snapshot_hash(tmp_path):
    pp,rp=setup(tmp_path);r=json.loads(rp.read_text());r['evidence_snapshot_sha256']='0'*64;rp.write_text(json.dumps(r))
    with pytest.raises(a.Refusal,match='SNAPSHOT_HASH'):a.verify(pp,rp)
def test_stale_request(tmp_path):
    pp,rp=setup(tmp_path);r=json.loads(rp.read_text());r['prepared_at']=1;rp.write_text(json.dumps(r))
    with pytest.raises(a.Refusal,match='REQUEST_STALE'):a.verify(pp,rp)
def test_live_token_change(tmp_path):
    pp,rp=setup(tmp_path,bad_live_token=True)
    with pytest.raises(a.Refusal,match='MARKET_TOKEN_BINDING'):a.verify(pp,rp)
def test_envelope_change(tmp_path):
    pp,rp=setup(tmp_path,mutate=lambda p:p.__setitem__('source_family','OTHER'))
    with pytest.raises(a.Refusal,match='RULE_ENVELOPE'):a.verify(pp,rp)
def test_capability_fail(tmp_path):
    pp,rp=setup(tmp_path,proof_fail='IDENTITY')
    with pytest.raises(a.Refusal,match='PROOF_RESULT_IDENTITY'):a.verify(pp,rp)
def test_manifest_race(tmp_path):
    pp,rp=setup(tmp_path);r=json.loads(rp.read_text());r['expected_manifest_sha256']='1'*64;rp.write_text(json.dumps(r))
    with pytest.raises(a.Refusal,match='MANIFEST_RACE'):a.verify(pp,rp)
