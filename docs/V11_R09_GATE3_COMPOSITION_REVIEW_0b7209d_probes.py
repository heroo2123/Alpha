"""Independent synthetic composition probes; run with candidate on PYTHONPATH.

No operational admission helper, network, actual provider data, or clock authority.
"""
import hashlib
import importlib.util
from pathlib import Path

import pytest
from tools import v11_r09_gate3_offline_io as io
from tools.v11_r09_gate3_launch import DurableBudget, LaunchContractError


def root_at(tmp_path):
    root = tmp_path / 'store'
    root.mkdir(mode=0o700)
    (root / 'objects').mkdir(mode=0o700)
    return root


def context():
    return dict(manifest_sha256='a'*64, policy_sha256='b'*64,
                build_id='independent-composition', clock_method='synthetic',
                max_clock_age_seconds=10, host_id='fixture-host', boot_id='boot-a')


def sample(phase, utc):
    raw = f'independent synthetic {phase} {utc}'.encode()
    return io.ClockEvidence(phase, io.MeasuredClock(utc, utc-99, .1, utc-99,
        'boot-a', hashlib.sha256(raw).hexdigest()), raw, 'synthetic')


def seal(store, data=b'raw', *, start=100, dependencies=()):
    provenance = io.ObjectProvenance('FEATURE_MANIFEST' if dependencies else 'RAW',
        data.hex(), 'synthetic-attempt', 'c'*64, 'd'*64, 'e'*64, dependencies)
    prefix = tuple(sample(p, start+i) for i, p in enumerate(
        ('request_start', 'body_receipt', 'decode_complete')))
    return store.seal_with_provenance(data, provenance, prefix,
        lambda: sample('durable_seal', start+3))


def state(budget):
    return (budget.received, budget.reserved, budget.count, budget.in_flight,
            budget.violated, budget.window_started, budget.last_started,
            {key: dict(value) for key, value in budget.attempts.items()})


@pytest.mark.parametrize('store_held', [False, True])
def test_replayed_window_interval_and_identity_are_unchanged(tmp_path, store_held):
    journal = tmp_path / 'budget'
    journal.mkdir(mode=0o700)
    root = root_at(tmp_path)
    limits = dict(max_bytes=16, max_requests=3, max_elapsed_seconds=10,
                  min_start_interval_seconds=2, boot_id='boot-a')
    with DurableBudget(journal, 'a'*64, **limits) as budget:
        with io.VersionedImmutableObjectStore(root, **context()) as store:
            budget.reserve('first', 8, started_monotonic=10)
            budget.consume('first', b'ABCD')
            budget.complete('first')
            original = state(budget)
            pin = store.descriptor_sha256
            if store_held:
                # Real PREPARE persists, but the recorder refuses its synthetic sample.
                provenance = io.ObjectProvenance('RAW', 'held', 'attempt', 'c'*64,
                                                 'd'*64, 'e'*64)
                prefix = tuple(sample(p, 100+i) for i, p in enumerate(
                    ('request_start', 'body_receipt', 'decode_complete')))
                def refuse():
                    raise ValueError('synthetic recorder unavailable')
                with pytest.raises(ValueError, match='recorder unavailable'):
                    store.seal_with_provenance(b'held bytes', provenance, prefix, refuse)
            else:
                receipt = seal(store)
    before = (journal / 'gate3.jsonl').read_bytes()
    for _ in range(2):
        with DurableBudget(journal, 'a'*64, **limits) as budget:
            with io.VersionedImmutableObjectStore(root, **context(),
                    expected_descriptor_sha256=pin) as store:
                assert store.report.classification == ('UNRESOLVED_PREPARE' if store_held else 'VALID')
                if not store_held:
                    got = store.receipts[receipt.object_sha256]
                    assert store.read_receipt(got) == b'raw'
                    assert got.acknowledgement == 'UNKNOWN'
                assert state(budget) == original
                for when in (11.999, 20, 21):
                    with pytest.raises(LaunchContractError, match='NOT_ATTEMPTED_BUDGET'):
                        budget.reserve('refused', 1, started_monotonic=when)
                assert state(budget) == original
                assert (journal / 'gate3.jsonl').read_bytes() == before
    for changes in ({'max_elapsed_seconds': 11}, {'min_start_interval_seconds': 3},
                    {'max_requests': 4}, {'max_bytes': 17}, {'boot_id': 'boot-b'}):
        with pytest.raises(LaunchContractError, match='JOURNAL_IDENTITY_MISMATCH'):
            DurableBudget(journal, 'a'*64, **(limits | changes))
        assert (journal / 'gate3.jsonl').read_bytes() == before
    with DurableBudget(journal, 'a'*64, **limits) as budget:
        budget.reserve('exact-interval', 1, started_monotonic=12)
        budget.consume('exact-interval', b'Z')
        budget.complete('exact-interval')
    with DurableBudget(journal, 'a'*64, **limits) as budget:
        assert budget.window_started == 10 and budget.last_started == 12
        assert budget.received == budget.reserved == 5
        assert budget.count == 2 and budget.in_flight is None
        with pytest.raises(LaunchContractError, match='NOT_ATTEMPTED_BUDGET'):
            budget.reserve('no-extended-window', 1, started_monotonic=20)


@pytest.mark.parametrize('violated', [False, True])
def test_incomplete_attempt_preserves_all_accounting_across_store_recovery(tmp_path, violated):
    journal = tmp_path / 'budget'
    journal.mkdir(mode=0o700)
    root = root_at(tmp_path)
    limits = dict(max_bytes=8, boot_id='boot-a')
    with DurableBudget(journal, 'a'*64, **limits) as budget:
        with io.VersionedImmutableObjectStore(root, **context()) as store:
            budget.reserve('same-request', 4, started_monotonic=1)
            budget.consume('same-request', b'AB')
            if violated:
                with pytest.raises(LaunchContractError, match='STREAM_ABORT_AT_ALLOWANCE'):
                    budget.consume('same-request', b'123')
            receipt = seal(store)
            pin = store.descriptor_sha256
            head = (3, store.report.journal_head)
            original = state(budget)
    before = (journal / 'gate3.jsonl').read_bytes()
    for boot in ('boot-a', 'boot-b', 'boot-a'):
        with io.VersionedImmutableObjectStore(root, **(context() | {'boot_id': boot}),
                expected_descriptor_sha256=pin, expected_head=head) as store:
            got = store.receipts[receipt.object_sha256]
            assert got.clocks == receipt.clocks
            assert store.read_receipt(got) == b'raw'
            assert got.acknowledgement == 'UNKNOWN' and not got.historical_feature_eligible
        with DurableBudget(journal, 'a'*64, **limits) as budget:
            assert state(budget) == original
            assert budget.received == (5 if violated else 2) and budget.reserved == 4
            with pytest.raises(LaunchContractError, match=(
                    'STREAM_VIOLATION_HELD' if violated else 'UNCERTAIN_REQUEST_HELD')):
                budget.reserve('retry', 1, started_monotonic=99)
        assert (journal / 'gate3.jsonl').read_bytes() == before


@pytest.mark.parametrize('cutoff, expected', [(103.1, (True, False, False)),
    (106.1, (True, True, False)), (109.099, (True, True, False)),
    (109.1, (True, True, True))])
def test_original_dependency_and_manifest_clocks_have_distinct_cutoffs(tmp_path, cutoff, expected):
    root = root_at(tmp_path)
    with io.VersionedImmutableObjectStore(root, **context()) as store:
        first = seal(store, b'raw-1', start=100)
        second = seal(store, b'raw-2', start=103)
        manifest = seal(store, b'feature', start=106,
                        dependencies=(first.commit_hash, second.commit_hash))
        original = (first, second, manifest)
        pin, head = store.descriptor_sha256, (7, store.report.journal_head)
    for _ in range(2):
        with io.VersionedImmutableObjectStore(root, **context(),
                expected_descriptor_sha256=pin, expected_head=head) as recovered:
            results = []
            for old in original:
                got = recovered.receipts[old.object_sha256]
                assert got.clocks == old.clocks and got.provenance == old.provenance
                assert got.acknowledgement == 'UNKNOWN' and not got.historical_feature_eligible
                seq = io.ClockSequence(boot_id='boot-a', max_measurement_age_seconds=10)
                for clock in got.clocks:
                    seq.record(clock.phase, clock.reading)
                results.append(seq.causal_before(cutoff))
            assert tuple(results) == expected
            # This conjunction is only a test oracle for necessary timing conditions.
            # Even all True cannot confer feature eligibility or historical runtime use.
            assert all(results) == (cutoff == 109.1)


def test_author_composition_closes_store_before_budget(tmp_path, monkeypatch):
    candidate = Path(io.__file__).resolve().parents[1]
    path = candidate / 'tests/test_v11_r09_gate3_restart_composition.py'
    spec = importlib.util.spec_from_file_location('author_composition', path)
    author = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(author)
    events = []
    for cls, name in ((DurableBudget, 'budget'), (io.VersionedImmutableObjectStore, 'store')):
        original_init, original_close = cls.__init__, cls.close
        def init(self, *args, _fn=original_init, _name=name, **kwargs):
            _fn(self, *args, **kwargs)
            events.append('open-' + _name)
        def close(self, _fn=original_close, _name=name):
            events.append('close-' + _name)
            return _fn(self)
        monkeypatch.setattr(cls, '__init__', init)
        monkeypatch.setattr(cls, 'close', close)
    with author._acquire(author._journal(tmp_path), 'a'*64, author._root(tmp_path), max_bytes=8):
        assert events == ['open-budget', 'open-store']
    assert events == ['open-budget', 'open-store', 'close-store', 'close-budget']
