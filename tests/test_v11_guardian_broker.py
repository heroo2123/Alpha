"""Synthetic PAPER broker mechanics; separate-principal proof is a distinct suite."""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11 import guardian_lease as lease, paper_guardian as guardian
from polymarket_scanner.v11 import guardian_protocol as protocol
from polymarket_scanner.v11 import guardian_client
from polymarket_scanner.v11.paper_guardian_broker import PaperGuardianBroker, validate_journal, launch_broker
from test_v11_paper_coordinator import rig, coordinator, proposal, fill, proof
from test_v11_paper_guardian import worker, child, build, state, admission, wait_for, finished, ENV, ROOT


def policy(**kw):
    return protocol.BrokerPolicy('synthetic-broker-tests',os.getuid(),os.getgid(),os.getuid(),os.getgid(),
        identity_mode='SYNTHETIC_SAME_UID_MECHANICS',**kw)


@pytest.fixture
def peer():
    with child() as p:yield dict(lease.process_identity(p.pid),gid=os.getgid())


@pytest.fixture
def broker(rig,worker):
    c=coordinator(rig);c.coordinate('reserve',(proposal(rig,units='5'),))
    return PaperGuardianBroker(build(rig,worker),policy())


def snapshot(b,peer,key='snapshot'):
    return b.handle(protocol.request(b.config,key,'SNAPSHOT',cursor=''),peer)['result']


def cancel(b,peer,key='cancel',pin=None):
    pin=pin or snapshot(b,peer)
    q=protocol.request(b.config,key,'CANCEL',**{k:pin[k] for k in ('snapshot_id','snapshot_sha256','intents')})
    return q


def test_only_cancel_changes_account_and_replay_keeps_original_receipt(broker,peer,rig):
    b=broker;before=b.c.snapshot();q=cancel(b,peer)
    response=b.handle(q,peer);record=b.c._head()
    assert response['outcome']=='REQUESTED_NOT_CONFIRMED'
    assert response['result']['terminal_confirmation'] is False
    assert state(b.c)['intents']['proposal']['status']=='CANCEL_REQUESTED'
    assert b.c.snapshot()['reserved_cash']==before['reserved_cash']
    assert b.c.snapshot()['cash']==before['cash']
    rig['now'][0]+=1
    assert b.handle(q,peer)==response and b.c._head()==record
    changed=deepcopy(q);changed['intents']['proposal']='f'*64
    with pytest.raises(EvidenceError,match='REQUEST_ID_CONFLICT'):b.handle(changed,peer)


@pytest.mark.parametrize('failure',['accepted','account','receipt'])
def test_crash_retry_and_new_peer_resume_one_durable_cancel(broker,peer,rig,monkeypatch,failure):
    b=broker;q=cancel(b,peer);before=b.c.snapshot()
    original_save=b._save;original_transition=b.c.transition
    def save(q,phase,response=None):
        result=original_save(q,phase,response)
        if (failure=='accepted' and phase=='ACCEPTED') or (failure=='receipt' and phase=='COMPLETED'):
            raise RuntimeError('SYNTHETIC_CRASH')
        return result
    def transition(*a,**kw):
        result=original_transition(*a,**kw)
        if failure=='account':raise RuntimeError('SYNTHETIC_CRASH')
        return result
    monkeypatch.setattr(b,'_save',save);monkeypatch.setattr(b.c,'transition',transition)
    with pytest.raises(RuntimeError,match='SYNTHETIC_CRASH'):b.handle(q,peer)
    monkeypatch.undo();rig['now'][0]+=1
    restarted=PaperGuardianBroker(build(rig,type('Worker',(),{'pid':b.g.worker['pid']})()),policy())
    with child() as replacement:
        result=restarted.handle(q,dict(lease.process_identity(replacement.pid),gid=os.getgid()))
    assert result['outcome']=='REQUESTED_NOT_CONFIRMED'
    assert restarted.c.snapshot()['reserved_cash']==before['reserved_cash']
    rows=b.store.records(kind='COORDINATOR_EVENT',event_id='v11-paper-account-state')
    assert sum(r['body']['details']['request'].get('status')=='CANCEL_REQUESTED' for r in rows)==1


def test_pending_cancel_survives_health_recovery_and_fill(broker,peer,rig,monkeypatch):
    b=broker;q=cancel(b,peer);save=b._save
    def crash(q,phase,response=None):
        row=save(q,phase,response)
        if phase=='ACCEPTED':raise RuntimeError('SYNTHETIC_CRASH')
        return row
    monkeypatch.setattr(b,'_save',crash)
    with pytest.raises(RuntimeError):b.handle(q,peer)
    monkeypatch.setattr(b,'_save',save)
    fill(rig,'proposal',units='1',collateral='.4')
    monkeypatch.setattr(b.g,'_health',lambda:None)
    b.handle(protocol.request(b.config,'new-snapshot','SNAPSHOT',cursor=''),peer)
    s=state(b.c)
    assert s['intents']['proposal']['cancel_requested'] and s['lots']
    assert s['intents']['proposal']['filled_units']=='1'


def test_explicit_terminal_between_pin_and_cancel_never_fabricates_confirmation(broker,peer,rig):
    b=broker;q=cancel(b,peer)
    b.c.reconcile_terminal('terminal',proof(rig,'proposal','terminal-proof','PAPER_TERMINAL',status='CANCELED',
        cumulative_fill_units='0',all_fills_reconciled=True,terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL'))
    old=b.c._head();r=b.handle(q,peer)
    assert b.c._head()==old and r['result']['terminal_confirmation'] is False


@pytest.mark.parametrize('change',['hash','snapshot','intent','signature','account','extra','operation'])
def test_bad_cancel_binding_never_changes_account_or_accepts(broker,peer,change):
    b=broker;q=cancel(b,peer);before=b.c._head();head=b._head()
    if change=='hash':q['snapshot_sha256']='e'*64
    if change=='snapshot':q['snapshot_id']='missing'
    if change=='intent':q['intents']={'foreign':'a'*64}
    if change=='signature':q['intents']['proposal']='b'*64
    if change=='account':q['config_sha256']='a'*64
    if change=='extra':q['cash']='100'
    if change=='operation':q['operation']='SUBMITTING'
    with pytest.raises(EvidenceError):b.handle(q,peer)
    assert b.c._head()==before and b._head()==head


@pytest.mark.parametrize('change',['uid','gid','pid','start','worker','self','missing'])
def test_peer_missing_malformed_unauthorized_or_not_independent_fails_closed(broker,peer,change):
    b=broker;q=protocol.request(b.config,'check','CHECK');p=dict(peer)
    if change in ('uid','gid'):p[change]+=1
    if change=='pid':p['pid']=99999999
    if change=='start':p['start_ticks']+=1
    if change=='worker':p=dict(b.g.worker,gid=os.getgid())
    if change=='self':p=dict(lease.process_identity(os.getpid()),gid=os.getgid())
    if change=='missing':p.pop('gid')
    with pytest.raises(EvidenceError):b.handle(q,p)
    assert b._head() is None and b.g._head() is None


def test_check_replay_never_renews_lease_and_both_processes_are_bound(broker,peer,monkeypatch,rig):
    b=broker
    # Empty retained target population and a test-only health oracle isolate lease mechanics.
    b.handle(cancel(b,peer),peer);monkeypatch.setattr(b.g,'_health',lambda:None)
    q=protocol.request(b.config,'check','CHECK');r=b.handle(q,peer);head=b.g._head()
    assert head['body']['details']['status']=='READY'
    assert head['body']['details']['process']['pid']==peer['pid']
    assert head['body']['details']['broker_process']['pid']==os.getpid()
    admission(b.g);rig['now'][0]+=3
    assert b.handle(q,peer)==r and b.g._head()==head
    with pytest.raises(EvidenceError,match='LEASE_GATED'):admission(b.g)


def test_interrupted_check_refuses_replay_and_cannot_renew(broker,peer,monkeypatch):
    b=broker;q=protocol.request(b.config,'check','CHECK')
    monkeypatch.setattr(b.g,'_cycle',lambda:(_ for _ in ()).throw(RuntimeError('SYNTHETIC_CRASH')))
    with pytest.raises(RuntimeError):b.handle(q,peer)
    result=b.handle(q,peer)
    assert result['outcome']=='REFUSED' and b.g._head()['body']['details']['status']=='GATED'


def test_storage_failure_retains_pending_and_blocks_later_healthy_check(broker,peer,monkeypatch):
    b=broker;q=cancel(b,peer);transition=b.c.transition
    monkeypatch.setattr(b.c,'transition',lambda *a,**k:(_ for _ in ()).throw(sqlite3.OperationalError('SYNTHETIC_DISK')))
    with pytest.raises(sqlite3.OperationalError):b.handle(q,peer)
    assert b._head()['body']['details']['phase']=='ACCEPTED'
    with pytest.raises(sqlite3.OperationalError):b.handle(protocol.request(b.config,'check','CHECK'),peer)
    assert b.g._head()['body']['details']['status']=='GATED'
    monkeypatch.setattr(b.c,'transition',transition)
    assert b.handle(q,peer)['outcome']=='REQUESTED_NOT_CONFIRMED'


def test_backward_clock_keeps_cancel_safety_without_new_financial_authority(broker,peer,rig):
    b=broker;q=cancel(b,peer);rig['now'][0]-=1000
    assert b.handle(q,peer)['outcome']=='REQUESTED_NOT_CONFIRMED'
    with pytest.raises(EvidenceError):b.c.transition('submit',intent_id='proposal',status='SUBMITTING')


@contextmanager
def running(b,*,connections=20,seconds=6):
    b.store.path.chmod(0o600)
    for suffix in ('-wal','-shm'):
        p=Path(str(b.store.path)+suffix)
        if p.exists():p.chmod(0o600)
    with tempfile.TemporaryDirectory(prefix='v11-broker-') as directory:
        path=Path(directory)/'s'
        raw=dict(guardian=guardian.configuration(b.c,policy=replace(b.g.policy,version='synthetic-process-check'),
            health_config=b.g.health_config,worker=b.g.worker),broker=asdict(b.policy),socket=str(path),
            connections=connections,seconds=seconds)
        p=launch(raw)
        try:
            wait_for(lambda:path.exists() or p.poll() is not None)
            assert p.poll() is None, p.communicate()
            yield p,path,raw
        finally:
            if p.poll() is None:p.kill()
            p.communicate(timeout=5)


def launch(raw):
    return launch_broker(raw['guardian'],policy=protocol.BrokerPolicy(**raw['broker']),path=raw['socket'],
        connections=raw['connections'],seconds=raw['seconds'])


def test_real_socket_cancel_disconnect_and_duplicate_delivery(broker):
    b=broker
    with running(b) as (p,path,raw):
        client=protocol.GuardianClient(path,b.policy,b.config)
        pin=client.snapshot('snapshot')['result']
        q=protocol.request(b.config,'cancel','CANCEL',**{k:pin[k] for k in ('snapshot_id','snapshot_sha256','intents')})
        # Drop the response after sending a complete authenticated request.
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            conn.connect(str(path));protocol.send(conn,q,time.monotonic()+1)
        wait_for(lambda:state(b.c)['intents']['proposal'].get('cancel_requested'))
        r=client.cancel('cancel',**{k:pin[k] for k in ('snapshot_id','snapshot_sha256','intents')})
        assert r['outcome']=='REQUESTED_NOT_CONFIRMED'
        assert client.cancel('cancel',**{k:pin[k] for k in ('snapshot_id','snapshot_sha256','intents')})==r
        assert p.poll() is None


def test_real_socket_stalled_malformed_and_unsupported_request_do_not_block_later_client(broker):
    b=broker
    with running(b) as (p,path,_):
        before=b.c._head()
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            conn.connect(str(path));conn.sendall(b'\x00');time.sleep(1.1)
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as conn:
            conn.connect(str(path));protocol.send(conn,protocol.request(b.config,'bad','NEW_ORDER'),time.monotonic()+1)
            assert conn.recv(1)==b''
        assert protocol.GuardianClient(path,b.policy,b.config).snapshot('ok')['outcome']=='SNAPSHOT'
        assert b.c._head()==before


def test_actual_broker_death_stale_socket_restart_and_receipt_replay(broker):
    b=broker
    with running(b) as (p,path,raw):
        client=protocol.GuardianClient(path,b.policy,b.config)
        pin=client.snapshot('pin')['result'];args={k:pin[k] for k in ('snapshot_id','snapshot_sha256','intents')}
        expected=client.cancel('cancel',**args);before=b.c._head();old_inode=path.stat().st_ino
        p.kill();p.communicate(timeout=5)
        with pytest.raises((EvidenceError,OSError)):client.snapshot('unavailable')
        replacement=launch(dict(raw,seconds=2))
        try:
            wait_for(lambda:replacement.poll() is not None or path.exists() and path.stat().st_ino!=old_inode)
            assert replacement.poll() is None, replacement.communicate()
            assert client.cancel('cancel',**args)==expected
            finished(replacement)
            assert b.c._head()==before
            assert b.g._head()['body']['details']['status']=='GATED'
        finally:
            if replacement.poll() is None:replacement.kill()
            replacement.communicate(timeout=5)


def test_lease_refuses_dead_broker_even_when_client_is_alive(broker,peer):
    b=broker
    with child() as process:
        b.g.process={k:v for k,v in peer.items() if k!='gid'}
        b.g.broker_process=lease.process_identity(process.pid)
        b.g._save(b.g._head(),status='READY',reasons=[],cursor='')
        admission(b.g);process.kill();process.communicate(timeout=5)
        with pytest.raises(EvidenceError):admission(b.g)


def test_broker_policy_cannot_publish_ready_without_both_process_identities(broker):
    with pytest.raises(EvidenceError,match='BROKER_IDENTITY_REQUIRED'):
        broker.g._save(None,status='READY',reasons=[],cursor='')


@pytest.mark.parametrize('wrong',['file','symlink','config'])
def test_endpoint_identity_refuses_foreign_existing_objects(broker,wrong):
    b=broker;b.store.path.chmod(0o600)
    with tempfile.TemporaryDirectory(prefix='v11-broker-') as directory:
        path=Path(directory)/'s';target=Path(directory)/'target';target.write_text('retained')
        if wrong=='file':path.write_text('retained')
        if wrong=='symlink':path.symlink_to(target)
        if wrong=='config':
            meta=Path(str(path)+'.broker-id');meta.write_text('e'*64);meta.chmod(0o600)
        with pytest.raises(EvidenceError):b.serve(path,seconds=.1)
        assert target.read_text()=='retained'
        if wrong=='file':assert path.read_text()=='retained'
        if wrong=='symlink':assert path.is_symlink()


def test_shared_archive_permissions_refuse_startup_without_changing_account(broker):
    b=broker;before=b.c._head();b.store.path.chmod(0o644)
    with tempfile.TemporaryDirectory(prefix='v11-broker-') as directory:
        with pytest.raises(EvidenceError,match='ARCHIVE_CUSTODY'):b.serve(Path(directory)/'s',seconds=.1)
    assert b.c._head()==before and b.g._head() is None


def test_finite_sibling_client_drives_broker_cancel_without_archive_handle(broker):
    b=broker;before=b.c.snapshot()
    with running(b) as (_,path,_):
        p=guardian_client.launch_client(path,policy=b.policy,config=b.config,cycles=2,interval_seconds=.1)
        assert finished(p)['outcome']=='FINITE_CLIENT_RUN_ENDED'
        assert state(b.c)['intents']['proposal']['cancel_requested']
        assert b.c.snapshot()['reserved_cash']==before['reserved_cash']
        with pytest.raises(EvidenceError):admission(b.g)


def test_client_disconnect_fails_closed_without_direct_archive_fallback(broker):
    b=broker;before=b.c._head()
    with tempfile.TemporaryDirectory(prefix='v11-client-') as directory:
        p=guardian_client.launch_client(Path(directory)/'absent',policy=b.policy,config=b.config,cycles=1)
        finished(p,code=2)
    assert b.c._head()==before and b.g._head() is None


@pytest.mark.parametrize('cycles,interval',[(0,.25),(True,.25),(201,.25),(200,1),(1,0),(1,True)])
def test_client_finite_work_bound(broker,cycles,interval):
    client=protocol.GuardianClient('/unused',broker.policy,broker.config)
    with pytest.raises(EvidenceError):guardian_client.run(client,cycles=cycles,interval_seconds=interval)


def test_huge_numeric_launch_fails_redacted_before_any_ipc(broker):
    p=guardian_client.launch_client('/unused',policy=broker.policy,config=broker.config,cycles=1,interval_seconds=10**1000)
    finished(p,code=2)


def test_stopped_or_dead_guardian_closes_broker_lease(broker,peer,monkeypatch):
    b=broker;b.handle(cancel(b,peer),peer);monkeypatch.setattr(b.g,'_health',lambda:None)
    b.handle(protocol.request(b.config,'check','CHECK'),peer);admission(b.g)
    os.kill(peer['pid'],signal.SIGSTOP)
    try:
        with pytest.raises(EvidenceError):admission(b.g)
    finally:os.kill(peer['pid'],signal.SIGCONT)


def test_broker_lock_failure_does_not_touch_other_guardian(broker,tmp_path):
    b=broker;b.store.path.chmod(0o600)
    lock=os.open(str(b.store.path)+'.guardian.lock',os.O_CREAT|os.O_RDWR,0o600)
    with tempfile.TemporaryDirectory(prefix='v11-broker-') as directory:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB);before=b.g._head()
            with pytest.raises(EvidenceError,match='ALREADY_RUNNING'):b.serve(Path(directory)/'s',seconds=.1)
            assert b.g._head()==before
        finally:os.close(lock)


def test_broker_journal_cannot_smuggle_authority(broker,peer):
    b=broker;snapshot(b,peer);d=deepcopy(b._head()['body']['details'])
    d['response']['result']['cash']='100'
    with pytest.raises(EvidenceError):b.store.safety_audit('smuggle',kind='RUNTIME_STATUS',event_id=b.key,details=d)


def test_principal_policy_changes_guardian_and_broker_config(broker,rig,worker):
    other=PaperGuardianBroker(build(rig,worker),replace(policy(),guardian_gid=os.getgid()+1))
    assert other.g.config!=broker.g.config and other.config!=broker.config
