"""Fresh GEFS census collection spread across bounded, non-executing steps.

The queue's epoch precedes every raw response. It expires or invalidates on a
new loss generation. Books and other observations are collected later under the
ordinary short claim, and only then is the full model assembled and checked.
"""
from copy import deepcopy
from dataclasses import asdict
import fcntl
import os

from .certification import StationMetadata
from .evidence import EvidenceError, digest, identity
from .forecast_sources import ForecastPlan
from .gefs_sources import GEFSPlan, PROVIDER, FIELD_VERSION, field_request, normalize_field
from .rules import RuleFingerprint


VERSION='alpha_v11_model_census_stage_v1'


def restore_plan(preparation):
    p=deepcopy(preparation['plan']);f=p.pop('forecast');m=f.pop('metadata');r=f.pop('rule')
    for key in ('observation_providers','forecast_providers'):m[key]=tuple(m[key])
    plan=GEFSPlan(ForecastPlan(RuleFingerprint(**r),StationMetadata(**m),**f),**p)
    if digest(asdict(plan))!=preparation['plan_sha256']:raise EvidenceError('CENSUS_MODEL_PLAN_INTEGRITY')
    return plan


class ModelCensusStage:
    def __init__(self,queue,scheduled):
        if queue.store is not scheduled.store:raise EvidenceError('CENSUS_MODEL_STORE_SCOPE')
        self.queue,self.scheduled,self.store=queue,scheduled,queue.store

    def _get(self,key):
        try:return self.store.get(key)
        except EvidenceError as exc:
            if str(exc)!='EVIDENCE_MISSING':raise
        return None

    def _head(self,event):return self.store.latest(kind='RUNTIME_STATUS',event_id='model-census:'+event)

    def fields(self,preparation):
        head=self._head(preparation['event_id'])
        if head is None or head['body']['details'].get('preparation_id')!=preparation['id']:return ()
        return tuple(head['body']['details']['state']['field_ids'])

    def _save(self,key,p,state,**details):
        head=self._head(p['event_id'])
        return self.store.audit(key,event_id='model-census:'+p['event_id'],kind='RUNTIME_STATUS',details=dict(
            version=VERSION,preparation_id=p['id'],state=state,**details,financial_authority=False),
            evidence_ids=(p['id'],),expected_previous_seq=head['seq'] if head else 0)

    async def step(self,command_id,*,preparation_id,event_id):
        identity(command_id,maximum=100)
        fd=os.open(self.store.path.with_name(self.store.path.name+'.model-census.lock'),os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise EvidenceError('MODEL_CENSUS_ALREADY_RUNNING') from None
            return await self._step(command_id,preparation_id=preparation_id,event_id=event_id)
        finally:os.close(fd)

    async def _step(self,command_id,*,preparation_id,event_id):
        barrier,p=self.queue.model_preparation(preparation_id,event_id=event_id);plan=restore_plan(p)
        if not 0<=self.store.clock()-plan.initialized_at<plan.maximum_run_age_seconds:
            raise EvidenceError('GEFS_RUN_PLAN_STALE_OR_FUTURE')
        key='model-stage:'+digest([command_id,preparation_id]);prior=self._get(key)
        if prior:return prior
        head=self._head(event_id)
        state=(deepcopy(head['body']['details']['state']) if head and head['body']['details'].get('preparation_id')==preparation_id
               else dict(field_ids=[],active=None))
        slots=[(m,h) for m in range(31) for h in plan.hours]
        if len(state['field_ids'])==len(slots):return self._save(key,p,state,outcome='MODEL_FIELDS_READY_FOR_SHORT_CENSUS')
        member,hour=slots[len(state['field_ids'])];request=field_request(plan,member,hour)
        if state['active'] is None:
            state['active']='model-get:'+digest([key,member,hour])
            self._save(key+':begin',p,state,outcome='MODEL_FIELD_GET_RESERVED')
        cid=state['active']
        source=self.store.latest_source(kind='MODEL',event_id=event_id,provider=PROVIDER,source_identity=request.source_identity)
        if source is not None:
            raw=self.store.get(source['body']['payload']['raw_evidence_id']) if source['body']['payload'].get('version')==FIELD_VERSION else source
            if raw['seq']<=barrier['seq']:source=None
        if source is None:
            source=self._get(cid+':0:capture')
            if source is None and self._get(cid+':schedule:0:reserve'):
                state['active']=None
                return self._save(key,p,state,outcome='INTERRUPTED_MODEL_GET_NOT_RETRIED')
            if source is None:
                result=await self.scheduled.cycle(cid,(request,))
                good=[s for s in result['sources'] if s['state']=='SUCCESS']
                if not good:
                    state['active']=None
                    return self._save(key,p,state,outcome='MODEL_FIELD_SOURCE_PENDING',
                        states=[s['state'] for s in result['sources']],omitted=len(result['omitted']))
                source=self.store.get(good[0]['capture_ids'][0])
        # A lost/expired collection epoch never gains eligibility from a late
        # response. Its successfully captured bytes remain immutable evidence.
        self.queue.model_preparation(preparation_id,event_id=event_id)
        if source['body']['payload'].get('version')==FIELD_VERSION:
            normalized=normalize_field(self.store,source['body']['payload']['raw_evidence_id'],plan=plan,
                member=member,hour=hour,record_id=source['id'])
        else:
            normalized=normalize_field(self.store,source['id'],plan=plan,member=member,hour=hour,
                record_id='census-field:'+digest([preparation_id,source['sha256'],member,hour]))
        raw=self.store.get(normalized['body']['payload']['raw_evidence_id'])
        if not barrier['seq']<raw['seq']<normalized['seq'] or raw['body']['received_at']<p['began_at']:
            raise EvidenceError('CENSUS_MODEL_NEW_RAW_REQUIRED')
        state['field_ids'].append(normalized['id']);state['active']=None
        return self._save(key,p,state,outcome='MODEL_FIELD_STAGED',completed_fields=len(state['field_ids']),required_fields=len(slots),
                          normalized_id=normalized['id'])
