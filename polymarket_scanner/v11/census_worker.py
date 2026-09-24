"""Bounded fresh public census, separate from the paper cancellation scheduler.

The worker collects books, the official METAR proxy and explicitly required PWS
quality inputs. Missing forecast adapters remain explicit gates. Coverage cannot approve a strategy,
renew a calibration, place an order, or synthesize a fill.
"""
import asyncio
from copy import deepcopy
from dataclasses import asdict, dataclass
import fcntl
import os
import time

from .book_inputs import BookPolicy, book_request, normalize_book_capture
from .collection import SourceRequest
from .evidence import EvidenceError, digest, finite, identity
from .rules import RuleFingerprint
from .weather_sources import normalize_weather_capture, madis_request
from .pws_quality import archive_neighborhood
from .pws_runtime import PWSQualityPlan, PWSQualitySettings, PWSQualityWorker
from .gefs_runtime import GEFSWorker
from .gefs_schedule import requested_plan
from .gefs_sources import assemble_path
from .model_census import ModelCensusStage, restore_plan


VERSION = 'alpha_v11_census_worker_v1'
KEY = 'v11-public-census-worker'


@dataclass(frozen=True)
class CensusPlan:
    rule: RuleFingerprint
    collateral_asset: str
    official_metar_proxy: bool = True
    pws: PWSQualityPlan | None = None

    def __post_init__(self):
        if not isinstance(self.rule, RuleFingerprint) or type(self.official_metar_proxy) is not bool:
            raise EvidenceError('CENSUS_PLAN_INVALID')
        identity(self.collateral_asset)
        if self.pws is not None and (not isinstance(self.pws,PWSQualityPlan)
                or self.pws.event_id!=self.rule.payload['event_id']
                or self.pws.official.station!=self.rule.payload['station']
                or self.pws.official.fingerprint!=self.rule.payload['metadata_fingerprint']):
            raise EvidenceError('CENSUS_PWS_PLAN_RULE_SCOPE')


@dataclass(frozen=True)
class CensusPolicy:
    version: str
    maximum_seconds: float = 20.
    retry_seconds: float = 30.
    maximum_requests: int = 63

    def __post_init__(self):
        identity(self.version)
        if (not 0 < finite(self.maximum_seconds) <= 60 or not 1 <= finite(self.retry_seconds) <= 300
                or type(self.maximum_requests) is not int or not 1 <= self.maximum_requests <= 63):
            raise EvidenceError('CENSUS_WORKER_POLICY_BOUND')


class CensusWorker:
    def __init__(self, scheduled, queue, health, *, plans, policy, book_policy, sleeper=asyncio.sleep,gefs=None):
        if scheduled.store is not queue.store or health.store is not queue.store:
            raise EvidenceError('CENSUS_WORKER_NAMESPACE_MISMATCH')
        if (not isinstance(policy, CensusPolicy) or not isinstance(book_policy, BookPolicy)
                or type(plans) is not tuple or not 1 <= len(plans) <= 32
                or any(not isinstance(p, CensusPlan) for p in plans)):
            raise EvidenceError('CENSUS_WORKER_CONFIGURATION_INVALID')
        self.scheduled, self.queue, self.health, self.store = scheduled, queue, health, queue.store
        self.policy, self.book_policy, self.sleeper = policy, book_policy, sleeper
        if gefs is not None and (not isinstance(gefs,GEFSWorker) or gefs.scheduled is not scheduled or gefs.health is not health):
            raise EvidenceError('CENSUS_GEFS_SHARED_COLLECTOR_REQUIRED')
        self.gefs=gefs;self.model_stage=ModelCensusStage(queue,scheduled) if gefs else None
        self.plans = {p.rule.payload['event_id']:p for p in plans}
        if len(self.plans) != len(plans) or not self.plans.keys() <= queue.routes.keys():
            raise EvidenceError('CENSUS_PLAN_EVENT_SCOPE')
        for event, plan in self.plans.items():
            route = queue.routes[event]; p = plan.rule.payload
            tokens = {b[side+'_token'] for b in p['partition'] for side in ('yes', 'no')}
            if (plan.rule.sha256 != route.rule_fingerprint or set(route.tokens) != tokens
                    or (p['station'], p['target_date'], p['family']) != (route.station, route.target_date, route.family)):
                raise EvidenceError('CENSUS_PLAN_RULE_ROUTE_MISMATCH')
        self.config = digest(dict(queue=queue.config, health=health.config, policy=asdict(policy),
            book_policy=asdict(book_policy), plans=[{k:v for k,v in asdict(p).items() if k!='pws' or v is not None}
                for p in sorted(plans, key=lambda p:p.rule.sha256)]))
        if gefs is not None:
            if any(e not in self.plans or p.rule!=self.plans[e].rule for e,p in gefs.plans.items()):
                raise EvidenceError('CENSUS_GEFS_PLAN_RULE_SCOPE')
            self.config=digest(dict(base=self.config,gefs=gefs.config))

    def _get(self, key):
        try:
            return self.store.get(key)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING':
                raise
        return None

    def _head(self):
        row = self.store.latest(kind='RUNTIME_STATUS', event_id=KEY)
        if row and row['body']['details'].get('config_sha256') != self.config:
            raise EvidenceError('CENSUS_WORKER_CONFIG_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self, key, state, **details):
        head = self._head()
        return self.store.safety_audit(key, event_id=KEY, kind='RUNTIME_STATUS', details=dict(
            version=VERSION, config_sha256=self.config, state=state, **details,
            financial_authority=False, real_orders_sent=False, deployment_acceptance=False),
            expected_previous_seq=head['seq'] if head else 0)

    def _requests(self, route, plan, revision):
        supplied = {'OFFICIAL_OBSERVATION'} if plan.official_metar_proxy else set()
        if plan.pws is not None:supplied.add('PWS_OBSERVATION')
        if self.gefs is not None and route.event_id in self.gefs.plans:supplied.add('MODEL')
        if not set(route.required_source_kinds) <= supplied:
            raise EvidenceError('CENSUS_REQUIRED_SOURCE_ADAPTER_UNAVAILABLE')
        requests = []
        if plan.official_metar_proxy:
            requests.append(SourceRequest('NOAA_AWC', 'https://aviationweather.gov/api/data/metar',
                route.event_id, 'OFFICIAL_OBSERVATION', route.station, revision,
                (('ids', route.station), ('format', 'json'))))
        if 'PWS_OBSERVATION' in route.required_source_kinds:
            p=plan.pws.official
            requests.append(madis_request(event_id=route.event_id,station=p.station,latitude=p.latitude,longitude=p.longitude))
        requests.extend(book_request(event_id=route.event_id, token_id=t, revision=revision) for t in sorted(route.tokens))
        if len(requests) > self.policy.maximum_requests:
            raise EvidenceError('CENSUS_REQUEST_PLAN_BOUND')
        return tuple(requests)

    async def step(self, cycle_id):
        identity(cycle_id, maximum=80)
        key = 'census-step:'+digest(cycle_id)
        prior = self._get(key)
        if prior:
            if prior['body']['details'].get('config_sha256') != self.config:
                raise EvidenceError('CENSUS_WORKER_REPLAY_CONFIG')
            return prior
        fd = os.open(self.store.path.with_name(self.store.path.name+'.census.lock'),
                     os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW, 0o600)
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:
                raise EvidenceError('CENSUS_WORKER_ALREADY_RUNNING') from None
            head = self._head()
            state = deepcopy(head['body']['details']['state']) if head else dict(last_event='', retry_at={})
            if self._get(key+':begin'):
                # Never repeat an interrupted GET cycle under the same identity.
                # Any captured responses remain in the archive for inspection.
                return self._save(key, state, outcome='INTERRUPTED_COLLECTION_NOT_RETRIED')
            health = self.health.sample(key+':clock')
            if health['body']['details']['clock_reasons']:
                return self._save(key, state, outcome='DEFERRED_CLOCK_UNHEALTHY')
            snapshot = self.queue.snapshot(); needed = set(snapshot['needs_census'])
            if snapshot['active']:
                needed.add(snapshot['active']['event_id'])  # Recover an abandoned claim through a fresh census.
            now = finite(self.store.clock())
            eligible = sorted(e for e in needed & self.plans.keys()
                              if self.queue.routes[e].valid_until > now and state['retry_at'].get(e, 0) <= now)
            ordered = [e for e in eligible if e > state['last_event']]+[e for e in eligible if e <= state['last_event']]
            if not ordered:
                return self._save(key, state, outcome='IDLE_OR_COOLDOWN', unconfigured_events=sorted(needed-self.plans.keys()))
            event = ordered[0]; state['last_event'] = event
            model_preparation_id=None
            if ('MODEL' in self.queue.routes[event].required_source_kinds and self.gefs is not None
                    and event in self.gefs.plans and snapshot['active'] is None):
                try:
                    if event not in self.queue.preparing_model_events():
                        gh=self.gefs._head();ga=gh['body']['details']['state']['active'] if gh else None
                        if ga is not None and ga['event_id']==event:
                            recovered=await self.gefs.step('census-recover:'+digest(key))
                            return self._save(key,state,outcome='PRIOR_GEFS_OPERATION_RECONCILED',event_id=event,gefs_step_id=recovered['id'])
                        plan=self.gefs.plans[event]
                        if self.gefs.rollover is not None:plan=requested_plan(plan,self.gefs.rollover,now=now)
                        expiry=min(now+1800,self.queue.routes[event].valid_until,
                            now+self.queue.policy.max_pending_age_seconds,now+plan.forecast.maximum_receipt_age_seconds)
                        prep=self.queue.begin_model_census(key+':model-epoch',plan=plan,expires_at=expiry)
                        model_preparation_id=prep['id']
                    else:
                        model_preparation_id=self.queue.snapshot()['model_preparations'][event]['id']
                    _,prepared=self.queue.model_preparation(model_preparation_id,event_id=event)
                    plan=restore_plan(prepared);fields=self.model_stage.fields(prepared)
                    if len(fields)!=31*len(plan.hours):
                        progress=await self.model_stage.step(key,preparation_id=model_preparation_id,event_id=event)
                        state['retry_at'][event]=now+1.
                        return self._save(key,state,outcome='MODEL_CENSUS_COLLECTION_PENDING',event_id=event,
                            preparation_id=model_preparation_id,model_stage_id=progress['id'],
                            model_stage_outcome=progress['body']['details']['outcome'])
                except (EvidenceError,OSError,TimeoutError) as exc:
                    state['retry_at'][event]=now+self.policy.retry_seconds
                    return self._save(key,state,outcome='MODEL_CENSUS_GATED',event_id=event,
                        reason=str(exc) if isinstance(exc,EvidenceError) else type(exc).__name__)
            state['retry_at'][event] = now+self.policy.retry_seconds
            self._save(key+':begin', state, outcome='COLLECTION_STARTED', event_id=event)
            try:
                with self.queue.work(key+':claim', exclude_events=tuple(sorted(self.queue.routes.keys()-{event}))) as claim:
                    if claim is None:
                        return self._save(key, state, outcome='NO_CURRENT_CLAIM', event_id=event)
                    outcome = await self._collect(key, claim, self.plans[event],model_preparation_id=model_preparation_id)
                    if outcome['outcome'] == 'CENSUS_SOURCE_COVERAGE_ONLY':
                        state['retry_at'].pop(event, None)
                    return self._save(key, state, event_id=event, **outcome)
            except (EvidenceError, OSError, TimeoutError) as exc:
                # A fault leaves the durable claim/census need intact. The next
                # distinct cycle recovers it; no source, intent or risk is erased.
                return self._save(key, state, outcome='CENSUS_INTERRUPTED', event_id=event,
                                  reason=str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__)
        finally:
            os.close(fd)

    async def _collect(self, key, claim, plan,*,model_preparation_id=None):
        route = self.queue.routes[claim['event_id']]
        books, sources, errors, collections = [], [], [], []
        required_pws='PWS_OBSERVATION' in route.required_source_kinds
        coverage_id = completion_id = None
        started = time.monotonic()
        budget = min(self.policy.maximum_seconds, self.queue.policy.max_work_seconds,
                     max(0, claim['deadline']-self.store.clock()))
        try:
            requests = self._requests(route, plan, key)
            if 'MODEL' in route.required_source_kinds and model_preparation_id is None:
                raise EvidenceError('CENSUS_MODEL_COLLECTION_PENDING')
            async with asyncio.timeout(budget):
                for offset in range(0, len(requests), 16):
                    # Additional batches respect the same persisted CLOB quota
                    # as other collectors. No bypass of a 429 or recovery delay.
                    if offset:
                        schedule = self.store.latest(kind='SOURCE_SCHEDULE', event_id='source-host:clob.polymarket.com')
                        delay = max(0, schedule['body']['details']['next_allowed_at']-self.store.clock()) if schedule else 0
                        if delay >= budget-(time.monotonic()-started):
                            raise EvidenceError('CENSUS_PROVIDER_COOLDOWN_EXCEEDS_BUDGET')
                        if delay:
                            await self.sleeper(delay)
                    collected = await self.scheduled.cycle('census-get:'+digest([key, offset]), requests[offset:offset+16])
                    collections.append(dict(cycle_id=collected['cycle_id'],
                        states=[x['state'] for x in collected['sources']], omitted=len(collected['omitted'])))
                    for source in collected['sources']:
                        if source['state'] != 'SUCCESS':
                            errors.append(source['state']); continue
                        raw_id = source['capture_ids'][0]; raw = self.store.get(raw_id)
                        try:
                            if raw['kind'] == 'BOOK':
                                normalized = normalize_book_capture(self.store, raw_id, rule=plan.rule,
                                    token_id=raw['body']['source_identity'], collateral_asset=plan.collateral_asset, policy=self.book_policy)
                                books.append(normalized['id'])
                            else:
                                normalized = normalize_weather_capture(self.store, raw_id,
                                    record_id='census-weather:'+digest(raw_id), station=route.station,
                                    official_max_age_seconds=dict(self.queue.policy.source_age_seconds)['OFFICIAL_OBSERVATION'])
                                if raw['kind']!='PWS_OBSERVATION':sources.append(normalized['id'])
                        except EvidenceError as exc:
                            errors.append(str(exc))
                    if collected['omitted']:
                        errors.append('CENSUS_PROVIDER_COOLDOWN')
                    if errors:
                        break
                if errors:
                    raise EvidenceError('CENSUS_INCOMPLETE_SOURCE_COVERAGE')
                if required_pws:
                    # A separate optional PWS worker may run between censuses.
                    # Recovery still requires a newly collected response under
                    # this claim, checked by complete_census through raw lineage.
                    selector=PWSQualityWorker(self.store,self.health,PWSQualitySettings((plan.pws,)))
                    selected=selector.current_inputs(route.event_id)
                    quality=archive_neighborhood(self.store,'census-pws:'+digest(key),event_id=route.event_id,
                        capture_ids=tuple(selected['capture_ids']),official=plan.pws.official,policy=plan.pws.policy,
                        expected_source_seq=selected['source_seq'],deadline=min(started+budget,time.monotonic()+2.))
                    sources.append(quality['id'])
                if model_preparation_id is not None:
                    _,prepared=self.queue.model_preparation(model_preparation_id,event_id=route.event_id)
                    model=assemble_path(self.store,plan=restore_plan(prepared),field_ids=self.model_stage.fields(prepared),
                        record_id='census-model:'+digest(key),deadline=min(started+budget,time.monotonic()+2.))
                    sources.append(model['id'])
                health = self.health.sample(key+':post-collection-clock')
                if health['body']['details']['clock_reasons']:
                    raise EvidenceError('CENSUS_CLOCK_CHANGED_DURING_COLLECTION')
                guard = self.store.latest(kind='RULE_STATE', event_id=route.event_id)
                if guard is None:
                    raise EvidenceError('CENSUS_CURRENT_RULE_REQUIRED')
                coverage = self.queue.complete_census(key+':coverage', claim_id=claim['claim_id'],
                    book_ids=tuple(books), source_ids=tuple(sources), rule_state_id=guard['id'],model_preparation_id=model_preparation_id)
                coverage_id = coverage['id']
        except (EvidenceError, TimeoutError) as exc:
            errors.append(str(exc) if isinstance(exc, EvidenceError) else 'CENSUS_COLLECTION_DEADLINE')
        # This is a coverage result, not a financial valuation. Admission still
        # requires the ordinary evaluator's exact post-census proposal IDs.
        status = self.store.audit(key+':coverage-result', event_id=route.event_id, kind='RUNTIME_STATUS',
            details=dict(version=VERSION, outcome='CENSUS_SOURCE_COVERAGE_ONLY' if coverage_id else 'GATED',
                         errors=errors, coverage_id=coverage_id, financial_authority=False))
        completion = self.queue.finish(key+':complete', claim_id=claim['claim_id'], result_ids=(status['id'],))
        completion_id = completion['id']
        coverage_current = coverage_id is not None and route.event_id not in self.queue.snapshot()['needs_census']
        if coverage_current:
            # One existing receipt requests ordinary reevaluation; it grants no
            # trading permission and does not erase concurrent notifications.
            self.queue.publish(key+':reevaluate', kind='BOOK', evidence_id=books[-1])
        if coverage_id and not coverage_current:
            errors.append('CENSUS_COVERAGE_INVALIDATED_BEFORE_COMPLETION')
        return dict(outcome='CENSUS_SOURCE_COVERAGE_ONLY' if coverage_current else 'GATED', errors=errors,
                    book_ids=books, source_ids=sources, coverage_id=coverage_id, completion_id=completion_id,
                    completion_outcome=completion['body']['details']['result']['outcome'],
                    collections=collections, elapsed_seconds=time.monotonic()-started)
