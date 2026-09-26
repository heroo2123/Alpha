"""Broker-owned liveness receipt mechanics; separate-principal IPC is separate.

Only the inherited synthetic strategy/account fixture supplies economic state.
The worker is a real child process; runtime health publication and replay use
the real archive paths with an explicit controlled clock and sync oracle.
"""
from copy import copy
from dataclasses import replace
import os

import pytest

from polymarket_scanner.v11 import candidate_liveness as liveness, runtime_health
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from polymarket_scanner.v11.liveness_protocol import ProducerPolicy, request, validate_response
from polymarket_scanner.v11.paper_guardian_broker import PaperGuardianBroker
from test_v11_guardian_broker import policy as synthetic_broker_policy
from test_v11_guardian_publication import make_coherent
from test_v11_paper_coordinator import rig
from test_v11_paper_guardian import worker
from test_v11_runtime_health import advance


@pytest.fixture
def engine(rig, worker, monkeypatch):
    _, health, guardian = make_coherent(rig, worker, monkeypatch)
    broker = PaperGuardianBroker(guardian, synthetic_broker_policy())
    policy = ProducerPolicy('synthetic-liveness', worker='worker', generation='one', candidate_gid=os.getgid())
    account, economics = broker.c._head(), broker.c.snapshot()
    yield liveness.CandidateLiveness(broker, policy, health)
    assert broker.c._head() == account and broker.c.snapshot() == economics


def semantic(engine, key='pulse'):
    value = request(engine.config, key, 'c' * 64)
    del value['challenge']  # Wire authentication has already produced the trusted receipt.
    return value


def receipt(engine):
    stamp = runtime_health.host_stamp(engine.store)
    return dict(stamp=dict(stamp), challenge_stamp=dict(stamp), challenge_sha256='c' * 64,
                peer=dict(engine.worker, gid=engine.policy.candidate_gid))


def absent(store, *keys):
    for key in keys:
        with pytest.raises(EvidenceError, match='EVIDENCE_MISSING'):
            store.get(key)


def rows(engine):
    return engine.store.records(kind='RUNTIME_STATUS', event_id=engine.key)


def no_probe():
    pytest.fail('replay/recovery must not create another observation or synchronization probe')


def test_pulse_preserves_received_stamp_and_publishes_current_sample_without_account_effects(engine, rig):
    account = engine.broker.c._head()
    economics = engine.broker.c.snapshot()
    q, observed = semantic(engine), receipt(engine)
    advance(rig, .2)
    response = engine.publish(q, observed)
    validate_response(response, q)
    assert response['outcome'] == 'PUBLISHED' and response['financial_authority'] is False
    ids = liveness.record_ids(engine.config, q['request_id'])
    accepted, heartbeat, sample = [engine.store.get(ids[name]) for name in ('accepted', 'heartbeat', 'health')]
    for name, row in (('accepted', accepted), ('heartbeat', heartbeat), ('health', sample)):
        assert response['result'][name + '_id'] == row['id']
        assert response['result'][name + '_sha256'] == row['sha256']
    assert accepted['body']['details']['receipt'] == observed
    assert accepted['body']['details']['counter'] == 1
    assert heartbeat['body']['details']['stamp'] == observed['stamp']
    assert sample['body']['details']['stamp'] == runtime_health.host_stamp(engine.store)
    assert sample['body']['details']['stamp']['monotonic'] > observed['stamp']['monotonic']
    for row in (heartbeat, sample):
        assert row['body']['details']['request']['observation_id'] == accepted['id']
        assert dict(id=accepted['id'], sha256=accepted['sha256']) in row['body']['evidence']
    view = runtime_health.read_health_snapshot(engine.store)
    assert view['row'] == sample and view['workers']['worker'] == heartbeat
    assert not sample['body']['details']['global_reasons']
    assert engine.broker.c._head() == account and engine.broker.c.snapshot() == economics


def test_completed_replay_returns_original_pair_without_probe_or_renewal(engine, rig):
    q, observed = semantic(engine), receipt(engine)
    first = engine.publish(q, observed)
    original = runtime_health.read_health_snapshot(engine.store)
    journal = rows(engine)
    advance(rig, 20)
    engine.health.sync_probe = no_probe
    assert engine.publish(q, observed) == first
    assert engine.publish(q, receipt(engine)) == first
    assert engine.recover() is None
    assert runtime_health.read_health_snapshot(engine.store) == original
    assert rows(engine) == journal


@pytest.mark.parametrize('resume', ['recover', 'retry'])
def test_acceptance_crash_before_pair_is_permanently_refused(engine, rig, monkeypatch, resume):
    q, observed = semantic(engine), receipt(engine)
    ids = liveness.record_ids(engine.config, q['request_id'])
    save = engine._save
    account = engine.broker.c._head()

    def crash_after_acceptance(details, *args, **kwargs):
        row = save(details, *args, **kwargs)
        if details['phase'] == 'ACCEPTED':
            raise RuntimeError('synthetic accepted-receipt interruption')
        return row

    with monkeypatch.context() as patch:
        patch.setattr(engine, '_save', crash_after_acceptance)
        with pytest.raises(RuntimeError, match='accepted-receipt'):
            engine.publish(q, observed)
    assert engine.store.get(ids['accepted'])['body']['details']['phase'] == 'ACCEPTED'
    absent(engine.store, ids['heartbeat'], ids['health'], ids['completed'])
    advance(rig, 20)
    engine.health.sync_probe = no_probe
    restarted = liveness.CandidateLiveness(engine.broker, engine.policy, engine.health)
    response = restarted.recover() if resume == 'recover' else restarted.publish(q, receipt(restarted))
    assert response['outcome'] == 'REFUSED'
    assert response['result'] == {'reason': 'INTERRUPTED_OBSERVATION'}
    assert restarted.publish(q, receipt(restarted)) == response
    absent(engine.store, ids['heartbeat'], ids['health'])
    assert restarted._head()['body']['details']['counter'] == 1
    assert engine.broker.c._head() == account


def test_pair_commit_before_response_recovers_exact_original_hashes(engine, rig, monkeypatch):
    q, observed = semantic(engine), receipt(engine)
    ids = liveness.record_ids(engine.config, q['request_id'])

    def crash_before_receipt(*args, **kwargs):
        raise RuntimeError('synthetic completion interruption')

    with monkeypatch.context() as patch:
        patch.setattr(engine, '_complete', crash_before_receipt)
        with pytest.raises(RuntimeError, match='completion interruption'):
            engine.publish(q, observed)
    original = {name: engine.store.get(ids[name]) for name in ('accepted', 'heartbeat', 'health')}
    absent(engine.store, ids['completed'])
    advance(rig, 20)
    engine.health.sync_probe = no_probe
    restarted = liveness.CandidateLiveness(engine.broker, engine.policy, engine.health)
    response = restarted.recover()
    assert response['outcome'] == 'PUBLISHED'
    for name, row in original.items():
        assert engine.store.get(ids[name]) == row
        assert response['result'][name + '_sha256'] == row['sha256']
    assert restarted.publish(q, receipt(restarted)) == response


def test_failed_second_append_rolls_back_pair_and_recovery_never_reobserves(engine, monkeypatch):
    q = semantic(engine)
    ids = liveness.record_ids(engine.config, q['request_id'])
    previous = runtime_health.read_health_snapshot(engine.store)
    validate = EvidenceStore._validate_safety_append

    def reject_sample(db, kind, event_id, body):
        if event_id == runtime_health.KEY:
            raise EvidenceError('SYNTHETIC_SECOND_APPEND_REFUSED')
        return validate(db, kind, event_id, body)

    with monkeypatch.context() as patch:
        patch.setattr(EvidenceStore, '_validate_safety_append', staticmethod(reject_sample))
        with pytest.raises(EvidenceError, match='SECOND_APPEND_REFUSED'):
            engine.publish(q, receipt(engine))
    absent(engine.store, ids['heartbeat'], ids['health'], ids['completed'])
    assert engine.store.get(ids['accepted'])['body']['details']['phase'] == 'ACCEPTED'
    view = runtime_health.read_health_snapshot(engine.store)
    assert view['row'] == previous['row'] and view['workers'] == previous['workers']
    engine.health.sync_probe = no_probe
    response = engine.recover()
    assert response['outcome'] == 'REFUSED'
    assert engine.publish(q, receipt(engine)) == response
    absent(engine.store, ids['heartbeat'], ids['health'])


def test_rate_counter_and_replay_limits_do_not_consume_or_renew_old_pulses(engine, rig):
    engine = liveness.CandidateLiveness(engine.broker, replace(engine.policy, maximum_pulses=2), engine.health)
    first_request = semantic(engine, 'first')
    first = engine.publish(first_request, receipt(engine))
    probe = engine.health.sync_probe
    engine.health.sync_probe = no_probe
    assert engine.publish(first_request, receipt(engine)) == first
    with pytest.raises(EvidenceError, match='PUBLICATION_RATE'):
        engine.publish(semantic(engine, 'too-fast'), receipt(engine))
    assert engine._head()['body']['details']['counter'] == 1
    absent(engine.store, liveness.record_ids(engine.config, 'too-fast')['accepted'])
    advance(rig, .2)
    engine.health.sync_probe = probe
    assert engine.publish(semantic(engine, 'second'), receipt(engine))['outcome'] == 'PUBLISHED'
    assert engine._head()['body']['details']['counter'] == 2
    engine.health.sync_probe = no_probe
    advance(rig, .2)
    with pytest.raises(EvidenceError, match='PUBLICATION_LIMIT'):
        engine.publish(semantic(engine, 'over-limit'), receipt(engine))
    assert engine.publish(first_request, receipt(engine)) == first
    assert engine._head()['body']['details']['counter'] == 2
    absent(engine.store, liveness.record_ids(engine.config, 'over-limit')['accepted'])


@pytest.mark.parametrize('changed', ['account', 'config', 'health_policy', 'unconfigured_worker'])
def test_constructor_refuses_mismatched_health_configuration(engine, changed):
    health, policy = copy(engine.health), engine.policy
    if changed == 'account':
        health.account_id = 'another-account'
    elif changed == 'config':
        health.config = 'e' * 64
    elif changed == 'health_policy':
        health.policy = replace(health.policy, maximum_sample_age_seconds=10.0)
    else:
        policy = replace(policy, worker='unconfigured')
    with pytest.raises(EvidenceError, match='LIVENESS_HEALTH'):
        liveness.CandidateLiveness(engine.broker, policy, health)
    assert rows(engine) == []


@pytest.mark.parametrize('changed', ['generation', 'policy_version', 'candidate_gid'])
def test_existing_journal_cannot_be_rebound_to_another_producer_generation(engine, changed):
    engine.publish(semantic(engine), receipt(engine))
    original = rows(engine)
    value = {'generation': 'two', 'version': 'changed', 'candidate_gid': engine.policy.candidate_gid + 1}
    field = 'version' if changed == 'policy_version' else changed
    policy = replace(engine.policy, **{field: value[field]})
    replacement = liveness.CandidateLiveness(engine.broker, policy, engine.health)
    with pytest.raises(EvidenceError, match='CONFIG_CHANGED_REVIEW_REQUIRED'):
        replacement.publish(semantic(replacement, 'rebound'), receipt(replacement))
    assert rows(engine) == original


@pytest.mark.parametrize('malformed', ['peer_pid', 'peer_start_ticks', 'peer_gid', 'stamp_boot', 'stamp_missing',
    'stamp_boolean', 'challenge_digest', 'challenge_expired', 'extra_authority'])
def test_malformed_receipt_is_refused_before_acceptance(engine, malformed):
    observed = receipt(engine)
    if malformed in {'peer_pid', 'peer_start_ticks', 'peer_gid'}:
        observed['peer'][malformed.removeprefix('peer_')] += 1
    elif malformed == 'stamp_boot':
        observed['stamp']['boot_id'] = 'e' * 36
    elif malformed == 'stamp_missing':
        del observed['stamp']['wall']
    elif malformed == 'stamp_boolean':
        observed['stamp']['monotonic'] = True
    elif malformed == 'challenge_digest':
        observed['challenge_sha256'] = 'invalid'
    elif malformed == 'challenge_expired':
        observed['challenge_stamp']['monotonic'] -= .6
    else:
        observed['health'] = 'READY'
    before = engine.broker.c._head()
    with pytest.raises(EvidenceError):
        engine.publish(semantic(engine), observed)
    assert rows(engine) == [] and engine.broker.c._head() == before


@pytest.mark.parametrize('field,value', [('config_sha256', 'e' * 64), ('operation', 'CANCEL'),
    ('generation', 'two'), ('financial_authority', True)])
def test_semantic_request_cannot_assert_another_config_or_authority(engine, field, value):
    q = semantic(engine)
    q[field] = value
    with pytest.raises(EvidenceError):
        engine.publish(q, receipt(engine))
    assert rows(engine) == []


@pytest.mark.parametrize('direction', [-1, 1])
def test_new_receipt_cannot_be_backdated_or_future_dated(engine, direction):
    observed = receipt(engine)
    for name in ('stamp', 'challenge_stamp'):
        for clock in ('wall', 'monotonic'):
            observed[name][clock] += direction * (engine.policy.publication_timeout_seconds + 1)
    with pytest.raises(EvidenceError, match='RECEIPT_TOO_OLD'):
        engine.publish(semantic(engine), observed)
    assert rows(engine) == []


def test_receipt_clock_step_is_refused_before_acceptance_and_same_request_can_retry(engine):
    q = semantic(engine)
    observed = receipt(engine)
    observed['challenge_stamp']['wall'] -= .4
    observed['challenge_stamp']['monotonic'] -= .1
    # Both challenge ages individually fit the .5s envelope, but their .3s
    # discontinuity exceeds this health configuration's .25s wall-step bound.
    liveness.validate_receipt(observed, engine.worker, engine.policy)
    before = runtime_health.read_health_snapshot(engine.store)
    with pytest.raises(EvidenceError, match='LIVENESS_RECEIPT_CLOCK_STEP'):
        engine.publish(q, observed)
    assert rows(engine) == []
    absent(engine.store, *liveness.record_ids(engine.config, q['request_id']).values())
    assert runtime_health.read_health_snapshot(engine.store) == before
    response = engine.publish(q, receipt(engine))
    assert response['outcome'] == 'PUBLISHED'
    assert engine._head()['body']['details']['counter'] == 1


def test_dead_worker_cannot_create_a_receipt_even_with_matching_old_identity(engine, worker):
    observed = receipt(engine)
    worker.kill()
    worker.wait(timeout=3)
    with pytest.raises(EvidenceError):
        engine.publish(semantic(engine), observed)
    assert rows(engine) == []
