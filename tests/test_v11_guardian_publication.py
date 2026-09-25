"""Coherent health publication races against real guardian decision writes.

Health sampling, source freshness, runtime admission, and event risk remain real
code paths. The shared account rig explicitly supplies synthetic strategy
admission/economics; these tests make no empirical or financial claims.
"""
from copy import deepcopy

import pytest

from polymarket_scanner.v11 import guardian_lease, paper_guardian, runtime_health
from polymarket_scanner.v11.evidence import EvidenceError
from test_v11_paper_coordinator import rig, coordinator, proposal
from test_v11_paper_guardian import worker, state
from test_v11_runtime_health import monitor, ready


@pytest.fixture
def coherent(rig, worker, monkeypatch):
    return make_coherent(rig, worker, monkeypatch)


def make_coherent(rig, worker, monkeypatch, *, policy=None):
    account = coordinator(rig)
    account.coordinate('reserve', (proposal(rig, units='2'),))
    health = monitor(rig, monkeypatch, policy=policy)
    # One explicit synthetic clock keeps current source/event/intent pins valid.
    # _health itself and its process-identity checks are never replaced.
    monkeypatch.setattr(paper_guardian, 'host_stamp', runtime_health.host_stamp)
    monkeypatch.setattr(guardian_lease, 'host_stamp', runtime_health.host_stamp)
    guardian = paper_guardian.PaperGuardian(account,
        policy=guardian_lease.GuardianPolicy('synthetic-publication-race'),
        health_config=health.config, worker=guardian_lease.process_identity(worker.pid))
    rig['boot'][0] = guardian.process['boot_id']
    ready(rig, health)
    guardian._health()
    return rig, health, guardian


def publish(health, key):
    return health.publish(key, heartbeat_key=key + ':heartbeat', worker='worker', generation='one')


def partial_heartbeat(health, guardian):
    health.heartbeat('partial-heartbeat', worker='worker', generation='one')
    with pytest.raises(EvidenceError, match='HEARTBEAT_CHANGED'):
        guardian._health()


def guardian_rows(guardian):
    return guardian.store.records(kind='RUNTIME_STATUS', event_id=guardian.key)


def cancel_requests(guardian):
    return [row for row in guardian.store.records(kind='COORDINATOR_EVENT', event_id='v11-paper-account-state')
            if row['body']['details'].get('request', {}).get('status') == 'CANCEL_REQUESTED']


def test_healthy_publication_before_trigger_commit_retries_without_durable_cancel(coherent, monkeypatch):
    _, health, guardian = coherent
    partial_heartbeat(health, guardian)
    before = guardian.coordinator._head()
    audit = guardian.store.safety_audit
    raced = []

    def publish_at_trigger(key, **kwargs):
        if kwargs['event_id'] == guardian.key and kwargs['details'].get('pending_trigger') and not raced:
            raced.append(key)
            publish(health, 'atomic-recovery')
        return audit(key, **kwargs)

    monkeypatch.setattr(guardian.store, 'safety_audit', publish_at_trigger)
    result = guardian._cycle()
    assert len(raced) == 1
    assert result['body']['details']['status'] == 'READY'
    assert guardian.coordinator._head() == before
    assert not cancel_requests(guardian)
    assert all(row['body']['details']['pending_trigger'] is None for row in guardian_rows(guardian))


def test_unhealthy_publication_before_ready_commit_cannot_publish_lease(coherent, monkeypatch):
    fixture, health, guardian = coherent
    cash = guardian.coordinator.snapshot()['reserved_cash']
    audit = guardian.store.safety_audit
    raced = []

    def fail_at_ready(key, **kwargs):
        if kwargs['event_id'] == guardian.key and kwargs['details']['status'] == 'READY' and not raced:
            raced.append(key)
            fixture['sync'][0] = False
            publish(health, 'atomic-sync-failure')
        return audit(key, **kwargs)

    monkeypatch.setattr(guardian.store, 'safety_audit', fail_at_ready)
    result = guardian._cycle()
    assert len(raced) == 1
    assert result['body']['details']['status'] == 'GATED'
    assert all(row['body']['details']['status'] != 'READY' for row in guardian_rows(guardian))
    assert state(guardian.coordinator)['intents']['proposal']['cancel_requested'] is True
    assert len(cancel_requests(guardian)) == 1
    assert guardian.coordinator.snapshot()['reserved_cash'] == cash


def test_repeated_publication_contention_gates_without_inventing_cancel(coherent, monkeypatch):
    _, health, guardian = coherent
    partial_heartbeat(health, guardian)
    before = guardian.coordinator._head()
    audit = guardian.store.safety_audit
    races = []

    def race_every_decision(key, **kwargs):
        details = kwargs['details']
        if kwargs['event_id'] == guardian.key and (details.get('pending_trigger') or details['status'] == 'READY'):
            races.append(key)
            publish(health, 'atomic-race-' + str(len(races)))
        return audit(key, **kwargs)

    monkeypatch.setattr(guardian.store, 'safety_audit', race_every_decision)
    result = guardian._cycle()['body']['details']
    assert len(races) == 2
    assert result['status'] == 'GATED'
    assert result['reasons'] == ['GUARDIAN_HEALTH_PUBLICATION_CHANGED']
    assert result['pending_trigger'] is None
    assert guardian.coordinator._head() == before
    assert not cancel_requests(guardian)
    assert all(row['body']['details']['pending_trigger'] is None for row in guardian_rows(guardian))


def test_committed_trigger_resumes_after_atomic_health_recovery(coherent, monkeypatch):
    _, health, guardian = coherent
    partial_heartbeat(health, guardian)
    cash = guardian.coordinator.snapshot()['reserved_cash']

    def crash_after_trigger(*args, **kwargs):
        raise RuntimeError('synthetic interruption after durable trigger')

    monkeypatch.setattr(guardian.cancellation, 'plan', crash_after_trigger)
    with pytest.raises(RuntimeError, match='after durable trigger'):
        guardian._cycle()
    pending = guardian._head()['body']['details']['pending_trigger']
    assert pending and not cancel_requests(guardian)
    publish(health, 'atomic-recovery-after-trigger')
    replacement = paper_guardian.PaperGuardian(guardian.coordinator, policy=guardian.policy,
        health_config=health.config, worker=guardian.worker)
    replacement._health()  # The new publication is healthy, yet cannot revoke the trigger.
    result = replacement._cycle()['body']['details']
    assert state(replacement.coordinator)['intents']['proposal']['cancel_requested'] is True
    assert len(cancel_requests(replacement)) == 1
    assert result['status'] == 'GATED' and result['pending_trigger'] is None
    assert replacement.store.get(pending)['body']['details']['pending_trigger'] == pending
    assert replacement.coordinator.snapshot()['reserved_cash'] == cash


@pytest.mark.parametrize('failure', ['health_expiry', 'worker_stopped'])
def test_freshness_failure_after_ready_write_lock_never_publishes_lease(coherent, monkeypatch, failure):
    fixture, _, guardian = coherent
    cash = guardian.coordinator.snapshot()['reserved_cash']
    audit, budget = guardian.store.safety_audit, guardian.store._budget
    process_identity = paper_guardian.process_identity
    armed, tripped = [False], []

    def observe_process(pid):
        # A deterministic /proc stopped-process result; the existing sibling
        # tests exercise actual SIGSTOP without this observation oracle.
        if failure == 'worker_stopped' and tripped and pid == guardian.worker['pid']:
            raise EvidenceError('GUARDIAN_PROCESS_NOT_RUNNING')
        return process_identity(pid)

    def mark_ready_write(key, **kwargs):
        armed[0] = kwargs['event_id'] == guardian.key and kwargs['details'].get('status') == 'READY'
        try:
            return audit(key, **kwargs)
        finally:
            armed[0] = False

    def change_after_write_lock(db, size):
        budget(db, size)
        if armed[0] and not tripped:
            assert db.in_transaction
            tripped.append(failure)
            if failure == 'health_expiry':
                fixture['now'][0] += 5.1
                fixture['mono'][0] += 5.1

    monkeypatch.setattr(paper_guardian, 'process_identity', observe_process)
    monkeypatch.setattr(guardian.store, 'safety_audit', mark_ready_write)
    monkeypatch.setattr(guardian.store, '_budget', change_after_write_lock)
    result = guardian._cycle()['body']['details']
    assert tripped == [failure]
    assert result['status'] == 'GATED'
    assert all(row['body']['details']['status'] != 'READY' for row in guardian_rows(guardian))
    assert state(guardian.coordinator)['intents']['proposal']['cancel_requested'] is True
    assert len(cancel_requests(guardian)) == 1
    assert guardian.coordinator.snapshot()['reserved_cash'] == cash


@pytest.mark.parametrize('malformed', ['missing_global_reasons', 'stamp_missing_wall', 'nonnumeric_time',
    'boolean_time', 'none_time', 'malformed_policy', 'heartbeat_missing_stamp_field',
    'sources_none', 'source_missing_record_id', 'policy_changed_without_config'])
def test_valid_hash_malformed_health_still_delivers_cancel_only(coherent, malformed):
    _, _, guardian = coherent
    store = guardian.store
    original = store.latest(kind='RUNTIME_STATUS', event_id=runtime_health.KEY)
    details = deepcopy(original['body']['details'])
    references = [entry['id'] for entry in original['body']['evidence']]
    cash = guardian.coordinator.snapshot()['reserved_cash']
    expected = deepcopy(state(guardian.coordinator))
    expected['intents']['proposal'].update(status='CANCEL_REQUESTED', cancel_requested=True)

    if malformed == 'missing_global_reasons':
        del details['global_reasons']
    elif malformed == 'stamp_missing_wall':
        del details['stamp']['wall']
    elif malformed in {'nonnumeric_time', 'boolean_time', 'none_time'}:
        # String "NaN" is valid JSON but not a finite numeric timestamp; an
        # actual floating NaN is correctly refused by canonical() before write.
        details['stamp']['monotonic'] = {'nonnumeric_time': 'NaN', 'boolean_time': True, 'none_time': None}[malformed]
    elif malformed == 'malformed_policy':
        details['policy']['maximum_wall_step_seconds'] = 'not-a-number'
    elif malformed == 'sources_none':
        details['sources'] = None
    elif malformed == 'source_missing_record_id':
        del details['sources'][0]['record_id']
    elif malformed == 'policy_changed_without_config':
        details['policy']['maximum_sample_age_seconds'] = 10.0
    else:
        old_heartbeat = details['workers'][0]['record_id']
        heartbeat = store.get(old_heartbeat)
        heartbeat_details = deepcopy(heartbeat['body']['details'])
        del heartbeat_details['stamp']['monotonic']
        broken = store.safety_audit('malformed-heartbeat', kind='RUNTIME_STATUS',
            event_id=heartbeat['event_id'], details=heartbeat_details)
        # Pin the actual malformed heartbeat, avoiding an easier changed-ID gate.
        details['workers'][0]['record_id'] = broken['id']
        references = [broken['id'] if key == old_heartbeat else key for key in references]

    invalid = store.safety_audit('malformed-health', kind='RUNTIME_STATUS', event_id=runtime_health.KEY,
        details=details, evidence_ids=tuple(references))
    assert store.get(invalid['id']) == invalid  # Integrity-valid, semantically malformed.
    result = guardian._cycle()['body']['details']
    assert result['status'] == 'GATED'
    assert all(row['body']['details']['status'] != 'READY' for row in guardian_rows(guardian))
    assert state(guardian.coordinator) == expected
    assert len(cancel_requests(guardian)) == 1
    assert guardian.coordinator.snapshot()['reserved_cash'] == cash


def test_finite_extended_sample_deadlines_cannot_outlive_bound_policy(rig, worker, monkeypatch):
    policy = runtime_health.HealthPolicy('fixture', 5.0, 30.0, .25, 2, .5, ('worker',))
    fixture, health, guardian = make_coherent(rig, worker, monkeypatch, policy=policy)
    original = guardian.store.latest(kind='RUNTIME_STATUS', event_id=runtime_health.KEY)
    details = deepcopy(original['body']['details'])
    heartbeat = guardian.store.get(details['workers'][0]['record_id'])['body']['details']
    fixture['now'][0] += 6.0
    fixture['mono'][0] += 6.0
    assert fixture['now'][0] >= original['body']['details']['valid_until']
    assert 0 <= fixture['now'][0] - heartbeat['stamp']['wall'] < policy.heartbeat_age_seconds
    assert 0 <= fixture['mono'][0] - heartbeat['stamp']['monotonic'] < policy.heartbeat_age_seconds
    details['valid_until'] = details['stamp']['wall'] + 60.0
    details['monotonic_valid_until'] = details['stamp']['monotonic'] + 60.0
    assert details['config_sha256'] == health.config
    extended = guardian.store.safety_audit('extended-health-deadlines', kind='RUNTIME_STATUS',
        event_id=runtime_health.KEY, details=details,
        evidence_ids=tuple(entry['id'] for entry in original['body']['evidence']))
    assert guardian.store.get(extended['id']) == extended
    cash = guardian.coordinator.snapshot()['reserved_cash']
    expected = deepcopy(state(guardian.coordinator))
    expected['intents']['proposal'].update(status='CANCEL_REQUESTED', cancel_requested=True)
    result = guardian._cycle()['body']['details']
    assert result['status'] == 'GATED'
    assert all(row['body']['details']['status'] != 'READY' for row in guardian_rows(guardian))
    assert state(guardian.coordinator) == expected
    assert len(cancel_requests(guardian)) == 1
    assert guardian.coordinator.snapshot()['reserved_cash'] == cash
