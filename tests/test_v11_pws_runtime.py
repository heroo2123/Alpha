from dataclasses import replace
from datetime import datetime, timezone
import fcntl

import pytest

from polymarket_scanner.v11 import pws_quality as qc
from polymarket_scanner.v11 import pws_runtime as worker
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.runtime_health import SourceNeed, admission_heads
from polymarket_scanner.v11.weather_sources import madis_request, normalize_weather_capture
from test_v11_pws_quality import policy, official
from test_v11_paper_coordinator import rig
from test_v11_runtime_health import monitor, ready, advance


def xml(at, *, moved=False):
    rows=[]
    for station,latitude in [('A',33.11 if moved else 33.01),('B',33.03),('C',33.05)]:
        for lag,temp in [(660,292.15),(360,292.65),(60,293.15)]:
            stamp=datetime.fromtimestamp(at-lag,timezone.utc).strftime('%Y-%m-%dT%H:%M')
            rows.append(f'<record var="V-T" shef_id="{station}" elev="300" lat="{latitude}" lon="-84" '
                        f'ObTime="{stamp}" provider="APRSWXNET" data_value="{temp}" QCD="S" QCA="59" QCR="0" />')
    return '<mesonet>'+''.join(rows)+'</mesonet>'


def capture(r,label='raw',*,event=None,moved=False,normalize=True):
    s=r['store'];e=event or r['context'].event_id;at=r['now'][0]
    request=madis_request(event_id=e,station='KATL',latitude=33.,longitude=-84.)
    raw=s.capture(label,event_id=e,kind='PWS_OBSERVATION',provider='NOAA_MADIS_CWOP',source_identity=request.source_identity,
        revision=label,payload=dict(response=xml(at,moved=moved),request_params=dict(request.params)),evidence_class='SYNTHETIC')
    return normalize_weather_capture(s,raw['id'],record_id=label+':norm',station='KATL') if normalize else raw


def assemble(r,monkeypatch,*,settings=None):
    e=r['context'].event_id
    needs=(SourceNeed(e,'fixture','OFFICIAL_OBSERVATION','fixture','KATL',60.),
           SourceNeed(e,'PWS_OBSERVATION_LEAD','PWS_OBSERVATION','ALPHA_PWS_QC','KATL',600.))
    m=monitor(r,monkeypatch,sources=needs,scopes={e:('fixture','PWS_OBSERVATION_LEAD')});ready(r,m)
    return worker.PWSQualityWorker(r['store'],m,settings or worker.PWSQualitySettings((worker.PWSQualityPlan(e,official(),policy()),)))


def test_archived_raw_to_qc_to_health_preserves_times_and_replay(rig,monkeypatch):
    raw=capture(rig);w=assemble(rig,monkeypatch);s=rig['store']
    result=w.step('qc');d=result['body']['details'];row=s.get(d['qc_id']);p=row['body']['payload']
    assert d['outcome']=='PWS_QC_RECORDED' and p['health']=='HEALTHY' and p['usable_station_count']==3
    assert p['source_captures']==[dict(id=raw['id'],sha256=raw['sha256'])]
    assert {x['first_received_at'] for x in p['stations']}=={raw['body']['received_at']}
    assert not p['lead_advantage_verified'] and not p['settlement_authority'] and row['body']['evidence_class']=='SYNTHETIC'
    w.health.sample('after-qc')
    guards=admission_heads(s,account_id='account',event_id=rig['context'].event_id,strategies=('PWS_OBSERVATION_LEAD',))
    assert len([h for h in guards if h[0]=='REGISTRY' and h[1].startswith('pws:')])==3
    tip=s.pin_read_view();advance(rig)
    assert w.step('qc')==result and s.pin_read_view()==tip
    again=w.step('another')['body']['details']
    assert again['outcome']=='UNCHANGED_INPUTS_NO_RECEIPT_RENEWAL' and s.get(again['qc_id'])==row
    assert not s.records(kind='TRADE') and s.latest(kind='COORDINATOR_EVENT',event_id='v11-paper-account-state') is None


def test_new_unprocessed_raw_invalidates_only_pws_dependency(rig,monkeypatch):
    capture(rig);w=assemble(rig,monkeypatch);row=w.step('qc');s=rig['store']
    capture(rig,'new-unprocessed',normalize=False)
    d=w.step('not-ready')['body']['details']
    assert d['reason']=='PWS_RUNTIME_CURRENT_NORMALIZATION_REQUIRED'
    w.health.sample('latest-health')
    assert admission_heads(s,account_id='account',event_id=rig['context'].event_id,strategies=('fixture',))
    with pytest.raises(EvidenceError,match='REQUIRED_SOURCE'):
        admission_heads(s,account_id='account',event_id=rig['context'].event_id,strategies=('PWS_OBSERVATION_LEAD',))
    assert s.get(row['body']['details']['qc_id'])['body']['payload']['health']=='HEALTHY'  # Historical result retained.


def test_other_event_metadata_drift_invalidates_and_cannot_be_cleared_by_replay(rig,monkeypatch):
    capture(rig);w=assemble(rig,monkeypatch);w.step('first');s=rig['store']
    moved=capture(rig,'moved',event='other-event',moved=True)
    sample=next(x for x in qc.samples_from_capture(s,moved['id'],as_of=rig['now'][0]) if x.station=='A')
    qc.PWSIdentityTracker(s,policy()).observe('independent-event-drift',sample,raw_evidence_id=moved['id'])
    w.health.sample('drift-health')
    with pytest.raises(EvidenceError,match='REQUIRED_SOURCE'):
        admission_heads(s,account_id='account',event_id=rig['context'].event_id,strategies=('PWS_OBSERVATION_LEAD',))
    result=w.step('recompute')['body']['details'];p=s.get(result['qc_id'])['body']['payload']
    assert p['health']=='DEGRADED' and p['usable_station_count']==2
    assert 'PERSISTENT_METADATA_QUARANTINE' in next(x for x in p['stations'] if x['station_key'].endswith(':A'))['rejection_reasons']


@pytest.mark.parametrize('at_stage',['metadata','qc'])
def test_interruption_resumes_saved_work_without_redating_committed_result(rig,monkeypatch,at_stage):
    capture(rig);w=assemble(rig,monkeypatch);s=rig['store'];original_capture=s.capture;original_save=w._save
    def stop_capture(key,**kw):
        if kw.get('provider')=='ALPHA_PWS_QC':raise RuntimeError('INTERRUPTED_AFTER_METADATA')
        return original_capture(key,**kw)
    def stop_save(key,state,**kw):
        if kw.get('outcome')=='PWS_QC_RECORDED':raise RuntimeError('INTERRUPTED_AFTER_QC')
        return original_save(key,state,**kw)
    with monkeypatch.context() as patch:
        if at_stage=='metadata':patch.setattr(s,'capture',stop_capture)
        else:patch.setattr(w,'_save',stop_save)
        with pytest.raises(RuntimeError,match='INTERRUPTED_AFTER'):w.step('interrupted')
    metadata=s.records(kind='REGISTRY');existing=s.latest_source(kind='PWS_OBSERVATION',event_id=rig['context'].event_id,
                                                               provider='ALPHA_PWS_QC',source_identity='KATL')
    assert w._head()['body']['details']['state']['active']
    advance(rig)
    restarted=worker.PWSQualityWorker(s,w.health,w.settings);result=restarted.step('resume')['body']['details']
    assert result['outcome']=='PWS_QC_RECORDED' and s.records(kind='REGISTRY')==metadata
    if existing:assert s.get(result['qc_id'])==existing
    assert restarted._head()['body']['details']['state']['active'] is None


@pytest.mark.parametrize('race',['source','metadata'])
def test_qc_commit_fences_new_source_or_cross_event_identity(rig,monkeypatch,race):
    normalized=capture(rig);s=rig['store'];original=s.capture
    def changed(key,**kw):
        if kw.get('provider')=='ALPHA_PWS_QC':
            if race=='source':original('racing-source',event_id=rig['context'].event_id,kind='PWS_OBSERVATION',
                provider='fixture',source_identity='new',revision='new',payload={},evidence_class='SYNTHETIC')
            else:s.audit('racing-metadata',event_id='pws:APRSWXNET:A',kind='REGISTRY',details=dict(quarantined=True))
        return original(key,**kw)
    monkeypatch.setattr(s,'capture',changed)
    with pytest.raises(EvidenceError,match='ARCHIVE_STATE_CHANGED'):
        qc.archive_neighborhood(s,'qc',event_id=rig['context'].event_id,capture_ids=(normalized['id'],),official=official(),policy=policy())
    assert s.latest_source(kind='PWS_OBSERVATION',event_id=rig['context'].event_id,provider='ALPHA_PWS_QC',source_identity='KATL') is None


def test_health_identity_guards_fence_race_at_account_commit_boundary(rig,monkeypatch):
    capture(rig);w=assemble(rig,monkeypatch);w.step('qc');w.health.sample('fresh');s=rig['store']
    heads=admission_heads(s,account_id='account',event_id=rig['context'].event_id,strategies=('PWS_OBSERVATION_LEAD',))
    s.audit('metadata-after-health-check',event_id='pws:APRSWXNET:A',kind='REGISTRY',details=dict(quarantined=True))
    with pytest.raises(EvidenceError,match='GUARDED_STATE_CHANGED'):
        s.audit('opening-boundary',event_id='test-boundary',kind='MEASUREMENT',details={},expected_heads=heads)


def test_window_overflow_is_explicit_without_selecting_a_profitable_subset(rig,monkeypatch):
    capture(rig,'one');capture(rig,'two')
    settings=worker.PWSQualitySettings((worker.PWSQualityPlan(rig['context'].event_id,official(),policy()),),maximum_captures=1)
    w=assemble(rig,monkeypatch,settings=settings);d=w.step('overflow')['body']['details']
    assert d['reason']=='PWS_RUNTIME_CAPTURE_WINDOW_OVERFLOW'
    assert rig['store'].latest_source(kind='PWS_OBSERVATION',event_id=rig['context'].event_id,provider='ALPHA_PWS_QC',source_identity='KATL') is None


def test_clock_gate_retains_inputs_and_defers_qc(rig,monkeypatch):
    normalized=capture(rig);w=assemble(rig,monkeypatch);rig['sync'][0]=False
    assert w.step('clock')['body']['details']['outcome']=='DEFERRED_CLOCK_UNHEALTHY'
    assert rig['store'].get(normalized['id'])==normalized
    assert not rig['store'].records(kind='REGISTRY')


def test_time_bound_keeps_pending_input_identity_for_next_step(rig,monkeypatch):
    capture(rig);w=assemble(rig,monkeypatch)
    with monkeypatch.context() as patch:
        def bounded(*a,**kw):raise EvidenceError('PWS_QC_TIME_BOUND')
        patch.setattr(worker,'archive_neighborhood',bounded)
        d=w.step('bounded')['body']['details']
    assert d['outcome']=='QC_PROGRESS_RETAINED' and d['state']['active']
    pending=d['state']['active']['qc_id'];result=w.step('next')['body']['details']
    assert result['qc_id']==pending and result['outcome']=='PWS_QC_RECORDED'


def test_worker_configuration_recovery_and_exclusive_lock_fail_closed(rig,monkeypatch):
    capture(rig);w=assemble(rig,monkeypatch);w.step('one')
    changed=worker.PWSQualityWorker(rig['store'],w.health,replace(w.settings,maximum_captures=32))
    with pytest.raises(EvidenceError,match='CONFIG_CHANGED_REVIEW'):changed.step('changed')
    path=rig['store'].path.with_name(rig['store'].path.name+'.pws-quality.lock')
    with path.open('r') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(EvidenceError,match='ALREADY_RUNNING'):w.step('concurrent')


def test_qc_replay_conflicting_policy_is_rejected(rig):
    normalized=capture(rig);kw=dict(event_id=rig['context'].event_id,capture_ids=(normalized['id'],),official=official(),policy=policy())
    first=qc.archive_neighborhood(rig['store'],'qc',**kw)
    assert qc.archive_neighborhood(rig['store'],'qc',**kw)==first
    with pytest.raises(EvidenceError,match='REPLAY_CONFLICT'):
        qc.archive_neighborhood(rig['store'],'qc',**dict(kw,policy=policy(fresh_seconds=300.)))
