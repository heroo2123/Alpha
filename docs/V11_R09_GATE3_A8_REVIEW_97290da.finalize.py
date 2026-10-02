from pathlib import Path
import json,hashlib,subprocess
b=Path('/tmp/alpha-v11-gate3-a8-review-97290da-final')
r=Path('/tmp/alpha-v11-gate3-a8-review-97290da')
m=json.loads(Path(str(b)+'.terminal.json').read_text())
def sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()
assert subprocess.check_output(['git','rev-parse','HEAD'],cwd=r,text=True).strip()==m['head']
assert subprocess.check_output(['git','rev-parse','HEAD^{tree}'],cwd=r,text=True).strip()==m['tree']
assert not subprocess.check_output(['git','status','--porcelain'],cwd=r,text=True).strip()
assert sha(m['report']['path'])==m['report']['sha256']
assert sha(m['probe']['path'])==m['probe']['sha256']
for p,h in m['logs'].items():
 assert sha(p)==h
 assert 'REVIEW_EXIT_CODE=0' in Path(p).read_text()
for p,h in m['candidate_file_sha256'].items(): assert sha(r/p)==h
assert m['verdict']=='CHANGES_REQUIRED' and m['authority'] is False
print('FINALIZATION_PASS: exact candidate, report, machine verdict, probes and successful test logs verified; review rejects candidate.')
