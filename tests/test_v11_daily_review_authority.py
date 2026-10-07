from __future__ import annotations
import copy,hashlib,importlib.util,json,os,sqlite3,time
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest

P=Path(__file__).resolve().parent.parent/'host_trust'/'v11-daily-review-authority'/'authority.py'
s=importlib.util.spec_from_file_location('auth',P);a=importlib.util.module_from_spec(s);s.loader.exec_module(a)
CHECKER='ALPHA_V11_PERPETUAL_DAY_PREP_0DD809E_V1'
RELEASE_GIT='f'*40
RELEASE_TREE='a'*40
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

def setup(tmp_path,*,mutate=None,proof_fail=None,bad_live_token=False,target_offset=1,stale_receipt=False,
          proof_omit=None,proof_duplicate=None,second_rule=False,
          metadata_material_changed=False,readiness_release_mismatch=False,
          churn_stale_cap=None,proof_financial=None,proof_checker_mismatch=None,proof_scope_mismatch=None,
          proof_fail_then_pass=None,inject_demotion=False,readiness_financial_violation=False,
          readiness_real_orders_violation=False,legacy_generations_cap=None,legacy_fail_gen=False,
          proof_evidence_mismatch=None):
    today=datetime.now(ZoneInfo('America/New_York')).date();target=today+timedelta(days=target_offset)
    anchor=payload(today,'1118070');cand=payload(target)
    if mutate:mutate(cand)
    event=event_for(cand);rule_sha=a.digest(cand);source_sha=a.digest(event);scope_key=a.digest(SCOPE)
    dbp=tmp_path/f'daily-{target.isoformat()}.sqlite';db=sqlite3.connect(dbp)
    db.executescript('create table v11_meta(key text primary key,value text);'
      'create table v11_records(seq integer primary key,record_id text unique,kind text,event_id text,recorded_at real,available_at real,body text,body_sha256 text);')
    db.executemany('insert into v11_meta values(?,?)',[('version','alpha_v11_evidence_v1'),('namespace','CHALLENGER:katl-shadow')])
    sr=append(db,1,'station-raw','STATION_METADATA','station:KATL',{'payload':{'station':'KATL'},'evidence_class':'PUBLIC_OBSERVED'})
    sm=append(db,2,'station-meta','REGISTRY','station:KATL',{'details':{'action':'METADATA','metadata_fingerprint':'e'*64,'material_changed':metadata_material_changed},'evidence':[sr]})
    ready=append(db,3,'readiness','MEASUREMENT','station:KATL',{'details':{
      'financial_authority':True if readiness_financial_violation else False,
      'real_orders':True if readiness_real_orders_violation else False,
      'release_git_sha':'0'*40 if readiness_release_mismatch else RELEASE_GIT,'release_tree_sha':RELEASE_TREE}})
    if inject_demotion:
        append(db,999,'demotion-row','REGISTRY','station:KATL',{'details':{'action':'DEMOTION','scope_key':scope_key,'reason':'test'}})
    raw=append(db,4,'raw','RULES',cand['event_id'],{'evidence_class':'PUBLIC_OBSERVED','payload':{'event':event}})
    received_at=time.time()-100000 if stale_receipt else time.time()
    rule=append(db,5,'rule','RULE_STATE',cand['event_id'],{'details':{'quarantined':False,'changed':False,'fingerprint':rule_sha,
      'preimage':cand,'source_event_sha256':source_sha,'source_received_at':received_at},'evidence':[raw]})
    fixed={'station_raw':sr,'station_metadata':sm,'technical_readiness':ready}
    for i,cap in enumerate(a.REQUIRED_CAPS,6):
        refs=[ready]
        if cap=='IDENTITY':refs=[sm,rule]
        elif cap=='RULE_SEMANTICS':refs=[rule,ready]
        elif cap=='SOURCE_INTEGRITY':refs=[sr,raw,ready]
        if proof_omit==cap:continue
        if churn_stale_cap==cap:
            # Real-world shape pinned directly: several fresh (seq>rule.seq), identity-matching,
            # result/financial_authority-valid re-mints whose checker_version is superseded/stale
            # land in scan order (seq ascending) strictly BEFORE the one true current-binding
            # match, mirroring the live preparer's ~5-minute re-mint cadence against a long-unchanged
            # rule_fingerprint. The staged skip-not-raise logic must still resolve the later match;
            # the old hard-raise-inside-scan ordering (ab0a9c9) would instead poison the whole
            # capability on the FIRST stale row, before ever reaching it.
            for k in range(3):
                append(db,70000+i*10+k,f'rollforward:capability:{cand["event_id"]}:stale:{cap.lower()}:{k}','REGISTRY','station:KATL',
                  {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
                   'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS',
                   'checker_version':'STALE_CHECKER_VERSION_SUPERSEDED'},'evidence':refs})
            append(db,90000+i*10,f'rollforward:capability:{cand["event_id"]}:current:{cap.lower()}','REGISTRY','station:KATL',
              {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
               'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS',
               'checker_version':CHECKER},'evidence':refs})
            continue
        if legacy_generations_cap==cap:
            # Real live shape (T1): capabilities whose evidence references the rotating
            # RULE_STATE/RULES records directly (IDENTITY -> rule, SOURCE_INTEGRITY -> raw) get a
            # DIFFERENT evidence id/sha from every one of the preparer's historical ~5-minute
            # generations for an unchanged rule_fingerprint, since each prior generation points at
            # its own now-superseded rule/raw pair. These rows are seq-ordered strictly BEFORE the
            # real rule record (seq 5), so the real fix's freshness filter (seq>rule.seq) drops
            # them before evidence is ever compared -- it is seq alone that makes this safe, not
            # evidence equality. ab0a9c9's ordering (hard evidence check over all `loose` rows
            # before the seq filter) would instead raise PROOF_EVIDENCE on the very first one.
            for g in range(2):
                gseq=-100-g*10
                oraw=append(db,gseq,f'raw-legacy-{g}','RULES',cand['event_id'],{'evidence_class':'PUBLIC_OBSERVED','payload':{'event':event}})
                orule=append(db,gseq+1,f'rule-legacy-{g}','RULE_STATE',cand['event_id'],{'details':{'quarantined':False,'changed':False,
                  'fingerprint':rule_sha,'preimage':cand,'source_event_sha256':source_sha,'source_received_at':received_at},'evidence':[oraw]})
                orefs={'IDENTITY':[sm,orule],'RULE_SEMANTICS':[orule,ready],'SOURCE_INTEGRITY':[sr,oraw,ready]}.get(cap,[ready])
                append(db,gseq+2,f'rollforward:capability:{cand["event_id"]}:legacy:{cap.lower()}:{g}','REGISTRY','station:KATL',
                  {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
                   'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,
                   'result':'FAIL' if (legacy_fail_gen and g==0) else 'PASS',
                   'checker_version':CHECKER},'evidence':orefs})
            # No `continue`: the genuine current-binding row is still appended below.
        if proof_fail_then_pass==cap:
            # A genuine FAIL for this exact identity (scope_key/capability/metadata_fingerprint/
            # rule_fingerprint), followed in scan order by a later, fresh, fully-matching PASS.
            # result/financial_authority are checked unconditionally across every identity-matching
            # row (stage 2, hard and non-skippable), so the later PASS must never paper over the
            # earlier FAIL.
            append(db,i,f'rollforward:capability:{cand["event_id"]}:5:{cap.lower()}:failed','REGISTRY','station:KATL',
              {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
               'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'FAIL',
               'checker_version':CHECKER},'evidence':refs})
            append(db,i+100,f'rollforward:capability:{cand["event_id"]}:5:{cap.lower()}:later_pass','REGISTRY','station:KATL',
              {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
               'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS',
               'checker_version':CHECKER},'evidence':refs})
            continue
        if proof_checker_mismatch==cap:
            # Only a wrong-checker_version proof exists for this capability: the checker_version
            # filter must still reject it (not silently accept), leaving zero hits.
            append(db,i,f'rollforward:capability:{cand["event_id"]}:5:{cap.lower()}:wrongchecker','REGISTRY','station:KATL',
              {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
               'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS',
               'checker_version':'SOME_OTHER_CHECKER_VERSION'},'evidence':refs})
            continue
        if proof_scope_mismatch==cap:
            # Only a wrong-scope proof exists for this capability: the scope filter must still
            # reject it (not silently accept), leaving zero hits.
            wrong_scope=dict(SCOPE,family='LOW')
            append(db,i,f'rollforward:capability:{cand["event_id"]}:5:{cap.lower()}:wrongscope','REGISTRY','station:KATL',
              {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':wrong_scope,'capability':cap,
               'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS',
               'checker_version':CHECKER},'evidence':refs})
            continue
        if proof_evidence_mismatch==cap:
            # Only a fresh, otherwise-valid (correct checker_version/scope/result) proof exists for
            # this capability, but its evidence points at the wrong records (the generic `default`
            # binding instead of this capability's own refs): the evidence filter must still reject
            # it, leaving zero hits, not silently accept a proof bound to the wrong evidence.
            append(db,i,f'rollforward:capability:{cand["event_id"]}:5:{cap.lower()}:wrongevidence','REGISTRY','station:KATL',
              {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
               'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS',
               'checker_version':CHECKER},'evidence':[ready]})
            continue
        append(db,i,f'rollforward:capability:{cand["event_id"]}:5:{cap.lower()}','REGISTRY','station:KATL',
          {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
           'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'FAIL' if cap==proof_fail else 'PASS',
           'checker_version':CHECKER},'evidence':refs,
          **({'financial_authority':True} if proof_financial==cap else {})})
        if proof_duplicate==cap:
            # A second, substantively IDENTICAL re-mint of the same capability/rule_fingerprint
            # identity at a later seq: this is the real-world "preparer reissues every 5 minutes"
            # shape and must be accepted (the whole point of the fix), not treated as ambiguous.
            append(db,i+100,f'rollforward:capability:{cand["event_id"]}:5:{cap.lower()}:dup','REGISTRY','station:KATL',
              {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
               'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS',
               'checker_version':CHECKER},'evidence':refs})
    rule2=None
    if second_rule:
        # A second RULE_STATE for the same event/content minted later than the capability proofs
        # above, with a different record_id. Used to exercise RULE_NOT_LATEST (request still points
        # at 'rule') and CAPABILITY_BEFORE_RULE (request repointed at 'rule2' by the caller, which
        # leaves every existing capability proof at a seq before the referenced rule record).
        rule2=append(db,14,'rule2','RULE_STATE',cand['event_id'],{'details':{'quarantined':False,'changed':False,'fingerprint':rule_sha,
          'preimage':cand,'source_event_sha256':source_sha,'source_received_at':received_at},'evidence':[raw]})
    db.commit();db.close();os.chmod(dbp,0o444)
    live=copy.deepcopy(event)
    if bad_live_token:live['markets'][0]['clobTokenIds']=json.dumps(['1','2'])
    a.live_event=lambda _d,e=live:copy.deepcopy(e)
    manifest=tmp_path/'station.json';manifest.write_text(a.canonical({'version':a.MANIFEST_VERSION,'reviews':[]}))
    mmpath=tmp_path/'model-manifest.json';mmpath.write_text('{"models":[]}')
    mspath=tmp_path/'model-state.json';mspath.write_text('{"state":"ok"}')
    policy={'version':a.POLICY_VERSION,'namespace':'CHALLENGER:katl-shadow','stage':'SHADOW','scope':SCOPE,'scope_key':scope_key,
      'metadata_fingerprint':'e'*64,'anchor_rule_payload':anchor,'envelope_sha256':a.digest(a.normalized_rule(anchor)),
      'required_capabilities':list(a.REQUIRED_CAPS),'checker_version':CHECKER,'fixed_evidence':fixed,
      'release_git_sha':RELEASE_GIT,'release_tree_sha':RELEASE_TREE,
      'model_manifest_path':str(mmpath),'model_manifest_sha256':hashlib.sha256(mmpath.read_bytes()).hexdigest(),
      'model_state_path':str(mspath),'model_state_sha256':hashlib.sha256(mspath.read_bytes()).hexdigest(),
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

def test_rule_receipt_stale(tmp_path):
    pp,rp=setup(tmp_path,stale_receipt=True)
    with pytest.raises(a.Refusal,match='RULE_RECEIPT_STALE'):a.verify(pp,rp)

def test_symlink_refused(tmp_path):
    # Regression test: user_file() must check symlink-ness on the UNRESOLVED path before
    # resolving it. If resolve() happens first, is_symlink() on the resolved path can never
    # see the symlink and FILE_SYMLINK_REFUSED can never fire.
    target=tmp_path/'real.txt';target.write_text('x');os.chmod(target,0o600)
    link=tmp_path/'link.txt';link.symlink_to(target)
    with pytest.raises(a.Refusal,match='FILE_SYMLINK_REFUSED'):
        a.user_file(str(link),str(tmp_path),os.getuid(),False)

def test_root_custody_refused(tmp_path):
    f=tmp_path/'root_target.txt';f.write_text('x');os.chmod(f,0o600)
    with pytest.raises(a.Refusal,match='ROOT_FILE_CUSTODY'):
        a.root_file(str(f))

def test_root_custody_symlink_refused(tmp_path):
    target=tmp_path/'real2.txt';target.write_text('x')
    link=tmp_path/'link2.txt';link.symlink_to(target)
    with pytest.raises(a.Refusal,match='FILE_SYMLINK_REFUSED'):
        a.root_file(str(link))

def test_lock_path_prefix_refused(tmp_path):
    # Real production LOCK_PATH prefix check, exercised directly so it stays covered without
    # requiring actual root ownership of /var/lock/alpha-v11 (see other publish tests, which
    # monkeypatch lock_path_for to a tmp_path lock purely to avoid a root-owned directory).
    with pytest.raises(a.Refusal,match='LOCK_PATH'):
        a.lock_path_for({'lock_path':str(tmp_path/'evil.lock')})
    assert a.lock_path_for({'lock_path':'/var/lock/alpha-v11/x.lock'})==Path('/var/lock/alpha-v11/x.lock')

def test_publish_fresh(tmp_path,monkeypatch):
    pp,rp=setup(tmp_path,target_offset=0)
    monkeypatch.setattr(a,'root_file',lambda p:Path(p))
    monkeypatch.setattr(a,'lock_path_for',lambda policy:tmp_path/'test.lock')
    out=a.publish(pp,rp)
    assert out['published'] is True and out['state']=='PUBLISHED'
    assert out['financial_authority'] is False and out['activation_authorized'] is False
    policy=json.loads(pp.read_text());manifest=json.loads(Path(policy['review_manifest']).read_text())
    assert len(manifest['reviews'])==1
    assert manifest['reviews'][0]['rule_fingerprint']==out['verified']['rule_fingerprint']
    assert hashlib.sha256(Path(policy['review_manifest']).read_bytes()).hexdigest()==out['new_manifest_sha256']

def test_publish_existing_review_republish(tmp_path,monkeypatch):
    # An expired exact-match (same namespace/stage/scope_key/metadata_fingerprint/rule_fingerprint)
    # review is NOT republished and does NOT raise EXISTING_REVIEW_CONFLICT: publish() returns the
    # existing manifest entry unchanged, reporting published=False/ALREADY_EXACT_REVIEW_PRESENT, with
    # the original (expired) expires_at preserved verbatim. EXISTING_REVIEW_CONFLICT is reserved for
    # a genuine capability_proofs mismatch on an otherwise-exact match (see the conflict test below).
    pp,rp=setup(tmp_path,target_offset=0)
    monkeypatch.setattr(a,'root_file',lambda p:Path(p))
    monkeypatch.setattr(a,'lock_path_for',lambda policy:tmp_path/'test.lock')
    policy=json.loads(pp.read_text());verified=a.verify(pp,rp)
    review=dict(verified['review']);review['expires_at']=time.time()-10
    manifest_path=Path(policy['review_manifest'])
    manifest_path.write_text(a.canonical({'version':a.MANIFEST_VERSION,'reviews':[review]}))
    req=json.loads(rp.read_text());req['expected_manifest_sha256']=hashlib.sha256(manifest_path.read_bytes()).hexdigest();rp.write_text(json.dumps(req))
    out=a.publish(pp,rp)
    assert out['published'] is False
    assert out['state']=='ALREADY_EXACT_REVIEW_PRESENT'
    assert out['review']['expires_at']==review['expires_at']

def test_publish_existing_review_conflict(tmp_path,monkeypatch):
    pp,rp=setup(tmp_path,target_offset=0)
    monkeypatch.setattr(a,'root_file',lambda p:Path(p))
    monkeypatch.setattr(a,'lock_path_for',lambda policy:tmp_path/'test.lock')
    policy=json.loads(pp.read_text());verified=a.verify(pp,rp)
    review=dict(verified['review']);review['capability_proofs']=dict(review['capability_proofs'])
    review['capability_proofs']['IDENTITY']={'id':'some-other-record','sha256':'0'*64}
    manifest_path=Path(policy['review_manifest'])
    manifest_path.write_text(a.canonical({'version':a.MANIFEST_VERSION,'reviews':[review]}))
    req=json.loads(rp.read_text());req['expected_manifest_sha256']=hashlib.sha256(manifest_path.read_bytes()).hexdigest();rp.write_text(json.dumps(req))
    with pytest.raises(a.Refusal,match='EXISTING_REVIEW_CONFLICT'):
        a.publish(pp,rp)

def test_current_no_request(tmp_path):
    pp,rp=setup(tmp_path,target_offset=0)
    out=a.current(pp)
    assert out['published'] is False and out['state']=='NO_CURRENT_DAY_REQUEST'
    assert out['financial_authority'] is False and out['activation_authorized'] is False

def test_current_policy_version_mismatch(tmp_path,monkeypatch):
    pp,rp=setup(tmp_path,target_offset=0)
    monkeypatch.setattr(a,'root_file',lambda p:Path(p))
    policy=json.loads(pp.read_text());policy['version']='alpha_v11_daily_review_rollforward_policy_v2';pp.write_text(json.dumps(policy))
    today=datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    today_req=Path(policy['allowed_request_root'])/f'{today}.json';today_req.write_text(rp.read_text())
    with pytest.raises(a.Refusal,match='POLICY_VERSION'):
        a.current(pp)

def test_current_v1_request(tmp_path,monkeypatch):
    pp,rp=setup(tmp_path,target_offset=0)
    monkeypatch.setattr(a,'root_file',lambda p:Path(p))
    policy=json.loads(pp.read_text())
    req=json.loads(rp.read_text());req['version']='alpha_v11_daily_review_rollforward_request_v1'
    today=datetime.now(ZoneInfo('America/New_York')).date().isoformat()
    today_req=Path(policy['allowed_request_root'])/f'{today}.json';today_req.write_text(json.dumps(req))
    with pytest.raises(a.Refusal,match='REQUEST_VERSION'):
        a.current(pp)

def test_capability_duplicate_benign_accepted(tmp_path):
    # Real-world shape: the live preparer re-mints a fresh, substantively identical
    # CAPABILITY_EVIDENCE row for the same capability/rule_fingerprint every ~5 minutes. Two such
    # rows (same result/checker_version/scope/evidence, different record_id/seq) must resolve to a
    # single accepted proof (the higher-seq one), not CAPABILITY_AMBIGUOUS_OR_MISSING.
    pp,rp=setup(tmp_path,proof_duplicate='PROTECTED_RISK')
    out=a.verify(pp,rp)
    assert out['review']['capability_proofs']['PROTECTED_RISK']['id'].endswith(':dup')

def test_capability_missing(tmp_path):
    pp,rp=setup(tmp_path,proof_omit='PROTECTED_RISK')
    with pytest.raises(a.Refusal,match='CAPABILITY_AMBIGUOUS_OR_MISSING_PROTECTED_RISK'):
        a.verify(pp,rp)

def test_capability_before_rule(tmp_path):
    # Every existing capability proof was minted before the rule record the request actually
    # references (same content/fingerprint, later seq, different record_id) — a stale/superseded
    # binding in the wrong direction, which must stay a hard refusal, not a skippable candidate.
    pp,rp=setup(tmp_path,second_rule=True)
    req=json.loads(rp.read_text());req['rule_record_id']='rule2';rp.write_text(json.dumps(req))
    with pytest.raises(a.Refusal,match='CAPABILITY_BEFORE_RULE_ACCOUNTING'):
        a.verify(pp,rp)

def test_rule_not_latest(tmp_path):
    # A newer RULE_STATE for the same event now exists (same content, later seq, new record_id);
    # the request's referenced rule record is no longer the latest, even though its own content is
    # still fully valid in isolation.
    pp,rp=setup(tmp_path,second_rule=True)
    with pytest.raises(a.Refusal,match='RULE_NOT_LATEST'):
        a.verify(pp,rp)

def test_rule_financial_authority(tmp_path):
    pp,rp=setup(tmp_path,mutate=lambda p:p.__setitem__('financial_authority',True))
    with pytest.raises(a.Refusal,match='RULE_FINANCIAL_AUTHORITY'):
        a.verify(pp,rp)

def test_metadata_binding(tmp_path):
    pp,rp=setup(tmp_path,metadata_material_changed=True)
    with pytest.raises(a.Refusal,match='METADATA_BINDING'):
        a.verify(pp,rp)

def test_readiness_release(tmp_path):
    pp,rp=setup(tmp_path,readiness_release_mismatch=True)
    with pytest.raises(a.Refusal,match='READINESS_RELEASE'):
        a.verify(pp,rp)

def test_model_manifest_changed(tmp_path,monkeypatch):
    pp,rp=setup(tmp_path,target_offset=0)
    monkeypatch.setattr(a,'root_file',lambda p:Path(p))
    monkeypatch.setattr(a,'lock_path_for',lambda policy:tmp_path/'test.lock')
    policy=json.loads(pp.read_text());policy['model_manifest_sha256']='0'*64;pp.write_text(json.dumps(policy))
    with pytest.raises(a.Refusal) as ei:
        a.publish(pp,rp)
    assert str(ei.value)=='MODEL_MANIFEST_CHANGED'

def test_model_state_changed(tmp_path,monkeypatch):
    pp,rp=setup(tmp_path,target_offset=0)
    monkeypatch.setattr(a,'root_file',lambda p:Path(p))
    monkeypatch.setattr(a,'lock_path_for',lambda policy:tmp_path/'test.lock')
    policy=json.loads(pp.read_text());policy['model_state_sha256']='0'*64;pp.write_text(json.dumps(policy))
    with pytest.raises(a.Refusal) as ei:
        a.publish(pp,rp)
    assert str(ei.value)=='MODEL_STATE_CHANGED'

def test_model_manifest_changed_post(tmp_path,monkeypatch):
    # Simulate the model manifest changing underneath us between the pre-write check and the
    # post-write recheck. Detection-only (as in the deployed code): the review manifest write has
    # already landed on disk by the time this fires.
    pp,rp=setup(tmp_path,target_offset=0)
    monkeypatch.setattr(a,'root_file',lambda p:Path(p))
    monkeypatch.setattr(a,'lock_path_for',lambda policy:tmp_path/'test.lock')
    policy=json.loads(pp.read_text());mmpath=Path(policy['model_manifest_path'])
    real_replace=os.replace
    def sneaky(src,dst):
        real_replace(src,dst);mmpath.write_text('{"models":["tampered"]}')
    monkeypatch.setattr(os,'replace',sneaky)
    with pytest.raises(a.Refusal) as ei:
        a.publish(pp,rp)
    assert str(ei.value)=='MODEL_MANIFEST_CHANGED_POST'
    manifest=json.loads(Path(policy['review_manifest']).read_text())
    assert len(manifest['reviews'])==1

def test_model_state_changed_post(tmp_path,monkeypatch):
    pp,rp=setup(tmp_path,target_offset=0)
    monkeypatch.setattr(a,'root_file',lambda p:Path(p))
    monkeypatch.setattr(a,'lock_path_for',lambda policy:tmp_path/'test.lock')
    policy=json.loads(pp.read_text());mspath=Path(policy['model_state_path'])
    real_replace=os.replace
    def sneaky(src,dst):
        real_replace(src,dst);mspath.write_text('{"state":"tampered"}')
    monkeypatch.setattr(os,'replace',sneaky)
    with pytest.raises(a.Refusal) as ei:
        a.publish(pp,rp)
    assert str(ei.value)=='MODEL_STATE_CHANGED_POST'
    manifest=json.loads(Path(policy['review_manifest']).read_text())
    assert len(manifest['reviews'])==1

def test_capability_legacy_generations_then_current_accepted(tmp_path):
    # T1 pinning test: kills mutant M3 (ab0a9c9's hard evidence check over all `loose` rows
    # before the seq filter). On this fixture M3 raises PROOF_EVIDENCE on the first pre-rule
    # legacy generation; the real fix (seq filter first) must pass using only the genuine row.
    pp,rp=setup(tmp_path,legacy_generations_cap='IDENTITY')
    out=a.verify(pp,rp)
    assert 'legacy' not in out['review']['capability_proofs']['IDENTITY']['id']

def test_capability_legacy_generation_fail_refused(tmp_path):
    # T1 pinning test: kills mutant M4 (stage-2 result/financial_authority check applied only to
    # the selected max-seq hit). A FAIL on an older, pre-rule generation must still hard-refuse
    # even though the genuine current-binding row is a clean PASS.
    pp,rp=setup(tmp_path,legacy_generations_cap='IDENTITY',legacy_fail_gen=True)
    with pytest.raises(a.Refusal,match='PROOF_RESULT_IDENTITY'):
        a.verify(pp,rp)

def test_capability_churn_stale_checker_then_current_accepted(tmp_path):
    # Several fresh (seq>rule.seq) re-mints with a superseded checker_version land in scan order
    # before the one true current-binding row; the skip-not-raise checker_version filter must
    # still resolve to the later match.
    pp,rp=setup(tmp_path,churn_stale_cap='EXECUTION_MECHANICS')
    out=a.verify(pp,rp)
    assert out['review']['capability_proofs']['EXECUTION_MECHANICS']['id'].endswith(':current:execution_mechanics')

def test_capability_fail_then_pass_refused(tmp_path):
    # Stage-2's result/financial_authority check runs over every identity-matching fresh row, not
    # just the final selected one: an earlier FAIL can never be papered over by a later fresh PASS.
    pp,rp=setup(tmp_path,proof_fail_then_pass='CONSERVATIVE_CONTRACT_VALUATION')
    with pytest.raises(a.Refusal,match='PROOF_RESULT_CONSERVATIVE_CONTRACT_VALUATION'):
        a.verify(pp,rp)

def test_capability_checker_mismatch_refused(tmp_path):
    # Kills M12: a fresh, otherwise-valid PASS proof with the wrong checker_version and no
    # correct proof must still refuse (the skip filter must not silently accept it).
    pp,rp=setup(tmp_path,proof_checker_mismatch='FORECAST_IDENTITY')
    with pytest.raises(a.Refusal,match='CAPABILITY_AMBIGUOUS_OR_MISSING_FORECAST_IDENTITY'):
        a.verify(pp,rp)

def test_capability_scope_mismatch_refused(tmp_path):
    # Kills M13: a fresh, otherwise-valid PASS proof with the wrong scope and no correct proof
    # must still refuse.
    pp,rp=setup(tmp_path,proof_scope_mismatch='EXECUTION_MECHANICS')
    with pytest.raises(a.Refusal,match='CAPABILITY_AMBIGUOUS_OR_MISSING_EXECUTION_MECHANICS'):
        a.verify(pp,rp)

def test_proof_financial_refused(tmp_path):
    # Kills M5: a proof-level financial_authority claim is a hard, non-skippable refusal even
    # though the same row is otherwise a fully matching fresh PASS.
    pp,rp=setup(tmp_path,proof_financial='PROTECTED_RISK')
    with pytest.raises(a.Refusal,match='PROOF_FINANCIAL_PROTECTED_RISK'):
        a.verify(pp,rp)

def test_readiness_financial_violation_refused(tmp_path):
    pp,rp=setup(tmp_path,readiness_financial_violation=True)
    with pytest.raises(a.Refusal,match='READINESS_NONFINANCIAL'):
        a.verify(pp,rp)

def test_readiness_real_orders_violation_refused(tmp_path):
    pp,rp=setup(tmp_path,readiness_real_orders_violation=True)
    with pytest.raises(a.Refusal,match='READINESS_NONFINANCIAL'):
        a.verify(pp,rp)

def test_fixed_keys_refused(tmp_path):
    pp,rp=setup(tmp_path)
    policy=json.loads(pp.read_text());del policy['fixed_evidence']['technical_readiness'];pp.write_text(json.dumps(policy))
    with pytest.raises(a.Refusal,match='FIXED_KEYS'):
        a.verify(pp,rp)

def test_policy_review_ttl_refused(tmp_path):
    pp,rp=setup(tmp_path)
    policy=json.loads(pp.read_text());policy['review_ttl_seconds']=0;pp.write_text(json.dumps(policy))
    with pytest.raises(a.Refusal,match='POLICY_REVIEW_TTL'):
        a.verify(pp,rp)

def test_demotion_refused(tmp_path):
    pp,rp=setup(tmp_path,inject_demotion=True)
    with pytest.raises(a.Refusal,match='DEMOTION'):
        a.verify(pp,rp)

def test_publish_remint_content_sha256_match_accepted(tmp_path,monkeypatch):
    # Unit-level check of publish()'s comparison logic in isolation: a stored review whose every
    # capability_proofs id/sha256 differs (the live preparer re-mints these every ~5 minutes) but
    # whose content_sha256 was copied verbatim from the same verify() result must be treated as the
    # identical claim (ALREADY_EXACT_REVIEW_PRESENT), not EXISTING_REVIEW_CONFLICT. This does not by
    # itself prove content_sha256 is stable across a REAL second cycle (see the two-cycle test
    # below for that) -- it only proves publish()'s own comparison does the right thing once given
    # two equal content_sha256 values.
    pp,rp=setup(tmp_path,target_offset=0)
    monkeypatch.setattr(a,'root_file',lambda p:Path(p))
    monkeypatch.setattr(a,'lock_path_for',lambda policy:tmp_path/'test.lock')
    policy=json.loads(pp.read_text());verified=a.verify(pp,rp)
    stored_proofs={cap:{'id':'remint-'+cap,'sha256':'1'*64,'content_sha256':p['content_sha256']}
      for cap,p in verified['review']['capability_proofs'].items()}
    review=dict(verified['review']);review['capability_proofs']=stored_proofs
    manifest_path=Path(policy['review_manifest'])
    manifest_path.write_text(a.canonical({'version':a.MANIFEST_VERSION,'reviews':[review]}))
    req=json.loads(rp.read_text());req['expected_manifest_sha256']=hashlib.sha256(manifest_path.read_bytes()).hexdigest();rp.write_text(json.dumps(req))
    out=a.publish(pp,rp)
    assert out['published'] is False
    assert out['state']=='ALREADY_EXACT_REVIEW_PRESENT'
    assert out['review']['capability_proofs']==stored_proofs

def test_publish_remint_genuinely_different_claim_conflict(tmp_path,monkeypatch):
    # Negative counterpart to the content_sha256-match-accepted test, using the same hand-crafted-
    # mismatch technique: a stored review whose content_sha256 for one capability is simply a wrong
    # literal (not derived from any real covered-field change) must still hard-refuse. This proves
    # publish()'s own comparison does not silently ignore a per-capability mismatch; it is not by
    # itself proof that every input the signature is supposed to cover actually varies it correctly
    # (see test_publish_remint_checker_version_change_conflict and
    # test_publish_remint_fixed_evidence_repin_conflict below for that).
    pp,rp=setup(tmp_path,target_offset=0)
    monkeypatch.setattr(a,'root_file',lambda p:Path(p))
    monkeypatch.setattr(a,'lock_path_for',lambda policy:tmp_path/'test.lock')
    policy=json.loads(pp.read_text());verified=a.verify(pp,rp)
    stored_proofs={cap:{'id':'remint-'+cap,'sha256':'1'*64,'content_sha256':p['content_sha256']}
      for cap,p in verified['review']['capability_proofs'].items()}
    stored_proofs['IDENTITY']=dict(stored_proofs['IDENTITY'],content_sha256='0'*64)
    review=dict(verified['review']);review['capability_proofs']=stored_proofs
    manifest_path=Path(policy['review_manifest'])
    manifest_path.write_text(a.canonical({'version':a.MANIFEST_VERSION,'reviews':[review]}))
    req=json.loads(rp.read_text());req['expected_manifest_sha256']=hashlib.sha256(manifest_path.read_bytes()).hexdigest();rp.write_text(json.dumps(req))
    with pytest.raises(a.Refusal,match='EXISTING_REVIEW_CONFLICT'):
        a.publish(pp,rp)

def test_content_sha256_not_constant_across_independent_generations(tmp_path):
    # Named for exactly what this kills, per the independent review: a content_sha256 that has
    # collapsed to a hardcoded constant (ignoring all its inputs). It does NOT isolate
    # rule_fingerprint's own contribution -- these two independent setup() calls also get fresh
    # station_raw/station_metadata/technical_readiness timestamps, so fixed_evidence_sha256 differs
    # too, and rule_fingerprint is in any case redundant with the manifest's own exact-match key
    # (namespace/stage/scope_key/metadata_fingerprint/rule_fingerprint), so dropping it from the
    # signature is a safe no-op for publish(). What actually must NOT be dropped -- checker_version
    # and fixed_evidence_sha256, the two fields that can genuinely vary between two proofs that
    # already match the same manifest entry -- is covered by
    # test_publish_remint_checker_version_change_conflict and
    # test_publish_remint_fixed_evidence_repin_conflict below.
    (tmp_path/'gen1').mkdir();(tmp_path/'gen2').mkdir()
    # live_event() is monkeypatched at module level by setup() itself (not via the pytest fixture),
    # so each verify() must run immediately after its own setup(), before the other setup() call
    # overwrites it.
    pp1,rp1=setup(tmp_path/'gen1',target_offset=1);out1=a.verify(pp1,rp1)
    pp2,rp2=setup(tmp_path/'gen2',target_offset=2);out2=a.verify(pp2,rp2)
    assert out1['review']['capability_proofs']['IDENTITY']['content_sha256']!=out2['review']['capability_proofs']['IDENTITY']['content_sha256']

def test_publish_remint_volatile_event_field_accepted(tmp_path,monkeypatch):
    # N1, reproduced for real: the live Gamma event carries volatile fields (liquidity, volume,
    # openInterest, updatedAt, series, ...) this fixture's event_for() deliberately does not model,
    # because bind_event() never checks them -- exactly the gap the independent review found on the
    # real 2026-10-07/-08 snapshots (~512/517 and ~237/238 distinct source_event_sha256 values for
    # one unchanged rule_fingerprint). This builds a GENUINE second cycle: a fresh RULE_STATE/RAW/
    # capability-proof generation (new record ids, same semantic rule content) whose RAW event
    # payload carries one such volatile field the first generation's did not. publish() for this
    # second generation must resolve to the SAME content_sha256 as the first and be accepted
    # (ALREADY_EXACT_REVIEW_PRESENT), not EXISTING_REVIEW_CONFLICT.
    pp,rp=setup(tmp_path,target_offset=0)
    monkeypatch.setattr(a,'root_file',lambda p:Path(p))
    monkeypatch.setattr(a,'lock_path_for',lambda policy:tmp_path/'test.lock')
    out1=a.publish(pp,rp)
    assert out1['published'] is True

    policy=json.loads(pp.read_text());req1=json.loads(rp.read_text())
    dbp=Path(req1['evidence_snapshot'])
    os.chmod(dbp,0o644)
    db=sqlite3.connect(dbp);db.row_factory=sqlite3.Row
    def mini(x):return {'id':x['id'],'sha256':x['sha256']}
    sr=mini(a.get(db,'station-raw'));sm=mini(a.get(db,'station-meta'));ready=mini(a.get(db,'readiness'));old_rule=a.get(db,'rule')
    cand=old_rule['body']['details']['preimage'];rule_sha=old_rule['body']['details']['fingerprint']
    received_at=old_rule['body']['details']['source_received_at'];scope_key=policy['scope_key']
    event2=event_for(cand);event2['liquidityNum']=12345.67  # volatile, unchecked by bind_event()
    source_sha2=a.digest(event2)

    maxseq=db.execute('select max(seq) from v11_records').fetchone()[0];at=time.time()
    def ap(seq,rid,kind,event_id,extra):
        body={'record_id':rid,'kind':kind,'event_id':event_id,'recorded_at':at,'available_at':at,
          'namespace':'CHALLENGER:katl-shadow','financial_authority':False,**extra};h=a.digest(body)
        db.execute('insert into v11_records values(?,?,?,?,?,?,?,?)',(seq,rid,kind,event_id,at,at,a.canonical(body),h))
        return {'id':rid,'sha256':h}
    raw2=ap(maxseq+1,'raw-cycle2','RULES',cand['event_id'],{'evidence_class':'PUBLIC_OBSERVED','payload':{'event':event2}})
    rule2=ap(maxseq+2,'rule-cycle2','RULE_STATE',cand['event_id'],{'details':{'quarantined':False,'changed':False,
      'fingerprint':rule_sha,'preimage':cand,'source_event_sha256':source_sha2,'source_received_at':received_at},'evidence':[raw2]})
    for i,cap in enumerate(a.REQUIRED_CAPS):
        refs2=[ready]
        if cap=='IDENTITY':refs2=[sm,rule2]
        elif cap=='RULE_SEMANTICS':refs2=[rule2,ready]
        elif cap=='SOURCE_INTEGRITY':refs2=[sr,raw2,ready]
        ap(maxseq+3+i,f'rollforward:capability:cycle2:{cap.lower()}','REGISTRY','station:KATL',
          {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
           'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS','checker_version':CHECKER},'evidence':refs2})
    db.commit();db.close();os.chmod(dbp,0o444)

    req2={'version':a.REQUEST_VERSION,'target_date':req1['target_date'],'evidence_snapshot':str(dbp),
      'evidence_snapshot_sha256':hashlib.sha256(dbp.read_bytes()).hexdigest(),'rule_record_id':'rule-cycle2',
      'expected_manifest_sha256':hashlib.sha256(Path(policy['review_manifest']).read_bytes()).hexdigest(),
      'prepared_at':time.time()}
    rp2=tmp_path/'request-cycle2.json';rp2.write_text(json.dumps(req2))
    a.live_event=lambda _d,e=event2:copy.deepcopy(e)
    out2=a.publish(pp,rp2)
    assert out2['published'] is False
    assert out2['state']=='ALREADY_EXACT_REVIEW_PRESENT'

def test_capability_evidence_mismatch_refused(tmp_path):
    # Kills the gap the independent review found: deleting the fresh-row evidence filter left the
    # whole suite green. A fresh, otherwise-valid (correct checker_version/scope/result) proof
    # bound to the WRONG evidence and no correct proof must still refuse.
    pp,rp=setup(tmp_path,proof_evidence_mismatch='IDENTITY')
    with pytest.raises(a.Refusal,match='CAPABILITY_AMBIGUOUS_OR_MISSING_IDENTITY'):
        a.verify(pp,rp)

def test_publish_remint_checker_version_change_conflict(tmp_path,monkeypatch):
    # Real covered-field change #1 (independent review, required change 1): a mid-day checker/
    # authority upgrade. The SAME rule record gets a fresh capability-proof generation tagged with
    # a NEW checker_version, and the policy is updated to match (mirroring a real reviewed-checker
    # rollout). This is a genuinely different claim -- produced by a different validator generation
    # -- and must still hard-refuse, not be waved through as a benign re-mint. Kills "drop
    # checker_version from content" (and the combined "drop checker_version and fixed_evidence").
    pp,rp=setup(tmp_path,target_offset=0)
    monkeypatch.setattr(a,'root_file',lambda p:Path(p))
    monkeypatch.setattr(a,'lock_path_for',lambda policy:tmp_path/'test.lock')
    out1=a.publish(pp,rp)
    assert out1['published'] is True

    policy=json.loads(pp.read_text());req1=json.loads(rp.read_text())
    dbp=Path(req1['evidence_snapshot'])
    os.chmod(dbp,0o644)
    db=sqlite3.connect(dbp);db.row_factory=sqlite3.Row
    def mini(x):return {'id':x['id'],'sha256':x['sha256']}
    sr=mini(a.get(db,'station-raw'));sm=mini(a.get(db,'station-meta'));ready=mini(a.get(db,'readiness'))
    old_rule=a.get(db,'rule');rule_mini=mini(old_rule);raw_mini=mini(a.get(db,'raw'))
    rule_sha=old_rule['body']['details']['fingerprint'];scope_key=policy['scope_key']
    NEW_CHECKER=CHECKER+'_V2'

    maxseq=db.execute('select max(seq) from v11_records').fetchone()[0];at=time.time()
    def ap(seq,rid,kind,event_id,extra):
        body={'record_id':rid,'kind':kind,'event_id':event_id,'recorded_at':at,'available_at':at,
          'namespace':'CHALLENGER:katl-shadow','financial_authority':False,**extra};h=a.digest(body)
        db.execute('insert into v11_records values(?,?,?,?,?,?,?,?)',(seq,rid,kind,event_id,at,at,a.canonical(body),h))
        return {'id':rid,'sha256':h}
    for i,cap in enumerate(a.REQUIRED_CAPS):
        refs2=[ready]
        if cap=='IDENTITY':refs2=[sm,rule_mini]
        elif cap=='RULE_SEMANTICS':refs2=[rule_mini,ready]
        elif cap=='SOURCE_INTEGRITY':refs2=[sr,raw_mini,ready]
        ap(maxseq+1+i,f'rollforward:capability:checkerv2:{cap.lower()}','REGISTRY','station:KATL',
          {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
           'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS','checker_version':NEW_CHECKER},'evidence':refs2})
    db.commit();db.close();os.chmod(dbp,0o444)

    policy['checker_version']=NEW_CHECKER;pp.write_text(json.dumps(policy))
    req2=dict(req1,evidence_snapshot_sha256=hashlib.sha256(dbp.read_bytes()).hexdigest(),
      expected_manifest_sha256=hashlib.sha256(Path(policy['review_manifest']).read_bytes()).hexdigest(),prepared_at=time.time())
    rp2=tmp_path/'request-checkerv2.json';rp2.write_text(json.dumps(req2))
    with pytest.raises(a.Refusal,match='EXISTING_REVIEW_CONFLICT'):
        a.publish(pp,rp2)

def test_publish_remint_fixed_evidence_repin_conflict(tmp_path,monkeypatch):
    # Real covered-field change #2 (independent review, required change 1): a mid-day re-pin of a
    # fixed-evidence record (e.g. a release restart minting a new technical_readiness row), with
    # the policy's fixed_evidence pointer updated to match. Same rule, same checker, same scope,
    # same result -- but a genuinely different fixed_evidence_sha256 -- must still hard-refuse.
    # Kills "drop fixed_evidence_sha256 from content" (and the combined drop-both mutant).
    pp,rp=setup(tmp_path,target_offset=0)
    monkeypatch.setattr(a,'root_file',lambda p:Path(p))
    monkeypatch.setattr(a,'lock_path_for',lambda policy:tmp_path/'test.lock')
    out1=a.publish(pp,rp)
    assert out1['published'] is True

    policy=json.loads(pp.read_text());req1=json.loads(rp.read_text())
    dbp=Path(req1['evidence_snapshot'])
    os.chmod(dbp,0o644)
    db=sqlite3.connect(dbp);db.row_factory=sqlite3.Row
    def mini(x):return {'id':x['id'],'sha256':x['sha256']}
    sr=mini(a.get(db,'station-raw'));sm=mini(a.get(db,'station-meta'))
    old_rule=a.get(db,'rule');rule_mini=mini(old_rule);raw_mini=mini(a.get(db,'raw'))
    rule_sha=old_rule['body']['details']['fingerprint'];scope_key=policy['scope_key']

    maxseq=db.execute('select max(seq) from v11_records').fetchone()[0];at=time.time()
    def ap(seq,rid,kind,event_id,extra):
        body={'record_id':rid,'kind':kind,'event_id':event_id,'recorded_at':at,'available_at':at,
          'namespace':'CHALLENGER:katl-shadow','financial_authority':False,**extra};h=a.digest(body)
        db.execute('insert into v11_records values(?,?,?,?,?,?,?,?)',(seq,rid,kind,event_id,at,at,a.canonical(body),h))
        return {'id':rid,'sha256':h}
    ready2=ap(maxseq+1,'readiness-v2','MEASUREMENT','station:KATL',{'details':{'financial_authority':False,'real_orders':False,
      'release_git_sha':RELEASE_GIT,'release_tree_sha':RELEASE_TREE}})
    for i,cap in enumerate(a.REQUIRED_CAPS):
        refs2=[ready2]
        if cap=='IDENTITY':refs2=[sm,rule_mini]
        elif cap=='RULE_SEMANTICS':refs2=[rule_mini,ready2]
        elif cap=='SOURCE_INTEGRITY':refs2=[sr,raw_mini,ready2]
        ap(maxseq+2+i,f'rollforward:capability:readyv2:{cap.lower()}','REGISTRY','station:KATL',
          {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
           'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS','checker_version':CHECKER},'evidence':refs2})
    db.commit();db.close();os.chmod(dbp,0o444)

    policy['fixed_evidence']['technical_readiness']=ready2;pp.write_text(json.dumps(policy))
    req2=dict(req1,evidence_snapshot_sha256=hashlib.sha256(dbp.read_bytes()).hexdigest(),
      expected_manifest_sha256=hashlib.sha256(Path(policy['review_manifest']).read_bytes()).hexdigest(),prepared_at=time.time())
    rp2=tmp_path/'request-readyv2.json';rp2.write_text(json.dumps(req2))
    with pytest.raises(a.Refusal,match='EXISTING_REVIEW_CONFLICT'):
        a.publish(pp,rp2)
