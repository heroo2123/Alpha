import datetime
import hashlib
import json
import os
import pathlib
import subprocess

base = pathlib.Path('/tmp/alpha-v11-a2a3-decision-review-fb3a804')
expected = 'fb3a8047760bcdaf0a94be20afcea7d61a7c2785'
expected_tree = 'a09d184c91011e0fa8ee0d171f8b9edd4dc7ea68'
def git(*args):
    return subprocess.check_output(['git', *args], cwd=base, text=True).strip()
initial = git('rev-parse','HEAD')
initial_tree = git('rev-parse','HEAD^{tree}')
code = 97
error = None
try:
    if initial != expected or initial_tree != expected_tree or git('status','--porcelain'):
        raise RuntimeError('Initial checkout identity or cleanliness mismatch')
    cmd = ['/home/alphaadmin/.local/node22/bin/codex', '--no-daemon', '--strict-config', '--approve-for-me', '-m', 'gpt-6-sol', '-c', 'model_reasoning_effort="high"', 'exec', '--ignore-user-config', '--ephemeral', '-C', str(base), '--color', 'never', '-o', str(base.with_suffix('.last.txt')), '-']
    with base.with_suffix('.prompt.txt').open('rb') as src, base.with_suffix('.log').open('xb') as out:
        code = subprocess.run(cmd, cwd=base, stdin=src, stdout=out, stderr=subprocess.STDOUT, timeout=1800).returncode
except Exception as exc:
    error = repr(exc)
finally:
    dirty = git('status','--porcelain')
    verdict = base.with_suffix('.verdict.json')
    record = {'schema':'ALPHA_V11_PARALLEL_GATE3_LANE_V1','lane':'a2a3-decision-independent-review','pid':os.getpid(),'completed_utc':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exit_code':code,'error':error,'initial_head':initial,'initial_tree':initial_tree,'head':git('rev-parse','HEAD'),'tree':git('rev-parse','HEAD^{tree}'),'clean':not bool(dirty),'uncommitted_status':dirty,'verdict_sha256':hashlib.sha256(verdict.read_bytes()).hexdigest() if verdict.exists() else None,'financial_authority':False,'live_execution_authority':False}
    base.with_suffix('.terminal.json').write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
