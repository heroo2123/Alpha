#!/usr/bin/env python3
from __future__ import annotations
import argparse, calendar, fcntl, hashlib, json, os, re, sqlite3, stat, time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

POLICY_VERSION='alpha_v11_daily_review_rollforward_policy_v1'
REQUEST_VERSION='alpha_v11_daily_review_rollforward_request_v2'
MANIFEST_VERSION='alpha_v11_certification_reviews_v1'
REQUIRED_CAPS=('ACCOUNTING','CONSERVATIVE_CONTRACT_VALUATION','EXECUTION_MECHANICS',
               'FORECAST_IDENTITY','IDENTITY','PROTECTED_RISK','RULE_SEMANTICS','SOURCE_INTEGRITY')
HEX64=re.compile(r'^[0-9a-f]{64}$')
DECIMAL=re.compile(r'^[0-9]{1,90}$')
CONDITION=re.compile(r'^0x[0-9a-f]{64}$')

class Refusal(RuntimeError): pass

def canonical(v): return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()
def need(ok,code):
    if not ok: raise Refusal(code)
def load(path):
    with open(path,'rb') as f:return json.load(f)
def shafile(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def norm(v): return re.sub(r'\s+',' ',str(v or '').strip().lower())

def regular_nosymlink(path):
    p=Path(path); info=p.lstat()
    need(stat.S_ISREG(info.st_mode),'FILE_NOT_REGULAR')
    need(not p.is_symlink(),'FILE_SYMLINK_REFUSED')
    return info

def root_custody(path):
    p=Path(path); info=regular_nosymlink(p)
    need(info.st_uid==0 and not (info.st_mode & 0o022),'ROOT_FILE_CUSTODY')
    for parent in p.parents:
        st=parent.lstat()
        need(stat.S_ISDIR(st.st_mode) and st.st_uid==0 and not (st.st_mode & 0o022),'ROOT_PARENT_CUSTODY')
        if str(parent)=='/':break
    return p

def owned_file(path,root,uid,*,readonly=False):
    p=Path(path).resolve(); r=Path(root).resolve()
    need(r in p.parents,'FILE_OUTSIDE_ALLOWED_ROOT')
    st=regular_nosymlink(p); need(st.st_uid==int(uid),'FILE_OWNER')
    need(not (st.st_mode & 0o022),'FILE_WRITABLE_BY_GROUP_OR_WORLD')
    if readonly: need((st.st_mode & 0o222)==0,'SNAPSHOT_NOT_READ_ONLY')
    return p

def decode(row):
    body=json.loads(row['body'])
    need(digest(body)==row['body_sha256'],'EVIDENCE_BODY_HASH')
    need(body.get('record_id')==row['record_id'] and body.get('kind')==row['kind']
         and body.get('event_id')==row['event_id'],'EVIDENCE_IDENTITY')
    need(body.get('recorded_at')==row['recorded_at'] and body.get('available_at')==row['available_at'],
         'EVIDENCE_TIME_IDENTITY')
    return {'seq':row['seq'],'id':row['record_id'],'kind':row['kind'],
            'event_id':row['event_id'],'sha256':row['body_sha256'],'body':body}

def get(db,rid):
    row=db.execute('SELECT * FROM v11_records WHERE record_id=?',(rid,)).fetchone()
    need(row is not None,'EVIDENCE_MISSING:'+rid)
    return decode(row)

def full_month(d):
    x=date.fromisoformat(d);return calendar.month_name[x.month].lower(),x.day

def date_token(d):
    x=date.fromisoformat(d);return f"{x.day} {calendar.month_abbr[x.month].lower()} '{x.year%100:02d}"

def partition_shape(part):
    need(isinstance(part,list) and len(part)==11,'PARTITION_COUNT')
    ordered=sorted(part,key=lambda x:-10**9 if x.get('lower') is None else float(x['lower']))
    need(ordered[0].get('lower') is None and ordered[-1].get('upper') is None,'PARTITION_TAILS')
    seen=set();prev=None;widths=[]
    for i,x in enumerate(ordered):
        need(x.get('unit')=='F','PARTITION_UNIT')
        for k,rx in (('market_id',DECIMAL),('condition_id',CONDITION),('yes_token',DECIMAL),('no_token',DECIMAL)):
            v=str(x.get(k,'')).lower(); need(bool(rx.fullmatch(v)),'PARTITION_ID:'+k)
            need(v not in seen,'PARTITION_DUPLICATE_ID');seen.add(v)
        lo,hi=x.get('lower'),x.get('upper')
        if lo is not None:need(float(lo).is_integer(),'PARTITION_NONINTEGER')
        if hi is not None:need(float(hi).is_integer(),'PARTITION_NONINTEGER')
        if i:need(lo is not None and prev is not None and float(lo)==float(prev)+1,'PARTITION_GAP')
        if lo is not None and hi is not None:
            need(float(hi)>=float(lo),'PARTITION_ORDER');widths.append(float(hi)-float(lo)+1)
        if hi is not None:prev=hi
    need(widths and len(set(widths))==1 and widths[0]==2.0,'PARTITION_WIDTH')
    return ordered

def expected_questions(part,target_date):
    month,day=full_month(target_date);out=[]
    for x in partition_shape(part):
        lo,hi=x.get('lower'),x.get('upper')
        if lo is None:q=f'will the highest temperature in atlanta be {int(hi)}°f or below on {month} {day}?'
        elif hi is None:q=f'will the highest temperature in atlanta be {int(lo)}°f or higher on {month} {day}?'
        else:q=f'will the highest temperature in atlanta be between {int(lo)}-{int(hi)}°f on {month} {day}?'
        out.append(norm(q))
    return out

def normalized_payload(payload):
    p=json.loads(canonical(payload)); d=p.get('target_date')
    need(isinstance(d,str),'TARGET_DATE');date.fromisoformat(d)
    need(DECIMAL.fullmatch(str(p.get('event_id',''))) is not None,'EVENT_ID')
    m,day=full_month(d)
    need(norm(p.get('title'))==f'highest temperature in atlanta on {m} {day}?','TITLE_TEMPLATE')
    part=partition_shape(p.get('partition')); qs=expected_questions(part,d)
    sc=p.get('strict_contract'); need(isinstance(sc,dict),'STRICT_CONTRACT')
    need([norm(x) for x in sc.get('questions',[])]==qs,'QUESTION_TEMPLATE')
    claimed=sc.get('sha256');need(HEX64.fullmatch(str(claimed or '')) is not None,'STRICT_SHA_FORMAT')
    core=dict(sc);core.pop('sha256',None);need(digest(core)==claimed,'STRICT_SHA_MISMATCH')
    token=re.escape(date_token(d)); rules=norm(sc.get('operative_rules'))
    rules,n=re.subn(r'\b'+token+r'\b','<target-date>',rules);need(n>=1,'OPERATIVE_DATE_TOKEN')
    for i,x in enumerate(part):
        x.update(market_id=f'<market-{i}>',condition_id=f'<condition-{i}>',
                 yes_token=f'<yes-{i}>',no_token=f'<no-{i}>',question=f'<question-{i}>',
                 lower=None if i==0 else '<lower>',upper=None if i==len(part)-1 else '<upper>')
    p['partition']=part;p['event_id']='<event>';p['target_date']='<date>';p['title']='<title>'
    sc['event_id']='<event>';sc['target_date']='<date>';sc['sha256']='<strict-sha>'
    sc['questions']=[f'<question-{i}>' for i in range(len(part))];sc['operative_rules']=rules
    return p

def bind_raw_event(payload,event):
    need(str(event.get('id'))==str(payload.get('event_id')),'RAW_EVENT_ID')
    need(norm(event.get('title'))==norm(payload.get('title')),'RAW_EVENT_TITLE')
    need(str(event.get('eventDate') or '')==payload.get('target_date'),'RAW_EVENT_DATE')
    need(event.get('active') is True and event.get('closed') is False and event.get('archived') is False,'RAW_EVENT_NOT_OPEN')
    sc=payload['strict_contract']; rules=norm(sc['operative_rules']); source=str(sc['operative_source'])
    need(norm(event.get('description'))==rules,'RAW_EVENT_RULES')
    markets=event.get('markets');need(isinstance(markets,list) and len(markets)==len(payload['partition']),'RAW_MARKET_COUNT')
    byid={str(x.get('id')):x for x in markets if isinstance(x,dict)};need(len(byid)==len(markets),'RAW_MARKET_IDS')
    for row in payload['partition']:
        m=byid.get(str(row['market_id']));need(m is not None,'RAW_MARKET_MISSING')
        need(str(m.get('conditionId','')).lower()==str(row['condition_id']).lower(),'RAW_CONDITION')
        need(norm(m.get('question'))==norm(row['question']),'RAW_QUESTION')
        need(norm(m.get('description'))==rules and str(m.get('resolutionSource') or '')==source,'RAW_MARKET_RULE_SOURCE')
        try:outcomes=json.loads(m.get('outcomes'));tokens=json.loads(m.get('clobTokenIds'))
        except Exception as exc:raise Refusal('RAW_BINARY_SCHEMA') from exc
        need(outcomes==['Yes','No'] and tokens==[str(row['yes_token']),str(row['no_token'])],'RAW_TOKEN_BINDING')
        need(m.get('active') is True and m.get('closed') is False and m.get('enableOrderBook') is True,'RAW_MARKET_NOT_OPEN')

def validate_policy(policy):
    need(policy.get('version')==POLICY_VERSION,'POLICY_VERSION')
    need(policy.get('namespace')=='CHALLENGER:katl-shadow' and policy.get('stage')=='SHADOW','POLICY_STAGE')
    need(set(policy.get('required_capabilities',[]))==set(REQUIRED_CAPS),'POLICY_CAPABILITIES')
    need(HEX64.fullmatch(str(policy.get('scope_key',''))) is not None,'POLICY_SCOPE_KEY_FORMAT')
    need(digest(policy.get('scope'))==policy['scope_key'],'POLICY_SCOPE_KEY')
    need(HEX64.fullmatch(str(policy.get('metadata_fingerprint',''))) is not None,'POLICY_METADATA_HASH')
    need(digest(normalized_payload(policy['anchor_rule_payload']))==policy['envelope_sha256'],'POLICY_ENVELOPE')
    need(30<=float(policy['maximum_request_age_seconds'])<=900,'POLICY_REQUEST_AGE')
    need(0<float(policy['review_ttl_seconds'])<=7*86400,'POLICY_REVIEW_TTL')
    need(isinstance(policy.get('checker_version'),str),'POLICY_CHECKER')
    return policy

def verify_db(policy,req):
    snap=owned_file(req['evidence_snapshot'],policy['allowed_snapshot_root'],policy['snapshot_owner_uid'],readonly=True)
    need(HEX64.fullmatch(str(req.get('evidence_snapshot_sha256',''))) is not None,'SNAPSHOT_SHA_FORMAT')
    need(shafile(snap)==req['evidence_snapshot_sha256'],'SNAPSHOT_HASH')
    need(re.fullmatch(r'daily-\d{4}-\d{2}-\d{2}\.sqlite',snap.name) is not None,'SNAPSHOT_NAME')
    db=sqlite3.connect('file:'+str(snap)+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    meta=dict(db.execute('SELECT key,value FROM v11_meta'));need(meta.get('namespace')==policy['namespace'],'DB_NAMESPACE')
    rr=get(db,req['rule_record_id']);need(rr['kind']=='RULE_STATE','RULE_KIND')
    latest=db.execute("SELECT * FROM v11_records WHERE kind='RULE_STATE' AND event_id=? ORDER BY seq DESC LIMIT 1",(rr['event_id'],)).fetchone()
    need(latest is not None and latest['record_id']==rr['id'],'RULE_NOT_LATEST')
    rd=rr['body'].get('details',{});need(rd.get('quarantined') is False and rd.get('changed') is False,'RULE_QUARANTINED')
    now=time.time();received=float(rd.get('source_received_at',-1));need(0<=now-received<=float(policy['maximum_request_age_seconds']),'RULE_RECEIPT_STALE')
    payload=rd.get('preimage');need(isinstance(payload,dict),'RULE_PREIMAGE')
    need(digest(payload)==rd.get('fingerprint'),'RULE_FINGERPRINT')
    need(payload.get('financial_authority') is False,'RULE_FINANCIAL_AUTHORITY')
    need(normalized_payload(payload)==normalized_payload(policy['anchor_rule_payload']),'RULE_ENVELOPE_MISMATCH')
    target=date.fromisoformat(payload['target_date']); today=datetime.now(ZoneInfo('America/New_York')).date()
    need(today-timedelta(days=1)<=target<=today+timedelta(days=int(policy['maximum_target_days_ahead'])),'TARGET_WINDOW')
    need(req.get('target_date')==target.isoformat() and snap.name==f'daily-{target.isoformat()}.sqlite','TARGET_BINDING')
    ev=rr['body'].get('evidence',[]);need(len(ev)==1,'RULE_EVIDENCE_COUNT')
    raw=get(db,ev[0]['id']);need(raw['sha256']==ev[0]['sha256'] and raw['kind']=='RULES','RULE_EVIDENCE')
    event=raw['body'].get('payload',{}).get('event');need(isinstance(event,dict),'RAW_EVENT')
    need(digest(event)==rd.get('source_event_sha256'),'RAW_EVENT_HASH');bind_raw_event(payload,event)
    fixed={}; refs=policy['fixed_evidence']
    need(set(refs)=={'station_raw','station_metadata','technical_readiness'},'FIXED_KEYS')
    for name,ref in refs.items():
        x=get(db,ref['id']);need(x['sha256']==ref['sha256'],'FIXED_HASH:'+name);fixed[name]=x
    ready=fixed['technical_readiness']['body'].get('details',{})
    need(ready.get('financial_authority') is False and ready.get('real_orders') is False,'READINESS_NONFINANCIAL')
    need(ready.get('release_git_sha')==policy['release_git_sha'] and ready.get('release_tree_sha')==policy['release_tree_sha'],'READINESS_RELEASE')
    rows=[decode(x) for x in db.execute("SELECT * FROM v11_records WHERE kind='REGISTRY' AND event_id='station:KATL' ORDER BY seq")]
    need(not any(x['body'].get('details',{}).get('action')=='DEMOTION' and x['body']['details'].get('scope_key')==policy['scope_key'] for x in rows),'DEMOTION_PRESENT')
    station_ref={'id':fixed['station_metadata']['id'],'sha256':fixed['station_metadata']['sha256']}
    station_raw_ref={'id':fixed['station_raw']['id'],'sha256':fixed['station_raw']['sha256']}
    ready_ref={'id':fixed['technical_readiness']['id'],'sha256':fixed['technical_readiness']['sha256']}
    rule_ref={'id':rr['id'],'sha256':rr['sha256']};raw_ref={'id':raw['id'],'sha256':raw['sha256']}
    expected={cap:[ready_ref] for cap in REQUIRED_CAPS}
    expected['IDENTITY']=[station_ref,rule_ref];expected['RULE_SEMANTICS']=[rule_ref,ready_ref]
    expected['SOURCE_INTEGRITY']=[station_raw_ref,raw_ref,ready_ref]
    proofs={};through=0
    for cap in REQUIRED_CAPS:
        hits=[]
        for x in rows:
            dd=x['body'].get('details',{})
            if dd.get('action')=='CAPABILITY_EVIDENCE' and dd.get('scope_key')==policy['scope_key'] and dd.get('capability')==cap and dd.get('metadata_fingerprint')==policy['metadata_fingerprint'] and dd.get('rule_fingerprint')==rd['fingerprint']:
                need(dd.get('result')=='PASS','CAPABILITY_FAIL:'+cap)
                need(dd.get('checker_version')==policy['checker_version'],'CAPABILITY_CHECKER:'+cap)
                need(canonical(dd.get('scope'))==canonical(policy['scope']),'CAPABILITY_SCOPE:'+cap)
                need(canonical(x['body'].get('evidence',[]))==canonical(expected[cap]),'CAPABILITY_LINEAGE:'+cap)
                hits.append(x)
        need(len(hits)==1,'CAPABILITY_AMBIGUOUS_OR_MISSING:'+cap)
        x=hits[0];need(x['seq']>rr['seq'],'CAPABILITY_BEFORE_RULE:'+cap)
        proofs[cap]={'id':x['id'],'sha256':x['sha256']};through=max(through,x['seq'])
    return {'target_date':target.isoformat(),'rule_fingerprint':rd['fingerprint'],'proofs':proofs,
            'reviewed_through_seq':through,'rule_record_id':rr['id'],'rule_record_sha256':rr['sha256']}

def verify(policy_path,request_path):
    policy=validate_policy(load(policy_path));req=load(request_path)
    need(req.get('version')==REQUEST_VERSION,'REQUEST_VERSION')
    owned_file(request_path,policy['allowed_request_root'],policy['request_owner_uid'])
    prepared=float(req.get('prepared_at',-1));need(0<=time.time()-prepared<=float(policy['maximum_request_age_seconds']),'REQUEST_STALE')
    current=shafile(policy['review_manifest'])
    if req.get('expected_manifest_sha256') is not None:need(req['expected_manifest_sha256']==current,'MANIFEST_RACE')
    v=verify_db(policy,req);now=time.time()
    review={'scope_key':policy['scope_key'],'namespace':policy['namespace'],'stage':'SHADOW',
      'metadata_fingerprint':policy['metadata_fingerprint'],'rule_fingerprint':v['rule_fingerprint'],
      'reviewer':policy['reviewer'],'review_id':f"katl-shadow-auto-{v['target_date']}-{v['rule_fingerprint'][:12]}",
      'approved_at':now,'expires_at':now+float(policy['review_ttl_seconds']),
      'reviewed_through_seq':v['reviewed_through_seq'],'capability_proofs':v['proofs']}
    return {'verified':v,'review':review,'current_manifest_sha256':current,'financial_authority':False,'activation_authorized':False}

def publish(policy_path,request_path):
    root_custody(policy_path);policy=validate_policy(load(policy_path));root_custody(policy['review_manifest'])
    root_custody(policy['model_manifest_path']);root_custody(policy['model_state_path'])
    need(shafile(policy['model_manifest_path'])==policy['model_manifest_sha256'],'MODEL_MANIFEST_CHANGED')
    need(shafile(policy['model_state_path'])==policy['model_state_sha256'],'MODEL_STATE_CHANGED')
    result=verify(policy_path,request_path);need(result['verified']['target_date']==datetime.now(ZoneInfo('America/New_York')).date().isoformat(),'PUBLISH_ONLY_CURRENT_DAY')
    lock=Path(policy['lock_path']);need(str(lock).startswith('/var/lock/alpha-v11/'),'LOCK_PATH');lock.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    fd=os.open(lock,os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        need(shafile(policy['review_manifest'])==result['current_manifest_sha256'],'MANIFEST_RACE_AFTER_LOCK')
        m=load(policy['review_manifest']);need(m.get('version')==MANIFEST_VERSION and isinstance(m.get('reviews'),list),'MANIFEST_SCHEMA')
        r=result['review'];exact=[x for x in m['reviews'] if x.get('namespace')==r['namespace'] and x.get('stage')=='SHADOW' and x.get('scope_key')==r['scope_key'] and x.get('metadata_fingerprint')==r['metadata_fingerprint'] and x.get('rule_fingerprint')==r['rule_fingerprint']]
        if exact:
            need(len(exact)==1 and exact[0].get('capability_proofs')==r['capability_proofs'],'EXISTING_REVIEW_CONFLICT')
            result['published']=False;result['review']=exact[0];return result
        need(len(m['reviews'])<1000,'MANIFEST_REVIEW_BOUND');m['reviews'].append(r)
        raw=canonical(m).encode();mp=Path(policy['review_manifest']);tmp=mp.parent/('.daily-review.'+str(os.getpid())+'.tmp')
        out=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
        try:os.write(out,raw);os.fsync(out)
        finally:os.close(out)
        os.replace(tmp,mp);dfd=os.open(mp.parent,os.O_RDONLY|os.O_DIRECTORY);os.fsync(dfd);os.close(dfd)
        need(shafile(policy['model_manifest_path'])==policy['model_manifest_sha256'],'MODEL_MANIFEST_CHANGED_POST')
        need(shafile(policy['model_state_path'])==policy['model_state_sha256'],'MODEL_STATE_CHANGED_POST')
        result['published']=True;result['new_manifest_sha256']=hashlib.sha256(raw).hexdigest();return result
    finally:os.close(fd)

def publish_current(policy_path):
    policy=load(policy_path);today=datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    req=Path(policy['allowed_request_root'])/(today+'.json')
    if not req.exists():return {'published':False,'state':'NO_CURRENT_DAY_REQUEST','target_date':today,'financial_authority':False}
    return publish(policy_path,str(req))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=('verify','publish','publish-current'));ap.add_argument('--policy',required=True);ap.add_argument('--request')
    a=ap.parse_args()
    try:
        if a.mode=='publish-current':need(a.request is None,'REQUEST_NOT_ALLOWED');r=publish_current(a.policy)
        else:need(bool(a.request),'REQUEST_REQUIRED');r=verify(a.policy,a.request) if a.mode=='verify' else publish(a.policy,a.request)
    except Exception as exc:
        print(json.dumps({'ok':False,'type':type(exc).__name__,'reason':str(exc)},sort_keys=True));raise SystemExit(2)
    print(json.dumps({'ok':True,**r},sort_keys=True))

if __name__=='__main__':main()
