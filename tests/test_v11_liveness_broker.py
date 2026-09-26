"""Finite local broker configuration, launch custody, and failure boundaries.

The one actual launcher smoke uses synthetic PAPER state and no client traffic.
Other process/probe boundaries are mocked before any child or service action.
Separate-principal transport and publication preemption have their own suite.
"""
from dataclasses import asdict
import errno
import fcntl
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
import time

import pytest

from polymarket_scanner.v11 import candidate_liveness, guardian_protocol, liveness_broker, runtime_health
from polymarket_scanner.v11.evidence import EvidenceError, canonical
from polymarket_scanner.v11.guardian_lease import GuardianPolicy
from polymarket_scanner.v11.paper_guardian import PaperGuardian, configuration
from polymarket_scanner.v11.paper_guardian_broker import PaperGuardianBroker
from test_v11_candidate_liveness import engine, rig, worker, semantic, receipt, rows
from test_v11_guardian_broker import peer


def launch_settings(engine):
    # Start with an unbound guardian policy: the child binds broker policy once.
    guardian = PaperGuardian(engine.broker.c, policy=GuardianPolicy('synthetic-launch'),
        health_config=engine.health.config, worker=engine.worker)
    raw = configuration(guardian.coordinator, policy=guardian.policy,
        health_config=guardian.health_config, worker=guardian.worker)
    return raw, guardian


def envelope(engine):
    guardian, _ = launch_settings(engine)
    return dict(version=liveness_broker.producer_wire.VERSION, guardian=guardian,
        broker=asdict(engine.broker.policy), producer=asdict(engine.policy),
        health=candidate_liveness.health_configuration(engine.health), socket='/unused/guardian.sock',
        liveness_socket='/unused/producer.sock', guardian_connections=2, producer_connections=3, seconds=.1)


def isolated_main(monkeypatch, raw):
    # Calling main in-process must never alter the pytest process's rlimits or alarms.
    monkeypatch.setattr(liveness_broker, 'process_limits', lambda: None)
    monkeypatch.setattr(liveness_broker.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(liveness_broker.signal, 'setitimer', lambda *args: None)
    monkeypatch.setattr(liveness_broker.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(raw)))
    return liveness_broker.main()


class RecordingInput(io.BytesIO):
    def __init__(self, *, fail=False):
        super().__init__()
        self.payload = b''
        self.fail = fail

    def write(self, raw):
        if self.fail:
            raise BrokenPipeError('synthetic launch pipe failure')
        self.payload += raw
        return super().write(raw)


def fake_child(*, fail=False):
    stream = RecordingInput(fail=fail)
    actions = []
    return SimpleNamespace(stdin=stream, saved_stdin=stream,
        kill=lambda: actions.append('kill'), communicate=lambda **kwargs: actions.append(kwargs), actions=actions)


def test_launcher_uses_fixed_module_clean_environment_and_closed_descriptors(engine, monkeypatch):
    guardian, _ = launch_settings(engine)
    child = fake_child()
    calls = []
    monkeypatch.setattr(liveness_broker.subprocess, 'Popen', lambda args, **kwargs: calls.append((args, kwargs)) or child)
    result = liveness_broker.launch_liveness_broker(guardian, policy=engine.broker.policy,
        producer=engine.policy, health=engine.health, path='/unused/guardian.sock', liveness_path='/unused/producer.sock',
        guardian_connections=2, producer_connections=3, seconds=.1)
    assert result is child and child.stdin is None and child.saved_stdin.closed
    args, kwargs = calls[0]
    assert args == [liveness_broker.sys.executable, '-s', '-E', '-m', 'polymarket_scanner.v11.liveness_broker']
    assert kwargs['cwd'] == Path(liveness_broker.__file__).resolve().parents[2]
    assert kwargs['env'] == {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}
    assert kwargs['close_fds'] is True and kwargs['start_new_session'] is True
    assert 'shell' not in kwargs and 'pass_fds' not in kwargs
    assert json.loads(child.saved_stdin.payload) == json.loads(canonical(envelope(engine)))
    assert child.actions == []


def test_launcher_pipe_failure_kills_and_reaps_only_its_child(engine, monkeypatch):
    guardian, _ = launch_settings(engine)
    child = fake_child(fail=True)
    monkeypatch.setattr(liveness_broker.subprocess, 'Popen', lambda *args, **kwargs: child)
    with pytest.raises(BrokenPipeError, match='synthetic launch'):
        liveness_broker.launch_liveness_broker(guardian, policy=engine.broker.policy, producer=engine.policy,
            health=engine.health, path='/unused/a', liveness_path='/unused/b')
    assert child.actions == ['kill', {'timeout': 5}]


def test_oversized_launch_never_spawns(engine, monkeypatch):
    monkeypatch.setattr(liveness_broker.subprocess, 'Popen', lambda *args, **kwargs: pytest.fail('oversized launch spawned'))
    with pytest.raises(EvidenceError, match='LIVENESS_LAUNCH_BOUND'):
        liveness_broker.launch_liveness_broker({'padding': 'x' * 33000}, policy=engine.broker.policy,
            producer=engine.policy, health=engine.health, path='/unused/a', liveness_path='/unused/b')


@pytest.mark.parametrize('where', ['top', 'guardian', 'broker', 'producer', 'health', 'health_policy', 'source', 'version'])
def test_main_rejects_unknown_or_wrong_typed_configuration_before_serve(engine, monkeypatch, capsys, where):
    raw = envelope(engine)
    if where == 'version':
        raw['version'] = 'unknown-version'
    elif where == 'top':
        raw['unknown'] = True
    elif where == 'health_policy':
        raw['health']['policy']['unknown'] = True
    elif where == 'source':
        raw['health']['sources'][0]['unknown'] = True
    else:
        raw[where]['unknown'] = True
    monkeypatch.setattr(liveness_broker.LivenessBroker, 'serve', lambda *args, **kwargs: pytest.fail('invalid configuration served'))
    assert isolated_main(monkeypatch, canonical(raw).encode()) == 2
    out = capsys.readouterr()
    assert out.err == '' and json.loads(out.out) == {'outcome': 'FAILED_CLOSED', 'financial_authority': False}


@pytest.mark.parametrize('raw', [b'[]', b'{invalid-json', b'[' * 1500 + b'0' + b']' * 1500, b'x' * 33000])
def test_main_malformed_input_is_bounded_and_redacted(monkeypatch, capsys, raw):
    monkeypatch.setattr(liveness_broker, 'restore', lambda *args: pytest.fail('malformed launch restored archive'))
    assert isolated_main(monkeypatch, raw) == 2
    out = capsys.readouterr()
    assert out.err == '' and json.loads(out.out) == {'outcome': 'FAILED_CLOSED', 'financial_authority': False}


def test_main_restores_typed_producer_and_passes_explicit_finite_limits(engine, monkeypatch, capsys):
    calls = []
    raw = envelope(engine)
    monkeypatch.setattr(liveness_broker.LivenessBroker, 'serve', lambda self, *args, **kwargs: calls.append((self, args, kwargs)))
    assert isolated_main(monkeypatch, canonical(raw).encode()) == 0
    out = capsys.readouterr()
    assert out.err == '' and json.loads(out.out) == {'outcome': 'FINITE_RUN_ENDED_GATED', 'financial_authority': False}
    server, args, kwargs = calls[0]
    assert server.producer.broker is server.broker
    assert server.producer.health.config == engine.health.config
    assert args == (raw['socket'], raw['liveness_socket'])
    assert kwargs == {'guardian_connections': 2, 'producer_connections': 3, 'seconds': .1}


@pytest.mark.parametrize('wrong', ['producer_type', 'broker_identity'])
def test_server_requires_the_exact_typed_producer_broker_binding(engine, wrong):
    broker = object() if wrong == 'broker_identity' else engine.broker
    producer = object() if wrong == 'producer_type' else engine
    with pytest.raises(EvidenceError, match='LIVENESS_BROKER_BINDING'):
        liveness_broker.LivenessBroker(broker, producer)


@pytest.mark.parametrize('field,value', [('guardian_connections', 0), ('guardian_connections', True),
    ('guardian_connections', 201), ('producer_connections', 0), ('producer_connections', 1.0),
    ('producer_connections', 201), ('seconds', 0), ('seconds', True), ('seconds', 61),
    ('seconds', float('inf')), ('seconds', '30')])
def test_invalid_finite_limits_are_refused_before_listener_custody(engine, monkeypatch, field, value):
    server = liveness_broker.LivenessBroker(engine.broker, engine)
    monkeypatch.setattr(engine.broker, '_listener', lambda *args: pytest.fail('invalid limits opened listener'))
    with pytest.raises(EvidenceError):
        server.serve('/unused/a', '/unused/b', **{field: value})
    assert server.job is None and server.peers == {}


def test_same_socket_path_is_refused_before_binding(engine, monkeypatch):
    server = liveness_broker.LivenessBroker(engine.broker, engine)
    monkeypatch.setattr(engine.broker, '_listener', lambda *args: pytest.fail('same endpoints reached listener'))
    with pytest.raises(EvidenceError, match='FINITE_RUN_BOUND'):
        server.serve('/unused/same', '/unused/same', seconds=.1)


@pytest.mark.parametrize('bad', ['relative_guardian', 'writable_guardian_parent', 'producer_file', 'producer_symlink'])
def test_endpoint_path_guard_preserves_existing_objects(engine, bad):
    server = liveness_broker.LivenessBroker(engine.broker, engine)
    with tempfile.TemporaryDirectory(prefix='v11-path-', dir='/tmp') as directory:
        root = Path(directory)
        guardian_path, producer_path = root / 'g', root / 'p'
        retained = root / 'retained'
        retained.write_text('synthetic retained fixture')
        if bad == 'relative_guardian':
            guardian_path = Path('relative-guardian.sock')
        elif bad == 'writable_guardian_parent':
            parent = root / 'writable'
            parent.mkdir()
            parent.chmod(0o777)
            guardian_path = parent / 'g'
        elif bad == 'producer_file':
            producer_path.write_text('existing producer fixture')
        else:
            producer_path.symlink_to(retained)
        with pytest.raises(EvidenceError):
            server.serve(guardian_path, producer_path, seconds=.1)
        assert retained.read_text() == 'synthetic retained fixture'
        if bad == 'producer_file':
            assert producer_path.read_text() == 'existing producer fixture'
        if bad == 'producer_symlink':
            assert producer_path.is_symlink()
        assert not (root / 'g').exists()


@pytest.mark.parametrize('stdout,code,expected', [(b'yes\n', 0, True), (b'no\n', 0, False),
    (b'yes\n', 1, None), (b'yes', 0, None), (b'synthetic healthy', 0, None)])
def test_restored_monitor_uses_strict_parent_guarded_production_sync(engine, monkeypatch, stdout, code, expected):
    calls = []
    def probe(args, **kwargs):
        calls.append((args, kwargs))
        return SimpleNamespace(returncode=code, stdout=stdout)
    monkeypatch.setattr(runtime_health.subprocess, 'run', probe)
    restored = candidate_liveness.restore_health(engine.store, candidate_liveness.health_configuration(engine.health))
    assert restored.config == engine.health.config
    result = restored.sync_probe()
    assert result['synchronized'] is expected and result['mechanism'] == 'LOCAL_SYSTEMD_TIMEDATED'
    args, kwargs = calls[0]
    assert args == ['/usr/bin/timedatectl', 'show', '--property=NTPSynchronized', '--value']
    assert kwargs['timeout'] == 2 and kwargs['check'] is False
    assert callable(kwargs['preexec_fn']) and 'shell' not in kwargs
    assert kwargs['env'] == {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'SYSTEMD_PAGER': 'cat'}


def test_serialized_health_cannot_install_a_synthetic_sync_probe(engine):
    raw = candidate_liveness.health_configuration(engine.health)
    raw['sync_probe'] = 'SYNTHETIC_OFF_HOST_FIXTURE'
    with pytest.raises(EvidenceError, match='HEALTH_LAUNCH_SCHEMA'):
        candidate_liveness.restore_health(engine.store, raw)


@pytest.mark.parametrize('failure', ['pipe', 'fork'])
def test_publication_spawn_failure_leaks_no_job_or_receipt_and_guardian_remains_usable(engine, peer, monkeypatch, failure):
    server = liveness_broker.LivenessBroker(engine.broker, engine)
    opened = []
    pipe = liveness_broker.os.pipe2
    def pipe2(flags):
        if failure == 'pipe':
            raise OSError(errno.EMFILE, 'synthetic pipe limit')
        pair = pipe(flags)
        opened.extend(pair)
        return pair
    def fork():
        raise OSError(errno.EAGAIN, 'synthetic fork limit')
    monkeypatch.setattr(liveness_broker.os, 'pipe2', pipe2)
    monkeypatch.setattr(liveness_broker.os, 'fork', fork)
    with pytest.raises(OSError):
        server._start_job(object(), semantic(engine), receipt(engine), time.monotonic() + 1)
    assert server.job is None and rows(engine) == []
    for descriptor in opened:
        with pytest.raises(OSError) as error:
            os.fstat(descriptor)
        assert error.value.errno == errno.EBADF
    response = engine.broker.handle(guardian_protocol.request(engine.broker.config, 'still-usable', 'SNAPSHOT', cursor=''), peer)
    assert response['outcome'] == 'SNAPSHOT' and response['financial_authority'] is False


def test_actual_finite_launcher_removes_endpoints_gates_lease_and_releases_lock(engine):
    raw, guardian = launch_settings(engine)
    expected = PaperGuardianBroker(guardian, engine.broker.policy)
    before = engine.broker.c._head()
    with tempfile.TemporaryDirectory(prefix='v11-launch-', dir='/tmp') as directory:
        guardian_path, producer_path = Path(directory) / 'g', Path(directory) / 'p'
        child = liveness_broker.launch_liveness_broker(raw, policy=engine.broker.policy,
            producer=engine.policy, health=engine.health, path=guardian_path, liveness_path=producer_path,
            guardian_connections=2, producer_connections=2, seconds=.1)
        try:
            out, err = child.communicate(timeout=5)
            assert child.returncode == 0, (out, err)
            assert err == b'' and json.loads(out) == {'outcome': 'FINITE_RUN_ENDED_GATED', 'financial_authority': False}
        finally:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=3)
        assert not guardian_path.exists() and not producer_path.exists()
    assert expected.g._head()['body']['details']['status'] == 'GATED'
    assert engine.broker.c._head() == before
    with Path(str(engine.store.path) + '.guardian.lock').open('r+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
