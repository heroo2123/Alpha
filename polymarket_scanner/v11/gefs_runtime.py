"""One quota-controlled GEFS file per step, with retained partial-run state.

Runs are explicitly pinned plans. Missing/malformed fields never shrink an
ensemble or generate a partial model. No service or financial interface exists.
"""
from copy import deepcopy
from dataclasses import asdict, replace
import fcntl
import os
import time

from .evidence import EvidenceError, digest, identity
from .gefs_sources import (GEFSPlan, PROVIDER, FIELD_VERSION, VERSION as PATH_VERSION, field_request,
    normalize_field, assemble_path, current_path_heads)
from .gefs_schedule import GEFSRunPolicy, requested_plan


VERSION='alpha_v11_gefs_worker_v1'
KEY='v11-gefs-source-worker'


class GEFSWorker:
    def __init__(self,scheduled,health,plans,*,rollover=None):
        if (scheduled.store is not health.store or type(plans) is not tuple or not 1<=len(plans)<=16
                or any(not isinstance(p,GEFSPlan) for p in plans) or len({p.event_id for p in plans})!=len(plans)):
            raise EvidenceError('GEFS_WORKER_SCOPE_BOUND')
        if rollover is not None and not isinstance(rollover,GEFSRunPolicy):
            raise EvidenceError('GEFS_RUN_SCHEDULE_TYPED_PLAN_REQUIRED')
        self.scheduled,self.health,self.store=scheduled,health,health.store
        self.plans={p.event_id:p for p in plans}
        self.rollover=rollover
        self.config=digest(dict(plans=[asdict(p) for p in plans],health=health.config))
        if rollover is not None:self.config=digest(dict(base=self.config,rollover=asdict(rollover)))

    def _get(self,key):
        try:return self.store.get(key)
        except EvidenceError as exc:
            if str(exc)!='EVIDENCE_MISSING':raise
        return None

    def _head(self):
        row=self.store.latest(kind='RUNTIME_STATUS',event_id=KEY)
        if row and row['body']['details'].get('config_sha256')!=self.config:
            raise EvidenceError('GEFS_WORKER_CONFIG_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self,key,state,**details):
        head=self._head()
        return self.store.safety_audit(key,event_id=KEY,kind='RUNTIME_STATUS',details=dict(
            version=VERSION,config_sha256=self.config,state=state,**details,
            financial_authority=False,forward_acceptance=False,calibrated_probability=False),
            expected_previous_seq=head['seq'] if head else 0)

    async def step(self,command_id,*,exclude_events=()):
        identity(command_id,maximum=80);key='gefs-step:'+digest(command_id)
        if type(exclude_events) is not tuple or not set(exclude_events)<=self.plans.keys():
            raise EvidenceError('GEFS_WORKER_EXCLUSION_SCOPE')
        prior=self._get(key)
        if prior:
            if prior['body']['details'].get('config_sha256')!=self.config:raise EvidenceError('GEFS_WORKER_REPLAY_CONFIG')
            return prior
        fd=os.open(self.store.path.with_name(self.store.path.name+'.gefs.lock'),os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise EvidenceError('GEFS_WORKER_ALREADY_RUNNING') from None
            head=self._head();state=deepcopy(head['body']['details']['state']) if head else dict(last_event='',active=None,events={})
            health=self.health.sample(key+':clock')
            if health['body']['details']['clock_reasons']:return self._save(key,state,outcome='DEFERRED_CLOCK_UNHEALTHY')
            active=state['active']
            if active is not None and active['event_id'] in exclude_events:
                return self._save(key,state,outcome='DEFERRED_MODEL_CENSUS_OWNS_COLLECTION')
            if active is None:
                events=sorted(self.plans.keys()-set(exclude_events))
                if not events:return self._save(key,state,outcome='DEFERRED_MODEL_CENSUS_OWNS_COLLECTION')
                event=next((e for e in events if e>state['last_event']),events[0]);state['last_event']=event
                saved=state['events'].setdefault(event,dict(field_ids=[],completed_id=None))
                if self.rollover is not None:
                    previous=saved.get('initialized_at',self.plans[event].initialized_at)
                    try:
                        selected=requested_plan(self.plans[event],self.rollover,now=self.store.clock(),previous_initialization=previous)
                    except EvidenceError as exc:
                        return self._save(key,state,outcome='GEFS_SOURCE_GATED',event_id=event,reason=str(exc))
                    saved['initialized_at']=previous
                    if selected.initialized_at!=previous:
                        # The previous state and every raw/normalized object stay
                        # in the append-only archive. No current HTTP is cancelled.
                        state['events'][event]=dict(field_ids=[],completed_id=None,initialized_at=selected.initialized_at)
                        return self._save(key,state,outcome='GEFS_REQUESTED_RUN_ADVANCED',event_id=event,
                            previous_state_id=head['id'] if head else None,previous_initialization=previous,
                            requested_initialization=selected.initialized_at,previous_field_count=len(saved['field_ids']),
                            previous_model_id=saved['completed_id'],provider_availability_verified=False)
                active=dict(event_id=event,collection_id='gefs-get:'+digest([key,event,len(saved['field_ids'])]))
                state['active']=active;self._save(key+':begin',state,outcome='GEFS_STEP_RESERVED',event_id=event)
            event=active['event_id'];plan=self.plans[event];saved=state['events'][event]
            if self.rollover is not None:plan=replace(plan,initialized_at=saved['initialized_at'])
            run_key=[self.config,event] if self.rollover is None else [self.config,event,plan.initialized_at]
            try:
                if not 0<=self.store.clock()-plan.initialized_at<plan.maximum_run_age_seconds:
                    raise EvidenceError('GEFS_RUN_PLAN_STALE_OR_FUTURE')
                current=self.store.latest_source(kind='MODEL',event_id=event,provider=PROVIDER,source_identity=plan.source_identity)
                if current is not None and current['id']!=saved['completed_id']:
                    p=current['body'].get('payload',{});refs=p.get('field_references',[])
                    if (p.get('version')==PATH_VERSION and current['body']['issued_at']==plan.initialized_at
                            and len(refs)==31*len(plan.hours) and p.get('path_sha256')==digest(dict(
                                plan=asdict(plan),field_ids=[r['id'] for r in refs],version=PATH_VERSION))):
                        current_path_heads(self.store,current)
                        saved.update(field_ids=[r['id'] for r in refs],completed_id=current['id']);state['active']=None
                        return self._save(key,state,outcome='CURRENT_COMPLETE_RUN_ADOPTED_NO_REFETCH',event_id=event,model_id=current['id'])
                if saved['completed_id']:
                    current_path_heads(self.store,self.store.get(saved['completed_id']))
                    state['active']=None
                    return self._save(key,state,outcome='COMPLETED_RUN_NO_REFETCH_OR_RECEIPT_RENEWAL',event_id=event,
                                      model_id=saved['completed_id'])
                slots=[(m,h) for m in range(31) for h in plan.hours]
                if len(saved['field_ids'])==len(slots):
                    model=assemble_path(self.store,plan=plan,field_ids=tuple(saved['field_ids']),record_id='gefs-path:'+digest(run_key),
                                        deadline=time.monotonic()+2.)
                    saved['completed_id']=model['id'];state['active']=None
                    return self._save(key,state,outcome='RUN_BOUND_LINEAR_PATH_ARCHIVED_UNCALIBRATED',event_id=event,model_id=model['id'])
                member,hour=slots[len(saved['field_ids'])];request=field_request(plan,member,hour)
                source=self.store.latest_source(kind='MODEL',event_id=event,provider=PROVIDER,source_identity=request.source_identity)
                normalized_id='gefs-field:'+digest([*run_key,member,hour])
                if source is None:
                    cid=active['collection_id']
                    # Reconcile an interrupted GET from its durable receipt. A
                    # reserved attempt without a receipt is never blindly resent.
                    source=self._get(cid+':0:capture')
                    if source is None and self._get(cid+':schedule:0:reserve'):
                        state['active']=None
                        return self._save(key,state,outcome='INTERRUPTED_COLLECTION_NOT_RETRIED',event_id=event)
                    if source is None:
                        result=await self.scheduled.cycle(cid,(request,))
                        good=[s for s in result['sources'] if s['state']=='SUCCESS']
                        if not good:
                            state['active']=None
                            return self._save(key,state,outcome='GEFS_SOURCE_PENDING',event_id=event,
                                states=[s['state'] for s in result['sources']],omitted=len(result['omitted']))
                        source=self.store.get(good[0]['capture_ids'][0])
                if source['body']['payload'].get('version')==FIELD_VERSION:
                    normalized=normalize_field(self.store,source['body']['payload']['raw_evidence_id'],plan=plan,
                        member=member,hour=hour,record_id=source['id'])
                else:
                    normalized=normalize_field(self.store,source['id'],plan=plan,member=member,hour=hour,record_id=normalized_id)
                saved['field_ids'].append(normalized['id']);state['active']=None
                return self._save(key,state,outcome='GEFS_FIELD_ARCHIVED_PATH_INCOMPLETE',event_id=event,
                    completed_fields=len(saved['field_ids']),required_fields=len(slots),normalized_id=normalized['id'])
            except (EvidenceError,KeyError,TypeError,ValueError) as exc:
                state['active']=None
                return self._save(key,state,outcome='GEFS_SOURCE_GATED',event_id=event,
                    reason=str(exc) if isinstance(exc,EvidenceError) else type(exc).__name__)
        finally:os.close(fd)
