"""Complete synthetic score cohorts through finite candidate audit reporting."""
from copy import deepcopy
from dataclasses import replace

import pytest

from polymarket_scanner.v11 import audit_reports, pws_score_audit as module
from polymarket_scanner.v11.audit_reports import AuditPolicy, AuditScheduler, AuditWorker
from polymarket_scanner.v11.causal_replay import ReplayPolicy
from polymarket_scanner.v11.evidence import EvidenceError, canonical
from polymarket_scanner.v11.learning_sources import LearningSourceView
from polymarket_scanner.v11.pws_lead import PWSObservationLead
from test_v11_audit_reports import finish
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_pws_admission import joined, coordinator, arrival
from test_v11_strategy_pipeline import factory


def score(r, key):
    return PWSObservationLead(r['store']).score_first_received_report(key, observation_id='lead')


def mixed(r):
    unknown=score(r,'unknown'); r['now'][0]+=1; arrival(r)
    measured=score(r,'measured'); d=deepcopy(measured['body']['details']); d.pop('score_protocol')
    legacy=r['store'].audit('legacy',event_id=measured['event_id'],kind='MEASUREMENT',details=d)
    d['score_protocol']='UNSUPPORTED'
    unsupported=r['store'].audit('unsupported',event_id=measured['event_id'],kind='MEASUREMENT',details=d)
    return unknown,measured,legacy,unsupported


def selected(r):
    window=dict(start=0,end=r['now'][0]+1); aggregate={}
    for row in r['store'].records(kind='MEASUREMENT',limit=1000): module.fold_pws_scores(row,aggregate,window)
    return dict(selection=aggregate.get('pws_score_selection',dict(count=0,refs=[],overflow=False)),
        through_seq=r['store'].pin_read_view()['through_seq'],window=window,archive_complete=True,policy=ReplayPolicy('scores',5.))


def audit(r, **kw): return module.pws_score_audit(r['store'],**{**selected(r),**kw})


def job(r, **kw):
    policy=AuditPolicy('scores',**dict(dict(records_per_step=256,pws_score_replay=ReplayPolicy('scores',5.)),**kw))
    r['now'][0]=(int(r['now'][0]//86400)+1)*86400+1
    AuditScheduler(r['store'],policy).request_due()
    return AuditWorker(coordinator(r),policy)


def test_mixed_complete_population_keeps_unknown_and_legacy_without_writes(joined,monkeypatch):
    r=joined; rows=mixed(r); before=r['store'].pin_read_view()
    monkeypatch.setattr(r['store'],'audit',lambda *a,**kw:pytest.fail('cohort wrote evidence'))
    result=audit(r)
    assert result['complete_retained_selection'] and result['status']=='PARTIAL'
    assert result['retained_score_count']==4 and result['score_matches']==2
    assert result['reproduced_measured_count']==1 and not result['all_scores_reproduced']
    assert result['original_status_counts']==dict(UNKNOWN=1,MEASURED_FIRST_RECEIVED_REPORT=3)
    assert [x['score_ref']['id'] for x in result['rows']]==[x['id'] for x in rows]
    assert [x['status'] for x in result['rows']]==['SCORE_REPRODUCED','SCORE_REPRODUCED','GATED','GATED']
    assert result['independent_sample_count'] is None and not result['financial_authority']
    assert r['store'].pin_read_view()==before


def test_empty_cohort_and_reproduced_unknown_do_not_claim_observation_labels(joined):
    r=joined; empty=audit(r)
    assert empty['status']=='NO_RETAINED_SCORES' and not empty['all_scores_reproduced']
    score(r,'unknown'); result=audit(r)
    assert result['all_scores_reproduced'] and result['reproduced_measured_count']==0
    assert not result['target_is_true_next_published_report'] and not result['calibration_authority']


@pytest.mark.parametrize('fault',['overflow','incomplete','duplicate','hash','sequence','window','count','clockoverflow'])
def test_incomplete_or_changed_cohort_never_credits_a_prefix(joined,fault):
    r=joined; mixed(r); kw=selected(r); s=kw['selection']
    if fault=='overflow':s['overflow']=True
    elif fault=='incomplete':kw['archive_complete']=False
    elif fault=='duplicate':s['refs'][1]=deepcopy(s['refs'][0])
    elif fault=='hash':s['refs'][1]['sha256']='f'*64
    elif fault=='sequence':s['refs'][1]['seq']=kw['through_seq']+1
    elif fault=='window':kw['window']['end']=r['now'][0]-.5
    elif fault=='clockoverflow':kw['window']['end']=10**400
    else:s['count']+=1
    result=module.pws_score_audit(r['store'],**kw)
    assert result['status']=='GATED' and not result['rows'] and result['score_matches']==0
    assert not result['complete_retained_selection'] and not result['original_status_counts']


def test_missing_selected_original_gates_cohort_but_missing_dependency_keeps_row(joined,monkeypatch):
    r=joined; mixed(r); get=LearningSourceView.get
    missing=['measured:start']
    def absent(self,key):
        if key==missing[0]:raise EvidenceError('ORIGINAL_MISSING')
        return get(self,key)
    monkeypatch.setattr(LearningSourceView,'get',absent)
    result=audit(r)
    assert result['complete_retained_selection'] and result['score_matches']==1 and len(result['rows'])==4
    missing[0]='measured'; result=audit(r)
    assert result['status']=='GATED' and not result['rows'] and not result['score_matches']


def test_fold_keeps_full_overflow_count_and_exact_window_edges(joined,monkeypatch):
    r=joined; rows=mixed(r); monkeypatch.setattr(module,'MAX_SCORES',1)
    kw=selected(r); assert kw['selection']['count']==4 and len(kw['selection']['refs'])==1
    assert kw['selection']['overflow'] and audit(r)['status']=='GATED'
    aggregate={}; at=rows[0]['body']['recorded_at']
    module.fold_pws_scores(rows[0],aggregate,dict(start=at,end=at+1))
    assert aggregate['pws_score_selection']['count']==1
    module.fold_pws_scores(rows[0],aggregate,dict(start=at-1,end=at))
    assert aggregate['pws_score_selection']['count']==1


@pytest.mark.parametrize('final_summary',[False,True])
def test_output_bound_clears_successful_prefix_including_final_summary(joined,monkeypatch,final_summary):
    r=joined; mixed(r); original=audit(r); one=deepcopy(original); one['rows']=one['rows'][:1]
    budget=len(canonical(original).encode())-1 if final_summary else len(canonical(one).encode())+10
    monkeypatch.setattr(module,'MAX_BYTES',budget)
    result=audit(r)
    assert result['status']=='GATED' and result['reason']=='PWS_SCORE_AUDIT_OUTPUT_BOUND'
    assert not result['rows'] and not result['score_matches'] and not result['reproduced_measured_count']


def test_shared_deadline_and_final_check_clear_positive_flags(joined,monkeypatch):
    r=joined; mixed(r); elapsed=[0.]; original=module.canonical
    def spent(value):
        if value.get('complete_retained_selection'):elapsed[0]=6.
        return original(value)
    monkeypatch.setattr(module,'canonical',spent)
    result=audit(r,monotonic=lambda:elapsed[0])
    assert result['status']=='GATED' and not result['rows'] and not result['score_matches']


def test_shared_read_budget_exhaustion_in_last_score_clears_prior_matches(joined,monkeypatch):
    r=joined; score(r,'unknown'); r['now'][0]+=1; arrival(r); score(r,'measured')
    original=module.replay_first_received_report; calls=[]
    def run(source,key):
        calls.append(key)
        if key=='measured': source._bytes=8*1024**2
        return original(source,key)
    monkeypatch.setattr(module,'replay_first_received_report',run)
    result=audit(r)
    assert calls==['unknown','measured']
    assert result['status']=='GATED' and result['reason']=='LEARNING_SOURCE_VIEW_READ_BOUND'
    assert not result['rows'] and not result['score_matches'] and not result['complete_retained_selection']


def test_combined_temperature_account_and_score_audit_retains_separate_claims(joined):
    from test_v11_pws_admission import evaluate
    r=joined; c=coordinator(r); c.recover('account-before'); evaluate(r); mixed(r)
    result=finish(job(r,replay=ReplayPolicy('temperature',5.),account_replay=ReplayPolicy('account',5.),
        account_valuation_replay=True))['body']['details']
    assert result['economic_replay']['retained_decision_count']==1
    assert result['account_replay']['retained_command_count']==1
    assert result['pws_score_replay']['retained_score_count']==4
    assert result['pws_score_replay']['reproduced_measured_count']==1
    assert not result['acceptance_granted'] and not result['financial_authority']


def test_disabled_payload_identity_and_enabled_policy_review(joined):
    original=AuditPolicy('scores')
    assert original.payload()==dict(version='scores',records_per_step=64,maximum_step_seconds=2.,maximum_job_records=20000)
    with pytest.raises(EvidenceError,match='PWS_SCORE_REPLAY_POLICY_REQUIRED'):AuditPolicy('scores',pws_score_replay={})
    worker=job(joined)
    with pytest.raises(EvidenceError,match='POLICY_CHANGED'):
        AuditScheduler(joined['store'],replace(worker.policy,pws_score_replay=None)).request_due()


def test_scheduled_job_resumes_pinned_population_and_retains_semantic_limits(joined):
    r=joined; mixed(r); worker=job(r,records_per_step=5)
    assert worker.step()['outcome']=='AUDIT_PARTIAL_PROGRESS'
    pin=worker._head()['body']['details']['state']['active']['view']['through_seq']
    score(r,'later-score'); result=finish(worker)['body']['details']
    assert result['pinned_view']['through_seq']==pin
    assert result['pws_score_replay']['retained_score_count']==4
    assert result['coverage']['pws_score_selection_complete']
    assert not result['coverage']['retained_pws_scores_reproduced']
    assert result['unresolved']['source_pws_contribution']=='NO_ACCEPTED_ABLATION_EVIDENCE'
    assert not result['acceptance_granted'] and result['LIVE']['pnl'] is None


def test_incomplete_worker_archive_does_not_publish_favorable_score_subset(joined):
    mixed(joined); worker=job(joined,maximum_job_records=3)
    result=finish(worker)['body']['details']
    assert not result['coverage']['archive_scan_complete']
    assert result['pws_score_replay']['status']=='GATED'
    assert not result['pws_score_replay']['score_matches']


def test_report_before_cursor_recovery_does_not_repeat_scoring(joined,monkeypatch):
    r=joined; mixed(r); worker=job(r); save=worker._save
    def crash(head,state,**kw):
        if kw.get('outcome')=='AUDIT_COMPLETE':raise OSError('AFTER_REPORT')
        return save(head,state,**kw)
    monkeypatch.setattr(worker,'_save',crash)
    with pytest.raises(OSError,match='AFTER_REPORT'):finish(worker)
    report=r['store'].latest(kind='RUNTIME_STATUS',event_id='v11-audit-report:DAILY')
    monkeypatch.setattr(audit_reports,'pws_score_audit',lambda *a,**kw:pytest.fail('repeated published proof'))
    assert finish(AuditWorker(coordinator(r),worker.policy))==report


def test_finite_candidate_publishes_score_audit_without_financial_effects(joined,monkeypatch):
    import asyncio
    import httpx
    from polymarket_scanner.v11 import candidate_assembly as app
    from test_v11_candidate_assembly import scoped_plan, synthetic_clock, transport
    from test_v11_request_assembly import inputs, target
    from test_v11_runtime_health import ready, advance
    r=joined; mixed(r)
    lane=app.TemperatureLane('pws',inputs(r),(target(r),),'fixture',r['request'].valuation_policy,10.)
    cfg=scoped_plan(r,lane);cfg=replace(cfg,audits=replace(cfg.audits,records_per_step=256,pws_score_replay=ReplayPolicy('scores',5.)))
    synthetic_clock(r,monkeypatch);calls=[]
    async def run():
        async with httpx.AsyncClient(transport=transport(r,calls)) as client:
            candidate=app.assemble_candidate(r['store'],client,cfg,generation='pws-scores')
            ready(r,candidate.runtime.health)
            await candidate.run('initial')
            advance(r,(int(r['now'][0]//86400)+1)*86400+1-r['now'][0])
            for i in range(12):
                await candidate.run('score-audit-'+str(i))
                report=r['store'].latest(kind='RUNTIME_STATUS',event_id='v11-audit-report:DAILY')
                if report and report['body']['details']['pws_score_replay']['retained_score_count']:
                    return report['body']['details']
            pytest.fail('candidate failed to finish score report')
    result=asyncio.run(run()); proof=result['pws_score_replay']
    assert proof['retained_score_count']==4 and proof['score_matches']==2
    assert proof['complete_retained_selection'] and not result['acceptance_granted']
    assert not coordinator(r)._state(coordinator(r)._head())['intents']
    assert not r['store'].records(kind='TRADE') and calls and all(req.method=='GET' for req in calls)
