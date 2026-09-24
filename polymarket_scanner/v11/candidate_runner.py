"""Finite off-host PAPER candidate orchestration, with no service/order transport.

One public collection job shares cooperative execution with priority safety ticks.
This is not an OS-isolated guardian or a hard real-time/deployment acceptance claim.
"""
import asyncio
from copy import deepcopy
from dataclasses import asdict, dataclass, replace
import fcntl
import os
import time

from .audit_reports import AuditWorker
from .census_worker import CensusWorker
from .discovery import MarketDiscovery
from .evidence import EvidenceError, canonical, digest, finite, identity
from .observation_pump import ObservationPump
from .maker_telemetry import MakerTelemetryWorker
from .pws_runtime import PWSQualityWorker
from .paper_runtime import PaperRuntime
from .runtime_health import KEY as HEALTH_KEY


VERSION = 'alpha_v11_candidate_runner_v1'
KEY = 'v11-candidate-runner'


@dataclass(frozen=True)
class CandidatePolicy:
    version: str
    maximum_seconds: float = 30.
    maximum_jobs: int = 8
    maximum_safety_ticks: int = 64
    safety_interval_seconds: float = .5
    job_timeout_seconds: float = 20.
    minimum_job_spacing_seconds: float = .25
    discovery_scan_interval_seconds: float = 300.

    def __post_init__(self):
        identity(self.version)
        for name,low,high in (('maximum_seconds',.1,300),('safety_interval_seconds',.05,5),
                ('job_timeout_seconds',.1,60),('minimum_job_spacing_seconds',.05,60),
                ('discovery_scan_interval_seconds',1,86400)):
            if not low <= finite(getattr(self,name)) <= high:raise EvidenceError('CANDIDATE_TIME_BOUND')
        for name,low,high in (('maximum_jobs',1,32),('maximum_safety_ticks',2,256)):
            if type(getattr(self,name)) is not int or not low <= getattr(self,name) <= high:
                raise EvidenceError('CANDIDATE_COUNT_BOUND')


@dataclass(frozen=True)
class ObservationBatch:
    requests: tuple
    station_by_event: tuple[tuple[str,str], ...]
    strategies: tuple[str, ...]
    required_providers_by_strategy: tuple[tuple[str,tuple[str,...]], ...]

    def __post_init__(self):
        from .collection import SourceRequest
        if (type(self.requests) is not tuple or not 1 <= len(self.requests) <= 16
                or any(not isinstance(r,SourceRequest) for r in self.requests)
                or type(self.station_by_event) is not tuple or not 1 <= len(self.station_by_event) <= 16
                or any(type(x) is not tuple or len(x)!=2 for x in self.station_by_event)
                or len(dict(self.station_by_event))!=len(self.station_by_event)
                or type(self.strategies) is not tuple or not 1 <= len(self.strategies) <= 16
                or len(set(self.strategies))!=len(self.strategies)
                or type(self.required_providers_by_strategy) is not tuple
                or len(self.required_providers_by_strategy)>16
                or any(type(x) is not tuple or len(x)!=2 for x in self.required_providers_by_strategy)
                or len(dict(self.required_providers_by_strategy))!=len(self.required_providers_by_strategy)):
            raise EvidenceError('CANDIDATE_OBSERVATION_PLAN_BOUND')
        for event,station in self.station_by_event:identity(event);identity(station)
        for strategy in self.strategies:identity(strategy)
        providers={r.provider for r in self.requests}
        if (not {r.event_id for r in self.requests} <= dict(self.station_by_event).keys()
                or set(dict(self.required_providers_by_strategy))!=set(self.strategies)):
            raise EvidenceError('CANDIDATE_OBSERVATION_SCOPE')
        for _,needed in self.required_providers_by_strategy:
            if (type(needed) is not tuple or not needed or len(set(needed))!=len(needed)
                    or not set(needed)<=providers):raise EvidenceError('CANDIDATE_OBSERVATION_SCOPE')


class CandidateRunner:
    def __init__(self,runtime,policy,*,census,discovery,audits,observation=None,observation_batch=None,maker_telemetry=None,pws_quality=None):
        if (not isinstance(runtime,PaperRuntime) or not isinstance(policy,CandidatePolicy)
                or not isinstance(census,CensusWorker) or not isinstance(discovery,MarketDiscovery)
                or not isinstance(audits,AuditWorker)):
            raise EvidenceError('CANDIDATE_COMPONENT_REQUIRED')
        if (runtime.worker_id is None or census.queue is not runtime.queue or census.health is not runtime.health
                or discovery.health is not runtime.health or audits.coordinator is not runtime.coordinator
                or audits.schedule_config!=runtime.audits.config or census.scheduled is not discovery.scheduled):
            raise EvidenceError('CANDIDATE_COMPONENT_SCOPE')
        if ((observation is None)!=(observation_batch is None)
                or observation is not None and (not isinstance(observation,ObservationPump)
                    or not isinstance(observation_batch,ObservationBatch) or observation.runtime is not runtime
                    or observation.observation.scheduled is not census.scheduled)):
            raise EvidenceError('CANDIDATE_OBSERVATION_SCOPE')
        if maker_telemetry is not None and (not isinstance(maker_telemetry,MakerTelemetryWorker)
                or maker_telemetry.research is not runtime.maker or maker_telemetry.health is not runtime.health
                or not set(maker_telemetry.event_ids)<=runtime.queue.routes.keys()):
            raise EvidenceError('CANDIDATE_MAKER_TELEMETRY_SCOPE')
        self.runtime,self.policy,self.store=runtime,policy,runtime.store
        self.census,self.discovery,self.audits=census,discovery,audits
        self.observation,self.observation_batch=observation,observation_batch
        self.maker_telemetry=maker_telemetry
        if pws_quality is not None and (not isinstance(pws_quality,PWSQualityWorker)
                or pws_quality.health is not runtime.health or not pws_quality.plans.keys()<=runtime.queue.routes.keys()
                or any(p.official.station!=runtime.queue.routes[e].station for e,p in pws_quality.plans.items())):
            raise EvidenceError('CANDIDATE_PWS_QUALITY_SCOPE')
        self.pws_quality=pws_quality
        self.kinds=('CENSUS','DISCOVERY','AUDIT')+(('OBSERVATION',) if observation else ())+(('MAKER_TELEMETRY',) if maker_telemetry else ())+(('PWS_QUALITY',) if pws_quality else ())
        config=dict(policy=asdict(policy),runtime=runtime.config,census=census.config,
            discovery=discovery.config,audits=audits.config,worker_id=runtime.worker_id,
            observation=asdict(observation_batch) if observation_batch else None)
        if maker_telemetry is not None:config['maker_telemetry']=maker_telemetry.config
        if pws_quality is not None:config['pws_quality']=pws_quality.config
        self.config=digest(config)

    def _get(self,key):
        try:return self.store.get(key)
        except EvidenceError as exc:
            if str(exc)!='EVIDENCE_MISSING':raise
        return None

    def _head(self):
        row=self.store.latest(kind='RUNTIME_STATUS',event_id=KEY)
        if row and row['body']['details'].get('config_sha256')!=self.config:
            raise EvidenceError('CANDIDATE_CONFIGURATION_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self,key,state,**details):
        if len(canonical(state).encode())>64*1024:raise EvidenceError('CANDIDATE_STATE_BOUND')
        head=self._head()
        return self.store.safety_audit(key,event_id=KEY,kind='RUNTIME_STATUS',details=dict(
            version=VERSION,config_sha256=self.config,state=state,**details,
            financial_authority=False,real_orders_sent=False,deployment_acceptance=False,
            independent_guardian_commissioned=False),expected_previous_seq=head['seq'] if head else 0)

    def _progress(self,run_id,state,**details):
        head=self._head()
        return self._save('candidate-progress:'+digest([run_id,head['seq'] if head else 0]),state,**details)

    def _healthy_clock(self):
        row=self.store.latest(kind='RUNTIME_STATUS',event_id=HEALTH_KEY)
        return (row is not None and not row['body']['details']['clock_reasons']
                and 0 <= self.store.clock()-row['body']['recorded_at'] < self.runtime.health.policy.maximum_sample_age_seconds)

    async def _job(self,job):
        kind,key=job['kind'],job['id']
        if kind=='CENSUS':return await self.census.step(key)
        if kind=='DISCOVERY':
            if self.discovery._get('discovery-step:'+digest(key)) is not None:
                return await self.discovery.step(key)
            head=self.discovery._head()
            if head is None or head['body']['details']['state']['phase'] in {'COMPLETE','INCOMPLETE'}:
                self.discovery.start('candidate-scan:'+digest(key))
            return await self.discovery.step(key)
        if kind=='AUDIT':return self.audits.step()
        if kind=='MAKER_TELEMETRY':return self.maker_telemetry.step(key)
        if kind=='PWS_QUALITY':return self.pws_quality.step(key)
        batch=self.observation_batch
        return await self.observation.cycle(key,tuple(replace(r,revision=key) for r in batch.requests),
            station_by_event=dict(batch.station_by_event),strategies=batch.strategies,
            required_providers_by_strategy=dict(batch.required_providers_by_strategy))

    async def run(self,run_id):
        """One finite invocation. Its completed identity never renews any work."""
        identity(run_id,maximum=80);final='candidate-run:'+digest(run_id)
        previous=self._get(final)
        if previous:
            if previous['body']['details'].get('config_sha256')!=self.config:raise EvidenceError('CANDIDATE_REPLAY_CONFIG')
            return previous
        fd=os.open(self.store.path.with_name(self.store.path.name+'.candidate.lock'),os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise EvidenceError('CANDIDATE_ALREADY_RUNNING') from None
            return await self._run(run_id,final)
        finally:os.close(fd)

    async def _run(self,run_id,final):
        head=self._head();state=deepcopy(head['body']['details']['state']) if head else dict(
            sequence=0,next_kind=0,active=None,discovery_not_before=0.)
        self._progress(run_id,state,outcome='RUN_STARTED')
        started=time.monotonic();deadline=started+self.policy.maximum_seconds
        next_safety=started;next_job=started;ticks=[];jobs=[];errors=[];task=None;job_deadline=None
        maximum_gap=0.;last_safety=None;interrupted=False;end_reason='RUN_BUDGET';runtime_outcomes={}

        def safety():
            nonlocal last_safety,maximum_gap,next_safety
            now=time.monotonic()
            if last_safety is not None:maximum_gap=max(maximum_gap,now-last_safety)
            last_safety=now
            h=self._head();key='candidate-tick:'+digest([run_id,h['seq'],len(ticks)])
            try:
                row=self.runtime.tick(key);ticks.append(row['id'])
                outcome=row['body']['details']['outcome']
                runtime_outcomes[outcome]=runtime_outcomes.get(outcome,0)+1
                self._progress(run_id,state,outcome='SAFETY_TICK_RECORDED',runtime_id=row['id'])
            except (EvidenceError,OSError,TimeoutError) as exc:
                errors.append(dict(stage='SAFETY',reason=str(exc) if isinstance(exc,EvidenceError) else type(exc).__name__))
                ticks.append(None)
            next_safety=time.monotonic()+self.policy.safety_interval_seconds

        async def finish_task(reason=None):
            nonlocal task,next_job
            job=deepcopy(state['active'])
            if reason is not None and not task.done():task.cancel()
            try:
                result=await task
                details=result.get('body',{}).get('details',result)
                output=dict(kind=job['kind'],command_id=job['id'],outcome=details.get('outcome','UNKNOWN'),
                    record_id=result.get('id') or details.get('report_id') or details.get('progress_id'))
                # A timeout/caller interruption never becomes a successful job claim.
                if reason is not None:output.update(interruption_reason=reason)
                if job['kind']=='DISCOVERY' and details.get('state',{}).get('phase') in {'COMPLETE','INCOMPLETE'}:
                    state['discovery_not_before']=finite(self.store.clock())+self.policy.discovery_scan_interval_seconds
                state['active']=None
            except asyncio.CancelledError:
                output=dict(kind=job['kind'],command_id=job['id'],outcome='INTERRUPTED_PENDING_RECOVERY',reason=reason)
                # Preserve the exact command for each worker's causal recovery rules.
            except Exception as exc:
                output=dict(kind=job['kind'],command_id=job['id'],outcome='WORKER_GATED',
                    reason=str(exc) if isinstance(exc,EvidenceError) else type(exc).__name__)
                errors.append(output)
                state['active']=None  # worker's own durable partial state remains authoritative
            jobs.append(output);task=None;next_job=time.monotonic()+self.policy.minimum_job_spacing_seconds
            self._progress(run_id,state,outcome='WORKER_RESULT_RECORDED',worker=output)

        try:
            while time.monotonic()<deadline and len(ticks)<self.policy.maximum_safety_ticks-1:
                now=time.monotonic()
                if now>=next_safety:safety()
                if errors and errors[-1].get('stage')=='SAFETY':end_reason='SAFETY_UNAVAILABLE';break
                if task is not None:
                    if task.done():await finish_task()
                    elif time.monotonic()>=job_deadline:
                        await finish_task('JOB_TIME_BOUND');end_reason='WORKER_INTERRUPTED';break
                if task is None and len(jobs)>=self.policy.maximum_jobs:end_reason='JOB_COUNT_BOUND';break
                if task is None and time.monotonic()>=next_job and self._healthy_clock():
                    if state['active'] is None:
                        selected=None
                        for _ in self.kinds:
                            kind=self.kinds[state['next_kind']];state['next_kind']=(state['next_kind']+1)%len(self.kinds)
                            if kind=='DISCOVERY' and self.store.clock()<state['discovery_not_before']:continue
                            selected=kind;break
                        if selected is not None:
                            state['sequence']+=1
                            state['active']=dict(kind=selected,id='candidate-job:'+digest([self.config,state['sequence']]))
                            self._progress(run_id,state,outcome='WORKER_RESERVED')
                    if state['active'] is not None:
                        task=asyncio.create_task(self._job(deepcopy(state['active'])))
                        job_deadline=min(deadline,time.monotonic()+self.policy.job_timeout_seconds)
                delay=max(0,min(next_safety,deadline,job_deadline if task else next_job)-time.monotonic())
                if task:
                    await asyncio.wait((task,),timeout=min(self.policy.safety_interval_seconds,delay))
                else:
                    # No catch-up spin when clock gating leaves next_job in the past.
                    await asyncio.sleep(max(.01,min(self.policy.safety_interval_seconds,delay)))
        except asyncio.CancelledError:
            interrupted=True;end_reason='CALLER_INTERRUPTED';raise
        finally:
            if task is not None:
                await finish_task(end_reason)
            if len(ticks)<self.policy.maximum_safety_ticks:safety()
            self._progress(run_id,state,outcome='INTERRUPTED' if interrupted else 'RUN_DRAINED',reason=end_reason)
        duration=time.monotonic()-started
        return self._save(final,state,outcome='DEGRADED' if errors or state['active'] is not None else 'BOUNDED_RUN_FINISHED',
            end_reason=end_reason,runtime_ids=ticks,worker_results=jobs,errors=errors,
            duration_monotonic_seconds=duration,cooperative_budget_overrun_seconds=max(0,duration-self.policy.maximum_seconds),
            maximum_safety_start_gap_seconds=maximum_gap,clock_healthy_at_finish=self._healthy_clock(),
            runtime_outcome_counts=runtime_outcomes,
            all_async_jobs_drained=True,active_command_requires_recovery=state['active'] is not None,
            forward_acceptance=False,hard_realtime_guarantee=False)
