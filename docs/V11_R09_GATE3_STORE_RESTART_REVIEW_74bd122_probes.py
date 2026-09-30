"""Independent synthetic review probes for exact 74bd122; no real stores.
Defect tests deliberately assert observed faulty behavior, not acceptance.
Run from the candidate with PYTHONPATH=. python -m pytest -q <this file>.
"""
import hashlib
import json
import os
import threading
from dataclasses import replace

import pytest
from tools import v11_r09_gate3_store_v1 as v1
from tools.v11_r09_gate3_offline_io import MeasuredClock
from tools.v11_r09_gate3_launch import LaunchContractError


def root_at(tmp_path):
    root = tmp_path / 'store'
    root.mkdir(mode=0o700)
    (root / 'objects').mkdir(mode=0o700)
    return root


def open_store(root, **kw):
    args = dict(manifest_sha256='a'*64, policy_sha256='b'*64,
        build_id='independent-fixture', clock_method='synthetic',
        max_clock_age_seconds=10, host_id='host-original', boot_id='boot-original')
    args.update(kw)
    return v1.VersionedImmutableObjectStore(root, **args)


def sample(phase, utc, mono):
    raw = f'{phase}:{utc}:{mono}'.encode()
    return v1.ClockEvidence(phase, MeasuredClock(utc, mono, .1, mono,
        'boot-original', hashlib.sha256(raw).hexdigest()), raw, 'synthetic')


def prefix():
    return tuple(sample(p, 100+i, 1+i) for i,p in enumerate(
        ('request_start', 'body_receipt', 'decode_complete')))


def seal(store, data=b'independent bytes', clocks=None, recorder=None):
    prov = v1.ObjectProvenance('RAW', 'request', 'attempt', 'c'*64, 'd'*64, 'e'*64)
    return store.seal_with_provenance(data, prov, prefix() if clocks is None else clocks,
        recorder or (lambda: sample('durable_seal', 103, 4)))


@pytest.mark.parametrize('pin_kind', ['descriptor', 'head', 'both'])
def test_defect_expected_identity_ignored_on_empty_store(tmp_path, pin_kind):
    root = root_at(tmp_path)
    kw = {}
    if pin_kind in ('descriptor', 'both'):
        kw['expected_descriptor_sha256'] = 'f'*64
    if pin_kind in ('head', 'both'):
        kw['expected_head'] = (3, 'e'*64)
    with open_store(root, **kw) as store:
        assert store.report.classification == 'VALID'
        assert store.descriptor_sha256 != 'f'*64
        assert len((root/'seals-v1.jsonl').read_bytes().splitlines()) == 1


def test_defect_mutable_clock_prefix_acknowledges_unreplayable_commit(tmp_path):
    root = root_at(tmp_path)
    clocks = list(prefix())
    original = tuple(clocks)
    with open_store(root) as store:
        pin = store.descriptor_sha256
        def recorder():
            clocks[:] = [sample(s.phase, s.reading.utc_seconds-1,
                s.reading.monotonic_seconds-1) for s in original]
            return sample('durable_seal', 103, 4)
        receipt = seal(store, clocks=clocks, recorder=recorder)
        assert receipt.acknowledgement == 'ACKNOWLEDGED_THIS_SESSION'
        assert receipt.clocks[:3] != original
        assert store.read_receipt(receipt) == b'independent bytes'
    with open_store(root, expected_descriptor_sha256=pin) as held:
        assert held.report.classification == 'JOURNAL_OR_IDENTITY_INVALID'


def test_defect_close_releases_ownership_during_inflight_seal(tmp_path):
    root = root_at(tmp_path)
    store = open_store(root)
    pin = store.descriptor_sha256
    entered, release = threading.Event(), threading.Event()
    errors = []
    def recorder():
        entered.set()
        assert release.wait(5)
        return sample('durable_seal', 103, 4)
    def work():
        try:
            seal(store, recorder=recorder)
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=work)
    thread.start()
    try:
        assert entered.wait(5)
        store.close()
        # Constructor obtains the lock while the first recorder is still running.
        with open_store(root, expected_descriptor_sha256=pin) as competing:
            assert competing.report.classification == 'UNRESOLVED_PREPARE'
            assert thread.is_alive()
    finally:
        release.set()
        thread.join(5)
        store.close()
    assert not thread.is_alive() and errors


def test_defect_reserve_passes_prepare_then_refuses_commit(tmp_path, monkeypatch):
    root = root_at(tmp_path)
    with open_store(root) as store:
        used = (root/'seals-v1.jsonl').stat().st_size
        # Scale only the cap to put this tiny fixture at the real reserve boundary.
        monkeypatch.setattr(v1, 'MAX_JOURNAL', used+3*v1.MAX_RECORD+v1.REPORT_RESERVE+10)
        with pytest.raises(LaunchContractError, match='STORE_JOURNAL_CAPACITY'):
            seal(store)
        records = [json.loads(x) for x in (root/'seals-v1.jsonl').read_bytes().splitlines()]
        assert [r['event'] for r in records] == ['INIT', 'PREPARE']
        assert len(list((root/'objects').iterdir())) == 1
        assert store._failed


def test_defect_original_host_not_bound(tmp_path):
    root = root_at(tmp_path)
    with open_store(root) as store:
        pin = store.descriptor_sha256
        receipt = seal(store)
    persisted = (root/'store-v1.json').read_bytes()+(root/'seals-v1.jsonl').read_bytes()
    assert b'host-original' not in persisted
    with open_store(root, expected_descriptor_sha256=pin, host_id='different-host') as other:
        assert other.report.classification == 'VALID'
        assert other.read_receipt(other.receipts[receipt.object_sha256]) == b'independent bytes'
        assert seal(other, b'new bytes').acknowledgement == 'ACKNOWLEDGED_THIS_SESSION'


def test_positive_repeated_recovery_and_external_head(tmp_path):
    root = root_at(tmp_path)
    with open_store(root) as store:
        pin = store.descriptor_sha256
        receipt = seal(store)
        head = (store._seq, store.report.journal_head)
    for _ in range(3):
        with open_store(root, expected_descriptor_sha256=pin, expected_head=head) as store:
            recovered = store.receipts[receipt.object_sha256]
            assert recovered.clocks == receipt.clocks
            assert recovered.acknowledgement == 'UNKNOWN'
            assert not recovered.historical_feature_eligible
            assert store.report.rollback_assurance == 'EXTERNAL_HEAD_MATCH'
            assert store.read_receipt(recovered) == b'independent bytes'


@pytest.mark.parametrize('kind', ['sparse_journal', 'sparse_object', 'fifo_descriptor',
    'symlink_journal', 'hardlink_object', 'unknown_name', 'torn_journal', 'changed_descriptor'])
def test_integrity_and_bounded_nonregular_refusals(tmp_path, kind):
    root = root_at(tmp_path)
    with open_store(root) as store:
        pin = store.descriptor_sha256
        receipt = seal(store)
    journal, descriptor = root/'seals-v1.jsonl', root/'store-v1.json'
    obj = root/'objects'/receipt.object_sha256
    if kind == 'sparse_journal':
        with journal.open('ab') as stream: stream.truncate(v1.MAX_JOURNAL+1)
    elif kind == 'sparse_object':
        with obj.open('ab') as stream: stream.truncate(v1.MAX_OBJECT+1)
    elif kind == 'fifo_descriptor':
        descriptor.rename(tmp_path/'saved'); os.mkfifo(descriptor, 0o600)
    elif kind == 'symlink_journal':
        journal.rename(tmp_path/'saved'); journal.symlink_to(tmp_path/'saved')
    elif kind == 'hardlink_object':
        os.link(obj, tmp_path/'alias')
    elif kind == 'unknown_name':
        (root/'objects'/'unknown').write_bytes(b'unknown')
    elif kind == 'torn_journal':
        with journal.open('ab') as stream: stream.write(b'{')
    else:
        descriptor.write_bytes(descriptor.read_bytes().replace(b'independent-fixture', b'changed-fixture'))
    before = set(x.name for x in (root/'objects').iterdir())
    with open_store(root, expected_descriptor_sha256=pin) as held:
        assert not held.report.recovery_validated
        assert held.report.classification != 'VALID'
        with pytest.raises(LaunchContractError): held.read_receipt(receipt)
    assert set(x.name for x in (root/'objects').iterdir()) == before


# Before/after side effects at every write/fsync/link/unlink in a small seal.
# OSError is an uncertainty probe, not a physical crash or power-loss model.
@pytest.mark.parametrize('operation,number', [('write',1),('write',2),('write',3),
    ('fsync',1),('fsync',2),('fsync',3),('fsync',4),('fsync',5),('link',1),('unlink',1)])
@pytest.mark.parametrize('after', [False, True])
def test_seal_side_effect_uncertainty_matrix(tmp_path, monkeypatch, operation, number, after):
    root = root_at(tmp_path)
    with open_store(root) as store:
        pin = store.descriptor_sha256
        real, count = getattr(os, operation), 0
        def fault(*args, **kwargs):
            nonlocal count
            count += 1
            if count == number and not after: raise OSError('independent injected fault')
            result = real(*args, **kwargs)
            if count == number: raise OSError('independent injected fault')
            return result
        with monkeypatch.context() as patch:
            patch.setattr(os, operation, fault)
            with pytest.raises(OSError, match='independent injected fault'): seal(store)
        assert store._failed
    names = set(x.name for x in (root/'objects').iterdir())
    lines = (root/'seals-v1.jsonl').read_bytes().splitlines()
    complete_commit = any(json.loads(x)['event']=='COMMIT' for x in lines)
    with open_store(root, expected_descriptor_sha256=pin) as recovered:
        # Before PREPARE's write the prior INIT-only store is still valid.
        valid = complete_commit or len(lines)==1
        assert (recovered.report.classification == 'VALID') == valid
        if complete_commit:
            receipt = next(iter(recovered.receipts.values()))
            assert receipt.acknowledgement == 'UNKNOWN'
            assert recovered.read_receipt(receipt) == b'independent bytes'
    assert set(x.name for x in (root/'objects').iterdir()) == names


@pytest.mark.parametrize('number', [1,2,3,4,5])
@pytest.mark.parametrize('after', [False, True])
def test_recovery_barrier_uncertainty_matrix(tmp_path, monkeypatch, number, after):
    root = root_at(tmp_path)
    with open_store(root) as store:
        pin = store.descriptor_sha256
        original = seal(store)
    real, count = os.fsync, 0
    def fault(fd):
        nonlocal count
        count += 1
        if count == number and not after: raise OSError('independent recovery fault')
        result = real(fd)
        if count == number: raise OSError('independent recovery fault')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', fault)
        with open_store(root, expected_descriptor_sha256=pin) as held:
            assert held.report.classification == 'RECOVERY_INCOMPLETE'
            with pytest.raises(LaunchContractError): held.read_receipt(original)
    with open_store(root, expected_descriptor_sha256=pin) as recovered:
        assert recovered.report.classification == 'VALID'
        assert recovered.receipts[original.object_sha256].clocks == original.clocks


@pytest.mark.parametrize('operation,number', [('write',1),('write',2),
    ('fsync',1),('fsync',2),('fsync',3),('fsync',4)])
@pytest.mark.parametrize('after', [False, True])
def test_initialization_write_and_barrier_uncertainty(tmp_path, monkeypatch, operation, number, after):
    root = root_at(tmp_path)
    real, count = getattr(os, operation), 0
    def fault(*args, **kwargs):
        nonlocal count
        count += 1
        if count == number and not after: raise OSError('independent init fault')
        result = real(*args, **kwargs)
        if count == number: raise OSError('independent init fault')
        return result
    with monkeypatch.context() as patch:
        patch.setattr(os, operation, fault)
        with pytest.raises(OSError, match='independent init fault'): open_store(root)
    descriptor = root/'store-v1.json'
    pin = hashlib.sha256(descriptor.read_bytes()).hexdigest()
    journal = root/'seals-v1.jsonl'
    has_init = journal.exists() and bool(journal.read_bytes())
    names = set(x.name for x in root.iterdir())
    with open_store(root, expected_descriptor_sha256=pin) as reopened:
        assert (reopened.report.classification == 'VALID') == has_init
        assert not reopened.receipts
    assert set(x.name for x in root.iterdir()) == names


@pytest.mark.parametrize('partial', [False, True])
def test_zero_and_torn_prepare_writes_hold_without_object_mutation(tmp_path, monkeypatch, partial):
    root = root_at(tmp_path)
    with open_store(root) as store:
        pin = store.descriptor_sha256
        real, count = os.write, 0
        def fault(fd, raw):
            nonlocal count
            count += 1
            if partial and count == 1:
                return real(fd, raw[:7])
            return 0
        with monkeypatch.context() as patch:
            patch.setattr(os, 'write', fault)
            with pytest.raises(LaunchContractError, match='STORE_SHORT_WRITE'): seal(store)
        assert store._failed
        assert not list((root/'objects').iterdir())
    with open_store(root, expected_descriptor_sha256=pin) as reopened:
        assert reopened.report.classification == ('JOURNAL_OR_IDENTITY_INVALID' if partial else 'VALID')
