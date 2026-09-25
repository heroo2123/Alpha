"""Joint-outcome basket research with explicit adverse partial-fill exposure.

Conditional full-fill payout floors are not locked executable profits. This
module writes measurements only; atomic multi-leg account admission is separate.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal
import math

from .evidence import EvidenceError, EvidenceStore, ReleaseBinding, digest, finite, identity
from .probability import BucketPrediction, _partition
from .rules import RuleFingerprint, RuleGuard
from .scenario_risk import (Attribution, PendingOrder, Position, event_scenarios, incremental_scenarios,
                            number, precise)
from .valuation import (CostComponent, ValuationPolicy, ENTRY_RISKS, PAYOUT, _book, _costs,
                        _cost_scope_reasons, _prediction, contract_target)


VERSION = 'alpha_v11_joint_basket_valuation_v1'
STRATEGIES = {'CROSS_TEMP_RELATIVE_VALUE', 'STRUCTURAL'}


@dataclass(frozen=True)
class BasketLeg:
    market_id: str
    side: str
    units: str
    book_id: str
    costs: tuple[CostComponent, ...]

    def __post_init__(self):
        identity(self.market_id); identity(self.book_id)
        if self.side not in {'YES', 'NO'} or not 0 < number(self.units) <= 1_000_000:
            raise EvidenceError('BASKET_LEG_TARGET_OR_SIZE')
        if type(self.costs) is not tuple or len(self.costs) > 16 or any(not isinstance(c, CostComponent) for c in self.costs):
            raise EvidenceError('BASKET_COST_INPUT_BOUND')


@dataclass(frozen=True)
class BasketPolicy:
    valuation: ValuationPolicy
    maximum_book_skew_seconds: float
    maximum_rule_age_seconds: float
    minimum_total_ev: str

    def __post_init__(self):
        if (not isinstance(self.valuation, ValuationPolicy)
                or not 0 <= finite(self.maximum_book_skew_seconds) <= self.valuation.max_book_age_seconds
                or not 0 < finite(self.maximum_rule_age_seconds) <= 86400):
            raise EvidenceError('BASKET_POLICY_FRESHNESS_BOUND')
        number(self.minimum_total_ev)


@precise
def analyze_basket(store: EvidenceStore, record_id: str, *, rule: RuleFingerprint, prediction: BucketPrediction,
                   binding: ReleaseBinding, strategy: str, account_id: str, legs: tuple[BasketLeg, ...],
                   policy: BasketPolicy, positions: tuple[Position, ...] = (), pending: tuple[PendingOrder, ...] = (),
                   realized_event_pnl: str = '0') -> dict:
    _, request = _basket_request(record_id, rule, prediction, binding, strategy, account_id,
                                 legs, policy, positions, pending, realized_event_pnl)
    try:
        previous = store.get(record_id)
    except EvidenceError as exc:
        if str(exc) != 'EVIDENCE_MISSING':
            raise
    else:
        d = previous['body'].get('details', {})
        if previous['kind'] != 'MEASUREMENT' or d.get('version') != VERSION or d.get('request_sha256') != digest(request):
            raise EvidenceError('BASKET_REQUEST_ID_COLLISION')
        return previous
    details = basket_details(store, record_id, rule=rule, prediction=prediction, binding=binding,
        strategy=strategy, account_id=account_id, legs=legs, policy=policy, positions=positions,
        pending=pending, realized_event_pnl=realized_event_pnl)
    return store.audit(record_id, event_id=rule.payload['event_id'], kind='MEASUREMENT', details=details,
                       evidence_ids=tuple(dict.fromkeys(leg.book_id for leg in legs)),
                       expected_heads=tuple(details['source_heads']))


def _basket_request(record_id, rule, prediction, binding, strategy, account_id,
                    legs, policy, positions, pending, realized_event_pnl):
    identity(record_id, maximum=80); identity(account_id)
    if (strategy not in STRATEGIES or type(legs) is not tuple or not 1 <= len(legs) <= 32
            or any(not isinstance(leg, BasketLeg) for leg in legs)):
        raise EvidenceError('BASKET_STRATEGY_OR_LEG_BOUND')
    targets = [contract_target(rule, leg.market_id, leg.side) for leg in legs]
    if len({t['token_id'] for t in targets}) != len(legs):
        raise EvidenceError('BASKET_DUPLICATE_ECONOMIC_LEG')
    request = dict(rule=asdict(rule), prediction_sha256=prediction.sha256, binding=asdict(binding), strategy=strategy,
                   account_id=account_id, legs=[asdict(l) for l in legs], policy=asdict(policy),
                   positions=[asdict(p) for p in positions], pending=[asdict(p) for p in pending],
                   realized_event_pnl=realized_event_pnl)
    return targets, request


@precise
def basket_details(store, record_id: str, *, rule: RuleFingerprint, prediction: BucketPrediction,
                   binding: ReleaseBinding, strategy: str, account_id: str, legs: tuple[BasketLeg, ...],
                   policy: BasketPolicy, positions: tuple[Position, ...] = (), pending: tuple[PendingOrder, ...] = (),
                   realized_event_pnl: str = '0') -> dict:
    """Shared calculation; no journal lookup/return, audit or account command.

    Historical callers supply an original receipt/time view. RuleGuard is only
    the archived data gate here, never renewed strategy certification/admission.
    """
    targets, request = _basket_request(record_id, rule, prediction, binding, strategy, account_id,
                                      legs, policy, positions, pending, realized_event_pnl)
    now = finite(store.clock()); event = rule.payload['event_id']
    heads = []
    for kind in ('RULE_STATE', 'BOOK'):
        head = store.latest(kind=kind, event_id=event)
        heads.append((kind, event, head['seq'] if head else 0))
    rules = RuleGuard(store).revalidate(event, rule.sha256, max_age_seconds=policy.maximum_rule_age_seconds)
    if not rules['passed']:
        raise EvidenceError(rules['reason'])
    partition = _partition(rule); vector = prediction.payload['buckets']
    probability = {b['market_id']:Decimal(str(finite(b['point']))) for b in vector}
    if (len(probability) != len(vector) or set(probability) != {b['market_id'] for b in partition}
            or any(not 0 <= p <= 1 for p in probability.values())
            or not math.isclose(float(sum(probability.values())), 1., rel_tol=0, abs_tol=1e-12)):
        raise EvidenceError('BASKET_COHERENT_WHOLE_EVENT_VECTOR_REQUIRED')
    rows, reasons, clocks = [], [], []
    for index, (leg, target) in enumerate(zip(legs, targets)):
        qty = number(leg.units)
        model, _ = _prediction(rule, prediction, binding, target, now, policy.valuation)
        depth = _book(store, leg.book_id, rule, target, qty, 'ACQUIRE', now, policy.valuation)
        costs = _costs(leg.costs, horizon=PAYOUT, required=ENTRY_RISKS,
                       already=frozenset({'MODEL_UNCERTAINTY', 'DEPTH_WALK'}))
        failures = _cost_scope_reasons(leg.costs, now, depth)
        if qty > number(policy.valuation.maximum_units):
            failures.append('REQUEST_EXCEEDS_APPROVED_SIZE_SKIP')
        if depth['status'] != 'MEASURED':
            failures.append(depth['reason'])
        if not costs['complete']:
            failures.append('UNKNOWN_OR_MISSING_COST_COVERAGE')
        row = store.get(leg.book_id); b = row['body']
        latest = store.latest_source(kind='BOOK', event_id=event, provider=b['provider'], source_identity=b['source_identity'])
        if not latest or latest['id'] != leg.book_id:
            failures.append('CURRENT_EXACT_BASKET_BOOK_REQUIRED')
        if b['observed_at'] is not None:
            clocks.append(b['observed_at'])
        all_in = bound = None
        if not failures:
            all_in = number(depth['gross_value'])+qty*number(costs['known_total_per_share'])
            bound = number(depth['worst_consumed_price'])+number(costs['known_total_per_share'])
            if not 0 <= bound <= 2:
                failures.append('BASKET_COLLATERAL_BOUND')
        point = Decimal(str(model['interval']['point']))
        rows.append(dict(index=index, target=target, units=leg.units, book=depth, costs=costs,
                         all_in_acquisition_cost=str(all_in) if all_in is not None else None,
                         pending_unit_collateral_bound=str(bound) if bound is not None else None,
                         point_payout_per_share=str(point), conservative_single_leg_payout_per_share='0',
                         point_ev_total=str(qty*point-all_in) if all_in is not None else None,
                         reasons=sorted(set(failures))))
        reasons.extend('LEG_'+str(index)+':'+r for r in failures)
    if clocks and max(clocks)-min(clocks) > policy.maximum_book_skew_seconds:
        reasons.append('BASKET_BOOK_OBSERVATION_SKEW')
    full = partial = None
    full_cost = point_payout = floor = conservative_ev = None
    constant = False
    if not reasons:
        attribution = (Attribution(strategy, '1'),)
        held = tuple(Position(record_id+':full:'+str(i), row['target']['token_id'], row['units'],
                              row['all_in_acquisition_cost'], attribution) for i, row in enumerate(rows))
        orders = tuple(PendingOrder(record_id+':pending:'+str(i), row['target']['token_id'], 'BUY', row['units'],
                                   row['pending_unit_collateral_bound'], attribution) for i, row in enumerate(rows))
        full = event_scenarios(rule, execution_namespace=store.namespace, account_id=account_id, positions=held, pending=())
        partial = incremental_scenarios(rule, execution_namespace=store.namespace, account_id=account_id,
                     positions=positions, pending=pending, proposed=orders, realized_event_pnl=realized_event_pnl)
        full_cost = sum(number(row['all_in_acquisition_cost']) for row in rows)
        payouts = [number(row['held_payout']) for row in full['outcomes']]
        floor = min(payouts); constant = len(set(payouts)) == 1
        point_payout = sum(probability[row['market_id']]*number(row['held_payout']) for row in full['outcomes'])
        # Under the current vacuous coherent simplex, the lower expectation of
        # a joint payout is its minimum outcome, not a sum of normalized bounds.
        conservative_ev = floor-full_cost
    eligible_economics = not reasons and conservative_ev > number(policy.minimum_total_ev)
    details = dict(version=VERSION, request=request, request_sha256=digest(request), strategy=strategy,
                   as_of=now, valuation_type='JOINT_SETTLEMENT_RESEARCH', binding=asdict(binding),
                   prediction=prediction.payload, legs=rows, source_heads=heads, full_fill_scenarios=full,
                   adverse_partial_fill_scenarios=partial, full_fill_all_in_cost=str(full_cost) if full_cost is not None else None,
                   point_expected_payout=str(point_payout) if point_payout is not None else None,
                   conditional_full_fill_payout_floor=str(floor) if floor is not None else None,
                   conservative_full_fill_ev_total=str(conservative_ev) if conservative_ev is not None else None,
                   constant_payout_after_all_legs_filled=constant,
                   bound_method='MINIMUM_JOINT_OUTCOME_UNDER_VACUOUS_SIMPLEX',
                   full_fill_economics='CANDIDATE' if eligible_economics else 'GATED' if reasons else 'REJECT',
                   outcome='GATED', reasons=sorted(set(reasons)) or
                       (['ATOMIC_MULTILEG_ACCOUNT_ADMISSION_REQUIRED'] if eligible_economics else
                        ['CONSERVATIVE_JOINT_EV_NOT_ABOVE_THRESHOLD']),
                   proposal=None, locked_executable_profit=None, trading_pnl=None, redeemed_collateral='0',
                   rewards_in_trading_ev=False, financial_authority=False)
    return details
