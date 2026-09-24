from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
import subprocess

import pytest

from polymarket_scanner.v11 import runtime_health as health
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from polymarket_scanner.v11.paper_cancellation import PaperCancellation, CancellationPolicy
from test_v11_paper_coordinator import rig, coordinator, proposal


def monitor(rig, monkeypatch, *, sources=None, scopes=None, policy=None):
    rig['mono'] = [100.]; rig['boot'] = ['a'*36]; rig['sync'] = [True]
    monkeypatch.setattr(health, 'host_stamp', lambda store:dict(boot_id=rig['boot'][0], monotonic=rig['mono'][0], wall=store.clock()))
    # paper_runtime imports the same function explicitly.
    from polymarket_scanner.v11 import paper_runtime
    monkeypatch.setattr(paper_runtime, 'host_stamp', health.host_stamp)
    p = policy or health.HealthPolicy('fixture', 5., 5., .25, 2, .5, ('worker',))
    event = rig['context'].event_id
    sources = sources or (health.SourceNeed(event, 'fixture', 'OFFICIAL_OBSERVATION', 'fixture', rig['context'].station_id, 60.),)
    return health.RuntimeHealth(rig['store'], p, account_id='account', scopes=scopes or {event:('fixture',)}, sources=sources,
        sync_probe=lambda:dict(synchronized=rig['sync'][0], mechanism='SYNTHETIC_OFF_HOST_FIXTURE', reason='TEST_ONLY', offset_seconds=None))


def advance(rig, seconds=1):
    rig['mono'][0] += seconds; rig['now'][0] += seconds


def ready(rig, m):
    m.heartbeat('hb1', worker='worker', generation='one'); first = m.sample('sample1')
    assert first['body']['details']['global_reasons'] == ['CLOCK_RECOVERY_SAMPLES_PENDING']
    advance(rig); m.heartbeat('hb2', worker='worker', generation='one'); row = m.sample('sample2')
    assert not row['body']['details']['global_reasons']
    return row


def gate(rig, strategies=('fixture',)):
    return health.admission_heads(rig['store'], account_id='account', event_id=rig['context'].event_id, strategies=strategies)


@pytest.mark.parametrize('raw,code,expected', [(b'yes\n',0,True),(b'no\n',0,False),(b'yes\n',1,None),(b'maybe\n',0,None)])
def test_read_only_sync_probe_accepts_only_successful_exact_local_status(monkeypatch, raw, code, expected):
    calls=[]
    def command(args, **kw):
        calls.append((args,kw)); return SimpleNamespace(stdout=raw,returncode=code)
    monkeypatch.setattr(health.subprocess, 'run', command)
    result=health.local_sync_status()
    assert result['synchronized'] is expected and result['offset_seconds'] is None
    args,kw=calls[0]
    assert args==['/usr/bin/timedatectl','show','--property=NTPSynchronized','--value']
    assert kw['timeout']==2 and not kw.get('shell') and 'sudo' not in args
    assert set(kw['env'])=={'PATH','LC_ALL','SYSTEMD_PAGER'}


def test_sync_timeout_is_unknown_without_raw_diagnostics(monkeypatch):
    monkeypatch.setattr(health.subprocess,'run',lambda *a,**kw:(_ for _ in ()).throw(subprocess.TimeoutExpired('secret',2)))
    assert health.local_sync_status()['reason']=='SYNC_STATUS_UNAVAILABLE'


def test_recovery_requires_distinct_spaced_samples_and_replay_is_not_a_heartbeat(rig,monkeypatch):
    m=monitor(rig,monkeypatch); m.heartbeat('hb1',worker='worker',generation='g')
    first=m.sample('s1'); same=m.sample('s2')
    assert same['body']['details']['stable_samples']==1
    with pytest.raises(EvidenceError,match='CLOCK_OR_LIVENESS'):gate(rig)
    advance(rig); assert m.sample('s1')==first
    assert m.sample('s3')['body']['details']['stable_samples']==2
    assert gate(rig)
    advance(rig,6)
    with pytest.raises(EvidenceError,match='CLOCK_OR_LIVENESS'):gate(rig)


@pytest.mark.parametrize('mode',['sync_loss','boot_change','wall_jump','monotonic_regression'])
def test_clock_and_boot_failures_gate_openings_without_timestamp_repair(rig,monkeypatch,mode):
    m=monitor(rig,monkeypatch);ready(rig,m)
    if mode=='sync_loss':rig['sync'][0]=False
    elif mode=='boot_change':rig['boot'][0]='b'*36
    elif mode=='wall_jump':rig['now'][0]+=10
    else:rig['mono'][0]-=2
    row=m.sample('failure');d=row['body']['details']
    assert d['clock_reasons'] and row['body']['recorded_at']==rig['now'][0]
    with pytest.raises(EvidenceError,match='CLOCK_OR_LIVENESS'):gate(rig)
    assert not d['financial_authority'] and not d['independent_guardian_commissioned']


def test_source_outage_only_gates_the_dependent_strategy(rig,monkeypatch):
    event=rig['context'].event_id
    sources=(health.SourceNeed(event,'fixture','OFFICIAL_OBSERVATION','fixture',rig['context'].station_id,60.),
             health.SourceNeed(event,'PWS_OBSERVATION_LEAD','PWS_OBSERVATION','ALPHA_PWS_QC','unavailable',60.))
    m=monitor(rig,monkeypatch,sources=sources,scopes={event:('fixture','PWS_OBSERVATION_LEAD')});ready(rig,m)
    assert gate(rig)
    with pytest.raises(EvidenceError,match='REQUIRED_SOURCE'):gate(rig,('PWS_OBSERVATION_LEAD',))
    d=m.sample('same')['body']['details']
    assert not health.cancellation_required(d,event,('fixture',))
    assert health.cancellation_required(d,event,('PWS_OBSERVATION_LEAD',))


def test_superseded_source_and_heartbeat_require_new_health_snapshot(rig,monkeypatch):
    m=monitor(rig,monkeypatch);ready(rig,m);m.heartbeat('newhb',worker='worker',generation='one')
    with pytest.raises(EvidenceError,match='HEARTBEAT_CHANGED'):gate(rig)
    m.sample('resample');assert gate(rig)
    old=rig['store'].get(rig['source_id'])['body']
    rig['store'].capture('revised',event_id=rig['context'].event_id,kind='OFFICIAL_OBSERVATION',provider='fixture',
        source_identity=rig['context'].station_id,revision='new',observed_at=rig['now'][0],payload=old['payload'],evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='SOURCE_GATED_OR_CHANGED'):gate(rig)
    m.sample('new-source');assert gate(rig)


def test_heartbeat_expires_even_when_monitor_sample_has_longer_lease(rig,monkeypatch):
    p=health.HealthPolicy('fixture',30.,2.,.25,2,.5,('worker',))
    m=monitor(rig,monkeypatch,policy=p);ready(rig,m);advance(rig,2)
    with pytest.raises(EvidenceError,match='HEARTBEAT_EXPIRED'):gate(rig)


def test_backward_wall_clock_still_delivers_cancel_and_never_lowers_capture_high_water(rig,monkeypatch):
    c=coordinator(rig);p=proposal(rig,units='2');c.coordinate('reserve',(p,))
    m=monitor(rig,monkeypatch);ready(rig,m);high=rig['now'][0]
    rig['now'][0]-=10;rig['mono'][0]+=1
    bad=m.sample('bad-clock');assert bad['body']['recorded_at']==high-10
    cancel=PaperCancellation(c,CancellationPolicy('fixture',2,10));cancel.plan('plan',trigger_id=bad['id'])
    row=cancel.advance('send',plan_id='plan')
    assert row['body']['details']['locally_requested_count']==1
    assert c.snapshot()['reserved_cash']=='0.8' and not row['body']['details']['reports'][0]['confirmed_paper_cancellation']
    assert row['body']['chronology']=='SAFETY_SEQUENCE_WITH_RAW_WALL_TIME'
    with pytest.raises(EvidenceError,match='CLOCK_REGRESSION'):
        rig['store'].capture('backdated',event_id=rig['context'].event_id,kind='MODEL',provider='fixture',source_identity='x',revision='1',payload={})
    # Restart keeps the raw regression and original high-water mark.
    reopened=EvidenceStore(rig['store'].path,'V11_PAPER',clock=lambda:rig['now'][0]);assert reopened.get('bad-clock')==bad
    rig['now'][0]=high-1
    with pytest.raises(EvidenceError,match='CLOCK_REGRESSION'):
        reopened.audit('opening',event_id='x',kind='MEASUREMENT',details={})


@pytest.mark.parametrize('mutation',['cash','faults','units','new_intent'])
def test_safety_cancel_append_cannot_mutate_account_economics_or_clear_faults(rig,mutation):
    c=coordinator(rig);p=proposal(rig,units='2');c.coordinate('reserve',(p,))
    head=c._head();d=deepcopy(head['body']['details']);state=d['state']
    state['intents'][p.proposal_id].update(status='CANCEL_REQUESTED',cancel_requested=True)
    if mutation=='cash':state['cash']='11'
    elif mutation=='faults':state['faults']=['changed']
    elif mutation=='units':state['intents'][p.proposal_id]['units']='3'
    else:state['intents']['unauthorized']=deepcopy(state['intents'][p.proposal_id])
    d['request']=dict(action='TRANSITION',status='CANCEL_REQUESTED',intent_id=p.proposal_id)
    with pytest.raises(EvidenceError,match='STATE_MUTATION_REFUSED'):
        rig['store'].safety_audit('bad',event_id=head['event_id'],kind='COORDINATOR_EVENT',details=d)


def test_safety_audit_rejects_opening_and_model_or_valuation_authority(rig):
    for kind,version in [('COORDINATOR_EVENT','alpha_v11_paper_coordinator_v1'),('MEASUREMENT','alpha_v11_valuation_v1'),('MODEL_EVENT','x')]:
        with pytest.raises(EvidenceError,match='CANNOT_AUTHORIZE'):
            rig['store'].safety_audit('bad'+kind,event_id='v11-paper-account-state',kind=kind,
                details=dict(version=version,request=dict(action='TRANSITION',status='SUBMITTING')))


def test_health_gate_is_atomic_with_reservation_and_submission(rig,monkeypatch):
    m=monitor(rig,monkeypatch);ready(rig,m);c=coordinator(rig);p=proposal(rig,units='2')
    # The proposal's book is unrelated to the required official source.
    assert c.coordinate('reserve',(p,))['body']['details']['reserved_intent_ids']==[p.proposal_id]
    rig['sync'][0]=False;m.sample('lost-sync')
    with pytest.raises(EvidenceError,match='CLOCK_OR_LIVENESS'):
        c.transition('submit',intent_id=p.proposal_id,status='SUBMITTING')
    assert c._state(c._head())['intents'][p.proposal_id]['status']=='RESERVED'


def test_health_change_racing_account_commit_cannot_authorize_opening(rig,monkeypatch):
    m=monitor(rig,monkeypatch);ready(rig,m);p=proposal(rig,units='2');c=coordinator(rig)
    original=rig['store'].audit
    def race(key,**kw):
        if key=='reserve':rig['sync'][0]=False;m.sample('racing-health')
        return original(key,**kw)
    monkeypatch.setattr(rig['store'],'audit',race)
    with pytest.raises(EvidenceError,match='GUARDED_STATE_CHANGED'):c.coordinate('reserve',(p,))
    assert c._head() is None


def test_clock_recovers_only_after_new_good_samples_without_clearing_other_faults(rig,monkeypatch):
    m=monitor(rig,monkeypatch);ready(rig,m);rig['sync'][0]=False;m.sample('bad')
    rig['sync'][0]=True;advance(rig);m.sample('recover1')
    with pytest.raises(EvidenceError,match='CLOCK_OR_LIVENESS'):gate(rig)
    advance(rig);m.sample('recover2');assert gate(rig)


def test_configuration_change_and_unconfigured_scope_fail_closed(rig,monkeypatch):
    m=monitor(rig,monkeypatch);ready(rig,m)
    changed=health.RuntimeHealth(rig['store'],replace(m.policy,version='changed'),account_id='account',scopes=m.scopes,sources=m.sources,sync_probe=m.sync_probe)
    with pytest.raises(EvidenceError,match='CONFIG_CHANGED'):changed.sample('changed')
    with pytest.raises(EvidenceError,match='SCOPE_OR_VERSION'):gate(rig,('unconfigured',))


def test_archive_clock_high_water_ahead_of_health_is_not_silently_ignored(rig,monkeypatch):
    m=monitor(rig,monkeypatch);ready(rig,m);real=rig['now'][0]
    rig['now'][0]+=100
    rig['store'].audit('future-clock',event_id='diagnostic',kind='RUNTIME_STATUS',details={})
    rig['now'][0]=real
    d=m.sample('restored-clock')['body']['details']
    assert 'EVIDENCE_CLOCK_HIGH_WATER_AHEAD' in d['clock_reasons']
    with pytest.raises(EvidenceError,match='CLOCK_OR_LIVENESS'):gate(rig)


def test_missing_boot_identity_gates_without_disabling_health_audit(rig,monkeypatch):
    m=monitor(rig,monkeypatch);ready(rig,m);rig['boot'][0]='UNKNOWN'
    d=m.sample('unknown-boot')['body']['details']
    assert 'LOCAL_BOOT_ID_UNAVAILABLE' in d['clock_reasons']
