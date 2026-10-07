#!/usr/bin/env python3
from __future__ import annotations
import json,os,re,subprocess,time,traceback
from pathlib import Path

BASE=Path('/home/alphaadmin/AlphaV11_BrainForward')
PY='/home/alphaadmin/AlphaV11_Dev/venv/bin/python'
ENV={'PATH':'/usr/bin:/bin','HOME':'/home/alphaadmin','PYTHONPATH':'/home/alphaadmin/AlphaV11_ShadowRuntime',
     'LC_ALL':'C.UTF-8'}
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

def resolved_days(base,days):
    return tuple(d for d in days if (base/d/'labels-status.json').exists())

def validation_current(base,days):
    path=base/'forward-validation-status.json'
    if not path.exists(): return False
    try:
        prior=json.loads(path.read_text())
        prior_days=tuple(sorted(str(row.get('day')) for row in prior.get('days',[]) if row.get('day')))
        return prior_days==tuple(sorted(days))
    except Exception:
        return False

def save(path,v):
    p=path.with_suffix('.tmp');p.write_text(json.dumps(v,indent=2,sort_keys=True,default=str)+'\n')
    os.chmod(p,0o600);os.replace(p,path)

def last_json(text):
    for line in reversed(text.splitlines()):
        try:return json.loads(line)
        except Exception:pass
    return {'raw':text[-2000:]}

def default_score_runner(base,timeout=60):
    out=subprocess.run([PY,str(base/'score_forward.py')],cwd=base,env=ENV,stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT,text=True,timeout=timeout,check=False).stdout.strip()
    return last_json(out)

def iterate(base=BASE,score_runner=default_score_runner):
    status=base/'brain-supervisor-status.json'
    try:
        days=captured_days(base)
        day_states={}
        for day in days:
            root=base/day
            cap=root/'capture-status.json';lab=root/'labels-status.json'
            if not cap.exists():
                day_states[day]={'state':'WAITING_CAPTURE'}
                continue
            if lab.exists():
                day_states[day]={'state':'LABELED',**json.loads(lab.read_text())}
                continue
            day_states[day]={'state':'CAPTURED_WAITING_LABEL','date':day,'financial_authority':False}
        resolved=resolved_days(base,days)
        pending=tuple(d for d in days if d not in resolved)
        if resolved:
            if not validation_current(base,resolved):
                fit=score_runner(base)
            else:
                fit=json.loads((base/'forward-validation-status.json').read_text())
            if (base/'forward-validation-status.json').exists():
                state='HAS_SCORED_RESOLVED_EVIDENCE_WITH_PENDING_DAYS' if pending else 'FORWARD_VALIDATION_COMPLETE'
            else:
                state='FORWARD_VALIDATION_ATTEMPTED'
        else:
            fit=None;state='WAITING_RESOLVED_EVIDENCE'
        value={'version':'alpha_v11_brain_forward_supervisor_v1','state':state,'at':time.time(),
              'days':day_states,'fit':fit,'parent_bundle_sha256':PARENT_BUNDLE_SHA256,
              'automatic_promotion':False,'financial_authority':False}
        save(status,value)
        return value
    except Exception as exc:
        with (base/'brain-supervisor-errors.log').open('a') as f:
            f.write(f'\n[{time.time()}] {type(exc).__name__}: {exc}\n');traceback.print_exc(file=f)
        value={'version':'alpha_v11_brain_forward_supervisor_v1','state':'GATED','at':time.time(),
              'error_type':type(exc).__name__,'error':str(exc),'automatic_promotion':False,'financial_authority':False}
        save(status,value)
        return value

if __name__=='__main__':
    while True:
        iterate(BASE)
        time.sleep(300)
