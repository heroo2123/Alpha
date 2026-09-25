"""Durable daily/weekly audit requests and a separate bounded reporting worker.

Scheduling performs no historical scan. Reporting is resumable, retains a pinned
archive boundary, sends no messages and cannot enter an account mutation path.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import fcntl
import os
import resource
import time

from .evidence import EvidenceError, digest, finite, identity
from .paper_coordinator import ACCOUNT_KEY
from .performance import PerformanceLab
from .execution_costs import ExecutionCostPolicy
from .causal_replay import ReplayPolicy, fold_replay_decisions, replay_audit
from .account_replay import fold_account_commands, account_replay_audit
from .pws_score_audit import fold_pws_scores, pws_score_audit
from .runtime_health import KEY as HEALTH_KEY


VERSION = 'alpha_v11_audit_reports_v1'
WORKER_KEY = 'v11-audit-report-worker'
PERIODS = {'DAILY':86400,'WEEKLY':7*86400}
LAYOUT_VERSION = 'alpha_v11_audit_drift_lifecycle_counts_v1'


@dataclass(frozen=True)
class AuditPolicy:
    version: str
    records_per_step: int = 64
    maximum_step_seconds: float = 2.
    maximum_job_records: int = 20000
    execution_costs: ExecutionCostPolicy | None = None
    replay: ReplayPolicy | None = None
    account_replay: ReplayPolicy | None = None
    account_valuation_replay: bool = False
    pws_score_replay: ReplayPolicy | None = None

    def __post_init__(self):
        identity(self.version)
        if self.replay is not None and not isinstance(self.replay,ReplayPolicy):
            raise EvidenceError('AUDIT_REPLAY_POLICY_REQUIRED')
        if self.account_replay is not None and not isinstance(self.account_replay,ReplayPolicy):
            raise EvidenceError('AUDIT_ACCOUNT_REPLAY_POLICY_REQUIRED')
        if self.pws_score_replay is not None and not isinstance(self.pws_score_replay,ReplayPolicy):
            raise EvidenceError('AUDIT_PWS_SCORE_REPLAY_POLICY_REQUIRED')
        if (type(self.account_valuation_replay) is not bool
                or self.account_valuation_replay and self.account_replay is None):
            raise EvidenceError('AUDIT_ACCOUNT_VALUATION_REPLAY_POLICY_REQUIRED')
        if self.execution_costs is not None and not isinstance(self.execution_costs,ExecutionCostPolicy):
            raise EvidenceError('AUDIT_EXECUTION_COST_POLICY_REQUIRED')
        if (type(self.records_per_step) is not int or not 1 <= self.records_per_step <= 256
                or type(self.maximum_job_records) is not int or not 1 <= self.maximum_job_records <= 100000
                or not 0 < finite(self.maximum_step_seconds) <= 5): raise EvidenceError('AUDIT_POLICY_BOUND')

    def payload(self):
        value=asdict(self)
        if self.execution_costs is None:value.pop('execution_costs')
        if self.replay is None:value.pop('replay')
        if self.account_replay is None:value.pop('account_replay')
        if not self.account_valuation_replay:value.pop('account_valuation_replay')
        if self.pws_score_replay is None:value.pop('pws_score_replay')
        return value


def request_event(period): return 'v11-audit-request:'+period


class AuditScheduler:
    def __init__(self, store, policy):
        if not isinstance(policy,AuditPolicy): raise EvidenceError('AUDIT_POLICY_REQUIRED')
        self.store,self.policy=store,policy; self.config=digest(policy.payload())

    def request_due(self):
        """At most two request writes; no report computation in the safety loop."""
        now=finite(self.store.clock()); day=datetime.fromtimestamp(now,timezone.utc).replace(hour=0,minute=0,second=0,microsecond=0)
        ends={'DAILY':day.timestamp(),'WEEKLY':(day-timedelta(days=day.weekday())).timestamp()}; ids=[]
        for period,end in ends.items():
            if end < PERIODS[period]: continue
            event=request_event(period); previous=self.store.latest(kind='RUNTIME_STATUS',event_id=event)
            if previous:
                old=previous['body']['details']
                if old.get('config_sha256')!=self.config: raise EvidenceError('AUDIT_POLICY_CHANGED_REVIEW_REQUIRED')
                if old['end']>=end: continue  # No replay/backward-clock duplicate or renewal.
            key='audit-request:'+digest([self.store.namespace,period,end])
            skipped=0 if previous is None else max(0,int((end-old['end'])/PERIODS[period])-1)
            row=self.store.audit(key,event_id=event,kind='RUNTIME_STATUS',details=dict(
                version=VERSION,config_sha256=self.config,period=period,start=end-PERIODS[period],end=end,
                skipped_complete_windows=skipped,first_request_history_unknown=previous is None,
                delivery='DURABLE_LOCAL_ONLY',financial_authority=False),
                expected_previous_seq=previous['seq'] if previous else 0)
            ids.append(row['id'])
        return tuple(ids)


def _bump(counts,key):
    key=str(key)
    if key not in counts and len(counts)>=128: key='OTHER_OVER_CAP'
    counts[key]=counts.get(key,0)+1


def _sample(samples,row):
    if len(samples)<32: samples.append(dict(id=row['id'],sha256=row['sha256'],seq=row['seq']))


def _aggregate():
    return dict(in_window_records=0,clock_untrusted_records=0,source_states={},source_reasons={},
        funnel_counts={},rejection_reasons={},account_outcomes={},station_transitions={},station_latest={},
        rule_drifts=0,rule_quarantines={},model_actions={},model_result_ids=[],learning_watermarks=[],
        runtime_outcomes={},health_failure_samples=0,markout_counts={},
        resting_admission_checks={},paper_cancellation_outcomes={},maker_retirement_reasons={},drift_outcomes={},
        incident_refs=[],malformed_records=0,metadata_overflow=False,runtime_durations=dict(count=0,total=0.,maximum=0.))


def _fold(row,a,window):
    b=row['body'];d=b.get('details',{});at=b['recorded_at'];kind=row['kind']
    # State as of the window end; future appends and future-window rows stay out.
    if at>=window['end']: return
    if kind=='REGISTRY' and d.get('action') in {'METADATA','DEMOTION'}:
        station=(d.get('metadata') or d.get('scope') or {}).get('station',row['event_id'])
        scope=station+'|'+(d.get('scope_key') or 'METADATA')
        if scope in a['station_latest'] or len(a['station_latest'])<128:
            a['station_latest'][scope]=dict(station=station,state=d.get('state'),record_id=row['id'],scope_key=d.get('scope_key'),
                                            certification='NOT_INFERRED_FROM_LOCAL_CAPABILITY_CLAIMS')
        else:a['metadata_overflow']=True
    if kind=='RULE_STATE':
        if row['event_id'] in a['rule_quarantines'] or len(a['rule_quarantines'])<128:
            a['rule_quarantines'][row['event_id']]=dict(quarantined=d.get('quarantined'),record_id=row['id'])
        else:a['metadata_overflow']=True
    if not window['start']<=at:return
    a['in_window_records']+=1
    if b.get('chronology')=='SAFETY_SEQUENCE_WITH_RAW_WALL_TIME':a['clock_untrusted_records']+=1
    if kind=='SOURCE_RESULT':
        _bump(a['source_states'],b['provider']+':'+b['state']);_bump(a['source_reasons'],b['reason'])
        if b['state']!='SUCCESS':_sample(a['incident_refs'],row)
    elif kind=='FUNNEL':
        _bump(a['funnel_counts'],'|'.join(b[k] for k in ('strategy','stage','state')))
        if b['state']!='PASS':_bump(a['rejection_reasons'],b['reason'])
    elif kind=='COORDINATOR_EVENT' and row['event_id']==ACCOUNT_KEY:
        for item in d.get('results',[]):
            _bump(a['account_outcomes'],item.get('outcome','UNKNOWN'))
            if item.get('outcome')!='RESERVED_RESEARCH':_bump(a['rejection_reasons'],item.get('reason','UNKNOWN'))
    elif kind=='REGISTRY':
        if d.get('action') in {'METADATA','DEMOTION'}:_bump(a['station_transitions'],str(d.get('state')))
    elif kind=='RULE_STATE':
        if d.get('changed') is True:a['rule_drifts']+=1;_sample(a['incident_refs'],row)
    elif kind=='MODEL_EVENT':
        _bump(a['model_actions'],d.get('action','UNKNOWN'))
        if (d.get('version')=='alpha_v11_reviewed_drift_worker_v1' and d.get('action')=='DRIFT_MEASUREMENT'
                and (d.get('result') or {}).get('version')=='alpha_v11_scoped_maker_markout_drift_v1'):
            measured=d['result'];request=measured['request'];policy=request['policy']
            if digest(measured)!=d.get('result_sha256') or measured.get('financial_authority') is not False:
                raise EvidenceError('AUDIT_MARKOUT_MEASUREMENT_BINDING')
            # Additive optional field: old completed reports and in-progress
            # aggregates retain their identities. Never average across results.
            summaries=a.setdefault('markout_monitoring',[])
            if len(summaries)>=32:a['metadata_overflow']=True
            else:summaries.append(dict(record_id=row['id'],sha256=row['sha256'],result_sha256=d['result_sha256'],
                scope=request['scope'],bundle_sha256=request['bundle_sha256'],policy_sha256=digest(policy),
                horizon_seconds=policy['horizon_seconds'],direction=policy['direction'],evidence_class=measured['evidence_class'],
                measurement_class=measured['measurement_class'],window=measured['window'],scores=measured['scores'],
                outcome=measured['outcome'],threshold_breaches=measured['threshold_breaches'],
                retained_quote_window_coverage_verified=measured['retained_quote_window_coverage_verified'],
                global_universe_coverage_verified=False,actual_trading_pnl=None,net_ev_capture=None))
        if (d.get('version')=='alpha_v11_reviewed_drift_worker_v1' and d.get('action')=='DRIFT_MEASUREMENT'
                and (d.get('result') or {}).get('version')=='alpha_v11_scoped_paper_fill_markout_v1'):
            measured=d['result'];request=measured['request'];policy=request['policy']
            if digest(measured)!=d.get('result_sha256') or measured.get('financial_authority') is not False:
                raise EvidenceError('AUDIT_FILL_MARKOUT_MEASUREMENT_BINDING')
            summaries=a.setdefault('fill_markout_monitoring',[])
            if len(summaries)>=32:a['metadata_overflow']=True
            else:summaries.append(dict(record_id=row['id'],sha256=row['sha256'],result_sha256=d['result_sha256'],
                scope=request['scope'],bundle_sha256=request['bundle_sha256'],policy_sha256=digest(policy),
                horizon_seconds=policy['horizon_seconds'],direction=policy['direction'],evidence_class=measured['evidence_class'],
                execution_class=measured['execution_class'],measurement_class=measured['measurement_class'],
                window=measured['window'],scores=measured['scores'],outcome=measured['outcome'],threshold_breaches=measured['threshold_breaches'],
                reconciled_fill_selection_verified=measured['reconciled_fill_selection_verified'],
                execution_timing_coverage_verified=measured['execution_timing_coverage_verified'],
                global_universe_coverage_verified=False,actual_trading_pnl=None,net_ev_capture=None))
        if d.get('version')=='alpha_v11_reviewed_drift_worker_v1' and d.get('action')=='DRIFT_RESULT':
            _bump(a['drift_outcomes'],d.get('outcome','UNKNOWN'))
            if d.get('outcome')!='NO_DECLARED_BREACH':_sample(a['incident_refs'],row)
        if d.get('action')=='IMMUTABLE_RESEARCH_RESULT':_sample(a['model_result_ids'],row)
        if d.get('action')=='REGISTER_PLAN' and len(a['learning_watermarks'])<32:
            a['learning_watermarks'].append(dict(record_id=row['id'],training_cutoff=d.get('plan',{}).get('training_cutoff'),
                                                   registration_timing=d.get('registration_timing')))
    elif kind=='RUNTIME_STATUS':
        if d.get('version')=='alpha_v11_paper_receipt_reconciliation_v1':
            receipt=a.setdefault('paper_receipt_reconciliation',dict(journal_outcomes={},delivery_attempt_outcomes={},
                latest_pending_count=0,latest_journal_id=None))
            _bump(receipt['journal_outcomes'],d.get('outcome','UNKNOWN'))
            for item in d.get('receipt_outcomes',[]):
                _bump(receipt['delivery_attempt_outcomes'],item['outcome'])
            receipt.update(latest_pending_count=len(d['state']['pending']),latest_journal_id=row['id'])
            if d.get('outcome')!='RECONCILED':_sample(a['incident_refs'],row)
        if d.get('version') in {'alpha_v11_paper_runtime_v1','alpha_v11_observation_pump_v1'}:
            _bump(a['runtime_outcomes'],d.get('outcome','UNKNOWN'))
            duration=d.get('duration_monotonic_seconds')
            if duration is not None:
                duration=finite(duration);values=a['runtime_durations'];values['count']+=1;values['total']+=duration;values['maximum']=max(values['maximum'],duration)
        if row['event_id']==HEALTH_KEY and d.get('global_reasons'):
            a['health_failure_samples']+=1;_sample(a['incident_refs'],row)
    elif kind=='MEASUREMENT' and d.get('version')=='alpha_v11_maker_research_v1' and d.get('request',{}).get('action')=='MARKOUT':
        # Sample counts only: averaging mixed horizons/targets is invalid.
        _bump(a['markout_counts'],str(d['request']['horizon_seconds'])+':'+d.get('status','UNKNOWN'))
    elif kind=='MEASUREMENT' and d.get('version')=='alpha_v11_resting_admission_check_v1':
        _bump(a['resting_admission_checks'],d.get('reason','UNKNOWN'))
        if d.get('passed') is False:_sample(a['incident_refs'],row)
    elif kind=='MEASUREMENT' and d.get('version')=='alpha_v11_paper_cancellation_v1':
        # These count audit records, not unique orders or exchange cancellations.
        _bump(a['paper_cancellation_outcomes'],d.get('outcome','UNKNOWN'))
    elif kind=='MEASUREMENT' and d.get('version')=='alpha_v11_maker_research_v1' and d.get('request',{}).get('action')=='RETIRE':
        _bump(a['maker_retirement_reasons'],d['request'].get('reason','UNKNOWN'))
        _sample(a['incident_refs'],row)


class AuditWorker:
    def __init__(self,coordinator,policy,*,rewards=None):
        if not isinstance(policy,AuditPolicy) or rewards is not None and rewards.research.coordinator is not coordinator:
            raise EvidenceError('AUDIT_COMPONENT_SCOPE')
        self.coordinator,self.store,self.policy,self.rewards=coordinator,coordinator.store,policy,rewards
        self.schedule_config=digest(policy.payload())
        self.config=digest(dict(schedule=self.schedule_config,account=coordinator.policy_sha,rewards=rewards.config if rewards else None,
                               layout=LAYOUT_VERSION))

    def _head(self):
        row=self.store.latest(kind='RUNTIME_STATUS',event_id=WORKER_KEY)
        if row and row['body']['details'].get('config_sha256')!=self.config:
            raise EvidenceError('AUDIT_WORKER_CONFIG_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self,head,state,**details):
        key='audit-progress:'+digest([self.config,head['id'] if head else None,state,details])
        return self.store.audit(key,event_id=WORKER_KEY,kind='RUNTIME_STATUS',details=dict(version=VERSION,
            config_sha256=self.config,state=state,**details,financial_authority=False,messages_sent=False),
            expected_previous_seq=head['seq'] if head else 0)

    def step(self):
        """Run outside the paper runtime lock, one bounded chunk, no sleeps/HTTP."""
        fd=os.open(self.store.path.with_name(self.store.path.name+'.audit.lock'),os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise EvidenceError('AUDIT_WORKER_ALREADY_RUNNING') from None
            return self._step()
        finally:os.close(fd)

    def _step(self):
        started=time.monotonic();deadline=started+self.policy.maximum_step_seconds
        head=self._head();state=deepcopy(head['body']['details']['state']) if head else dict(cursors={p:0 for p in PERIODS},active=None)
        if state['active'] is None:
            requests=[]
            for period,cursor in state['cursors'].items():
                requests.extend(self.store.records(kind='RUNTIME_STATUS',event_id=request_event(period),after_seq=cursor,limit=1))
            if not requests:return dict(outcome='NO_AUDIT_WORK',financial_authority=False)
            request=min(requests,key=lambda r:r['seq']);window=request['body']['details']
            if window.get('config_sha256')!=self.schedule_config or window.get('version')!=VERSION:
                raise EvidenceError('AUDIT_REQUEST_POLICY_MISMATCH')
            wanted=[('COORDINATOR_EVENT',ACCOUNT_KEY),('RUNTIME_STATUS',HEALTH_KEY)]
            if self.rewards:wanted.append(('MEASUREMENT',self.rewards.key))
            view=self.store.pin_read_view(tuple(wanted))
            state['active']=dict(request_id=request['id'],request_seq=request['seq'],window=window,
                view=view,cursor=0,scanned=0,aggregate=_aggregate())
            head=self._save(head,state,outcome='AUDIT_STARTED')
        job=state['active'];window=job['window'];report_key='audit-report:'+digest(job['request_id'])
        try:complete=self.store.get(report_key)
        except EvidenceError as exc:
            if str(exc)!='EVIDENCE_MISSING':raise
            complete=None
        if complete is not None:
            d=complete['body']['details']
            if d.get('config_sha256')!=self.config or d.get('request_id')!=job['request_id']:
                raise EvidenceError('AUDIT_REPORT_REPLAY_CONFLICT')
        else:
            consumed=0
            while job['cursor']<job['view']['through_seq'] and consumed<self.policy.records_per_step and time.monotonic()<deadline and job['scanned']<self.policy.maximum_job_records:
                limit=min(16,self.policy.records_per_step-consumed,self.policy.maximum_job_records-job['scanned'])
                rows=self.store.page_through(through_seq=job['view']['through_seq'],after_seq=job['cursor'],limit=limit)
                if not rows:raise EvidenceError('AUDIT_PINNED_SEQUENCE_MISSING')
                for row in rows:
                    try:
                        _fold(row,job['aggregate'],window)
                        if self.policy.replay is not None:fold_replay_decisions(row,job['aggregate'],window)
                        if self.policy.account_replay is not None:fold_account_commands(row,job['aggregate'],window)
                        if self.policy.pws_score_replay is not None:fold_pws_scores(row,job['aggregate'],window)
                    except (EvidenceError,KeyError,TypeError,ValueError):
                        job['aggregate']['malformed_records']+=1;_sample(job['aggregate']['incident_refs'],row)
                    job['cursor']=row['seq'];job['scanned']+=1;consumed+=1
            finished=job['cursor']==job['view']['through_seq']
            capped=not finished and job['scanned']>=self.policy.maximum_job_records
            if not finished and not capped:
                saved=self._save(head,state,outcome='AUDIT_PARTIAL_PROGRESS',duration_seconds=time.monotonic()-started)
                return dict(outcome='AUDIT_PARTIAL_PROGRESS',progress_id=saved['id'],scanned=job['scanned'],financial_authority=False)
            # Publication has a separate <=1 s metadata budget. It cannot run in
            # the cancellation loop; periodic invocation controls its CPU share.
            pinned={p['event_id']:p['record_id'] for p in job['view']['heads']}
            account=self.store.get(pinned[ACCOUNT_KEY]) if pinned.get(ACCOUNT_KEY) else None
            performance=PerformanceLab(self.coordinator).build(start=window['start'],end=window['end'],account_row=account,
                execution_policy=self.policy.execution_costs)
            exposure=self.coordinator._risk(self.coordinator._state(account))
            reward=None
            if self.rewards and pinned.get(self.rewards.key):
                source=self.store.get(pinned[self.rewards.key]);rs=source['body']['details']['state']
                reward=dict(source_id=source['id'],synthetic_payment_count=len(rs['payments']),
                    tracked_quote_count=len(rs['tracked']),actual_verified_income=None,
                    cash_credit='0',estimates_in_trading_pnl=False,
                    actual_discrepancy=None,coverage='RESEARCH_ONLY_NO_LIVE_PAYMENT_ATTESTOR')
            latest_health=self.store.get(pinned[HEALTH_KEY]) if pinned.get(HEALTH_KEY) else None
            result=dict(version=VERSION,config_sha256=self.config,request_id=job['request_id'],period=window['period'],
                window=dict(start=window['start'],end=window['end']),pinned_view=job['view'],
                coverage=dict(archive_scan_complete=finished,scanned_records=job['scanned'],row_cap_reached=capped,
                    skipped_complete_windows=window['skipped_complete_windows'],first_request_history_unknown=window['first_request_history_unknown'],
                    metadata_overflow=job['aggregate']['metadata_overflow'],
                    semantic_coverage_complete=finished and not job['aggregate']['malformed_records'] and not job['aggregate']['metadata_overflow'],
                    protected_champion_and_certification_review=False),
                PAPER=performance,LIVE=dict(status='NOT_OBSERVED',pnl=None,capital=None,financial_authority=False),
                operations=job['aggregate'],current_exposure=exposure,
                exposure_time='PINNED_CURRENT_ACCOUNT_NOT_HISTORICAL_WINDOW_END',
                health=dict(record_id=latest_health['id'] if latest_health else None,
                    recorded_at=latest_health['body']['recorded_at'] if latest_health else None,
                    status='PINNED_OBSERVATION_NOT_CONTINUOUS_RUNTIME_PROOF'),
                rewards=reward or dict(status='NO_REWARD_LEDGER_PIN',actual_verified_income=None),
                unresolved=dict(calibration='NO_INDEPENDENT_TARGET_ALIGNED_LABELS',slippage='NOT_SEPARATELY_IDENTIFIED',
                    source_pws_contribution='NO_ACCEPTED_ABLATION_EVIDENCE',rejected_counterfactuals='NOT_COMPUTED_NO_FILL_ASSUMPTION',
                    champion_challenger='RESEARCH_RESULT_REFERENCES_ONLY',current_champion='PROTECTED_POINTER_NOT_ATTESTED',
                    promotion_candidates='REVIEW_ONLY_NO_AUTHORITY',resource_trend='RUNTIME_DURATION_ONLY_HOST_CPU_RAM_DISK_UNVERIFIED'),
                review_required=True,financial_authority=False,acceptance_granted=False,messages_sent=False,
                delivery='DURABLE_LOCAL_ONLY',duration_seconds=time.monotonic()-started,
                reporter_process_peak_rss_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
            if self.policy.execution_costs is not None:
                costs=performance['execution_costs']
                result['coverage']['execution_cost_population_complete']=costs['complete_cost_population']
                result['coverage']['execution_price_comparisons_complete']=costs['complete_price_comparisons']
                result['unresolved']['slippage']='SEPARATE_SYNTHETIC_RECEIPT_COMPARISONS_'+costs['status']
            if self.policy.replay is not None:
                replayed=replay_audit(self.coordinator,
                    selection=job['aggregate'].get('economic_replay_selection',dict(count=0,refs=[],overflow=False)),
                    through_seq=job['view']['through_seq'],window=result['window'],
                    archive_complete=finished and not job['aggregate']['malformed_records'],policy=self.policy.replay)
                result['economic_replay']=replayed
                result['coverage']['economic_replay_selection_complete']=replayed['complete_retained_selection']
                result['coverage']['retained_economics_reproduced']=replayed['all_economics_reproduced']
                result['unresolved']['full_engine_replay']='CONTROL_FLOW_OTHER_STRATEGIES_AND_HISTORICAL_EXECUTABLE_UNVERIFIED'
            if self.policy.account_replay is not None:
                replayed=account_replay_audit(self.coordinator,
                    selection=job['aggregate'].get('account_replay_selection',dict(count=0,refs=[],overflow=False)),
                    through_seq=job['view']['through_seq'],window=result['window'],
                    archive_complete=finished and not job['aggregate']['malformed_records'],policy=self.policy.account_replay,
                    replay_valuations=self.policy.account_valuation_replay)
                result['account_replay']=replayed
                result['coverage']['account_replay_selection_complete']=replayed['complete_retained_selection']
                result['coverage']['retained_account_effects_reproduced']=replayed['all_effects_reproduced']
                if self.policy.account_valuation_replay:
                    result['coverage']['prepared_valuation_selection_complete']=replayed['prepared_valuation_coverage']['complete']
                    result['coverage']['retained_prepared_valuations_reproduced']=replayed['prepared_valuation_coverage']['all_prepared_valuations_reproduced']
                result['unresolved']['full_engine_replay']='ORIGINAL_PREPARATION_CONTROL_OTHER_COMMANDS_AND_EXECUTABLE_UNVERIFIED'
            if self.policy.pws_score_replay is not None:
                replayed=pws_score_audit(self.store,
                    selection=job['aggregate'].get('pws_score_selection',dict(count=0,refs=[],overflow=False)),
                    through_seq=job['view']['through_seq'],window=result['window'],
                    archive_complete=finished and not job['aggregate']['malformed_records'],policy=self.policy.pws_score_replay)
                result['pws_score_replay']=replayed
                result['coverage']['pws_score_selection_complete']=replayed['complete_retained_selection']
                result['coverage']['retained_pws_scores_reproduced']=replayed['all_scores_reproduced']
                result['unresolved']['pws_label_truth']='RECEIPT_SCORE_REPLAY_NOT_PUBLICATION_CONTINUITY_OR_CALIBRATION'
            refs=[job['request_id'],head['id']]+[p['record_id'] for p in job['view']['heads'] if p['record_id']]
            complete=self.store.audit(report_key,event_id='v11-audit-report:'+window['period'],kind='RUNTIME_STATUS',details=result,evidence_ids=tuple(dict.fromkeys(refs)))
        state['cursors'][window['period']]=job['request_seq'];state['active']=None
        self._save(head,state,outcome='AUDIT_COMPLETE',report_id=complete['id'])
        return dict(outcome='AUDIT_COMPLETE',report_id=complete['id'],financial_authority=False)
