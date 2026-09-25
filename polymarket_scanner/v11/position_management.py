"""Receipt-bound, whole-event exit research for actual paper inventory.

The conservative objective is the minimum wealth across the exact partition.
A vacuous single-token lower bound must not erase a held basket's payout floor.
Only the common coordinator can reserve inventory or reconcile synthetic fills.
"""
from dataclasses import asdict, dataclass
from copy import deepcopy
from decimal import Decimal

from .certification import CapabilityScope
from .event_risk import EventContext, EventRiskEngine, SafetyReductions
from .evidence import EvidenceError, ReleaseBinding, canonical, digest, finite, identity
from .model_artifacts import predict_with_bundle
from .model_registry import ActiveModelRegistry
from .probability import FINAL_EXTREME, UNRESOLVED_EXTREME
from .rules import RuleFingerprint
from .scenario_risk import Attribution, event_scenarios, number, precise
from .strategy_admission import StrategyAdmission, VERSION as ADMISSION_VERSION
from .valuation import CostComponent, ValuationPolicy, compare_hold_sale, contract_target


VERSION = 'alpha_v11_position_management_v1'
REASONS = {'THESIS_INVALIDATED', 'NET_SALE_EXCEEDS_HOLD', 'EDGE_CAPTURED',
           'EVENT_RISK', 'SETTLEMENT_UNCERTAINTY', 'SOURCE_HEALTH', 'EXPOSURE_REDUCTION'}


@dataclass(frozen=True)
class ExitRequest:
    admission_id: str
    event_state_id: str
    market_id: str
    side: str
    units: str
    book_id: str
    expires_at: float
    policy: ValuationPolicy
    hold_costs: tuple[CostComponent, ...]
    sale_costs: tuple[CostComponent, ...]
    reason: str
    observed_input_id: str | None = None
    coverage_input_id: str | None = None
    preconfirmation_id: str | None = None
    source_release_id: str | None = None

    def __post_init__(self):
        for key in (self.admission_id, self.event_state_id, self.market_id, self.book_id):
            identity(key)
        for key in (self.observed_input_id, self.coverage_input_id, self.preconfirmation_id, self.source_release_id):
            if key is not None:
                identity(key)
        finite(self.expires_at)
        if self.side not in {'YES', 'NO'} or not 0 < number(self.units) <= 1_000_000:
            raise EvidenceError('EXIT_SIZE_OR_SIDE')
        if self.reason not in REASONS or not isinstance(self.policy, ValuationPolicy):
            raise EvidenceError('EXIT_REASON_OR_POLICY')
        for costs in (self.hold_costs, self.sale_costs):
            if type(costs) is not tuple or len(costs) > 16 or any(not isinstance(c, CostComponent) for c in costs):
                raise EvidenceError('EXIT_COST_INPUT_BOUND')
        if (self.observed_input_id is None) != (self.coverage_input_id is None):
            raise EvidenceError('EXIT_EXACT_OBSERVATION_AND_COVERAGE_REQUIRED')

    @classmethod
    def from_dict(cls, raw):
        p = dict(raw); p['policy'] = ValuationPolicy(**p['policy'])
        for key in ('hold_costs', 'sale_costs'):
            p[key] = tuple(CostComponent(**{**c, 'covers':tuple(c['covers'])}) for c in p[key])
        return cls(**p)


def _original(store, request):
    row = store.get(request.admission_id)
    if row['kind'] != 'REGISTRY' or row['body'].get('details', {}).get('version') != ADMISSION_VERSION:
        raise EvidenceError('STRATEGY_ADMISSION_RECORD_REQUIRED')
    original = row['body']['details']['request']
    return (original, CapabilityScope(**original['scope']), EventContext(**original['context']),
            RuleFingerprint(**original['rule']), ReleaseBinding(**original['binding']))


def _inventory(coordinator, state, rule, *, own_intent_id=None):
    from .paper_coordinator import UNRESOLVED
    event = rule.payload['event_id']
    if state['faults']:
        raise EvidenceError('PAPER_ACCOUNT_FAULT_ACTIVE')
    if state['rules'].get(event) != asdict(rule):
        raise EvidenceError('EXIT_HELD_RULE_RECONCILIATION_REQUIRED')
    if any(i['event_id'] == event and i['status'] in UNRESOLVED and key != own_intent_id
           for key, i in state['intents'].items()):
        raise EvidenceError('EXIT_RECONCILE_OPEN_EVENT_INTENTS_FIRST')
    lots = {key:p for key,p in state['lots'].items() if p['event_id'] == event}
    fingerprint = digest(dict(account_id=state['account_id'], namespace=state['execution_namespace'],
                              policy_sha256=coordinator.policy_sha, rule=asdict(rule),
                              context=state['contexts'].get(event), lots=lots))
    return lots, fingerprint


@precise
def _joint_value(coordinator, state, rule, value, *, own_intent_id=None):
    lots, fingerprint = _inventory(coordinator, state, rule, own_intent_id=own_intent_id)
    token, qty = value['target']['token_id'], number(value['units'])
    held = sum(number(p['units']) for p in lots.values() if p['token_id'] == token)
    if qty > held or qty <= 0:
        raise EvidenceError('EXIT_EXCEEDS_ACTUAL_HELD_INVENTORY')
    positions = tuple(coordinator._position(p) for p in lots.values())
    view = event_scenarios(rule, execution_namespace=coordinator.store.namespace,
                          account_id=coordinator.policy.account_id, positions=positions, pending=())
    target = value['target']; rows = []
    for row in view['outcomes']:
        pays = (row['market_id'] == target['market_id']) == (target['side'] == 'YES')
        rows.append(dict(market_id=row['market_id'], before=number(row['held_payout']),
                         after=number(row['held_payout'])-qty*int(pays)))
    before, after = min(r['before'] for r in rows), min(r['after'] for r in rows)
    sale = qty*(number(value['book']['worst_consumed_price'])-number(value['sale_costs']['known_total_per_share']))
    avoided_hold_cost = qty*number(value['hold_costs']['known_total_per_share'])
    # Other lots' future cost assumptions are unchanged and cancel. Only this
    # reduction's explicit future costs are avoided; entry basis never enters EV.
    advantage = sale+after-before+avoided_hold_cost
    return dict(inventory_sha256=fingerprint, held_units=str(held),
                objective='EXACT_PARTITION_MINIMUM_PROSPECTIVE_WEALTH',
                before_payout_floor=str(before), residual_payout_floor=str(after),
                full_sale_net_proceeds_at_limit=str(sale), avoided_future_hold_cost=str(avoided_hold_cost),
                advantage_at_limit_total=str(advantage), advantage_at_limit_per_share=str(advantage/qty),
                outcomes=[{k:str(v) if isinstance(v, Decimal) else v for k,v in r.items()} for r in rows],
                pending_hedge_credit=False, conditional_on_full_fill=True,
                actual_realized_pnl=None, financial_authority=False)


def payout_inputs(store, request, original, rule, cutoff):
    """Shared causal payout inputs; no protected pointer or approval is read."""
    from .strategy_pipeline import _model_inputs, _condition
    leased = {s['evidence_id']:s for s in original['source_leases']}
    ids = tuple(k for k,s in leased.items() if s['role'] == 'MODEL')
    if not ids:
        raise EvidenceError('EXIT_ARCHIVED_MODEL_LEASE_REQUIRED')
    conditioned = request.observed_input_id is not None
    inference_at = cutoff
    if conditioned:
        if any(key not in leased or leased[key]['role'] != role for key,role in
               ((request.observed_input_id, 'OFFICIAL'), (request.coverage_input_id, 'FEATURES'))):
            raise EvidenceError('EXIT_CONDITION_REQUIRES_ADMISSION_LEASE')
        inference_at = finite(store.get(request.coverage_input_id)['body']['payload']['as_of'])
        if inference_at > cutoff:
            raise EvidenceError('EXIT_CONDITION_CUTOFF_IN_FUTURE')
    components = _model_inputs(store, rule, ids, inference_at,
                               target=UNRESOLVED_EXTREME if conditioned else FINAL_EXTREME)
    observed, coverage = _condition(store, rule, request, inference_at, components, available_cutoff=cutoff) if conditioned else (None, None)
    return components, observed, coverage, inference_at, min(leased[k]['maximum_age_seconds'] for k in ids)


def _predict(store, request, original, scope, context, rule, binding, cutoff):
    admission = StrategyAdmission(store).revalidate(request.admission_id, context=context, rule=rule,
                            binding=asdict(binding), strategies=(scope.strategy,))
    components, observed, coverage, inference_at, maximum_age = payout_inputs(store, request, original, rule, cutoff)
    model = ActiveModelRegistry().pin(scope_key=scope.key, mode='V11_PAPER' if original['stage']=='PAPER' else 'V11_SHADOW')
    if model.state_sha256 != admission['model_state_sha256'] or model.bundle.sha256 != binding.bundle_sha256:
        raise EvidenceError('EXIT_MODEL_CHANGED_RECOMPUTE')
    prediction = predict_with_bundle(model.bundle, rule, components, as_of=inference_at,
            max_source_age_seconds=maximum_age,
            observed=observed, remaining_coverage=coverage)
    if not ActiveModelRegistry().revalidate(model)['passed']:
        raise EvidenceError('EXIT_MODEL_CHANGED_RECOMPUTE')
    return prediction, admission


class _ReadAt:
    def __init__(self, store, at):
        self.store, self.at = store, at

    def __getattr__(self, name):
        return getattr(self.store, name)

    def clock(self):
        return self.at

    def audit(self, key, **kw):
        return {'body': {'details':kw['details']}}


def _value(coordinator, state, request, rule, binding, prediction, cutoff, *, own_intent_id=None):
    lots, fingerprint = _inventory(coordinator, state, rule, own_intent_id=own_intent_id)
    target = contract_target(rule, request.market_id, request.side)
    held = [p for p in lots.values() if p['token_id'] == target['token_id']]
    value = compare_hold_sale(_ReadAt(coordinator.store, cutoff), '__exit_compute__', rule=rule,
           prediction=prediction, binding=binding, market_id=request.market_id, side=request.side,
           units=request.units, held_units=str(sum(number(p['units']) for p in held)),
           held_all_in_cost_basis=str(sum(number(p['all_in_cost_basis']) for p in held)),
           book_id=request.book_id, policy=request.policy, hold_costs=request.hold_costs,
           sale_costs=request.sale_costs, thesis_reason=request.reason)['body']['details']
    value['single_token_sale_advantage_per_share'] = value['sale_advantage_per_share']
    joint = None
    if value['outcome'] != 'GATED':
        from .position_attribution import consume_lots
        qty = number(value['units'])
        sale_proceeds = qty*number(value['net_sale_per_share'], signed=True)
        if sale_proceeds < 0:
            value.update(outcome='GATED', reasons=['EXIT_NET_PROCEEDS_NEGATIVE'])
            sale_proceeds = Decimal(0)
        basis, allocations = consume_lots(deepcopy(lots), token_id=target['token_id'],
                                           quantity=qty, net_proceeds=sale_proceeds)
        value.update(hypothetical_lifetime_pnl=str(sale_proceeds-basis),
                     hypothetical_fifo_allocations=allocations,
                     basis_allocation='ACQUISITION_SEQUENCE_WITH_EXPLICIT_LEGACY_UNKNOWN_COHORT')
    if value['outcome'] != 'GATED':
        joint = _joint_value(coordinator, state, rule, value, own_intent_id=own_intent_id)
        advantage = number(joint['advantage_at_limit_per_share'], signed=True)
        value.update(sale_advantage_per_share=str(advantage),
                     outcome='REDUCE_RESEARCH_CANDIDATE' if advantage > number(request.policy.minimum_ev_per_share) else 'HOLD_RESEARCH_ESTIMATE',
                     reasons=['JOINT_NET_SALE_EXCEEDS_HOLD' if advantage > number(request.policy.minimum_ev_per_share) else 'SALE_REMOVES_TOO_MUCH_JOINT_HOLD_VALUE'])
    value['position_management'] = dict(version=VERSION, request=asdict(request),
                  request_sha256=digest(asdict(request)), inventory_sha256=fingerprint, joint=joint,
                  account_policy_sha256=coordinator.policy_sha,
                  reason_origin='REQUESTED_CATEGORY_ECONOMICS_INDEPENDENTLY_RECOMPUTED')
    return value


def revalidate_exit(coordinator, proposal, value, *, state=None, own_intent_id=None):
    """Reproduce protected inference and inventory economics at each order gate."""
    saved = value.get('position_management', {})
    if saved.get('version') != VERSION:
        raise EvidenceError('CURRENT_POSITION_EXIT_EVALUATION_REQUIRED')
    request = ExitRequest.from_dict(saved['request'])
    original, scope, context, rule, binding = _original(coordinator.store, request)
    if (proposal.context != context or proposal.rule != rule or proposal.event_state_id != request.event_state_id
            or proposal.admission_ids != (request.admission_id,)
            or proposal.attribution != (Attribution(scope.strategy, '1'),)
            or proposal.preconfirmation_id != request.preconfirmation_id or proposal.source_release_id != request.source_release_id
            or proposal.expires_at > request.expires_at or saved['account_policy_sha256'] != coordinator.policy_sha):
        raise EvidenceError('EXIT_PROPOSAL_BINDING')
    prediction, admission = _predict(coordinator.store, request, original, scope, context, rule, binding, value['as_of'])
    row = coordinator._head()
    actual = coordinator._state(row) if state is None else state
    recomputed = _value(coordinator, actual, request, rule, binding, prediction, value['as_of'], own_intent_id=own_intent_id)
    if canonical(recomputed) != canonical(value):
        raise EvidenceError('EXIT_INVENTORY_OR_VALUATION_CHANGED_RECOMPUTE')
    if value['outcome'] != 'REDUCE_RESEARCH_CANDIDATE':
        raise EvidenceError('EXIT_JOINT_ECONOMICS_NOT_QUALIFIED')
    from .paper_coordinator import ACCOUNT_KEY
    return tuple(tuple(h) for h in admission['heads'])+(('COORDINATOR_EVENT', ACCOUNT_KEY, row['seq'] if row else 0),)


class PositionManager:
    def __init__(self, coordinator):
        self.coordinator, self.store = coordinator, coordinator.store

    def evaluate(self, record_id: str, request: ExitRequest):
        from .paper_coordinator import ACCOUNT_KEY, Proposal
        identity(record_id, maximum=60)
        store, coordinator = self.store, self.coordinator
        request_sha = digest(asdict(request))
        original, scope, context, rule, binding = _original(store, request)
        def prior(key):
            try:
                return store.get(key)
            except EvidenceError as exc:
                if str(exc) != 'EVIDENCE_MISSING':
                    raise
        old = prior(record_id)
        if old:
            if old['kind'] != 'MEASUREMENT' or old['body']['details'].get('version') != VERSION or old['body']['details'].get('request_sha256') != request_sha:
                raise EvidenceError('EXIT_REQUEST_ID_COLLISION')
            return old
        start = prior(record_id+':start')
        if start is None:
            head = coordinator._head()
            start = store.audit(record_id+':start', event_id=context.event_id, kind='MEASUREMENT',
                       details=dict(version=VERSION, request_sha256=request_sha, account_head_id=head['id'] if head else None),
                       evidence_ids=(request.admission_id,))
        if start['body']['details'].get('request_sha256') != request_sha:
            raise EvidenceError('EXIT_REQUEST_ID_COLLISION')
        cutoff = start['body']['recorded_at']; value = proposal = None; outcome = 'GATED'; heads = ()
        refs = [request.admission_id, start['id']]
        try:
            head = coordinator._head(); state = coordinator._state(head)
            if (head['id'] if head else None) != start['body']['details']['account_head_id']:
                raise EvidenceError('EXIT_ACCOUNT_CHANGED_DURING_EVALUATION')
            prediction, admission = _predict(store, request, original, scope, context, rule, binding, cutoff)
            event = EventRiskEngine(store).revalidate(request.event_state_id)
            if context.account_id != coordinator.policy.account_id or event['request']['context'] != asdict(context) or event['request']['binding'] != asdict(binding):
                raise EvidenceError('EXIT_EVENT_OR_ACCOUNT_BINDING')
            if not cutoff <= finite(store.clock()) < min(request.expires_at, admission['valid_until'], event['valid_until']):
                raise EvidenceError('EXIT_EVALUATION_EXPIRED')
            value = _value(coordinator, state, request, rule, binding, prediction, cutoff)
            value_id = record_id+':valuation'; saved = prior(value_id)
            if saved:
                if canonical(saved['body']['details']) != canonical(value):
                    raise EvidenceError('EXIT_PARTIAL_EVALUATION_CHANGED')
            else:
                store.audit(value_id, event_id=context.event_id, kind='MEASUREMENT', details=value,
                            evidence_ids=(request.book_id, request.admission_id))
            refs += [value_id, request.event_state_id]
            outcome, reason = value['outcome'], value['reasons'][0]
            if outcome == 'REDUCE_RESEARCH_CANDIDATE':
                proposed = Proposal(record_id+':proposal', digest(dict(exit=request_sha, inventory=value['position_management']['inventory_sha256'])),
                        context, rule, value_id, request.event_state_id, (Attribution(scope.strategy, '1'),),
                        min(request.expires_at, admission['valid_until'], event['valid_until']),
                        value['remaining_units_if_fully_sold'], (request.admission_id,), request.preconfirmation_id, request.source_release_id)
                # Evaluation may run inside the active event-queue claim. Order
                # admission requires that claim's later completed result; do not
                # create a circular requirement for completion during evaluation.
                heads = SafetyReductions(store).atomic_heads(context)
                event_row = store.get(request.event_state_id)
                heads += (('COORDINATOR_EVENT', event_row['event_id'], event_row['seq']),)
                heads += revalidate_exit(coordinator, proposed, value)
                heads += coordinator._current_book_heads(value, context.event_id)
                if any(event['safety']['flags'][key] for key in ('no_new_orders', 'manual_review', 'quarantined')):
                    raise EvidenceError('OPERATOR_SUPPRESSES_NEW_ORDER')
                pre = coordinator._preconfirmation(request.preconfirmation_id, strategies=(scope.strategy,),
                           context=context, rule=rule, binding=asdict(binding), admissions=(request.admission_id,))
                release = coordinator._source_release(request.source_release_id, strategies=(scope.strategy,),
                           context=context, rule=rule, binding=asdict(binding), admissions=(request.admission_id,),
                           event_state_id=request.event_state_id, value=value)
                for pin in (pre, release):
                    if pin is not None:
                        heads += tuple(tuple(h) for h in pin['heads'])
                proposal = asdict(proposed)
            else:
                heads = (('COORDINATOR_EVENT', ACCOUNT_KEY, head['seq'] if head else 0),)
        except EvidenceError as exc:
            reason, outcome, proposal, heads = str(exc), 'GATED', None, ()
        unique = {}
        for kind, event, seq in heads:
            if (kind, event) in unique and unique[(kind, event)] != seq:
                raise EvidenceError('EXIT_STATE_CHANGED_RECOMPUTE')
            unique[(kind, event)] = seq
        return store.audit(record_id, event_id=context.event_id, kind='MEASUREMENT',
               details=dict(version=VERSION, request=asdict(request), request_sha256=request_sha,
                            evaluation_started_at=cutoff, outcome=outcome, reason=reason, valuation=value,
                            proposal=proposal, actual_inventory_changed=False, financial_authority=False),
               evidence_ids=tuple(refs), expected_heads=tuple((k,e,n) for (k,e),n in unique.items()))

    def proposal(self, evaluation_id):
        from .paper_coordinator import Proposal
        row = self.store.get(evaluation_id); d = row['body'].get('details', {})
        if row['kind'] != 'MEASUREMENT' or d.get('version') != VERSION or not d.get('proposal'):
            raise EvidenceError('EXIT_HAS_NO_ECONOMIC_PROPOSAL')
        p = dict(d['proposal'])
        p.update(context=EventContext(**p['context']), rule=RuleFingerprint(**p['rule']),
                 attribution=tuple(Attribution(**a) for a in p['attribution']), admission_ids=tuple(p['admission_ids']))
        return Proposal(**p)
