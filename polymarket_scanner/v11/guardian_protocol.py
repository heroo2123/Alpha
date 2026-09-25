"""Bounded local PAPER safety protocol; no database handles or order transport."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import socket
import stat
import struct
import time

from .evidence import EvidenceError, canonical, finite, identity, sha
from .guardian_lease import process_identity


VERSION = 'alpha_v11_paper_cancel_broker_v1'
MAX_FRAME = 32768


@dataclass(frozen=True)
class BrokerPolicy:
    version: str
    broker_uid: int
    broker_gid: int
    guardian_uid: int
    guardian_gid: int
    identity_mode: str = 'DISTINCT_PRINCIPALS'
    timeout_seconds: float = 1.
    maximum_intents: int = 8

    def __post_init__(self):
        identity(self.version)
        if (any(type(v) is not int or not 1 <= v <= 2**31-1 for v in
                (self.broker_uid,self.broker_gid,self.guardian_uid,self.guardian_gid))
                or self.identity_mode not in {'DISTINCT_PRINCIPALS','SYNTHETIC_SAME_UID_MECHANICS'}
                or self.identity_mode == 'DISTINCT_PRINCIPALS' and self.broker_uid == self.guardian_uid
                or not .05 <= finite(self.timeout_seconds) <= 2
                or type(self.maximum_intents) is not int or not 1 <= self.maximum_intents <= 16):
            raise EvidenceError('BROKER_POLICY_BOUND')


def _pairs(items):
    result={}
    for k,v in items:
        if k in result: raise EvidenceError('BROKER_DUPLICATE_JSON_KEY')
        result[k]=v
    return result


def decode(raw):
    if not 1 <= len(raw) <= MAX_FRAME: raise EvidenceError('BROKER_FRAME_BOUND')
    try:
        value=json.loads(raw,object_pairs_hook=_pairs,
            parse_constant=lambda _:(_ for _ in ()).throw(EvidenceError('BROKER_NONFINITE_JSON')))
        canonical(value)
    except (ValueError,UnicodeError,RecursionError):
        raise EvidenceError('BROKER_INVALID_JSON') from None
    if type(value) is not dict: raise EvidenceError('BROKER_OBJECT_REQUIRED')
    return value


def _remaining(sock,end):
    left=end-time.monotonic()
    if left <= 0: raise EvidenceError('BROKER_CONNECTION_DEADLINE')
    sock.settimeout(left)


def receive(sock,end):
    def exact(size):
        data=bytearray()
        while len(data)<size:
            _remaining(sock,end); chunk=sock.recv(size-len(data))
            if not chunk: raise EvidenceError('BROKER_DISCONNECTED_FRAME')
            data.extend(chunk)
        return bytes(data)
    size=struct.unpack('!I',exact(4))[0]
    if not 1 <= size <= MAX_FRAME: raise EvidenceError('BROKER_FRAME_BOUND')
    return decode(exact(size))


def send(sock,value,end):
    raw=canonical(value).encode()
    if not 1 <= len(raw) <= MAX_FRAME: raise EvidenceError('BROKER_FRAME_BOUND')
    _remaining(sock,end);sock.sendall(struct.pack('!I',len(raw))+raw)


def peer_identity(sock, *, uid, gid):
    try:
        pid,actual_uid,actual_gid=struct.unpack('3i',sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
        if (actual_uid,actual_gid)!=(uid,gid): raise EvidenceError('BROKER_PEER_UNAUTHORIZED')
        # Unmapped namespace principals can appear as overflow IDs in both
        # SO_PEERCRED and /proc ownership. Even a mapped use of that number is
        # ambiguous here; refuse it instead of treating matching values as proof.
        for kind,value in (('uid',actual_uid),('gid',actual_gid)):
            raw=Path('/proc/sys/kernel/overflow'+kind).read_text().strip()
            if not raw.isascii() or not raw.isdigit() or len(raw)>10 or not 0<=int(raw)<=2**32-1:
                raise EvidenceError('BROKER_PEER_IDENTITY_UNAVAILABLE')
            if value==int(raw):raise EvidenceError('BROKER_PEER_OVERFLOW_IDENTITY')
        peer=process_identity(pid)
        if peer['uid']!=uid: raise EvidenceError('BROKER_PEER_IDENTITY_AMBIGUOUS')
        return dict(peer,gid=actual_gid)
    except (OSError,struct.error):
        raise EvidenceError('BROKER_PEER_IDENTITY_UNAVAILABLE') from None


def check_peer(sock,original):
    if peer_identity(sock,uid=original['uid'],gid=original['gid'])!=original:
        raise EvidenceError('BROKER_PEER_IDENTITY_CHANGED')


def endpoint_parent(path,uid,*,maximum=100):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts or maximum is not None and len(os.fsencode(path))>maximum:
        raise EvidenceError('BROKER_SOCKET_PATH_BOUND')
    for parent in (path.parent,*path.parent.parents):
        info=parent.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o022 and not info.st_mode & stat.S_ISVTX:
            raise EvidenceError('BROKER_SOCKET_PARENT_CUSTODY')
    info=path.parent.stat()
    if info.st_uid!=uid or info.st_mode & 0o022:
        raise EvidenceError('BROKER_SOCKET_PARENT_CUSTODY')
    return path


def request(config,request_id,operation,**fields):
    sha(config);identity(request_id,maximum=80)
    return dict(version=VERSION,config_sha256=config,request_id=request_id,operation=operation,**fields)


def validate_request(value,policy,config):
    base={'version','config_sha256','request_id','operation'}
    if type(value) is not dict or value.get('version')!=VERSION or value.get('config_sha256')!=config:
        raise EvidenceError('BROKER_PROTOCOL_OR_CONFIG')
    identity(value.get('request_id'),maximum=80)
    op=value.get('operation')
    if op=='SNAPSHOT':
        if set(value)!=base|{'cursor'}:raise EvidenceError('BROKER_REQUEST_SCHEMA')
        if type(value['cursor']) is not str:raise EvidenceError('BROKER_REQUEST_SCHEMA')
        identity(value['cursor'] or '-',maximum=160)
    elif op=='CHECK':
        if set(value)!=base:raise EvidenceError('BROKER_REQUEST_SCHEMA')
    elif op=='CANCEL':
        if set(value)!=base|{'snapshot_id','snapshot_sha256','intents'}:raise EvidenceError('BROKER_REQUEST_SCHEMA')
        identity(value['snapshot_id']);sha(value['snapshot_sha256'])
        if type(value['intents']) is not dict or not 1<=len(value['intents'])<=policy.maximum_intents:
            raise EvidenceError('BROKER_CANCEL_TARGET_BOUND')
        for key,sig in value['intents'].items():identity(key);sha(sig)
    else:raise EvidenceError('BROKER_CANCEL_ONLY_OPERATIONS')


def validate_result(response,q,policy):
    expected={'SNAPSHOT':'SNAPSHOT','CHECK':'CHECKED','CANCEL':'REQUESTED_NOT_CONFIRMED'}
    outcome=response['outcome'];r=response['result']
    if outcome not in {expected[q['operation']],'REFUSED'}:raise EvidenceError('BROKER_RESPONSE_OPERATION')
    if outcome=='REFUSED':
        if r!={'reason':'INTERRUPTED_OBSERVATION'}:raise EvidenceError('BROKER_RESPONSE_RESULT')
    elif outcome=='CHECKED':
        if set(r)!={'status_id'}:raise EvidenceError('BROKER_RESPONSE_RESULT')
        identity(r['status_id'])
    elif outcome=='REQUESTED_NOT_CONFIRMED':
        if (set(r)!={'intents','terminal_confirmation','account_snapshot_id','account_snapshot_sha256'}
                or r['intents']!=q['intents'] or r['terminal_confirmation'] is not False):
            raise EvidenceError('BROKER_RESPONSE_RESULT')
        identity(r['account_snapshot_id']);sha(r['account_snapshot_sha256'])
    else:
        if set(r)!={'snapshot_id','snapshot_sha256','snapshot_seq','intents','next_cursor','remaining'}:
            raise EvidenceError('BROKER_RESPONSE_RESULT')
        identity(r['snapshot_id']);sha(r['snapshot_sha256'])
        if (type(r['snapshot_seq']) is not int or r['snapshot_seq']<1 or type(r['remaining']) is not bool
                or type(r['next_cursor']) is not str or type(r['intents']) is not dict
                or len(r['intents'])>policy.maximum_intents):raise EvidenceError('BROKER_RESPONSE_RESULT')
        identity(r['next_cursor'] or '-')
        for key,sig in r['intents'].items():identity(key);sha(sig)


class GuardianClient:
    """Fixed safety methods. No fallback to an account database on any failure."""
    def __init__(self,path,policy,config):
        if not isinstance(policy,BrokerPolicy):raise EvidenceError('BROKER_POLICY_REQUIRED')
        sha(config);self.path,self.policy,self.config=Path(path),policy,config

    def _call(self,value):
        validate_request(value,self.policy,self.config)
        p=self.policy;path=endpoint_parent(self.path,p.broker_uid)
        before=path.lstat()
        if not stat.S_ISSOCK(before.st_mode) or before.st_uid!=p.broker_uid:
            raise EvidenceError('BROKER_SOCKET_IDENTITY')
        end=time.monotonic()+p.timeout_seconds
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            _remaining(conn,end);conn.connect(str(path))
            peer=peer_identity(conn,uid=p.broker_uid,gid=p.broker_gid)
            after=path.lstat()
            if (before.st_dev,before.st_ino)!=(after.st_dev,after.st_ino):raise EvidenceError('BROKER_SOCKET_CHANGED')
            send(conn,value,end);result=receive(conn,end);check_peer(conn,peer)
        if (set(result)!={'version','config_sha256','request_id','operation','outcome','result','financial_authority'}
                or any(result[k]!=value[k] for k in ('version','config_sha256','request_id','operation'))
                or result['financial_authority'] is not False or type(result['result']) is not dict
                or result['outcome'] not in {'SNAPSHOT','CHECKED','REQUESTED_NOT_CONFIRMED','PENDING','REFUSED'}):
            raise EvidenceError('BROKER_RESPONSE_BINDING')
        validate_result(result,value,p)
        if result['outcome']=='REFUSED':raise EvidenceError('BROKER_REQUEST_REFUSED')
        return result

    def snapshot(self,request_id,*,cursor=''):
        return self._call(request(self.config,request_id,'SNAPSHOT',cursor=cursor))

    def check(self,request_id):
        return self._call(request(self.config,request_id,'CHECK'))

    def cancel(self,request_id,*,snapshot_id,snapshot_sha256,intents):
        return self._call(request(self.config,request_id,'CANCEL',snapshot_id=snapshot_id,
            snapshot_sha256=snapshot_sha256,intents=intents))
