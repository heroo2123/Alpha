"""Disposable synthetic v1 restart fixtures; no provider or real store access."""
import hashlib
import multiprocessing
import os

import pytest

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
            def interrupted(self, event, data):
                result = real(self, event, data)
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
