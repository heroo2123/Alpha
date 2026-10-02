import datetime, hashlib, json, pathlib, subprocess, time, traceback
root=pathlib.Path('/tmp/alpha-v11-a4-b1-closure-spec-20261002')
base=pathlib.Path('/tmp/alpha-v11-a4-b1-author-20261002-retry')
prompt=pathlib.Path('/tmp/alpha-v11-a4-b1-author-20261002.prompt.txt')
reset_epoch=1790914800
def git(*args): return subprocess.check_output(['git',*args],cwd=root,text=True).strip()
code=97; error=None; started=None
try:
    if not git('rev-parse','HEAD').startswith('1f993fb') or git('status','--porcelain'):
        raise RuntimeError('Refusing changed B1 checkout before retry')
    while time.time()<reset_epoch+10:
        time.sleep(min(30,reset_epoch+10-time.time()))
    if not git('rev-parse','HEAD').startswith('1f993fb') or git('status','--porcelain'):
        raise RuntimeError('Refusing changed B1 checkout at retry')
    started=datetime.datetime.now(datetime.timezone.utc).isoformat()
    cmd=['/home/alphaadmin/.local/bin/claude','--model','sonnet','--effort','high','--permission-mode','acceptEdits','--print','--output-format','stream-json','--verbose']
    with prompt.open('rb') as src, base.with_suffix('.log').open('xb') as out:
        code=subprocess.run(cmd,cwd=root,stdin=src,stdout=out,stderr=subprocess.STDOUT,timeout=7200).returncode
except Exception as exc:
    error=repr(exc)
    base.with_suffix('.runner-error.log').write_text(traceback.format_exc())
finally:
    status=git('status','--porcelain')
    author=pathlib.Path('/tmp/alpha-v11-a4-b1-author-20261002.author.json')
    record={'schema':'ALPHA_V11_A4_B1_RETRY_LANE_V1','completed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'started_utc':started,'head':git('rev-parse','HEAD'),'tree':git('rev-parse','HEAD^{tree}'),'exit_code':code,'error':error,'clean':not bool(status),'uncommitted_status':status,'author_record_exists':author.exists(),'author_record_sha256':hashlib.sha256(author.read_bytes()).hexdigest() if author.exists() else None,'independent_acceptance':False,'a4_pass':False,'launchable':False,'qualification':'UNQUALIFIED','financial_authority':False}
    base.with_suffix('.terminal.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
