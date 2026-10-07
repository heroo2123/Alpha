#!/usr/bin/env python3
from __future__ import annotations
import argparse,calendar,fcntl,hashlib,http.client,json,os,re,sqlite3,ssl,stat,time,urllib.parse
from datetime import date,datetime,timedelta
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
def need(v,c):
    if not v: raise Refusal(c)
def canonical(v): return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def digest(v): return hashlib.sha256(canonical(v).encode()).hexdigest()
def load(p):
    with open(p,'rb') as f:return json.load(f)
def shafile(p):
    h=hashlib.sha256()
    with open(p,'rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def norm(s): return re.sub(r'\s+',' ',str(s or '').strip().lower())

def regular_file(p):
    p=Path(p);i=p.lstat();need(not stat.S_ISLNK(i.st_mode) and not p.is_symlink(),'FILE_SYMLINK_REFUSED');need(stat.S_ISREG(i.st_mode),'FILE_NOT_REGULAR');return p,i
def root_file(p):
    p,i=regular_file(p);need(i.st_uid==0 and not(i.st_mode&0o022),'ROOT_FILE_CUSTODY')
    for q in p.parents:
        j=q.lstat();need(stat.S_ISDIR(j.st_mode) and j.st_uid==0 and not(j.st_mode&0o022),'ROOT_PARENT_CUSTODY')
        if str(q)=='/':break
    return p
def user_file(p,root,uid,readonly=False):
    praw=Path(p);i=praw.lstat();need(not stat.S_ISLNK(i.st_mode) and not praw.is_symlink(),'FILE_SYMLINK_REFUSED');need(stat.S_ISREG(i.st_mode),'FILE_NOT_REGULAR')
    p=praw.resolve();root=Path(root).resolve();need(root in p.parents,'USER_FILE_ROOT')
    need(i.st_uid==int(uid),'USER_FILE_OWNER')
    need((i.st_mode&0o222)==0 if readonly else not(i.st_mode&0o022),'USER_FILE_MODE')
    return p

def rec(row):
    b=json.loads(row['body']);need(digest(b)==row['body_sha256'],'EVIDENCE_BODY_HASH')
    need(b.get('record_id')==row['record_id'] and b.get('kind')==row['kind'] and b.get('event_id')==row['event_id'],'EVIDENCE_IDENTITY')
    need(b.get('recorded_at')==row['recorded_at'] and b.get('available_at')==row['available_at'],'EVIDENCE_TIME')
    return {'seq':row['seq'],'id':row['record_id'],'kind':row['kind'],'event_id':row['event_id'],'sha256':row['body_sha256'],'body':b}
def get(db,rid):
    r=db.execute('select * from v11_records where record_id=?',(rid,)).fetchone();need(r is not None,'EVIDENCE_MISSING:'+rid);return rec(r)

def month_day(d):
    x=date.fromisoformat(d);return calendar.month_name[x.month].lower(),x.day
def short_date(d):
    x=date.fromisoformat(d);return f"{x.day} {calendar.month_abbr[x.month].lower()} '{x.year%100:02d}"
def partition(part):
    need(isinstance(part,list) and len(part)==11,'PARTITION_COUNT')
    rows=sorted(part,key=lambda x:-1e9 if x.get('lower') is None else float(x['lower']))
    need(rows[0].get('lower') is None and rows[-1].get('upper') is None,'PARTITION_TAILS')
    seen=set();prev=None;widths=[]
    for i,x in enumerate(rows):
        need(x.get('unit')=='F','PARTITION_UNIT')
        for k,rx in (('market_id',DECIMAL),('condition_id',CONDITION),('yes_token',DECIMAL),('no_token',DECIMAL)):
            v=str(x.get(k,'')).lower();need(rx.fullmatch(v) is not None,'PARTITION_ID_'+k);need(v not in seen,'PARTITION_DUPLICATE');seen.add(v)
        lo,hi=x.get('lower'),x.get('upper')
        if lo is not None: need(float(lo).is_integer(),'PARTITION_NONINTEGER')
        if hi is not None: need(float(hi).is_integer(),'PARTITION_NONINTEGER')
        if i: need(lo is not None and prev is not None and float(lo)==float(prev)+1,'PARTITION_GAP')
        if lo is not None and hi is not None: need(float(hi)>=float(lo),'PARTITION_ORDER');widths.append(float(hi)-float(lo)+1)
        if hi is not None:prev=hi
    need(widths and set(widths)=={2.0},'PARTITION_WIDTH')
    return rows
def questions(rows,d):
    m,day=month_day(d);out=[]
    for x in partition(rows):
        lo,hi=x.get('lower'),x.get('upper')
        if lo is None:q=f'will the highest temperature in atlanta be {int(hi)}°f or below on {m} {day}?'
        elif hi is None:q=f'will the highest temperature in atlanta be {int(lo)}°f or higher on {m} {day}?'
        else:q=f'will the highest temperature in atlanta be between {int(lo)}-{int(hi)}°f on {m} {day}?'
        out.append(norm(q))
    return out
def normalized_rule(p):
    p=json.loads(canonical(p));d=p.get('target_date');date.fromisoformat(d)
    need(DECIMAL.fullmatch(str(p.get('event_id',''))) is not None,'EVENT_ID')
    m,day=month_day(d);need(norm(p.get('title'))==f'highest temperature in atlanta on {m} {day}?','TITLE_TEMPLATE')
    rows=partition(p.get('partition'));sc=p.get('strict_contract');need(isinstance(sc,dict),'STRICT_CONTRACT')
    need([norm(x) for x in sc.get('questions',[])]==questions(rows,d),'QUESTION_TEMPLATE')
    claimed=sc.get('sha256');need(HEX64.fullmatch(str(claimed or '')) is not None,'STRICT_SHA_FORMAT')
    core=dict(sc);core.pop('sha256',None);need(digest(core)==claimed,'STRICT_SHA_MISMATCH')
    need(sc.get('event_id')==p['event_id'] and sc.get('target_date')==d,'STRICT_BINDING')
    n=norm(sc.get('operative_rules'));n,c=re.subn(r'\b'+re.escape(short_date(d))+r'\b','<target-date>',n);need(c>=1,'OPERATIVE_DATE')
    for i,x in enumerate(rows):
        x.update(market_id=f'<market-{i}>',condition_id=f'<condition-{i}>',yes_token=f'<yes-{i}>',no_token=f'<no-{i}>',
                 question=f'<question-{i}>',lower=None if i==0 else '<lower>',upper=None if i==len(rows)-1 else '<upper>')
    p['partition']=rows;p['event_id']='<event>';p['target_date']='<date>';p['title']='<title>'
    sc['event_id']='<event>';sc['target_date']='<date>';sc['sha256']='<strict-sha>';sc['questions']=[f'<question-{i}>' for i in range(len(rows))];sc['operative_rules']=n
    return p

def gamma_slug(d):
    m,day=month_day(d);return f'highest-temperature-in-atlanta-on-{m}-{day}-{date.fromisoformat(d).year}'
def live_event(d):
    host='gamma-api.polymarket.com';slug=gamma_slug(d);path='/events?'+urllib.parse.urlencode({'slug':slug})
    c=http.client.HTTPSConnection(host,443,context=ssl.create_default_context(),timeout=10)
    try:
        c.request('GET',path,headers={'Host':host,'User-Agent':'Alpha-V11-root-rollforward/2','Accept':'application/json','Connection':'close'})
        r=c.getresponse();need(r.status==200,'GAMMA_HTTP');need(r.getheader('Location') is None,'GAMMA_REDIRECT')
        raw=r.read(4*1024*1024+1);need(len(raw)<=4*1024*1024,'GAMMA_SIZE')
    finally:c.close()
    try: rows=json.loads(raw)
    except Exception as e: raise Refusal('GAMMA_JSON') from e
    need(isinstance(rows,list) and len(rows)==1 and isinstance(rows[0],dict),'GAMMA_EXACT_EVENT');need(rows[0].get('slug')==slug,'GAMMA_SLUG')
    return rows[0]
def bind_event(p,e,open_required):
    need(str(e.get('id'))==str(p.get('event_id')),'EVENT_ID_BINDING');need(norm(e.get('title'))==norm(p.get('title')),'EVENT_TITLE_BINDING')
    need(str(e.get('eventDate') or '')==str(p.get('target_date')),'EVENT_DATE_BINDING')
    if open_required:need(e.get('active') is True and e.get('closed') is False and e.get('archived') is False,'EVENT_NOT_OPEN')
    sc=p['strict_contract'];need(norm(e.get('description'))==norm(sc.get('operative_rules')),'EVENT_RULE_TEXT')
    ms=e.get('markets');need(isinstance(ms,list) and len(ms)==len(p['partition']),'MARKET_COUNT');by={str(x.get('id')):x for x in ms}
    need(len(by)==len(ms),'MARKET_IDS')
    for row in p['partition']:
        m=by.get(str(row['market_id']));need(m is not None,'MARKET_MISSING')
        need(str(m.get('conditionId','')).lower()==str(row['condition_id']).lower(),'CONDITION_BINDING')
        need(norm(m.get('question'))==norm(row['question']),'MARKET_QUESTION');need(norm(m.get('description'))==norm(sc['operative_rules']),'MARKET_RULE_TEXT')
        need(str(m.get('resolutionSource') or '')==str(sc.get('operative_source') or ''),'MARKET_SOURCE')
        try: toks=json.loads(m.get('clobTokenIds'));outs=json.loads(m.get('outcomes'))
        except Exception as e2: raise Refusal('MARKET_BINARY_SCHEMA') from e2
        need(toks==[str(row['yes_token']),str(row['no_token'])] and outs==['Yes','No'],'MARKET_TOKEN_BINDING')
        if open_required:need(m.get('active') is True and m.get('closed') is False and m.get('enableOrderBook') is True,'MARKET_NOT_OPEN')

def verify_snapshot(policy,req):
    snap=user_file(req['evidence_snapshot'],policy['allowed_snapshot_root'],policy['snapshot_owner_uid'],True)
    need(HEX64.fullmatch(str(req.get('evidence_snapshot_sha256',''))) is not None,'SNAPSHOT_SHA');need(shafile(snap)==req['evidence_snapshot_sha256'],'SNAPSHOT_HASH')
    need(re.fullmatch(r'daily-\d{4}-\d{2}-\d{2}\.sqlite',snap.name) is not None,'SNAPSHOT_NAME')
    db=sqlite3.connect('file:'+str(snap)+'?mode=ro&immutable=1',uri=True);db.row_factory=sqlite3.Row
    meta=dict(db.execute('select key,value from v11_meta'));need(meta.get('namespace')==policy['namespace'],'DB_NAMESPACE')
    rr=get(db,req['rule_record_id']);need(rr['kind']=='RULE_STATE','RULE_KIND')
    latest=db.execute("select * from v11_records where kind='RULE_STATE' and event_id=? order by seq desc limit 1",(rr['event_id'],)).fetchone()
    need(latest is not None and latest['record_id']==rr['id'],'RULE_NOT_LATEST')
    rd=rr['body'].get('details',{})
    need(rd.get('changed') is False and rd.get('quarantined') is False,'RULE_QUARANTINED')
    maxage=float(policy['maximum_request_age_seconds']);now=time.time();received=float(rd.get('source_received_at',-1));need(0<=now-received<=maxage,'RULE_RECEIPT_STALE')
    p=rd.get('preimage');need(isinstance(p,dict),'RULE_PREIMAGE');need(digest(p)==rd.get('fingerprint'),'RULE_HASH')
    need(p.get('financial_authority') is False,'RULE_FINANCIAL_AUTHORITY')
    need(normalized_rule(p)==normalized_rule(policy['anchor_rule_payload']),'RULE_ENVELOPE');need(digest(normalized_rule(policy['anchor_rule_payload']))==policy['envelope_sha256'],'POLICY_ENVELOPE')
    target=p['target_date'];need(req.get('target_date')==target and snap.name==f'daily-{target}.sqlite','TARGET_BINDING')
    today=datetime.now(ZoneInfo('America/New_York')).date();td=date.fromisoformat(target);need(today-timedelta(days=1)<=td<=today+timedelta(days=int(policy['maximum_target_days_ahead'])),'TARGET_WINDOW')
    bind_event(p,live_event(target),True)
    ev=rr['body'].get('evidence',[]);need(len(ev)==1,'RULE_EVIDENCE_COUNT');raw=get(db,ev[0]['id']);need(raw['sha256']==ev[0]['sha256'] and raw['kind']=='RULES','RULE_EVIDENCE')
    stored=raw['body'].get('payload',{}).get('event');need(isinstance(stored,dict) and digest(stored)==rd.get('source_event_sha256'),'RAW_EVENT_HASH');bind_event(p,stored,False)
    fixed=policy['fixed_evidence'];need(set(fixed)=={'station_raw','station_metadata','technical_readiness'},'FIXED_KEYS');fr={}
    for name in ('station_raw','station_metadata','technical_readiness'):
        ref=fixed[name];x=get(db,ref['id']);need(x['sha256']==ref['sha256'],'FIXED_'+name);fr[name]=x
    smd=fr['station_metadata']['body'].get('details',{})
    need(smd.get('metadata_fingerprint')==policy['metadata_fingerprint'] and smd.get('material_changed') is False,'METADATA_BINDING')
    tr=fr['technical_readiness']['body'].get('details',{});need(tr.get('financial_authority') is False and tr.get('real_orders') is False,'READINESS_NONFINANCIAL')
    need(tr.get('release_git_sha')==policy['release_git_sha'] and tr.get('release_tree_sha')==policy['release_tree_sha'],'READINESS_RELEASE')
    rows=[rec(x) for x in db.execute("select * from v11_records where kind='REGISTRY' and event_id='station:KATL' order by seq")]
    need(not any(x['body'].get('details',{}).get('action')=='DEMOTION' and x['body']['details'].get('scope_key')==policy['scope_key'] for x in rows),'DEMOTION')
    refs={'IDENTITY':[{'id':fr['station_metadata']['id'],'sha256':fr['station_metadata']['sha256']},{'id':rr['id'],'sha256':rr['sha256']}],
          'RULE_SEMANTICS':[{'id':rr['id'],'sha256':rr['sha256']},{'id':fr['technical_readiness']['id'],'sha256':fr['technical_readiness']['sha256']}],
          'SOURCE_INTEGRITY':[{'id':fr['station_raw']['id'],'sha256':fr['station_raw']['sha256']},{'id':raw['id'],'sha256':raw['sha256']},{'id':fr['technical_readiness']['id'],'sha256':fr['technical_readiness']['sha256']}]}
    default=[{'id':fr['technical_readiness']['id'],'sha256':fr['technical_readiness']['sha256']}];proofs={};through=0
    for cap in REQUIRED_CAPS:
        # Identity match only (action/scope_key/capability/metadata_fingerprint/rule_fingerprint).
        # The live preparer re-mints a fresh CAPABILITY_EVIDENCE row for every capability on every
        # ~5-minute cycle, so this can legitimately collect hundreds of historical rows for a single
        # still-current rule_fingerprint. That volume alone must never be treated as ambiguity.
        loose=[x for x in rows if (lambda dd:dd.get('action')=='CAPABILITY_EVIDENCE' and dd.get('scope_key')==policy['scope_key']
                and dd.get('capability')==cap and dd.get('metadata_fingerprint')==policy['metadata_fingerprint']
                and dd.get('rule_fingerprint')==rd['fingerprint'])(x['body'].get('details',{}))]
        need(len(loose)>0,'CAPABILITY_AMBIGUOUS_OR_MISSING_'+cap)
        # result/financial_authority bind the verdict itself for this exact fingerprint: any
        # non-PASS or financial-authority-claiming row for the SAME identity is a hard, non-skippable
        # refusal (a later benign re-mint can never paper over an earlier FAIL/financial claim).
        for x in loose:
            dd=x['body'].get('details',{})
            need(dd.get('result')=='PASS','PROOF_RESULT_'+cap)
            need(x['body'].get('financial_authority') is False,'PROOF_FINANCIAL_'+cap)
        # A proof must postdate the rule record it attests to. Superseded (pre-rule) re-mints are
        # expected and benign; they are simply not eligible, not a reason to refuse outright.
        fresh=[x for x in loose if x['seq']>rr['seq']]
        need(len(fresh)>0,'CAPABILITY_BEFORE_RULE_'+cap)
        # checker_version/scope/evidence identify WHICH current binding a fresh proof asserts.
        # A fresh-but-stale-binding row (e.g. one that still names an earlier, now-superseded rule
        # or raw record id even though its rule_fingerprint is unchanged) is skipped, not fatal,
        # as long as at least one fresh row names the CURRENT binding exactly.
        hits=[]
        for x in fresh:
            dd=x['body'].get('details',{})
            if dd.get('checker_version')!=policy['checker_version']:continue
            if canonical(dd.get('scope'))!=canonical(policy['scope']):continue
            if canonical(x['body'].get('evidence',[]))!=canonical(refs.get(cap,default)):continue
            hits.append(x)
        need(len(hits)>=1,'CAPABILITY_AMBIGUOUS_OR_MISSING_'+cap)
        if len(hits)>1:
            # Every surviving hit above has already matched checker_version, scope, evidence, and
            # (from the unconditional check above) result/financial_authority exactly, so multiple
            # hits are necessarily substantively identical benign re-mints. This equality is kept as
            # a defensive invariant, not a reachable branch, so a future refactor can never silently
            # let two disagreeing proofs both count as valid.
            sigs={(canonical(h['body'].get('evidence',[])),h['body'].get('details',{}).get('result')) for h in hits}
            need(len(sigs)==1,'CAPABILITY_CONFLICTING_PROOFS_'+cap)
        x=max(hits,key=lambda h:h['seq']);dd=x['body'].get('details',{})
        # content_sha256 is a semantic-claim signature, independent of this proof record's own
        # id/sha256 (which the live preparer re-mints every ~5 minutes even when nothing substantive
        # changed): it binds result, financial_authority, checker_version, scope, the rule content
        # (rule_fingerprint) and the fixed evidence's own sha256 (station_raw/station_metadata/
        # technical_readiness, which are pinned once in the policy and never re-minted).
        # Deliberately excludes source_event_sha256 (the raw live Gamma event's own hash): that
        # event is re-fetched and re-stored on every RULE_STATE mint and carries volatile fields
        # (liquidity, volume, openInterest, updatedAt, series, ...) that change on every poll with
        # zero semantic effect, so including it here would make content_sha256 change every cycle
        # too and defeat the entire point of this signature (confirmed against live 2026-10-07/-08
        # snapshots: ~512/517 and ~237/238 distinct source_event_sha256 values for one unchanged
        # rule_fingerprint). The live event's actually-relevant properties (event id, title, dates,
        # market ids/condition ids/question/resolutionSource/clobTokenIds/outcomes, and open/closed
        # state) are independently re-validated by bind_event() on every single verify_snapshot()
        # call regardless of any stored review, and that call fails closed (raises, before any
        # proof or content_sha256 is ever built) the moment any of them stops matching the rule's
        # own preimage -- so this signature does not need to separately track them.
        content={'result':dd.get('result'),'financial_authority':x['body'].get('financial_authority'),
          'checker_version':dd.get('checker_version'),'scope':policy['scope'],'rule_fingerprint':rd['fingerprint'],
          'fixed_evidence_sha256':{name:fr[name]['sha256'] for name in fr}}
        proofs[cap]={'id':x['id'],'sha256':x['sha256'],'content_sha256':digest(content)};through=max(through,x['seq'])
    db.close();return {'target_date':target,'rule_fingerprint':rd['fingerprint'],'proofs':proofs,'reviewed_through_seq':through}

def verify(policy_path,request_path):
    policy=load(policy_path);req=load(request_path)
    need(policy.get('version')==POLICY_VERSION,'POLICY_VERSION');need(req.get('version')==REQUEST_VERSION,'REQUEST_VERSION')
    need(policy.get('namespace')=='CHALLENGER:katl-shadow' and policy.get('stage')=='SHADOW','POLICY_STAGE')
    need(set(policy.get('required_capabilities',[]))==set(REQUIRED_CAPS),'POLICY_CAPS');need(digest(policy['scope'])==policy['scope_key'],'POLICY_SCOPE')
    need(HEX64.fullmatch(str(policy.get('scope_key',''))) is not None and HEX64.fullmatch(str(policy.get('metadata_fingerprint',''))) is not None,'POLICY_HASHES')
    need(isinstance(policy.get('checker_version'),str),'POLICY_CHECKER')
    need(isinstance(policy.get('allowed_request_root'),str) and isinstance(policy.get('request_owner_uid'),int),'POLICY_REQUEST_CUSTODY')
    need(30<=float(policy['maximum_request_age_seconds'])<=900,'POLICY_AGE')
    need(0<float(policy['review_ttl_seconds'])<=7*86400,'POLICY_REVIEW_TTL')
    age=time.time()-float(req.get('prepared_at',-1));need(0<=age<=float(policy['maximum_request_age_seconds']),'REQUEST_STALE')
    current=shafile(policy['review_manifest']);need(req.get('expected_manifest_sha256')==current,'MANIFEST_RACE')
    v=verify_snapshot(policy,req);now=time.time();review={'scope_key':policy['scope_key'],'namespace':policy['namespace'],'stage':'SHADOW',
      'metadata_fingerprint':policy['metadata_fingerprint'],'rule_fingerprint':v['rule_fingerprint'],'reviewer':policy['reviewer'],
      'review_id':f"katl-shadow-auto-{v['target_date']}-{v['rule_fingerprint'][:12]}",'approved_at':now,
      'expires_at':now+float(policy['review_ttl_seconds']),'reviewed_through_seq':v['reviewed_through_seq'],'capability_proofs':v['proofs']}
    return {'verified':v,'review':review,'current_manifest_sha256':current,'financial_authority':False,'activation_authorized':False}

def lock_path_for(policy):
    lock=Path(policy['lock_path']);need(str(lock).startswith('/var/lock/alpha-v11/'),'LOCK_PATH');return lock

def publish(policy_path,request_path):
    root_file(policy_path);policy=load(policy_path);root_file(policy['review_manifest'])
    root_file(policy['model_manifest_path']);root_file(policy['model_state_path'])
    need(shafile(policy['model_manifest_path'])==policy['model_manifest_sha256'],'MODEL_MANIFEST_CHANGED')
    need(shafile(policy['model_state_path'])==policy['model_state_sha256'],'MODEL_STATE_CHANGED')
    user_file(request_path,policy['allowed_request_root'],policy['request_owner_uid'],False)
    result=verify(policy_path,request_path);need(result['verified']['target_date']==datetime.now(ZoneInfo('America/New_York')).date().isoformat(),'PUBLISH_ONLY_TARGET_DATE')
    lock=lock_path_for(policy);lock.parent.mkdir(mode=0o755,parents=True,exist_ok=True)
    fd=os.open(lock,os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX);mp=Path(policy['review_manifest']);need(shafile(mp)==result['current_manifest_sha256'],'MANIFEST_RACE_AFTER_LOCK')
        m=load(mp);need(m.get('version')==MANIFEST_VERSION and isinstance(m.get('reviews'),list),'MANIFEST_SCHEMA');r=result['review']
        exact=[x for x in m['reviews'] if x.get('namespace')==r['namespace'] and x.get('stage')=='SHADOW' and x.get('scope_key')==r['scope_key'] and x.get('metadata_fingerprint')==r['metadata_fingerprint'] and x.get('rule_fingerprint')==r['rule_fingerprint']]
        if exact:
            # Same day/rule already has a stored review. The live preparer re-mints every
            # CAPABILITY_EVIDENCE row (and the rule/raw records a proof's evidence points at) on
            # every ~5-minute cycle even when the underlying claim hasn't changed, so comparing raw
            # capability_proofs id/sha would make every cycle after the first refuse with
            # EXISTING_REVIEW_CONFLICT purely from benign id churn. Compare by content_sha256
            # instead -- the semantic-claim signature computed in verify_snapshot() -- so a fresh
            # re-mint of the SAME already-satisfied claim is a no-op, while a genuinely different
            # claim (different result, checker_version, scope, rule content, or fixed evidence)
            # still fails closed. A stored review from before this fix (or any
            # malformed/legacy entry) has no content_sha256 to compare against and is therefore
            # never treated as benign -- it still hard-refuses, matching the old strict behavior.
            match=exact[0];stored=match.get('capability_proofs') or {}
            same_claim=(len(exact)==1 and set(stored)==set(r['capability_proofs']) and all(
              stored.get(cap,{}).get('content_sha256') is not None and
              stored[cap]['content_sha256']==r['capability_proofs'][cap]['content_sha256']
              for cap in r['capability_proofs']))
            need(same_claim,'EXISTING_REVIEW_CONFLICT')
            result.update(published=False,state='ALREADY_EXACT_REVIEW_PRESENT',review=match);return result
        need(len(m['reviews'])<1000,'MANIFEST_BOUND');m['reviews'].append(r);raw=canonical(m).encode();tmp=mp.parent/('.daily-review.'+str(os.getpid())+'.tmp')
        f=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o644)
        try:os.write(f,raw);os.fsync(f)
        finally:os.close(f)
        os.replace(tmp,mp);dd=os.open(mp.parent,os.O_RDONLY|os.O_DIRECTORY);os.fsync(dd);os.close(dd)
        need(shafile(policy['model_manifest_path'])==policy['model_manifest_sha256'],'MODEL_MANIFEST_CHANGED_POST')
        need(shafile(policy['model_state_path'])==policy['model_state_sha256'],'MODEL_STATE_CHANGED_POST')
        result.update(published=True,state='PUBLISHED',new_manifest_sha256=hashlib.sha256(raw).hexdigest());return result
    finally:os.close(fd)

def current(policy_path):
    policy=load(policy_path);today=datetime.now(ZoneInfo('America/New_York')).date().isoformat();p=Path(policy['allowed_request_root'])/(today+'.json')
    if not p.exists():return {'published':False,'state':'NO_CURRENT_DAY_REQUEST','target_date':today,'financial_authority':False,'activation_authorized':False}
    try:return publish(policy_path,str(p))
    except Refusal as e:
        if str(e) in {'REQUEST_STALE','MANIFEST_RACE'}:return {'published':False,'state':str(e),'target_date':today,'financial_authority':False,'activation_authorized':False}
        raise

def main():
    ap=argparse.ArgumentParser();ap.add_argument('mode',choices=('verify','publish','publish-current'));ap.add_argument('--policy',required=True);ap.add_argument('--request');a=ap.parse_args()
    try:
        if a.mode=='publish-current':need(a.request is None,'REQUEST_FORBIDDEN');r=current(a.policy)
        else:need(bool(a.request),'REQUEST_REQUIRED');r=verify(a.policy,a.request) if a.mode=='verify' else publish(a.policy,a.request)
    except Exception as e:print(json.dumps({'ok':False,'type':type(e).__name__,'reason':str(e)},sort_keys=True));raise SystemExit(2)
    print(json.dumps({'ok':True,**r},sort_keys=True))
if __name__=='__main__':main()
