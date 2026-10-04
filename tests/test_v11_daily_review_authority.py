from __future__ import annotations
import importlib.util,json,sqlite3,hashlib
from datetime import datetime,timedelta
from zoneinfo import ZoneInfo

P='/tmp/alpha-v11-perpetual-rollforward-20261004/host_trust/v11-daily-review-authority/authority.py'
s=importlib.util.spec_from_file_location('auth',P);a=importlib.util.module_from_spec(s);s.loader.exec_module(a)

def payload(target):
    month=target.strftime('%B');day=target.day;abbr=target.strftime('%b').lower();yy=target.year%100
    part=[]
    for i in range(11):
        lo=None if i==0 else 70+(i-1)*2
        hi=69 if i==0 else None if i==10 else 71+(i-1)*2
        if lo is None:q=f'Will the highest temperature in Atlanta be {int(hi)}°F or below on {month} {day}?'
        elif hi is None:q=f'Will the highest temperature in Atlanta be {int(lo)}°F or higher on {month} {day}?'
        else:q=f'Will the highest temperature in Atlanta be between {int(lo)}-{int(hi)}°F on {month} {day}?'
        part.append({'market_id':str(5000+i),'condition_id':'0x'+format(i+1,'064x'),'question':q,
          'lower':lo,'upper':hi,'unit':'F','yes_token':str(10**40+i*2+1),'no_token':str(10**40+i*2+2)})
    questions=[a.norm(x['question']) for x in part]
    return {'version':'alpha_v11_universal_rule_v1','event_id':'1123309','title':f'Highest temperature in Atlanta on {month} {day}?',
      'strict_contract':{'version':'weather_contract_strict_v9_reviewed_city_station_binding','event_id':'1123309',
        'family':'daily_high_temperature','location':'atlanta',
        'operative_rules':f"fixed reviewed semantics in atlanta on {day} {abbr} '{yy:02d} with unchanged suffix",
        'operative_source':'https://www.weather.gov/wrh/timeseries?site=katl','questions':questions,
        'sha256':'a'*64,'station':'KATL','target_date':target.isoformat(),'unit':'F'},
      'station':'KATL','city':'atlanta','target_date':target.isoformat(),'timezone':'America/New_York','unit':'F',
      'family':'daily_high_temperature','statistic':'DAILY_HIGHEST_TEMP','observation_population':'WRH_HOURLY_DATA',
      'precision_rounding':'WHOLE_DEGREE_F','primary_source':'https://www.weather.gov/wrh/timeseries?site=katl',
      'source_family':'NWS_WRH_TIMESERIES','fallback_policy':'WEATHER_UNDERGROUND_IF_WRH_UNAVAILABLE_BY_NEXT_DAY_2359_ET',
      'correction_policy':'ACCEPT_REVISIONS_UNTIL_FIRST_FOLLOWING_DATE_DATAPOINT',
      'finality_and_deadline_policy':'FIRST_FOLLOWING_DATE_DATAPOINT_OR_NEXT_DAY_2359_ET','no_data_outcome':'LOWEST_BRACKET',
      'partition':part,'metadata_fingerprint':'e'*64,
      'compiler_version':'weather_only_contract_compiler_v1_inventory_no_financial_authority',
      'semantic_profile_version':'weather_temperature_rule_authority_v2_hko_decimal_fail_closed','financial_authority':False}

def append(db,seq,rid,kind,event,body_extra):
    at=1000.+seq;body={'record_id':rid,'kind':kind,'event_id':event,'recorded_at':at,'available_at':at,
      'namespace':'CHALLENGER:katl-shadow','financial_authority':False,**body_extra}
    enc=a.canonical(body);sha=a.digest(body)
    db.execute('INSERT INTO v11_records VALUES(?,?,?,?,?,?,?,?)',(seq,rid,kind,event,at,at,enc,sha));return sha

def setup(tmp_path,offset=1):
    today=datetime.now(ZoneInfo('America/New_York')).date();target=today+timedelta(days=offset);anchor=payload(today);cand=payload(target)
    # Change only expected daily fields.
    cand['event_id']='1129999';cand['strict_contract']['event_id']='1129999'
    event={'id':'1129999','title':cand['title'],'markets':[]};srcsha=a.digest(event);rule_sha=a.digest(cand)
    dbp=tmp_path/f'daily-{target.isoformat()}.sqlite';db=sqlite3.connect(dbp)
    db.executescript('CREATE TABLE v11_meta(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE v11_records(seq INTEGER PRIMARY KEY,record_id TEXT UNIQUE,kind TEXT,event_id TEXT,recorded_at REAL,available_at REAL,body TEXT,body_sha256 TEXT);')
    db.executemany('INSERT INTO v11_meta VALUES(?,?)',[('version','alpha_v11_evidence_v1'),('namespace','CHALLENGER:katl-shadow')])
    rawsha=append(db,1,'raw','RULES','1129999',{'evidence_class':'PUBLIC_OBSERVED','payload':{'event':event}})
    rsha=append(db,2,'rule','RULE_STATE','1129999',{'details':{'quarantined':False,'changed':False,'fingerprint':rule_sha,
      'preimage':cand,'source_event_sha256':srcsha},'evidence':[{'id':'raw','sha256':rawsha}]})
    append(db,3,'meta','REGISTRY','station:KATL',{'details':{'action':'METADATA','metadata_fingerprint':'e'*64,'material_changed':False}})
    seq=4
    for cap in a.REQUIRED_CAPS:
        append(db,seq,'proof:'+cap.lower(),'REGISTRY','station:KATL',{'details':{'action':'CAPABILITY_EVIDENCE',
          'scope_key':'f'*64,'capability':cap,'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS'}});seq+=1
    db.commit();db.close()
    manifest=tmp_path/'station.json';manifest.write_text(a.canonical({'version':a.MANIFEST_VERSION,'reviews':[]}))
    policy={'version':a.POLICY_VERSION,'namespace':'CHALLENGER:katl-shadow','stage':'SHADOW','scope_key':'f'*64,
      'metadata_fingerprint':'e'*64,'anchor_rule_payload':anchor,'envelope_sha256':a.digest(a.normalized_payload(anchor)),
      'required_capabilities':list(a.REQUIRED_CAPS),'reviewer':'ROOT_DAILY_ROLLFORWARD_V1','maximum_target_days_ahead':3,
      'review_ttl_seconds':259200,'allowed_evidence_root':str(tmp_path),'review_manifest':str(manifest),
      'lock_path':'/var/lock/alpha-v11/test.lock','allowed_request_root':str(tmp_path),'request_owner_uid':tmp_path.stat().st_uid}
    pp=tmp_path/'policy.json';pp.write_text(json.dumps(policy))
    req={'version':a.REQUEST_VERSION,'evidence_db':str(dbp),'rule_record_id':'rule',
      'expected_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest()}
    rp=tmp_path/'request.json';rp.write_text(json.dumps(req))
    return pp,rp,dbp,manifest

def test_verify_valid_daily_rollforward(tmp_path):
    pp,rp,_,_=setup(tmp_path)
    out=a.verify(pp,rp)
    assert out['verified']['target_date']
    assert set(out['review']['capability_proofs'])==set(a.REQUIRED_CAPS)
    assert out['financial_authority'] is False and out['activation_authorized'] is False

def mutate_rule(dbp,fn):
    db=sqlite3.connect(dbp);row=db.execute("select body from v11_records where record_id='rule'").fetchone();b=json.loads(row[0]);fn(b)
    db.execute("update v11_records set body=?,body_sha256=? where record_id='rule'",(a.canonical(b),a.digest(b)));db.commit();db.close()

def test_source_change_refused(tmp_path):
    pp,rp,dbp,_=setup(tmp_path)
    mutate_rule(dbp,lambda b:b['details']['preimage'].__setitem__('primary_source','https://example.invalid'))
    try:a.verify(pp,rp);assert False
    except a.Refusal as e:assert 'RULE_SHA_MISMATCH' in str(e) or 'RULE_ENVELOPE_MISMATCH' in str(e)

def test_raw_event_hash_change_refused(tmp_path):
    pp,rp,dbp,_=setup(tmp_path);db=sqlite3.connect(dbp);row=db.execute("select body from v11_records where record_id='raw'").fetchone();b=json.loads(row[0])
    b['payload']['event']['title']='tampered';db.execute("update v11_records set body=?,body_sha256=? where record_id='raw'",(a.canonical(b),a.digest(b)));db.commit();db.close()
    try:a.verify(pp,rp);assert False
    except a.Refusal as e:assert str(e) in {'RULE_SOURCE_EVIDENCE','RAW_EVENT_HASH'}

def test_capability_fail_refused(tmp_path):
    pp,rp,dbp,_=setup(tmp_path);db=sqlite3.connect(dbp);row=db.execute("select body from v11_records where record_id='proof:identity'").fetchone();b=json.loads(row[0])
    b['details']['result']='FAIL';db.execute("update v11_records set body=?,body_sha256=? where record_id='proof:identity'",(a.canonical(b),a.digest(b)));db.commit();db.close()
    try:a.verify(pp,rp);assert False
    except a.Refusal as e:assert str(e)=='CAPABILITY_NOT_PASS:IDENTITY'

def test_manifest_race_refused(tmp_path):
    pp,rp,_,manifest=setup(tmp_path);manifest.write_text(a.canonical({'version':a.MANIFEST_VERSION,'reviews':[{'x':1}]}))
    try:a.verify(pp,rp);assert False
    except a.Refusal as e:assert str(e)=='MANIFEST_RACE'
