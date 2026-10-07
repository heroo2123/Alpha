from __future__ import annotations
from pathlib import Path
import json,math,os,re
from polymarket_scanner.v11.evidence import EvidenceStore,digest
from polymarket_scanner.v11.probability import score_vectors

BASE=Path('/home/alphaadmin/AlphaV11_BrainForward')
START_DAY='2026-10-05'
DAYS=tuple(sorted(
    p.name for p in BASE.iterdir()
    if p.is_dir()
    and re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.name)
    and p.name >= START_DAY
    and (p/'capture-status.json').exists()
))
if not DAYS:
    raise SystemExit('WAITING_FORWARD_CAPTURES')
rows=[]
for day in DAYS:
    root=BASE/day
    if not (root/'capture-status.json').exists() or not (root/'labels-status.json').exists():
        raise SystemExit('WAITING_FORWARD_LABELS:'+day)
    store=EvidenceStore(root/'source.sqlite','CHALLENGER:katl-shadow')
    cs=json.loads((root/'capture-status.json').read_text())
    ls=json.loads((root/'labels-status.json').read_text())
    capture=store.get(cs['capture_id'])['body']['details']
    labels={mid:store.get(lid)['body']['payload']['value'] for mid,lid in ls['label_ids'].items()}
    probs=[];truth=[];ids=[]
    for child in capture['rows']:
        d=store.get(child['decision_id'])['body']['details']
        mid=child['target_identity']['market_id']
        probs.append(float(d['explanation']['point']))
        truth.append(int(labels[mid]))
        ids.append(mid)
    if sum(truth)!=1 or not math.isclose(sum(probs),1.0,abs_tol=1e-12):
        raise RuntimeError('FORWARD_VECTOR_INVALID:'+day)
    rows.append({'day':day,'event_id':store.get(cs['capture_id'])['event_id'],
                 'probabilities':probs,'labels':truth,'market_ids':ids,
                 'capture_id':cs['capture_id'],'prediction_sha256':cs['prediction_sha256']})
scores=score_vectors([r['probabilities'] for r in rows],[r['labels'].index(1) for r in rows],
    event_ids=[r['event_id'] for r in rows],city_days=['atlanta:'+r['day'] for r in rows])
out={'version':'alpha_v11_brain_forward_validation_v1',
     'state':'FORWARD_VALIDATION_COMPLETE','parent_bundle_sha256':'fd9a32aa12ac7c8018ec524d1b74514bb57c42c0e60241672a4446b95908e641',
     'days':rows,'scores':scores,'new_challenger_trained':False,
     'reason':'FORWARD_SAMPLE_ONLY_DO_NOT_WEAKEN_PREREGISTERED_200_TRAIN_DAY_POLICY',
     'financial_authority':False,'automatic_promotion':False}
(BASE/'forward-validation-status.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
os.chmod(BASE/'forward-validation-status.json',0o600)
print(json.dumps(out,sort_keys=True))
