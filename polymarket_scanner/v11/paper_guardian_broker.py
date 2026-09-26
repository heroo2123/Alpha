"""Finite Linux PAPER broker. Owns the archive; peers have only safety verbs.

This is a local synthetic-custody adapter, not a venue transport or service.
The only account mutation is the existing signature-pinned CANCEL_REQUESTED.
"""
from dataclasses import asdict, replace
from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import time

from .evidence import EvidenceError, canonical, digest, identity, sha
from .guardian_lease import process_identity
from .guardian_protocol import (VERSION, MAX_FRAME, BrokerPolicy, check_peer, decode,
    endpoint_parent, peer_identity, receive, send, validate_request, validate_result)
from .paper_cancellation import managed_opening
from .paper_coordinator import ACCOUNT_KEY, UNRESOLVED, cancel_identity
from .paper_guardian import PaperGuardian, GuardianDeadline, _deadline, process_limits, restore


def journal_key(account_id):
    return 'paper-cancel-broker:'+digest(identity(account_id))


def bind_guardian(guardian, policy):
    """Bind peer policy to the existing account/maker/basket lease identity."""
    version='broker-policy:'+digest(dict(guardian=asdict(guardian.policy),broker=asdict(policy)))
    return PaperGuardian(guardian.coordinator, policy=replace(guardian.policy,version=version),
        health_config=guardian.health_config,worker=guardian.worker)


def broker_digest(guardian, policy):
    return digest(dict(guardian_config=guardian.config,broker=asdict(policy)))


def launch_broker(guardian_config,*,policy,path,connections=100,seconds=30):
    """Caller-owned finite sibling, fixed module, closed FDs and sanitized environment."""
    raw=canonical(dict(guardian=guardian_config,broker=asdict(policy),socket=str(path),
        connections=connections,seconds=seconds)).encode()
    if len(raw)>MAX_FRAME:raise EvidenceError('BROKER_LAUNCH_BOUND')
    child=subprocess.Popen([sys.executable,'-s','-E','-m','polymarket_scanner.v11.paper_guardian_broker'],
        cwd=Path(__file__).resolve().parents[2],env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8'},
        stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,close_fds=True,start_new_session=True)
    try:child.stdin.write(raw);child.stdin.close();child.stdin=None
    except BaseException:
        child.kill();child.communicate(timeout=5)
        raise
    return child


def validate_journal(event_id,d):
    keys={'version','config_sha256','guardian_config','account_id','account_policy_sha256',
          'policy','request','phase','response','financial_authority'}
    if (type(d) is not dict or set(d)!=keys or d['version']!=VERSION
            or d['financial_authority'] is not False or event_id!=journal_key(d['account_id'])
            or d['phase'] not in {'ACCEPTED','COMPLETED'}):
        raise EvidenceError('BROKER_JOURNAL_SCHEMA')
    p=BrokerPolicy(**d['policy']);sha(d['guardian_config']);sha(d['account_policy_sha256'])
    if d['config_sha256']!=digest(dict(guardian_config=d['guardian_config'],broker=asdict(p))):
        raise EvidenceError('BROKER_JOURNAL_CONFIG')
    validate_request(d['request'],p,d['config_sha256'])
    if d['phase']=='ACCEPTED':
        if d['response'] is not None:raise EvidenceError('BROKER_JOURNAL_PHASE')
    else:
        r=d['response'];q=d['request']
        if (type(r) is not dict or set(r)!= {'version','config_sha256','request_id','operation','outcome','result','financial_authority'}
                or any(r[k]!=q[k] for k in ('version','config_sha256','request_id','operation'))
                or r['financial_authority'] is not False or type(r['result']) is not dict
                or r['outcome'] not in {'SNAPSHOT','CHECKED','REQUESTED_NOT_CONFIRMED','REFUSED'}):
            raise EvidenceError('BROKER_JOURNAL_RESPONSE')
        validate_result(r,q,p)  # No state, arbitrary payload, or authority in receipts.


class PaperGuardianBroker:
    """Single serialized broker, one durable pending request, immutable receipts."""
    def __init__(self, guardian, policy):
        if not isinstance(policy,BrokerPolicy):raise EvidenceError('BROKER_POLICY_REQUIRED')
        if (os.getuid(),os.getgid())!=(policy.broker_uid,policy.broker_gid):
            raise EvidenceError('BROKER_LOCAL_IDENTITY')
        self.g=bind_guardian(guardian,policy);self.policy=policy
        self.store=self.g.store;self.c=self.g.coordinator
        self.config=broker_digest(self.g,policy);self.key=journal_key(self.c.policy.account_id)

    def _get(self,key):
        try:return self.store.get(key)
        except EvidenceError as exc:
            if str(exc)!='EVIDENCE_MISSING':raise
            return None

    def _id(self,q,phase):return 'broker-'+phase+':'+digest([self.config,q['request_id']])

    def _head(self):
        row=self.store.latest(kind='RUNTIME_STATUS',event_id=self.key)
        if row:
            d=row['body']['details'];validate_journal(self.key,d)
            if d['config_sha256']!=self.config:raise EvidenceError('BROKER_CONFIG_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self,q,phase,response=None):
        head=self._head()
        return self.store.safety_audit(self._id(q,phase.lower()),kind='RUNTIME_STATUS',event_id=self.key,
            details=dict(version=VERSION,config_sha256=self.config,guardian_config=self.g.config,
                account_id=self.c.policy.account_id,account_policy_sha256=self.c.policy_sha,
                policy=asdict(self.policy),request=q,phase=phase,response=response,financial_authority=False),
            expected_previous_seq=head['seq'] if head else 0)

    def _gate(self,reason):
        head=self.g._head();d=head['body']['details'] if head else {}
        return self.g._save(head,status='GATED',reasons=[reason],cursor=d.get('cursor',''),
            intents=d.get('cancel_intents'),pending_trigger=d.get('pending_trigger'),
            snapshot=self.store.get(d['account_snapshot_id']) if d.get('pending_trigger') else None)

    def _snapshot(self,q):
        row=self.c._head()
        if row is None:raise EvidenceError('BROKER_EXISTING_ACCOUNT_REQUIRED')
        state=self.c._state(row)
        retained=sorted(k for k,i in state['intents'].items() if i['status'] in UNRESOLVED
                        and not i.get('cancel_requested') and managed_opening(i))
        cursor=q['cursor'];order=[k for k in retained if k>cursor]+[k for k in retained if k<=cursor]
        chosen=order[:self.policy.maximum_intents]
        return dict(snapshot_id=row['id'],snapshot_sha256=row['sha256'],snapshot_seq=row['seq'],
            intents={k:cancel_identity(state['intents'][k]) for k in chosen},
            next_cursor=chosen[-1] if chosen else '',remaining=len(order)>len(chosen))

    def _validate_targets(self,q):
        row=self.store.get(q['snapshot_id'])
        if (row['sha256']!=q['snapshot_sha256'] or row['kind']!='COORDINATOR_EVENT'
                or row['event_id']!=ACCOUNT_KEY or row['body']['details'].get('policy_sha256')!=self.c.policy_sha):
            raise EvidenceError('BROKER_ACCOUNT_SNAPSHOT_BINDING')
        saved=self.c._state(row);current=self.c._state(self.c._head())
        for key,sig in q['intents'].items():
            old=saved['intents'].get(key);now=current['intents'].get(key)
            if (old is None or now is None or old['status'] not in UNRESOLVED or not managed_opening(old)
                    or cancel_identity(old)!=sig or cancel_identity(now)!=sig):
                raise EvidenceError('BROKER_INTENT_SIGNATURE_BINDING')

    def _complete(self,q,outcome,result):
        response=dict(version=VERSION,config_sha256=self.config,request_id=q['request_id'],operation=q['operation'],
            outcome=outcome,result=result,financial_authority=False)
        self._save(q,'COMPLETED',response)
        return response

    def _cancel(self,q):
        self._gate('BROKER_DURABLE_CANCEL_PENDING')
        self._validate_targets(q)
        for key,sig in q['intents'].items():
            command='broker-cancel:'+digest([self.config,q['request_id'],key])
            receipt=self._get(command)
            if receipt:
                # The existing coordinator checks immutable request replay before state.
                self.c.transition(command,intent_id=key,status='CANCEL_REQUESTED',expected_cancel_identity=sig)
                continue
            now=self.c._state(self.c._head())['intents'][key]
            if now['status'] in UNRESOLVED and not now.get('cancel_requested'):
                self.c.transition(command,intent_id=key,status='CANCEL_REQUESTED',expected_cancel_identity=sig)
        observed=self.c._head()
        return self._complete(q,'REQUESTED_NOT_CONFIRMED',dict(intents=q['intents'],terminal_confirmation=False,
            account_snapshot_id=observed['id'],account_snapshot_sha256=observed['sha256']))

    def recover(self):
        head=self._head()
        if head and head['body']['details']['phase']=='ACCEPTED':
            q=head['body']['details']['request']
            if q['operation']=='CANCEL':return self._cancel(q)
            # Restart/retry cannot renew a lease or invent a historical observation.
            self._gate('BROKER_INTERRUPTED_OBSERVATION')
            return self._complete(q,'REFUSED',dict(reason='INTERRUPTED_OBSERVATION'))

    def handle(self,q,peer):
        validate_request(q,self.policy,self.config)
        actual=process_identity(peer['pid'])
        if (set(peer)!= {'pid','start_ticks','uid','boot_id','gid'}
                or actual!={k:v for k,v in peer.items() if k!='gid'}
                or (peer['uid'],peer['gid'])!=(self.policy.guardian_uid,self.policy.guardian_gid)
                or actual in (self.g.worker,process_identity(os.getpid()))):
            raise EvidenceError('BROKER_GUARDIAN_IDENTITY')
        self.g.process=actual;self.g.broker_process=process_identity(os.getpid())
        # Recovery precedes any new operation, including a healthy CHECK.
        self.recover()
        old=self._get(self._id(q,'accepted'))
        if old:
            if old['body']['details']['request']!=q:raise EvidenceError('BROKER_REQUEST_ID_CONFLICT')
            receipt=self.store.get(self._id(q,'completed'))
            return receipt['body']['details']['response']
        if q['operation']=='CANCEL':self._validate_targets(q)
        if q['operation']!='SNAPSHOT':self._gate('BROKER_REQUEST_IN_PROGRESS')
        self._save(q,'ACCEPTED')  # One atomic discovery point before any account effects.
        if q['operation']=='CANCEL':return self._cancel(q)
        if q['operation']=='SNAPSHOT':return self._complete(q,'SNAPSHOT',self._snapshot(q))
        row=self.g._cycle()
        return self._complete(q,'CHECKED',dict(status_id=row['id']))

    def _custody(self):
        path=self.store.path
        endpoint_parent(path,os.getuid(),maximum=None)
        parent=path.parent.lstat()
        if not stat.S_ISDIR(parent.st_mode) or parent.st_uid!=os.getuid() or parent.st_mode&0o077:
            raise EvidenceError('BROKER_ARCHIVE_PARENT_CUSTODY')
        for name in (str(path),str(path)+'-wal',str(path)+'-shm',str(path)+'-journal'):
            p=Path(name)
            if p.exists() or p.is_symlink():
                info=p.lstat()
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
                    raise EvidenceError('BROKER_ARCHIVE_CUSTODY')

    @contextmanager
    def _socket(self,path,config,*,socket_type=socket.SOCK_STREAM,passcred=False):
        """Broker-owned endpoint custody shared by the two distinct protocols."""
        path=endpoint_parent(path,self.policy.broker_uid);sha(config);bound=False
        meta=Path(str(path)+'.broker-id')
        mfd=os.open(meta,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600) if not meta.exists() else None
        if mfd is not None:
            try:os.write(mfd,config.encode());os.fsync(mfd)
            finally:os.close(mfd)
        mfd=os.open(meta,os.O_RDONLY|os.O_NOFOLLOW)
        try:
            info=os.fstat(mfd)
            if (not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077
                    or os.read(mfd,65)!=config.encode()):
                raise EvidenceError('BROKER_ENDPOINT_CONFIG_CUSTODY')
        finally:os.close(mfd)
        if path.exists() or path.is_symlink():
            info=path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid!=os.getuid():
                raise EvidenceError('BROKER_EXISTING_ENDPOINT_REFUSED')
            with socket.socket(socket.AF_UNIX,socket_type) as probe:
                probe.settimeout(self.policy.timeout_seconds)
                try:probe.connect(str(path))
                except ConnectionRefusedError:path.unlink()
                else:raise EvidenceError('BROKER_ENDPOINT_IN_USE')
        try:
            with socket.socket(socket.AF_UNIX,socket_type) as listener:
                if passcred:listener.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
                listener.bind(str(path));bound=True;inode=path.stat().st_ino
                os.chmod(path,0o666);listener.listen(4)
                yield listener
        finally:
            if bound and path.is_socket() and path.stat().st_ino==inode:path.unlink()

    @contextmanager
    def _listener(self,path):
        self._custody();path=endpoint_parent(path,self.policy.broker_uid)
        # The shared lock excludes the previous direct trusted guardian too.
        fd=os.open(str(self.store.path)+'.guardian.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
        locked=False
        try:
            info=os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
                raise EvidenceError('BROKER_LOCK_CUSTODY')
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise EvidenceError('BROKER_ALREADY_RUNNING') from None
            locked=True
            self._gate('BROKER_STARTING');self.recover()
            with self._socket(path,self.config) as listener:yield listener
        finally:
            try:
                if locked:self._gate('BROKER_FINITE_RUN_ENDED')
            finally:
                os.close(fd)

    def serve(self,path,*,connections=100,seconds=30):
        if (type(connections) is not int or not 1<=connections<=200
                or type(seconds) not in (int,float) or not .1<=seconds<=60):
            raise EvidenceError('BROKER_FINITE_RUN_BOUND')
        with self._listener(path) as listener:
            end=time.monotonic()+seconds
            for _ in range(connections):
                left=end-time.monotonic()
                if left<=0:break
                listener.settimeout(left)
                try:conn,_=listener.accept()
                except TimeoutError:break
                with conn:
                    try:
                        peer=peer_identity(conn,uid=self.policy.guardian_uid,gid=self.policy.guardian_gid)
                        deadline=min(end,time.monotonic()+self.policy.timeout_seconds)
                        q=receive(conn,deadline);check_peer(conn,peer)
                        response=self.handle(q,peer)
                        check_peer(conn,peer);send(conn,response,deadline)
                    except (EvidenceError,OSError,sqlite3.Error,ValueError,TypeError,KeyError,RecursionError,OverflowError):
                        continue


def main():
    try:
        process_limits();signal.signal(signal.SIGALRM,_deadline);signal.setitimer(signal.ITIMER_REAL,65.)
        raw=decode(sys.stdin.buffer.read(MAX_FRAME+1))
        if set(raw)!= {'guardian','broker','socket','connections','seconds'}:raise EvidenceError('BROKER_LAUNCH_SCHEMA')
        PaperGuardianBroker(restore(raw['guardian']),BrokerPolicy(**raw['broker'])).serve(
            raw['socket'],connections=raw['connections'],seconds=raw['seconds'])
    except (GuardianDeadline,EvidenceError,OSError,sqlite3.Error,ValueError,TypeError,KeyError,MemoryError,RecursionError,OverflowError):
        print('{"outcome":"FAILED_CLOSED","financial_authority":false}')
        return 2
    print('{"outcome":"FINITE_RUN_ENDED_GATED","financial_authority":false}')
    return 0


if __name__=='__main__':raise SystemExit(main())
