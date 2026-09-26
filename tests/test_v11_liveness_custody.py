"""Disposable distinct-principal pulse custody, preemption and restart proof.

All account/source data and fault hooks are explicit synthetic fixtures. These
tests use no venue, host service, production account or external network.
"""
from copy import deepcopy
from contextlib import closing, contextmanager
from dataclasses import asdict
from functools import partial
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import struct
import sys
import threading
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))

from guardian_custody_namespace import (NamespaceUnavailable, _credentials, _validate_role,
                                        prerequisites, run_namespace_fixture)
from test_v11_guardian_custody import _denials, _finish, _line


def _send(process, value):
    process.stdin.write(json.dumps(value).encode()+b'\n'); process.stdin.flush()


def _print(value):
    print(json.dumps(value), flush=True)


def _rows(config):
    try:
        with closing(sqlite3.connect('file:'+str(Path(config['private'])/'paper.sqlite')+'?mode=ro', uri=True)) as db:
            return [dict(seq=seq, id=key, body=json.loads(body)) for seq,key,body in
                    db.execute('SELECT seq,record_id,body FROM v11_records ORDER BY seq')]
    except sqlite3.Error as exc:
        raise AssertionError(dict(stage='fixture_readonly_inspection', error=type(exc).__name__,
                                  resources=_diagnostics(config))) from None


def _diagnostics(config):
    result = {}
    for name in ('paper.sqlite', 'paper.sqlite-wal', 'paper.sqlite-shm'):
        path = Path(config['private'], name)
        try:
            info = path.lstat(); result[name] = dict(uid=info.st_uid, mode=oct(info.st_mode & 0o777))
        except OSError as exc: result[name] = dict(error=type(exc).__name__)
    marker = Path(config['private'], 'publication-error.json')
    if marker.exists(): result['publication_error'] = json.loads(marker.read_text())
    result['controller_fd_count'] = len(list(Path('/proc/self/fd').iterdir()))
    return result


def _broker(private, endpoint, producer_endpoint, worker_pid, mode):
    from test_v11_paper_coordinator import coordinator, proposal, rig
    from polymarket_scanner.v11.candidate_liveness import CandidateLiveness, health_configuration
    from polymarket_scanner.v11.evidence import canonical
    from polymarket_scanner.v11.guardian_lease import GuardianPolicy, process_identity
    from polymarket_scanner.v11.guardian_protocol import BrokerPolicy
    from polymarket_scanner.v11.liveness_protocol import ProducerPolicy
    from polymarket_scanner.v11.liveness_broker import LivenessBroker
    from polymarket_scanner.v11.paper_guardian import PaperGuardian, configuration, restore
    from polymarket_scanner.v11.paper_guardian_broker import PaperGuardianBroker
    from polymarket_scanner.v11.runtime_health import RuntimeHealth, HealthPolicy, SourceNeed
    from polymarket_scanner.v11 import runtime_health

    patch = pytest.MonkeyPatch(); saved = Path(private)/'fixture-config.json'
    if not saved.exists():
        fixture = rig.__wrapped__(Path(private), patch)
        account = coordinator(fixture); account.coordinate('custody-seed', (proposal(fixture, units='2'),))
        wall, mono = fixture['now'][0], time.monotonic()
        account.store.clock = lambda: wall+time.monotonic()-mono
        hp = HealthPolicy('synthetic-pulse-custody', 2., 2., .5, 2, .05, ('candidate',))
        need = SourceNeed(fixture['context'].event_id, 'fixture', 'OFFICIAL_OBSERVATION',
                          'fixture', fixture['context'].station_id, 60.)
        monitor = RuntimeHealth(account.store, hp, account_id='account',
            scopes={fixture['context'].event_id: ('fixture',)}, sources=(need,),
            sync_probe=lambda: dict(synchronized=True, mechanism='SYNTHETIC_OFF_HOST_FIXTURE'))
        guardian = PaperGuardian(account, policy=GuardianPolicy('synthetic-pulse-custody'),
                                 health_config=monitor.config, worker=process_identity(int(worker_pid)))
        raw = dict(guardian=configuration(account, policy=guardian.policy, health_config=monitor.config,
                   worker=guardian.worker), health=health_configuration(monitor), wall=wall, mono=mono)
        saved.write_text(canonical(raw)); saved.chmod(0o600)
    raw = json.loads(saved.read_text()); guardian = restore(raw['guardian'])
    guardian.store.clock = lambda: raw['wall']+time.monotonic()-raw['mono']
    hp = dict(raw['health']['policy']); hp['workers'] = tuple(hp['workers'])
    monitor = RuntimeHealth(guardian.store, HealthPolicy(**hp), account_id='account',
        scopes={k: tuple(v) for k,v in raw['health']['scopes'].items()},
        sources=tuple(SourceNeed(**v) for v in raw['health']['sources']),
        sync_probe=lambda: dict(synchronized=True, mechanism='SYNTHETIC_OFF_HOST_FIXTURE'))
    policy = BrokerPolicy('synthetic-pulse-custody', 1001, 1001, 1002, 1002)
    broker = PaperGuardianBroker(guardian, policy)
    producer = CandidateLiveness(broker, ProducerPolicy('synthetic-pulse-custody', 'candidate',
        'custody-generation', 1003, minimum_interval_seconds=.05, publication_timeout_seconds=3.), monitor)
    server = LivenessBroker(broker, producer)
    publish_original = producer.publish
    def report_failure(request, receipt):
        try: return publish_original(request, receipt)
        except BaseException as exc:
            # This is a disposable fixture-only diagnostic inside broker
            # custody. Never change production error/authority behavior.
            from polymarket_scanner.v11.evidence import EvidenceError
            reason = str(exc)[:160] if isinstance(exc, (EvidenceError, sqlite3.Error)) else type(exc).__name__
            Path(private, 'publication-error.json').write_text(json.dumps(dict(error=type(exc).__name__, reason=reason)))
            raise
    producer.publish = report_failure
    original = deepcopy(broker.c._state(broker.c._head())); reserved = broker.c.snapshot()['reserved_cash']
    config = dict(socket=endpoint, liveness_socket=producer_endpoint, private=private,
                  policy=asdict(policy), config=broker.config, liveness_config=producer.config, broker_pid=os.getpid())
    if mode in {'probe_preempt', 'probe_death'}:
        actual_run = runtime_health.subprocess.run
        probe_code = ('import json,os,time\nfrom pathlib import Path\n'
                      f'Path({str(Path(private, "busy-probe-pid"))!r}).write_text('
                      'json.dumps(dict(pid=os.getpid(),parent=os.getppid())))\ntime.sleep(10)\n')
        def fixture_command(args, **kwargs):
            assert args == ['/usr/bin/timedatectl', 'show', '--property=NTPSynchronized', '--value']
            assert kwargs['timeout'] == 2 and callable(kwargs['preexec_fn'])
            assert kwargs['env'] == {'PATH': '/usr/bin:/bin', 'LC_ALL': 'C', 'SYSTEMD_PAGER': 'cat'}
            # Retain the actual production parent-death preexec, timeout,
            # descriptor handling and bounded subprocess.run machinery.
            return actual_run([sys.executable, '-s', '-E', '-c', probe_code], **kwargs)
        patch.setattr(runtime_health.subprocess, 'run', fixture_command)
        monitor.sync_probe = partial(runtime_health.local_sync_status, parent_death=True)
    if mode in {'preempt', 'death'}:
        publish = monitor.publish_observation
        def busy(*args, **kwargs):
            accepted = broker.store.get(kwargs['observation_id'])['body']['details']
            if accepted['request']['request_id'] == 'busy':
                # An actual child holds SQLite's writer lock. Only this fixture
                # adds the pause, after the durable accepted observation.
                with sqlite3.connect(broker.store.path) as db:
                    db.execute('BEGIN IMMEDIATE')
                    Path(private, 'busy-writer-pid').write_text(str(os.getpid()))
                    time.sleep(10)
            return publish(*args, **kwargs)
        monitor.publish_observation = busy
    holder = sqlite3.connect(broker.store.path)
    holder.execute('PRAGMA journal_mode=WAL'); holder.execute('SELECT COUNT(*) FROM v11_records').fetchone()
    start = server._start_job
    def started(conn, request, receipt, deadline):
        # Keep actual sidecars present for initial denial checks, but never
        # carry a live SQLite connection into fork. Production EvidenceStore
        # closes every parent connection; inherited SQLite caches plus closed
        # raw descriptors are not a supported connection lifecycle.
        holder.close()
        start(conn, request, receipt, deadline)
        if request['request_id'] == 'busy' and mode in {'preempt', 'death', 'probe_preempt', 'probe_death'}:
            _print(dict(job=server.job['pid']))
    server._start_job = started
    original_socket = broker._socket
    @contextmanager
    def listening(path, *args, **kwargs):
        with original_socket(path, *args, **kwargs) as listener:
            if Path(path) == Path(producer_endpoint):
                assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_ACCEPTCONN) == 1
                assert listener.getsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED) == 1
                _print({'listening': True})
            yield listener
    broker._socket = listening
    _print(config)
    try:
        server.serve(endpoint, producer_endpoint, guardian_connections=100, producer_connections=128, seconds=8)
        state = broker.c._state(broker.c._head())
        expected = deepcopy(original)
        expected['intents']['proposal'].update(status='CANCEL_REQUESTED', cancel_requested=True)
        assert canonical(state) == canonical(expected), 'pulse/cancel changed unrelated account state'
        assert broker.c.snapshot()['reserved_cash'] == reserved
        _print(dict(reservations_unchanged=True, financial_authority=False, no_unrelated_account_mutation=True))
    finally:
        holder.close(); patch.undo()


def _wire(config, request_id, *, extra=None):
    from polymarket_scanner.v11 import liveness_protocol as wire
    conn = socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET); conn.settimeout(2)
    conn.connect(config['liveness_socket'])
    hello = wire.decode(conn.recv(wire.MAX_PULSE_BYTES+1)); wire.validate_challenge(hello, config['liveness_config'])
    q = wire.request(config['liveness_config'], request_id, hello['challenge'])
    if extra: q.update(extra)
    return conn, q


def _candidate():
    from polymarket_scanner.v11.evidence import EvidenceError
    from polymarket_scanner.v11 import liveness_protocol as wire
    _print({'ready': True}); config = None; busy_result = []; threads = []
    for line in sys.stdin:
        command = json.loads(line); op = command['operation']
        if op == 'configure':
            config = command['config']; _print(dict(denied=_denials(config))); continue
        client = wire.LivenessClient(config['liveness_socket'], broker_uid=1001, broker_gid=1001,
                                     config=config['liveness_config'], timeout_seconds=4.)
        if op == 'pulse':
            try: _print(dict(response=client.pulse(command['request_id'])))
            except EvidenceError as exc: _print(dict(refused=str(exc)))
        elif op == 'forbidden':
            conn, q = _wire(config, 'forbidden', extra={'status': 'READY'})
            with conn:
                conn.send(wire.encode(q)); assert conn.recv(1) == b''
            _print({'forbidden_closed': True})
        elif op == 'start_busy':
            def publish_busy():
                try: client.pulse('busy'); busy_result.append('UNEXPECTED_SUCCESS')
                except (EvidenceError, OSError): busy_result.append('INTERRUPTED')
            thread = threading.Thread(target=publish_busy, daemon=True); threads.append(thread); thread.start()
            _print({'busy_started': True})
        elif op == 'start_flood':
            def flood():
                for index in range(32):
                    try:
                        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as conn:
                            conn.settimeout(.1); conn.connect(config['liveness_socket'])
                            hello = wire.decode(conn.recv(wire.MAX_PULSE_BYTES+1))
                            q = wire.request(config['liveness_config'], 'flood:'+str(index), hello['challenge'])
                            q['operation'] = 'NEW_ORDER'; conn.send(wire.encode(q)); conn.recv(1)
                    except (EvidenceError, OSError, KeyError): pass
                    time.sleep(.02)
            thread = threading.Thread(target=flood, daemon=True); threads.append(thread); thread.start()
            _print({'flood_started': True})
        elif op == 'finish_busy':
            for thread in threads: thread.join(4)
            assert all(not thread.is_alive() for thread in threads) and busy_result == ['INTERRUPTED']
            _print({'publication_interrupted': True})
        elif op == 'transfer':
            conn, q = _wire(config, 'transferred')
            with conn, socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as relay:
                relay.settimeout(3); relay.bind(command['relay']); relay.listen(1)
                _print({'transfer_ready': True})
                receiver, _ = relay.accept()
                with receiver:
                    receiver.sendmsg([wire.encode(q)], [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                                                         struct.pack('i', conn.fileno()))])
                _print({'transferred': True})
        elif op == 'exit':
            _print({'exited': True}); return
        else: raise AssertionError('unsupported explicit candidate fixture action')


def _probe(config):
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as conn:
        conn.settimeout(2); conn.connect(config['liveness_socket'])
        try: response = conn.recv(1)
        except ConnectionResetError: response = b''
        assert response == b''
    _print({'unauthorized_closed_before_challenge': True})


def _receiver(relay):
    from polymarket_scanner.v11 import liveness_protocol as wire
    with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as connection:
        connection.settimeout(2); connection.connect(relay)
        raw, ancillary, flags, _ = connection.recvmsg(1025, socket.CMSG_SPACE(4), socket.MSG_CMSG_CLOEXEC)
        assert not flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
        assert len(ancillary) == 1 and ancillary[0][:2] == (socket.SOL_SOCKET, socket.SCM_RIGHTS)
        fd = struct.unpack('i', ancillary[0][2])[0]
        with socket.socket(fileno=fd) as transferred:
            transferred.settimeout(2); transferred.send(raw)
            try: result = transferred.recv(1)
            except ConnectionResetError: result = b''
            assert result == b''
        wire.validate_request(wire.decode(raw), wire.decode(raw)['config_sha256'])
    _print({'transferred_sender_refused': True})


def _guardian(config, operation):
    from polymarket_scanner.v11.guardian_protocol import BrokerPolicy, GuardianClient
    denied = _denials(config)
    client = GuardianClient(config['socket'], BrokerPolicy(**config['policy']), config['config'])
    started = time.monotonic()
    if operation in {'check', 'check-stopped'}:
        result = client.check('custody-'+operation); _print(dict(response=result, denied=denied)); return
    snapshot = client.snapshot('custody-snapshot')['result']
    assert set(snapshot['intents']) == {'proposal'}
    args = {key: snapshot[key] for key in ('snapshot_id', 'snapshot_sha256', 'intents')}
    response = client.cancel('custody-cancel', **args)
    assert response['outcome'] == 'REQUESTED_NOT_CONFIRMED'
    assert response['result']['terminal_confirmation'] is False
    assert response == client.cancel('custody-cancel', **args)
    _print(dict(cancelled=True, elapsed=time.monotonic()-started, denied=denied, financial_authority=False))


def run(context, payload):
    mode = payload['mode']
    assert mode in {'healthy', 'preempt', 'transfer', 'death', 'probe_preempt', 'probe_death', 'worker_stop'}
    prefix = [context.python, '-s', '-E', '-B', str(context.repository/'tests'/Path(__file__).name)]
    worker = context.spawn('candidate', prefix+['--candidate']); assert _line(worker) == {'ready': True}
    endpoint, pulse_endpoint = context.socket_directory/'g.sock', context.socket_directory/'p.sock'
    broker_args = prefix+['--broker', str(context.private['broker']), str(endpoint), str(pulse_endpoint), str(worker.pid)]
    broker = context.spawn('broker', broker_args+[mode]); config = _line(broker)
    listening = _line(broker)
    assert listening == {'listening': True}, dict(stage='both_broker_endpoints_listening', received=listening)
    _send(worker, dict(operation='configure', config=config))
    denied = _line(worker)['denied']
    expected_denials = {name+':'+action for name in
        ('paper.sqlite', 'paper.sqlite-wal', 'paper.sqlite-shm', 'fixture-config.json')
        for action in ('read', 'write')} | {'socket-unlink', 'socket-directory-rename', 'broker-sigterm'}
    assert len(denied) == len(expected_denials) and set(denied) == expected_denials, denied
    def role(role_name, arguments, configuration=None):
        process = context.spawn(role_name, prefix+arguments)
        if configuration is not None:
            _send(process, configuration); process.stdin.close(); process.stdin = None
        return _finish(process)
    def command(operation, **fields):
        _send(worker, dict(operation=operation, **fields)); return _line(worker)
    def cancel(): return role('guardian', ['--guardian', 'cancel'], config)
    proof = dict(actual_distinct_principals=True, mode=mode, financial_authority=False, production_commissioned=False)
    if mode in {'healthy', 'worker_stop'}:
        reply = command('pulse', request_id='first')
        assert 'response' in reply, dict(reply=reply, broker_exit=broker.poll(), resources=_diagnostics(config))
        first = reply['response']; time.sleep(.07)
        reply = command('pulse', request_id='second')
        assert 'response' in reply, dict(reply=reply, broker_exit=broker.poll(), resources=_diagnostics(config))
        second = reply['response']
        assert first['outcome'] == second['outcome'] == 'PUBLISHED'
        checked = role('guardian', ['--guardian', 'check'], config)
        rows = _rows(config)
        status = next(r for r in rows if r['id'] == checked['response']['result']['status_id'])
        assert status['body']['details']['status'] == 'READY', status['body']['details']['reasons']
        account_before = [r for r in rows if r['body'].get('details', {}).get('version') == 'alpha_v11_paper_coordinator_v1']
        assert account_before
        if mode == 'worker_stop':
            pulse_rows = [r for r in rows if r['id'].startswith('liveness-')]
            worker.send_signal(signal.SIGSTOP)
            try:
                deadline = time.monotonic()+1
                while True:
                    raw_stat = Path('/proc', str(worker.pid), 'stat').read_text()
                    if raw_stat[raw_stat.rindex(')')+2:].split()[0] == 'T': break
                    assert time.monotonic() < deadline; time.sleep(.005)
                # The actual candidate is stopped and has no helper heartbeat.
                # A distinct guardian process still operates its safety socket.
                stopped = role('guardian', ['--guardian', 'check-stopped'], config)
                after = _rows(config)
                status = next(r for r in after if r['id'] == stopped['response']['result']['status_id'])
                assert status['body']['details']['status'] == 'GATED'
                triggers = [r['body'].get('details', {}) for r in after if r['seq'] > rows[-1]['seq']]
                assert any('GUARDIAN_PROCESS_NOT_RUNNING' in d.get('reasons', [])
                           and set(d.get('cancel_intents', {})) == {'proposal'} for d in triggers)
                states = [r['body']['details']['state'] for r in after
                          if r['body'].get('details', {}).get('version') == 'alpha_v11_paper_coordinator_v1']
                assert states[-1]['intents']['proposal']['status'] == 'CANCEL_REQUESTED'
                assert states[-1]['intents']['proposal']['cancel_requested'] is True
                assert [r for r in after if r['id'].startswith('liveness-')] == pulse_rows
                proof.update(healthy_pair=True, stopped_candidate_cannot_pulse=True,
                             guardian_observed_stopped_process=True, autonomous_cancel_requested=True)
            finally:
                if worker.poll() is None: worker.send_signal(signal.SIGCONT)
        else:
            before = len(rows); time.sleep(.07)
            assert command('pulse', request_id='first')['response'] == first
            assert len(_rows(config)) == before, 'replay renewed old liveness'
            assert command('forbidden') == {'forbidden_closed': True}
            assert role('candidate', ['--probe', 'candidate'], config)['unauthorized_closed_before_challenge']
            assert role('guardian', ['--probe', 'guardian'], config)['unauthorized_closed_before_challenge']
            assert len(_rows(config)) == before, 'unauthorized or malformed pulse appended evidence'
            assert [r for r in _rows(config) if r['body'].get('details', {}).get('version') == 'alpha_v11_paper_coordinator_v1'] == account_before
            proof.update(healthy_pair=True, replay_did_not_renew=True, malformed_and_foreign_peer_no_append=True,
                         cancellation=cancel())
    elif mode == 'transfer':
        before = len(_rows(config)); relay = str(context.private['candidate']/'relay.sock')
        _send(worker, dict(operation='transfer', relay=relay)); assert _line(worker) == {'transfer_ready': True}
        assert role('candidate', ['--receiver', relay])['transferred_sender_refused']
        assert _line(worker) == {'transferred': True}
        assert len(_rows(config)) == before
        proof.update(transferred_socket_did_not_append=True, cancellation=cancel())
    else:
        assert command('start_busy') == {'busy_started': True}
        job = _line(broker)['job']; deadline = time.monotonic()+2; probe_pid = None
        while True:
            accepted = [r for r in _rows(config) if r['body'].get('details', {}).get('phase') == 'ACCEPTED'
                        and r['body'].get('details', {}).get('request', {}).get('request_id') == 'busy']
            marker = Path(config['private'], 'busy-probe-pid' if mode.startswith('probe_') else 'busy-writer-pid')
            if accepted and marker.exists():
                raw_marker = marker.read_text()
                if mode.startswith('probe_') and raw_marker:
                    probe = json.loads(raw_marker)
                    assert probe['parent'] == job
                    probe_pid = probe['pid']; break
                if raw_marker == str(job): break
            assert time.monotonic() < deadline, dict(stage='busy_publication_proof', broker_exit=broker.poll(),
                                                    resources=_diagnostics(config))
            time.sleep(.01)
        def reap_orphan(pid):
            deadline = time.monotonic()+2
            while True:
                reaped, status = os.waitpid(pid, os.WNOHANG)
                if reaped:
                    assert os.waitstatus_to_exitcode(status) == -signal.SIGKILL; break
                assert time.monotonic() < deadline; time.sleep(.01)
            assert not Path('/proc', str(pid)).exists()
        if mode in {'preempt', 'probe_preempt'}:
            assert command('start_flood') == {'flood_started': True}
            cancellation = cancel(); assert cancellation['elapsed'] < 1.5
            assert command('finish_busy') == {'publication_interrupted': True}
            assert not Path('/proc', str(job)).exists(), 'publication child not reaped'
            if probe_pid is not None: reap_orphan(probe_pid)
            proof.update(cancellation=cancellation, publication_preempted_and_reaped=True,
                         probe_preempted_and_reaped=probe_pid is not None)
        else:
            broker.kill(); out, err = broker.communicate(timeout=3)
            assert broker.returncode == -signal.SIGKILL and not out and not err
            reap_orphan(job)
            if probe_pid is not None: reap_orphan(probe_pid)
            assert command('finish_busy') == {'publication_interrupted': True}
            broker = context.spawn('broker', broker_args+['restart']); replacement = _line(broker)
            listening = _line(broker)
            assert listening == {'listening': True}, dict(stage='restart_endpoints_listening', received=listening)
            assert replacement['config'] == config['config'] and replacement['liveness_config'] == config['liveness_config']
            config = replacement
            assert command('pulse', request_id='busy') == {'refused': 'LIVENESS_REQUEST_REFUSED'}
            assert not [r for r in _rows(config) if r['id'].startswith('liveness-heartbeat:')]
            proof.update(parent_death_killed_writer=True, orphan_reaped=True,
                         historical_observation_refused=True, probe_parent_death_reaped=probe_pid is not None,
                         cancellation=cancel())
    proof['broker'] = _finish(broker)
    assert worker.poll() is None
    return proof


@pytest.mark.parametrize('mode', ['healthy', 'preempt', 'transfer', 'death', 'probe_preempt', 'probe_death', 'worker_stop'])
def test_actual_separate_principal_liveness_custody_and_recovery(mode):
    try: prerequisites()
    except NamespaceUnavailable as exc: pytest.skip('EXTERNAL_CUSTODY_GATE_UNAVAILABLE: '+str(exc))
    result = run_namespace_fixture(__file__, {'mode': mode}, timeout=40)
    assert result['status'] == 'PASSED' and result['setgroups'] == 'deny'
    assert {p['role'] for p in result['roles']} == {'broker', 'guardian', 'candidate'}
    assert result['result']['actual_distinct_principals'] is True
    assert result['result']['broker']['reservations_unchanged'] is True
    assert result['network_namespace'] != os.readlink('/proc/self/ns/net')


if __name__ == '__main__':
    mode = sys.argv[1]
    role = {'--candidate': 'candidate', '--broker': 'broker', '--guardian': 'guardian',
            '--receiver': 'candidate'}.get(mode, sys.argv[2] if mode == '--probe' else None)
    if role is None: raise SystemExit('unsupported explicit synthetic fixture role')
    _validate_role(_credentials(), role)
    if mode == '--candidate': _candidate()
    elif mode == '--broker': _broker(*sys.argv[2:])
    elif mode == '--receiver': _receiver(sys.argv[2])
    else:
        config = json.loads(sys.stdin.buffer.read(32769))
        if mode == '--probe': _probe(config)
        elif mode == '--guardian': _guardian(config, sys.argv[2])
