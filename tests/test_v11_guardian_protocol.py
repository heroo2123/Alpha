"""Bounded local safety transport; no broker ledger or financial operations."""
from contextlib import contextmanager
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

from polymarket_scanner.v11 import guardian_protocol as protocol
from polymarket_scanner.v11.evidence import EvidenceError, canonical


CONFIG = 'a'*64
SIGNATURE = 'b'*64


def policy(**changes):
    values = dict(version='synthetic-protocol', broker_uid=1001, broker_gid=1001,
                  guardian_uid=1002, guardian_gid=1002)
    return protocol.BrokerPolicy(**dict(values, **changes))


def cancel_request():
    return protocol.request(CONFIG, 'cancel-request', 'CANCEL', snapshot_id='snapshot',
                            snapshot_sha256='c'*64, intents={'intent': SIGNATURE})


@pytest.mark.parametrize('field,value', [
    ('broker_uid', 0), ('broker_uid', True), ('broker_gid', '1001'),
    ('guardian_uid', -1), ('guardian_gid', 2**31), ('guardian_gid', 1002.),
    ('guardian_uid', 1001), ('identity_mode', 'UNREVIEWED'),
    ('timeout_seconds', True), ('timeout_seconds', .01), ('timeout_seconds', 3),
    ('timeout_seconds', float('nan')), ('maximum_intents', False),
    ('maximum_intents', 0), ('maximum_intents', 17),
])
def test_policy_rejects_invalid_types_identities_and_budgets(field, value):
    with pytest.raises(EvidenceError):
        policy(**{field: value})


def test_same_uid_requires_explicit_synthetic_mechanics_mode():
    p = policy(guardian_uid=1001, identity_mode='SYNTHETIC_SAME_UID_MECHANICS')
    assert p.broker_uid == p.guardian_uid
    assert p.identity_mode == 'SYNTHETIC_SAME_UID_MECHANICS'


@pytest.mark.parametrize('raw,reason', [
    (b'', 'FRAME_BOUND'), (b'x'*(protocol.MAX_FRAME+1), 'FRAME_BOUND'),
    (b'{"x":1,"x":2}', 'DUPLICATE_JSON_KEY'),
    (b'{"outer":{"x":1,"x":2}}', 'DUPLICATE_JSON_KEY'),
    (b'{"x":NaN}', 'NONFINITE_JSON'), (b'{"x":Infinity}', 'NONFINITE_JSON'),
    (b'{"x":-Infinity}', 'NONFINITE_JSON'),
    (b'{"x":1e999}', 'INVALID_JSON'), (b'{', 'INVALID_JSON'),
    (b'{"x":"\xff"}', 'INVALID_JSON'),
    (b'[]', 'OBJECT_REQUIRED'), (b'null', 'OBJECT_REQUIRED'),
    (b'false', 'OBJECT_REQUIRED'), (b'1', 'OBJECT_REQUIRED'),
])
def test_decode_refuses_ambiguous_nonfinite_malformed_or_nonobject_frames(raw, reason):
    with pytest.raises(EvidenceError, match=reason):
        protocol.decode(raw)


@pytest.mark.parametrize('depth', [26, 1500])
def test_decode_refuses_excessive_nesting(depth):
    raw = b'{"x":'*depth+b'0'+b'}'*depth
    with pytest.raises(EvidenceError):
        protocol.decode(raw)


def test_decode_boundary_accepts_one_complete_bounded_object():
    raw = b'{"x":"'+b'a'*(protocol.MAX_FRAME-8)+b'"}'
    assert len(raw) == protocol.MAX_FRAME
    assert protocol.decode(raw) == {'x': 'a'*(protocol.MAX_FRAME-8)}


@pytest.mark.parametrize('value', [
    protocol.request(CONFIG, 'snapshot', 'SNAPSHOT', cursor=''),
    protocol.request(CONFIG, 'snapshot-next', 'SNAPSHOT', cursor='intent'),
    protocol.request(CONFIG, 'check', 'CHECK'), cancel_request(),
])
def test_only_exact_bounded_safety_requests_are_accepted(value):
    protocol.validate_request(value, policy(), CONFIG)
    assert protocol.decode(canonical(value).encode()) == value


@pytest.mark.parametrize('operation', [
    'PLACE_ORDER', 'SUBMITTING', 'FILL', 'TERMINAL', 'WITHDRAW', 'CREATE_WALLET',
    'SET_STATE', 'SQL', 'EXEC', 'PROMOTE', None, 42,
])
def test_financial_mutation_or_arbitrary_dispatch_operations_are_refused(operation):
    value = protocol.request(CONFIG, 'refused', operation)
    with pytest.raises(EvidenceError, match='CANCEL_ONLY_OPERATIONS'):
        protocol.validate_request(value, policy(), CONFIG)


@pytest.mark.parametrize('field,value', [
    ('version', 'unsupported'), ('config_sha256', 'd'*64),
    ('request_id', None), ('request_id', False), ('request_id', 'x'*81),
    ('snapshot_id', False), ('snapshot_sha256', 'B'*64),
    ('snapshot_sha256', None), ('intents', []), ('intents', {}),
    ('intents', {str(i): SIGNATURE for i in range(9)}),
    ('intents', {1: SIGNATURE}), ('intents', {'intent': False}),
])
def test_cancel_schema_identity_and_target_bounds(field, value):
    request = cancel_request(); request[field] = value
    with pytest.raises(EvidenceError):
        protocol.validate_request(request, policy(), CONFIG)


@pytest.mark.parametrize('cursor', [None, False, 0, [], {}, 'x'*161, 'bad\ncursor'])
def test_snapshot_cursor_has_an_exact_string_type_and_bound(cursor):
    request = protocol.request(CONFIG, 'snapshot', 'SNAPSHOT', cursor=cursor)
    with pytest.raises(EvidenceError):
        protocol.validate_request(request, policy(), CONFIG)


@pytest.mark.parametrize('operation,extra', [
    ('CHECK', {'state': {}}), ('CHECK', {'status': 'READY'}),
    ('SNAPSHOT', {'cursor': '', 'database': '/unapproved'}),
    ('CANCEL', {'snapshot_id': 'snapshot', 'snapshot_sha256': 'c'*64,
                'intents': {'intent': SIGNATURE}, 'status': 'SUBMITTING'}),
])
def test_request_fields_cannot_expand_the_fixed_operation_schema(operation, extra):
    with pytest.raises(EvidenceError, match='REQUEST_SCHEMA'):
        protocol.validate_request(protocol.request(CONFIG, 'request', operation, **extra), policy(), CONFIG)


class FragmentSocket:
    def __init__(self, data=b'', chunk=3):
        self.data = bytearray(data); self.chunk = chunk
        self.timeouts = []; self.sent = []; self.receives = 0

    def settimeout(self, value): self.timeouts.append(value)

    def recv(self, size):
        self.receives += 1
        result = bytes(self.data[:min(size, self.chunk)])
        del self.data[:len(result)]
        return result

    def sendall(self, value): self.sent.append(value)

    def connect(self, _): pass

    def __enter__(self): return self

    def __exit__(self, *args): pass


def frame(value):
    raw = canonical(value).encode()
    return struct.pack('!I', len(raw))+raw


def test_fragmented_header_and_body_reassemble_and_send_one_length_prefixed_frame():
    value = cancel_request(); source = FragmentSocket(frame(value), chunk=1)
    assert protocol.receive(source, time.monotonic()+2) == value
    assert source.receives > 4 and all(0 < t <= 2 for t in source.timeouts)
    target = FragmentSocket(); protocol.send(target, value, time.monotonic()+2)
    assert target.sent == [frame(value)]


@pytest.mark.parametrize('raw', [b'', b'\0', b'\0\0\0', struct.pack('!I', 8)+b'{}'])
def test_disconnect_in_header_or_body_is_explicit(raw):
    with pytest.raises(EvidenceError, match='DISCONNECTED_FRAME'):
        protocol.receive(FragmentSocket(raw), time.monotonic()+2)


@pytest.mark.parametrize('size', [0, protocol.MAX_FRAME+1, 2**32-1])
def test_invalid_advertised_frame_size_is_rejected_before_reading_body(size):
    source = FragmentSocket(struct.pack('!I', size)+b'body', chunk=4)
    with pytest.raises(EvidenceError, match='FRAME_BOUND'):
        protocol.receive(source, time.monotonic()+2)
    assert source.receives == 1 and source.data == b'body'


def test_oversized_output_is_rejected_before_socket_write():
    target = FragmentSocket()
    with pytest.raises(EvidenceError, match='FRAME_BOUND'):
        protocol.send(target, {'x': 'x'*protocol.MAX_FRAME}, time.monotonic()+2)
    assert not target.sent and not target.timeouts


def test_one_absolute_deadline_covers_every_fragment(monkeypatch):
    moments = iter((0., .1, .3))
    monkeypatch.setattr(protocol.time, 'monotonic', lambda: next(moments))
    source = FragmentSocket(frame({'x': 1}), chunk=1)
    with pytest.raises(EvidenceError, match='CONNECTION_DEADLINE'):
        protocol.receive(source, .25)
    assert source.receives == 2 and source.timeouts == [.25, .15]


def test_expired_deadline_cannot_start_a_socket_write(monkeypatch):
    monkeypatch.setattr(protocol.time, 'monotonic', lambda: 10.)
    target = FragmentSocket()
    with pytest.raises(EvidenceError, match='CONNECTION_DEADLINE'):
        protocol.send(target, {}, 10.)
    assert not target.sent


def test_actual_local_socket_read_timeout_is_bounded():
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with left, right:
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            protocol.receive(left, started+.03)
        assert time.monotonic()-started < 1


class PeerSocket:
    def __init__(self, raw): self.raw = raw

    def getsockopt(self, *args):
        assert args == (socket.SOL_SOCKET, socket.SO_PEERCRED, 12)
        if isinstance(self.raw, OSError): raise self.raw
        return self.raw


def process(pid=123, uid=1001):
    return dict(pid=pid, uid=uid, start_ticks=456, boot_id='a'*36)


@pytest.mark.parametrize('raw', [b'', b'\0'*8, b'\0'*16, OSError('synthetic kernel failure')])
def test_malformed_or_unavailable_kernel_peer_credentials_fail_closed(raw):
    with pytest.raises(EvidenceError, match='PEER_IDENTITY_UNAVAILABLE'):
        protocol.peer_identity(PeerSocket(raw), uid=1001, gid=1001)


@pytest.mark.parametrize('actual_uid,actual_gid', [(1002, 1001), (1001, 1002)])
def test_kernel_identity_must_match_both_configured_uid_and_gid(monkeypatch, actual_uid, actual_gid):
    monkeypatch.setattr(protocol, 'process_identity', lambda _: pytest.fail('unauthorized peer was inspected'))
    with pytest.raises(EvidenceError, match='PEER_UNAUTHORIZED'):
        protocol.peer_identity(PeerSocket(struct.pack('3i', 123, actual_uid, actual_gid)), uid=1001, gid=1001)


@pytest.mark.parametrize('kind',['uid','gid'])
def test_configured_overflow_identity_is_ambiguous_even_if_process_owner_would_match(monkeypatch,kind):
    monkeypatch.setattr(protocol.Path,'read_text',lambda path:'1001' if path.name=='overflow'+kind else '65534')
    monkeypatch.setattr(protocol,'process_identity',lambda _:pytest.fail('ambiguous peer was inspected'))
    with pytest.raises(EvidenceError,match='PEER_OVERFLOW_IDENTITY'):
        protocol.peer_identity(PeerSocket(struct.pack('3i',123,1001,1001)),uid=1001,gid=1001)


@pytest.mark.parametrize('value',['','unknown','-1','+1','1 2','4294967296','9'*20,OSError('unavailable')])
def test_missing_or_malformed_kernel_overflow_identity_fails_closed(monkeypatch,value):
    def read(_):
        if isinstance(value,OSError):raise value
        return value
    monkeypatch.setattr(protocol.Path,'read_text',read)
    monkeypatch.setattr(protocol,'process_identity',lambda _:pytest.fail('ambiguous peer was inspected'))
    with pytest.raises(EvidenceError,match='PEER_IDENTITY_UNAVAILABLE'):
        protocol.peer_identity(PeerSocket(struct.pack('3i',123,1001,1001)),uid=1001,gid=1001)


def test_process_identity_is_bound_to_kernel_peer_and_rechecked(monkeypatch):
    peer = process(); monkeypatch.setattr(protocol, 'process_identity', lambda pid: dict(peer))
    conn = PeerSocket(struct.pack('3i', 123, 1001, 1001))
    original = protocol.peer_identity(conn, uid=1001, gid=1001)
    assert original == dict(peer, gid=1001)
    protocol.check_peer(conn, original)
    peer['start_ticks'] += 1
    with pytest.raises(EvidenceError, match='PEER_IDENTITY_CHANGED'):
        protocol.check_peer(conn, original)
    peer['uid'] = 1002
    with pytest.raises(EvidenceError, match='PEER_IDENTITY_AMBIGUOUS'):
        protocol.peer_identity(conn, uid=1001, gid=1001)


@pytest.mark.skipif(not hasattr(socket, 'SO_PEERCRED'), reason='Linux kernel peer credentials required')
def test_actual_unix_socketpair_reports_kernel_process_identity():
    left, right = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with left, right:
        peer = protocol.peer_identity(left, uid=os.getuid(), gid=os.getgid())
        assert peer['pid'] == os.getpid() and peer['uid'] == os.getuid()
        assert peer['gid'] == os.getgid() and peer['start_ticks'] > 0
        protocol.check_peer(left, peer)


@pytest.mark.parametrize('path', ['relative.sock', '/tmp/../protocol.sock', '/tmp/'+'x'*101])
def test_endpoint_rejects_relative_traversing_or_oversized_paths(path):
    with pytest.raises(EvidenceError, match='SOCKET_PATH_BOUND'):
        protocol.endpoint_parent(path, 1001)


def test_endpoint_parent_requires_actual_owner_private_directory_and_no_symlink():
    with tempfile.TemporaryDirectory(prefix='v11-protocol-') as directory:
        root = Path(directory)
        assert protocol.endpoint_parent(root/'s.sock', os.getuid()) == root/'s.sock'
        with pytest.raises(EvidenceError, match='PARENT_CUSTODY'):
            protocol.endpoint_parent(root/'s.sock', os.getuid()+1)
        actual = root/'actual'; actual.mkdir(mode=0o700)
        alias = root/'alias'; alias.symlink_to(actual, target_is_directory=True)
        with pytest.raises(EvidenceError, match='PARENT_CUSTODY'):
            protocol.endpoint_parent(alias/'s.sock', os.getuid())
        actual.chmod(0o777)
        with pytest.raises(EvidenceError, match='PARENT_CUSTODY'):
            protocol.endpoint_parent(actual/'s.sock', os.getuid())


def response(value, outcome='CHECKED'):
    result = {'status_id': 'synthetic-status'}
    if outcome == 'REFUSED':
        result = {'reason': 'INTERRUPTED_OBSERVATION'}
    elif outcome == 'SNAPSHOT':
        result = dict(snapshot_id='synthetic-snapshot', snapshot_sha256='c'*64,
                      snapshot_seq=1, intents={'intent': SIGNATURE}, next_cursor='intent', remaining=False)
    elif outcome == 'REQUESTED_NOT_CONFIRMED':
        result = dict(intents=value['intents'], terminal_confirmation=False,
                      account_snapshot_id='synthetic-snapshot', account_snapshot_sha256='c'*64)
    return dict(**{k: value[k] for k in ('version', 'config_sha256', 'request_id', 'operation')},
                outcome=outcome, result=result, financial_authority=False)


def mocked_client(monkeypatch, answer):
    attributes = SimpleNamespace(st_mode=stat.S_IFSOCK | 0o600, st_uid=1001, st_dev=1, st_ino=2)
    path = SimpleNamespace(lstat=lambda: attributes)
    conn = FragmentSocket()
    monkeypatch.setattr(protocol, 'endpoint_parent', lambda *args: path)
    monkeypatch.setattr(protocol.socket, 'socket', lambda *args: conn)
    monkeypatch.setattr(protocol, 'peer_identity', lambda *args, **kw: dict(process(), gid=1001))
    monkeypatch.setattr(protocol, 'check_peer', lambda *args: None)
    monkeypatch.setattr(protocol, 'send', lambda *args: None)
    monkeypatch.setattr(protocol, 'receive', lambda *args: answer)
    return protocol.GuardianClient('/unused-protocol-fixture', policy(), CONFIG)


@pytest.mark.parametrize('mutation', ['version', 'config_sha256', 'request_id', 'operation',
    'authority_true', 'authority_zero', 'result', 'outcome', 'missing', 'extra'])
def test_client_binds_response_to_exact_request_and_nonauthority_schema(monkeypatch, mutation):
    value = protocol.request(CONFIG, 'check', 'CHECK'); answer = response(value)
    if mutation in {'version', 'config_sha256', 'request_id', 'operation'}: answer[mutation] = 'different'
    elif mutation == 'authority_true': answer['financial_authority'] = True
    elif mutation == 'authority_zero': answer['financial_authority'] = 0
    elif mutation == 'result': answer['result'] = []
    elif mutation == 'outcome': answer['outcome'] = 'ORDERS_PLACED'
    elif mutation == 'missing': answer.pop('result')
    else: answer['extra'] = 'unrecognized'
    with pytest.raises(EvidenceError, match='RESPONSE_BINDING'):
        mocked_client(monkeypatch, answer).check('check')


def test_client_turns_explicit_refusal_into_failure(monkeypatch):
    value = protocol.request(CONFIG, 'check', 'CHECK')
    with pytest.raises(EvidenceError, match='REQUEST_REFUSED'):
        mocked_client(monkeypatch, response(value, 'REFUSED')).check('check')


@pytest.mark.parametrize('outcome', ['SNAPSHOT', 'PENDING'])
def test_client_rejects_an_outcome_that_does_not_complete_the_requested_operation(monkeypatch, outcome):
    value = protocol.request(CONFIG, 'check', 'CHECK')
    with pytest.raises(EvidenceError, match='RESPONSE_OPERATION'):
        mocked_client(monkeypatch, response(value, outcome)).check('check')


@pytest.mark.parametrize('operation,outcome', [
    ('CHECK', 'CHECKED'), ('SNAPSHOT', 'SNAPSHOT'), ('CANCEL', 'REQUESTED_NOT_CONFIRMED')])
def test_client_accepts_exact_typed_results_for_each_safety_operation(monkeypatch, operation, outcome):
    value = (cancel_request() if operation == 'CANCEL' else
             protocol.request(CONFIG, 'check', operation, **({'cursor': ''} if operation == 'SNAPSHOT' else {})))
    answer = response(value, outcome)
    assert mocked_client(monkeypatch, answer)._call(value) == answer


@pytest.mark.parametrize('operation,outcome,field,invalid', [
    ('CHECK', 'CHECKED', 'status_id', False),
    ('CHECK', 'CHECKED', 'status_id', ''),
    ('CHECK', 'CHECKED', 'extra', 1),
    ('CHECK', 'REFUSED', 'reason', 'RETRY_WITH_AUTHORITY'),
    ('SNAPSHOT', 'SNAPSHOT', 'snapshot_id', None),
    ('SNAPSHOT', 'SNAPSHOT', 'snapshot_sha256', 'C'*64),
    ('SNAPSHOT', 'SNAPSHOT', 'snapshot_seq', True),
    ('SNAPSHOT', 'SNAPSHOT', 'snapshot_seq', 0),
    ('SNAPSHOT', 'SNAPSHOT', 'remaining', 1),
    ('SNAPSHOT', 'SNAPSHOT', 'next_cursor', None),
    ('SNAPSHOT', 'SNAPSHOT', 'intents', []),
    ('SNAPSHOT', 'SNAPSHOT', 'intents', {str(i): SIGNATURE for i in range(9)}),
    ('SNAPSHOT', 'SNAPSHOT', 'intents', {'intent': 'invalid'}),
    ('CANCEL', 'REQUESTED_NOT_CONFIRMED', 'terminal_confirmation', True),
    ('CANCEL', 'REQUESTED_NOT_CONFIRMED', 'terminal_confirmation', 0),
    ('CANCEL', 'REQUESTED_NOT_CONFIRMED', 'intents', {'other-intent': SIGNATURE}),
    ('CANCEL', 'REQUESTED_NOT_CONFIRMED', 'account_snapshot_id', False),
    ('CANCEL', 'REQUESTED_NOT_CONFIRMED', 'account_snapshot_sha256', 'invalid'),
])
def test_client_refuses_malformed_result_identity_types_or_authority(monkeypatch, operation, outcome, field, invalid):
    value = (cancel_request() if operation == 'CANCEL' else
             protocol.request(CONFIG, 'check', operation, **({'cursor': ''} if operation == 'SNAPSHOT' else {})))
    answer = response(value, outcome); answer['result'][field] = invalid
    with pytest.raises(EvidenceError):
        mocked_client(monkeypatch, answer)._call(value)


@contextmanager
def local_server():
    """One local authenticated exchange, with bounded cleanup on either failure."""
    observed = []; failures = []
    with tempfile.TemporaryDirectory(prefix='v11-protocol-') as directory:
        path = Path(directory)/'s.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.settimeout(2); listener.bind(str(path)); listener.listen(1)
            def serve():
                try:
                    connection, _ = listener.accept()
                    with connection:
                        peer = protocol.peer_identity(connection, uid=os.getuid(), gid=os.getgid())
                        value = protocol.receive(connection, time.monotonic()+2)
                        protocol.validate_request(value, policy(), CONFIG)
                        observed.append((peer, value))
                        protocol.send(connection, response(value), time.monotonic()+2)
                except BaseException as exc:
                    failures.append(exc)
            thread = threading.Thread(target=serve, daemon=True); thread.start()
            try:
                yield path, observed
            finally:
                thread.join(timeout=3)
                assert not thread.is_alive(), 'local protocol server failed to finish'
                if failures: raise failures[0]


@pytest.mark.skipif(not hasattr(socket, 'SO_PEERCRED') or not hasattr(os, 'getuid')
    or os.getuid() == 0 or os.getgid() == 0,
    reason='Actual client policy requires a nonroot Linux test principal')
def test_actual_client_and_server_authenticate_each_other_over_local_unix_socket():
    p = policy(broker_uid=os.getuid(), broker_gid=os.getgid(), guardian_uid=os.getuid(),
               guardian_gid=os.getgid(), identity_mode='SYNTHETIC_SAME_UID_MECHANICS')
    with local_server() as (path, observed):
        result = protocol.GuardianClient(path, p, CONFIG).check('check')
    assert result['outcome'] == 'CHECKED' and result['financial_authority'] is False
    assert observed[0][0]['pid'] == os.getpid()
    assert observed[0][1] == protocol.request(CONFIG, 'check', 'CHECK')
