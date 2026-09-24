"""One bounded public observation cycle between paper safety/runtime ticks.

Existing collector allowlists, quotas and retry budgets remain authoritative.
No ambiguous restart automatically repeats an interrupted collection cycle.
"""
from dataclasses import asdict
import fcntl
import os

from .evidence import EvidenceError, digest, identity
from .runtime_health import KEY as HEALTH_KEY


VERSION = 'alpha_v11_observation_pump_v1'
KEY = 'v11-observation-pump'


class ObservationPump:
    def __init__(self, observation, runtime):
        if observation.store is not runtime.store:
            raise EvidenceError('OBSERVATION_PUMP_NAMESPACE_MISMATCH')
        self.observation, self.runtime, self.store = observation, runtime, runtime.store

    def _existing(self, key):
        try: return self.store.get(key)
        except EvidenceError as exc:
            if str(exc) == 'EVIDENCE_MISSING': return None
            raise

    async def cycle(self, cycle_id, requests, *, station_by_event, strategies, required_providers_by_strategy=None):
        identity(cycle_id, maximum=80)
        if type(requests) is not tuple or not 1 <= len(requests) <= 16:
            raise EvidenceError('OBSERVATION_PUMP_REQUEST_BOUND')
        request_sha = digest(dict(cycle_id=cycle_id, requests=[asdict(r) for r in requests],
            station_by_event=station_by_event, strategies=strategies, required_providers_by_strategy=required_providers_by_strategy,
            runtime_config=self.runtime.config))
        key = 'pump:'+digest(cycle_id); prior=self._existing(key)
        if prior:
            if prior['body']['details']['request_sha256'] != request_sha: raise EvidenceError('OBSERVATION_PUMP_REPLAY_CONFLICT')
            return prior
        fd=os.open(self.store.path.with_name(self.store.path.name+'.pump.lock'),os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW,0o600)
        try:
            try: fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise EvidenceError('OBSERVATION_PUMP_ALREADY_RUNNING') from None
            before=self.runtime.tick('pump-before:'+digest(cycle_id))
            health=self.store.latest(kind='RUNTIME_STATUS',event_id=HEALTH_KEY)
            begin=self._existing(key+':begin'); observation_status=None; error=None; collected=False
            if begin and begin['body']['details']['request_sha256'] != request_sha:
                raise EvidenceError('OBSERVATION_PUMP_REPLAY_CONFLICT')
            if health is None or health['body']['details']['clock_reasons']:
                outcome='COLLECTION_DEFERRED_CLOCK'
            elif begin:
                # Successful raw receipts remain durable and the feed can resume.
                # A new cycle ID must respect the persisted provider cooldowns.
                saved=self._existing('obs:'+digest(cycle_id)+':status')
                observation_status=saved['body']['details'] if saved else None
                outcome='RECORDED_OBSERVATION_RECOVERED' if saved else 'INTERRUPTED_COLLECTION_NOT_RETRIED'
            else:
                self.store.audit(key+':begin',event_id=KEY,kind='RUNTIME_STATUS',details=dict(
                    version=VERSION,request_sha256=request_sha,outcome='COLLECTION_STARTED',financial_authority=False))
                try:
                    observation_status=await self.observation.cycle('obs:'+digest(cycle_id),requests,
                        station_by_event=station_by_event,strategies=strategies,
                        required_providers_by_strategy=required_providers_by_strategy)
                    collected=True;outcome='OBSERVATION_CYCLE_RECORDED'
                except (EvidenceError, OSError, TimeoutError) as exc:
                    outcome='OBSERVATION_CYCLE_INTERRUPTED'
                    error=str(exc) if isinstance(exc,EvidenceError) else type(exc).__name__
            after=self.runtime.tick('pump-after:'+digest(cycle_id))
            refs=(before['id'],after['id'])
            status_id='obs:'+digest(cycle_id)+':status'
            if self._existing(status_id):refs+=(status_id,)
            return self.store.safety_audit(key,event_id=KEY,kind='RUNTIME_STATUS',evidence_ids=refs,details=dict(
                version=VERSION,request_sha256=request_sha,outcome=outcome,error=error,
                before_runtime_id=before['id'],after_runtime_id=after['id'],observation=observation_status,
                collection_attempted=bool(begin) or collected or error is not None,
                financial_authority=False,public_get_only=True,forward_or_live_acceptance=False))
        finally:os.close(fd)
