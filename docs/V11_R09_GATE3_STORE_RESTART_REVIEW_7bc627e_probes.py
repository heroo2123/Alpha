"""Independent Astra review of exact 7bc627e. Disposable synthetic roots only.
Two defect probes assert reproduced faults; their PASS does not mean acceptance.
Run with PYTHONPATH=<candidate> python -m pytest -q <this file>.
"""
import importlib.util
from pathlib import Path
import hashlib
import json
import os
import select
import signal
import subprocess
import sys
import threading
from dataclasses import replace

import pytest
from tools import v11_r09_gate3_store_v1 as v1
from tools.v11_r09_gate3_launch import LaunchContractError

# Retain the prior independent fault controls, excluding its now-fixed defect oracles.
_previous = Path(__file__).with_name('V11_R09_GATE3_STORE_RESTART_REVIEW_74bd122_probes.py')
_spec = importlib.util.spec_from_file_location('previous_restart_review', _previous)
p = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(p)
for _name in dir(p):
    if _name.startswith('test_') and not _name.startswith('test_defect_'):
        globals()[_name] = getattr(p, _name)


@pytest.mark.parametrize('which', ['descriptor', 'head', 'both'])
def test_repaired_r1_same_root_complete_loss(tmp_path, which):
    root = p.root_at(tmp_path)
    with p.open_store(root) as s:
        pin, head = s.descriptor_sha256, (s._seq, s._head)
    (root/'store-v1.json').unlink()
    (root/'seals-v1.jsonl').unlink()
    args = {}
    if which in ('descriptor', 'both'): args['expected_descriptor_sha256'] = pin
    if which in ('head', 'both'): args['expected_head'] = head
    with p.open_store(root, **args) as held:
        assert held.report.classification == 'JOURNAL_OR_IDENTITY_INVALID'
    assert sorted(x.name for x in root.iterdir()) == ['objects']
    with p.open_store(root) as fresh:
        assert fresh.report.classification == 'VALID'
        assert fresh.descriptor_sha256 != pin


def test_repaired_r2_close_waits_and_callback_refuses(tmp_path):
    root = p.root_at(tmp_path)
    s = p.open_store(root)
    pin = s.descriptor_sha256
    entered, release, closing, closed = (threading.Event() for _ in range(4))
    results, errors = [], []
    def recorder():
        with pytest.raises(LaunchContractError, match='STORE_CALLBACK_REENTRANCY'):
            s.close()
        entered.set()
        assert release.wait(5)
        return p.sample('durable_seal', 103, 4)
    def writer():
        try: results.append(p.seal(s, recorder=recorder))
        except BaseException as exc: errors.append(exc)
    def closer():
        closing.set()
        try: s.close(); closed.set()
        except BaseException as exc: errors.append(exc)
    writer_thread, closer_thread = threading.Thread(target=writer), threading.Thread(target=closer)
    try:
        writer_thread.start(); assert entered.wait(5)
        closer_thread.start(); assert closing.wait(5)
        assert not closed.wait(.1)
        with pytest.raises(BlockingIOError): p.open_store(root)
    finally:
        release.set(); writer_thread.join(5)
        if closer_thread.ident: closer_thread.join(5)
        s.close()
    assert not errors and len(results) == 1 and closed.is_set()
    assert not writer_thread.is_alive() and not closer_thread.is_alive()
    with p.open_store(root, expected_descriptor_sha256=pin) as recovered:
        assert recovered.report.classification == 'VALID'
        assert recovered.receipts[results[0].object_sha256].acknowledgement == 'UNKNOWN'


@pytest.mark.parametrize('threaded', [False, True])
def test_repaired_r3_frozen_prefix(tmp_path, threaded):
    root = p.root_at(tmp_path)
    clocks = list(p.prefix()); original = tuple(clocks)
    def mutate(): clocks[:] = [p.sample(s.phase, s.reading.utc_seconds-1,
                                       s.reading.monotonic_seconds-1) for s in original]
    def recorder():
        if threaded:
            t = threading.Thread(target=mutate); t.start(); t.join(5)
            assert not t.is_alive()
        else: mutate()
        return p.sample('durable_seal', 103, 4)
    with p.open_store(root) as s:
        pin = s.descriptor_sha256
        receipt = p.seal(s, clocks=clocks, recorder=recorder)
        assert receipt.clocks[:3] == original
    with p.open_store(root, expected_descriptor_sha256=pin) as s:
        assert s.report.classification == 'VALID'
        assert s.receipts[receipt.object_sha256].clocks == receipt.clocks


@pytest.mark.parametrize('extra', [-1, 0, 1])
@pytest.mark.parametrize('limit', ['bytes', 'events'])
def test_repaired_r4_capacity_boundary(tmp_path, monkeypatch, extra, limit):
    root = p.root_at(tmp_path)
    with p.open_store(root) as s:
        pin = s.descriptor_sha256
        before = (root/'seals-v1.jsonl').read_bytes()
        with monkeypatch.context() as m:
            m.setattr(v1, 'MAX_JOURNAL' if limit == 'bytes' else 'MAX_EVENTS',
                      len(before)+3*v1.MAX_RECORD+v1.REPORT_RESERVE+extra
                      if limit == 'bytes' else s._seq+3+extra)
            if extra < 0:
                with pytest.raises(LaunchContractError, match='STORE_JOURNAL_CAPACITY'):
                    p.seal(s)
                assert (root/'seals-v1.jsonl').read_bytes() == before
                assert not s._failed and not list((root/'objects').iterdir())
            else:
                receipt = p.seal(s)
                assert s.read_receipt(receipt) == b'independent bytes'
        if extra < 0: p.seal(s)
    with p.open_store(root, expected_descriptor_sha256=pin) as s:
        assert s.report.classification == 'VALID'


@pytest.mark.parametrize('has_receipt', [False, True])
def test_repaired_r5_host_boot_bound(tmp_path, has_receipt):
    root = p.root_at(tmp_path)
    with p.open_store(root) as s:
        pin = s.descriptor_sha256
        receipt = p.seal(s) if has_receipt else None
    before = (root/'seals-v1.jsonl').read_bytes()
    descriptor = json.loads((root/'store-v1.json').read_bytes())
    assert descriptor['original_host_id'] == 'host-original'
    assert descriptor['original_boot_id'] == 'boot-original'
    with p.open_store(root, expected_descriptor_sha256=pin, host_id='another-host') as s:
        assert s.report.classification == 'JOURNAL_OR_IDENTITY_INVALID'
    assert (root/'seals-v1.jsonl').read_bytes() == before
    with p.open_store(root, expected_descriptor_sha256=pin, boot_id='another-boot') as s:
        assert s.report.classification == 'VALID'
        with pytest.raises(LaunchContractError, match='STORE_CROSS_BOOT_ACQUISITION_HELD'):
            p.seal(s)
        if receipt:
            assert s.read_receipt(s.receipts[receipt.object_sha256]) == b'independent bytes'
            assert s.receipts[receipt.object_sha256].clocks == receipt.clocks


@pytest.mark.parametrize('operation', ['read', 'seal'])
def test_defect_fork_with_other_thread_owning_mutex_hangs(tmp_path, operation):
    root = p.root_at(tmp_path); s = p.open_store(root)
    entered, release = threading.Event(), threading.Event()
    errors = []
    def recorder():
        entered.set(); assert release.wait(5)
        return p.sample('durable_seal', 103, 4)
    def writer():
        try: p.seal(s, recorder=recorder)
        except BaseException as exc: errors.append(exc)
    t = threading.Thread(target=writer); t.start(); assert entered.wait(5)
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        # At-fork cleanup has run and closed the copied descriptors.
        os.write(w, b'C' if s.root_fd is None and s._pid == -1 else b'X')
        try:
            s.read_receipt(None) if operation == 'read' else p.seal(s)
        except LaunchContractError:
            os.write(w, b'R')
        os._exit(0)
    os.close(w)
    try:
        assert select.select([r], [], [], 2)[0]
        assert os.read(r, 1) == b'C'
        # Defect oracle: inherited read/seal never reaches its owner rejection.
        assert not select.select([r], [], [], .3)[0]
        assert os.waitpid(pid, os.WNOHANG) == (0, 0)
        with pytest.raises(BlockingIOError): p.open_store(root)
    finally:
        os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0); os.close(r)
        release.set(); t.join(5); s.close()
    assert not t.is_alive() and not errors


def test_defect_accepted_descriptor_exceeds_own_replay_bound(tmp_path):
    root = p.root_at(tmp_path)
    label = '\U0001f680'*128
    kw = dict(build_id=label, clock_method=label, host_id=label)
    with p.open_store(root, **kw) as s:
        pin = s.descriptor_sha256
        receipt = p.seal(s, clocks=tuple(replace(c, method=label) for c in p.prefix()),
                         recorder=lambda: replace(p.sample('durable_seal', 103, 4), method=label))
        assert s.read_receipt(receipt) == b'independent bytes'
        assert receipt.acknowledgement == 'ACKNOWLEDGED_THIS_SESSION'
    assert (root/'store-v1.json').stat().st_size > 4096
    before = (root/'seals-v1.jsonl').read_bytes()
    with p.open_store(root, expected_descriptor_sha256=pin, **kw) as s:
        assert s.report.classification == 'JOURNAL_OR_IDENTITY_INVALID'
        assert s.report.reasons == ('OBJECT_FILE_IDENTITY',)
        with pytest.raises(LaunchContractError): s.read_receipt(receipt)
    assert (root/'seals-v1.jsonl').read_bytes() == before


# A real exec of the owning process, without close(), must release all store FDs.
def test_exec_owner_releases_lock_without_explicit_close(tmp_path):
    root = p.root_at(tmp_path)
    with p.open_store(root) as s: pin = s.descriptor_sha256
    program = '''import os,sys
from pathlib import Path
from tools.v11_r09_gate3_store_v1 import VersionedImmutableObjectStore as S
s=S(Path(sys.argv[1]),manifest_sha256='a'*64,policy_sha256='b'*64,build_id='independent-fixture',clock_method='synthetic',max_clock_age_seconds=10,host_id='host-original',boot_id='boot-original',expected_descriptor_sha256=sys.argv[2])
assert s.report.classification=='VALID'
os.execv(sys.executable,[sys.executable,'-c',"import sys;print('EXECUTED',flush=True);sys.stdin.read()"])
'''
    child = subprocess.Popen([sys.executable, '-c', program, str(root), pin],
                             stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert select.select([child.stdout], [], [], 5)[0]
        assert child.stdout.readline() == b'EXECUTED\n'
        assert child.poll() is None
        with p.open_store(root, expected_descriptor_sha256=pin) as s:
            assert s.report.classification == 'VALID'
    finally:
        child.communicate(b'', timeout=5)
    assert child.returncode == 0


KILL_OPERATIONS = {
    'initialize': [('open', 1), ('open', 2), ('write', 1), ('write', 2),
                   ('fsync', 1), ('fsync', 2), ('fsync', 3), ('fsync', 4)],
    'seal': [('write', 1), ('write', 2), ('write', 3), ('fsync', 1), ('fsync', 2),
             ('fsync', 3), ('fsync', 4), ('fsync', 5), ('link', 1), ('unlink', 1)],
    'recover': [('write', 1), ('fsync', 1), ('fsync', 2), ('fsync', 3),
                ('fsync', 4), ('fsync', 5)],
}


@pytest.mark.parametrize('mode,operation,number,after', [
    (mode, op, number, after) for mode, ops in KILL_OPERATIONS.items()
    for op, number in ops for after in (False, True)])
def test_sigkill_syscall_boundary(tmp_path, mode, operation, number, after):
    root = p.root_at(tmp_path); pin = None
    if mode != 'initialize':
        with p.open_store(root) as s:
            pin = s.descriptor_sha256
            if mode == 'recover': original = p.seal(s)
    pid = os.fork()
    if pid == 0:
        if mode == 'seal': s = p.open_store(root, expected_descriptor_sha256=pin)
        real = getattr(os, operation); calls = 0
        def crash(*args, **kw):
            nonlocal calls
            # Count only metadata creation for init open; independent of path checks.
            if operation == 'open' and mode == 'initialize':
                matching = args[1] & os.O_CREAT
            else: matching = True
            if matching: calls += 1
            target = number
            if matching and calls == target and not after: os.kill(os.getpid(), signal.SIGKILL)
            result = real(*args, **kw)
            if matching and calls == target: os.kill(os.getpid(), signal.SIGKILL)
            return result
        setattr(os, operation, crash)
        if mode == 'seal': p.seal(s)
        else: p.open_store(root, expected_descriptor_sha256=pin)
        os._exit(99)
    # Bound cleanup even if a probe fails to reach its injected boundary.
    import time
    deadline = time.monotonic()+5
    status = None
    while time.monotonic() < deadline:
        got, observed = os.waitpid(pid, os.WNOHANG)
        if got: status = observed; break
        time.sleep(.01)
    if status is None:
        os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
        pytest.fail('probe child did not reach SIGKILL boundary')
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    descriptor = root/'store-v1.json'; journal = root/'seals-v1.jsonl'
    if pin is None and descriptor.exists(): pin = hashlib.sha256(descriptor.read_bytes()).hexdigest()
    original_names = sorted(x.name for x in (root/'objects').iterdir())
    before = journal.read_bytes() if journal.exists() else b''
    events = [json.loads(line)['event'] for line in before.splitlines()]
    fresh = not descriptor.exists()
    valid = fresh or bool(events) and events[-1] != 'PREPARE'
    with p.open_store(root, expected_descriptor_sha256=pin) as s:
        assert (s.report.classification == 'VALID') == valid
        if not valid:
            with pytest.raises(LaunchContractError): s.read_receipt(None)
        for receipt in s.receipts.values():
            assert receipt.acknowledgement == 'UNKNOWN'
            assert not receipt.historical_feature_eligible
            assert receipt.clocks == (original.clocks if mode == 'recover' else
                                      (*p.prefix(), p.sample('durable_seal', 103, 4)))
    assert sorted(x.name for x in (root/'objects').iterdir()) == original_names
    if journal.exists(): assert journal.read_bytes().startswith(before)


# Abstract persistence model, independent of the implementation's syscall wrappers.
# File fsync fixes bytes; directory fsync fixes names. Before their barriers,
# bytes may survive as empty/prefix/full and unsynced names may survive or vanish.
# A synced link set survives until the next dir barrier; an unsynced unlink may
# survive or be lost. This models permitted states, not actual power failure.
def survivor_states():
    out = []
    def add(stage, journal, names, payload='full'):
        out.append((stage, journal, names, payload))
    for j in ('init', 'torn_prepare', 'prepare'):
        add('prepare_write_unsynced', j, '')
    add('prepare_synced', 'prepare', '')
    for payload in ('empty', 'partial', 'full'):
        for names in ('', 'temp'):
            add('temp_write_unsynced', 'prepare', names, payload)
    for names in ('', 'temp'): add('temp_bytes_synced', 'prepare', names)
    for names in ('', 'temp', 'final', 'both'): add('link_unsynced', 'prepare', names)
    add('link_names_synced', 'prepare', 'both')
    for names in ('both', 'final'): add('unlink_unsynced', 'prepare', names)
    add('unlink_names_synced', 'prepare', 'final')
    for j in ('prepare', 'torn_commit', 'commit'):
        add('commit_write_unsynced', j, 'final')
    add('commit_synced', 'commit', 'final')
    for j in ('commit', 'torn_recovery', 'recovery'):
        add('recovery_write_unsynced', j, 'final')
    add('recovery_synced', 'recovery', 'final')
    return out


@pytest.mark.parametrize('stage,journal_state,names,payload', survivor_states())
def test_deterministic_volatile_survivors(tmp_path, stage, journal_state, names, payload):
    root = p.root_at(tmp_path)
    with p.open_store(root) as s:
        pin = s.descriptor_sha256; original = p.seal(s)
    with p.open_store(root, expected_descriptor_sha256=pin) as s:
        assert s.report.classification == 'VALID'
    journal = root/'seals-v1.jsonl'
    lines = journal.read_bytes().splitlines(keepends=True)
    init, prepare, commit, recovery = lines
    raw_by_state = {
        'init': init, 'torn_prepare': init+prepare[:13], 'prepare': init+prepare,
        'torn_commit': init+prepare+commit[:13], 'commit': init+prepare+commit,
        'torn_recovery': init+prepare+commit+recovery[:13],
        'recovery': init+prepare+commit+recovery,
    }
    journal.write_bytes(raw_by_state[journal_state])
    final = root/'objects'/original.object_sha256
    temp = root/'objects'/json.loads(prepare)['data']['temporary']
    if names in ('temp', 'both'): os.link(final, temp)
    if names in ('', 'temp'): final.unlink()
    data = {'full': b'independent bytes', 'empty': b'', 'partial': b'indep'}[payload]
    if names in ('temp', 'both'): temp.write_bytes(data)
    before = {x.name: x.read_bytes() for x in (root/'objects').iterdir()}
    expected = ('JOURNAL_OR_IDENTITY_INVALID' if journal_state.startswith('torn') else
                'UNRESOLVED_PREPARE' if journal_state == 'prepare' else 'VALID')
    with p.open_store(root, expected_descriptor_sha256=pin) as s:
        assert s.report.classification == expected, stage
        if expected == 'VALID' and journal_state != 'init':
            r = s.receipts[original.object_sha256]
            assert r.clocks == original.clocks and r.acknowledgement == 'UNKNOWN'
            assert s.read_receipt(r) == b'independent bytes'
        elif expected != 'VALID':
            with pytest.raises(LaunchContractError): s.read_receipt(original)
    assert before == {x.name: x.read_bytes() for x in (root/'objects').iterdir()}
    if expected != 'VALID': assert journal.read_bytes() == raw_by_state[journal_state]


@pytest.mark.parametrize('boundary', ['before_temp_create', 'after_temp_create',
    'before_recorder', 'after_recorder', 'before_caller_return', 'after_caller_return'])
def test_sigkill_semantic_boundary(tmp_path, boundary):
    root = p.root_at(tmp_path)
    with p.open_store(root) as s: pin = s.descriptor_sha256
    pid = os.fork()
    if pid == 0:
        s = p.open_store(root, expected_descriptor_sha256=pin)
        def die(): os.kill(os.getpid(), signal.SIGKILL)
        real_open, real_seal = os.open, v1.VersionedImmutableObjectStore.seal_with_provenance
        def opening(name, flags, *args, **kw):
            match = isinstance(name, str) and name.startswith('.tmp-') and flags & os.O_CREAT
            if match and boundary == 'before_temp_create': die()
            fd = real_open(name, flags, *args, **kw)
            if match and boundary == 'after_temp_create': die()
            return fd
        def recorder():
            if boundary == 'before_recorder': die()
            result = p.sample('durable_seal', 103, 4)
            if boundary == 'after_recorder': die()
            return result
        def returning(self, *args, **kw):
            result = real_seal(self, *args, **kw)
            if boundary == 'before_caller_return': die()
            return result
        os.open = opening
        v1.VersionedImmutableObjectStore.seal_with_provenance = returning
        p.seal(s, recorder=recorder)
        if boundary == 'after_caller_return': die()
        os._exit(99)
    import time
    deadline = time.monotonic()+5; status = None
    while time.monotonic() < deadline:
        got, observed = os.waitpid(pid, os.WNOHANG)
        if got: status = observed; break
        time.sleep(.01)
    if status is None:
        os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
        pytest.fail('semantic probe child timed out')
    assert os.WIFSIGNALED(status) and os.WTERMSIG(status) == signal.SIGKILL
    before = {x.name: x.read_bytes() for x in (root/'objects').iterdir()}
    expected = 'VALID' if boundary.endswith('caller_return') else 'UNRESOLVED_PREPARE'
    with p.open_store(root, expected_descriptor_sha256=pin) as s:
        assert s.report.classification == expected
        if expected == 'VALID':
            r = next(iter(s.receipts.values()))
            assert r.acknowledgement == 'UNKNOWN'
            assert s.read_receipt(r) == b'independent bytes'
    assert before == {x.name: x.read_bytes() for x in (root/'objects').iterdir()}
