"""Finite current-source preparations for the nonfinancial candidate.

One bounded stage per invocation, immutable selected inputs and durable restart
state. No collection, fitting, approval, service control or financial transport.
Prepared inputs remain uncalibrated and consumers retain all admission gates.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import fcntl
import os
import sqlite3
import time

from .certification import StationMetadata
from .evidence import EvidenceError, canonical, digest, finite, identity
from .gefs_sources import GEFSPlan, PROVIDER as GEFS_PROVIDER
from .nowcast_features import archive_nowcast_features, physical_feature_identity
from .physical_inference import archive_physical_model, physical_model_identity
from .probability import NEXT_OBSERVATION, UNRESOLVED_EXTREME
from .pws_quality import metadata_sequence
from .remaining_forecast import archive_remaining_path, PROVIDER as REMAINING_PROVIDER
from .request_assembly import SourceSelector
from .rules import RuleFingerprint
from .strategy_admission import ROLES


VERSION='alpha_v11_input_preparation_worker_v1'
KEY='v11-input-preparation-worker'


@dataclass(frozen=True)
class RemainingPreparation:
    name: str
    plan: GEFSPlan
    observation: SourceSelector

    def __post_init__(self):
        identity(self.name,maximum=64)
        if (not isinstance(self.plan,GEFSPlan) or not isinstance(self.observation,SourceSelector)
                or self.observation.role!='OFFICIAL'):
            raise EvidenceError('PREPARATION_REMAINING_PLAN_REQUIRED')

    @property
    def rule(self):return self.plan.rule

    @property
    def event_id(self):return self.plan.event_id

    def output_sources(self,maximum_age_seconds):
        return (SourceSelector('MODEL',REMAINING_PROVIDER,self.plan.source_identity+':REMAINING',maximum_age_seconds),
                SourceSelector('FEATURES',REMAINING_PROVIDER,self.plan.source_identity+':COVERAGE',maximum_age_seconds))


@dataclass(frozen=True)
class PhysicalPreparation:
    name: str
    rule: RuleFingerprint
    official: StationMetadata
    base: SourceSelector
    input_target: str
    awc_source_identity: str
    maximum_observation_age_seconds: float
    trajectory_window_seconds: float
    maximum_gap_seconds: float
    pws: SourceSelector | None = None
    daylight: SourceSelector | None = None
    coverage: SourceSelector | None = None
    pair_without_pws: bool = True

    def __post_init__(self):
        identity(self.name,maximum=64);identity(self.awc_source_identity)
        if (not isinstance(self.rule,RuleFingerprint) or not isinstance(self.official,StationMetadata)
                or not isinstance(self.base,SourceSelector) or self.base.role!='MODEL'
                or self.input_target not in {NEXT_OBSERVATION,UNRESOLVED_EXTREME}
                or (self.coverage is None)!=(self.input_target==NEXT_OBSERVATION)
                or self.coverage is not None and (not isinstance(self.coverage,SourceSelector) or self.coverage.role!='FEATURES')
                or self.pws is not None and (not isinstance(self.pws,SourceSelector) or self.pws.role!='PWS'
                    or self.pws.provider!='ALPHA_PWS_QC' or self.pws.source_identity!=self.official.station)
                or self.daylight is not None and (not isinstance(self.daylight,SourceSelector) or self.daylight.role!='MODEL')
                or type(self.pair_without_pws) is not bool
                or self.rule.payload['station']!=self.official.station
                or self.rule.payload['metadata_fingerprint']!=self.official.fingerprint
                or any(not 0<finite(v)<=86400 for v in (self.maximum_observation_age_seconds,
                    self.trajectory_window_seconds,self.maximum_gap_seconds))):
            raise EvidenceError('PREPARATION_PHYSICAL_SCOPE_OR_BOUND')

    @property
    def event_id(self):return self.rule.payload['event_id']

    def feature_source(self,without_pws=False):
        return physical_feature_identity(self.official,('PWS',) if without_pws else (),
            self.maximum_observation_age_seconds,self.trajectory_window_seconds,self.maximum_gap_seconds)

    def output_sources(self,maximum_age_seconds,*,without_pws=False):
        if without_pws and not self.pair_without_pws:raise EvidenceError('PREPARATION_ABLATION_NOT_CONFIGURED')
        channel=physical_model_identity(self.rule,self.input_target,self.base.provider,self.base.source_identity,
                                        self.feature_source(without_pws))
        result=(SourceSelector('MODEL','ALPHA_PHYSICAL_MODEL',channel,maximum_age_seconds),)
        if self.coverage is not None:
            result+=(SourceSelector('FEATURES','ALPHA_PHYSICAL_MODEL',channel+':coverage',maximum_age_seconds),)
        return result


@dataclass(frozen=True)
class PreparationSettings:
    plans: tuple[RemainingPreparation | PhysicalPreparation, ...]
    maximum_captures: int = 32
    maximum_seconds: float = 2.
    maximum_stage_attempts: int = 3

    def __post_init__(self):
        if (type(self.plans) is not tuple or not 1<=len(self.plans)<=32
                or any(type(p) not in {RemainingPreparation,PhysicalPreparation} for p in self.plans)
                or len({p.name for p in self.plans})!=len(self.plans)
                or len({p.event_id for p in self.plans})>16
                or type(self.maximum_captures) is not int or not 1<=self.maximum_captures<=32
                or type(self.maximum_stage_attempts) is not int or not 1<=self.maximum_stage_attempts<=8
                or not .05<=finite(self.maximum_seconds)<=2):
            raise EvidenceError('PREPARATION_SETTINGS_BOUND')
        outputs=[(p.event_id,s.provider,s.source_identity) for p in self.plans
                 for ablated in ((False,True) if isinstance(p,PhysicalPreparation) and p.pair_without_pws else (False,))
                 for s in (p.output_sources(1.,without_pws=ablated) if isinstance(p,PhysicalPreparation) else p.output_sources(1.))]
        if len(set(outputs))!=len(outputs):raise EvidenceError('PREPARATION_OUTPUT_CHANNEL_CONFLICT')


class PreparationWorker:
    def __init__(self,store,health,settings):
        if health.store is not store or not isinstance(settings,PreparationSettings):
            raise EvidenceError('PREPARATION_COMPONENT_SCOPE')
        self.store,self.health,self.settings=store,health,deepcopy(settings)
        self.plans={p.name:p for p in self.settings.plans}
        self.config=digest(dict(settings=asdict(self.settings),health=health.config))

    def _get(self,key):
        try:return self.store.get(key)
        except EvidenceError as exc:
            if str(exc)!='EVIDENCE_MISSING':raise
        return None

    def _head(self):
        if (self.config!=digest(dict(settings=asdict(self.settings),health=self.health.config))
                or self.plans!={p.name:p for p in self.settings.plans}):
            raise EvidenceError('PREPARATION_CONFIGURATION_MUTATED')
        row=self.store.latest(kind='RUNTIME_STATUS',event_id=KEY)
        if row and row['body']['details'].get('config_sha256')!=self.config:
            raise EvidenceError('PREPARATION_CONFIGURATION_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self,key,state,**details):
        if len(canonical(state).encode())>64*1024:raise EvidenceError('PREPARATION_STATE_BOUND')
        head=self._head()
        return self.store.safety_audit(key,event_id=KEY,kind='RUNTIME_STATUS',details=dict(
            version=VERSION,config_sha256=self.config,state=state,**details,financial_authority=False,
            calibrated_probability=False,source_truth_independently_attested=False,forward_acceptance=False),
            expected_previous_seq=head['seq'] if head else 0)

    def current_inputs(self,plan,deadline):
        """Consistent bounded selection, including absent optional source channels."""
        at=finite(self.store.clock());channels=[];total=0
        with self.store._connect() as db:
            db.execute('BEGIN');db.set_progress_handler(lambda:time.monotonic()>=deadline,1000)
            def decode(row):
                nonlocal total
                if time.monotonic()>=deadline:raise EvidenceError('PREPARATION_TIME_BOUND')
                if row is None:return None
                total+=len(row['body'].encode())
                if total>2*1024*1024:raise EvidenceError('PREPARATION_SOURCE_BYTES_BOUND')
                return self.store._decode(row)
            def channel(kind,provider,source):
                row=decode(db.execute("SELECT * FROM v11_records WHERE kind=? AND event_id=? "
                    "AND json_extract(body,'$.provider')=? AND json_extract(body,'$.source_identity')=? "
                    "ORDER BY seq DESC LIMIT 1",(kind,plan.event_id,provider,source)).fetchone())
                channels.append([kind,provider,source,row['id'] if row else None])
                if row and (row['body']['available_at']>at or row['body']['recorded_at']>at
                        or row['body']['evidence_class']=='HISTORICAL_AVAILABILITY_UNKNOWN'):
                    raise EvidenceError('PREPARATION_SOURCE_NOT_CAUSAL')
                return row
            def source(selector,*,optional=False):
                if selector is None:return None
                row=channel(ROLES[selector.role],selector.provider,selector.source_identity)
                if row is None:
                    if optional:return None
                    raise EvidenceError('PREPARATION_REQUIRED_SOURCE_ABSENT')
                b=row['body'];times=[b['received_at']]
                t=b['issued_at'] if selector.role=='MODEL' else b['observed_at']
                if t is not None:times.append(t)
                if any(not 0<=at-t<selector.maximum_age_seconds for t in times):
                    if optional:return None
                    raise EvidenceError('PREPARATION_REQUIRED_SOURCE_STALE')
                return row['id']
            try:
                if isinstance(plan,RemainingPreparation):
                    path=source(SourceSelector('MODEL',GEFS_PROVIDER,plan.plan.source_identity,
                                               plan.plan.maximum_run_age_seconds))
                    row=decode(db.execute('SELECT * FROM v11_records WHERE record_id=?',(path,)).fetchone())
                    initialized=finite(row['body']['issued_at'])
                    if initialized<plan.plan.initialized_at:raise EvidenceError('PREPARATION_PATH_BEFORE_DECLARED_SEED')
                    # Only a received complete path may advance the run. The
                    # derivation verifies its exact reconstructed plan hash and
                    # every field; this is not clock-based source availability.
                    replace(plan.plan,initialized_at=initialized)
                    result=dict(path_id=path,observation_id=source(plan.observation),initialized_at=initialized)
                else:
                    base=source(plan.base);coverage=source(plan.coverage)
                    awc=channel('OFFICIAL_OBSERVATION','NOAA_AWC',plan.awc_source_identity)
                    awc_ids=[]
                    if awc:
                        rows=db.execute("SELECT * FROM v11_records WHERE kind='OFFICIAL_OBSERVATION' AND event_id=? "
                            "AND json_extract(body,'$.provider')='NOAA_AWC' AND json_extract(body,'$.source_identity')=? "
                            "AND (json_extract(body,'$.received_at')>=? OR seq=?) ORDER BY seq LIMIT ?",
                            (plan.event_id,plan.awc_source_identity,at-plan.trajectory_window_seconds,awc['seq'],
                             2*self.settings.maximum_captures+1)).fetchall()
                        if len(rows)>2*self.settings.maximum_captures:raise EvidenceError('PREPARATION_CAPTURE_WINDOW_OVERFLOW')
                        values=[decode(row) for row in rows]
                        normalized_raw={r['body']['payload'].get('raw_evidence_id') for r in values}
                        awc_ids=[r['id'] for r in values if r['id'] not in normalized_raw]
                        if len(awc_ids)>self.settings.maximum_captures:raise EvidenceError('PREPARATION_CAPTURE_WINDOW_OVERFLOW')
                    result=dict(base_id=base,coverage_id=coverage,awc_ids=awc_ids,
                        pws_id=source(plan.pws,optional=True),daylight_id=source(plan.daylight,optional=True))
                result['channels']=channels
                # PWS metadata has cross-event scope; its current sequence fences
                # a previously selected neighborhood. It does not grant QC acceptance.
                if isinstance(plan,PhysicalPreparation) and plan.pws is not None:
                    result['pws_metadata_sequence']=metadata_sequence(self.store)
                return result,at
            except sqlite3.OperationalError as exc:
                if 'interrupted' in str(exc).lower():raise EvidenceError('PREPARATION_TIME_BOUND') from None
                raise
            finally:db.set_progress_handler(None,0)

    def _current(self,plan,selected):
        for kind,provider,channel,key in selected['channels']:
            row=self.store.latest_source(kind=kind,event_id=plan.event_id,provider=provider,source_identity=channel)
            if (row['id'] if row else None)!=key:raise EvidenceError('PREPARATION_SELECTED_SOURCE_CHANGED')
        if 'pws_metadata_sequence' in selected and metadata_sequence(self.store)!=selected['pws_metadata_sequence']:
            raise EvidenceError('PREPARATION_PWS_METADATA_CHANGED')

    def _stage(self,plan,active,deadline):
        selected=active['inputs'];outputs=active['outputs'];stage=active['stage'];token=active['output_token']
        self._current(plan,selected)
        if time.monotonic()>=deadline:raise EvidenceError('PREPARATION_TIME_BOUND')
        if isinstance(plan,RemainingPreparation):
            pair=archive_remaining_path(self.store,'prepared-remaining:'+token,plan=replace(plan.plan,initialized_at=selected['initialized_at']),
                path_id=selected['path_id'],observation_id=selected['observation_id'],deadline=deadline)
            outputs.update(model_id=pair['model']['id'],coverage_id=pair['coverage']['id'])
            return True
        stages=['FEATURES','ABLATION_FEATURES','MODEL','ABLATION_MODEL'] if plan.pair_without_pws else ['FEATURES','MODEL']
        action=stages[stage];without=action.startswith('ABLATION');suffix='without' if without else 'with'
        if action.endswith('FEATURES'):
            parents=selected['awc_ids']+([selected['pws_id']] if selected['pws_id'] and not without else [])+(
                [selected['daylight_id']] if selected['daylight_id'] else [])
            refs=[dict(id=row['id'],sha256=row['sha256']) for row in (self.store.get(key) for key in parents)]
            # Shared causal features have one identity across observation and
            # payout plans. Reprocessing them cannot supersede the other target
            # or renew feature time merely because a different model needs them.
            key='prepared-feature:'+digest([plan.event_id,plan.feature_source(without),refs,
                active['as_of'] if without else None]);feature=self._get(key)
            if feature is None:
                feature=archive_nowcast_features(self.store,key,event_id=plan.event_id,official=plan.official,
                    awc_capture_ids=tuple(selected['awc_ids']),pws_capture_id=selected['pws_id'],daylight_capture_id=selected['daylight_id'],
                    max_observation_age_seconds=plan.maximum_observation_age_seconds,
                    trajectory_window_seconds=plan.trajectory_window_seconds,maximum_gap_seconds=plan.maximum_gap_seconds,
                    ablated_families=frozenset({'PWS'}) if without else frozenset(),as_of=active['as_of'])
            if (feature['kind']!='FEATURES' or feature['event_id']!=plan.event_id
                    or feature['body']['source_identity']!=plan.feature_source(without)
                    or feature['body']['payload']['dependencies']!=refs):
                raise EvidenceError('PREPARATION_FEATURE_UNAVAILABLE_OR_REPLAY_CONFLICT')
            if not without:active['as_of']=feature['body']['payload']['context']['as_of']
            elif feature['body']['payload']['context']['as_of']!=active['as_of']:
                raise EvidenceError('PREPARATION_PAIRED_CUTOFF_MISMATCH')
            outputs[suffix+'_feature_id']=feature['id']
        else:
            pair=archive_physical_model(self.store,'prepared-physical:'+digest([token,suffix]),rule=plan.rule,
                base_model_id=selected['base_id'],feature_id=outputs[suffix+'_feature_id'],input_target=plan.input_target,
                coverage_id=selected['coverage_id'])
            outputs[suffix+'_model_id']=pair['model']['id']
            if pair['coverage']:outputs[suffix+'_coverage_id']=pair['coverage']['id']
        active['stage']+=1
        return active['stage']==len(stages)

    def step(self,command_id):
        identity(command_id,maximum=80);key='preparation-step:'+digest(command_id)
        previous=self._get(key)
        if previous:
            if previous['body']['details'].get('config_sha256')!=self.config:raise EvidenceError('PREPARATION_REPLAY_CONFIG')
            return previous
        fd=os.open(self.store.path.with_name(self.store.path.name+'.preparation.lock'),os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise EvidenceError('PREPARATION_ALREADY_RUNNING') from None
            head=self._head();state=deepcopy(head['body']['details']['state']) if head else dict(last_plan='',active=None,completed={})
            # A retried interrupted command must probe current clock health;
            # replaying its historical sample cannot authorize new derivations.
            clock=self.health.sample('preparation-clock:'+digest([key,self.store.pin_read_view()]))
            if clock['body']['details']['clock_reasons']:return self._save(key,state,outcome='DEFERRED_CLOCK_UNHEALTHY')
            started=time.monotonic();deadline=started+self.settings.maximum_seconds
            if state['active'] is None:
                ordered=sorted(self.plans);name=next((n for n in ordered if n>state['last_plan']),ordered[0]);state['last_plan']=name
                plan=self.plans[name]
                try:selected,at=self.current_inputs(plan,deadline)
                except (EvidenceError,KeyError,TypeError,ValueError) as exc:
                    return self._save(key,state,outcome='PREPARATION_SOURCE_GATED',plan=name,event_id=plan.event_id,
                        reason=str(exc) if isinstance(exc,EvidenceError) else type(exc).__name__)
                token=digest(selected);completed=state['completed'].get(name)
                if completed and completed['input_token']==token:
                    return self._save(key,state,outcome='UNCHANGED_INPUTS_NO_RECEIPT_RENEWAL',plan=name,event_id=plan.event_id,
                        outputs=completed['outputs'],previous_outcome=completed['outcome'],previous_reason=completed.get('reason'))
                state['active']=dict(plan=name,inputs=selected,input_token=token,as_of=at,stage=0,stage_attempts=0,outputs={},
                    output_token=digest([self.config,name,selected]))
                self._save(key+':begin',state,outcome='PREPARATION_INPUTS_PINNED',plan=name,event_id=plan.event_id)
            active=state['active'];name=active['plan'];plan=self.plans[name]
            if active['stage_attempts']>=self.settings.maximum_stage_attempts:
                state['completed'][name]=dict(input_token=active['input_token'],outputs=active['outputs'],outcome='GATED',
                                              reason='PREPARATION_STAGE_ATTEMPT_BOUND')
                state['active']=None
                return self._save(key,state,outcome='PREPARATION_SOURCE_GATED',plan=name,event_id=plan.event_id,
                                 reason='PREPARATION_STAGE_ATTEMPT_BOUND',outputs=active['outputs'])
            active['stage_attempts']+=1
            attempt='preparation-attempt:'+digest([key,self._head()['seq']])
            self._save(attempt,state,outcome='PREPARATION_STAGE_RESERVED',plan=name,event_id=plan.event_id)
            try:finished=self._stage(plan,active,deadline)
            except (EvidenceError,KeyError,TypeError,ValueError) as exc:
                # Immutable outputs already written remain evidence. A changed or
                # expired dependency is never refreshed under the old operation.
                reason=str(exc) if isinstance(exc,EvidenceError) else type(exc).__name__
                resumable=(reason in {'PREPARATION_TIME_BOUND','REMAINING_PREPARATION_TIME_BOUND','SOURCE_VIEW_TIME_BOUND'}
                           and active['stage_attempts']<self.settings.maximum_stage_attempts)
                if not resumable:
                    state['completed'][name]=dict(input_token=active['input_token'],outputs=active['outputs'],outcome='GATED',reason=reason)
                    state['active']=None
                return self._save(key,state,outcome='PREPARATION_PROGRESS_RETAINED' if resumable else 'PREPARATION_SOURCE_GATED',
                    plan=name,event_id=plan.event_id,reason=reason,outputs=active['outputs'])
            if finished:
                state['completed'][name]=dict(input_token=active['input_token'],outputs=active['outputs'],outcome='COMPLETE')
                state['active']=None
            else:active['stage_attempts']=0
            elapsed=time.monotonic()-started
            return self._save(key,state,outcome='PREPARATION_COMPLETE' if finished else 'PREPARATION_STAGE_RECORDED',
                plan=name,event_id=plan.event_id,outputs=active['outputs'],duration_monotonic_seconds=elapsed,
                cooperative_budget_overrun_seconds=max(0,elapsed-self.settings.maximum_seconds))
        finally:os.close(fd)
