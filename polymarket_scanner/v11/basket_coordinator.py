"""Atomic basket preparation for the existing shared nonfinancial account.

Joint EV is never relabeled as profitable independent legs. Admission reserves
every leg, while actual fills and terminal reconciliation remain leg-specific.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal

from .basket_valuation import VERSION as VALUE_VERSION, STRATEGIES
from .certification import CapabilityScope
from .event_queue import admission_heads
from .event_risk import EventContext, EventRiskEngine, SafetyReductions
from .evidence import EvidenceError, canonical, digest, finite, identity
from .model_artifacts import predict_with_bundle
from .model_registry import ActiveModelRegistry
from .probability import FINAL_EXTREME
from .rules import RuleFingerprint
from .scenario_risk import Attribution, _attribution, number, precise
from .strategy_admission import StrategyAdmission
from .valuation import contract_target


@dataclass(frozen=True)
class BasketProposal:
    proposal_id: str
    thesis_id: str
    context: EventContext
    rule: RuleFingerprint
    valuation_id: str
    event_state_id: str
    attribution: tuple[Attribution, ...]
    expires_at: float
    desired_positions: tuple[tuple[str, str], ...]
    admission_ids: tuple[str, ...]

    def __post_init__(self):
        identity(self.proposal_id, maximum=80)
        for key in (self.thesis_id, self.valuation_id, self.event_state_id):
            identity(key)
        _attribution(self.attribution); finite(self.expires_at)
        if len(self.attribution) != 1 or self.attribution[0].strategy not in STRATEGIES:
            raise EvidenceError('BASKET_EXACT_STRATEGY_ATTRIBUTION_REQUIRED')
        if type(self.admission_ids) is not tuple or len(self.admission_ids) != 1:
            raise EvidenceError('BASKET_SCOPED_ADMISSION_REQUIRED')
        identity(self.admission_ids[0])
        if (type(self.desired_positions) is not tuple or not 1 <= len(self.desired_positions) <= 32
                or any(type(pair) is not tuple or len(pair) != 2 for pair in self.desired_positions)):
            raise EvidenceError('BASKET_DESIRED_POSITION_BOUND')
        for token, units in self.desired_positions:
            identity(token)
            if not 0 < number(units) <= 1_000_000:
                raise EvidenceError('BASKET_DESIRED_POSITION_BOUND')
        if len(dict(self.desired_positions)) != len(self.desired_positions):
            raise EvidenceError('BASKET_DUPLICATE_DESIRED_TOKEN')
        if self.context.event_id != self.rule.payload['event_id'] or self.context.station_id != self.rule.payload['station']:
            raise EvidenceError('PROPOSAL_RULE_CONTEXT')

    @classmethod
    def from_dict(cls, raw):
        p = dict(raw)
        p.update(context=EventContext(**p['context']), rule=RuleFingerprint(**p['rule']),
                 attribution=tuple(Attribution(**a) for a in p['attribution']),
                 admission_ids=tuple(p['admission_ids']),
                 desired_positions=tuple(tuple(pair) for pair in p['desired_positions']))
        return cls(**p)


def _model(store, proposal, value, admission):
    # Reproduce the entire vector from leased archived inputs and the protected
    # bundle. A caller-supplied probability object cannot become account authority.
    from .strategy_pipeline import _model_inputs
    original = store.get(proposal.admission_ids[0])['body']['details']['request']
    scope = CapabilityScope(**original['scope'])
    leases = [s for s in original['source_leases'] if s['role'] == 'MODEL']
    if not leases:
        raise EvidenceError('BASKET_ARCHIVED_MODEL_LEASE_REQUIRED')
    cutoff = finite(value['prediction']['as_of'])
    inputs = _model_inputs(store, proposal.rule, tuple(s['evidence_id'] for s in leases), cutoff, target=FINAL_EXTREME)
    mode = 'V11_PAPER' if original['stage'] == 'PAPER' else 'V11_SHADOW'
    model = ActiveModelRegistry().pin(scope_key=scope.key, mode=mode)
    if model.state_sha256 != admission['model_state_sha256'] or model.bundle.sha256 != value['binding']['bundle_sha256']:
        raise EvidenceError('BASKET_MODEL_CHANGED_RECOMPUTE')
    prediction = predict_with_bundle(model.bundle, proposal.rule, inputs, as_of=cutoff,
                       max_source_age_seconds=min(s['maximum_age_seconds'] for s in leases))
    if canonical(prediction.payload) != canonical(value['prediction']):
        raise EvidenceError('BASKET_PREDICTION_NOT_REPRODUCED_FROM_PROTECTED_INPUTS')
    if not ActiveModelRegistry().revalidate(model)['passed']:
        raise EvidenceError('BASKET_MODEL_CHANGED_RECOMPUTE')


@precise
def prepare(coordinator, proposal: BasketProposal, now: float):
    store, context = coordinator.store, proposal.context
    if context.account_id != coordinator.policy.account_id:
        raise EvidenceError('PROPOSAL_ACCOUNT_MISMATCH')
    membership = next((m for m in coordinator.correlation.memberships if m.station == context.station_id), None)
    if (membership is None or membership.city != context.city_id
            or membership.metadata_fingerprint != proposal.rule.payload['metadata_fingerprint']):
        raise EvidenceError('PROPOSAL_CITY_METADATA_SCOPE_MISMATCH')
    heads = SafetyReductions(store).atomic_heads(context)
    queue = admission_heads(store, event_id=context.event_id, valuation_id=proposal.valuation_id)
    heads += tuple(tuple(h) for h in queue['heads'])
    event = EventRiskEngine(store).revalidate(proposal.event_state_id)
    event_row = store.get(proposal.event_state_id)
    heads += (('COORDINATOR_EVENT', event_row['event_id'], event_row['seq']),)
    row = store.get(proposal.valuation_id); value = row['body'].get('details', {})
    strategy = proposal.attribution[0].strategy
    if (row['kind'] != 'MEASUREMENT' or row['event_id'] != context.event_id or value.get('version') != VALUE_VERSION
            or value['request']['rule'] != asdict(proposal.rule) or value['request']['account_id'] != context.account_id
            or value['request_sha256'] != digest(value['request']) or value['strategy'] != strategy
            or value['binding'] != event['request']['binding'] or value['binding']['rule_fingerprint'] != proposal.rule.sha256
            or event['request']['context'] != asdict(context)
            or value['request']['policy']['valuation']['collateral_asset'] != coordinator.policy.collateral_asset):
        raise EvidenceError('BASKET_VALUATION_BINDING')
    if value['full_fill_economics'] != 'CANDIDATE' or value['valuation_type'] != 'JOINT_SETTLEMENT_RESEARCH':
        raise EvidenceError('BASKET_JOINT_ECONOMICS_NOT_QUALIFIED')
    admission = StrategyAdmission(store).revalidate(proposal.admission_ids[0], context=context, rule=proposal.rule,
                             binding=value['binding'], strategies=(strategy,))
    heads += tuple(tuple(h) for h in admission['heads'])
    _model(store, proposal, value, admission)
    # Replay the valuation inputs rather than accepting mutable-looking summary
    # fields as a proof. Immutable records alone do not certify their producer.
    from .basket_valuation import BasketLeg, BasketPolicy, analyze_basket
    from .probability import BucketPrediction
    from .evidence import ReleaseBinding
    from .valuation import CostComponent, ValuationPolicy
    from .scenario_risk import Position, PendingOrder
    request = value['request']
    legs = tuple(BasketLeg(**{**leg, 'costs':tuple(CostComponent(**{**c, 'covers':tuple(c['covers'])})
                                               for c in leg['costs'])}) for leg in request['legs'])
    policy = BasketPolicy(**{**request['policy'], 'valuation':ValuationPolicy(**request['policy']['valuation'])})
    # A computation-only sink uses the real store for reads and checks the
    # resulting measurement without appending duplicate research records.
    class Check:
        def __getattr__(self, name):
            return getattr(store, name)

        def get(self, key):
            if key == '__basket_recompute__':
                raise EvidenceError('EVIDENCE_MISSING')
            return store.get(key)

        def audit(self, key, **kw):
            return {'body': {'details': kw['details']}}
    prediction = BucketPrediction(canonical(value['prediction']), digest(value['prediction']))
    def attrs(raw):
        return {**raw, 'attribution':tuple(Attribution(**a) for a in raw['attribution'])}
    recomputed = analyze_basket(Check(), '__basket_recompute__', rule=proposal.rule, prediction=prediction,
            binding=ReleaseBinding(**value['binding']), strategy=strategy, account_id=context.account_id,
            legs=legs, policy=policy, positions=tuple(Position(**attrs(p)) for p in request['positions']),
            pending=tuple(PendingOrder(**attrs(p)) for p in request['pending']),
            realized_event_pnl=request['realized_event_pnl'])['body']['details']
    for key in ('request_sha256', 'legs', 'full_fill_all_in_cost', 'conditional_full_fill_payout_floor',
                'conservative_full_fill_ev_total', 'full_fill_economics'):
        if canonical(recomputed[key]) != canonical(value[key]):
            raise EvidenceError('BASKET_VALUE_CHANGED_OR_NOT_REPRODUCIBLE')
    heads += tuple(tuple(h) for h in recomputed['source_heads'])
    flags = event['safety']['flags']
    if flags['no_new_orders'] or flags['manual_review'] or flags['quarantined'] or flags['reduce_only']:
        raise EvidenceError('OPERATOR_SUPPRESSES_NEW_ORDER')
    if not event['ordinary_new_risk_research_allowed']:
        raise EvidenceError('EVENT_OR_OPERATOR_SUPPRESSES_NEW_RISK')
    desired = dict(proposal.desired_positions)
    if set(desired) != {leg['target']['token_id'] for leg in value['legs']}:
        raise EvidenceError('BASKET_DESIRED_TOKEN_SET_MISMATCH')
    vp = value['request']['policy']['valuation']
    expiry = min(proposal.expires_at, event['valid_until'], admission['valid_until'],
                 value['as_of']+coordinator.policy.maximum_intent_lifetime_seconds*event['guard']['lifetime_multiplier'],
                 value['prediction']['as_of']+vp['max_prediction_age_seconds'])
    if queue['valid_until'] is not None:
        expiry = min(expiry, queue['valid_until'])
    candidates = []; capital = Decimal(0); total_units = Decimal(0)
    for leg in value['legs']:
        target = contract_target(proposal.rule, leg['target']['market_id'], leg['target']['side'])
        if leg['target'] != target or not leg['costs']['complete']:
            raise EvidenceError('BASKET_EXACT_TARGET_AND_COST_REQUIRED')
        heads += coordinator._current_book_heads(dict(book=leg['book'], policy=vp), context.event_id)
        book = store.get(leg['book']['book_id'])['body']; qty = number(leg['units'])
        expiry = min(expiry, book['observed_at']+vp['max_book_age_seconds'], book['received_at']+vp['max_book_age_seconds'])
        for component in leg['costs']['components']:
            if component['valid_until'] is not None:
                expiry = min(expiry, component['valid_until'])
        if qty > number(coordinator.limits.max_position_units)*Decimal(str(event['guard']['size_multiplier']))*Decimal(str(admission['model_size_multiplier'])):
            raise EvidenceError('EVENT_STATE_SIZE_LIMIT')
        price = number(leg['book']['worst_consumed_price'])
        depth = sum(number(l['size']) for l in book['payload']['asks'] if number(l['price']) <= price)
        if depth < qty*Decimal(str(event['guard']['liquidity_multiplier'])):
            raise EvidenceError('STATE_ADJUSTED_LIQUIDITY_INSUFFICIENT')
        reserve = qty*number(leg['pending_unit_collateral_bound'])
        capital += reserve; total_units += qty
        candidates.append(dict(proposal_id=proposal.proposal_id+':leg:'+str(leg['index']), basket_id=proposal.proposal_id,
                thesis_id=proposal.thesis_id, desired_total_units=desired[target['token_id']], event_id=context.event_id,
                token_id=target['token_id'], target=target, direction='BUY', units=leg['units'], filled_units='0',
                unit_collateral_bound=leg['pending_unit_collateral_bound'], attribution=[asdict(a) for a in proposal.attribution],
                valuation_id=proposal.valuation_id, event_state_id=proposal.event_state_id,
                admission_ids=list(proposal.admission_ids), binding=value['binding'],
                event_queue_completion_id=queue['completion_id'], rule_fingerprint=proposal.rule.sha256,
                conservative_ev_total=None, joint_ev_only=True, capital_at_risk=str(reserve),
                status='RESERVED', cancel_requested=False, financial_authority=False))
    # A sweep's displayed average can be better than its permitted limit. The
    # account cannot promise joint economics at an execution price it did not cap.
    bounded_ev = number(value['conditional_full_fill_payout_floor'])-capital
    threshold = max(number(policy.minimum_total_ev), total_units*(coordinator.policy_amount('minimum_ev_per_share')+
                    Decimal(str(event['guard']['additional_ev_per_share']))))
    if bounded_ev <= threshold:
        raise EvidenceError('BASKET_LIMIT_PRICE_JOINT_EV_NOT_ABOVE_THRESHOLD')
    if capital > coordinator.policy_amount('per_intent_cash_limit'):
        raise EvidenceError('BASKET_AGGREGATE_INTENT_CASH_LIMIT')
    if not value['as_of'] <= finite(store.clock()) < expiry:
        raise EvidenceError('PROPOSAL_EVIDENCE_OR_SIGNAL_EXPIRED')
    for candidate in candidates:
        candidate['expires_at'] = expiry
    # Recheck authority after reproduction. Head disagreements are rejected by
    # the common batch/transition guard merge before the atomic account append.
    after = StrategyAdmission(store).revalidate(proposal.admission_ids[0], context=context, rule=proposal.rule,
                             binding=value['binding'], strategies=(strategy,))
    heads += tuple(tuple(h) for h in after['heads'])
    return dict(proposal_id=proposal.proposal_id, thesis_id=proposal.thesis_id, event_id=context.event_id,
                direction='BUY', basket_legs=candidates, conservative_ev_total=str(bounded_ev),
                capital_at_risk=str(capital), joint_ev_only=True, financial_authority=False), heads


def submission_heads(coordinator, state, intent):
    group = state.get('baskets', {}).get(intent['basket_id'])
    if group is None or intent['proposal_id'] not in group['intent_ids']:
        raise EvidenceError('BASKET_GROUP_RECONCILIATION_REQUIRED')
    for key in group['intent_ids']:
        member = state['intents'].get(key)
        if member is None or member['status'] in {'UNKNOWN', 'CANCELED', 'EXPIRED', 'REJECTED', 'CANCEL_REQUESTED'}:
            raise EvidenceError('BASKET_PARTIAL_OR_AMBIGUOUS_GROUP_REQUIRES_REPLAN')
    prepared, heads = prepare(coordinator, BasketProposal.from_dict(group['proposal']), finite(coordinator.store.clock()))
    member = next(p for p in prepared['basket_legs'] if p['proposal_id'] == intent['proposal_id'])
    if (member['event_queue_completion_id'] != intent['event_queue_completion_id']
            or finite(coordinator.store.clock()) >= intent['expires_at'] or state['faults']):
        raise EvidenceError('SUBMISSION_PIN_EXPIRED_OR_SUPPRESSED')
    return heads
