"""Original candidate receipts bind health without becoming fresh on replay."""
from copy import deepcopy
from dataclasses import asdict
from types import SimpleNamespace

import pytest

from polymarket_scanner.v11 import candidate_liveness as liveness
from polymarket_scanner.v11 import runtime_health as health
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from polymarket_scanner.v11.liveness_protocol import ProducerPolicy, VERSION
from test_v11_paper_coordinator import rig
from test_v11_runtime_health import monitor, advance, gate


def accepted(rig, m, *, request_id='pulse', producer=None, worker=None, health_config=None,
             account_id=None, receipt=None, phase='ACCEPTED', record_id=None, mutate=None):
    """Synthetic immutable receipt; no process, socket or deployment authority."""
    producer = producer or ProducerPolicy('fixture', 'worker', 'one', 1002)
    worker = worker or dict(pid=123, start_ticks=10, uid=1001, boot_id=rig['boot'][0])
    stamp = health.host_stamp(rig['store'])
    receipt = receipt or dict(stamp=stamp, challenge_stamp=dict(stamp), challenge_sha256='d'*64,
                             peer=dict(worker, gid=producer.candidate_gid))
    configuration = liveness.config_digest('c'*64, health_config or m.config, producer, worker)
    ids = liveness.record_ids(configuration, request_id)
    request = dict(version=VERSION, config_sha256=configuration, request_id=request_id, operation='PULSE')
    response = None if phase == 'ACCEPTED' else dict(request, outcome='REFUSED',
        result=dict(reason='INTERRUPTED_OBSERVATION'), financial_authority=False)
    d = dict(version=VERSION, config_sha256=configuration, broker_config='c'*64,
        health_config=health_config or m.config, producer=asdict(producer), worker=worker,
        account_id=account_id or m.account_id, request=request, phase=phase, response=response,
        receipt=receipt, counter=1, financial_authority=False)
    event = liveness.journal_key(d['account_id'], producer.worker)
    # Deliberately malformed historical records use ordinary fixture audit so
    # health validation itself must reject them, without bypassing its code.
    if mutate:
        mutate(d)
        row = rig['store'].audit(record_id or ids['accepted'], kind='RUNTIME_STATUS', event_id=event, details=d)
    else:
        row = rig['store'].safety_audit(record_id or ids['accepted'], kind='RUNTIME_STATUS', event_id=event, details=d)
    return row, ids


def publish(m, row, ids, **overrides):
    args = dict(heartbeat_key=ids['heartbeat'], worker='worker', generation='one', observation_id=row['id'])
    args.update(overrides)
    return m.publish_observation(ids['health'], **args)


def absent(store, ids):
    for name in ('heartbeat', 'health'):
        with pytest.raises(EvidenceError, match='EVIDENCE_MISSING'): store.get(ids[name])


def test_slow_probe_never_restamps_the_original_receipt(rig, monkeypatch):
    m = monitor(rig, monkeypatch); row, ids = accepted(rig, m)
    stamp = deepcopy(row['body']['details']['receipt']['stamp']); probe = m.sync_probe
    other = EvidenceStore(rig['store'].path, 'V11_PAPER', clock=rig['store'].clock)
    def slow_probe():
        with other._connect() as db: db.execute('BEGIN IMMEDIATE')
        advance(rig, 2.)
        return probe()
    m.sync_probe = slow_probe
    sample = publish(m, row, ids); heartbeat = rig['store'].get(ids['heartbeat'])
    h, s = heartbeat['body']['details'], sample['body']['details']
    assert h['stamp'] == stamp and h['stamp'] != s['stamp']
    assert s['stamp'] == health.host_stamp(rig['store'])
    assert heartbeat['body']['recorded_at'] == sample['body']['recorded_at'] == rig['now'][0]
    assert h['request'] == dict(action='HEARTBEAT', worker='worker', generation='one', observation_id=row['id'])
    assert s['request'] == dict(action='SAMPLE', heartbeat_key=heartbeat['id'], worker='worker',
                                generation='one', observation_id=row['id'])
    ref = dict(id=row['id'], sha256=row['sha256'])
    assert heartbeat['body']['evidence'] == [ref] and ref in sample['body']['evidence']
    assert dict(id=heartbeat['id'], sha256=heartbeat['sha256']) in sample['body']['evidence']


def test_two_original_pulses_can_recover_health_without_claiming_authority(rig, monkeypatch):
    m = monitor(rig, monkeypatch); row, ids = accepted(rig, m)
    publish(m, row, ids); advance(rig)
    row, ids = accepted(rig, m, request_id='second'); d = publish(m, row, ids)['body']['details']
    assert not d['global_reasons'] and gate(rig)
    assert d['financial_authority'] is False and d['independent_guardian_commissioned'] is False


@pytest.mark.parametrize('delay', ['before_probe', 'during_probe'])
def test_delayed_receipt_remains_stale_even_though_sample_is_fresh(rig, monkeypatch, delay):
    m = monitor(rig, monkeypatch); row, ids = accepted(rig, m)
    original = m.sync_probe
    if delay == 'before_probe': advance(rig, 6)
    else:
        def slow():
            advance(rig, 6)
            return original()
        m.sync_probe = slow
    d = publish(m, row, ids)['body']['details']
    assert d['stamp'] == health.host_stamp(rig['store'])
    assert 'WORKER_HEARTBEAT_STALE_OR_IDENTITY_CHANGED:worker' in d['global_reasons']
    with pytest.raises(EvidenceError, match='CLOCK_OR_LIVENESS_GATED'): gate(rig)


def test_completed_replay_preserves_receipt_and_never_probes_or_reads_current_process(rig, monkeypatch):
    m = monitor(rig, monkeypatch); row, ids = accepted(rig, m)
    sample = publish(m, row, ids); heartbeat = rig['store'].get(ids['heartbeat']); advance(rig, 100)
    m.sync_probe = lambda: pytest.fail('replay must not probe')
    monkeypatch.setattr(liveness, 'process_identity', lambda *a: pytest.fail('replay must not renew process proof'))
    assert publish(m, row, ids) == sample
    assert m._publication_replay(ids['health'], ids['heartbeat'], 'worker', 'one', observation_id=row['id']) == sample
    assert rig['store'].get(ids['heartbeat']) == heartbeat


@pytest.mark.parametrize('mismatch', ['health_config', 'account_id', 'worker', 'generation', 'phase', 'record_id'])
def test_original_receipt_binding_is_required_before_probe_or_writes(rig, monkeypatch, mismatch):
    m = monitor(rig, monkeypatch); options = {}
    if mismatch == 'health_config': options[mismatch] = 'f'*64
    elif mismatch == 'account_id': options[mismatch] = 'other-account'
    elif mismatch == 'worker': options['producer'] = ProducerPolicy('fixture', 'other-worker', 'one', 1002)
    elif mismatch == 'generation': options['producer'] = ProducerPolicy('fixture', 'worker', 'other-generation', 1002)
    elif mismatch == 'phase': options[mismatch] = 'COMPLETED'
    else: options[mismatch] = 'different-accepted-id'
    row, ids = accepted(rig, m, **options)
    m.sync_probe = lambda: pytest.fail('invalid receipt must not probe')
    with pytest.raises(EvidenceError, match='LIVENESS_OBSERVATION_BINDING'): publish(m, row, ids)
    absent(rig['store'], ids)


@pytest.mark.parametrize('mutation', [
    lambda d: d['receipt']['peer'].update(gid=1003),
    lambda d: d['receipt']['stamp'].update(boot_id='b'*36),
    lambda d: d['receipt']['stamp'].update(wall=True),
    lambda d: d['receipt']['stamp'].update(monotonic='100'),
    lambda d: d['receipt']['challenge_stamp'].update(monotonic=101.),
    lambda d: d['receipt']['challenge_stamp'].update(monotonic=99.),
    lambda d: d['receipt'].update(challenge_sha256='bad'),
    lambda d: d.update(producer=[]),
    lambda d: d.update(worker=None),
])
def test_malformed_historical_receipts_fail_closed(rig, monkeypatch, mutation):
    m = monitor(rig, monkeypatch); row, ids = accepted(rig, m, mutate=mutation)
    m.sync_probe = lambda: pytest.fail('invalid receipt must not probe')
    with pytest.raises(EvidenceError): publish(m, row, ids)
    absent(rig['store'], ids)


def test_challenge_wall_step_must_fit_health_policy_too(rig, monkeypatch):
    m = monitor(rig, monkeypatch); stamp = health.host_stamp(rig['store'])
    receipt = dict(stamp=stamp, challenge_stamp=dict(stamp, wall=stamp['wall']-.4, monotonic=stamp['monotonic']-.1),
        challenge_sha256='d'*64, peer=dict(pid=123, start_ticks=10, uid=1001, gid=1002, boot_id=rig['boot'][0]))
    row, ids = accepted(rig, m, receipt=receipt)
    m.sync_probe = lambda: pytest.fail('invalid receipt must not probe')
    with pytest.raises(EvidenceError, match='LIVENESS_OBSERVATION_CLOCK'): publish(m, row, ids)
    absent(rig['store'], ids)


def test_one_receipt_cannot_be_republished_under_alternate_record_ids(rig, monkeypatch):
    m = monitor(rig, monkeypatch); row, ids = accepted(rig, m)
    m.sync_probe = lambda: pytest.fail('invalid pair keys must not probe')
    with pytest.raises(EvidenceError, match='LIVENESS_PUBLICATION_KEYS'):
        m.publish_observation('another-health', heartbeat_key='another-heartbeat', worker='worker',
                              generation='one', observation_id=row['id'])
    absent(rig['store'], ids)


def test_observed_pair_cannot_replay_as_legacy_or_other_observation(rig, monkeypatch):
    m = monitor(rig, monkeypatch); row, ids = accepted(rig, m); sample = publish(m, row, ids)
    m.sync_probe = lambda: pytest.fail('conflicting replay must not probe')
    with pytest.raises(EvidenceError, match='REPLAY_CONFLICT'):
        m.publish(ids['health'], heartbeat_key=ids['heartbeat'], worker='worker', generation='one')
    another, _ = accepted(rig, m, request_id='another')
    with pytest.raises(EvidenceError, match='LIVENESS_PUBLICATION_KEYS'):
        publish(m, another, ids)
    assert rig['store'].get(ids['health']) == sample


def test_half_pair_cannot_be_filled_or_restamped(rig, monkeypatch):
    m = monitor(rig, monkeypatch); row, ids = accepted(rig, m)
    m._heartbeat(ids['heartbeat'], worker='worker', generation='one', observation=row)
    m.sync_probe = lambda: pytest.fail('half pair must not probe')
    with pytest.raises(EvidenceError, match='INCOMPLETE_PUBLICATION'): publish(m, row, ids)
    with pytest.raises(EvidenceError, match='EVIDENCE_MISSING'): rig['store'].get(ids['health'])


@pytest.mark.parametrize('tamper', ['stamp', 'reference', 'request', 'financial_authority'])
def test_restricted_transaction_refuses_receipt_smuggling(rig, monkeypatch, tamper):
    m = monitor(rig, monkeypatch); row, ids = accepted(rig, m)
    d = dict(version=health.VERSION, config_sha256=m.config,
        request=dict(action='HEARTBEAT', worker='worker', generation='one', observation_id=row['id']),
        stamp=dict(row['body']['details']['receipt']['stamp']), worker='worker', generation='one', financial_authority=False)
    refs = (row['id'],)
    if tamper == 'stamp': d['stamp']['monotonic'] += 1
    elif tamper == 'reference': refs = ()
    elif tamper == 'request': d['request']['extra'] = True
    else: d['financial_authority'] = True
    with pytest.raises(EvidenceError):
        with rig['store'].runtime_health_publication() as bound:
            bound.safety_audit(ids['heartbeat'], kind='RUNTIME_STATUS', event_id=health._worker_key('worker'),
                               details=d, evidence_ids=refs)
    absent(rig['store'], ids)


def test_missing_sample_observation_reference_rolls_back_both_records(rig, monkeypatch):
    m = monitor(rig, monkeypatch); row, ids = accepted(rig, m); original = EvidenceStore.safety_audit
    def omit(self, key, **kwargs):
        if key == ids['health']:
            kwargs['evidence_ids'] = tuple(v for v in kwargs['evidence_ids'] if v != row['id'])
        return original(self, key, **kwargs)
    monkeypatch.setattr(EvidenceStore, 'safety_audit', omit)
    with pytest.raises(EvidenceError, match='OBSERVATION_BINDING'): publish(m, row, ids)
    absent(rig['store'], ids)
    assert rig['store'].get(row['id']) == row


def test_original_publish_shape_remains_unchanged(rig, monkeypatch):
    m = monitor(rig, monkeypatch)
    sample = m.publish('legacy-health', heartbeat_key='legacy-heartbeat', worker='worker', generation='one')
    heartbeat = rig['store'].get('legacy-heartbeat')
    assert heartbeat['body']['details']['request'] == dict(action='HEARTBEAT', worker='worker', generation='one')
    assert heartbeat['body']['evidence'] == []
    assert sample['body']['details']['request'] == dict(action='SAMPLE', heartbeat_key='legacy-heartbeat', worker='worker', generation='one')


def test_optional_sync_parent_guard_only_applies_to_requested_child_probe(monkeypatch):
    calls = []; guarded = []
    monkeypatch.setattr(health.os, 'getpid', lambda: 321)
    monkeypatch.setattr(health, '_sync_parent_death', lambda expected: guarded.append(expected))
    def run(args, **kwargs):
        calls.append(kwargs)
        if 'preexec_fn' in kwargs: kwargs['preexec_fn']()
        return SimpleNamespace(returncode=0, stdout=b'yes\n')
    monkeypatch.setattr(health.subprocess, 'run', run)
    assert health.local_sync_status()['synchronized'] is True
    assert health.local_sync_status(parent_death=True)['synchronized'] is True
    assert 'preexec_fn' not in calls[0] and guarded == [321]


@pytest.mark.parametrize('result,parent,exits', [(0,321,False), (1,321,True), (0,999,True)])
def test_sync_subprocess_guard_requires_prctl_and_original_parent(monkeypatch, result, parent, exits):
    calls = []
    libc = SimpleNamespace(prctl=lambda *args: calls.append(args) or result)
    monkeypatch.setattr(health.ctypes, 'CDLL', lambda *a, **kw: libc)
    monkeypatch.setattr(health.os, 'getppid', lambda: parent)
    monkeypatch.setattr(health.os, '_exit', lambda code: (_ for _ in ()).throw(RuntimeError(str(code))))
    if exits:
        with pytest.raises(RuntimeError, match='127'): health._sync_parent_death(321)
    else: health._sync_parent_death(321)
    assert calls == [(1, health.signal.SIGKILL, 0, 0, 0)]
