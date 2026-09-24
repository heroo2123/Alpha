"""One archived forecast normalization per step; no new network authority."""
from copy import deepcopy
from dataclasses import asdict
import fcntl
import os

from .evidence import EvidenceError, digest, identity
from .forecast_sources import ForecastPlan, PROVIDER, VERSION as SOURCE_VERSION, normalize_forecast_capture


VERSION='alpha_v11_forecast_normalization_worker_v1'
KEY='v11-forecast-normalization-worker'


class ForecastNormalizationWorker:
    def __init__(self,store,health,plans):
        if (health.store is not store or type(plans) is not tuple or not 1<=len(plans)<=16
                or any(not isinstance(p,ForecastPlan) for p in plans)
                or len({p.event_id for p in plans})!=len(plans)):
            raise EvidenceError('FORECAST_WORKER_SCOPE_BOUND')
        self.store,self.health=store,health
        self.plans={p.event_id:p for p in plans}
        self.config=digest(dict(plans=[asdict(p) for p in plans],health=health.config))

    def _get(self,key):
        try:return self.store.get(key)
        except EvidenceError as exc:
            if str(exc)!='EVIDENCE_MISSING':raise
        return None

    def _head(self):
        row=self.store.latest(kind='RUNTIME_STATUS',event_id=KEY)
        if row and row['body']['details'].get('config_sha256')!=self.config:
            raise EvidenceError('FORECAST_WORKER_CONFIG_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self,key,state,**details):
        head=self._head()
        return self.store.safety_audit(key,event_id=KEY,kind='RUNTIME_STATUS',details=dict(
            version=VERSION,config_sha256=self.config,state=state,**details,
            financial_authority=False,forecast_run_verified=False,forward_acceptance=False),
            expected_previous_seq=head['seq'] if head else 0)

    def step(self,command_id):
        identity(command_id,maximum=80);key='forecast-step:'+digest(command_id)
        prior=self._get(key)
        if prior:
            if prior['body']['details'].get('config_sha256')!=self.config:raise EvidenceError('FORECAST_WORKER_REPLAY_CONFIG')
            return prior
        fd=os.open(self.store.path.with_name(self.store.path.name+'.forecast.lock'),os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise EvidenceError('FORECAST_WORKER_ALREADY_RUNNING') from None
            head=self._head();state=deepcopy(head['body']['details']['state']) if head else dict(last_event='',active=None)
            clock=self.health.sample(key+':clock')
            if clock['body']['details']['clock_reasons']:return self._save(key,state,outcome='DEFERRED_CLOCK_UNHEALTHY')
            active=state['active']
            if active is None:
                ordered=sorted(self.plans);event=next((e for e in ordered if e>state['last_event']),ordered[0]);state['last_event']=event
                plan=self.plans[event]
                source=self.store.latest_source(kind='MODEL',event_id=event,provider=PROVIDER,source_identity=plan.source_identity)
                if source is None:return self._save(key,state,outcome='FORECAST_SOURCE_ABSENT',event_id=event)
                if source['body']['payload'].get('version')==SOURCE_VERSION:
                    try:normalize_forecast_capture(self.store,source['body']['payload']['raw_evidence_id'],plan=plan,record_id=source['id'])
                    except (EvidenceError,KeyError,TypeError,ValueError) as exc:
                        return self._save(key,state,outcome='FORECAST_NORMALIZATION_GATED',event_id=event,
                            reason=str(exc) if isinstance(exc,EvidenceError) else type(exc).__name__)
                    return self._save(key,state,outcome='NORMALIZED_SOURCE_RUN_STILL_UNVERIFIED',event_id=event,normalized_id=source['id'])
                active=dict(event_id=event,raw_id=source['id'],normalized_id='forecast-normalized:'+digest([key,source['id'],self.config]))
                state['active']=active;self._save(key+':begin',state,outcome='FORECAST_RAW_PINNED',event_id=event)
            event=active['event_id']
            try:result=normalize_forecast_capture(self.store,active['raw_id'],plan=self.plans[event],record_id=active['normalized_id'])
            except (EvidenceError,KeyError,TypeError,ValueError) as exc:
                state['active']=None
                return self._save(key,state,outcome='FORECAST_NORMALIZATION_GATED',event_id=event,
                    reason=str(exc) if isinstance(exc,EvidenceError) else type(exc).__name__)
            state['active']=None
            return self._save(key,state,outcome='FORECAST_MEMBERS_ARCHIVED_RUN_UNVERIFIED',event_id=event,normalized_id=result['id'])
        finally:os.close(fd)
