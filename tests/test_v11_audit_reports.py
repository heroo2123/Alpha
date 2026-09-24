from copy import deepcopy
from dataclasses import replace
import fcntl
import os

import pytest

from polymarket_scanner.v11.audit_reports import AuditPolicy,AuditScheduler,AuditWorker,request_event
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.paper_coordinator import ACCOUNT_KEY
from test_v11_maker_research import rig,factory,setup,bundle,propose
from test_v11_runtime_maker import integrated


def jobs(rig,**changes):
    policy=replace(AuditPolicy('fixture-audit'),**changes)
    rig['store'].funnel('rejection',event_id=rig['context'].event_id,strategy='FUTURE_FORECAST',stage='CANDIDATE',
        state='GATED',reason='UNVALIDATED_CALIBRATION',cycle_id='fixture')
    rig['now'][0]=(int(rig['now'][0]//86400)+1)*86400+1
    scheduler=AuditScheduler(rig['store'],policy);ids=scheduler.request_due()
    return scheduler,AuditWorker(rig['coordinator'],policy),ids


def finish(worker,bound=200):
    for _ in range(bound):
        out=worker.step()
        if out['outcome']=='AUDIT_COMPLETE':return worker.store.get(out['report_id'])
    pytest.fail('bounded fixture report did not complete')


def test_schedule_daily_and_iso_weekly_requests_once_without_backfill_guess(rig):
    scheduler,worker,ids=jobs(rig)
    assert len(ids)==2 and scheduler.request_due()==()
    for key in ids:
        d=rig['store'].get(key)['body']['details']
        assert d['end']<rig['now'][0] and d['first_request_history_unknown']
        assert d['end']-d['start']==(86400 if d['period']=='DAILY' else 604800)
        assert d['delivery']=='DURABLE_LOCAL_ONLY'
    rig['now'][0]+=3*86400;scheduler.request_due()
    daily=rig['store'].latest(kind='RUNTIME_STATUS',event_id=request_event('DAILY'))
    assert daily['body']['details']['skipped_complete_windows']==2


def test_pinned_page_cannot_include_later_append_and_preserves_head(rig):
    c=rig['coordinator'];head=c.recover('existing')
    view=rig['store'].pin_read_view((('COORDINATOR_EVENT',ACCOUNT_KEY),))
    later=c.recover('later')
    assert view['heads'][0]['record_id']==head['id'] and later['seq']>view['through_seq']
    rows=rig['store'].page_through(through_seq=view['through_seq'],after_seq=head['seq']-1,limit=64)
    assert rows[-1]['id']==head['id'] and rows[-1]['sha256']==view['tip_sha256']


@pytest.mark.parametrize('args',[dict(through_seq=-1),dict(through_seq=1,after_seq=2),dict(through_seq=1,limit=65)])
def test_read_view_bounds(args,rig):
    with pytest.raises(EvidenceError,match='PAGE_BOUND'):rig['store'].page_through(**args)


def test_worker_resumes_and_freezes_new_receipts_outside_existing_job(rig):
    scheduler,worker,ids=jobs(rig,records_per_step=5)
    first=worker.step();assert first['outcome']=='AUDIT_PARTIAL_PROGRESS'
    active=worker._head()['body']['details']['state']['active'];tip=active['view']['through_seq']
    rig['store'].funnel('later',event_id=rig['context'].event_id,strategy='FUTURE_FORECAST',stage='CANDIDATE',
        state='GATED',reason='LATER_APPEND',cycle_id='later')
    rig['coordinator'].recover('new-account-after-pin')
    worker=AuditWorker(rig['coordinator'],worker.policy);done=finish(worker)['body']['details']
    assert done['coverage']['archive_scan_complete'] and done['pinned_view']['through_seq']==tip
    assert done['PAPER']['account_head_id'] is None
    assert 'LATER_APPEND' not in done['operations']['rejection_reasons']
    assert done['operations']['rejection_reasons']['UNVALIDATED_CALIBRATION']==1
    assert done['LIVE']['pnl'] is None and not done['messages_sent']
    assert done['unresolved']['current_champion']=='PROTECTED_POINTER_NOT_ATTESTED'


def test_row_cap_yields_explicit_partial_coverage_and_does_not_erase_other_window(rig):
    scheduler,worker,ids=jobs(rig,maximum_job_records=3)
    row=finish(worker);d=row['body']['details']
    assert d['coverage']['row_cap_reached'] and not d['coverage']['archive_scan_complete']
    assert d['coverage']['scanned_records']==3 and d['review_required']
    next_report=finish(worker)
    assert next_report['body']['details']['period']!=d['period']


def test_restart_after_report_before_cursor_commit_does_not_republish(rig,monkeypatch):
    scheduler,worker,ids=jobs(rig,records_per_step=256)
    original=worker._save
    def crash(head,state,**details):
        if details.get('outcome')=='AUDIT_COMPLETE':raise OSError('synthetic interruption')
        return original(head,state,**details)
    monkeypatch.setattr(worker,'_save',crash)
    with pytest.raises(OSError):worker.step()
    old=rig['store'].latest(kind='RUNTIME_STATUS',event_id='v11-audit-report:DAILY')
    worker=AuditWorker(rig['coordinator'],worker.policy);out=worker.step()
    assert out['report_id']==old['id'] and out['outcome']=='AUDIT_COMPLETE'
    assert rig['store'].latest(kind='RUNTIME_STATUS',event_id='v11-audit-report:DAILY')==old


def test_scheduler_policy_change_requires_review_and_preserves_requests(rig):
    scheduler,worker,ids=jobs(rig)
    changed=AuditScheduler(rig['store'],replace(worker.policy,version='different'))
    with pytest.raises(EvidenceError,match='POLICY_CHANGED'):changed.request_due()
    assert all(rig['store'].get(key)['id']==key for key in ids)


def test_runtime_only_schedules_and_cancellation_can_run_while_report_worker_is_locked(rig,monkeypatch):
    rt,mk=integrated(rig,monkeypatch);propose(rig)
    first=rt.tick('schedule')['body']['details']
    assert len(first['audit_request_ids'])==2
    worker=AuditWorker(rig['coordinator'],rt.audits.policy)
    path=rig['store'].path.with_name(rig['store'].path.name+'.audit.lock')
    fd=os.open(path,os.O_CREAT|os.O_WRONLY,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(EvidenceError,match='ALREADY_RUNNING'):worker.step()
        rig['sync'][0]=False
        d=rt.tick('cancel-during-report')['body']['details']
        assert len(d['retired_quote_ids'])==1 and not d['audit_request_ids']
    finally:os.close(fd)
    assert not d['financial_authority'] and rig['coordinator']._head() is None


def test_end_to_end_runtime_request_to_separate_durable_report(rig,monkeypatch):
    rt,mk=integrated(rig,monkeypatch)
    rt.tick('integrated-report-schedule')
    worker=AuditWorker(rig['coordinator'],rt.audits.policy)
    d=finish(worker)['body']['details']
    assert d['period']=='DAILY' and d['coverage']['archive_scan_complete']
    assert d['PAPER']['execution_namespace']=='V11_PAPER'
    assert d['LIVE']['status']=='NOT_OBSERVED' and d['review_required']
    assert d['rewards']['actual_verified_income'] is None


def test_account_policy_or_namespace_change_cannot_resume_another_report(rig):
    scheduler,worker,ids=jobs(rig,records_per_step=1);worker.step()
    c=rig['coordinator'];other=type(c)(c.store,policy=replace(c.policy,account_id='other-account'),correlation=c.correlation,limits=c.limits)
    with pytest.raises(EvidenceError,match='CONFIG_CHANGED'):
        AuditWorker(other,worker.policy).step()


def test_station_scope_quarantine_is_not_overwritten_by_another_scope(rig):
    for scope,state in [('forecast','QUARANTINED'),('maker','NO_NEW_RISK')]:
        rig['store'].audit('scope-'+scope,event_id='station:KATL',kind='REGISTRY',details=dict(
            action='DEMOTION',scope={'station':'KATL'},scope_key=scope,state=state))
    _,worker,_=jobs(rig)
    d=finish(worker)['body']['details']['operations']['station_latest']
    assert d['KATL|forecast']['state']=='QUARANTINED'
    assert d['KATL|maker']['state']=='NO_NEW_RISK'
    assert d['KATL|METADATA']['certification']=='NOT_INFERRED_FROM_LOCAL_CAPABILITY_CLAIMS'
