#!/usr/bin/env python3
from __future__ import annotations
import argparse,calendar,fcntl,hashlib,json,os,re,sqlite3,stat,time
from datetime import date,datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

POLICY_VERSION='alpha_v11_daily_review_rollforward_policy_v1'
REQUEST_VERSION='alpha_v11_daily_review_rollforward_request_v2'
MANIFEST_VERSION='alpha_v11_certification_reviews_v1'
REQUIRED_CAPS=(
 'ACCOUNTING','CONSERVATIVE_CONTRACT_VALUATION','EXECUTION_MECHANICS',
 'FORECAST_IDENTITY','IDENTITY','PROTECTED_RISK','RULE_SEMANTICS','SOURCE_INTEGRITY')
HEX64=re.compile(r'^[0-9a-f]{64}$')
GIT40=re.compile(r'^[0-9a-f]{40}$')
DECIMAL=re.compile(r'^[0-9]{1,90}$')
CONDITION=re.compile(r'^0x[0-9a-f]{64}$')

class Refusal(RuntimeError): pass

def canonical(v): return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()
def need(cond,code):
    if not cond: raise Refusal(code)
def load(path):
    with open(path,'rb') as f:return json.load(f)
def shafile(path):
    h=hashlib.sha256()
    with open(path,'rb') as f:
        while True:
            b=f.read(1024*1024)
            if not b:return h.hexdigest()
            h.update(b)
def norm(s): return re.sub(r'\s+',' ',str(s or '').strip().lower())

def regular_nosymlink(path):
    p=Path(path);info=p.lstat()
    need(stat.S_ISREG(info.st_mode),'FILE_NOT_REGULAR')
    need(not p.is_symlink(),'FILE_SYMLINK_REFUSED')
    return info

def root_custody_file(path):
    p=Path(path);info=regular_nosymlink(p)
    need(info.st_uid==0 and not (info.st_mode & 0o022),'ROOT_FILE_CUSTODY')
    for parent in p.parents:
        pi=parent.lstat()
        need(stat.S_ISDIR(pi.st_mode) and pi.st_uid==0 and not (pi.st_mode & 0o022),'ROOT_PARENT_CUSTODY')
        if str(parent)=='/': break
    return info

def request_custody(path,policy):
    p=Path(path).resolve();root=Path(policy['allowed_request_root']).resolve()
    need(root in p.parents,'REQUEST_ROOT');info=regular_nosymlink(p)
    expected_uid=int(policy['request_owner_uid'])
    need(info.st_uid==expected_uid and not (info.st_mode & 0o022),'REQUEST_FILE_CUSTODY')
    return p

def snapshot_custody(path,policy):
    p=Path(path).resolve();root=Path(policy['allowed_snapshot_root']).resolve()
    need(root in p.parents,'SNAPSHOT_ROOT');info=regular_nosymlink(p)
    expected_uid=int(policy['snapshot_owner_uid'])
    need(info.st_uid==expected_uid,'SNAPSHOT_OWNER')
    need((info.st_mode & 0o222)==0,'SNAPSHOT_MUST_BE_READ_ONLY')
    return p

def record(row):
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
    need(row is not None,'EVIDENCE_MISSING:'+rid);return record(row)

def daily_date_token(d):
    x=date.fromisoformat(d)
    return f"{x.day} {calendar.month_abbr[x.month].lower()} '{x.year%100:02d}"

def full_month(d):
    x=date.fromisoformat(d);return calendar.month_name[x.month].lower(),x.day

def partition_shape(part,unit):
    need(isinstance(part,list) and len(part)==11,'PARTITION_COUNT')
    ordered=sorted(part,key=lambda x:-10**9 if x.get('lower') is None else float(x['lower']))
    need(ordered[0].get('lower') is None and ordered[-1].get('upper') is None,'PARTITION_TAILS')
    ids=set();prev=None;widths=[]
    for i,x in enumerate(ordered):
        need(x.get('unit')==unit,'PARTITION_UNIT')
        for k,rx in (('market_id',DECIMAL),('condition_id',CONDITION),('yes_token',DECIMAL),('no_token',DECIMAL)):
            v=str(x.get(k,'')).lower();need(bool(rx.fullmatch(v)),'PARTITION_ID:'+k);need(v not in ids,'PARTITION_DUPLICATE_ID');ids.add(v)
        lo=x.get('lower');hi=x.get('upper')
        if lo is not None: need(float(lo).is_integer(),'PARTITION_NONINTEGER')
        if hi is not None: need(float(hi).is_integer(),'PARTITION_NONINTEGER')
        if i>0: need(lo is not None and prev is not None and float(lo)==float(prev)+1,'PARTITION_GAP')
        if lo is not None and hi is not None:
            need(float(hi)>=float(lo),'PARTITION_ORDER');widths.append(float(hi)-float(lo)+1)
        if hi is not None:prev=hi
    need(widths and len(set(widths))==1 and widths[0]==2.0,'PARTITION_WIDTH')
    return ordered

def expected_questions(part,target_date):
    month,day=full_month(target_date);out=[]
    for x in partition_shape(part,'F'):
        lo=x.get('lower');hi=x.get('upper')
        if lo is None:s=f'will the highest temperature in atlanta be {int(hi)}°f or below on {month} {day}?'
        elif hi is None:s=f'will the highest temperature in atlanta be {int(lo)}°f or higher on {month} {day}?'
        else:s=f'will the highest temperature in atlanta be between {int(lo)}-{int(hi)}°f on {month} {day}?'
        out.append(norm(s))
    return out

def normalize_rules(text,target_date):
    token=re.escape(daily_date_token(target_date))
    n=norm(text)
    n,repl=re.subn(r'\b'+token+r'\b','<target-date>',n)
    need(repl>=1,'OPERATIVE_RULE_DATE_TOKEN')
    return n

def normalized_payload(payload):
    p=json.loads(canonical(payload));d=p.get('target_date')
    need(isinstance(d,str),'TARGET_DATE_MISSING');date.fromisoformat(d)
    need(p.get('event_id') and DECIMAL.fullmatch(str(p['event_id'])),'EVENT_ID')
    need(norm(p.get('title'))==f"highest temperature in atlanta on {full_month(d)[0]} {full_month(d)[1]}?",
         'TITLE_TEMPLATE')
    part=partition_shape(p.get('partition'),'F')
    qs=expected_questions(part,d)
    need([norm(x) for x in p.get('strict_contract',{}).get('questions',[])]==qs,'QUESTION_TEMPLATE')
    for i,x in enumerate(part):
        x['market_id']=f'<market-{i}>';x['condition_id']=f'<condition-{i}>'
        x['yes_token']=f'<yes-{i}>';x['no_token']=f'<no-{i}>';x['question']=f'<question-{i}>'
        x['lower']=None if i==0 else '<lower>';x['upper']=None if i==len(part)-1 else '<upper>'
    p['partition']=part;p['event_id']='<event>';p['target_date']='<date>';p['title']='<title>'
    sc=p.get('strict_contract');need(isinstance(sc,dict),'STRICT_CONTRACT')
    need(sc.get('event_id')==payload['event_id'] and sc.get('target_date')==d,'STRICT_DATE_EVENT_BINDING')
    claimed=sc.get('sha256');need(HEX64.fullmatch(str(claimed or '')),'STRICT_SHA_FORMAT')
    core=dict(sc);core.pop('sha256',None);need(digest(core)==claimed,'STRICT_SHA_MISMATCH')
    sc['event_id']='<event>';sc['target_date']='<date>';sc['sha256']='<strict-sha>'
    sc['questions']=[f'<question-{i}>' for i in range(len(part))]
    sc['operative_rules']=normalize_rules(sc.get('operative_rules'),d)
    return p

def bind_raw_event(payload,event):
    need(str(event.get('id'))==str(payload.get('event_id')),'RAW_EVENT_ID')
    need(norm(event.get('title'))==norm(payload.get('title')),'RAW_EVENT_TITLE')
    need(str(event.get('eventDate') or '')==str(payload.get('target_date')),'RAW_EVENT_DATE')
    need(event.get('active') is True and event.get('closed') is False and event.get('archived') is False,'RAW_EVENT_NOT_OPEN')
    sc=payload.get('strict_contract',{});rules=norm(sc.get('operative_rules'));source=norm(sc.get('operative_source'))
    need(norm(event.get('description'))==rules,'RAW_EVENT_RULES')
    markets=event.get('markets');need(isinstance(markets,list) and len(markets)==len(payload.get('partition',[])),'RAW_MARKET_COUNT')
    byid={str(x.get('id')):x for x in markets if isinstance(x,dict)}
    need(len(byid)==len(markets),'RAW_MARKET_IDS')
    for row in payload['partition']:
        m=byid.get(str(row['market_id']));need(m is not None,'RAW_MARKET_MISSING')
        need(str(m.get('conditionId','')).lower()==str(row['condition_id']).lower(),'RAW_CONDITION_ID')
        need(norm(m.get('question'))==norm(row.get('question')),'RAW_QUESTION')
        need(norm(m.get('description'))==rules and norm(m.get('resolutionSource'))==source,'RAW_MARKET_RULE_OR_SOURCE')
        try: outcomes=json.loads(m.get('outcomes'));tokens=json.loads(m.get('clobTokenIds'))
        except Exception as exc: raise Refusal('RAW_BINARY_SCHEMA') from exc
        need(outcomes==['Yes','No'] and tokens==[str(row['yes_token']),str(row['no_token'])],'RAW_TOKEN_BINDING')
        need(m.get('active') is True and m.get('closed') is False and m.get('enableOrderBook') is True,'RAW_MARKET_NOT_OPEN')
    return True

def validate_rule_against_policy(payload,rule_sha,policy):
    need(HEX64.fullmatch(rule_sha or ''),'RULE_SHA_FORMAT')
    need(digest(payload)==rule_sha,'RULE_SHA_MISMATCH')
    need(payload.get('financial_authority') is False,'RULE_FINANCIAL_AUTHORITY')
    anchor=policy['anchor_rule_payload']
    need(normalized_payload(payload)==normalized_payload(anchor),'RULE_ENVELOPE_MISMATCH')
    need(digest(normalized_payload(anchor))==policy['envelope_sha256'],'POLICY_ENVELOPE_HASH')
    target=date.fromisoformat(payload['target_date']);today=datetime.now(ZoneInfo('America/New_York')).date()
    need(today-timedelta(days=1)<=target<=today+timedelta(days=int(policy['maximum_target_days_ahead'])),
         'TARGET_DATE_WINDOW')
    return target

def verify_db(policy,request):
    rawp=snapshot_custody(request['evidence_snapshot'],policy);dbp=rawp.resolve()
    need(re.fullmatch(r'daily-\d{4}-\d{2}-\d{2}\.sqlite',dbp.name) is not None,'EVIDENCE_SNAPSHOT_NAME')
    need(HEX64.fullmatch(str(request.get('evidence_snapshot_sha256',''))),'SNAPSHOT_SHA_FORMAT')
    need(shafile(dbp)==request['evidence_snapshot_sha256'],'SNAPSHOT_HASH')
    db=sqlite3.connect('file:'+str(dbp)+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    meta=dict(db.execute('SELECT key,value FROM v11_meta'))
    need(meta.get('namespace')==policy['namespace'],'DB_NAMESPACE')
    rr=get(db,request['rule_record_id']);need(rr['kind']=='RULE_STATE','RULE_RECORD_KIND')
    rd=rr['body'].get('details',{});need(rd.get('quarantined') is False and rd.get('changed') is False,'RULE_QUARANTINED')
    max_age=float(policy['maximum_request_age_seconds']);now=time.time()
    received=float(rd.get('source_received_at',-1))
    need(0<=now-received<=max_age,'RULE_RECEIPT_STALE')
    payload=rd.get('preimage');need(isinstance(payload,dict),'RULE_PREIMAGE')
    target=validate_rule_against_policy(payload,rd.get('fingerprint'),policy)
    need(dbp.name==f'daily-{target.isoformat()}.sqlite','DB_DATE_BINDING')
    need(request.get('target_date')==target.isoformat(),'REQUEST_TARGET_DATE_BINDING')
    need(rd.get('source_event_sha256') and HEX64.fullmatch(rd['source_event_sha256']),'SOURCE_EVENT_SHA')
    evidence=rr['body'].get('evidence',[]);need(len(evidence)==1,'RULE_SOURCE_EVIDENCE_COUNT')
    raw=get(db,evidence[0]['id']);need(raw['sha256']==evidence[0]['sha256'] and raw['kind']=='RULES','RULE_SOURCE_EVIDENCE')
    event=raw['body'].get('payload',{}).get('event');need(isinstance(event,dict),'RAW_EVENT')
    need(digest(event)==rd['source_event_sha256'],'RAW_EVENT_HASH')
    bind_raw_event(payload,event)
    fixed=policy.get('fixed_evidence',{})
    need(isinstance(fixed,dict) and set(fixed)=={'station_raw','station_metadata','technical_readiness'},'POLICY_FIXED_EVIDENCE')
    fixed_rows={}
    for name,ref in fixed.items():
        need(isinstance(ref,dict) and set(ref)=={'id','sha256'} and HEX64.fullmatch(str(ref['sha256'])),'POLICY_FIXED_REFERENCE')
        x=get(db,ref['id']);need(x['sha256']==ref['sha256'],'FIXED_EVIDENCE_HASH:'+name);fixed_rows[name]=x
    need(fixed_rows['station_raw']['kind']=='STATION_METADATA' and fixed_rows['station_raw']['event_id']=='station:KATL','FIXED_STATION_RAW')
    need(fixed_rows['station_metadata']['kind']=='REGISTRY' and fixed_rows['station_metadata']['event_id']=='station:KATL','FIXED_STATION_METADATA')
    need(fixed_rows['technical_readiness']['kind']=='MEASUREMENT' and fixed_rows['technical_readiness']['event_id']=='station:KATL','FIXED_TECHNICAL_READINESS')
    rd0=fixed_rows['technical_readiness']['body'].get('details',{})
    need(rd0.get('financial_authority') is False and rd0.get('real_orders') is False,'FIXED_READINESS_NONFINANCIAL')
    need(rd0.get('release_git_sha')==policy['release_git_sha'],'RELEASE_GIT_CHANGED')
    need(rd0.get('release_tree_sha')==policy['release_tree_sha'],'RELEASE_TREE_CHANGED')
    metas=[]
    rows=[record(x) for x in db.execute("SELECT * FROM v11_records WHERE kind='REGISTRY' AND event_id='station:KATL' ORDER BY seq")]
    for x in rows:
        dd=x['body'].get('details',{})
        if dd.get('action')=='METADATA' and dd.get('metadata_fingerprint')==policy['metadata_fingerprint']:metas.append(x)
    need(metas and metas[-1]['body']['details'].get('material_changed') is False,'METADATA_BINDING')
    failures=[x for x in rows if (x['body'].get('details',{}).get('action')=='DEMOTION' and
              x['body']['details'].get('scope_key')==policy['scope_key'])]
    need(not failures,'SCOPE_DEMOTION_PRESENT')
    proofmap={};through=0
    readiness_ref={'id':fixed_rows['technical_readiness']['id'],'sha256':fixed_rows['technical_readiness']['sha256']}
    station_ref={'id':fixed_rows['station_metadata']['id'],'sha256':fixed_rows['station_metadata']['sha256']}
    station_raw_ref={'id':fixed_rows['station_raw']['id'],'sha256':fixed_rows['station_raw']['sha256']}
    rule_ref={'id':rr['id'],'sha256':rr['sha256']}
    raw_ref={'id':raw['id'],'sha256':raw['sha256']}
    expected_evidence={cap:[readiness_ref] for cap in REQUIRED_CAPS}
    expected_evidence['IDENTITY']=[station_ref,rule_ref]
    expected_evidence['RULE_SEMANTICS']=[rule_ref,readiness_ref]
    expected_evidence['SOURCE_INTEGRITY']=[station_raw_ref,raw_ref,readiness_ref]
    for cap in REQUIRED_CAPS:
        # Loose identity match only (action/scope_key/capability/metadata_fingerprint/rule_fingerprint).
        # The live preparer re-mints a fresh CAPABILITY_EVIDENCE row for every capability on every
        # ~5-minute cycle, so this can legitimately collect hundreds of historical rows for a single
        # still-current rule_fingerprint. That volume alone must never be treated as ambiguity.
        loose=[x for x in rows if (lambda dd:dd.get('action')=='CAPABILITY_EVIDENCE' and dd.get('scope_key')==policy['scope_key']
                and dd.get('capability')==cap and dd.get('metadata_fingerprint')==policy['metadata_fingerprint']
                and dd.get('rule_fingerprint')==rd['fingerprint'])(x['body'].get('details',{}))]
        need(loose,'CAPABILITY_MISSING:'+cap)
        # result/financial_authority bind the verdict itself for this exact fingerprint: any
        # non-PASS or financial-authority-claiming row for the SAME identity is a hard, non-skippable
        # refusal (a later benign re-mint can never paper over an earlier FAIL/financial claim).
        for x in loose:
            dd=x['body'].get('details',{})
            if dd.get('result')!='PASS':raise Refusal('CAPABILITY_NOT_PASS:'+cap)
            need(x['body'].get('financial_authority') is False,'CAPABILITY_NOT_PASS:'+cap)
        # A proof must postdate the rule record it attests to. Superseded (pre-rule) re-mints are
        # expected and benign; they are simply not eligible, not a reason to refuse outright.
        fresh=[x for x in loose if x['seq']>rr['seq']]
        need(fresh,'CAPABILITY_BEFORE_RULE:'+cap)
        # checker_version/scope/evidence identify WHICH current binding a fresh proof asserts. A
        # fresh-but-stale-binding row is skipped, not fatal, as long as at least one fresh row names
        # the CURRENT binding exactly.
        hits=[]
        for x in fresh:
            dd=x['body'].get('details',{})
            if dd.get('checker_version')!=policy['checker_version']:continue
            if canonical(dd.get('scope'))!=canonical(policy['scope']):continue
            if canonical(x['body'].get('evidence',[]))!=canonical(expected_evidence[cap]):continue
            hits.append(x)
        need(hits,'CAPABILITY_MISSING:'+cap)
        if len(hits)>1:
            # Every surviving hit has already matched checker_version, scope, evidence, and (from
            # the unconditional check above) result/financial_authority exactly, so multiple hits are
            # necessarily substantively identical benign re-mints. Kept as a defensive invariant.
            sigs={(canonical(h['body'].get('evidence',[])),h['body'].get('details',{}).get('result')) for h in hits}
            need(len(sigs)==1,'CAPABILITY_CONFLICTING_PROOFS:'+cap)
        x=max(hits,key=lambda h:h['seq']);proofmap[cap]={'id':x['id'],'sha256':x['sha256']};through=max(through,x['seq'])
    return dict(target_date=target.isoformat(),rule_fingerprint=rd['fingerprint'],proofs=proofmap,
                reviewed_through_seq=through,rule_record_id=rr['id'],rule_record_sha256=rr['sha256'])

def build_review(policy,verified,now):
    ttl=min(float(policy['review_ttl_seconds']),7*86400.)
    return {'scope_key':policy['scope_key'],'namespace':policy['namespace'],'stage':'SHADOW',
      'metadata_fingerprint':policy['metadata_fingerprint'],'rule_fingerprint':verified['rule_fingerprint'],
      'reviewer':policy['reviewer'],'review_id':f"katl-shadow-auto-{verified['target_date']}-{verified['rule_fingerprint'][:12]}",
      'approved_at':now,'expires_at':now+ttl,'reviewed_through_seq':verified['reviewed_through_seq'],
      'capability_proofs':verified['proofs']}

def verify(policy_path,request_path):
    policy=load(policy_path);req=load(request_path)
    need(policy.get('version')==POLICY_VERSION,'POLICY_VERSION');need(req.get('version')==REQUEST_VERSION,'REQUEST_VERSION')
    need(policy.get('namespace')=='CHALLENGER:katl-shadow' and policy.get('stage')=='SHADOW','POLICY_SCOPE')
    need(set(policy.get('required_capabilities',[]))==set(REQUIRED_CAPS),'POLICY_CAPABILITIES')
    need(HEX64.fullmatch(policy.get('scope_key','')) and HEX64.fullmatch(policy.get('metadata_fingerprint','')),'POLICY_HASHES')
    need(isinstance(policy.get('scope'),dict) and digest(policy['scope'])==policy['scope_key'],'POLICY_SCOPE_KEY')
    need(isinstance(policy.get('checker_version'),str) and 1<=len(policy['checker_version'])<=200,'POLICY_CHECKER_VERSION')
    need(isinstance(policy.get('fixed_evidence'),dict),'POLICY_FIXED_EVIDENCE')
    need(GIT40.fullmatch(str(policy.get('release_git_sha',''))) and GIT40.fullmatch(str(policy.get('release_tree_sha',''))),'POLICY_RELEASE_HASHES')
    protected=policy.get('protected_files')
    need(isinstance(protected,dict) and 1<=len(protected)<=8,'POLICY_PROTECTED_FILES')
    for f,expected_sha in protected.items():
        need(HEX64.fullmatch(str(expected_sha)),'POLICY_PROTECTED_FILE_SHA')
        root_custody_file(f);need(shafile(Path(f))==expected_sha,'PROTECTED_FILE_CHANGED')
    need(isinstance(policy.get('allowed_request_root'),str) and isinstance(policy.get('request_owner_uid'),int),'POLICY_REQUEST_CUSTODY')
    need(isinstance(policy.get('allowed_snapshot_root'),str) and isinstance(policy.get('snapshot_owner_uid'),int),'POLICY_SNAPSHOT_CUSTODY')
    max_age=float(policy.get('maximum_request_age_seconds',0));need(30<=max_age<=900,'POLICY_REQUEST_AGE_BOUND')
    prepared=float(req.get('prepared_at',-1));now=time.time();need(0<=now-prepared<=max_age,'REQUEST_STALE')
    manifest=Path(policy['review_manifest']);current_manifest_sha256=shafile(manifest)
    expected=req.get('expected_manifest_sha256')
    if expected is not None: need(expected==current_manifest_sha256,'MANIFEST_RACE')
    v=verify_db(policy,req);review=build_review(policy,v,time.time())
    return {'verified':v,'review':review,'current_manifest_sha256':current_manifest_sha256,
            'financial_authority':False,'activation_authorized':False}

def publish(policy_path,request_path):
    root_custody_file(policy_path);policy=load(policy_path)
    root_custody_file(policy['review_manifest']);request_custody(request_path,policy)
    result=verify(policy_path,request_path);mp=Path(policy['review_manifest'])
    today=datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    need(result['verified']['target_date']==today,'PUBLISH_ONLY_ON_TARGET_LOCAL_DATE')
    lock=Path(policy['lock_path'])
    need(str(lock).startswith('/var/lock/alpha-v11/'),'LOCK_PATH_POLICY')
    lock.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    fd=os.open(lock,os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX)
        need(shafile(mp)==result['current_manifest_sha256'],'MANIFEST_RACE_AFTER_LOCK')
        m=load(mp);need(m.get('version')==MANIFEST_VERSION and isinstance(m.get('reviews'),list),'MANIFEST_SCHEMA')
        review=result['review'];exact=[r for r in m['reviews'] if r.get('namespace')==review['namespace'] and r.get('stage')=='SHADOW'
               and r.get('scope_key')==review['scope_key'] and r.get('metadata_fingerprint')==review['metadata_fingerprint']
               and r.get('rule_fingerprint')==review['rule_fingerprint']]
        if exact:
            need(len(exact)==1,'EXISTING_REVIEW_AMBIGUOUS')
            need(float(exact[0].get('expires_at',0))>time.time(),'EXISTING_REVIEW_EXPIRED')
            result['published']=False;result['state']='ALREADY_REVIEWED';result['review']=exact[0];return result
        need(len(m['reviews'])<1000,'MANIFEST_REVIEW_BOUND')
        m['reviews'].append(review)
        raw=canonical(m).encode();parent=mp.parent
        tmp=parent/('.daily-review.'+str(os.getpid())+'.tmp')
        outfd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
        try:
            os.write(outfd,raw);os.fsync(outfd)
        finally:os.close(outfd)
        os.replace(tmp,mp);dfd=os.open(parent,os.O_RDONLY|os.O_DIRECTORY);os.fsync(dfd);os.close(dfd)
        result['published']=True;result['new_manifest_sha256']=hashlib.sha256(raw).hexdigest();return result
    finally:os.close(fd)

def publish_current(policy_path):
    root_custody_file(policy_path);policy=load(policy_path)
    today=datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    root=Path(policy['allowed_request_root']).resolve()
    request=root/f'{today}.json'
    if not request.exists():
        return {'published':False,'state':'NO_CURRENT_DAY_REQUEST','target_date':today,
                'financial_authority':False,'activation_authorized':False}
    return publish(policy_path,str(request))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=('verify','publish','publish-current'))
    ap.add_argument('--policy',required=True);ap.add_argument('--request')
    a=ap.parse_args()
    try:
        if a.mode=='publish-current':
            need(a.request is None,'REQUEST_NOT_ALLOWED_FOR_PUBLISH_CURRENT');r=publish_current(a.policy)
        else:
            need(bool(a.request),'REQUEST_REQUIRED')
            r=verify(a.policy,a.request) if a.mode=='verify' else publish(a.policy,a.request)
    except Exception as exc:
        print(json.dumps({'ok':False,'reason':str(exc),'type':type(exc).__name__},sort_keys=True));raise SystemExit(2)
    print(json.dumps({'ok':True,**r},sort_keys=True))
if __name__=='__main__':main()
