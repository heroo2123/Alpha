#!/usr/bin/env python3
from __future__ import annotations
import json,os,re,subprocess,time,traceback
from pathlib import Path

BASE=Path('/home/alphaadmin/AlphaV11_BrainForward')
STATUS=BASE/'brain-supervisor-status.json'
ERRORS=BASE/'brain-supervisor-errors.log'
PY='/home/alphaadmin/AlphaV11_Dev/venv/bin/python'
ENV={'PATH':'/usr/bin:/bin','HOME':'/home/alphaadmin','PYTHONPATH':'/home/alphaadmin/AlphaV11_ShadowRuntime',
     'LC_ALL':'C.UTF-8'}
START_DAY='2026-10-05'

def captured_days():
    return tuple(sorted(
        p.name for p in BASE.iterdir()
        if p.is_dir()
        and re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.name)
        and p.name >= START_DAY
        and (p/'capture-status.json').exists()
    ))

def validation_current(days):
    path=BASE/'forward-validation-status.json'
    if not path.exists(): return False
    try:
        prior=json.loads(path.read_text())
        prior_days=tuple(sorted(str(row.get('day')) for row in prior.get('days',[]) if row.get('day')))
        return prior_days==tuple(days)
    except Exception:
        return False

def save(v):
    p=STATUS.with_suffix('.tmp');p.write_text(json.dumps(v,indent=2,sort_keys=True,default=str)+'\n')
    os.chmod(p,0o600);os.replace(p,STATUS)

def run(args,timeout=60):
    return subprocess.run(args,cwd=BASE,env=ENV,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          text=True,timeout=timeout,check=False).stdout.strip()

def last_json(text):
    for line in reversed(text.splitlines()):
        try:return json.loads(line)
        except Exception:pass
    return {'raw':text[-2000:]}

while True:
    try:
        days=captured_days()
        day_states={}
        for day in days:
            root=BASE/day
            cap=root/'capture-status.json';lab=root/'labels-status.json'
            if not cap.exists():
                day_states[day]={'state':'WAITING_CAPTURE'}
                continue
            if lab.exists():
                day_states[day]={'state':'LABELED',**json.loads(lab.read_text())}
                continue
            day_states[day]={'state':'CAPTURED_WAITING_LABEL','date':day,'financial_authority':False}
        if days and all((BASE/d/'labels-status.json').exists() for d in days):
            if not validation_current(days):
                out=run([PY,str(BASE/'score_forward.py')],timeout=60)
                fit=last_json(out)
            else:
                fit=json.loads((BASE/'forward-validation-status.json').read_text())
            state='FORWARD_VALIDATION_COMPLETE' if (BASE/'forward-validation-status.json').exists() else 'FORWARD_VALIDATION_ATTEMPTED'
        else:
            fit=None;state='WAITING_RESOLVED_EVIDENCE'
        save({'version':'alpha_v11_brain_forward_supervisor_v1','state':state,'at':time.time(),
              'days':day_states,'fit':fit,'parent_bundle_sha256':'fd9a32aa12ac7c8018ec524d1b74514bb57c42c0e60241672a4446b95908e641',
              'automatic_promotion':False,'financial_authority':False})
    except Exception as exc:
        with ERRORS.open('a') as f:
            f.write(f'\n[{time.time()}] {type(exc).__name__}: {exc}\n');traceback.print_exc(file=f)
        save({'version':'alpha_v11_brain_forward_supervisor_v1','state':'GATED','at':time.time(),
              'error_type':type(exc).__name__,'error':str(exc),'automatic_promotion':False,'financial_authority':False})
    time.sleep(300)
