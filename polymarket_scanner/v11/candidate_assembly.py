"""Compose a bounded nonfinancial candidate from typed, explicitly scoped plans.

Construction is not deployment, source certification, model approval or runtime
acceptance. This module has no service control, secrets, wallet or order transport.
It uses the ordinary host clock probe and existing anonymous public collector.
"""
from dataclasses import asdict, dataclass

from .audit_reports import AuditPolicy, AuditScheduler, AuditWorker
from .book_inputs import BookPolicy
from .candidate_runner import CandidatePolicy, CandidateRunner, ObservationBatch
from .census_worker import CensusPlan, CensusPolicy, CensusWorker
from .collection import PublicCollector
from .discovery import DiscoveryPolicy, MarketDiscovery
from .event_queue import EventRoute, EventQueue, TriggerPolicy
from .evidence import EvidenceError, digest, finite, identity
from .observation_pump import ObservationPump
from .observation_runtime import ObservationRuntime, ScheduledCollector
from .paper_coordinator import PaperAccountPolicy, PaperCoordinator
from .paper_runtime import PaperRuntime, RuntimePolicy, TemperatureEventAdapter
from .reaction_runtime import PWSLeadEventAdapter, SourceReleaseEventAdapter, PositionExitEventAdapter
from .request_assembly import (ScopeInputs, TargetPlan, SourceSelector, RequestAssembler, EntryRequestFactory,
    RelativeValueRequestFactory, PWSRequestFactory, SourceReleaseRequestFactory, ExitRequestFactory)
from .risk_inputs import EventRiskInputs, RiskInputPolicy, RiskAwareEventAdapter
from .runtime_feed import FeedPolicy
from .runtime_health import HealthPolicy, RuntimeHealth, SourceNeed
from .scenario_risk import CorrelationMap, ScenarioLimits
from .strategy_admission import ROLES
from .strategy_runtime import MultiStrategyEventAdapter, RelativeValueEventAdapter
from .basket_valuation import BasketPolicy
from .pws_lead import LeadPolicy
from .pws_runtime import PWSQualitySettings, PWSQualityWorker
from .forecast_sources import ForecastPlan
from .forecast_runtime import ForecastNormalizationWorker
from .gefs_sources import GEFSPlan
from .gefs_runtime import GEFSWorker
from .maker_research import MakerResearch, MakerResearchPolicy
from .maker_telemetry import MakerTelemetryPolicy, MakerTelemetryWorker
from .maker_runtime import MakerTarget, MakerRequestFactory, MakerEventAdapter
from .microstructure import MicrostructurePolicy
from .valuation import CostComponent, ValuationPolicy


@dataclass(frozen=True)
class TemperatureLane:
    name: str
    inputs: ScopeInputs
    targets: tuple[TargetPlan, ...]
    book_provider: str
    valuation: ValuationPolicy
    lifetime_seconds: float

    def __post_init__(self):
        identity(self.name); identity(self.book_provider)
        if (not isinstance(self.inputs,ScopeInputs) or not isinstance(self.valuation,ValuationPolicy)
                or not 0 < finite(self.lifetime_seconds) <= 60
                or type(self.targets) is not tuple or not 1 <= len(self.targets) <= (32 if type(self) is RelativeValueLane else 6)
                or any(not isinstance(t,TargetPlan) for t in self.targets)):
            raise EvidenceError('CANDIDATE_LANE_PLAN_BOUND')


@dataclass(frozen=True)
class RelativeValueLane(TemperatureLane):
    basket_policy: BasketPolicy
    maximum_proposals: int = 1

    def __post_init__(self):
        super().__post_init__()
        if (not isinstance(self.basket_policy,BasketPolicy) or type(self.maximum_proposals) is not int
                or not 1 <= self.maximum_proposals <= 6):
            raise EvidenceError('CANDIDATE_RELATIVE_PLAN_BOUND')


@dataclass(frozen=True)
class PWSLeadLane(TemperatureLane):
    observation_inputs: ScopeInputs
    without_pws_sources: tuple[SourceSelector, ...]
    lead_policy: LeadPolicy

    def __post_init__(self):
        super().__post_init__()
        if (not isinstance(self.observation_inputs,ScopeInputs) or not isinstance(self.lead_policy,LeadPolicy)
                or type(self.without_pws_sources) is not tuple or not 1 <= len(self.without_pws_sources) <= 16
                or any(not isinstance(s,SourceSelector) or s.role!='MODEL' for s in self.without_pws_sources)):
            raise EvidenceError('CANDIDATE_PWS_PLAN_BOUND')


@dataclass(frozen=True)
class SourceReleaseLane(TemperatureLane):
    pass


@dataclass(frozen=True)
class ExitLane(TemperatureLane):
    hold_costs: tuple[CostComponent, ...]
    sale_costs: tuple[CostComponent, ...]
    reason: str


@dataclass(frozen=True)
class MakerLane:
    name: str
    inputs: ScopeInputs
    targets: tuple[MakerTarget, ...]
    book_provider: str
    valuation: ValuationPolicy
    lifetime_seconds: float
    microstructure: MicrostructurePolicy
    payout_inputs: ScopeInputs | None = None

    def __post_init__(self):
        identity(self.name);identity(self.book_provider)
        if (not isinstance(self.inputs,ScopeInputs) or self.inputs.scope.strategy!='MAKER_RESEARCH'
                or not isinstance(self.valuation,ValuationPolicy) or not isinstance(self.microstructure,MicrostructurePolicy)
                or not 0<finite(self.lifetime_seconds)<=60 or type(self.targets) is not tuple or not 1<=len(self.targets)<=6
                or any(not isinstance(t,MakerTarget) for t in self.targets)
                or self.payout_inputs is not None and not isinstance(self.payout_inputs,ScopeInputs)):
            raise EvidenceError('CANDIDATE_MAKER_LANE_PLAN_BOUND')


LANES = (TemperatureLane,RelativeValueLane,PWSLeadLane,SourceReleaseLane,ExitLane,MakerLane)


@dataclass(frozen=True)
class CandidateEvent:
    route: EventRoute
    census: CensusPlan
    risk_inputs: ScopeInputs
    risk_book_provider: str
    risk_valuation: ValuationPolicy
    risk_policy: RiskInputPolicy
    lanes: tuple[TemperatureLane | MakerLane, ...]

    def __post_init__(self):
        identity(self.risk_book_provider)
        if (not isinstance(self.route,EventRoute) or not isinstance(self.census,CensusPlan)
                or not isinstance(self.risk_inputs,ScopeInputs) or not isinstance(self.risk_policy,RiskInputPolicy)
                or not isinstance(self.risk_valuation,ValuationPolicy)
                or type(self.lanes) is not tuple or not 1 <= len(self.lanes) <= 6
                or any(type(l) not in LANES for l in self.lanes)
                or len({l.name for l in self.lanes}) != len(self.lanes)):
            raise EvidenceError('CANDIDATE_EVENT_PLAN_INVALID')
        if sum(l.maximum_proposals if type(l) is RelativeValueLane else len(l.targets) for l in self.lanes)>6:
            raise EvidenceError('CANDIDATE_AGGREGATE_PROPOSAL_BOUND')
        if (self.census.rule != self.risk_inputs.rule or self.route.event_id != self.risk_inputs.context.event_id
                or self.route.rule_fingerprint != self.risk_inputs.rule.sha256
                or self.risk_inputs.scope.strategy not in {l.inputs.scope.strategy for l in self.lanes}
                or any(l.inputs.rule != self.risk_inputs.rule or l.inputs.context != self.risk_inputs.context
                       or l.inputs.binding != self.risk_inputs.binding
                       or l.inputs.stage != self.risk_inputs.stage for l in self.lanes)):
            raise EvidenceError('CANDIDATE_COMMON_EVENT_SCOPE_MISMATCH')


@dataclass(frozen=True)
class MakerTelemetryPlan:
    research: MakerResearchPolicy
    telemetry: MakerTelemetryPolicy
    inputs: tuple[ScopeInputs, ...]

    def __post_init__(self):
        if (not isinstance(self.research,MakerResearchPolicy) or not isinstance(self.telemetry,MakerTelemetryPolicy)
                or type(self.inputs) is not tuple or not 1 <= len(self.inputs) <= 16
                or any(not isinstance(s,ScopeInputs) or s.scope.strategy!='MAKER_RESEARCH' for s in self.inputs)
                or len({s.context.event_id for s in self.inputs})!=len(self.inputs)):
            raise EvidenceError('CANDIDATE_MAKER_TELEMETRY_PLAN_BOUND')


@dataclass(frozen=True)
class CandidatePlan:
    version: str
    account: PaperAccountPolicy
    correlation: CorrelationMap
    limits: ScenarioLimits
    events: tuple[CandidateEvent, ...]
    trigger: TriggerPolicy
    health: HealthPolicy
    runtime: RuntimePolicy
    candidate: CandidatePolicy
    census: CensusPolicy
    books: BookPolicy
    discovery: DiscoveryPolicy
    audits: AuditPolicy
    feed: FeedPolicy
    worker_id: str
    observation: ObservationBatch | None = None
    maker: MakerTelemetryPlan | None = None
    pws_quality: PWSQualitySettings | None = None
    forecasts: tuple[ForecastPlan, ...] = ()
    gefs: tuple[GEFSPlan, ...] = ()

    def __post_init__(self):
        identity(self.version); identity(self.worker_id)
        for value,kind in ((self.account,PaperAccountPolicy),(self.correlation,CorrelationMap),(self.limits,ScenarioLimits),
            (self.trigger,TriggerPolicy),(self.health,HealthPolicy),(self.runtime,RuntimePolicy),(self.candidate,CandidatePolicy),
            (self.census,CensusPolicy),(self.books,BookPolicy),(self.discovery,DiscoveryPolicy),(self.audits,AuditPolicy),(self.feed,FeedPolicy)):
            if not isinstance(value,kind): raise EvidenceError('CANDIDATE_TYPED_POLICY_REQUIRED')
        if (type(self.events) is not tuple or not 1 <= len(self.events) <= 16
                or any(not isinstance(e,CandidateEvent) for e in self.events)
                or len({e.route.event_id for e in self.events}) != len(self.events)
                or self.worker_id not in self.health.workers
                or any(e.risk_inputs.context.account_id != self.account.account_id for e in self.events)
                or self.observation is not None and not isinstance(self.observation,ObservationBatch)):
            raise EvidenceError('CANDIDATE_PLAN_SCOPE_BOUND')
        if self.maker is not None:
            if not isinstance(self.maker,MakerTelemetryPlan):raise EvidenceError('CANDIDATE_MAKER_PLAN_REQUIRED')
            events={e.route.event_id:e for e in self.events}
            for s in self.maker.inputs:
                e=events.get(s.context.event_id)
                if e is None or (s.context,s.rule,s.binding,s.stage)!=(e.risk_inputs.context,e.risk_inputs.rule,e.risk_inputs.binding,e.risk_inputs.stage):
                    raise EvidenceError('CANDIDATE_MAKER_PLAN_SCOPE')
        maker_inputs={s.context.event_id:s for s in self.maker.inputs} if self.maker else {}
        if any(type(l) is MakerLane and maker_inputs.get(e.route.event_id)!=l.inputs for e in self.events for l in e.lanes):
            raise EvidenceError('CANDIDATE_MAKER_LANE_REQUIRES_SHARED_TELEMETRY')
        if self.observation is not None:
            routes={e.route.event_id:e.route for e in self.events}
            if (not {r.event_id for r in self.observation.requests} <= routes.keys()
                    or any(e not in routes or routes[e].station!=station for e,station in self.observation.station_by_event)):
                raise EvidenceError('CANDIDATE_OBSERVATION_ROUTE_MISMATCH')
        if self.pws_quality is not None:
            if not isinstance(self.pws_quality,PWSQualitySettings):raise EvidenceError('CANDIDATE_PWS_QUALITY_PLAN_REQUIRED')
            events={e.route.event_id:e for e in self.events}
            if any(p.event_id not in events or p.official.station!=events[p.event_id].route.station
                    or p.official.fingerprint!=events[p.event_id].census.rule.payload['metadata_fingerprint']
                    or events[p.event_id].census.pws is not None and events[p.event_id].census.pws!=p
                    for p in self.pws_quality.plans):
                raise EvidenceError('CANDIDATE_PWS_QUALITY_SCOPE')
        events={e.route.event_id:e for e in self.events}
        if (type(self.forecasts) is not tuple or len(self.forecasts)>16
                or any(not isinstance(p,ForecastPlan) for p in self.forecasts)
                or len({p.event_id for p in self.forecasts})!=len(self.forecasts)
                or any(p.event_id not in events or p.rule!=events[p.event_id].census.rule for p in self.forecasts)):
            raise EvidenceError('CANDIDATE_FORECAST_SCOPE')
        if (type(self.gefs) is not tuple or len(self.gefs)>16 or any(not isinstance(p,GEFSPlan) for p in self.gefs)
                or len({p.event_id for p in self.gefs})!=len(self.gefs)
                or any(p.event_id not in events or p.rule!=events[p.event_id].census.rule for p in self.gefs)):
            raise EvidenceError('CANDIDATE_GEFS_SCOPE')


def _lane(queue,coordinator,lane,maker):
    a=RequestAssembler(queue,lane.inputs,book_provider=lane.book_provider,valuation_policy=lane.valuation,
                       lifetime_seconds=lane.lifetime_seconds)
    strategy=lane.inputs.scope.strategy
    if type(lane) is MakerLane:
        f=MakerRequestFactory(a,maker,lane.targets,microstructure=lane.microstructure,payout_inputs=lane.payout_inputs)
        return MakerEventAdapter(maker,f)
    if type(lane) is RelativeValueLane:
        if strategy not in {'CROSS_TEMP_RELATIVE_VALUE','STRUCTURAL'}: raise EvidenceError('CANDIDATE_RELATIVE_SCOPE_REQUIRED')
        f=RelativeValueRequestFactory(a,lane.targets,basket_policy=lane.basket_policy,maximum_proposals=lane.maximum_proposals)
        return RelativeValueEventAdapter(queue.store,f)
    if type(lane) is ExitLane:
        if strategy in {'PWS_OBSERVATION_LEAD','SOURCE_SHOCK','RELEASE_OPPORTUNITY'}:
            raise EvidenceError('CANDIDATE_SPECIAL_EXIT_JOIN_NOT_CONFIGURED')
        f=ExitRequestFactory(a,lane.targets,hold_costs=lane.hold_costs,sale_costs=lane.sale_costs,reason=lane.reason)
        return PositionExitEventAdapter(coordinator,f)
    entries=EntryRequestFactory(a,lane.targets)
    if type(lane) is PWSLeadLane:
        if strategy!='PWS_OBSERVATION_LEAD':raise EvidenceError('CANDIDATE_PWS_SCOPE_REQUIRED')
        return PWSLeadEventAdapter(queue.store,PWSRequestFactory(entries,observation_inputs=lane.observation_inputs,
            without_pws_sources=lane.without_pws_sources,lead_policy=lane.lead_policy))
    if type(lane) is SourceReleaseLane:
        if strategy not in {'SOURCE_SHOCK','RELEASE_OPPORTUNITY'}: raise EvidenceError('CANDIDATE_RELEASE_SCOPE_REQUIRED')
        return SourceReleaseEventAdapter(queue.store,SourceReleaseRequestFactory(entries))
    if strategy not in {'FUTURE_FORECAST','SAME_DAY_LATE_LOCK'}: raise EvidenceError('CANDIDATE_TEMPERATURE_SCOPE_REQUIRED')
    return TemperatureEventAdapter(queue.store,entries)


class _EventDispatch:
    def __init__(self,coordinator,adapters):
        self.coordinator,self.store,self.adapters=coordinator,coordinator.store,adapters
        self.config=digest({event:adapter.config for event,adapter in sorted(adapters.items())})

    def evaluate(self,claim,prefix):
        if claim['event_id'] not in self.adapters:raise EvidenceError('CANDIDATE_EVENT_NOT_CONFIGURED')
        return self.adapters[claim['event_id']].evaluate(claim,prefix)


def assemble_candidate(store,client,plan,*,generation):
    """Build one finite candidate; no collection, clock override or service action.

    The caller owns the anonymous HTTP client's lifetime. runner.run(run_id) is
    the existing bounded invocation and does not launch an autonomous service.
    All protected source/model and actual clock gates are evaluated at runtime.
    """
    if not isinstance(plan,CandidatePlan):raise EvidenceError('CANDIDATE_PLAN_REQUIRED')
    identity(generation)
    scheduled=ScheduledCollector(PublicCollector(store,client))
    coordinator=PaperCoordinator(store,policy=plan.account,correlation=plan.correlation,limits=plan.limits)
    # Refuse a conflicting existing account or queue; construction never replaces
    # their state to make a new configuration appear compatible.
    coordinator._head()
    maker=MakerResearch(coordinator,plan.maker.research) if plan.maker else None
    if maker is not None:maker._head()
    queue=EventQueue(store,routes=tuple(e.route for e in plan.events),policy=plan.trigger)
    queue.snapshot()
    scopes={}; needs={}; adapters={}; risk=[]
    maker_scopes={s.context.event_id:s for s in plan.maker.inputs} if plan.maker else {}
    for event in plan.events:
        eid=event.route.event_id
        scopes[eid]=tuple(sorted({l.inputs.scope.strategy for l in event.lanes}))
        inputs=[event.risk_inputs,*[l.inputs for l in event.lanes],
                *[l.observation_inputs for l in event.lanes if type(l) is PWSLeadLane],
                *[l.payout_inputs for l in event.lanes if type(l) is MakerLane and l.payout_inputs is not None]]
        scopes[eid]=tuple(sorted({*scopes[eid],*(s.scope.strategy for s in inputs)}))
        if eid in maker_scopes:
            scopes[eid]=tuple(sorted({*scopes[eid],'MAKER_RESEARCH'}));inputs.append(maker_scopes[eid])
        for context in inputs:
            if context.context.account_id!=plan.account.account_id or context.context.event_id!=eid:
                raise EvidenceError('CANDIDATE_HEALTH_SOURCE_SCOPE')
            for source in context.sources:
                kind=ROLES[source.role]
                if kind=='FEATURES':continue  # Feature/coverage leases remain in StrategyAdmission.
                key=(eid,context.scope.strategy,kind,source.provider,source.source_identity)
                need=SourceNeed(*key,source.maximum_age_seconds)
                if key not in needs or need.maximum_age_seconds<needs[key].maximum_age_seconds:needs[key]=need
        adapters[eid]=MultiStrategyEventAdapter(store,tuple((l.name,_lane(queue,coordinator,l,maker)) for l in event.lanes))
        a=RequestAssembler(queue,event.risk_inputs,book_provider=event.risk_book_provider,
            valuation_policy=event.risk_valuation,lifetime_seconds=plan.runtime.maximum_tick_seconds)
        risk.append(EventRiskInputs(a,coordinator,event.risk_policy))
    health=RuntimeHealth(store,plan.health,account_id=plan.account.account_id,scopes=scopes,sources=tuple(needs[k] for k in sorted(needs)))
    evaluator=RiskAwareEventAdapter(_EventDispatch(coordinator,adapters),tuple(risk))
    runtime=PaperRuntime(coordinator,queue,health,plan.runtime,evaluator=evaluator,worker_id=plan.worker_id,
        generation=generation,feed_policy=plan.feed,audits=AuditScheduler(store,plan.audits),maker=maker)
    runtime._head()
    census=CensusWorker(scheduled,queue,health,plans=tuple(e.census for e in plan.events),policy=plan.census,book_policy=plan.books)
    observation=ObservationPump(ObservationRuntime(scheduled),runtime) if plan.observation is not None else None
    runner=CandidateRunner(runtime,plan.candidate,census=census,discovery=MarketDiscovery(scheduled,health,plan.discovery),
        audits=AuditWorker(coordinator,plan.audits),observation=observation,observation_batch=plan.observation,
        maker_telemetry=MakerTelemetryWorker(maker,health,plan.maker.telemetry,event_ids=tuple(maker_scopes)) if maker else None,
        pws_quality=PWSQualityWorker(store,health,plan.pws_quality) if plan.pws_quality else None,
        forecasts=ForecastNormalizationWorker(store,health,plan.forecasts) if plan.forecasts else None,
        gefs=GEFSWorker(scheduled,health,plan.gefs) if plan.gefs else None)
    runner.assembly_sha256=digest(asdict(plan))
    return runner
