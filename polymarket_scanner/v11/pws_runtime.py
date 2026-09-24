"""Bounded archived-MADIS to defensive-QC work for the nonfinancial candidate.

No HTTP, provider-clock substitution, source certification or model promotion.
One event is processed per step. Source and metadata changes fence publication;
partial metadata work can resume without repeating collection or renewing receipts.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass
import fcntl
import os
import time

from .certification import StationMetadata
from .evidence import EvidenceError, canonical, digest, finite, identity
from .pws_quality import PWSPolicy, archive_neighborhood, metadata_sequence


VERSION = 'alpha_v11_pws_quality_worker_v1'
KEY = 'v11-pws-quality-worker'


@dataclass(frozen=True)
class PWSQualityPlan:
    event_id: str
    official: StationMetadata
    policy: PWSPolicy

    def __post_init__(self):
        identity(self.event_id)
        if not isinstance(self.official,StationMetadata) or not isinstance(self.policy,PWSPolicy):
            raise EvidenceError('PWS_RUNTIME_CONTEXT_REQUIRED')


@dataclass(frozen=True)
class PWSQualitySettings:
    plans: tuple[PWSQualityPlan, ...]
    maximum_captures: int = 64
    maximum_seconds: float = 2.

    def __post_init__(self):
        if (type(self.plans) is not tuple or not 1<=len(self.plans)<=16
                or any(not isinstance(p,PWSQualityPlan) for p in self.plans)
                or len({p.event_id for p in self.plans})!=len(self.plans)
                or type(self.maximum_captures) is not int or not 1<=self.maximum_captures<=64
                or not .05<=finite(self.maximum_seconds)<=2):
            raise EvidenceError('PWS_RUNTIME_PLAN_BOUND')


class PWSQualityWorker:
    def __init__(self,store,health,settings):
        if health.store is not store or not isinstance(settings,PWSQualitySettings):
            raise EvidenceError('PWS_RUNTIME_COMPONENT_SCOPE')
        self.store,self.health,self.settings=store,health,settings
        self.plans={p.event_id:p for p in settings.plans}
        self.config=digest(dict(settings=asdict(settings),health=health.config))

    def _get(self,key):
        try:return self.store.get(key)
        except EvidenceError as exc:
            if str(exc)!='EVIDENCE_MISSING':raise
        return None

    def _head(self):
        row=self.store.latest(kind='RUNTIME_STATUS',event_id=KEY)
        if row and row['body']['details'].get('config_sha256')!=self.config:
            raise EvidenceError('PWS_RUNTIME_CONFIG_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self,key,state,**details):
        if len(canonical(state).encode())>64*1024:raise EvidenceError('PWS_RUNTIME_STATE_BOUND')
        head=self._head()
        return self.store.safety_audit(key,event_id=KEY,kind='RUNTIME_STATUS',details=dict(
            version=VERSION,config_sha256=self.config,state=state,**details,
            financial_authority=False,settlement_authority=False,lead_advantage_verified=False,
            forward_acceptance=False),expected_previous_seq=head['seq'] if head else 0)

    def current_inputs(self,event_id):
        """One consistent, bounded receipt window. Overflow is never truncated."""
        if event_id not in self.plans:raise EvidenceError('PWS_RUNTIME_EVENT_SCOPE')
        plan=self.plans[event_id]
        at=finite(self.store.clock());channel='CWOP_NEAR:'+plan.official.station
        with self.store._connect() as db:
            db.execute('BEGIN')
            source_seq=db.execute("SELECT COALESCE(MAX(seq),0) FROM v11_records "
                "WHERE kind='PWS_OBSERVATION' AND event_id=?",(plan.event_id,)).fetchone()[0]
            row=db.execute("SELECT * FROM v11_records WHERE kind='PWS_OBSERVATION' AND event_id=? "
                "AND json_extract(body,'$.provider')='NOAA_MADIS_CWOP' "
                "AND json_extract(body,'$.source_identity')=? ORDER BY seq DESC LIMIT 1",
                (plan.event_id,channel)).fetchone()
            if row is None:raise EvidenceError('PWS_RUNTIME_SOURCE_ABSENT')
            latest=self.store._decode(row);b=latest['body']
            if (b['available_at']>at or b['recorded_at']>at
                    or b['evidence_class']=='HISTORICAL_AVAILABILITY_UNKNOWN'
                    or b['payload'].get('adapter_version')!='alpha_v11_madis_public_xml_v1'
                    or b['payload'].get('settlement_station_context')!=plan.official.station):
                raise EvidenceError('PWS_RUNTIME_CURRENT_NORMALIZATION_REQUIRED')
            rows=db.execute("SELECT * FROM v11_records WHERE kind='PWS_OBSERVATION' AND event_id=? "
                "AND json_extract(body,'$.provider')='NOAA_MADIS_CWOP' "
                "AND json_extract(body,'$.source_identity')=? AND available_at<=? AND recorded_at<=? "
                "AND json_extract(body,'$.payload.adapter_version')='alpha_v11_madis_public_xml_v1' "
                "AND (json_extract(body,'$.observed_at')>=? OR seq=?) ORDER BY seq LIMIT ?",
                (plan.event_id,channel,at,at,at-plan.policy.history_seconds,latest['seq'],
                 self.settings.maximum_captures+1)).fetchall()
        if len(rows)>self.settings.maximum_captures:raise EvidenceError('PWS_RUNTIME_CAPTURE_WINDOW_OVERFLOW')
        return dict(channel_id=latest['id'],source_seq=source_seq,capture_ids=[self.store._decode(r)['id'] for r in rows])

    def step(self,command_id):
        identity(command_id,maximum=80);key='pws-quality-step:'+digest(command_id)
        previous=self._get(key)
        if previous:
            if previous['body']['details'].get('config_sha256')!=self.config:
                raise EvidenceError('PWS_RUNTIME_REPLAY_CONFIG')
            return previous
        fd=os.open(self.store.path.with_name(self.store.path.name+'.pws-quality.lock'),
                   os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise EvidenceError('PWS_RUNTIME_ALREADY_RUNNING') from None
            head=self._head()
            state=deepcopy(head['body']['details']['state']) if head else dict(last_event='',active=None,completed={})
            clock=self.health.sample(key+':clock')
            if clock['body']['details']['clock_reasons']:
                return self._save(key,state,outcome='DEFERRED_CLOCK_UNHEALTHY')
            deadline=time.monotonic()+self.settings.maximum_seconds
            active=state['active']
            if active is None:
                ordered=sorted(self.plans)
                event=next((e for e in ordered if e>state['last_event']),ordered[0])
                state['last_event']=event;plan=self.plans[event]
                try:selected=self.current_inputs(event)
                except EvidenceError as exc:
                    return self._save(key,state,outcome='PWS_SOURCE_GATED',event_id=event,reason=str(exc))
                token=digest([selected['channel_id'],metadata_sequence(self.store)])
                completed=state['completed'].get(event)
                if completed and completed['input_token']==token:
                    return self._save(key,state,outcome='UNCHANGED_INPUTS_NO_RECEIPT_RENEWAL',
                        event_id=event,qc_id=completed['qc_id'])
                active=dict(event_id=event,**selected,qc_id='pws-runtime-qc:'+digest([key,event,selected,self.config]))
                state['active']=active
                self._save(key+':begin',state,outcome='QC_INPUTS_PINNED',event_id=event)
            event=active['event_id'];plan=self.plans[event]
            try:
                qc=archive_neighborhood(self.store,active['qc_id'],event_id=event,
                    capture_ids=tuple(active['capture_ids']),official=plan.official,policy=plan.policy,
                    expected_source_seq=active['source_seq'],deadline=deadline)
            except EvidenceError as exc:
                resumable=str(exc) in {'PWS_QC_TIME_BOUND','ARCHIVE_STATE_CHANGED','AUDIT_STATE_CHANGED'}
                if not resumable:state['active']=None
                return self._save(key,state,outcome='QC_PROGRESS_RETAINED' if resumable else 'PWS_SOURCE_GATED',
                    event_id=event,reason=str(exc))
            state['completed'][event]=dict(qc_id=qc['id'],input_token=digest([
                active['channel_id'],qc['body']['payload']['metadata_sequence']]))
            state['active']=None
            return self._save(key,state,outcome='PWS_QC_RECORDED',event_id=event,qc_id=qc['id'],
                health=qc['body']['payload']['health'],source_captures=len(active['capture_ids']))
        finally:os.close(fd)
