"""Independent exact-fe854fc offline reproductions. Writes only stdout."""
import copy
import json
import os
import runpy
import sys
import subprocess
from pathlib import Path
from unittest.mock import patch
ROOT = Path(os.environ.get('ALPHA_REVIEW_ROOT', '/tmp/alpha-v11-gate3-preflight-checker-repair-20261002'))
assert subprocess.check_output(['git', '-C', str(ROOT), 'rev-parse', 'HEAD'], text=True).strip() == 'fe854fc5486d4ea24eda442308034b150feeaeda', 'review reproduction requires exact fe854fc'
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
with patch('socket.socket', side_effect=AssertionError('network forbidden')), patch('socket.getaddrinfo', side_effect=AssertionError('DNS forbidden')):
    t = runpy.run_path(str(ROOT/'tests/test_v11_gate3_evidence_preflight_checker.py'))
    fixture, run = t['_synthetic_fixture'], t['_run']
    rows = []
    def record(label, fn):
        try:
            result = fn()
            rows.append({'case':label, 'outcome':result.outcome, 'reasons':list(result.refusal_reasons)})
        except Exception as exc:
            rows.append({'case':label, 'exception':type(exc).__name__, 'message':str(exc)})
    def changed(label, package=None, restrictions=None, binding=None, **kwargs):
        p,r,proto,b = fixture()
        p,r=json.loads(p),json.loads(r)
        if restrictions: restrictions(r)
        r=json.dumps(r).encode()
        p['restrictions_ref'].update(sha256=t['_sha'](r),byte_length=len(r))
        if package: package(p)
        p=json.dumps(p).encode()
        b=json.loads(t['_synthetic_binding_raw'](p,r,proto))
        if binding: binding(b)
        record(label,lambda:run(p,r,proto,json.dumps(b).encode(),**kwargs))
    record('synthetic baseline',lambda:run(*fixture()))
    for index,name in [(0,'package'),(1,'restrictions'),(3,'binding')]:
        for raw in [b'null',b'[]',b'1']:
            f=list(fixture());f[index]=raw
            record(name+' top-level '+raw.decode(),lambda f=f:run(*f))
    for key in ['directories','input_refs']:
        changed('prior repair '+key+'=1',package=lambda p,key=key:p.__setitem__(key,1))
    changed('prior repair private_package float length',binding=lambda b:b['private_package'].__setitem__('byte_length',float(b['private_package']['byte_length'])))
    changed('prior repair private_package missing path',binding=lambda b:b['private_package'].pop('path'))
    changed('prior repair missing storage diagnostic',package=lambda p:p['storage_qualification'].__setitem__('physically_reserved_bytes',0),resources=t['ResourceObservation'](float('nan'),0,0))
    for key in ['complete_lineage_review','shared_history_head','unresolved_attempt_reconciliation']:
        changed('restriction '+key+'=false',restrictions=lambda r,key=key:r.__setitem__(key,False))
    changed('GEFS scope review=false',restrictions=lambda r:r['known_control_domains']['GEFS'].__setitem__('scope_independence_review',False))
    for status in ['HELD','DENIED','UNKNOWN','']:
        changed('GEFS status '+repr(status),package=lambda p,status=status:p['requests'][0].__setitem__('restriction_status',status),restrictions=lambda r,status=status:r['known_control_domains']['GEFS'].__setitem__('status',status))
    changed('persistence_review=false',package=lambda p:p['storage_qualification'].__setitem__('persistence_review',False))
    changed('request port float',package=lambda p:p['requests'][0].__setitem__('port',443.0))
    changed('author_model overlong',package=lambda p:p.__setitem__('author_model','x'*4097))
    changed('blocking_reasons not list',package=lambda p:p.__setitem__('blocking_reasons',False))
    changed('unknown_outputs array65',package=lambda p:p.__setitem__('unknown_outputs_not_required_as_inputs',['x']*65))
    changed('binding source_inputs malformed ref',binding=lambda b:b.__setitem__('source_inputs',[{'sha256':False}]))
    changed('binding owner_instruction_record=false',binding=lambda b:b.__setitem__('owner_instruction_record',False))
    term=copy.deepcopy(t['GOOD_REVIEW_TERMINAL']);term['exit_code']=False;term.pop('error')
    changed('terminal exit_code=false and missing error',review_terminal=term)
    changed('clock naive time',clock=t['ClockObservation']('2026-10-02T10:05:00',0.3,10.0,True))
    changed('clock monotonic string false',clock=t['ClockObservation']('2026-10-02T10:05:00Z',0.3,10.0,'false'))
    p,r,proto,_=fixture();p=p.replace(b'"author_model": "synthetic-test-fixture"', b'"author_model": 1e999');b=t['_synthetic_binding_raw'](p,r,proto)
    record('JSON exponent overflow 1e999',lambda:run(p,r,proto,b))
    root=Path(t['REAL_PRIVATE_ROOT']);p=(root/'package.json').read_bytes();r=(root/'restriction-history.json').read_bytes();proto=Path(t['REAL_PROTOCOL_PATH']).read_bytes();b=Path(t['REAL_BINDING_PATH']).read_bytes()
    record('real retained package with synthetic in-window observations',lambda:run(p,r,proto,b))
    print(json.dumps({'candidate':'fe854fc5486d4ea24eda442308034b150feeaeda','socket_and_dns':'patched to raise','cases':rows},indent=2))
