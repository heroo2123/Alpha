"""New full-branch acceptance regressions: expected to FAIL on held 0b7209d.
Failures reproduce defects, never acceptance. Synthetic paths and owned children only.
"""
import importlib.util
import json
import os
from pathlib import Path
import select
import signal

import pytest
from tools import v11_r09_gate3_launch as launch
from tools import v11_r09_gate3_offline_io as io

R=Path(io.__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('launch_fixture',R/'tests/test_v11_r09_gate3_launch.py')
author=importlib.util.module_from_spec(spec);spec.loader.exec_module(author)


def child_result(action, timeout=2):
    read,write=os.pipe();pid=os.fork()
    if pid==0:
        os.close(read)
        try:
            os.write(write,b'READY\n')
            try: result=action()
            except BaseException as exc: result={'error_type':type(exc).__name__,'error':str(exc)}
            os.write(write,json.dumps(result).encode()+b'\n')
        finally: os._exit(0)
    os.close(write)
    try:
        assert select.select([read],[],[],2)[0], 'owned child did not start'
        data=os.read(read,65536)
        if b'\n' not in data: raise AssertionError('missing startup marker')
        rest=data.split(b'\n',1)[1]
        if not rest:
            if not select.select([read],[],[],timeout)[0]: return {'blocked':True,'seconds':timeout}
            rest=os.read(read,65536)
        return json.loads(rest)
    finally:
        # Always kill/reap only the process created above; no leaked blocked child.
        try: os.kill(pid,signal.SIGKILL)
        except ProcessLookupError: pass
        os.waitpid(pid,0);os.close(read)


def test_launch_manifest_fifo_artifact_rejects_without_blocking(tmp_path,monkeypatch):
    payload,repo,root,start=author.candidate(tmp_path,monkeypatch)
    assert len(author.validate(payload,repo,root,start))==64
    ref=payload['identity']['created_ref']
    path=root/'objects'/ref['sha256']
    path.unlink();os.mkfifo(path,0o600)
    result=child_result(lambda:{'digest':author.validate(payload,repo,root,start)})
    assert result.get('error_type')=='LaunchContractError', json.dumps(result,sort_keys=True)
    assert path.is_fifo()


def test_forked_budget_cannot_write_while_parent_owns_composed_store(tmp_path):
    journal=tmp_path/'budget';journal.mkdir(mode=0o700)
    root=tmp_path/'store';root.mkdir(mode=0o700);(root/'objects').mkdir(mode=0o700)
    with launch.DurableBudget(journal,'a'*64,max_bytes=1,boot_id='synthetic-boot') as budget:
        with io.VersionedImmutableObjectStore(root,manifest_sha256='a'*64,policy_sha256='b'*64,
                build_id='synthetic',clock_method='synthetic',max_clock_age_seconds=10,
                host_id='synthetic-host',boot_id='synthetic-boot') as store:
            before=(journal/'gate3.jsonl').read_bytes()
            def inherited():
                try: store.read_receipt(None)
                except launch.LaunchContractError as exc: store_error=str(exc)
                budget.reserve('child',1,started_monotonic=1)
                budget.consume('child',b'X');budget.complete('child')
                return {'child_received':budget.received,'store_error':store_error}
            result=child_result(inherited)
            # Exercise stale parent state only when child incorrectly succeeded.
            if 'child_received' in result:
                budget.reserve('parent',1,started_monotonic=3)
                budget.consume('parent',b'Y');budget.complete('parent')
                result['parent_received']=budget.received
                result['actual_delivered_bytes']=2
                result['max_bytes']=budget.max_bytes
                result['journal_changed']=(journal/'gate3.jsonl').read_bytes()!=before
        after=(journal/'gate3.jsonl').read_bytes()
    if 'child_received' in result:
        try:
            with launch.DurableBudget(journal,'a'*64,max_bytes=1,boot_id='synthetic-boot'): pass
        except launch.LaunchContractError as exc: result['reopen_error']=str(exc)
    assert 'child_received' not in result and after==before, json.dumps(result,sort_keys=True)
