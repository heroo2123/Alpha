"""Repo-side KATL economic CandidatePlan builder (req8/req9 nonfinancial unblock).

The host's build_katl_decision_plan.py and continuous-shadow-v2 daily modules
assemble smoke-only plans. Their short freshness windows and/or long census
cadence prevent ordinary current-input evaluations and none configures a
PWS_OBSERVATION_LEAD sleeve.

This module builds the same event/scope/route shape with:
  - freshness/revalidation windows set against MicrostructurePolicy's own
    <=120 second ceiling on book age (microstructure.py's hard upper bound,
    not a tunable smoke value) instead of an arbitrary smoke number;
  - a higher per-run job budget and shorter census interval so the
    asynchronous CensusWorker has a realistic chance of keeping book/model
    coverage under that 120-second ceiling before a safety tick claims an
    event that still needs it;
  - an explicit `pws` sleeve (CensusPlan.pws, CandidatePlan.pws_quality and a
    PWSLeadLane) wired through the existing PWSRequestFactory/PWSLeadEvent
    Adapter/PWSObservationLead admission path. Without the reviewed PWS pair
    and causal exact-source evidence described in the handoff, it stays gated.

This module makes no network, database or service call. It only assembles
typed, self-validating dataclasses; the real admission/risk/scenario-
reservation code (candidate_assembly.assemble_candidate and everything it
calls) still refuses any input that is not actually fresh, certified and
reviewed. financial_authority stays False throughout -- nothing here places
an order, mutates a real account, or gives PWS settlement authority. The
default settlement-window policy derives an observation close only for a
supported, fresh, RuleGuard-bound rule. Execution health and default cost
coverage remain UNKNOWN/GATED. Explicit None retains the original fail-closed
settlement-window fallback.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .audit_reports import AuditPolicy
from .book_inputs import BookPolicy, PROVIDER as BOOK_PROVIDER
from .candidate_assembly import CandidateEvent, CandidatePlan, PWSLeadLane, TemperatureLane
from .candidate_runner import CandidatePolicy
from .census_worker import CensusPlan, CensusPolicy
from .certification import CapabilityScope, StationMetadata
from .discovery import DiscoveryPolicy
from .event_queue import EventRoute, TriggerPolicy, KINDS
from .event_risk import EventContext, EventPolicy, StateGuard
from .evidence import EvidenceError, ReleaseBinding, digest
from .forecast_features import ForecastFeatureContract
from .gefs_schedule import GEFSRunPolicy
from .gefs_sources import GEFSPlan
from .microstructure import MicrostructurePolicy
from .paper_coordinator import PaperAccountPolicy
from .paper_risk_observation import ObservationPolicy
from .paper_runtime import RuntimePolicy
from .physical_inference import PhysicalFeatureContract
from .pws_lead import LeadPolicy
from .pws_quality import PWSPolicy
from .pws_runtime import PWSQualityPlan, PWSQualitySettings
from .request_assembly import ScopeInputs, SourceSelector, TargetPlan
from .shadow_commission import PinnedFeatureContract, ShadowScopeTarget
from .risk_inputs import RiskInputPolicy
from .rules import RuleFingerprint
from .runtime_feed import FeedPolicy
from .runtime_health import HealthPolicy
from .scenario_risk import CorrelationMap, ScenarioLimits
from .settlement_window import SettlementWindowPolicy, VERSION as SETTLEMENT_WINDOW_VERSION
from .valuation import CostComponent, ValuationPolicy


VERSION = 'katl-v11-economic-plan-v2'

# MicrostructurePolicy.maximum_book_age_seconds is hard-capped at 120 by that
# dataclass's own __post_init__ (not a smoke choice this module can relax).
# Every other book/event freshness window is set at or inside that ceiling so
# an admission never tolerates evidence staler than microstructure can use.
BOOK_FRESHNESS_SECONDS = 110.
MICROSTRUCTURE_HISTORY_SECONDS = 180.
CENSUS_INTERVAL_SECONDS = 90.

# Match the independently reviewed Oct-11 economic template and this event's
# ScopeInputs rule-age bound. This policy does not establish settlement finality.
DEFAULT_SETTLEMENT_WINDOW_POLICY = SettlementWindowPolicy(SETTLEMENT_WINDOW_VERSION, 86400.)


@dataclass(frozen=True)
class PWSSleeve:
    """Everything the PWS_OBSERVATION_LEAD sleeve needs beyond the base plan.

    `payout_scope`/`observation_scope` must each already carry a protected,
    reviewed CapabilityScope for strategy='PWS_OBSERVATION_LEAD' -- this
    module does not and cannot create that review; see the handoff doc.
    """
    official: StationMetadata
    quality_policy: PWSPolicy
    payout_scope: CapabilityScope
    observation_scope: CapabilityScope
    observation_bundle_sha256: str
    payout_sources: tuple[SourceSelector, ...]
    observation_sources: tuple[SourceSelector, ...]
    without_pws_sources: tuple[SourceSelector, ...]
    lead_policy: LeadPolicy
    rule_max_age_seconds: float = 3600.
    payout_costs: tuple[CostComponent, ...] = ()

    def __post_init__(self):
        if not isinstance(self.official, StationMetadata) or not isinstance(self.quality_policy, PWSPolicy):
            raise EvidenceError('KATL_PLAN_PWS_CONTEXT_REQUIRED')
        for scope in (self.payout_scope, self.observation_scope):
            if not isinstance(scope, CapabilityScope) or scope.strategy != 'PWS_OBSERVATION_LEAD':
                raise EvidenceError('KATL_PLAN_PWS_SCOPE_REQUIRED')
        if not isinstance(self.lead_policy, LeadPolicy):
            raise EvidenceError('KATL_PLAN_PWS_LEAD_POLICY_REQUIRED')
        if not isinstance(self.observation_bundle_sha256, str) or len(self.observation_bundle_sha256) != 64:
            raise EvidenceError('KATL_PLAN_PWS_OBSERVATION_BUNDLE_REQUIRED')
        for sources in (self.payout_sources, self.observation_sources):
            if any(sum(s.role == role for s in sources) != 1 for role in ('MODEL', 'OFFICIAL', 'PWS')):
                raise EvidenceError('KATL_PLAN_PWS_SOURCE_DEPENDENCIES_REQUIRED')
        if sum(s.role == 'FEATURES' for s in self.payout_sources) != 1:
            raise EvidenceError('KATL_PLAN_PWS_PAYOUT_FEATURES_REQUIRED')
        if any(s.role != 'MODEL' for s in self.without_pws_sources):
            raise EvidenceError('KATL_PLAN_PWS_ABLATION_MODEL_REQUIRED')
        if (type(self.payout_costs) is not tuple or len(self.payout_costs) > 16
                or any(not isinstance(c, CostComponent) for c in self.payout_costs)):
            raise EvidenceError('KATL_PLAN_PWS_COST_BOUND')


def pws_sleeve_from_config(value: dict, official: StationMetadata) -> PWSSleeve:
    """Decode an explicitly reviewed local config; no source or review is minted."""
    required = {'quality_policy', 'payout_scope', 'observation_scope',
                'observation_bundle_sha256', 'payout_sources', 'observation_sources',
                'without_pws_sources', 'lead_policy', 'rule_max_age_seconds', 'payout_costs'}
    if type(value) is not dict or not required <= set(value) or set(value) - required - {'commission', 'temperature_costs'}:
        raise EvidenceError('KATL_PLAN_PWS_CONFIG_SCHEMA')
    try:
        quality = dict(value['quality_policy'])
        quality['distance_bands'] = tuple(tuple(band) for band in quality['distance_bands'])
        return PWSSleeve(
            official=official, quality_policy=PWSPolicy(**quality),
            payout_scope=CapabilityScope(**value['payout_scope']),
            observation_scope=CapabilityScope(**value['observation_scope']),
            observation_bundle_sha256=value['observation_bundle_sha256'],
            payout_sources=tuple(SourceSelector(**s) for s in value['payout_sources']),
            observation_sources=tuple(SourceSelector(**s) for s in value['observation_sources']),
            without_pws_sources=tuple(SourceSelector(**s) for s in value['without_pws_sources']),
            lead_policy=LeadPolicy(**value['lead_policy']),
            rule_max_age_seconds=value['rule_max_age_seconds'],
            payout_costs=_cost_components(value['payout_costs']),
        )
    except (TypeError, KeyError, ValueError) as exc:
        raise EvidenceError('KATL_PLAN_PWS_CONFIG_SCHEMA') from exc


def _cost_components(value) -> tuple[CostComponent, ...]:
    if type(value) not in (list, tuple) or len(value) > 16:
        raise EvidenceError('KATL_PLAN_COST_CONFIG_BOUND')
    try:
        return tuple(CostComponent(**{**c, 'covers': tuple(c['covers'])}) for c in value)
    except (KeyError, TypeError, ValueError) as exc:
        raise EvidenceError('KATL_PLAN_COST_CONFIG_SCHEMA') from exc


def commission_targets(plan: CandidatePlan, base_target: ShadowScopeTarget,
                       pws_config: dict | None) -> tuple[ShadowScopeTarget, ...]:
    """Bind every cohort scope to separately staged protected model identities.

    These are expected identities only; commissioning preflight still pins and
    revalidates each model from the protected registry. pws_config=None is only
    accepted for an economic-only plan (no PWSLeadLane), and vice versa.
    """
    if (not isinstance(plan, CandidatePlan) or len(plan.events) != 1
            or len(plan.events[0].lanes) != (1 if pws_config is None else 2)
            or type(plan.events[0].lanes[0]) is not TemperatureLane
            or not isinstance(base_target, ShadowScopeTarget)
            or (pws_config is not None
                and (type(plan.events[0].lanes[1]) is not PWSLeadLane
                     or type(pws_config) is not dict or type(pws_config.get('commission')) is not dict
                     or set(pws_config['commission']) != {'payout', 'observation'}))):
        raise EvidenceError('KATL_PLAN_COMMISSION_SHAPE_REQUIRED')
    event = plan.events[0]
    if base_target.scope != event.lanes[0].inputs.scope or base_target.event_id != event.route.event_id:
        raise EvidenceError('KATL_PLAN_COMMISSION_BASE_TARGET_MISMATCH')
    targets = [base_target]
    if pws_config is None:
        return tuple(targets)
    for role, inputs, expected_target in (
            ('payout', event.lanes[1].inputs, 'FINAL_CONTRACT_PAYOUT'),
            ('observation', event.lanes[1].observation_inputs, 'NEXT_OFFICIAL_OBSERVATION')):
        row = pws_config['commission'][role]
        if type(row) is not dict or set(row) != {'model_epoch', 'model_state_sha256', 'feature_contract'}:
            raise EvidenceError('KATL_PLAN_COMMISSION_IDENTITY_REQUIRED')
        fc = dict(row['feature_contract'])
        if 'model_widths' in fc:
            fc['model_widths'] = tuple(tuple(width) for width in fc['model_widths'])
        contract = (PinnedFeatureContract(**{**fc, 'model_ids': tuple(fc['model_ids'])})
                    if 'feature_schema_sha256' in fc else
                    PhysicalFeatureContract(**fc) if 'input_target' in fc else ForecastFeatureContract(**fc))
        target = (contract.bundle_target if isinstance(contract, PhysicalFeatureContract)
                  else contract.prediction_target if isinstance(contract, ForecastFeatureContract)
                  else contract.target)
        if target != expected_target or contract.family != base_target.feature_contract.family:
            raise EvidenceError('KATL_PLAN_COMMISSION_FEATURE_TARGET_MISMATCH')
        targets.append(ShadowScopeTarget(inputs.scope, base_target.unit, base_target.sample_target,
                                         base_target.event_id, row['model_epoch'],
                                         row['model_state_sha256'], contract))
    return tuple(targets)


_UNSPECIFIED = object()


def upgrade_host_plan(base: CandidatePlan, *, official: StationMetadata, pws_config: dict | None,
                      risk_execution_health: ObservationPolicy | None = _UNSPECIFIED,
                      risk_settlement_window: SettlementWindowPolicy | None = _UNSPECIFIED) -> CandidatePlan:
    """Replace one daily smoke event while retaining its reviewed account limits.

    The host must pass the plan returned by its existing build_plan(store).
    It must load pws_config from a separately reviewed local artifact; a present
    but malformed configuration fails before any candidate is assembled. Only an
    explicit pws_config=None selects the economic/EventRisk plan without the PWS
    sleeve, so a missing PWS champion cannot block the temperature lane.
    Omitting risk_execution_health/risk_settlement_window carries the base
    event's own existing policy forward unchanged; passing either explicitly
    (including explicit None) overrides it exactly like build_plan does.
    """
    if (not isinstance(base, CandidatePlan) or len(base.events) != 1 or len(base.events[0].lanes) != 1
            or type(base.events[0].lanes[0]) is not TemperatureLane
            or any((base.observation, base.maker, base.pws_quality, base.forecasts,
                    base.preparations, base.drift, base.reconciliation, base.guardian_config))):
        raise EvidenceError('KATL_PLAN_HOST_SHAPE_REVIEW_REQUIRED')
    event = base.events[0]
    if risk_execution_health is _UNSPECIFIED:
        risk_execution_health = event.risk_execution_health
    if risk_settlement_window is _UNSPECIFIED:
        risk_settlement_window = event.risk_settlement_window
    inputs = event.risk_inputs
    model_sources = tuple(s for s in inputs.sources if s.role == 'MODEL')
    if len(model_sources) != 1 or event.route.station != official.station:
        raise EvidenceError('KATL_PLAN_HOST_MODEL_OR_STATION_REQUIRED')
    binding = inputs.binding
    pws = pws_sleeve_from_config(pws_config, official) if pws_config is not None else None
    base_costs = event.lanes[0].targets[0].costs
    temperature_costs = (_cost_components(pws_config['temperature_costs'])
                         if pws_config is not None and 'temperature_costs' in pws_config else base_costs)
    if base_costs and temperature_costs != base_costs:
        raise EvidenceError('KATL_PLAN_HOST_TEMPERATURE_COST_CONFLICT')
    plan, _, _ = build_plan(
        context=inputs.context, scope=inputs.scope, rule=inputs.rule,
        metadata_fingerprint=official.fingerprint,
        model={'provider': model_sources[0].provider, 'source_identity': model_sources[0].source_identity},
        route_valid_until=event.route.valid_until, release=binding.code_commit, tree=binding.code_tree,
        bundle_sha256=binding.bundle_sha256, collateral=base.account.collateral_asset,
        worker=base.worker_id, account_policy=base.account, correlation=base.correlation,
        scenario_limits=base.limits, stage=inputs.stage,
        book_provider=event.risk_book_provider, main_sources=inputs.sources,
        pws=pws, gefs=base.gefs, gefs_rollover=base.gefs_rollover,
        temperature_costs=temperature_costs,
        risk_execution_health=risk_execution_health, risk_settlement_window=risk_settlement_window)
    return plan


def build_plan(*, context: EventContext, scope: CapabilityScope, rule: RuleFingerprint,
               metadata_fingerprint: str, model: dict, route_valid_until: float, release: str, tree: str,
               bundle_sha256: str, collateral: str, worker: str,
               account_policy: PaperAccountPolicy, correlation: CorrelationMap,
               scenario_limits: ScenarioLimits, stage: str = 'SHADOW',
               book_provider: str = BOOK_PROVIDER, main_sources: tuple[SourceSelector, ...] | None = None,
               pws: PWSSleeve | None = None, gefs: tuple[GEFSPlan, ...] = (),
               gefs_rollover: GEFSRunPolicy | None = None,
               temperature_costs: tuple[CostComponent, ...] = (),
               risk_execution_health: ObservationPolicy | None = None,
               risk_settlement_window: SettlementWindowPolicy | None = DEFAULT_SETTLEMENT_WINDOW_POLICY):
    """Construct the nonfinancial KATL CandidatePlan; no network/service call.

    `context`/`scope` must be the exact EventContext/CapabilityScope already
    certified and reviewed for this deployment (this module does not invent
    or approve either). `model` is an already-fetched MODEL evidence row body
    (same shape the live script reads via `store.get('katl-gefs-current:path')
    ['body']`) -- only its `provider`/`source_identity` are used, so any MODEL
    source the lane should lease works, not only a GEFS coverage path.
    `route_valid_until` is the event route's own expiry (the live script uses
    the GEFS path's `coverage.local_day_end`; any correct local-day-end epoch
    works). `main_sources` defaults to a single MODEL selector, correct for
    the live FUTURE_FORECAST scope; a scope needing OFFICIAL/FEATURES leases
    too (e.g. SAME_DAY_LATE_LOCK) must supply its own tuple. Returns
    (plan, scope, model) like the live script's build_plan, for an identical
    deployment-report shape. The caller must supply reviewed account and
    scenario limits; this builder never silently widens paper risk budgets.
    The settlement-window default matches the reviewed Oct-11 template.
    Explicit None preserves the original UNKNOWN, byte-identical fallback.
    `risk_execution_health` remains unset unless a reviewed policy is supplied;
    its metrics then remain UNKNOWN.
    """
    event_id, account = context.event_id, context.account_id
    if (scope.station != context.station_id or rule.payload['event_id'] != event_id
            or rule.payload['metadata_fingerprint'] != metadata_fingerprint
            or scope.strategy not in {'FUTURE_FORECAST', 'SAME_DAY_LATE_LOCK'}
            or account_policy.account_id != account or account_policy.collateral_asset != collateral
            or not any(m.station == context.station_id and m.metadata_fingerprint == metadata_fingerprint
                       for m in correlation.memberships)):
        raise EvidenceError('KATL_PLAN_REVIEWED_SCOPE_OR_RISK_POLICY_REQUIRED')
    if main_sources is None and scope.strategy != 'FUTURE_FORECAST':
        raise EvidenceError('KATL_PLAN_EXPLICIT_SAME_DAY_SOURCES_REQUIRED')
    if main_sources is None:
        main_sources = (SourceSelector('MODEL', model['provider'], model['source_identity'], 43200.),)
    if (type(temperature_costs) is not tuple or len(temperature_costs) > 16
            or any(not isinstance(c, CostComponent) for c in temperature_costs)):
        raise EvidenceError('KATL_PLAN_TEMPERATURE_COST_BOUND')
    if ((risk_execution_health is not None and type(risk_execution_health) is not ObservationPolicy)
            or (risk_settlement_window is not None
                and type(risk_settlement_window) is not SettlementWindowPolicy)):
        raise EvidenceError('KATL_PLAN_RISK_POLICY_TYPE')
    if pws is not None:
        common_scope_fields = ('station', 'family', 'source_rule_family', 'strategy', 'season', 'time_of_day')
        if (pws.official.station != context.station_id or pws.official.fingerprint != metadata_fingerprint
                or pws.observation_bundle_sha256 == bundle_sha256
                or any(getattr(pws.payout_scope, field) != getattr(pws.observation_scope, field)
                       for field in common_scope_fields)):
            raise EvidenceError('KATL_PLAN_PWS_CONTEXT_OR_MODEL_PAIR_REQUIRED')
    config = {'version': VERSION, 'release': release, 'tree': tree, 'event_id': event_id,
              'scope': asdict(scope), 'rule': rule.sha256, 'bundle': bundle_sha256,
              'route_valid_until': route_valid_until, 'stage': stage,
              'collateral': collateral, 'worker': worker, 'book_provider': book_provider,
              'account': asdict(account_policy), 'correlation': asdict(correlation),
              'limits': asdict(scenario_limits), 'main_sources': [asdict(s) for s in main_sources],
              'temperature_costs': [asdict(c) for c in temperature_costs],
              'pws': asdict(pws) if pws is not None else None,
              'gefs': [asdict(g) for g in gefs],
              'gefs_rollover': asdict(gefs_rollover) if gefs_rollover is not None else None}
    # Only present when configured, so every existing release config digest is unchanged.
    if risk_execution_health is not None:
        config['risk_execution_health'] = asdict(risk_execution_health)
    if risk_settlement_window is not None:
        config['risk_settlement_window'] = asdict(risk_settlement_window)
    config_sha = digest(config)
    binding = ReleaseBinding(release, tree, config_sha, bundle_sha256, rule.sha256)
    risk_sources = list(main_sources)
    if pws is not None:
        for source in pws.payout_sources:
            if source.role == 'FEATURES':
                continue
            key = (source.role, source.provider, source.source_identity)
            old = next((i for i, s in enumerate(risk_sources)
                        if (s.role, s.provider, s.source_identity) == key), None)
            if old is None:
                risk_sources.append(source)
            elif source.maximum_age_seconds < risk_sources[old].maximum_age_seconds:
                risk_sources[old] = source
    inputs = ScopeInputs(context, scope, rule, binding, stage, 86400., tuple(risk_sources))
    temperature_inputs = ScopeInputs(context, scope, rule, binding, stage, 86400., main_sources)
    first = rule.payload['partition'][0]
    target = TargetPlan(first['market_id'], 'YES', '1', '1', temperature_costs)

    valuation = ValuationPolicy(VERSION, collateral, BOOK_FRESHNESS_SECONDS, BOOK_FRESHNESS_SECONDS, '.05', '1')
    temperature_lane = TemperatureLane('temperature', temperature_inputs, (target,), book_provider, valuation, 20.)
    lanes = (temperature_lane,)

    required_source_kinds = (('MODEL',) if gefs else ()) + ('OFFICIAL_OBSERVATION',)
    if pws is not None:
        required_source_kinds += ('PWS_OBSERVATION',)
        # The observation (lead) model is its own reviewed champion, separate
        # from the payout scope's bundle -- never the same artifact that could
        # later claim settlement authority (pws_lead.py's paired-ablation check
        # already refuses a shared bundle; this keeps the plan honest too).
        observation_binding = ReleaseBinding(release, tree, config_sha, pws.observation_bundle_sha256, rule.sha256)
        payout_inputs = ScopeInputs(context, pws.payout_scope, rule, binding, stage,
                                     pws.rule_max_age_seconds, pws.payout_sources)
        observation_inputs = ScopeInputs(context, pws.observation_scope, rule, observation_binding, stage,
                                          pws.rule_max_age_seconds, pws.observation_sources)
        payout_target = TargetPlan(first['market_id'], 'YES', '1', '1', pws.payout_costs)
        pws_lane = PWSLeadLane('pws-observation-lead', payout_inputs, (payout_target,), book_provider, valuation, 20.,
                               observation_inputs, pws.without_pws_sources, pws.lead_policy)
        lanes += (pws_lane,)

    tokens = tuple(t for b in rule.payload['partition'] for t in (b['yes_token'], b['no_token']))
    route = EventRoute(event_id, context.station_id, rule.payload['target_date'], rule.payload['family'],
                       rule.sha256, tokens, route_valid_until, required_source_kinds)
    census = CensusPlan(rule, collateral, official_metar_proxy=True,
                        pws=PWSQualityPlan(event_id, pws.official, pws.quality_policy) if pws is not None else None)

    # normal.revalidate_seconds is set just under the book/microstructure
    # ceiling so a NORMAL-state coordinator event survives one full
    # census-to-admission cycle; caution/event/recovery must stay strictly
    # below it (EventPolicy.__post_init__'s STRICTER_DEGRADED_GUARDS_REQUIRED).
    normal = StateGuard(1, 0, 1, 1, 100)
    caution = StateGuard(.5, .01, .5, 2, 45)
    event_guard = StateGuard(.2, .02, .2, 3, 20)
    recovery = StateGuard(.5, .01, .5, 2, 45)
    event_policy = EventPolicy(
        VERSION, BOOK_FRESHNESS_SECONDS, 43200., BOOK_FRESHNESS_SECONDS,
        2., 5., 21600., 43200.,
        .01, .05, .1, .2, .2, .5, .1, .3, .5, .9,
        3, .1, 60., 3, 20., 10.,
        normal, caution, event_guard, recovery,
    )
    micro = MicrostructurePolicy(VERSION, collateral, 120., MICROSTRUCTURE_HISTORY_SECONDS, 30., .01, 2)
    risk_policy = RiskInputPolicy(VERSION, micro, event_policy, 32, 2.)

    source_ages = []
    for kind in sorted(KINDS):
        age = (43200. if kind == 'MODEL' else 3600. if kind == 'OFFICIAL_OBSERVATION'
               else BOOK_FRESHNESS_SECONDS if kind in {'BOOK', 'TRADE'}
               else 300. if kind == 'PWS_OBSERVATION' else 300.)
        source_ages.append((kind, age))
    trigger = TriggerPolicy(VERSION, 2, 1, 4, 64, 300., 60., 200000, 86400.,
                            tuple(source_ages), (('KATL', 300.),))
    health = HealthPolicy(VERSION, 30., 30., 2., 2, .05, (worker,))
    runtime = RuntimePolicy(VERSION, 32, 1, 1, 20., CENSUS_INTERVAL_SECONDS, 15.)
    # The daily host template permits 12 jobs and the separate preflight
    # builder permits 1. Six is a bounded candidate budget, not a measured
    # throughput or scheduling guarantee under host load.
    candidate = CandidatePolicy(VERSION, 75., 6, 160, .5, 60., .1, 120.)
    event = CandidateEvent(route, census, inputs, book_provider, valuation, risk_policy, lanes,
                           risk_execution_health=risk_execution_health,
                           risk_settlement_window=risk_settlement_window)
    plan = CandidatePlan(
        VERSION, account_policy, correlation, scenario_limits, (event,), trigger, health, runtime, candidate,
        CensusPolicy(VERSION, 60., 30., 63), BookPolicy(VERSION, BOOK_FRESHNESS_SECONDS, 1000),
        DiscoveryPolicy(VERSION, page_size=10, maximum_pages=2, maximum_event_hits=100,
                        maximum_events_per_step=8, maximum_processing_seconds=2.,
                        maximum_scan_seconds=300., maximum_page_age_seconds=300.,
                        maximum_metadata_age_seconds=86400., maximum_page_failures=3),
        AuditPolicy(VERSION), FeedPolicy(VERSION), worker,
        pws_quality=PWSQualitySettings((PWSQualityPlan(event_id, pws.official, pws.quality_policy),))
                    if pws is not None else None,
        gefs=gefs, gefs_rollover=gefs_rollover,
    )
    return plan, scope, model
