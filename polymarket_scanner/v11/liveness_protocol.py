"""Bounded authenticated candidate pulses, without order or ledger authority."""
from dataclasses import dataclass
import os
from pathlib import Path
import socket
import stat
import struct
import time

from .evidence import EvidenceError, canonical, finite, identity, sha
from .guardian_protocol import check_peer, decode, endpoint_parent, peer_identity


VERSION = 'alpha_v11_candidate_liveness_v1'
MAX_PULSE_BYTES = 1024
_MAX_RIGHTS = 16
_BASE = {'version', 'config_sha256', 'request_id', 'operation'}


@dataclass(frozen=True)
class ProducerPolicy:
    version: str
    worker: str
    generation: str
    candidate_gid: int
    minimum_interval_seconds: float = .1
    publication_timeout_seconds: float = 3.
    maximum_pulses: int = 64

    def __post_init__(self):
        identity(self.version); identity(self.worker); identity(self.generation, maximum=80)
        if (type(self.candidate_gid) is not int or not 1 <= self.candidate_gid <= 2**31-1
                or not .05 <= finite(self.minimum_interval_seconds) <= 5
                or not .1 <= finite(self.publication_timeout_seconds) <= 4
                or type(self.maximum_pulses) is not int or not 1 <= self.maximum_pulses <= 128):
            raise EvidenceError('LIVENESS_POLICY_BOUND')


def request(config, request_id, challenge):
    sha(config); identity(request_id, maximum=80); sha(challenge)
    return dict(version=VERSION, config_sha256=config, request_id=request_id,
                operation='PULSE', challenge=challenge)


def validate_request(value, config, *, wire=True):
    sha(config)
    if (type(wire) is not bool or type(value) is not dict
            or set(value) != (_BASE | {'challenge'} if wire else _BASE) or value.get('version') != VERSION
            or value.get('config_sha256') != config or value.get('operation') != 'PULSE'):
        raise EvidenceError('LIVENESS_REQUEST_SCHEMA')
    identity(value['request_id'], maximum=80)
    if wire: sha(value['challenge'])


def validate_challenge(value, config):
    sha(config)
    if (type(value) is not dict or set(value) != {'version', 'config_sha256', 'challenge'}
            or value['version'] != VERSION or value['config_sha256'] != config):
        raise EvidenceError('LIVENESS_CHALLENGE_SCHEMA')
    sha(value['challenge'])


def validate_response(value, q):
    validate_request(q, q.get('config_sha256') if type(q) is dict else None,
                     wire=type(q) is dict and 'challenge' in q)
    if (type(value) is not dict or set(value) != _BASE | {'outcome', 'result', 'financial_authority'}
            or any(value[k] != q[k] for k in _BASE) or value['financial_authority'] is not False
            or type(value['result']) is not dict):
        raise EvidenceError('LIVENESS_RESPONSE_BINDING')
    result = value['result']
    if value['outcome'] == 'REFUSED':
        if result != {'reason': 'INTERRUPTED_OBSERVATION'}:
            raise EvidenceError('LIVENESS_RESPONSE_RESULT')
    elif value['outcome'] == 'PUBLISHED':
        if set(result) != {name+suffix for name in ('accepted', 'heartbeat', 'health')
                           for suffix in ('_id', '_sha256')}:
            raise EvidenceError('LIVENESS_RESPONSE_RESULT')
        for name in ('accepted', 'heartbeat', 'health'):
            identity(result[name+'_id']); sha(result[name+'_sha256'])
    else:
        raise EvidenceError('LIVENESS_RESPONSE_OUTCOME')


def encode(value):
    raw = canonical(value).encode()
    if not 1 <= len(raw) <= MAX_PULSE_BYTES:
        raise EvidenceError('LIVENESS_PACKET_BOUND')
    return raw


def _packet(conn):
    """One kernel packet. Reject and close every received descriptor."""
    if not all(hasattr(socket, name) for name in ('SCM_CREDENTIALS', 'MSG_CMSG_CLOEXEC', 'SO_PASSCRED')):
        raise EvidenceError('LIVENESS_LINUX_CREDENTIALS_REQUIRED')
    capacity = socket.CMSG_SPACE(struct.calcsize('3i')) + socket.CMSG_SPACE(_MAX_RIGHTS*struct.calcsize('i'))
    raw, ancillary, flags, _ = conn.recvmsg(MAX_PULSE_BYTES+1, capacity, socket.MSG_CMSG_CLOEXEC)
    # Even a rejected/truncated packet may have installed descriptors. Close
    # every complete SCM_RIGHTS integer before any validation can raise.
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
            size = struct.calcsize('i')
            for offset in range(0, len(data)-len(data)%size, size):
                descriptor = struct.unpack_from('i', data, offset)[0]
                try: os.close(descriptor)
                except OSError: pass
    if flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC):
        raise EvidenceError('LIVENESS_PACKET_TRUNCATED')
    if not 1 <= len(raw) <= MAX_PULSE_BYTES:
        raise EvidenceError('LIVENESS_PACKET_BOUND')
    return raw, ancillary


def receive_pulse(conn, peer):
    """Authenticate the packet sender as well as the connected peer.

    The listener enables SO_PASSCRED before binding/listening. An inherited or
    transferred connection cannot turn another process's pulse into liveness.
    Nonblocking sockets retain their normal BlockingIOError behavior.
    """
    raw, ancillary = _packet(conn)
    if (len(ancillary) != 1 or ancillary[0][:2] != (socket.SOL_SOCKET, socket.SCM_CREDENTIALS)
            or len(ancillary[0][2]) != struct.calcsize('3i')):
        raise EvidenceError('LIVENESS_PACKET_CREDENTIALS')
    pid, uid, gid = struct.unpack('3i', ancillary[0][2])
    if (type(peer) is not dict or any(type(peer.get(k)) is not int for k in ('pid', 'uid', 'gid'))
            or (pid, uid, gid) != (peer['pid'], peer['uid'], peer['gid'])):
        raise EvidenceError('LIVENESS_PACKET_PEER_CHANGED')
    check_peer(conn, peer)
    return decode(raw)


def _remaining(conn, end):
    left = end-time.monotonic()
    if left <= 0: raise EvidenceError('LIVENESS_CONNECTION_DEADLINE')
    conn.settimeout(left)


def _endpoint_signature(info, uid):
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != uid:
        raise EvidenceError('LIVENESS_SOCKET_IDENTITY')
    return info.st_dev, info.st_ino


class LivenessClient:
    """Publish an authenticated pulse; never opens an archive or account."""
    def __init__(self, path, *, broker_uid, broker_gid, config, timeout_seconds=1.):
        if (any(type(value) is not int or not 1 <= value <= 2**31-1 for value in (broker_uid, broker_gid))
                or not .05 <= finite(timeout_seconds) <= 4):
            raise EvidenceError('LIVENESS_CLIENT_BOUND')
        sha(config)
        self.path, self.broker_uid, self.broker_gid = Path(path), broker_uid, broker_gid
        self.config, self.timeout_seconds = config, timeout_seconds

    def pulse(self, request_id):
        identity(request_id, maximum=80)
        path = endpoint_parent(self.path, self.broker_uid)
        original = _endpoint_signature(path.lstat(), self.broker_uid)
        end = time.monotonic()+self.timeout_seconds
        with socket.socket(socket.AF_UNIX, socket.SOCK_SEQPACKET) as conn:
            _remaining(conn, end); conn.connect(str(path))
            peer = peer_identity(conn, uid=self.broker_uid, gid=self.broker_gid)
            if original != _endpoint_signature(path.lstat(), self.broker_uid):
                raise EvidenceError('LIVENESS_SOCKET_CHANGED')
            _remaining(conn, end); raw, ancillary = _packet(conn)
            if ancillary: raise EvidenceError('LIVENESS_RESPONSE_ANCILLARY')
            challenge = decode(raw); validate_challenge(challenge, self.config)
            check_peer(conn, peer)
            q = request(self.config, request_id, challenge['challenge'])
            raw = encode(q)
            _remaining(conn, end)
            if conn.send(raw) != len(raw): raise EvidenceError('LIVENESS_INCOMPLETE_SEND')
            _remaining(conn, end); raw, ancillary = _packet(conn)
            if ancillary: raise EvidenceError('LIVENESS_RESPONSE_ANCILLARY')
            check_peer(conn, peer)
            if original != _endpoint_signature(path.lstat(), self.broker_uid):
                raise EvidenceError('LIVENESS_SOCKET_CHANGED')
        result = decode(raw); validate_response(result, q)
        if result['outcome'] == 'REFUSED': raise EvidenceError('LIVENESS_REQUEST_REFUSED')
        return result
