"""Disposable synthetic v1 restart fixtures; no provider or real store access."""
import hashlib
import multiprocessing
import os
import select
import signal
import subprocess
import sys
import threading

import pytest

from tools import v11_r09_gate3_store_v1 as store_v1
from tools.v11_r09_gate3_launch import LaunchContractError
from tools.v11_r09_gate3_offline_io import (ClockEvidence, MeasuredClock,
    ObjectProvenance, VersionedImmutableObjectStore)

PINNED = {}


def root_at(tmp_path):
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    (root / 'objects').mkdir(mode=0o700)
    return root


def open_store(root, **changes):
    kwargs = dict(manifest_sha256='a'*64, policy_sha256='b'*64, build_id='fixture-v1',
                  clock_method='synthetic', max_clock_age_seconds=10,
                  host_id='fixture-host', boot_id='fixture-boot')
    kwargs.update(changes)
    if root in PINNED and 'expected_descriptor_sha256' not in changes:
        kwargs['expected_descriptor_sha256'] = PINNED[root]
    store = VersionedImmutableObjectStore(root, **kwargs)
    if store.report.classification == 'VALID':
        PINNED.setdefault(root, store.descriptor_sha256)
    return store


def sample(phase, utc, mono):
    raw = f'{phase}:{utc}:{mono}'.encode()
    return ClockEvidence(phase, MeasuredClock(utc, mono, 0.1, mono,
        'fixture-boot', hashlib.sha256(raw).hexdigest()), raw, 'synthetic')


def seal(store, raw=b'fixture bytes', recorder=None):
    phases = ('request_start', 'body_receipt', 'decode_complete')
    prefix = tuple(sample(p, 100+i, 1+i) for i, p in enumerate(phases))
    prov = ObjectProvenance('RAW', 'request-1', 'attempt-1', 'c'*64,
                            'd'*64, 'e'*64)
    return store.seal_with_provenance(raw, prov, prefix,
             recorder or (lambda: sample('durable_seal', 103, 4)))


def test_clean_restart_retains_original_clock_and_unknown_ack(tmp_path):
    root = root_at(tmp_path)
    with open_store(root) as store:
        receipt = seal(store)
        assert store.read_receipt(receipt) == b'fixture bytes'
        assert store.report.classification == 'VALID'
        head = store.report.journal_head
    with open_store(root, expected_head=(3, head)) as recovered:
        assert recovered.report.classification == 'VALID'
        assert recovered.report.rollback_assurance == 'EXTERNAL_HEAD_MATCH'
        old = recovered.receipts[receipt.object_sha256]
        assert old.acknowledgement == 'UNKNOWN'
        assert old.clocks == receipt.clocks
        assert old.object_seal_witnessed and not old.historical_feature_eligible
        assert recovered.read_receipt(old) == b'fixture bytes'
        with pytest.raises(LaunchContractError, match='OBJECT_ALREADY_EXISTS'):
            seal(recovered)
        second = seal(recovered, b'other bytes')
        assert recovered.read_receipt(second) == b'other bytes'
    with open_store(root) as again:
        assert again.report.classification == 'VALID'
        assert again.receipts[receipt.object_sha256].clocks == receipt.clocks


def test_recorder_runs_after_namespace_fsync_and_reentrancy_refused(tmp_path):
    root = root_at(tmp_path)
    with open_store(root) as store:
        def recorder():
            assert len(list((root / 'objects').iterdir())) == 1
            with pytest.raises(LaunchContractError, match='STORE_SEAL_INTERFACE'):
                seal(store, b'reentrant')
            return sample('durable_seal', 103, 4)
        receipt = seal(store, recorder=recorder)
        assert store.read_receipt(receipt) == b'fixture bytes'


def test_failed_recorder_holds_prepare_and_preserves_final(tmp_path):
    root = root_at(tmp_path)
    with open_store(root) as store:
        def failed():
            raise RuntimeError('fixture recorder failure')
        with pytest.raises(RuntimeError):
            seal(store, recorder=failed)
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            store.read_receipt(None)
    with open_store(root) as recovered:
        assert recovered.report.classification == 'UNRESOLVED_PREPARE'
        assert len(list((root / 'objects').iterdir())) == 1


def test_torn_journal_and_unknown_name_hold(tmp_path):
    root = root_at(tmp_path)
    with open_store(root) as store:
        receipt = seal(store)
    (root / 'objects' / 'unexpected').write_bytes(b'x')
    with open_store(root) as held:
        assert held.report.classification == 'NAMESPACE_CONFLICT'
    (root / 'objects' / 'unexpected').unlink()
    with (root / 'seals-v1.jsonl').open('ab') as stream:
        stream.write(b'{"incomplete":')
    with open_store(root) as held:
        assert held.report.classification == 'JOURNAL_OR_IDENTITY_INVALID'
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            held.read_receipt(receipt)


def _compete(root, sender):
    try:
        with open_store(root):
            sender.send('acquired')
    except BlockingIOError:
        sender.send('blocked')


def test_exclusive_process_lock_and_fork_child_cannot_unlock(tmp_path):
    root = root_at(tmp_path)
    with open_store(root) as store:
        with pytest.raises(BlockingIOError):
            open_store(root)
        receiver, sender = multiprocessing.get_context('spawn').Pipe(False)
        child = multiprocessing.get_context('spawn').Process(target=_compete,
                                                            args=(root, sender))
        child.start()
        try:
            assert receiver.poll(5) and receiver.recv() == 'blocked'
            child.join(5)
        finally:
            if child.is_alive():
                child.terminate(); child.join(5)
        pid = os.fork()
        if pid == 0:
            try:
                store.read_receipt(None)
            except LaunchContractError:
                os._exit(0)
            os._exit(1)
        assert os.waitpid(pid, 0)[1] == 0
        with pytest.raises(BlockingIOError):
            open_store(root)
    with open_store(root) as reopened:
        assert reopened.report.classification == 'VALID'


def test_legacy_nonempty_and_context_mismatch_refused(tmp_path):
    root = root_at(tmp_path)
    (root / 'objects' / 'legacy').write_bytes(b'legacy')
    with open_store(root) as held:
        assert held.report.classification == 'LEGACY_PROVENANCE_MISSING'
    (root / 'objects' / 'legacy').unlink()
    with open_store(root):
        pass
    with open_store(root, manifest_sha256='f'*64) as held:
        assert held.report.classification == 'JOURNAL_OR_IDENTITY_INVALID'
        assert not held.report.recovery_validated


def test_budget_is_not_changed_by_store_success(tmp_path):
    # The standalone store has no budget handle or capture-success method.
    root = root_at(tmp_path)
    with open_store(root) as store:
        receipt = seal(store)
        assert receipt.acknowledgement == 'ACKNOWLEDGED_THIS_SESSION'
        assert not hasattr(receipt, 'capture_success')


@pytest.mark.parametrize('failed_event,expected', [
    ('PREPARE', 'UNRESOLVED_PREPARE'),
    ('COMMIT', 'VALID'),
])
def test_complete_record_survives_uncertain_journal_fsync(tmp_path, monkeypatch,
                                                              failed_event, expected):
    root = root_at(tmp_path)
    with open_store(root) as store:
        real = os.fsync
        journal_calls = 0
        def uncertain(fd):
            nonlocal journal_calls
            if fd == store.journal_fd:
                journal_calls += 1
                if journal_calls == (1 if failed_event == 'PREPARE' else 2):
                    # The file's bytes may survive even if the fsync call fails.
                    raise OSError('synthetic uncertain journal fsync')
            return real(fd)
        with monkeypatch.context() as patch:
            patch.setattr(os, 'fsync', uncertain)
            with pytest.raises(OSError, match='uncertain journal'):
                seal(store)
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            store.seal_with_provenance(b'x', None, (), lambda: None)
    with open_store(root) as reopened:
        assert reopened.report.classification == expected
        if expected == 'VALID':
            receipt = next(iter(reopened.receipts.values()))
            assert receipt.acknowledgement == 'UNKNOWN'
            assert reopened.read_receipt(receipt) == b'fixture bytes'
        else:
            assert list((root / 'objects').iterdir()) == []


def test_failed_recovery_record_fsync_requires_new_validation(tmp_path, monkeypatch):
    root = root_at(tmp_path)
    with open_store(root) as store:
        original = seal(store)
    real = os.fsync
    journal_calls = 0
    def uncertain(fd):
        nonlocal journal_calls
        if os.readlink(f'/proc/self/fd/{fd}').endswith('/seals-v1.jsonl'):
            journal_calls += 1
            if journal_calls == 2:
                raise OSError('synthetic recovery fsync failure')
        return real(fd)
    with monkeypatch.context() as patch:
        patch.setattr(os, 'fsync', uncertain)
        with open_store(root) as held:
            assert held.report.classification == 'RECOVERY_INCOMPLETE'
            with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
                held.read_receipt(original)
    with open_store(root) as recovered:
        assert recovered.report.classification == 'VALID'
        assert recovered.receipts[original.object_sha256].clocks == original.clocks


def test_fifo_journal_and_alias_do_not_block_or_repair(tmp_path):
    root = root_at(tmp_path)
    with open_store(root) as store:
        receipt = seal(store)
    journal = root / 'seals-v1.jsonl'
    saved = tmp_path / 'saved-journal'
    journal.rename(saved)
    os.mkfifo(journal, 0o600)
    with open_store(root) as held:
        assert held.report.classification == 'JOURNAL_OR_IDENTITY_INVALID'
    journal.unlink()
    saved.rename(journal)
    os.link(root / 'objects' / receipt.object_sha256,
            root / 'objects' / '.tmp-leftover')
    with open_store(root) as held:
        assert held.report.classification == 'NAMESPACE_CONFLICT'
        assert (root / 'objects' / '.tmp-leftover').exists()


def test_cross_boot_is_historical_read_only_and_forgery_rejected(tmp_path):
    root = root_at(tmp_path)
    with open_store(root) as store:
        original = seal(store)
    with open_store(root, boot_id='new-boot') as recovered:
        receipt = recovered.receipts[original.object_sha256]
        assert recovered.read_receipt(receipt) == b'fixture bytes'
        with pytest.raises(LaunchContractError, match='STORE_CROSS_BOOT_ACQUISITION_HELD'):
            seal(recovered, b'new')
        from dataclasses import replace
        with pytest.raises(LaunchContractError, match='OBJECT_RECEIPT_MISMATCH'):
            recovered.read_receipt(replace(receipt, historical_feature_eligible=True))


def test_feature_manifest_keeps_raw_dependency_and_no_eligibility_claim(tmp_path):
    root = root_at(tmp_path)
    with open_store(root) as store:
        raw = seal(store)
        phases = ('request_start', 'body_receipt', 'decode_complete')
        prefix = tuple(sample(p, 200+i, 10+i) for i, p in enumerate(phases))
        manifest = ObjectProvenance('FEATURE_MANIFEST', 'feature-request',
            'feature-attempt', 'c'*64, 'd'*64, 'e'*64, (raw.commit_hash,))
        feature = store.seal_with_provenance(b'fixture feature manifest', manifest,
             prefix, lambda: sample('durable_seal', 203, 13))
        assert not feature.historical_feature_eligible
    with open_store(root) as recovered:
        got = recovered.receipts[feature.object_sha256]
        assert got.provenance.dependencies == (raw.commit_hash,)
        assert not got.historical_feature_eligible


def test_root_replacement_poison_and_no_reuse(tmp_path):
    root = root_at(tmp_path)
    with open_store(root) as store:
        other = tmp_path / 'replacement'
        other.mkdir(mode=0o700)
        (other / 'objects').mkdir(mode=0o700)
        root.rename(tmp_path / 'moved')
        other.rename(root)
        with pytest.raises(LaunchContractError, match='OBJECT_ROOT_CHANGED'):
            seal(store)
        root.rename(other)
        (tmp_path / 'moved').rename(root)
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            seal(store)


@pytest.mark.parametrize('journal_state,names,expected', [
    ('prepare', 'none', 'UNRESOLVED_PREPARE'),
    ('prepare', 'temporary', 'UNRESOLVED_PREPARE'),
    ('prepare', 'final', 'UNRESOLVED_PREPARE'),
    ('prepare', 'both', 'UNRESOLVED_PREPARE'),
    ('commit', 'none', 'NAMESPACE_CONFLICT'),
    ('commit', 'final', 'VALID'),
    ('commit', 'both', 'NAMESPACE_CONFLICT'),
])
def test_persistence_survival_model(tmp_path, journal_state, names, expected):
    # Model: INIT and PREPARE have returned file fsync; final link becomes
    # durable at the first directory fsync, removal of temporary at the second,
    # and COMMIT only at its journal fsync. Unsynced names/bytes may survive or
    # disappear. This tests on-disk survivor states, not physical power loss.
    root = root_at(tmp_path)
    with open_store(root) as store:
        receipt = seal(store)
    journal = root / 'seals-v1.jsonl'
    if journal_state == 'prepare':
        journal.write_bytes(b''.join(journal.read_bytes().splitlines(keepends=True)[:2]))
    final = root / 'objects' / receipt.object_sha256
    import json
    prepared = json.loads(journal.read_bytes().splitlines()[1])
    temporary = root / 'objects' / prepared['data']['temporary']
    if names in ('temporary', 'both'):
        os.link(final, temporary)
    if names in ('none', 'temporary'):
        final.unlink()
    with open_store(root) as reopened:
        assert reopened.report.classification == expected
        if expected == 'VALID':
            assert reopened.receipts[receipt.object_sha256].acknowledgement == 'UNKNOWN'
        else:
            assert set(p.name for p in (root / 'objects').iterdir()) == (
                ({receipt.object_sha256} if names in ('final', 'both') else set()) |
                ({temporary.name} if names in ('temporary', 'both') else set()))


def _crash_seal(root, marker):
    from tools.v11_r09_gate3_store_v1 import VersionedImmutableObjectStore as V1
    with open_store(root) as store:
        if marker == 'after_link':
            real = os.link
            def interrupted(*args, **kwargs):
                real(*args, **kwargs)
                os._exit(17)
            os.link = interrupted
        else:
            real = V1._append
            def interrupted(self, event, data, **kwargs):
                result = real(self, event, data, **kwargs)
                if event == 'COMMIT':
                    os._exit(18)
                return result
            V1._append = interrupted
        seal(store)


@pytest.mark.parametrize('marker,exit_code,classification', [
    ('after_link', 17, 'UNRESOLVED_PREPARE'),
    ('after_commit', 18, 'VALID'),
])
def test_process_interruption_is_not_power_loss(tmp_path, marker, exit_code,
                                                  classification):
    root = root_at(tmp_path)
    with open_store(root):
        pass
    child = multiprocessing.get_context('fork').Process(target=_crash_seal,
                                                         args=(root, marker))
    child.start()
    try:
        child.join(5)
        assert not child.is_alive() and child.exitcode == exit_code
    finally:
        if child.is_alive():
            child.terminate(); child.join(5)
    with open_store(root) as recovered:
        assert recovered.report.classification == classification
        if classification == 'VALID':
            assert next(iter(recovered.receipts.values())).acknowledgement == 'UNKNOWN'


def test_partial_initialization_and_legacy_helper_cannot_modify_v1(tmp_path):
    from tools.v11_r09_gate3_offline_io import ImmutableObjectStore
    root = root_at(tmp_path)
    with open_store(root) as store:
        assert not store.report.recovery_validated
        with pytest.raises(BlockingIOError):
            ImmutableObjectStore(root)
    with ImmutableObjectStore(root) as legacy:
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            legacy.seal(b'legacy cannot enter v1')
    journal = root / 'seals-v1.jsonl'
    saved = tmp_path / 'saved'
    journal.rename(saved)
    with open_store(root) as held:
        assert held.report.classification == 'JOURNAL_OR_IDENTITY_INVALID'
        assert not journal.exists()
    saved.rename(journal)
    with open_store(root) as recovered:
        assert recovered.report.classification == 'VALID'
        assert recovered.report.recovery_validated


def test_disk_reserve_refusal_precedes_prepare_and_remains_usable(tmp_path, monkeypatch):
    from types import SimpleNamespace
    root = root_at(tmp_path)
    with open_store(root) as store:
        with monkeypatch.context() as patch:
            patch.setattr(os, 'fstatvfs', lambda _fd: SimpleNamespace(f_bavail=0,
                                                            f_frsize=4096))
            with pytest.raises(LaunchContractError, match='STORE_DISK_RESERVE'):
                seal(store)
        assert len((root / 'seals-v1.jsonl').read_bytes().splitlines()) == 1
        receipt = seal(store)
        assert store.read_receipt(receipt) == b'fixture bytes'


def test_raw_clock_evidence_and_age_reject_before_prepare(tmp_path):
    from dataclasses import replace
    root = root_at(tmp_path)
    prefix = tuple(sample(p, 100+i, 1+i) for i, p in enumerate(
        ('request_start', 'body_receipt', 'decode_complete')))
    prov = ObjectProvenance('RAW', 'request-1', 'attempt-1', 'c'*64, 'd'*64, 'e'*64)
    with open_store(root) as store:
        with pytest.raises(LaunchContractError, match='CLOCK_RAW_EVIDENCE'):
            store.seal_with_provenance(b'x', prov,
                (replace(prefix[0], raw=b'fake'), *prefix[1:]),
                lambda: sample('durable_seal', 103, 4))
        stale = replace(prefix[1], reading=replace(prefix[1].reading,
                          measured_monotonic_seconds=-100))
        with pytest.raises(LaunchContractError, match='CLOCK_MEASUREMENT_BOUND'):
            store.seal_with_provenance(b'x', prov, (prefix[0], stale, prefix[2]),
                lambda: sample('durable_seal', 103, 4))
        assert len((root / 'seals-v1.jsonl').read_bytes().splitlines()) == 1
        assert seal(store).object_seal_witnessed


def test_external_head_detects_complete_local_rollback(tmp_path):
    root = root_at(tmp_path)
    with open_store(root) as store:
        receipt = seal(store)
        head = store.report.journal_head
    journal = root / 'seals-v1.jsonl'
    journal.write_bytes(b''.join(journal.read_bytes().splitlines(keepends=True)[:2]))
    (root / 'objects' / receipt.object_sha256).unlink()
    with open_store(root, expected_head=(3, head)) as held:
        assert held.report.classification == 'JOURNAL_OR_IDENTITY_INVALID'
        assert not held.report.recovery_validated


def test_mutated_object_poisoned_and_reopen_namespace_conflict(tmp_path):
    root = root_at(tmp_path)
    with open_store(root) as store:
        receipt = seal(store)
        (root / 'objects' / receipt.object_sha256).write_bytes(b'tampered byte')
        with pytest.raises(LaunchContractError, match='OBJECT_DIGEST_MISMATCH'):
            store.read_receipt(receipt)
        with pytest.raises(LaunchContractError, match='OBJECT_DURABILITY_UNCERTAIN'):
            store.read_receipt(receipt)
    with open_store(root) as held:
        assert held.report.classification == 'NAMESPACE_CONFLICT'


@pytest.mark.parametrize('pins', ('descriptor', 'head', 'both'))
def test_retained_pin_refuses_complete_same_root_state_loss(tmp_path, pins):
    root = root_at(tmp_path)
    with open_store(root) as store:
        descriptor = store.descriptor_sha256
        head = (1, store.report.journal_head)
    (root / 'store-v1.json').unlink()
    (root / 'seals-v1.jsonl').unlink()
    PINNED.pop(root)
    supplied = {}
    if pins in ('descriptor', 'both'):
        supplied['expected_descriptor_sha256'] = descriptor
    if pins in ('head', 'both'):
        supplied['expected_head'] = head
    with open_store(root, **supplied) as held:
        assert held.report.classification == 'JOURNAL_OR_IDENTITY_INVALID'
        assert not held.report.recovery_validated
    assert set(p.name for p in root.iterdir()) == {'objects'}
    with open_store(root) as fresh:
        assert fresh.report.classification == 'VALID'
        assert fresh.descriptor_sha256 != descriptor


def test_close_waits_for_seal_and_callback_cannot_close(tmp_path):
    root = root_at(tmp_path)
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    store = open_store(root)
    errors = []
    def recorder():
        with pytest.raises(LaunchContractError, match='STORE_CALLBACK_REENTRANCY'):
            store.close()
        entered.set()
        assert release.wait(5)
        return sample('durable_seal', 103, 4)
    def writer():
        try:
            seal(store, recorder=recorder)
        except BaseException as exc:
            errors.append(exc)
    def closer():
        try:
            store.close()
        except BaseException as exc:
            errors.append(exc)
        finally:
            closed.set()
    writer_thread = threading.Thread(target=writer)
    close_thread = threading.Thread(target=closer)
    try:
        writer_thread.start()
        assert entered.wait(5)
        close_thread.start()
        assert not closed.wait(0.1)
        with pytest.raises(BlockingIOError):
            open_store(root)
        release.set()
        writer_thread.join(5)
        close_thread.join(5)
        assert not writer_thread.is_alive() and not close_thread.is_alive()
        assert not errors
        assert closed.is_set()
    finally:
        release.set()
        writer_thread.join(5)
        close_thread.join(5) if close_thread.ident is not None else None
        store.close()
    with open_store(root) as reopened:
        assert reopened.report.classification == 'VALID'
        assert len(reopened.receipts) == 1


def test_close_waits_for_read(tmp_path, monkeypatch):
    from tools import v11_r09_gate3_store_v1 as module
    root = root_at(tmp_path)
    store = open_store(root)
    receipt = seal(store)
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    real_file = module._file
    results = []
    def paused_file(fd, name, **kwargs):
        if fd == store.dir_fd and name == receipt.object_sha256:
            entered.set()
            assert release.wait(5)
        return real_file(fd, name, **kwargs)
    def reader():
        results.append(store.read_receipt(receipt))
    def closer():
        store.close()
        closed.set()
    with monkeypatch.context() as patch:
        patch.setattr(module, '_file', paused_file)
        read_thread = threading.Thread(target=reader)
        close_thread = threading.Thread(target=closer)
        try:
            read_thread.start()
            assert entered.wait(5)
            close_thread.start()
            assert not closed.wait(0.1)
            with pytest.raises(BlockingIOError):
                open_store(root)
            release.set()
            read_thread.join(5)
            close_thread.join(5)
            assert not read_thread.is_alive() and not close_thread.is_alive()
            assert results == [b'fixture bytes']
        finally:
            release.set()
            read_thread.join(5)
            close_thread.join(5) if close_thread.ident is not None else None
            store.close()


def test_fork_cleanup_and_exec_release_after_close(tmp_path):
    root = root_at(tmp_path)
    store = open_store(root)
    pid = os.fork()
    if pid == 0:
        store.close()
        os._exit(0)
    assert os.waitpid(pid, 0)[1] == 0
    with pytest.raises(BlockingIOError):
        open_store(root)
    store.close()
    program = ('from pathlib import Path; from tools.v11_r09_gate3_store_v1 '
               'import VersionedImmutableObjectStore as S; '
               's=S(Path(__import__("sys").argv[1]), manifest_sha256="a"*64, '
               'policy_sha256="b"*64, build_id="fixture-v1", '
               'clock_method="synthetic", max_clock_age_seconds=10, '
               'host_id="fixture-host", boot_id="fixture-boot", '
               'expected_descriptor_sha256=__import__("sys").argv[2]); '
               'assert s.report.classification=="VALID"; s.close()')
    result = subprocess.run([sys.executable, '-c', program, str(root), PINNED[root]],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('mutation', ('recorder', 'thread'))
def test_mutable_prefix_is_frozen_before_prepare(tmp_path, mutation):
    root = root_at(tmp_path)
    prefix = [sample(p, 100+i, 1+i) for i, p in enumerate(
        ('request_start', 'body_receipt', 'decode_complete'))]
    original = tuple(prefix)
    provenance = ObjectProvenance('RAW', 'request-1', 'attempt-1',
                                  'c'*64, 'd'*64, 'e'*64)
    entered, release = threading.Event(), threading.Event()
    def change():
        prefix[:] = [sample(p, 200+i, 10+i) for i, p in enumerate(
            ('request_start', 'body_receipt', 'decode_complete'))]
    def recorder():
        if mutation == 'recorder':
            change()
        else:
            entered.set()
            assert release.wait(5)
        return sample('durable_seal', 103, 4)
    with open_store(root) as store:
        if mutation == 'thread':
            worker = threading.Thread(target=lambda: (entered.wait(5), change(),
                                                       release.set()))
            worker.start()
        receipt = store.seal_with_provenance(b'freeze-prefix', provenance, prefix,
                                             recorder)
        if mutation == 'thread':
            worker.join(5)
            assert not worker.is_alive()
        assert receipt.clocks[:3] == original
    with open_store(root) as recovered:
        assert recovered.report.classification == 'VALID'
        assert recovered.receipts[receipt.object_sha256].clocks == receipt.clocks


@pytest.mark.parametrize('extra,accepted', ((-1, False), (0, True), (1, True)))
def test_transaction_reserve_boundary_is_stable(tmp_path, monkeypatch,
                                                extra, accepted):
    from tools import v11_r09_gate3_store_v1 as module
    root = root_at(tmp_path)
    with open_store(root) as store:
        initial = (root / 'seals-v1.jsonl').stat().st_size
        cap = initial + 3 * module.MAX_RECORD + module.REPORT_RESERVE + extra
        with monkeypatch.context() as patch:
            patch.setattr(module, 'MAX_JOURNAL', cap)
            if accepted:
                receipt = seal(store)
                assert store.read_receipt(receipt) == b'fixture bytes'
            else:
                with pytest.raises(LaunchContractError, match='STORE_JOURNAL_CAPACITY'):
                    seal(store)
                assert (root / 'seals-v1.jsonl').stat().st_size == initial
        if not accepted:
            assert seal(store).object_seal_witnessed
    with open_store(root) as recovered:
        assert recovered.report.classification == 'VALID'


def test_recovery_record_capacity_refuses_without_writing(tmp_path, monkeypatch):
    from tools import v11_r09_gate3_store_v1 as module
    # INIT's canonical record length is fixed even though its hash and store ID vary.
    first = tmp_path / 'first'
    first.mkdir()
    root = root_at(first)
    with open_store(root):
        pass
    init_size = (root / 'seals-v1.jsonl').stat().st_size
    second = tmp_path / 'second'
    second.mkdir()
    constrained = root_at(second)
    cap = module.REPORT_RESERVE + init_size + 1
    with monkeypatch.context() as patch:
        patch.setattr(module, 'MAX_JOURNAL', cap)
        with open_store(constrained) as store:
            assert store.report.classification == 'VALID'
        journal = constrained / 'seals-v1.jsonl'
        original = journal.read_bytes()
        with open_store(constrained) as held:
            assert held.report.classification == 'RECOVERY_INCOMPLETE'
        assert journal.read_bytes() == original


def test_original_host_and_init_only_cross_boot_are_bound(tmp_path):
    root = root_at(tmp_path)
    with open_store(root) as store:
        descriptor = (root / 'store-v1.json').read_bytes()
        assert b'fixture-host' in descriptor and b'fixture-boot' in descriptor
    with open_store(root, host_id='different-host') as held:
        assert held.report.classification == 'JOURNAL_OR_IDENTITY_INVALID'
    with open_store(root, boot_id='new-boot') as historic:
        assert historic.report.classification == 'VALID'
        with pytest.raises(LaunchContractError, match='STORE_CROSS_BOOT_ACQUISITION_HELD'):
            seal(historic)
    with open_store(root) as same_boot:
        assert seal(same_boot).object_seal_witnessed


def _crash_initialization_boundary(root, stage):
    from tools.v11_r09_gate3_store_v1 import VersionedImmutableObjectStore as V1
    real_open, real_write, real_fsync, real_append = (os.open, os.write,
                                                     os.fsync, V1._append)
    def path(fd):
        return os.readlink(f'/proc/self/fd/{fd}')
    def opening(name, flags, *args, **kwargs):
        match = ((stage.endswith('descriptor_create') and name == 'store-v1.json') or
                 (stage.endswith('journal_create') and name == 'seals-v1.jsonl'))
        if match and stage.startswith('before_'):
            os._exit(29)
        fd = real_open(name, flags, *args, **kwargs)
        if match and stage.startswith('after_'):
            os._exit(29)
        return fd
    def writing(fd, raw):
        match = (stage.endswith('descriptor_write') and
                 path(fd).endswith('/store-v1.json')) or (
                 stage.endswith('init_write') and path(fd).endswith('/seals-v1.jsonl'))
        if match and stage.startswith('before_'):
            os._exit(29)
        count = real_write(fd, raw)
        if match and stage.startswith('after_'):
            os._exit(29)
        return count
    def syncing(fd):
        name = path(fd)
        match = ((stage.endswith('descriptor_fsync') and name.endswith('/store-v1.json')) or
                 (stage.endswith('journal_fsync') and name.endswith('/seals-v1.jsonl')) or
                 (stage.endswith('root_fsync') and name == str(root)))
        if match and stage.startswith('before_'):
            os._exit(29)
        result = real_fsync(fd)
        if match and stage.startswith('after_'):
            os._exit(29)
        return result
    def appending(self, event, data, **kwargs):
        result = real_append(self, event, data, **kwargs)
        if event == 'INIT' and stage == 'after_init_fsync':
            os._exit(29)
        return result
    os.open, os.write, os.fsync, V1._append = opening, writing, syncing, appending
    open_store(root)


@pytest.mark.parametrize('stage,expected', [
    ('before_descriptor_create', 'VALID'),
    ('after_descriptor_create', 'JOURNAL_OR_IDENTITY_INVALID'),
    ('before_descriptor_write', 'JOURNAL_OR_IDENTITY_INVALID'),
    ('after_descriptor_write', 'JOURNAL_OR_IDENTITY_INVALID'),
    ('before_descriptor_fsync', 'JOURNAL_OR_IDENTITY_INVALID'),
    ('after_descriptor_fsync', 'JOURNAL_OR_IDENTITY_INVALID'),
    ('before_journal_create', 'JOURNAL_OR_IDENTITY_INVALID'),
    ('after_journal_create', 'JOURNAL_OR_IDENTITY_INVALID'),
    ('before_journal_fsync', 'JOURNAL_OR_IDENTITY_INVALID'),
    ('after_journal_fsync', 'JOURNAL_OR_IDENTITY_INVALID'),
    ('before_root_fsync', 'JOURNAL_OR_IDENTITY_INVALID'),
    ('after_root_fsync', 'JOURNAL_OR_IDENTITY_INVALID'),
    ('before_init_write', 'JOURNAL_OR_IDENTITY_INVALID'),
    ('after_init_write', 'VALID'),
    ('after_init_fsync', 'VALID'),
])
def test_initialization_process_interruption_matrix(tmp_path, stage, expected):
    root = root_at(tmp_path)
    child = multiprocessing.get_context('fork').Process(
        target=_crash_initialization_boundary, args=(root, stage))
    child.start()
    try:
        child.join(5)
        assert not child.is_alive() and child.exitcode == 29
    finally:
        if child.is_alive():
            child.terminate(); child.join(5)
    descriptor = root / 'store-v1.json'
    if descriptor.exists():
        PINNED[root] = hashlib.sha256(descriptor.read_bytes()).hexdigest()
    with open_store(root) as reopened:
        assert reopened.report.classification == expected


def _crash_seal_boundary(root, stage):
    with open_store(root) as store:
        real_write, real_fsync, real_open = os.write, os.fsync, os.open
        real_link, real_unlink = os.link, os.unlink
        journal_syncs = 0
        directory_syncs = 0
        def boundary(matched, action):
            if matched and stage == 'before_' + action:
                os._exit(31)
            return matched
        def after(matched, action):
            if matched and stage == 'after_' + action:
                os._exit(31)
        def path(fd):
            return os.readlink(f'/proc/self/fd/{fd}')
        def writing(fd, raw):
            name = path(fd)
            payload = bytes(raw)
            event = ('prepare_write' if b'"event":"PREPARE"' in payload else
                     'commit_write' if b'"event":"COMMIT"' in payload else
                     'temp_write' if '/objects/.tmp-' in name else '')
            matched = bool(event)
            boundary(matched, event)
            count = real_write(fd, raw)
            after(matched, event)
            return count
        def syncing(fd):
            nonlocal journal_syncs, directory_syncs
            name = path(fd)
            action = ''
            if name.endswith('/seals-v1.jsonl'):
                journal_syncs += 1
                action = 'prepare_fsync' if journal_syncs == 1 else 'commit_fsync'
            elif name == str(root / 'objects'):
                directory_syncs += 1
                action = ('link_dir_fsync' if directory_syncs == 1 else
                          'unlink_dir_fsync')
            elif '/objects/.tmp-' in name:
                action = 'temp_fsync'
            boundary(bool(action), action)
            result = real_fsync(fd)
            after(bool(action), action)
            return result
        def opening(name, flags, *args, **kwargs):
            matched = isinstance(name, str) and name.startswith('.tmp-') and bool(flags & os.O_CREAT)
            boundary(matched, 'temp_create')
            fd = real_open(name, flags, *args, **kwargs)
            after(matched, 'temp_create')
            return fd
        def linking(*args, **kwargs):
            boundary(True, 'link')
            result = real_link(*args, **kwargs)
            after(True, 'link')
            return result
        def unlinking(*args, **kwargs):
            boundary(True, 'unlink')
            result = real_unlink(*args, **kwargs)
            after(True, 'unlink')
            return result
        def recorder():
            boundary(True, 'recorder')
            result = sample('durable_seal', 103, 4)
            after(True, 'recorder')
            return result
        os.write, os.fsync, os.open = writing, syncing, opening
        os.link, os.unlink = linking, unlinking
        seal(store, recorder=recorder)
        if stage == 'after_caller_return':
            os._exit(31)


@pytest.mark.parametrize('stage,expected', [
    ('before_prepare_write', 'VALID'),
    ('after_prepare_write', 'UNRESOLVED_PREPARE'),
    ('before_prepare_fsync', 'UNRESOLVED_PREPARE'),
    ('after_prepare_fsync', 'UNRESOLVED_PREPARE'),
    ('before_temp_create', 'UNRESOLVED_PREPARE'),
    ('after_temp_create', 'UNRESOLVED_PREPARE'),
    ('before_temp_write', 'UNRESOLVED_PREPARE'),
    ('after_temp_write', 'UNRESOLVED_PREPARE'),
    ('before_temp_fsync', 'UNRESOLVED_PREPARE'),
    ('after_temp_fsync', 'UNRESOLVED_PREPARE'),
    ('before_link', 'UNRESOLVED_PREPARE'),
    ('after_link', 'UNRESOLVED_PREPARE'),
    ('before_link_dir_fsync', 'UNRESOLVED_PREPARE'),
    ('after_link_dir_fsync', 'UNRESOLVED_PREPARE'),
    ('before_unlink', 'UNRESOLVED_PREPARE'),
    ('after_unlink', 'UNRESOLVED_PREPARE'),
    ('before_unlink_dir_fsync', 'UNRESOLVED_PREPARE'),
    ('after_unlink_dir_fsync', 'UNRESOLVED_PREPARE'),
    ('before_recorder', 'UNRESOLVED_PREPARE'),
    ('after_recorder', 'UNRESOLVED_PREPARE'),
    ('before_commit_write', 'UNRESOLVED_PREPARE'),
    ('after_commit_write', 'VALID'),
    ('before_commit_fsync', 'VALID'),
    ('after_commit_fsync', 'VALID'),
    ('after_caller_return', 'VALID'),
])
def test_seal_process_interruption_matrix(tmp_path, stage, expected):
    root = root_at(tmp_path)
    with open_store(root):
        pass
    child = multiprocessing.get_context('fork').Process(
        target=_crash_seal_boundary, args=(root, stage))
    child.start()
    try:
        child.join(5)
        assert not child.is_alive() and child.exitcode == 31
    finally:
        if child.is_alive():
            child.terminate(); child.join(5)
    with open_store(root) as reopened:
        assert reopened.report.classification == expected
        if expected == 'VALID' and reopened.receipts:
            assert next(iter(reopened.receipts.values())).acknowledgement == 'UNKNOWN'


def _crash_recovery_boundary(root, stage):
    real_write, real_fsync = os.write, os.fsync
    journal_syncs = 0
    def boundary(action, before):
        if stage == ('before_' if before else 'after_') + action:
            os._exit(37)
    def writing(fd, raw):
        action = 'recovery_write' if b'"event":"RECOVERY_VALIDATED"' in bytes(raw) else ''
        if action:
            boundary(action, True)
        result = real_write(fd, raw)
        if action:
            boundary(action, False)
        return result
    def syncing(fd):
        nonlocal journal_syncs
        name = os.readlink(f'/proc/self/fd/{fd}')
        if name.endswith('/seals-v1.jsonl'):
            journal_syncs += 1
            action = ('journal_barrier' if journal_syncs == 1 else
                      'recovery_fsync')
        elif name == str(root / 'objects'):
            action = 'objects_dir_barrier'
        elif name == str(root):
            action = 'root_barrier'
        elif name.startswith(str(root / 'objects') + '/'):
            action = 'object_barrier'
        else:
            action = ''
        if action:
            boundary(action, True)
        result = real_fsync(fd)
        if action:
            boundary(action, False)
        return result
    os.write, os.fsync = writing, syncing
    open_store(root)


@pytest.mark.parametrize('stage', [
    'before_object_barrier', 'after_object_barrier',
    'before_journal_barrier', 'after_journal_barrier',
    'before_objects_dir_barrier', 'after_objects_dir_barrier',
    'before_root_barrier', 'after_root_barrier',
    'before_recovery_write', 'after_recovery_write',
    'before_recovery_fsync', 'after_recovery_fsync',
])
def test_recovery_process_interruption_matrix(tmp_path, stage):
    root = root_at(tmp_path)
    with open_store(root) as store:
        original = seal(store)
    child = multiprocessing.get_context('fork').Process(
        target=_crash_recovery_boundary, args=(root, stage))
    child.start()
    try:
        child.join(5)
        assert not child.is_alive() and child.exitcode == 37
    finally:
        if child.is_alive():
            child.terminate(); child.join(5)
    with open_store(root) as recovered:
        assert recovered.report.classification == 'VALID'
        receipt = recovered.receipts[original.object_sha256]
        assert receipt.clocks == original.clocks
        assert receipt.acknowledgement == 'UNKNOWN'
        assert recovered.read_receipt(receipt) == b'fixture bytes'


@pytest.mark.parametrize('survivor,expected', [
    ('init_only', 'VALID'),
    ('init_with_object', 'NAMESPACE_CONFLICT'),
    ('torn_prepare', 'JOURNAL_OR_IDENTITY_INVALID'),
    ('torn_commit', 'JOURNAL_OR_IDENTITY_INVALID'),
    ('complete_commit_without_object', 'NAMESPACE_CONFLICT'),
    ('complete_commit_partial_object', 'NAMESPACE_CONFLICT'),
    ('torn_recovery', 'JOURNAL_OR_IDENTITY_INVALID'),
])
def test_unsynced_survivor_model_extremes(tmp_path, survivor, expected):
    # Deterministic byte/name survivor states only. No physical power-loss claim.
    root = root_at(tmp_path)
    with open_store(root) as store:
        receipt = seal(store)
    if survivor == 'torn_recovery':
        with open_store(root) as recovered:
            assert recovered.report.classification == 'VALID'
    journal = root / 'seals-v1.jsonl'
    lines = journal.read_bytes().splitlines(keepends=True)
    if survivor.startswith('init_'):
        journal.write_bytes(lines[0])
    elif survivor == 'torn_prepare':
        journal.write_bytes(lines[0] + lines[1][:len(lines[1]) // 2])
    elif survivor == 'torn_commit':
        journal.write_bytes(lines[0] + lines[1] +
                            lines[2][:len(lines[2]) // 2])
    elif survivor == 'torn_recovery':
        journal.write_bytes(b''.join(lines[:-1]) +
                            lines[-1][:len(lines[-1]) // 2])
    if survivor in ('init_only', 'complete_commit_without_object'):
        (root / 'objects' / receipt.object_sha256).unlink()
    elif survivor == 'complete_commit_partial_object':
        (root / 'objects' / receipt.object_sha256).write_bytes(b'partial')
    original_names = set(p.name for p in (root / 'objects').iterdir())
    original_journal = journal.read_bytes()
    with open_store(root) as reopened:
        assert reopened.report.classification == expected
    assert set(p.name for p in (root / 'objects').iterdir()) == original_names
    assert journal.read_bytes().startswith(original_journal)


@pytest.mark.parametrize('surviving_bytes', (b'', b'partial', b'fixture bytes'))
def test_unsynced_temporary_bytes_do_not_resolve_prepare(tmp_path, surviving_bytes):
    root = root_at(tmp_path)
    with open_store(root) as store:
        receipt = seal(store)
    journal = root / 'seals-v1.jsonl'
    lines = journal.read_bytes().splitlines(keepends=True)
    import json
    temporary = json.loads(lines[1])['data']['temporary']
    journal.write_bytes(b''.join(lines[:2]))
    (root / 'objects' / receipt.object_sha256).unlink()
    (root / 'objects' / temporary).write_bytes(surviving_bytes)
    with open_store(root) as held:
        assert held.report.classification == 'UNRESOLVED_PREPARE'
    assert (root / 'objects' / temporary).read_bytes() == surviving_bytes


@pytest.mark.parametrize('operation', ['read', 'seal'])
def test_repaired_r6_inherited_call_rejects_before_blocking_on_mutex(tmp_path, operation):
    # A prior defect let a forked child block forever trying to acquire a mutex
    # copied mid-hold from a parent thread that does not exist in the child.
    root = root_at(tmp_path)
    store = open_store(root)
    entered, release = threading.Event(), threading.Event()
    errors = []

    def recorder():
        entered.set()
        assert release.wait(5)
        return sample('durable_seal', 103, 4)

    def writer():
        try:
            seal(store, recorder=recorder)
        except BaseException as exc:
            errors.append(exc)

    writer_thread = threading.Thread(target=writer)
    writer_thread.start()
    try:
        assert entered.wait(5)
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            os.close(r)
            try:
                if operation == 'read':
                    store.read_receipt(None)
                else:
                    seal(store, raw=b'inherited')
            except LaunchContractError:
                os.write(w, b'R')
            else:
                os.write(w, b'X')
            os._exit(0)
        os.close(w)
        reaped = False
        try:
            assert select.select([r], [], [], 2)[0]
            assert os.read(r, 1) == b'R'
            assert os.waitpid(pid, 0) == (pid, 0)
            reaped = True
        finally:
            os.close(r)
            if not reaped:
                # Bounded cleanup: a failed assertion above must not leave a
                # blocked/orphaned child if the prompt-rejection regressed.
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                os.waitpid(pid, 0)
    finally:
        release.set()
        writer_thread.join(5)
        store.close()
    assert not writer_thread.is_alive() and not errors
    with open_store(root) as reopened:
        assert reopened.report.classification == 'VALID'


@pytest.mark.parametrize('extra', [-1, 0, 1])
def test_repaired_r7_descriptor_capacity_boundary_before_init_and_on_recovery(
        tmp_path, monkeypatch, extra):
    root = root_at(tmp_path)
    with open_store(root) as store:
        natural_size = (root / 'store-v1.json').stat().st_size
    (root / 'store-v1.json').unlink()
    (root / 'seals-v1.jsonl').unlink()
    with monkeypatch.context() as patch:
        patch.setattr(store_v1, 'MAX_DESCRIPTOR', natural_size + extra)
        if extra < 0:
            with pytest.raises(LaunchContractError, match='STORE_DESCRIPTOR_CAPACITY'):
                open_store(root, expected_descriptor_sha256=None)
            assert sorted(p.name for p in root.iterdir()) == ['objects']
        else:
            with open_store(root, expected_descriptor_sha256=None) as reinitialized:
                assert reinitialized.report.classification == 'VALID'
                pin = reinitialized.descriptor_sha256
                receipt = seal(reinitialized)
            with open_store(root, expected_descriptor_sha256=pin) as reopened:
                assert reopened.report.classification == 'VALID'
                assert reopened.read_receipt(
                    reopened.receipts[receipt.object_sha256]) == b'fixture bytes'


def test_repaired_r7_unicode_descriptor_expansion_rejected_before_init(tmp_path):
    root = root_at(tmp_path)
    label = '\U0001f680' * 128
    with pytest.raises(LaunchContractError, match='STORE_DESCRIPTOR_CAPACITY'):
        open_store(root, build_id=label, clock_method=label, host_id=label)
    assert sorted(p.name for p in root.iterdir()) == ['objects']
    with open_store(root) as fresh:
        assert fresh.report.classification == 'VALID'
