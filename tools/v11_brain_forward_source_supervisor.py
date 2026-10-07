#!/usr/bin/env python3
from __future__ import annotations
import json,os,re,runpy,sys,time,traceback
from pathlib import Path

BASE=Path('/home/alphaadmin/AlphaV11_BrainForward')
START_DAY='2026-10-05'

def discover_days():
    return tuple(sorted(
        p.name for p in BASE.iterdir()
        if p.is_dir()
        and re.fullmatch(r'\d{4}-\d{2}-\d{2}', p.name)
        and p.name >= START_DAY
        and (p/'brain_source.py').exists()
        and (p/'source.sqlite').exists()
    ))
STATUS=BASE/'source-supervisor-status.json'
ERRORS=BASE/'source-supervisor-errors.log'

def save(value):
    tmp=STATUS.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,indent=2,sort_keys=True,default=str)+'\n')
    os.chmod(tmp,0o600);os.replace(tmp,STATUS)

def run_script(path,day):
    old=list(sys.argv)
    try:
        sys.argv=[str(path),day]
        runpy.run_path(str(path),run_name='__main__')
        return None
    except SystemExit as exc:
        return str(exc)
    except Exception as exc:
        with ERRORS.open('a') as f:
            f.write(f'\n[{time.time()}] {day} {path.name} {type(exc).__name__}: {exc}\n')
            traceback.print_exc(file=f)
        return type(exc).__name__+':'+str(exc)
    finally:
        sys.argv=old

iteration=0
while True:
    iteration+=1;days=[]
    for day in discover_days():
        root=BASE/day
        capture=root/'capture-status.json';labels=root/'labels-status.json'
        cap_note=label_note=None
        if not capture.exists():
            cap_note=run_script(BASE/'capture_forward.py',day)
        if capture.exists() and not labels.exists():
            label_note=run_script(BASE/'label_forward.py',day)
        state='LABELED' if labels.exists() else 'CAPTURED_WAITING_LABEL' if capture.exists() else 'COLLECTING_MODEL'
        days.append({'date':day,'state':state,'capture_ready':capture.exists(),'labels_ready':labels.exists(),
                     'capture_note':cap_note,'label_note':label_note,'financial_authority':False})
    save({'version':'alpha_v11_brain_forward_source_supervisor_v1','iteration':iteration,'at':time.time(),
          'days':days,'financial_authority':False,'automatic_promotion':False})
    time.sleep(30)
