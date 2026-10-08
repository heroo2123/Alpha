"""Fresh GEFS census collection spread across bounded, non-executing steps.

The queue's epoch precedes every raw response. It expires or invalidates on a
new loss generation. Books and other observations are collected later under the
ordinary short claim, and only then is the full model assembled and checked.

Persistence is O(1) per step: each RUNTIME_STATUS row records only the delta
(the field just staged, if any, plus a running count/hash-chain digest), never
the whole accumulated field_ids list. fields() reconstructs the current tuple
by replaying this census run's own RUNTIME_STATUS chain, walking backward from
the latest row through each row's own `chain_previous_id` pointer. Only the
genesis row and field-bearing rows advance that pointer; reserve/pending/
interrupted rows point at the last advancing row and are never walked unless
they are the head. The walk is therefore bounded by the plan's own slot count
(genesis + fields + head) however long a source stays pending. A pre-existing head written by the old
full-list format is still read correctly (and transparently promoted to the
new delta format on the next write), so a run already in progress at upgrade
time resumes without loss or duplication.
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
CHAIN_FORMAT='alpha_v11_model_census_chain_v2'


def restore_plan(preparation):
    p=deepcopy(preparation['plan']);f=p.pop('forecast');m=f.pop('metadata');r=f.pop('rule')
    for key in ('observation_providers','forecast_providers'):m[key]=tuple(m[key])
    plan=GEFSPlan(ForecastPlan(RuleFingerprint(**r),StationMetadata(**m),**f),**p)
    if digest(asdict(plan))!=preparation['plan_sha256']:raise EvidenceError('CENSUS_MODEL_PLAN_INTEGRITY')
    return plan


def _genesis_digest(inherited):
    return digest(dict(inherited_root=digest(list(inherited))))


def _chain_digest(previous_digest,field_id):
    return digest(dict(previous_digest=previous_digest,field_id=field_id))


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

    def _fresh_cursor(self):
        return dict(field_ids=(),active=None,chain_previous_id=None,
                    inherited_field_ids=[],chain_digest=_genesis_digest([]),chain_count=0)

    def _reconstruct(self,preparation):
        """Replay only this preparation's own chain to rebuild field_ids/active.

        Fails closed (typed EvidenceError) on a gap, reorder, duplicate, foreign
        row, or digest mismatch, instead of silently accepting corrupted state.
        A legacy full-list head (written before this delta format existed) is
        read directly and trusted exactly as it always was: the storage layer
        already byte-verifies that single row.
        """
        event=preparation['event_id'];prep_id=preparation['id']
        head=self._head(event)
        if head is None or head['body']['details'].get('preparation_id')!=prep_id:
            return self._fresh_cursor()
        d=head['body']['details']
        if 'census' not in d:
            legacy=d.get('state') or {}
            ids=legacy.get('field_ids')
            if type(ids) is not list or any(type(x) is not str for x in ids):
                raise EvidenceError('MODEL_CENSUS_LEGACY_STATE_INVALID')
            return dict(field_ids=tuple(ids),active=legacy.get('active'),chain_previous_id=None,
                        inherited_field_ids=list(ids),chain_digest=_genesis_digest(ids),chain_count=len(ids))
        required=31*len(restore_plan(preparation).hours)
        # Genesis + at most `required` field rows + one non-advancing head.
        bound=required+2
        chain=[];node=head
        while True:
            if len(chain)>=bound:raise EvidenceError('MODEL_CENSUS_CHAIN_BOUND_EXCEEDED')
            nd=node['body']['details']
            if (node['kind']!='RUNTIME_STATUS' or node['event_id']!='model-census:'+event
                    or nd.get('preparation_id')!=prep_id):
                raise EvidenceError('MODEL_CENSUS_CHAIN_FOREIGN_ROW')
            census=nd.get('census')
            if type(census) is not dict or census.get('format')!=CHAIN_FORMAT:
                raise EvidenceError('MODEL_CENSUS_CHAIN_FORMAT_INVALID')
            chain.append(census)
            prev_id=census.get('chain_previous_id')
            if prev_id is None:break
            if type(prev_id) is not str:raise EvidenceError('MODEL_CENSUS_CHAIN_FORMAT_INVALID')
            try:node=self.store.get(prev_id)
            except EvidenceError as exc:
                if str(exc)=='EVIDENCE_MISSING':raise EvidenceError('MODEL_CENSUS_CHAIN_GAP') from None
                raise
        chain.reverse()
        genesis=chain[0];inherited=genesis.get('inherited_field_ids')
        if type(inherited) is not list or any(type(x) is not str for x in inherited):
            raise EvidenceError('MODEL_CENSUS_CHAIN_FORMAT_INVALID')
        ids=list(inherited);seen=set(ids)
        if len(seen)!=len(ids):raise EvidenceError('MODEL_CENSUS_CHAIN_DUPLICATE_FIELD')
        running_digest=_genesis_digest(inherited);running_count=len(inherited)
        for index,census in enumerate(chain):
            field=census.get('field_id')
            if field is None and 0<index<len(chain)-1:
                # A non-advancing row is only ever the head; nothing chains onto it.
                raise EvidenceError('MODEL_CENSUS_CHAIN_FORMAT_INVALID')
            if index and census.get('inherited_field_ids') is not None:
                raise EvidenceError('MODEL_CENSUS_CHAIN_FORMAT_INVALID')
            if field is not None:
                if type(field) is not str or field in seen:raise EvidenceError('MODEL_CENSUS_CHAIN_DUPLICATE_FIELD')
                seen.add(field);ids.append(field);running_count+=1
                running_digest=_chain_digest(running_digest,field)
            if census.get('count')!=running_count:raise EvidenceError('MODEL_CENSUS_CHAIN_GAP')
            if census.get('digest')!=running_digest:raise EvidenceError('MODEL_CENSUS_CHAIN_DIGEST_MISMATCH')
        if len(ids)>required:raise EvidenceError('MODEL_CENSUS_CHAIN_OVERRUN')
        # Next rows chain onto the last advancing row: the head itself if it is
        # genesis or carries a field, else the row the head points at.
        anchor=head['id'] if len(chain)==1 or chain[-1].get('field_id') is not None else chain[-1]['chain_previous_id']
        return dict(field_ids=tuple(ids),active=chain[-1].get('active'),chain_previous_id=anchor,
                    inherited_field_ids=None,chain_digest=running_digest,chain_count=running_count)

    def fields(self,preparation):
        return self._reconstruct(preparation)['field_ids']

    def _save(self,key,p,cursor,*,field_id=None,**details):
        head=self._head(p['event_id'])
        new_count=cursor['chain_count']+(1 if field_id is not None else 0)
        new_digest=_chain_digest(cursor['chain_digest'],field_id) if field_id is not None else cursor['chain_digest']
        census=dict(format=CHAIN_FORMAT,chain_previous_id=cursor['chain_previous_id'],
                    inherited_field_ids=cursor['inherited_field_ids'],count=new_count,
                    field_id=field_id,active=cursor['active'],digest=new_digest)
        row=self.store.audit(key,event_id='model-census:'+p['event_id'],kind='RUNTIME_STATUS',details=dict(
            version=VERSION,preparation_id=p['id'],census=census,**details,financial_authority=False),
            evidence_ids=(p['id'],),expected_previous_seq=head['seq'] if head else 0)
        # Only genesis and field rows advance the chain, so reserve/pending rows
        # never lengthen the replay walk; a stage after a reserve within the same
        # step chains onto the same last advancing row.
        if field_id is not None or cursor['chain_previous_id'] is None:cursor['chain_previous_id']=row['id']
        cursor['inherited_field_ids']=None
        cursor['chain_digest']=new_digest;cursor['chain_count']=new_count
        if field_id is not None:cursor['field_ids']=cursor['field_ids']+(field_id,)
        return row

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
        cursor=self._reconstruct(p)
        slots=[(m,h) for m in range(31) for h in plan.hours]
        if len(cursor['field_ids'])==len(slots):return self._save(key,p,cursor,outcome='MODEL_FIELDS_READY_FOR_SHORT_CENSUS')
        member,hour=slots[len(cursor['field_ids'])];request=field_request(plan,member,hour)
        if cursor['active'] is None:
            cursor['active']='model-get:'+digest([key,member,hour])
            self._save(key+':begin',p,cursor,outcome='MODEL_FIELD_GET_RESERVED')
        cid=cursor['active']
        source=self.store.latest_source(kind='MODEL',event_id=event_id,provider=PROVIDER,source_identity=request.source_identity)
        if source is not None:
            raw=self.store.get(source['body']['payload']['raw_evidence_id']) if source['body']['payload'].get('version')==FIELD_VERSION else source
            if raw['seq']<=barrier['seq']:source=None
        if source is None:
            source=self._get(cid+':0:capture')
            if source is None and self._get(cid+':schedule:0:reserve'):
                cursor['active']=None
                return self._save(key,p,cursor,outcome='INTERRUPTED_MODEL_GET_NOT_RETRIED')
            if source is None:
                result=await self.scheduled.cycle(cid,(request,))
                good=[s for s in result['sources'] if s['state']=='SUCCESS']
                if not good:
                    cursor['active']=None
                    return self._save(key,p,cursor,outcome='MODEL_FIELD_SOURCE_PENDING',
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
        cursor['active']=None
        return self._save(key,p,cursor,field_id=normalized['id'],outcome='MODEL_FIELD_STAGED',
            completed_fields=len(cursor['field_ids'])+1,required_fields=len(slots),normalized_id=normalized['id'])
