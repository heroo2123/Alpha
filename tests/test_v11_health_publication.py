"""Atomic synthetic health publication and immutable, consistent observations."""
from contextlib import contextmanager
from copy import copy, deepcopy
from dataclasses import replace
import multiprocessing
import os
import threading

import pytest

from polymarket_scanner.v11 import runtime_health as health
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from test_v11_paper_coordinator import rig
from test_v11_runtime_health import monitor, advance, gate, ready


def publish(m, number, *, generation='one'):
    return m.publish('sample'+str(number), heartbeat_key='heartbeat'+str(number),
                     worker='worker', generation=generation)


def absent(store, *keys):
    for key in keys:
        with pytest.raises(EvidenceError, match='EVIDENCE_MISSING'): store.get(key)


def test_atomic_pair_records_exact_original_heartbeat_and_normal_recovery(rig, monkeypatch):
    m = monitor(rig, monkeypatch)
    first = publish(m, 1)
    assert first['body']['details']['global_reasons'] == ['CLOCK_RECOVERY_SAMPLES_PENDING']
    advance(rig)
    sample = publish(m, 2); heartbeat = rig['store'].get('heartbeat2')
    d = sample['body']['details']
    assert not d['global_reasons'] and d['stable_samples'] == 2
    assert d['request'] == dict(action='SAMPLE', heartbeat_key='heartbeat2', worker='worker', generation='one')
    assert d['workers'] == [dict(worker='worker', reason=None, record_id='heartbeat2')]
    assert dict(id=heartbeat['id'], sha256=heartbeat['sha256']) in sample['body']['evidence']
    view = health.read_health_snapshot(rig['store'])
    assert view['row'] == sample and view['workers'] == {'worker': heartbeat}
    assert view['error'] is None and view['heads'] == (
        ('RUNTIME_STATUS', health.KEY, sample['seq']),
        ('RUNTIME_STATUS', health._worker_key('worker'), heartbeat['seq']))
    assert gate(rig)


def test_completed_pair_replay_cannot_probe_renew_or_count_another_recovery_sample(rig, monkeypatch):
    m = monitor(rig, monkeypatch); sample = publish(m, 1)
    heartbeat = rig['store'].get('heartbeat1'); advance(rig, 20)
    m.sync_probe = lambda: pytest.fail('completed publication replay must not probe')
    assert publish(m, 1) == sample
    assert rig['store'].get('heartbeat1') == heartbeat
    assert sample['body']['details']['stable_samples'] == 1


@pytest.mark.parametrize('change', ['heartbeat_key', 'generation', 'key', 'config'])
def test_original_pair_identity_cannot_be_rebound(rig, monkeypatch, change):
    m = monitor(rig, monkeypatch); original = publish(m, 1)
    args = dict(heartbeat_key='heartbeat1', worker='worker', generation='one'); key = 'sample1'
    if change == 'heartbeat_key': args['heartbeat_key'] = 'different'
    if change == 'generation': args['generation'] = 'different'
    if change == 'key': key = 'different'
    if change == 'config': m.config = 'f'*64
    with pytest.raises(EvidenceError): m.publish(key, **args)
    assert rig['store'].get('sample1') == original
    absent(rig['store'], 'different')


@pytest.mark.parametrize('existing', ['heartbeat', 'sample', 'legacy_pair'])
def test_incomplete_or_legacy_pair_cannot_be_silently_finished_or_upgraded(rig, monkeypatch, existing):
    m = monitor(rig, monkeypatch)
    if existing in {'heartbeat', 'legacy_pair'}: m.heartbeat('heartbeat1', worker='worker', generation='one')
    if existing in {'sample', 'legacy_pair'}: m.sample('sample1')
    before = health.read_health_snapshot(rig['store'], workers=('worker',))
    with pytest.raises(EvidenceError, match='INCOMPLETE_PUBLICATION|REPLAY_CONFLICT'): publish(m, 1)
    assert health.read_health_snapshot(rig['store'], workers=('worker',)) == before


def test_distinct_keys_and_configured_worker_are_required_before_probe(rig, monkeypatch):
    m = monitor(rig, monkeypatch); m.sync_probe = lambda: pytest.fail('invalid publication must not probe')
    with pytest.raises(EvidenceError, match='PUBLICATION_KEYS'):
        m.publish('same', heartbeat_key='same', worker='worker', generation='one')
    with pytest.raises(EvidenceError, match='UNCONFIGURED_WORKER'):
        m.publish('sample', heartbeat_key='heartbeat', worker='other', generation='one')


def test_probe_runs_without_writer_lock_and_clock_stamp_follows_concurrent_append(rig, monkeypatch):
    m = monitor(rig, monkeypatch); original_probe = m.sync_probe
    other = EvidenceStore(rig['store'].path, 'V11_PAPER', clock=rig['store'].clock)
    def probe():
        with other._connect() as db:
            db.execute('BEGIN IMMEDIATE')  # Would fail if publication already held the write lock.
        advance(rig)
        other.audit('concurrent-append', kind='MEASUREMENT', event_id='synthetic', details={})
        return original_probe()
    m.sync_probe = probe
    sample = publish(m, 1)
    assert 'EVIDENCE_CLOCK_HIGH_WATER_AHEAD' not in sample['body']['details']['global_reasons']
    assert sample['body']['details']['stamp']['wall'] == rig['now'][0]


def test_standalone_sample_also_observes_clock_after_probe_and_archive_snapshot(rig, monkeypatch):
    m = monitor(rig, monkeypatch); original_probe = m.sync_probe
    m.heartbeat('hb', worker='worker', generation='one')
    def probe():
        advance(rig)
        rig['store'].audit('during-probe', kind='MEASUREMENT', event_id='synthetic', details={})
        return original_probe()
    m.sync_probe = probe
    d = m.sample('sample')['body']['details']
    assert 'EVIDENCE_CLOCK_HIGH_WATER_AHEAD' not in d['global_reasons']


@pytest.mark.parametrize('failure', ['record_limit', 'sample_validation'])
def test_failure_of_second_append_rolls_back_the_heartbeat_too(rig, monkeypatch, failure):
    m = monitor(rig, monkeypatch); store = rig['store']
    if failure == 'record_limit':
        with store._connect() as db: count = db.execute('SELECT COUNT(*) FROM v11_records').fetchone()[0]
        store.limits = replace(store.limits, max_records=count+1)
    else:
        original = EvidenceStore._validate_safety_append
        def reject(db, kind, event, body):
            if event == health.KEY: raise EvidenceError('SYNTHETIC_SECOND_APPEND_FAILURE')
            return original(db, kind, event, body)
        monkeypatch.setattr(EvidenceStore, '_validate_safety_append', staticmethod(reject))
    with pytest.raises(EvidenceError): publish(m, 1)
    absent(store, 'heartbeat1', 'sample1')
    assert health.read_health_snapshot(store)['row'] is None


@pytest.mark.skipif('fork' not in multiprocessing.get_all_start_methods(), reason='Linux process crash semantics')
def test_actual_child_death_after_heartbeat_insert_commits_neither_record(rig, monkeypatch):
    m = monitor(rig, monkeypatch); original = rig['store']._budget
    def writer():
        calls = [0]
        def budget(db, size):
            calls[0] += 1
            if calls[0] == 2: os._exit(23)
            original(db, size)
        rig['store']._budget = budget
        publish(m, 1)
    p = multiprocessing.get_context('fork').Process(target=writer)
    p.start()
    try:
        p.join(timeout=5)
        assert p.exitcode == 23
    finally:
        if p.is_alive(): p.kill(); p.join(timeout=2)
    absent(rig['store'], 'heartbeat1', 'sample1')
    assert publish(m, 1)['body']['details']['stable_samples'] == 1


def test_reader_never_observes_half_publication_while_writer_is_between_inserts(rig, monkeypatch):
    m = monitor(rig, monkeypatch); first = publish(m, 1); advance(rig)
    store = rig['store']; original = store._budget
    entered = threading.Event(); release = threading.Event(); calls = [0]; failures = []
    def budget(db, size):
        calls[0] += 1
        if calls[0] == 2:
            entered.set()
            if not release.wait(3): raise RuntimeError('synthetic reader did not finish')
        original(db, size)
    monkeypatch.setattr(store, '_budget', budget)
    def writer():
        try: publish(m, 2)
        except BaseException as exc: failures.append(exc)
    thread = threading.Thread(target=writer, daemon=True); thread.start()
    try:
        assert entered.wait(3)
        view = health.read_health_snapshot(store)
        assert view['row'] == first and view['workers']['worker']['id'] == 'heartbeat1'
        absent(store, 'heartbeat2', 'sample2')
    finally:
        release.set(); thread.join(timeout=3)
    assert not thread.is_alive() and not failures
    view = health.read_health_snapshot(store)
    assert view['row']['id'] == 'sample2' and view['workers']['worker']['id'] == 'heartbeat2'


def interleave_publication_after_sample_read(rig, m, monkeypatch):
    """A real second SQLite connection commits during the reader's transaction."""
    original = rig['store']._connect; raced = [False]
    writer = copy(m)
    writer.store = EvidenceStore(rig['store'].path, 'V11_PAPER', clock=rig['store'].clock)
    def trace(sql):
        if not raced[0] and "'v11-worker:" in sql and 'ORDER BY seq DESC LIMIT 1' in sql:
            raced[0] = True; advance(rig); publish(writer, 3)
    @contextmanager
    def connection():
        with original() as db:
            db.set_trace_callback(trace)
            try: yield db
            finally: db.set_trace_callback(None)
    monkeypatch.setattr(rig['store'], '_connect', connection)
    return raced


def test_sample_workers_and_high_water_share_one_sqlite_read_snapshot(rig, monkeypatch):
    m = monitor(rig, monkeypatch); publish(m, 1); advance(rig); old = publish(m, 2)
    old_high = rig['now'][0]
    raced = interleave_publication_after_sample_read(rig, m, monkeypatch)
    view = health.read_health_snapshot(rig['store'])
    assert raced[0] and view['row'] == old and view['workers']['worker']['id'] == 'heartbeat2'
    assert view['archive_high'] == old_high
    assert rig['store'].latest(kind='RUNTIME_STATUS', event_id=health.KEY)['id'] == 'sample3'


def test_admission_retains_original_health_and_worker_cas_during_publication_race(rig, monkeypatch):
    m = monitor(rig, monkeypatch); publish(m, 1); advance(rig); old = publish(m, 2)
    raced = interleave_publication_after_sample_read(rig, m, monkeypatch)
    heads = gate(rig)
    assert raced[0] and ('RUNTIME_STATUS', health.KEY, old['seq']) in heads
    with pytest.raises(EvidenceError, match='GUARDED_STATE_CHANGED'):
        rig['store'].audit('stale-check', kind='MEASUREMENT', event_id='synthetic', details={}, expected_heads=heads)
    absent(rig['store'], 'stale-check')


def test_standalone_heartbeat_still_requires_resampling(rig, monkeypatch):
    m = monitor(rig, monkeypatch); ready(rig, m)
    m.heartbeat('unpaired', worker='worker', generation='one')
    with pytest.raises(EvidenceError, match='HEARTBEAT_CHANGED_RESAMPLE'): gate(rig)


def test_missing_other_worker_is_not_fabricated_by_atomic_publication(rig, monkeypatch):
    p = health.HealthPolicy('fixture', 5., 5., .25, 2, .5, ('worker', 'other'))
    m = monitor(rig, monkeypatch, policy=p); publish(m, 1)
    view = health.read_health_snapshot(rig['store'])
    assert view['workers']['other'] is None
    assert ('RUNTIME_STATUS', health._worker_key('other'), 0) in view['heads']
    assert 'WORKER_HEARTBEAT_MISSING:other' in view['row']['body']['details']['global_reasons']


def test_malformed_health_preserves_head_and_bounded_workers_before_rejection(rig, monkeypatch):
    m = monitor(rig, monkeypatch); sample = publish(m, 1)
    d = deepcopy(sample['body']['details']); d['workers'] = []
    invalid = rig['store'].safety_audit('malformed', kind='RUNTIME_STATUS', event_id=health.KEY, details=d)
    view = health.read_health_snapshot(rig['store'])
    assert view['error'] == 'RUNTIME_HEALTH_WORKER_SCHEMA'
    assert view['row'] == invalid and view['heads'][0] == ('RUNTIME_STATUS', health.KEY, invalid['seq'])
    assert view['workers']['worker']['id'] == 'heartbeat1'
    with pytest.raises(EvidenceError, match='WORKER_SCHEMA'): gate(rig)


def test_empty_archive_snapshot_fences_health_absence(rig):
    view = health.read_health_snapshot(rig['store'])
    assert view['row'] is None and view['workers'] == {} and view['error'] is None
    assert view['heads'] == (('RUNTIME_STATUS', health.KEY, 0),)


@pytest.mark.parametrize('attempt', ['ordinary', 'wrong_safety', 'incomplete', 'nested'])
def test_restricted_publication_context_cannot_batch_other_writes(rig, monkeypatch, attempt):
    m = monitor(rig, monkeypatch); store = rig['store']
    with pytest.raises(EvidenceError):
        with store.runtime_health_publication() as bound:
            if attempt == 'ordinary': bound.audit('forbidden', kind='MEASUREMENT', event_id='synthetic', details={})
            elif attempt == 'wrong_safety':
                bound.safety_audit('forbidden', kind='RUNTIME_STATUS', event_id='synthetic', details={})
            elif attempt == 'nested':
                with bound.runtime_health_publication(): pass
            else:
                paired = copy(m); paired.store = bound
                paired.heartbeat('heartbeat1', worker='worker', generation='one')
    absent(store, 'forbidden', 'heartbeat1')


def test_caught_append_failure_poisoned_context_cannot_commit_a_partial_pair(rig, monkeypatch):
    m = monitor(rig, monkeypatch); store = rig['store']
    with pytest.raises(EvidenceError, match='PUBLICATION_INCOMPLETE'):
        with store.runtime_health_publication() as bound:
            paired = copy(m); paired.store = bound
            paired.heartbeat('heartbeat1', worker='worker', generation='one')
            with pytest.raises(EvidenceError):
                bound.safety_audit('forbidden', kind='RUNTIME_STATUS', event_id='synthetic', details={})
    absent(store, 'heartbeat1', 'forbidden')


def test_true_backward_clock_remains_unhealthy_and_raw_timestamps_are_preserved(rig, monkeypatch):
    m = monitor(rig, monkeypatch); publish(m, 1); advance(rig); publish(m, 2)
    rig['now'][0] -= 10
    sample = publish(m, 3)
    assert 'EVIDENCE_CLOCK_HIGH_WATER_AHEAD' in sample['body']['details']['global_reasons']
    assert sample['body']['recorded_at'] == rig['now'][0]
    assert rig['store'].get('heartbeat3')['body']['recorded_at'] == rig['now'][0]
    with pytest.raises(EvidenceError, match='CLOCK_OR_LIVENESS_GATED'): gate(rig)
