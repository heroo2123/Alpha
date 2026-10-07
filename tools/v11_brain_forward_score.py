from __future__ import annotations
from pathlib import Path
import json,math,os,re
from polymarket_scanner.v11.evidence import EvidenceStore,digest
from polymarket_scanner.v11.probability import score_vectors

BASE=Path('/home/alphaadmin/AlphaV11_BrainForward')
START_DAY='2026-10-05'
PARENT_BUNDLE_SHA256='fd9a32aa12ac7c8018ec524d1b74514bb57c42c0e60241672a4446b95908e641'

def captured_days(base):
    return tuple(sorted(
        p.name for p in base.iterdir()
        if p.is_dir()
        and re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.name)
        and p.name >= START_DAY
        and (p/'capture-status.json').exists()
    ))

def run(base=BASE):
    days=captured_days(base)
    if not days:
        raise SystemExit('WAITING_FORWARD_CAPTURES')
    resolved=tuple(d for d in days if (base/d/'labels-status.json').exists())
    pending=tuple(d for d in days if d not in resolved)
    if not resolved:
        raise SystemExit('WAITING_FORWARD_LABELS')
    rows=[]
    for day in resolved:
        root=base/day
        store=EvidenceStore(root/'source.sqlite','CHALLENGER:katl-shadow')
        cs=json.loads((root/'capture-status.json').read_text())
        ls=json.loads((root/'labels-status.json').read_text())
        label_ids=ls['label_ids']
        if len(set(label_ids.values()))!=len(label_ids):
            raise RuntimeError('DUPLICATE_LABEL_RECORD_ID:'+day)
        capture_record=store.get(cs['capture_id'])
        capture=capture_record['body']['details']
        row_identity={child['target_identity']['market_id']:child['target_identity']
                      for child in capture['rows']}
        labels={}
        for mid,lid in label_ids.items():
            rec=store.get(lid)
            if rec['kind']!='LABEL':
                raise RuntimeError('LABEL_MARKET_IDENTITY_MISMATCH:'+day)
            payload=rec['body']['payload']
            # Real labels (label_forward.py) carry no top-level market_id; identity
            # lives in payload['target_identity']. A record filed under the wrong
            # market key (e.g. a swapped winner/loser) must be caught here, not
            # silently accepted because an absent top-level field compared equal
            # to None.
            target_identity=payload.get('target_identity')
            if (not isinstance(target_identity,dict) or target_identity.get('market_id')!=mid
                    or (mid in row_identity and target_identity!=row_identity[mid])):
                raise RuntimeError('LABEL_MARKET_IDENTITY_MISMATCH:'+day)
            labels[mid]=payload['value']
        probs=[];truth=[];ids=[]
        for child in capture['rows']:
            # Real DECISION records (EvidenceStore.decision) keep 'explanation' at
            # the top of the body; there is no 'details' wrapper around it.
            d=store.get(child['decision_id'])['body']
            mid=child['target_identity']['market_id']
            if mid not in labels:
                raise RuntimeError('LABEL_MISSING_FOR_MARKET:'+day)
            probs.append(float(d['explanation']['point']))
            truth.append(int(labels[mid]))
            ids.append(mid)
        if sum(truth)!=1 or not math.isclose(sum(probs),1.0,abs_tol=1e-12):
            raise RuntimeError('FORWARD_VECTOR_INVALID:'+day)
        rows.append({'day':day,'event_id':capture_record['event_id'],
                     'probabilities':probs,'labels':truth,'market_ids':ids,
                     'capture_id':cs['capture_id'],'prediction_sha256':cs['prediction_sha256']})
    scores=score_vectors([r['probabilities'] for r in rows],[r['labels'].index(1) for r in rows],
        event_ids=[r['event_id'] for r in rows],city_days=['atlanta:'+r['day'] for r in rows])
    out={'version':'alpha_v11_brain_forward_validation_v1',
         'state':('FORWARD_VALIDATION_COMPLETE' if not pending
                   else 'HAS_SCORED_RESOLVED_EVIDENCE_WITH_PENDING_DAYS'),
         'parent_bundle_sha256':PARENT_BUNDLE_SHA256,
         'days':rows,'pending_days':sorted(pending),'scores':scores,'new_challenger_trained':False,
         'reason':'FORWARD_SAMPLE_ONLY_DO_NOT_WEAKEN_PREREGISTERED_200_TRAIN_DAY_POLICY',
         'financial_authority':False,'automatic_promotion':False}
    (base/'forward-validation-status.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
    os.chmod(base/'forward-validation-status.json',0o600)
    print(json.dumps(out,sort_keys=True))
    return out

if __name__=='__main__':
    run(BASE)
