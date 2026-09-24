"""Current archived inputs -> scoped runtime requests, with no authority fallback.

Plans are configuration, not certification or model approval. Protected admission
still verifies each exact scope/bundle and source version. No source is refetched,
re-dated, imputed, or silently substituted. Discovery cannot broaden these plans.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass

from .basket_valuation import BasketLeg, BasketPolicy
from .certification import CapabilityScope
from .event_risk import EventContext, EventRiskEngine, _key
from .evidence import EvidenceError, ReleaseBinding, digest, finite, identity
from .position_management import ExitRequest
from .pws_lead import LeadPolicy
from .reaction_runtime import PWSLeadRequest, SourceReleaseRequest
from .relative_value import DiscoveryRequest
from .rules import RuleFingerprint
from .scenario_risk import number
from .strategy_admission import StrategyAdmission, SourceLease, ROLES
from .strategy_pipeline import EntryRequest
from .valuation import CostComponent, ValuationPolicy, contract_target


VERSION = 'alpha_v11_current_request_assembly_v1'


@dataclass(frozen=True)
class SourceSelector:
    role: str
    provider: str
    source_identity: str
    maximum_age_seconds: float

    def __post_init__(self):
        identity(self.provider); identity(self.source_identity)
        if self.role not in ROLES or not 0 < finite(self.maximum_age_seconds) <= 86400:
            raise EvidenceError('ASSEMBLY_SOURCE_SELECTOR_INVALID')


@dataclass(frozen=True)
class ScopeInputs:
    context: EventContext
    scope: CapabilityScope
    rule: RuleFingerprint
    binding: ReleaseBinding
    stage: str
    rule_max_age_seconds: float
    sources: tuple[SourceSelector, ...]

    def __post_init__(self):
        if (not isinstance(self.context, EventContext) or not isinstance(self.scope, CapabilityScope)
                or not isinstance(self.rule, RuleFingerprint) or not isinstance(self.binding, ReleaseBinding)
                or self.stage not in {'PAPER','SHADOW'} or not 0 < finite(self.rule_max_age_seconds) <= 86400):
            raise EvidenceError('ASSEMBLY_NONFINANCIAL_SCOPE_REQUIRED')
        if (type(self.sources) is not tuple or not 1 <= len(self.sources) <= 16
                or any(not isinstance(s, SourceSelector) for s in self.sources)
                or len({(s.role,s.provider,s.source_identity) for s in self.sources}) != len(self.sources)):
            raise EvidenceError('ASSEMBLY_SOURCE_BOUND')
        if self.context.event_id != self.rule.payload['event_id'] or self.binding.rule_fingerprint != self.rule.sha256:
            raise EvidenceError('ASSEMBLY_RULE_CONTEXT_MISMATCH')


@dataclass(frozen=True)
class TargetPlan:
    market_id: str
    side: str
    units: str
    desired_total_units: str
    costs: tuple[CostComponent, ...]

    def __post_init__(self):
        identity(self.market_id)
        if self.side not in {'YES','NO'} or not 0 < number(self.units) <= number(self.desired_total_units) <= 1_000_000:
            raise EvidenceError('ASSEMBLY_TARGET_SIZE')
        if type(self.costs) is not tuple or len(self.costs) > 16 or any(not isinstance(c,CostComponent) for c in self.costs):
            raise EvidenceError('ASSEMBLY_COST_BOUND')


class RequestAssembler:
    def __init__(self, queue, inputs, *, book_provider, valuation_policy, lifetime_seconds):
        if not isinstance(inputs,ScopeInputs) or not isinstance(valuation_policy,ValuationPolicy):
            raise EvidenceError('ASSEMBLY_TYPED_CONFIGURATION_REQUIRED')
        identity(book_provider)
        if not 0 < finite(lifetime_seconds) <= 60: raise EvidenceError('ASSEMBLY_DECISION_LIFETIME_BOUND')
        route = queue.routes.get(inputs.context.event_id)
        if route is None or route.rule_fingerprint != inputs.rule.sha256:
            raise EvidenceError('ASSEMBLY_REGISTERED_RULE_ROUTE_REQUIRED')
        self.queue, self.store = queue, queue.store
        self.inputs = deepcopy(inputs)
        self.book_provider, self.valuation_policy, self.lifetime = book_provider, valuation_policy, lifetime_seconds
        self.config = digest(self.description())

    def description(self):
        return dict(version=VERSION, queue=self.queue.config, inputs=asdict(self.inputs),
                    book_provider=self.book_provider, valuation_policy=asdict(self.valuation_policy), lifetime_seconds=self.lifetime)

    def check(self, claim):
        if digest(self.description()) != self.config: raise EvidenceError('ASSEMBLY_CONFIGURATION_CHANGED')
        state = self.queue.snapshot(); active = state['active']
        if (self.queue._worker != claim.get('claim_id') or active is None
                or active['claim_id'] != claim.get('claim_id') or active['event_id'] != claim.get('event_id')
                or claim.get('event_id') != self.inputs.context.event_id):
            raise EvidenceError('ASSEMBLY_HELD_EVENT_CLAIM_REQUIRED')
        if active['requires_full_census'] or active['event_id'] in state['needs_census']:
            raise EvidenceError('ASSEMBLY_FRESH_CENSUS_REQUIRED')
        if not active['claimed_at'] <= finite(self.store.clock()) < active['deadline']:
            raise EvidenceError('ASSEMBLY_CLAIM_EXPIRED')
        return active

    def sources(self, inputs):
        leases = []
        for select in inputs.sources:
            row = self.store.latest_source(kind=ROLES[select.role], event_id=inputs.context.event_id,
                                          provider=select.provider, source_identity=select.source_identity)
            if row is None: raise EvidenceError('ASSEMBLY_REQUIRED_SOURCE_UNAVAILABLE')
            leases.append(SourceLease(row['id'],select.role,select.maximum_age_seconds))
        return tuple(leases)

    def pin(self, claim, inputs=None):
        active = self.check(claim); inputs = inputs or self.inputs
        if inputs.context != self.inputs.context or inputs.rule != self.inputs.rule or inputs.stage != self.inputs.stage:
            raise EvidenceError('ASSEMBLY_PAIRED_INPUT_SCOPE_MISMATCH')
        leases = self.sources(inputs)
        # Admission includes the current official head even in a model-only scope.
        # Different claims get new pins; no old pin's expiry is refreshed.
        key = 'assembled-pin:'+digest([active['claim_id'],asdict(inputs),[asdict(s) for s in leases]])
        kw = asdict(inputs); kw.pop('sources')
        kw.update(context=inputs.context,scope=inputs.scope,rule=inputs.rule,binding=inputs.binding,source_leases=leases)
        try: row = self.store.get(key)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING': raise
            row = StrategyAdmission(self.store).pin(key,**kw)
        else:
            StrategyAdmission(self.store).revalidate(key,context=inputs.context,rule=inputs.rule,
                binding=asdict(inputs.binding),strategies=(inputs.scope.strategy,))
        return row, leases

    def book(self, target):
        if not isinstance(target,TargetPlan): raise EvidenceError('ASSEMBLY_TARGET_REQUIRED')
        exact = contract_target(self.inputs.rule,target.market_id,target.side)
        row = self.store.latest_source(kind='BOOK',event_id=self.inputs.context.event_id,
                                      provider=self.book_provider,source_identity=exact['token_id'])
        if row is None: raise EvidenceError('ASSEMBLY_CURRENT_EXACT_BOOK_UNAVAILABLE')
        body = row['body']; p = body['payload']; now = finite(self.store.clock())
        if (any(p.get(k) != v for k,v in exact.items()) or p.get('rule_fingerprint') != self.inputs.rule.sha256
                or p.get('collateral_asset') != self.valuation_policy.collateral_asset
                or body.get('evidence_class') == 'HISTORICAL_AVAILABILITY_UNKNOWN'
                or body.get('observed_at') is None or not body['observed_at'] <= body['received_at'] <= body['available_at'] <= now
                or now-body['observed_at'] >= self.valuation_policy.max_book_age_seconds):
            raise EvidenceError('ASSEMBLY_BOOK_IDENTITY_OR_FRESHNESS')
        return row

    def current(self, claim, targets):
        pin, leases = self.pin(claim); books = tuple(self.book(t) for t in targets)
        row = self.store.latest(kind='COORDINATOR_EVENT',event_id=_key('event-risk',self.inputs.context.event_id))
        if row is None: raise EvidenceError('ASSEMBLY_CURRENT_EVENT_RISK_REQUIRED')
        event = EventRiskEngine(self.store).revalidate(row['id']); er = event['request']
        source_ids = {s.evidence_id for s in leases if s.role != 'FEATURES'}
        if (er['context'] != asdict(self.inputs.context) or er['binding'] != asdict(self.inputs.binding)
                or not {b['id'] for b in books} <= set(er['book_ids']) or not source_ids <= set(er['source_ids'])):
            raise EvidenceError('ASSEMBLY_EVENT_RISK_INPUTS_CHANGED_RECOMPUTE')
        expiry = min(finite(self.store.clock())+self.lifetime, claim['deadline'], event['valid_until'],
                     pin['body']['details']['assessment']['valid_until'])
        if finite(self.store.clock()) >= expiry: raise EvidenceError('ASSEMBLY_INPUTS_EXPIRED')
        return pin, leases, books, row, expiry


def _single(leases, role):
    keys = tuple(s.evidence_id for s in leases if s.role == role)
    if len(keys) != 1: raise EvidenceError('ASSEMBLY_EXACT_'+role+'_INPUT_REQUIRED')
    return keys[0]


class EntryRequestFactory:
    def __init__(self, assembler, targets):
        if (type(targets) is not tuple or not 1 <= len(targets) <= 6
                or any(not isinstance(t,TargetPlan) for t in targets)):
            raise EvidenceError('ASSEMBLY_ENTRY_TARGET_BOUND')
        self.assembler, self.targets = assembler, targets

    @property
    def config(self): return digest(dict(assembler=self.assembler.config,targets=[asdict(t) for t in self.targets],type=type(self).__name__))

    def __call__(self, claim):
        a = self.assembler; pin, leases, books, event, expiry = a.current(claim,self.targets)
        models = tuple(s.evidence_id for s in leases if s.role == 'MODEL')
        same_day = a.inputs.scope.strategy != 'FUTURE_FORECAST'
        observed, coverage = (_single(leases,'OFFICIAL'),_single(leases,'FEATURES')) if same_day else (None,None)
        return tuple(EntryRequest(pin['id'],event['id'],t.market_id,t.side,t.units,t.desired_total_units,
            b['id'],expiry,a.valuation_policy,t.costs,models,observed,coverage) for t,b in zip(self.targets,books))


class RelativeValueRequestFactory(EntryRequestFactory):
    def __init__(self, assembler, targets, *, basket_policy, maximum_proposals=1):
        if (type(targets) is not tuple or not 1 <= len(targets) <= 32 or any(not isinstance(t,TargetPlan) for t in targets)
                or not isinstance(basket_policy,BasketPolicy) or basket_policy.valuation != assembler.valuation_policy
                or type(maximum_proposals) is not int or not 1 <= maximum_proposals <= 6):
            raise EvidenceError('ASSEMBLY_BASKET_PLAN_BOUND')
        self.assembler, self.targets, self.policy, self.maximum = assembler, targets, basket_policy, maximum_proposals

    @property
    def config(self): return digest(dict(base=super().config,basket=asdict(self.policy),maximum=self.maximum))

    def __call__(self, claim):
        a = self.assembler; pin, leases, books, event, expiry = a.current(claim,self.targets)
        legs = tuple(BasketLeg(t.market_id,t.side,t.units,b['id'],t.costs) for t,b in zip(self.targets,books))
        desired = tuple((contract_target(a.inputs.rule,t.market_id,t.side)['token_id'],t.desired_total_units) for t in self.targets)
        return (DiscoveryRequest(pin['id'],event['id'],legs,desired,tuple(s.evidence_id for s in leases if s.role=='MODEL'),
                                  self.policy,expiry,self.maximum),)


class PWSRequestFactory:
    def __init__(self, entry_factory, *, observation_inputs, without_pws_sources, lead_policy):
        if (type(entry_factory) is not EntryRequestFactory
                or not isinstance(observation_inputs,ScopeInputs) or not isinstance(lead_policy,LeadPolicy)
                or type(without_pws_sources) is not tuple or not 1 <= len(without_pws_sources) <= 16
                or any(not isinstance(s,SourceSelector) or s.role!='MODEL' for s in without_pws_sources)):
            raise EvidenceError('ASSEMBLY_PWS_PLAN_REQUIRED')
        self.entries, self.observation, self.without, self.policy = entry_factory, deepcopy(observation_inputs), without_pws_sources, lead_policy

    @property
    def config(self): return digest(dict(entry=self.entries.config,observation=asdict(self.observation),
                                        without=[asdict(s) for s in self.without],policy=asdict(self.policy)))

    def __call__(self, claim):
        a = self.entries.assembler; entries = self.entries(claim)
        observation, _ = a.pin(claim,self.observation)
        ids = []
        for s in self.without:
            row = a.store.latest_source(kind='MODEL',event_id=a.inputs.context.event_id,provider=s.provider,source_identity=s.source_identity)
            if row is None: raise EvidenceError('ASSEMBLY_PWS_ABLATION_UNAVAILABLE')
            body = row['body']; now = finite(a.store.clock())
            if body['issued_at'] is None or not 0 <= now-body['issued_at'] < s.maximum_age_seconds:
                raise EvidenceError('ASSEMBLY_PWS_ABLATION_ISSUE_AGE')
            ids.append(row['id'])
        return tuple(PWSLeadRequest(e,observation['id'],tuple(ids),self.policy) for e in entries)


class SourceReleaseRequestFactory:
    def __init__(self, entry_factory):
        if type(entry_factory) is not EntryRequestFactory:
            raise EvidenceError('ASSEMBLY_RELEASE_ENTRY_FACTORY_REQUIRED')
        self.entries = entry_factory

    @property
    def config(self): return digest(dict(entry=self.entries.config,type=type(self).__name__))

    def __call__(self, claim):
        entries = self.entries(claim); result = []
        for e in entries:
            previous = self.entries.assembler.store.previous_source(e.observed_input_id)
            if previous is None: raise EvidenceError('ASSEMBLY_RELEASE_PREDECESSOR_UNAVAILABLE')
            result.append(SourceReleaseRequest(e,previous['id'],e.observed_input_id))
        return tuple(result)


class ExitRequestFactory(EntryRequestFactory):
    def __init__(self, assembler, targets, *, hold_costs, sale_costs, reason):
        super().__init__(assembler,targets)
        from .position_management import REASONS
        if reason not in REASONS or any(type(c) is not tuple or len(c)>16 or any(not isinstance(x,CostComponent) for x in c)
                                       for c in (hold_costs,sale_costs)):
            raise EvidenceError('ASSEMBLY_EXIT_COSTS_OR_REASON')
        self.hold_costs, self.sale_costs, self.reason = hold_costs, sale_costs, reason

    @property
    def config(self): return digest(dict(base=super().config,hold=[asdict(c) for c in self.hold_costs],
                                        sale=[asdict(c) for c in self.sale_costs],reason=self.reason))

    def __call__(self, claim):
        a = self.assembler; pin, leases, books, event, expiry = a.current(claim,self.targets)
        conditioned = any(s.role == 'FEATURES' for s in leases)
        observed, coverage = (_single(leases,'OFFICIAL'),_single(leases,'FEATURES')) if conditioned else (None,None)
        return tuple(ExitRequest(pin['id'],event['id'],t.market_id,t.side,t.units,b['id'],expiry,
            a.valuation_policy,self.hold_costs,self.sale_costs,self.reason,observed,coverage) for t,b in zip(self.targets,books))
