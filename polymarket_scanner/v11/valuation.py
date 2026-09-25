"""Exact-target, size-aware research economics; never an order/fill ledger.

Depth walk is charged once. Every additional risk/cost has exactly one declared
owner. Unknown fees/reserves gate economics; a quoted sale is not realized P&L.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal

from ..production.fees import fee_requirement
from .evidence import EvidenceError, EvidenceStore, ReleaseBinding, digest, finite, identity, sha
from .measurement import amount, executable_depth
from .probability import BucketPrediction, FINAL_EXTREME
from .rules import RuleFingerprint


VERSION = 'alpha_v11_executable_ev_v1'
PAYOUT = 'FINAL_CONTRACT_PAYOUT'
SALE = 'CURRENT_EXECUTABLE_SALE'
ENTRY_RISKS = frozenset({'ACQUISITION_FEES', 'POST_SNAPSHOT_SLIPPAGE', 'EXECUTION_UNCERTAINTY',
                         'SETTLEMENT_REVISION', 'SOURCE_FALLBACK', 'OPPORTUNITY_RISK', 'REDEMPTION_COST'})
HOLD_RISKS = frozenset({'SETTLEMENT_REVISION', 'SOURCE_FALLBACK', 'OPPORTUNITY_RISK', 'REDEMPTION_COST'})
SALE_RISKS = frozenset({'EXIT_FEES', 'POST_SNAPSHOT_SLIPPAGE', 'EXECUTION_UNCERTAINTY'})


@dataclass(frozen=True)
class CostComponent:
    name: str
    horizon: str
    per_share: str | None
    covers: tuple[str, ...]
    assumption_sha256: str
    priced_buy_limit: str | None = None
    post_only: bool | None = None
    valid_until: float | None = None

    def __post_init__(self):
        identity(self.name)
        sha(self.assumption_sha256)
        if self.horizon not in {PAYOUT, SALE}:
            raise EvidenceError('COST_HORIZON_INVALID')
        if (type(self.covers) is not tuple or not self.covers or len(set(self.covers)) != len(self.covers)
                or not set(self.covers) <= ENTRY_RISKS | SALE_RISKS | {'MODEL_UNCERTAINTY', 'DEPTH_WALK'}):
            raise EvidenceError('COST_COVERAGE_INVALID')
        if self.per_share is not None:
            if not isinstance(self.per_share, str) or amount(self.per_share) > 1_000_000:
                raise EvidenceError('COST_AMOUNT_INVALID')
        if self.priced_buy_limit is not None:
            if (self.covers != ('ACQUISITION_FEES',) or type(self.post_only) is not bool
                    or not 0 < amount(self.priced_buy_limit) < 1 or self.valid_until is None):
                raise EvidenceError('FEE_PRICING_SCOPE_INVALID')
        elif self.post_only is not None:
            raise EvidenceError('FEE_PRICING_SCOPE_INVALID')
        if self.valid_until is not None:
            finite(self.valid_until)


@dataclass(frozen=True)
class ValuationPolicy:
    policy_version: str
    collateral_asset: str
    max_book_age_seconds: float
    max_prediction_age_seconds: float
    minimum_ev_per_share: str
    maximum_units: str

    def __post_init__(self):
        identity(self.policy_version)
        identity(self.collateral_asset)
        if finite(self.max_book_age_seconds) <= 0 or finite(self.max_prediction_age_seconds) <= 0:
            raise EvidenceError('VALUATION_AGE_BOUND')
        if amount(self.minimum_ev_per_share) > 1 or not 0 < amount(self.maximum_units) <= 1_000_000:
            raise EvidenceError('VALUATION_POLICY_BOUND')


def contract_target(rule: RuleFingerprint, market_id: str, side: str) -> dict:
    if side not in {'YES', 'NO'}:
        raise EvidenceError('CONTRACT_SIDE_INVALID')
    rows = [b for b in rule.payload['partition'] if b['market_id'] == market_id]
    if len(rows) != 1:
        raise EvidenceError('EXACT_MARKET_REQUIRED')
    b = rows[0]
    return dict(market_id=market_id, condition_id=b['condition_id'],
                token_id=b['yes_token' if side == 'YES' else 'no_token'], side=side)


def buy_fee_cost(snapshot: dict, *, target: dict, as_of: float, max_age_seconds: float,
                 limit_price: str, post_only: bool) -> CostComponent:
    """Reuse the existing explicit BUY fee policy, with target/freshness checks.

    The published schedule remains conditional, not a signed venue ceiling.
    No SELL extrapolation is made. Caller must pin/revalidate the source snapshot
    and keep the submission limit at or below the priced BUY limit.
    """
    at, ttl = finite(as_of), finite(max_age_seconds)
    if (ttl <= 0 or not isinstance(snapshot, dict)
            or snapshot.get('token') != target['token_id']
            or snapshot.get('condition') != target['condition_id']):
        raise EvidenceError('FEE_TARGET_MISMATCH')
    received = finite(snapshot.get('received_at'))
    if not 0 <= at-received <= ttl:
        raise EvidenceError('FEE_EVIDENCE_STALE_OR_FUTURE')
    fee = fee_requirement(snapshot, limit_price, post_only)
    return CostComponent('EXISTING_BUY_FEE_POLICY', PAYOUT, str(fee), ('ACQUISITION_FEES',),
                         digest({'snapshot': snapshot, 'priced_buy_limit': limit_price,
                                 'post_only': post_only, 'as_of': at, 'ttl': ttl}),
                         limit_price, post_only, received+ttl)


def _costs(costs: tuple[CostComponent, ...], *, horizon: str,
           required: frozenset[str], already: frozenset[str]) -> dict:
    if type(costs) is not tuple or len(costs) > 16:
        raise EvidenceError('COST_COUNT_BOUND')
    coverage, names, total, unknown = set(already), set(), Decimal(0), []
    rows = []
    for c in costs:
        if not isinstance(c, CostComponent) or c.horizon != horizon or c.name in names:
            raise EvidenceError('COST_HORIZON_OR_IDENTITY_MISMATCH')
        if set(c.covers) & coverage:
            raise EvidenceError('COST_RISK_DOUBLE_COUNT')
        if not set(c.covers) <= required:
            raise EvidenceError('COST_RISK_UNEXPECTED')
        coverage.update(c.covers)
        names.add(c.name)
        rows.append(asdict(c))
        if c.per_share is None:
            unknown.extend(c.covers)
        else:
            total += amount(c.per_share)
    missing = required - coverage
    return dict(components=rows, already_included=sorted(already), missing=sorted(missing),
                unknown=sorted(unknown), known_total_per_share=str(total),
                complete=not missing and not unknown,
                provenance_status='DECLARED_ASSUMPTIONS_NOT_INDEPENDENT_ATTESTATION')


def _prediction(rule: RuleFingerprint, prediction: BucketPrediction, binding: ReleaseBinding,
                target: dict, at: float, policy: ValuationPolicy) -> tuple[dict, Decimal]:
    if not isinstance(binding, ReleaseBinding):
        raise EvidenceError('RELEASE_BINDING_REQUIRED')
    p = prediction.payload
    if (p['event_id'] != rule.payload['event_id'] or p['rule_fingerprint'] != rule.sha256
            or binding.rule_fingerprint != rule.sha256 or p['bundle_sha256'] != binding.bundle_sha256):
        raise EvidenceError('VALUATION_MODEL_RULE_BINDING')
    if not 0 <= at-finite(p['as_of']) <= policy.max_prediction_age_seconds:
        raise EvidenceError('PREDICTION_STALE_OR_FUTURE')
    interval = prediction.binary(target['market_id'], target['side'], required_target=FINAL_EXTREME)
    row = next(b for b in p['buckets'] if b['market_id'] == target['market_id'])
    if row['yes_token' if target['side'] == 'YES' else 'no_token'] != target['token_id']:
        raise EvidenceError('PREDICTION_TOKEN_MISMATCH')
    # The implemented prediction family has vacuous model bounds. Revision and
    # fallback are separate declared reserves, never silently assumed covered.
    if p['uncertainty_method'] != 'VACUOUS_BOUNDS_NO_CALIBRATION_EVIDENCE':
        raise EvidenceError('UNSUPPORTED_BOUND_COVERAGE_REQUIRES_REVIEW')
    if interval.lower != 0 or interval.upper != 1 or p['calibration_status'] != 'UNCALIBRATED':
        raise EvidenceError('VACUOUS_BOUND_CONTRACT_VIOLATION')
    return dict(prediction_sha256=prediction.sha256, prediction=p, interval=asdict(interval),
                probability_covered_risks=['MODEL_UNCERTAINTY'],
                calibration_status=p['calibration_status']), amount(interval.lower)


def _book(store: EvidenceStore, book_id: str, rule: RuleFingerprint, target: dict,
          units: Decimal, direction: str, at: float, policy: ValuationPolicy) -> dict:
    row = store.get(book_id)
    body = row['body']
    if row['kind'] != 'BOOK' or row['event_id'] != rule.payload['event_id']:
        raise EvidenceError('VALUATION_BOOK_IDENTITY')
    p = body['payload']
    if (any(p.get(k) != v for k, v in target.items()) or p.get('rule_fingerprint') != rule.sha256
            or p.get('collateral_asset') != policy.collateral_asset):
        raise EvidenceError('VALUATION_BOOK_TARGET_MISMATCH')
    result = dict(book_id=book_id, book_sha256=row['sha256'], evidence_class=body['evidence_class'],
                  received_at=body['received_at'], observed_at=body['observed_at'], status='GATED',
                  measurement_class='VISIBLE_DEPTH_ESTIMATE_NOT_EXECUTION', gross_value=None,
                  visible_units=None, full_depth=False)
    observed = body['observed_at']
    if (body['evidence_class'] == 'HISTORICAL_AVAILABILITY_UNKNOWN' or observed is None
            or not 0 <= at-observed <= policy.max_book_age_seconds
            or not 0 <= at-body['available_at'] <= policy.max_book_age_seconds):
        return dict(result, reason='BOOK_STALE_OR_NONCAUSAL')
    if p.get('stream_healthy') is not True:
        return dict(result, reason='BOOK_STREAM_UNSYNCHRONIZED')
    if not p.get('bids') or not p.get('asks'):
        return dict(result, reason='BOOK_SIDE_MISSING')
    # Parse both sides and reject malformed/crossed books even when only buying.
    buy = executable_depth(p['asks'], units, direction='ACQUIRE', fee_per_share='0')
    sell = executable_depth(p['bids'], units, direction='SELL', fee_per_share='0')
    if max(amount(l['price']) for l in p['bids']) >= min(amount(l['price']) for l in p['asks']):
        return dict(result, reason='BOOK_CROSSED_OR_LOCKED')
    depth = buy if direction == 'ACQUIRE' else sell
    remaining, worst = units, None
    for level in sorted(p['asks' if direction == 'ACQUIRE' else 'bids'],
                        key=lambda l: amount(l['price']), reverse=direction == 'SELL'):
        remaining -= min(remaining, amount(level['size']))
        worst = level['price']
        if remaining == 0:
            break
    return dict(result, status='MEASURED' if depth.full_depth else 'GATED',
                reason='FULL_VISIBLE_DEPTH' if depth.full_depth else 'INSUFFICIENT_VISIBLE_DEPTH',
                gross_value=str(depth.gross_value), visible_units=str(depth.visible_units),
                full_depth=depth.full_depth, direction=direction,
                fees_in_depth=False, spread_and_depth_walk_included=True, worst_consumed_price=worst)


def _cost_scope_reasons(costs: tuple[CostComponent, ...], at: float, depth: dict) -> list[str]:
    reasons = []
    for c in costs:
        if c.valid_until is not None and at > c.valid_until:
            reasons.append('COST_EVIDENCE_EXPIRED')
        if c.priced_buy_limit is not None and (c.post_only or
                (depth.get('worst_consumed_price') is not None and
                 amount(depth['worst_consumed_price']) > amount(c.priced_buy_limit))):
            reasons.append('BUY_FEE_EXECUTION_SCOPE_MISMATCH')
    return sorted(set(reasons))


def settlement_entry_details(store: EvidenceStore, *, rule: RuleFingerprint,
                     prediction: BucketPrediction, binding: ReleaseBinding, market_id: str,
                     side: str, units: str, book_id: str, policy: ValuationPolicy,
                     costs: tuple[CostComponent, ...]) -> dict:
    """Conservative final-payout EV. NEXT_OBSERVATION cannot enter this path."""
    at, qty = finite(store.clock()), amount(units)
    if not 0 < qty <= 1_000_000:
        raise EvidenceError('VALUATION_UNITS_BOUND')
    target = contract_target(rule, market_id, side)
    model, payout = _prediction(rule, prediction, binding, target, at, policy)
    depth = _book(store, book_id, rule, target, qty, 'ACQUIRE', at, policy)
    decomposition = _costs(costs, horizon=PAYOUT, required=ENTRY_RISKS,
                           already=frozenset({'MODEL_UNCERTAINTY', 'DEPTH_WALK'}))
    reasons = _cost_scope_reasons(costs, at, depth)
    if qty > amount(policy.maximum_units):
        reasons.append('REQUEST_EXCEEDS_APPROVED_SIZE_SKIP')
    if depth['status'] != 'MEASURED':
        reasons.append(depth['reason'])
    if not decomposition['complete']:
        reasons.append('UNKNOWN_OR_MISSING_COST_COVERAGE')
    ev = None
    if not reasons:
        ev = payout-amount(depth['gross_value'])/qty-amount(decomposition['known_total_per_share'])
        if ev <= amount(policy.minimum_ev_per_share):
            reasons.append('CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD')
    details = dict(version=VERSION, valuation_type='SETTLEMENT', horizon=PAYOUT, target=target,
                   binding=asdict(binding), policy=asdict(policy), as_of=at, units=str(qty),
                   value_unit='COLLATERAL_PER_SHARE', collateral_asset=policy.collateral_asset,
                   model=model, book=depth, costs=decomposition,
                   conservative_payout_per_share=str(payout),
                   conservative_ev_per_share=str(ev) if ev is not None else None,
                   conservative_ev_total=str(ev*qty) if ev is not None else None,
                   outcome='ACCEPT_RESEARCH' if not reasons else 'GATED' if ev is None else 'REJECT',
                   reasons=reasons or ['ECONOMIC_CANDIDATE_REQUIRES_ALL_OTHER_GATES'],
                   trading_ev_includes_rewards=False, incremental_reward_ev=None,
                   rewards_for_spendable_cash='0', execution_status='NOT_SUBMITTED',
                   trading_pnl=None, financial_authority=False)
    return details


def settlement_entry(store: EvidenceStore, record_id: str, *, rule: RuleFingerprint,
                     prediction: BucketPrediction, binding: ReleaseBinding, market_id: str,
                     side: str, units: str, book_id: str, policy: ValuationPolicy,
                     costs: tuple[CostComponent, ...]) -> dict:
    """Persist the same numerical result used by bounded read-only replay."""
    details = settlement_entry_details(store, rule=rule, prediction=prediction, binding=binding,
        market_id=market_id, side=side, units=units, book_id=book_id, policy=policy, costs=costs)
    return store.audit(record_id, event_id=rule.payload['event_id'], kind='MEASUREMENT',
                       details=details, evidence_ids=(book_id,))


def compare_hold_sale(store: EvidenceStore, record_id: str, *, rule: RuleFingerprint,
                      prediction: BucketPrediction, binding: ReleaseBinding, market_id: str,
                      side: str, units: str, held_units: str, held_all_in_cost_basis: str,
                      book_id: str, policy: ValuationPolicy, hold_costs: tuple[CostComponent, ...],
                      sale_costs: tuple[CostComponent, ...], thesis_reason: str) -> dict:
    """Research sale-versus-hold comparison; ledger must reserve actual inventory.

    Entry fees are sunk for this choice, and retained in hypothetical lifetime
    P&L through the supplied all-in basis. No opposite-token netting or cancel.
    """
    identity(thesis_reason)
    at, qty, held = finite(store.clock()), amount(units), amount(held_units)
    basis = amount(held_all_in_cost_basis)
    if not 0 < qty <= held <= 1_000_000:
        raise EvidenceError('INSUFFICIENT_HELD_INVENTORY')
    target = contract_target(rule, market_id, side)
    model, payout = _prediction(rule, prediction, binding, target, at, policy)
    depth = _book(store, book_id, rule, target, qty, 'SELL', at, policy)
    hold = _costs(hold_costs, horizon=PAYOUT, required=HOLD_RISKS, already=frozenset({'MODEL_UNCERTAINTY'}))
    sale = _costs(sale_costs, horizon=SALE, required=SALE_RISKS, already=frozenset({'DEPTH_WALK'}))
    reasons = _cost_scope_reasons(hold_costs+sale_costs, at, depth)
    if qty > amount(policy.maximum_units):
        reasons.append('REQUEST_EXCEEDS_APPROVED_SIZE_SKIP')
    if depth['status'] != 'MEASURED':
        reasons.append(depth['reason'])
    if not hold['complete'] or not sale['complete']:
        reasons.append('UNKNOWN_OR_MISSING_COST_COVERAGE')
    hold_value = sale_value = difference = lifetime = None
    if not reasons:
        hold_value = payout-amount(hold['known_total_per_share'])
        sale_value = amount(depth['gross_value'])/qty-amount(sale['known_total_per_share'])
        difference = sale_value-hold_value
        lifetime = sale_value*qty-basis*qty/held
    details = dict(version=VERSION, valuation_type='EXIT_COMPARISON', target=target,
                   binding=asdict(binding), policy=asdict(policy), as_of=at,
                   units=str(qty), held_units=str(held), remaining_units_if_fully_sold=str(held-qty),
                   value_unit='COLLATERAL_PER_SHARE', collateral_asset=policy.collateral_asset,
                   hold_horizon=PAYOUT, sale_horizon=SALE, model=model, book=depth,
                   hold_costs=hold, sale_costs=sale, held_all_in_cost_basis=str(basis),
                   net_hold_per_share=None if hold_value is None else str(hold_value),
                   net_sale_per_share=None if sale_value is None else str(sale_value),
                   sale_advantage_per_share=None if difference is None else str(difference),
                   hypothetical_lifetime_pnl=None if lifetime is None else str(lifetime),
                   entry_costs_sunk_for_choice=True, thesis_reason=thesis_reason,
                   outcome='GATED' if reasons else 'REDUCE_RESEARCH_CANDIDATE' if
                     difference > amount(policy.minimum_ev_per_share) else 'HOLD_RESEARCH_ESTIMATE',
                   reasons=reasons or ['COMPARE_PROSPECTIVE_VALUES'],
                   actual_inventory_changed=False, realized_pnl=None, cancellation_is_exit=False,
                   execution_status='NOT_SUBMITTED', financial_authority=False)
    return store.audit(record_id, event_id=rule.payload['event_id'], kind='MEASUREMENT',
                       details=details, evidence_ids=(book_id,))


def repricing_status() -> dict:
    """Honest missing-model state; no next-observation probability as exit price."""
    return dict(valuation_type='REPRICING', horizon='EXECUTABLE_EARLY_EXIT', outcome='GATED',
                reason='CONDITIONAL_REPRICING_DEPTH_AND_EXIT_COST_MODEL_NOT_VALIDATED',
                expected_exit_value=None, financial_authority=False)
