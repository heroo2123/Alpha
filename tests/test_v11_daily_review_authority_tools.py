from __future__ import annotations
import hashlib,importlib.util,json,os,sqlite3,time
from datetime import datetime,timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import pytest

# This targets tools/v11_daily_review_authority.py -- the live-deployed-equivalent file
# (byte-identical to the root-owned /usr/local/libexec/alpha-v11-daily-review-authority.py).
# It is a DIFFERENT, simpler module than host_trust/v11-daily-review-authority/authority.py
# (already covered by tests/test_v11_daily_review_authority.py) and than
# tools/v11_daily_review_rollforward_publisher.py (already covered by
# tests/test_v11_daily_review_rollforward_publisher.py). Do not confuse the three: this file's
# verify()/publish() use CAPABILITY_MISSING / CAPABILITY_NOT_PASS / SCOPE_DEMOTION_PRESENT (not
# CAPABILITY_AMBIGUOUS_OR_MISSING / CAPABILITY_FAIL / DEMOTION_PRESENT used by the publisher), has
# no content_sha256/model-manifest logic anywhere, and its root-custody checks run unconditionally
# inside verify() itself (protected_files), not only inside publish().

P=Path(__file__).resolve().parent.parent/'tools'/'v11_daily_review_authority.py'
s=importlib.util.spec_from_file_location('authtool',P);a=importlib.util.module_from_spec(s);s.loader.exec_module(a)
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
    return {'id':p['event_id'],'title':p['title'],'eventDate':p['target_date'],
      'description':p['strict_contract']['operative_rules'],'active':True,'closed':False,'archived':False,'markets':markets}

def append(db,seq,rid,kind,event,extra):
    at=time.time();body={'record_id':rid,'kind':kind,'event_id':event,'recorded_at':at,'available_at':at,
      'namespace':'CHALLENGER:katl-shadow','financial_authority':False,**extra};h=a.digest(body)
    db.execute('insert into v11_records values(?,?,?,?,?,?,?,?)',(seq,rid,kind,event,at,at,a.canonical(body),h))
    return {'id':rid,'sha256':h}

def setup(tmp_path,*,target_offset=1,proof_omit=None,proof_checker_mismatch=None,proof_scope_mismatch=None,
          proof_evidence_mismatch=None,proof_fail_then_pass=None,proof_financial=None,many_duplicates_cap=None,
          many_duplicates_count=55,churn_stale_cap=None,legacy_generations_cap=None,legacy_fail_gen=False,
          second_rule=False,inject_demotion=False,stale_receipt=False):
    today=datetime.now(ZoneInfo('America/New_York')).date();target=today+timedelta(days=target_offset)
    anchor=payload(today,'1118070');cand=payload(target)
    event=event_for(cand);rule_sha=a.digest(cand);source_sha=a.digest(event);scope_key=a.digest(SCOPE)
    dbp=tmp_path/f'daily-{target.isoformat()}.sqlite';db=sqlite3.connect(dbp)
    db.executescript('create table v11_meta(key text primary key,value text);'
      'create table v11_records(seq integer primary key,record_id text unique,kind text,event_id text,recorded_at real,available_at real,body text,body_sha256 text);')
    db.executemany('insert into v11_meta values(?,?)',[('version','alpha_v11_evidence_v1'),('namespace','CHALLENGER:katl-shadow')])
    sr=append(db,1,'station-raw','STATION_METADATA','station:KATL',{'payload':{'station':'KATL'},'evidence_class':'PUBLIC_OBSERVED'})
    sm=append(db,2,'station-meta','REGISTRY','station:KATL',{'details':{'action':'METADATA','metadata_fingerprint':'e'*64,'material_changed':False},'evidence':[sr]})
    ready=append(db,3,'readiness','MEASUREMENT','station:KATL',{'details':{
      'financial_authority':False,'real_orders':False,'release_git_sha':RELEASE_GIT,'release_tree_sha':RELEASE_TREE}})
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
            # Direct regression shape for the live defect: stale re-mints (wrong checker_version,
            # e.g. minted under a now-superseded checker build) coexist with a correctly-bound
            # current re-mint for the SAME still-valid rule_fingerprint. Pre-fix code iterated every
            # loose-matching row in ascending seq order and did
            # `need(dd.get('checker_version')==policy['checker_version'],'CAPABILITY_CHECKER:'+cap)`
            # unconditionally -- so the first (lower-seq) stale row killed verification outright even
            # though a later, correctly-bound row existed. Post-fix only requires that at least one
            # FRESH row match the current binding; stale-binding rows are skipped, not fatal.
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
            # Realistic live shape: capabilities whose evidence lineage names the rotating
            # RULE_STATE/RULES records directly (IDENTITY->rule, SOURCE_INTEGRITY->raw) accumulate
            # evidence refs pointing at now-superseded rule/raw pairs for every historical
            # ~5-minute generation, even though rule_fingerprint itself never changed. These rows sit
            # at seq strictly BEFORE the real rule record (seq 5). Pre-fix code had no seq>rule.seq
            # concept at all -- it validated every loose-matching row's evidence refs against the
            # CURRENT expected_evidence unconditionally, so the first (lowest-seq, i.e. legacy) row
            # hit `need(canonical(x['body'].get('evidence',[]))==canonical(expected_evidence[cap]),
            # 'CAPABILITY_EVIDENCE_REFS:'+cap)` and hard-refused before the genuine current row was
            # ever reached. Post-fix's seq>rr['seq'] freshness filter drops these before evidence is
            # even compared.
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
            append(db,i,f'rollforward:capability:{cand["event_id"]}:5:{cap.lower()}:wrongchecker','REGISTRY','station:KATL',
              {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
               'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS',
               'checker_version':'SOME_OTHER_CHECKER_VERSION'},'evidence':refs})
            continue
        if proof_scope_mismatch==cap:
            wrong_scope=dict(SCOPE,family='LOW')
            append(db,i,f'rollforward:capability:{cand["event_id"]}:5:{cap.lower()}:wrongscope','REGISTRY','station:KATL',
              {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':wrong_scope,'capability':cap,
               'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS',
               'checker_version':CHECKER},'evidence':refs})
            continue
        if proof_evidence_mismatch==cap:
            append(db,i,f'rollforward:capability:{cand["event_id"]}:5:{cap.lower()}:wrongevidence','REGISTRY','station:KATL',
              {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
               'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS',
               'checker_version':CHECKER},'evidence':[ready]})
            continue
        append(db,i,f'rollforward:capability:{cand["event_id"]}:5:{cap.lower()}','REGISTRY','station:KATL',
          {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
           'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS',
           'checker_version':CHECKER},'evidence':refs,
          **({'financial_authority':True} if proof_financial==cap else {})})
        if many_duplicates_cap==cap:
            # Direct regression test for the live WAITING_PROTECTED_REVIEW failure: dozens of
            # substantively identical re-mints (same result/checker_version/scope/evidence, only
            # record_id/seq differ) for ONE capability/rule_fingerprint, interleaved with ONE stale
            # (wrong-checker) historical row that precedes them. Pre-fix code's per-row
            # `need(dd.get('checker_version')==policy['checker_version'],'CAPABILITY_CHECKER:'+cap)`
            # would raise on that single stale row (encountered first, in ascending-seq order) before
            # ever reaching the 55 valid re-mints. The fix must accept all of it.
            append(db,i*1000+100,f'rollforward:capability:{cand["event_id"]}:5:{cap.lower()}:onestale','REGISTRY','station:KATL',
              {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
               'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS',
               'checker_version':'OLD_CHECKER_BEFORE_ROTATION'},'evidence':refs})
            for k in range(many_duplicates_count):
                append(db,i*1000+200+k,f'rollforward:capability:{cand["event_id"]}:5:{cap.lower()}:remint:{k}','REGISTRY','station:KATL',
                  {'details':{'action':'CAPABILITY_EVIDENCE','scope_key':scope_key,'scope':SCOPE,'capability':cap,
                   'metadata_fingerprint':'e'*64,'rule_fingerprint':rule_sha,'result':'PASS',
                   'checker_version':CHECKER},'evidence':refs})
    if second_rule:
        append(db,14,'rule2','RULE_STATE',cand['event_id'],{'details':{'quarantined':False,'changed':False,'fingerprint':rule_sha,
          'preimage':cand,'source_event_sha256':source_sha,'source_received_at':received_at},'evidence':[raw]})
    db.commit();db.close();os.chmod(dbp,0o444)
    manifest=tmp_path/'station.json';manifest.write_text(a.canonical({'version':a.MANIFEST_VERSION,'reviews':[]}))
    protected_path=tmp_path/'protected-release-file.bin';protected_path.write_bytes(b'alpha-v11-release-artifact')
    policy={'version':a.POLICY_VERSION,'namespace':'CHALLENGER:katl-shadow','stage':'SHADOW','scope':SCOPE,'scope_key':scope_key,
      'metadata_fingerprint':'e'*64,'anchor_rule_payload':anchor,'envelope_sha256':a.digest(a.normalized_payload(anchor)),
      'required_capabilities':list(a.REQUIRED_CAPS),'checker_version':CHECKER,'fixed_evidence':fixed,
      'release_git_sha':RELEASE_GIT,'release_tree_sha':RELEASE_TREE,
      'protected_files':{str(protected_path):hashlib.sha256(protected_path.read_bytes()).hexdigest()},
      'reviewer':'ROOT_DAILY_ROLLFORWARD_V2','maximum_target_days_ahead':3,'review_ttl_seconds':259200,
      'maximum_request_age_seconds':900,'allowed_request_root':str(tmp_path),'request_owner_uid':os.getuid(),
      'allowed_snapshot_root':str(tmp_path),'snapshot_owner_uid':os.getuid(),'review_manifest':str(manifest),
      'lock_path':'/var/lock/alpha-v11/test.lock'}
    pp=tmp_path/'policy.json';pp.write_text(json.dumps(policy))
    req={'version':a.REQUEST_VERSION,'target_date':target.isoformat(),'evidence_snapshot':str(dbp),
      'evidence_snapshot_sha256':hashlib.sha256(dbp.read_bytes()).hexdigest(),'rule_record_id':'rule2' if second_rule else 'rule',
      'expected_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),'prepared_at':time.time()}
    rp=tmp_path/'request.json';rp.write_text(json.dumps(req));return pp,rp

@pytest.fixture(autouse=True)
def _bypass_root_custody(monkeypatch):
    # verify() unconditionally calls root_custody_file() on every policy['protected_files'] entry
    # (and publish() additionally calls it on policy_path / review_manifest). These assert uid==0
    # root ownership, which a non-root test process can never satisfy; the actual content-hash check
    # (shafile(Path(f))==expected_sha) still runs for real and is unaffected by this bypass.
    monkeypatch.setattr(a,'root_custody_file',lambda p:None)

def patch_lock(monkeypatch,tmp_path):
    real_open=os.open
    def fake_open(path,flags,mode=0o777):
        if str(path).startswith('/var/lock/alpha-v11/'):path=tmp_path/'test.lock'
        return real_open(path,flags,mode)
    monkeypatch.setattr(os,'open',fake_open)

# ---- baseline ----

def test_valid(tmp_path):
    pp,rp=setup(tmp_path);out=a.verify(pp,rp)
    assert set(out['review']['capability_proofs'])==set(a.REQUIRED_CAPS)
    assert out['financial_authority'] is False
    assert out['activation_authorized'] is False
    for cap in a.REQUIRED_CAPS:
        proof=out['review']['capability_proofs'][cap]
        assert set(proof)=={'id','sha256'}

def test_snapshot_hash_mismatch(tmp_path):
    pp,rp=setup(tmp_path);r=json.loads(rp.read_text());r['evidence_snapshot_sha256']='0'*64;rp.write_text(json.dumps(r))
    with pytest.raises(a.Refusal,match='SNAPSHOT_HASH'):a.verify(pp,rp)

def test_stale_request(tmp_path):
    pp,rp=setup(tmp_path);r=json.loads(rp.read_text());r['prepared_at']=1;rp.write_text(json.dumps(r))
    with pytest.raises(a.Refusal,match='REQUEST_STALE'):a.verify(pp,rp)

def test_demotion_refused(tmp_path):
    pp,rp=setup(tmp_path,inject_demotion=True)
    with pytest.raises(a.Refusal,match='SCOPE_DEMOTION_PRESENT'):a.verify(pp,rp)

# ---- the live bug: benign duplicate re-mints (mixed with a stale historical row) must be tolerated ----

def test_capability_many_duplicates_with_one_stale_row_accepted(tmp_path):
    pp,rp=setup(tmp_path,many_duplicates_cap='ACCOUNTING',many_duplicates_count=55)
    out=a.verify(pp,rp)
    assert 'ACCOUNTING' in out['review']['capability_proofs']
    assert out['review']['capability_proofs']['ACCOUNTING']['id'].endswith(':remint:54')

def test_capability_churn_stale_checker_then_current_accepted(tmp_path):
    pp,rp=setup(tmp_path,churn_stale_cap='EXECUTION_MECHANICS')
    out=a.verify(pp,rp)
    assert out['review']['capability_proofs']['EXECUTION_MECHANICS']['id'].endswith(':current:execution_mechanics')

def test_capability_legacy_generations_then_current_accepted(tmp_path):
    pp,rp=setup(tmp_path,legacy_generations_cap='IDENTITY')
    out=a.verify(pp,rp)
    assert 'legacy' not in out['review']['capability_proofs']['IDENTITY']['id']

# ---- genuine FAIL / financial_authority still hard-refuse, non-skippable ----

def test_capability_fail_then_pass_still_refused(tmp_path):
    pp,rp=setup(tmp_path,proof_fail_then_pass='CONSERVATIVE_CONTRACT_VALUATION')
    with pytest.raises(a.Refusal,match='CAPABILITY_NOT_PASS:CONSERVATIVE_CONTRACT_VALUATION'):
        a.verify(pp,rp)

def test_capability_financial_authority_still_refused(tmp_path):
    pp,rp=setup(tmp_path,proof_financial='PROTECTED_RISK')
    with pytest.raises(a.Refusal,match='CAPABILITY_NOT_PASS:PROTECTED_RISK'):
        a.verify(pp,rp)

def test_capability_legacy_generation_fail_still_refused(tmp_path):
    # Even a stale (pre-rule) generation's FAIL is non-skippable: the result/financial_authority
    # check runs over every loose-matching row BEFORE the seq>rule.seq freshness filter is applied.
    pp,rp=setup(tmp_path,legacy_generations_cap='IDENTITY',legacy_fail_gen=True)
    with pytest.raises(a.Refusal,match='CAPABILITY_NOT_PASS:IDENTITY'):
        a.verify(pp,rp)

# ---- fresh-but-wrong-checker/-scope/-evidence, no valid alternative: must still refuse ----

def test_capability_checker_mismatch_no_alternative_refused(tmp_path):
    pp,rp=setup(tmp_path,proof_checker_mismatch='FORECAST_IDENTITY')
    with pytest.raises(a.Refusal,match='CAPABILITY_MISSING:FORECAST_IDENTITY'):
        a.verify(pp,rp)

def test_capability_scope_mismatch_no_alternative_refused(tmp_path):
    pp,rp=setup(tmp_path,proof_scope_mismatch='EXECUTION_MECHANICS')
    with pytest.raises(a.Refusal,match='CAPABILITY_MISSING:EXECUTION_MECHANICS'):
        a.verify(pp,rp)

def test_capability_evidence_mismatch_no_alternative_refused(tmp_path):
    pp,rp=setup(tmp_path,proof_evidence_mismatch='IDENTITY')
    with pytest.raises(a.Refusal,match='CAPABILITY_MISSING:IDENTITY'):
        a.verify(pp,rp)

def test_capability_missing_entirely_refused(tmp_path):
    pp,rp=setup(tmp_path,proof_omit='PROTECTED_RISK')
    with pytest.raises(a.Refusal,match='CAPABILITY_MISSING:PROTECTED_RISK'):
        a.verify(pp,rp)

# ---- all proofs predate the rule record ----

def test_capability_before_rule_when_all_proofs_predate(tmp_path):
    pp,rp=setup(tmp_path,second_rule=True)
    with pytest.raises(a.Refusal,match='CAPABILITY_BEFORE_RULE:ACCOUNTING'):
        a.verify(pp,rp)

# ---- publish(): unchanged coarse-manifest ALREADY_REVIEWED behavior (no content_sha256 logic) ----

def test_publish_fresh(tmp_path,monkeypatch):
    pp,rp=setup(tmp_path,target_offset=0)
    patch_lock(monkeypatch,tmp_path)
    out=a.publish(pp,rp)
    assert out['published'] is True
    policy=json.loads(pp.read_text());manifest=json.loads(Path(policy['review_manifest']).read_text())
    assert len(manifest['reviews'])==1

def test_publish_already_reviewed_is_noop(tmp_path,monkeypatch):
    pp,rp=setup(tmp_path,target_offset=0)
    patch_lock(monkeypatch,tmp_path)
    first=a.publish(pp,rp);assert first['published'] is True
    # Re-publish for the same day/rule: the coarse manifest match (namespace/stage/scope_key/
    # metadata_fingerprint/rule_fingerprint) already has an unexpired entry, so this is a no-op
    # (ALREADY_REVIEWED), not a re-append or conflict -- this file's publish() was never touched by
    # the fix and must keep behaving exactly as before.
    req=json.loads(rp.read_text())
    policy=json.loads(pp.read_text());manifest_path=Path(policy['review_manifest'])
    req['expected_manifest_sha256']=hashlib.sha256(manifest_path.read_bytes()).hexdigest();rp.write_text(json.dumps(req))
    out=a.publish(pp,rp)
    assert out['published'] is False and out['state']=='ALREADY_REVIEWED'

def test_publish_current_no_request(tmp_path):
    pp,rp=setup(tmp_path,target_offset=0)
    out=a.publish_current(pp)
    assert out['published'] is False and out['state']=='NO_CURRENT_DAY_REQUEST'
    assert out['financial_authority'] is False and out['activation_authorized'] is False
