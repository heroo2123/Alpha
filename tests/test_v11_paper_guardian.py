"""Synthetic PAPER mechanics plus actual Linux sibling-process failure tests.

No live orders, credentials, production services, network or empirical claims.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict, replace
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

import pytest

from polymarket_scanner.v11 import guardian_lease as lease, paper_guardian as guardian
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.paper_coordinator import PaperCoordinator
from polymarket_scanner.v11.runtime_health import HealthPolicy, RuntimeHealth, SourceNeed
from test_v11_paper_coordinator import rig, coordinator, proposal, proof


ENV = {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8'}
ROOT = Path(__file__).resolve().parents[1]


@contextmanager
def child(code='import time; print("READY", flush=True); time.sleep(60)', *args):
    p = subprocess.Popen([sys.executable,'-s','-E','-c',code,*map(str,args)], stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=ENV, close_fds=True, start_new_session=True)
    try:
        assert p.stdout.readline() == b'READY\n'
        yield p
    finally:
        if p.poll() is None:
            p.kill()
        p.communicate(timeout=5)


@pytest.fixture
def worker():
    with child() as p: yield p


def build(rig, worker, **kw):
    return guardian.PaperGuardian(coordinator(rig), policy=lease.GuardianPolicy('synthetic-process-check', **kw),
        health_config='d'*64, worker=lease.process_identity(worker.pid))


def status(g, *, ready=True):
    return g._save(g._head(), status='READY' if ready else 'GATED',
                   reasons=[] if ready else ['SYNTHETIC_GATE'], cursor='')


def admission(g, **kw):
    return lease.admission_heads(g.store, account_id=g.coordinator.policy.account_id,
        account_policy_sha=g.coordinator.policy_sha, **kw)


def state(c): return c._state(c._head())


def launch(g, *, cycles=2, request=None):
    config = guardian.configuration(g.coordinator, policy=g.policy, health_config=g.health_config, worker=g.worker)
    if request is None: return guardian.launch_guardian(config, cycles=cycles)
    p = subprocess.Popen([sys.executable,'-s','-E','-m','polymarket_scanner.v11.paper_guardian'],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=ENV, close_fds=True, start_new_session=True)
    p.stdin.write(json.dumps(request if request is not None else dict(config=config, cycles=cycles)).encode())
    p.stdin.close(); p.stdin = None
    return p


def finished(p, code=0):
    out,err = p.communicate(timeout=8)
    assert p.returncode == code, (out,err)
    assert err == b''
    assert json.loads(out)['financial_authority'] is False
    return json.loads(out)


def wait_for(check, timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        result=check()
        if result: return result
        time.sleep(.02)
    raise AssertionError('local process condition did not arrive')


@pytest.mark.parametrize('field,value', [('lease_seconds',.1),('interval_seconds',0),('maximum_intents',17),
    ('maximum_intents',True),('maximum_wall_step_seconds',3),('lease_seconds',float('nan'))])
def test_policy_is_bounded(field,value):
    with pytest.raises(EvidenceError): lease.GuardianPolicy('fixture',**{field:value})


def test_separate_process_is_required(rig):
    with pytest.raises(EvidenceError,match='SEPARATE_PROCESS'):
        guardian.PaperGuardian(coordinator(rig), policy=lease.GuardianPolicy('fixture'),
            health_config='d'*64,worker=lease.process_identity(os.getpid()))


def test_required_missing_and_durable_activation_gate_fresh_coordinators(rig,worker):
    g=build(rig,worker); p=proposal(rig)
    c=PaperCoordinator(g.store,policy=g.coordinator.policy,correlation=g.coordinator.correlation,
                       limits=g.coordinator.limits,guardian_config=g.config)
    with pytest.raises(EvidenceError,match='REQUIRED_BEFORE_OPENING'):c.coordinate('absent',(p,))
    assert c._head() is None
    status(g,ready=False)
    with pytest.raises(EvidenceError,match='LEASE_GATED'):coordinator(rig).coordinate('gated',(p,))
    assert c._head() is None
    status(g)
    assert c.coordinate('reserved',(p,))['body']['details']['reserved_intent_ids']==['proposal']
    with pytest.raises(EvidenceError,match='CONFIG_CHANGED'):admission(g,required_config='e'*64)


@pytest.mark.parametrize('mode',['wall_stale','wall_back','mono_stale','boot','process_start','config','status'])
def test_invalid_or_expired_original_lease_fails_closed(rig,worker,monkeypatch,mode):
    g=build(rig,worker); row=status(g); original=lease.host_stamp(g.store)
    if mode in {'wall_stale','wall_back','mono_stale','boot'}:
        altered=dict(original)
        if mode=='wall_stale':altered['wall']+=10
        if mode=='wall_back':altered['wall']-=1
        if mode=='mono_stale':altered['monotonic']+=10
        if mode=='boot':altered['boot_id']='a'*36
        monkeypatch.setattr(lease,'host_stamp',lambda _:altered)
    else:
        row=deepcopy(row);d=row['body']['details']
        if mode=='process_start':d['process']['start_ticks']+=1
        if mode=='config':d['config_sha256']='e'*64
        if mode=='status':d['status']='GATED';d['reasons']=['FAIL']
    with pytest.raises(EvidenceError):lease.check_lease(g.store,row,account_id='account',account_policy_sha=g.coordinator.policy_sha)


def test_lease_expires_inside_account_write_after_preparation(rig,worker,monkeypatch):
    g=build(rig,worker);p=proposal(rig);status(g)
    budget=g.store._budget
    def expire(db,size):
        budget(db,size);rig['now'][0]+=3
    monkeypatch.setattr(g.store,'_budget',expire)
    with pytest.raises(EvidenceError,match='LEASE_GATED'):g.coordinator.coordinate('late',(p,))
    assert g.coordinator._head() is None


def test_changed_or_first_guardian_at_commit_is_cas_fenced(rig,worker,monkeypatch):
    g=build(rig,worker);p=proposal(rig);audit=g.store.audit
    def changed(key,**kw):
        if key=='racing':status(g,ready=False)
        return audit(key,**kw)
    monkeypatch.setattr(g.store,'audit',changed)
    with pytest.raises(EvidenceError,match='GUARDED_STATE_CHANGED'):g.coordinator.coordinate('racing',(p,))
    assert g.coordinator._head() is None


def test_cancel_and_explicit_fill_terminal_remain_available_when_guardian_gated(rig,worker):
    c=coordinator(rig);c.coordinate('reserve',(proposal(rig),));before=c.snapshot()
    g=build(rig,worker);status(g,ready=False)
    with pytest.raises(EvidenceError,match='LEASE_GATED'):c.transition('submit',intent_id='proposal',status='SUBMITTING')
    c.transition('cancel',intent_id='proposal',status='CANCEL_REQUESTED')
    assert c.snapshot()['reserved_cash']==before['reserved_cash']
    f=proof(rig,'proposal','late-fill','PAPER_FILL',fill_id='late-fill',units='1',all_in_collateral='.4',direction='BUY')
    c.record_fill('record-late',f)
    t=proof(rig,'proposal','terminal','PAPER_TERMINAL',status='CANCELED',cumulative_fill_units='1',
            all_fills_reconciled=True,terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL')
    c.reconcile_terminal('reconcile',t)
    assert state(c)['intents']['proposal']['status']=='CANCELED' and state(c)['lots']


def test_pending_cancel_survives_crash_before_plan_then_healthy_restart(rig,worker,monkeypatch):
    c=coordinator(rig);c.coordinate('reserve',(proposal(rig),));g=build(rig,worker)
    def crash(*a,**kw):raise RuntimeError('synthetic crash before plan')
    monkeypatch.setattr(g.cancellation,'plan',crash)
    with pytest.raises(RuntimeError):g._cycle()
    old=g._head()['body']['details'];assert old['pending_trigger'] and old['cancel_intents']
    g2=build(rig,worker)
    monkeypatch.setattr(g2,'_health',lambda:None)
    g2._cycle()
    assert state(c)['intents']['proposal']['cancel_requested'] is True
    assert g2._head()['body']['details']['pending_trigger'] is None
    requests=[r for r in g.store.records(kind='COORDINATOR_EVENT') if r['event_id']=='v11-paper-account-state'
              and r['body']['details']['request'].get('status')=='CANCEL_REQUESTED']
    assert len(requests)==1


def test_restart_after_account_effect_preserves_exactly_one_cancel(rig,worker,monkeypatch):
    c=coordinator(rig);c.coordinate('reserve',(proposal(rig),));g=build(rig,worker)
    commit=g.cancellation._commit
    def crash(key,request,*args,**kw):
        if request['action']=='DELIVER_LOCAL_CANCEL_REQUESTS':raise RuntimeError('synthetic journal crash')
        return commit(key,request,*args,**kw)
    monkeypatch.setattr(g.cancellation,'_commit',crash)
    with pytest.raises(RuntimeError):g._cycle()
    reserved=c.snapshot()['reserved_cash'];g2=build(rig,worker);g2._cycle()
    assert c.snapshot()['reserved_cash']==reserved
    requests=[r for r in g.store.records(kind='COORDINATOR_EVENT') if r['event_id']=='v11-paper-account-state'
              and r['body']['details']['request'].get('status')=='CANCEL_REQUESTED']
    assert len(requests)==1 and not g2._head()['body']['details']['pending_trigger']


def test_resource_policy_change_requires_new_review(rig,worker):
    g=build(rig,worker);status(g)
    g.store.limits=replace(g.store.limits,max_records=g.store.limits.max_records-1)
    changed=build(rig,worker)
    assert changed.config!=g.config
    with pytest.raises(EvidenceError,match='CONFIG_CHANGED_REVIEW'):changed._cycle()


@pytest.mark.parametrize('where',['health','delivery'])
def test_terminal_deadline_escapes_local_error_handlers(rig,worker,monkeypatch,where):
    g=build(rig,worker);g.coordinator.coordinate('reserve',(proposal(rig),))
    if where=='health':monkeypatch.setattr(g,'_health',guardian._deadline)
    else:monkeypatch.setattr(g.coordinator,'transition',lambda *a,**kw:guardian._deadline())
    with pytest.raises(guardian.GuardianDeadline):g._cycle()
    assert g._head()['body']['details']['status']=='GATED'


@pytest.mark.parametrize('mode',['stopped','dead','cpu_bound','fd_exhaustion'])
def test_actual_sibling_cancels_despite_candidate_runtime_lock_and_failure(rig,mode):
    code='''import fcntl, os, resource, sys, time
f=open(sys.argv[1], 'w'); fcntl.flock(f,fcntl.LOCK_EX)
print('READY',flush=True)
if sys.argv[2]=='cpu_bound':
    while True: pass
if sys.argv[2]=='fd_exhaustion':
    resource.setrlimit(resource.RLIMIT_NOFILE,(32,32));fds=[]
    try:
        while True:fds.append(os.open('/dev/null',os.O_RDONLY))
    except OSError:pass
time.sleep(60)
'''
    c=coordinator(rig);c.coordinate('reserve',(proposal(rig),));before=c.snapshot();saved=deepcopy(state(c))
    with child(code,str(rig['store'].path)+'.runtime.lock',mode) as p:
        g=build(rig,p)
        if mode=='stopped':os.kill(p.pid,signal.SIGSTOP)
        if mode=='dead':p.kill();p.wait(timeout=3)
        proc=launch(g);finished(proc)
    after=state(c)
    saved['intents']['proposal'].update(status='CANCEL_REQUESTED',cancel_requested=True)
    assert after==saved and c.snapshot()['reserved_cash']==before['reserved_cash']
    d=g._head()['body']['details'];assert d['process']['pid']==proc.pid and d['process']['pid']!=p.pid
    assert d['status']=='STOPPED' and d['independent_guardian_commissioned'] is False


def test_actual_sqlite_writer_contention_fails_bounded_then_restart_delivers(rig,worker):
    c=coordinator(rig);c.coordinate('reserve',(proposal(rig),));g=build(rig,worker);before=deepcopy(state(c))
    code="import sqlite3,sys,time; d=sqlite3.connect(sys.argv[1]); d.execute('BEGIN IMMEDIATE'); print('READY',flush=True); time.sleep(60)"
    with child(code,g.store.path):
        started=time.monotonic();finished(launch(g),code=2)
        assert time.monotonic()-started<4 and state(c)==before
    finished(launch(g))
    assert state(c)['intents']['proposal']['cancel_requested'] is True


def test_actual_archive_exhaustion_never_reports_success_or_changes_account(rig,worker):
    c=coordinator(rig);c.coordinate('reserve',(proposal(rig),));g=build(rig,worker);before=deepcopy(state(c))
    g.store.limits=replace(g.store.limits,max_records=1)
    finished(launch(g),code=2)
    assert state(c)==before and g._head() is None


def live_health(rig,g):
    rig['store'].clock=time.time
    m=RuntimeHealth(g.store,HealthPolicy('synthetic-real-process',10.,10.,1.,2,.01,('candidate',)),
        account_id='account',scopes={rig['context'].event_id:('fixture',)},
        sources=(SourceNeed(rig['context'].event_id,'fixture','OFFICIAL_OBSERVATION','fixture','missing',60.),),
        sync_probe=lambda:dict(synchronized=True,mechanism='SYNTHETIC_OFF_HOST_FIXTURE',offset_seconds=None))
    m.heartbeat('real-hb',worker='candidate',generation='synthetic');m.sample('real-h1');time.sleep(.02);m.sample('real-h2')
    result=guardian.PaperGuardian(g.coordinator,policy=g.policy,health_config=m.config,worker=g.worker)
    result.fixture_monitor=m
    return result


@pytest.mark.parametrize('mode',['stop','kill'])
def test_actual_guardian_death_closes_lease_while_candidate_still_lives(rig,worker,mode):
    g=live_health(rig,build(rig,worker));p=launch(g,cycles=20)
    try:
        row=wait_for(lambda:(r if (r:=g._head()) and r['body']['details']['status']=='READY' else None))
        assert row['body']['details']['process']['pid']==p.pid
        if mode=='stop':os.kill(p.pid,signal.SIGSTOP)
        else:p.kill();p.wait(timeout=3)
        with pytest.raises(EvidenceError):admission(g)
        assert worker.poll() is None
    finally:
        if p.poll() is None:p.kill()
        p.communicate(timeout=3)


def test_finite_worker_limits_are_applied_in_actual_child(rig,worker):
    g=live_health(rig,build(rig,worker));p=launch(g,cycles=20)
    try:
        wait_for(lambda:(r if (r:=g._head()) and r['body']['details']['status']=='READY' else None))
        status_text=Path(f'/proc/{p.pid}/status').read_text()
        limits=Path(f'/proc/{p.pid}/limits').read_text()
        assert 'NoNewPrivs:\t1' in status_text
        assert '536870912' in limits and 'Max open files            64' in limits
        environ=Path(f'/proc/{p.pid}/environ').read_bytes()
        assert set(item.split(b'=')[0] for item in environ.split(b'\0') if item)=={b'PATH',b'LANG'}
    finally:
        p.kill();p.communicate(timeout=3)


@pytest.mark.parametrize('envelope',[{'operation':'PLACE_ORDER'},{'config':{},'cycles':1},
    {'config':{'password':'synthetic-forbidden-field'},'cycles':1}, {'oversize':'x'*33000}])
def test_actual_input_schema_has_no_arbitrary_operation_or_secret_route(rig,worker,envelope):
    g=build(rig,worker)
    assert finished(launch(g,request=envelope),code=2)['outcome']=='FAILED_CLOSED'
    assert g._head() is None and g.coordinator._head() is None


def test_unsafe_lock_and_duplicate_worker_are_refused(rig,worker):
    g=build(rig,worker);lock=Path(str(g.store.path)+'.guardian.lock')
    lock.symlink_to(g.store.path)
    finished(launch(g),code=2);lock.unlink()
    lock.touch(mode=0o600)
    with lock.open('r+') as f:
        fcntl.flock(f,fcntl.LOCK_EX)
        finished(launch(g),code=2)
    assert g._head() is None


def test_deep_json_failure_is_redacted_in_actual_worker():
    p=subprocess.run([sys.executable,'-s','-E','-m','polymarket_scanner.v11.paper_guardian'],
        input=b'['*1500+b'0'+b']'*1500,cwd=ROOT,env=ENV,capture_output=True,timeout=5)
    assert p.returncode==2 and p.stderr==b'' and json.loads(p.stdout)['outcome']=='FAILED_CLOSED'


def test_request_rescans_remainder_under_bound_without_freeing_cash(rig,worker):
    c=coordinator(rig)
    c.coordinate('reserve',(proposal(rig,'a',units='1'),proposal(rig,'b',units='1',bucket=1)))
    g=build(rig,worker,maximum_intents=1);before=c.snapshot()['reserved_cash']
    g._cycle();assert sum(i.get('cancel_requested',False) for i in state(c)['intents'].values())==1
    g._cycle();assert all(i['cancel_requested'] for i in state(c)['intents'].values())
    assert c.snapshot()['reserved_cash']==before


def test_guardian_cycle_retains_raw_backward_clock_for_cancel_only(rig,worker):
    c=coordinator(rig);c.coordinate('reserve',(proposal(rig),));g=build(rig,worker)
    rig['now'][0]-=10;g._cycle()
    assert state(c)['intents']['proposal']['cancel_requested']
    assert g._head()['body']['recorded_at']==rig['now'][0]
    with pytest.raises(EvidenceError,match='CLOCK_REGRESSION'):
        g.store.audit('ordinary',event_id='clock',kind='MEASUREMENT',details={})


def test_interleaved_new_heartbeat_is_conservatively_cancelled_until_resampled(rig,worker):
    c=coordinator(rig);c.coordinate('reserve',(proposal(rig),));g=live_health(rig,build(rig,worker))
    g.fixture_monitor.heartbeat('new-hb',worker='candidate',generation='synthetic')
    with pytest.raises(EvidenceError,match='HEARTBEAT_CHANGED'):g._health()
    g._cycle()
    assert state(c)['intents']['proposal']['cancel_requested'] is True
    g.fixture_monitor.sample('new-sample');g._cycle()
    assert state(c)['intents']['proposal']['cancel_requested'] is True
    assert g._head()['body']['details']['status']=='READY'
