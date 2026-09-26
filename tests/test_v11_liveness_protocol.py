"""Pulse schemas and actual Linux per-packet credentials; no archive custody."""
from contextlib import contextmanager
import multiprocessing
import os
from pathlib import Path
import socket
import stat
import struct
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

from polymarket_scanner.v11 import liveness_protocol as protocol
from polymarket_scanner.v11.evidence import EvidenceError


CONFIG = 'a'*64
NONCE = 'b'*64
LINUX = all(hasattr(socket, name) for name in ('SO_PASSCRED', 'SO_PEERCRED', 'SOCK_SEQPACKET'))
linux = pytest.mark.skipif(not LINUX, reason='Linux authenticated packet sockets required')


def policy(**changes):
    values = dict(version='synthetic-pulse', worker='candidate', generation='generation', candidate_gid=1003)
    return protocol.ProducerPolicy(**dict(values, **changes))


def pulse():
    return protocol.request(CONFIG, 'pulse', NONCE)


def response(q=None, outcome='PUBLISHED'):
    q = pulse() if q is None else q
    result = {name+suffix: (name if suffix == '_id' else 'c'*64)
              for name in ('accepted', 'heartbeat', 'health') for suffix in ('_id', '_sha256')}
    if outcome == 'REFUSED': result = {'reason': 'INTERRUPTED_OBSERVATION'}
    return dict(**{key: q[key] for key in ('version', 'config_sha256', 'request_id', 'operation')},
                outcome=outcome, result=result, financial_authority=False)


@pytest.mark.parametrize('field,value', [
    ('version', ''), ('worker', False), ('generation', 'x'*81), ('generation', 'bad\ngeneration'),
    ('candidate_gid', 0), ('candidate_gid', -1), ('candidate_gid', True), ('candidate_gid', 1003.),
    ('candidate_gid', 2**31), ('minimum_interval_seconds', .049), ('minimum_interval_seconds', 5.01),
    ('minimum_interval_seconds', True), ('minimum_interval_seconds', float('nan')),
    ('publication_timeout_seconds', .099), ('publication_timeout_seconds', 4.01),
    ('publication_timeout_seconds', False), ('publication_timeout_seconds', float('inf')),
    ('maximum_pulses', 0), ('maximum_pulses', 129), ('maximum_pulses', True), ('maximum_pulses', 4.),
])
def test_policy_identity_types_and_resource_bounds(field, value):
    with pytest.raises(EvidenceError): policy(**{field: value})


@pytest.mark.parametrize('interval,timeout,count', [(.05, .1, 1), (5, 4, 128)])
def test_policy_accepts_exact_boundary_values(interval, timeout, count):
    p = policy(minimum_interval_seconds=interval, publication_timeout_seconds=timeout, maximum_pulses=count)
    assert p.minimum_interval_seconds == interval and p.publication_timeout_seconds == timeout
    assert p.maximum_pulses == count


def test_wire_challenge_and_immutable_semantic_request_are_separate():
    q = pulse(); protocol.validate_request(q, CONFIG)
    semantic = {k: v for k, v in q.items() if k != 'challenge'}
    protocol.validate_request(semantic, CONFIG, wire=False)
    with pytest.raises(EvidenceError): protocol.validate_request(semantic, CONFIG)
    with pytest.raises(EvidenceError): protocol.validate_request(q, CONFIG, wire=False)
    protocol.validate_response(response(q), q)
    protocol.validate_response(response(q), semantic)


@pytest.mark.parametrize('field,value', [
    ('version', 'other'), ('config_sha256', 'c'*64), ('request_id', False), ('request_id', 'x'*81),
    ('operation', 'CANCEL'), ('operation', 'NEW_ORDER'), ('operation', 'SQL'), ('operation', None),
    ('challenge', 'B'*64), ('challenge', 'b'*63), ('challenge', False), ('extra', 'authority'),
])
def test_pulse_accepts_no_operation_payload_or_identity_expansion(field, value):
    q = pulse(); q[field] = value
    with pytest.raises(EvidenceError): protocol.validate_request(q, CONFIG)


@pytest.mark.parametrize('value', [None, [], {}, {'version': protocol.VERSION, 'config_sha256': CONFIG},
    {'version': 'other', 'config_sha256': CONFIG, 'challenge': NONCE},
    {'version': protocol.VERSION, 'config_sha256': 'd'*64, 'challenge': NONCE},
    {'version': protocol.VERSION, 'config_sha256': CONFIG, 'challenge': False},
    {'version': protocol.VERSION, 'config_sha256': CONFIG, 'challenge': 'B'*64},
    {'version': protocol.VERSION, 'config_sha256': CONFIG, 'challenge': NONCE, 'clock': 1},
])
def test_challenge_is_exact_config_bound_and_unambiguous(value):
    with pytest.raises(EvidenceError): protocol.validate_challenge(value, CONFIG)


@pytest.mark.parametrize('field,value', [
    ('version', 'other'), ('config_sha256', 'd'*64), ('request_id', 'other'), ('operation', 'CANCEL'),
    ('financial_authority', True), ('financial_authority', 0), ('outcome', 'READY'),
    ('outcome', 'PENDING'), ('result', []), ('extra', None), ('challenge', NONCE),
])
def test_response_is_exact_request_bound_and_has_no_financial_authority(field, value):
    r = response(); r[field] = value
    with pytest.raises(EvidenceError): protocol.validate_response(r, pulse())


@pytest.mark.parametrize('name', ['accepted', 'heartbeat', 'health'])
@pytest.mark.parametrize('suffix,value', [('_id', False), ('_id', ''), ('_sha256', 'C'*64), ('_sha256', None)])
def test_response_provenance_requires_valid_record_identity_and_hash(name, suffix, value):
    r = response(); r['result'][name+suffix] = value
    with pytest.raises(EvidenceError): protocol.validate_response(r, pulse())


def test_response_cannot_smuggle_state_or_request_a_favorable_retry():
    r = response(); r['result']['state'] = 'READY'
    with pytest.raises(EvidenceError): protocol.validate_response(r, pulse())
    r = response(outcome='REFUSED'); protocol.validate_response(r, pulse())
    r['result']['reason'] = 'RETRY_WITH_AUTHORITY'
    with pytest.raises(EvidenceError): protocol.validate_response(r, pulse())


def test_packet_encoding_is_canonical_bounded_and_has_no_stream_header():
    q = pulse(); raw = protocol.encode(q)
    assert raw.startswith(b'{') and protocol.decode(raw) == q
    assert protocol.encode({'x': 'a'*(protocol.MAX_PULSE_BYTES-8)}) == b'{"x":"'+b'a'*1016+b'"}'
    with pytest.raises(EvidenceError, match='PACKET_BOUND'): protocol.encode({'x': 'a'*1017})


@contextmanager
def packet_pair(*, credentials=True):
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    with left, right:
        left.settimeout(1); right.settimeout(1)
        if credentials: left.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        peer = protocol.peer_identity(left, uid=os.getuid(), gid=os.getgid())
        yield left, right, peer


@linux
def test_kernel_credential_packet_is_decoded_and_nonblocking_empty_read_is_preserved():
    with packet_pair() as (left, right, peer):
        left.setblocking(False)
        with pytest.raises(BlockingIOError): protocol.receive_pulse(left, peer)
        right.send(protocol.encode(pulse()))
        assert protocol.receive_pulse(left, peer) == pulse()


@linux
@pytest.mark.parametrize('raw,reason', [
    (b'', 'PACKET_BOUND'), (b'x'*1025, 'PACKET_BOUND'), (b'x'*2048, 'PACKET_TRUNCATED'),
    (b'{"x":1,"x":2}', 'DUPLICATE_JSON_KEY'), (b'{', 'INVALID_JSON'),
])
def test_actual_packet_rejects_empty_oversized_or_ambiguous_content(raw, reason):
    with packet_pair() as (left, right, peer):
        right.send(raw)
        with pytest.raises(EvidenceError, match=reason): protocol.receive_pulse(left, peer)


@linux
def test_missing_per_packet_credentials_fails_closed():
    with packet_pair(credentials=False) as (left, right, peer):
        right.send(protocol.encode(pulse()))
        with pytest.raises(EvidenceError, match='PACKET_CREDENTIALS'): protocol.receive_pulse(left, peer)


def _child_send(conn):
    conn.send(protocol.encode(pulse())); conn.close()


@linux
def test_inherited_connection_cannot_claim_the_original_process_liveness():
    with packet_pair() as (left, right, peer):
        child = multiprocessing.get_context('fork').Process(target=_child_send, args=(right,))
        child.start()
        try:
            with pytest.raises(EvidenceError, match='PACKET_PEER_CHANGED'): protocol.receive_pulse(left, peer)
            child.join(2); assert not child.is_alive() and child.exitcode == 0
        finally:
            if child.is_alive(): child.terminate(); child.join(2)


@linux
@pytest.mark.parametrize('count,reason', [(1, 'PACKET_CREDENTIALS'), (64, 'PACKET_TRUNCATED')])
def test_received_descriptors_are_closed_even_when_ancillary_is_truncated(monkeypatch, count, reason):
    closed = []; actual_close = os.close
    def close(fd):
        closed.append(fd); actual_close(fd)
    with packet_pair() as (left, right, peer), open(os.devnull, 'rb') as original:
        right.sendmsg([protocol.encode(pulse())],
                      [(socket.SOL_SOCKET, socket.SCM_RIGHTS, struct.pack(f'{count}i', *([original.fileno()]*count)))])
        with monkeypatch.context() as patch:
            patch.setattr(protocol.os, 'close', close)
            with pytest.raises(EvidenceError, match=reason): protocol.receive_pulse(left, peer)
        assert closed and len(closed) <= protocol._MAX_RIGHTS+3
        for descriptor in closed:
            with pytest.raises(OSError): os.fstat(descriptor)
        os.fstat(original.fileno())


@linux
@pytest.mark.parametrize('mutation', ['missing', 'duplicate', 'short', 'other', 'pid', 'uid', 'gid'])
def test_exactly_one_complete_matching_credential_is_required(monkeypatch, mutation):
    peer = dict(pid=123, uid=1001, gid=1003, start_ticks=4, boot_id='synthetic')
    values = [123, 1001, 1003]
    if mutation in ('pid', 'uid', 'gid'): values[('pid', 'uid', 'gid').index(mutation)] += 1
    ancillary = [(socket.SOL_SOCKET, socket.SCM_CREDENTIALS, struct.pack('3i', *values))]
    if mutation == 'missing': ancillary = []
    elif mutation == 'duplicate': ancillary *= 2
    elif mutation == 'short': ancillary[0] = (socket.SOL_SOCKET, socket.SCM_CREDENTIALS, b'\0'*8)
    elif mutation == 'other': ancillary.append((socket.SOL_SOCKET, 999999, b''))
    monkeypatch.setattr(protocol, '_packet', lambda _: (protocol.encode(pulse()), ancillary))
    monkeypatch.setattr(protocol, 'check_peer', lambda *args: pytest.fail('invalid credentials reached peer check'))
    with pytest.raises(EvidenceError): protocol.receive_pulse(object(), peer)


@contextmanager
def local_server(*, challenge=None, refuse=False, response_delay=0):
    observed = []; failures = []
    with tempfile.TemporaryDirectory(prefix='v11-pulse-') as directory:
        path = Path(directory)/'p.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            listener.settimeout(2); listener.bind(str(path)); listener.listen(1)
            def serve():
                try:
                    conn, _ = listener.accept()
                    with conn:
                        conn.settimeout(2)
                        peer = protocol.peer_identity(conn, uid=os.getuid(), gid=os.getgid())
                        hello = dict(version=protocol.VERSION, config_sha256=CONFIG, challenge=NONCE)
                        conn.send(protocol.encode(hello if challenge is None else challenge))
                        if challenge is not None: return
                        q = protocol.receive_pulse(conn, peer); protocol.validate_request(q, CONFIG)
                        assert q['challenge'] == NONCE; observed.append((peer, q))
                        if response_delay: time.sleep(response_delay)
                        try: conn.send(protocol.encode(response(q, 'REFUSED' if refuse else 'PUBLISHED')))
                        except BrokenPipeError:
                            if not response_delay: raise
                except BaseException as exc: failures.append(exc)
            thread = threading.Thread(target=serve, daemon=True); thread.start()
            try: yield path, observed
            finally:
                thread.join(3)
                assert not thread.is_alive(), 'bounded synthetic pulse server failed to exit'
                if failures: raise failures[0]


def client(path, **changes):
    return protocol.LivenessClient(path, broker_uid=os.getuid(), broker_gid=os.getgid(),
                                   config=CONFIG, **changes)


@linux
def test_actual_client_publishes_only_after_authenticated_challenge():
    with local_server() as (path, observed):
        result = client(path).pulse('pulse')
    assert result == response() and observed[0][1] == pulse()
    assert observed[0][0]['pid'] == os.getpid()


@linux
def test_client_refusal_does_not_fall_back_to_an_archive_or_financial_operation():
    with local_server(refuse=True) as (path, observed):
        with pytest.raises(EvidenceError, match='REQUEST_REFUSED'): client(path).pulse('pulse')
    assert len(observed) == 1


@linux
def test_client_refuses_malformed_challenge_before_sending_a_pulse():
    with local_server(challenge={'challenge': NONCE}) as (path, observed):
        with pytest.raises(EvidenceError, match='CHALLENGE_SCHEMA'): client(path).pulse('pulse')
    assert observed == []


@linux
def test_client_response_wait_uses_the_original_bounded_deadline():
    with local_server(response_delay=.2) as (path, observed):
        started = time.monotonic()
        with pytest.raises(TimeoutError): client(path, timeout_seconds=.05).pulse('pulse')
        assert time.monotonic()-started < 1
    assert len(observed) == 1


@linux
def test_challenge_wait_and_send_share_one_absolute_deadline(monkeypatch):
    ticks = iter((100., 100.01, 100.03, 100.06))
    monkeypatch.setattr(protocol.time, 'monotonic', lambda: next(ticks))
    info = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=1001, st_dev=1, st_ino=2)
    monkeypatch.setattr(protocol, 'endpoint_parent', lambda *args: SimpleNamespace(lstat=lambda: info))
    sent = []; timeouts = []
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): timeouts.append(value)
        def connect(self, value): pass
        def send(self, raw): sent.append(raw); return len(raw)
    monkeypatch.setattr(protocol.socket, 'socket', lambda *args: Conn())
    monkeypatch.setattr(protocol, 'peer_identity', lambda *args, **kwargs: {})
    monkeypatch.setattr(protocol, 'check_peer', lambda *args: None)
    hello = dict(version=protocol.VERSION, config_sha256=CONFIG, challenge=NONCE)
    monkeypatch.setattr(protocol, '_packet', lambda *args: (protocol.encode(hello), []))
    with pytest.raises(EvidenceError, match='CONNECTION_DEADLINE'):
        protocol.LivenessClient('/synthetic', broker_uid=1001, broker_gid=1001,
                                config=CONFIG, timeout_seconds=.05).pulse('pulse')
    assert sent == [] and timeouts == pytest.approx([.04, .02])


@linux
@pytest.mark.parametrize('change_at', [2, 3])
def test_client_rechecks_protected_endpoint_before_pulse_and_after_receipt(monkeypatch, change_at):
    count = 0
    def lstat():
        nonlocal count
        count += 1
        return SimpleNamespace(st_mode=stat.S_IFSOCK | 0o660, st_uid=1001,
                               st_dev=1, st_ino=3 if count >= change_at else 2)
    monkeypatch.setattr(protocol, 'endpoint_parent', lambda *args: SimpleNamespace(lstat=lstat))
    sent = []
    class Conn:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def settimeout(self, value): pass
        def connect(self, value): pass
        def send(self, raw): sent.append(raw); return len(raw)
    monkeypatch.setattr(protocol.socket, 'socket', lambda *args: Conn())
    monkeypatch.setattr(protocol, 'peer_identity', lambda *args, **kwargs: {})
    monkeypatch.setattr(protocol, 'check_peer', lambda *args: None)
    packets = iter([dict(version=protocol.VERSION, config_sha256=CONFIG, challenge=NONCE), response()])
    monkeypatch.setattr(protocol, '_packet', lambda *args: (protocol.encode(next(packets)), []))
    with pytest.raises(EvidenceError, match='SOCKET_CHANGED'):
        protocol.LivenessClient('/synthetic', broker_uid=1001, broker_gid=1001, config=CONFIG).pulse('pulse')
    assert len(sent) == (0 if change_at == 2 else 1)
