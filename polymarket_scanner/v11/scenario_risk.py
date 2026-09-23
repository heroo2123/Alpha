"""Bounded exact-bucket scenarios and conservative correlated loss aggregation.

All positions/orders belong to one declared namespace/account. Hypothetical
inventory never offsets LIVE; no independence or financial authority is inferred.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext
import re

from .evidence import EvidenceError, EvidenceStore, digest, identity, sha
from .measurement import amount
from .probability import _partition
from .rules import RuleFingerprint


VERSION = 'alpha_v11_scenario_risk_v1'
GROUPS = {'EVENT', 'CITY', 'REGION', 'WEATHER', 'SOURCE', 'MODEL', 'PORTFOLIO'}


def namespace(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r'V11_PAPER|(?:CHALLENGER|ABLATION):[a-zA-Z0-9_-]{1,64}', value):
        raise EvidenceError('NONFINANCIAL_NAMESPACE_REQUIRED')
    return value


def number(value: str, *, signed=False) -> Decimal:
    if not isinstance(value, str) or len(value) > 48:
        raise EvidenceError('RISK_DECIMAL_REPRESENTATION')
    try:
        result = Decimal(value)
    except Exception:
        raise EvidenceError('RISK_DECIMAL_INVALID') from None
    if (not result.is_finite() or abs(result) > 1_000_000_000
            or result.as_tuple().exponent < -18 or (not signed and result < 0)):
        raise EvidenceError('RISK_DECIMAL_BOUND')
    return result


@dataclass(frozen=True)
class Attribution:
    strategy: str
    weight: str

    def __post_init__(self):
        identity(self.strategy)
        if not 0 < number(self.weight) <= 1:
            raise EvidenceError('ATTRIBUTION_WEIGHT_INVALID')


def _attribution(value: tuple[Attribution, ...]) -> None:
    if (type(value) is not tuple or not 1 <= len(value) <= 16
            or any(not isinstance(v, Attribution) for v in value)
            or len({v.strategy for v in value}) != len(value)
            or sum(number(v.weight) for v in value) != 1):
        raise EvidenceError('ATTRIBUTION_MUST_PARTITION_ONE_ECONOMIC_POSITION')


@dataclass(frozen=True)
class Position:
    lot_id: str
    token_id: str
    units: str
    all_in_cost_basis: str
    attribution: tuple[Attribution, ...]

    def __post_init__(self):
        identity(self.lot_id); identity(self.token_id)
        if not 0 < number(self.units) <= 1_000_000:
            raise EvidenceError('POSITION_UNITS_BOUND')
        number(self.all_in_cost_basis)
        _attribution(self.attribution)


@dataclass(frozen=True)
class PendingOrder:
    intent_id: str
    token_id: str
    direction: str
    remaining_units: str
    conservative_collateral_per_share: str
    attribution: tuple[Attribution, ...]

    def __post_init__(self):
        identity(self.intent_id); identity(self.token_id)
        if self.direction not in {'BUY', 'SELL'} or not 0 < number(self.remaining_units) <= 1_000_000:
            raise EvidenceError('PENDING_ORDER_INVALID')
        if not 0 <= number(self.conservative_collateral_per_share) <= 2:
            raise EvidenceError('PENDING_ORDER_PRICE_BOUND')
        if self.direction == 'SELL' and number(self.conservative_collateral_per_share) > 1:
            raise EvidenceError('SALE_PROCEEDS_BOUND')
        _attribution(self.attribution)


def event_scenarios(rule: RuleFingerprint, *, execution_namespace: str, account_id: str,
                    positions: tuple[Position, ...], pending: tuple[PendingOrder, ...],
                    realized_event_pnl: str = '0') -> dict:
    namespace(execution_namespace); identity(account_id)
    if (type(positions) is not tuple or type(pending) is not tuple or len(positions)+len(pending) > 1000
            or any(not isinstance(p, Position) for p in positions)
            or any(not isinstance(p, PendingOrder) for p in pending)):
        raise EvidenceError('SCENARIO_POSITION_ORDER_BOUND')
    if len({p.lot_id for p in positions}) != len(positions) or len({p.intent_id for p in pending}) != len(pending):
        raise EvidenceError('DUPLICATE_ECONOMIC_EXPOSURE_ID')
    buckets = _partition(rule)
    tokens = {b[k]: (b['market_id'], k == 'yes_token') for b in buckets for k in ('yes_token', 'no_token')}
    if any(p.token_id not in tokens for p in positions+pending):
        raise EvidenceError('POSITION_RULE_TOKEN_MISMATCH')
    realized = number(realized_event_pnl, signed=True)
    with localcontext() as context:
        context.prec = 80
        held, costs, sell_reserved, buy_reserved, concentration = {}, {}, {}, Decimal(0), {}
        for p in positions:
            held[p.token_id] = held.get(p.token_id, Decimal(0))+number(p.units)
            costs[p.token_id] = costs.get(p.token_id, Decimal(0))+number(p.all_in_cost_basis)
        for p in pending:
            if p.direction == 'SELL':
                sell_reserved[p.token_id] = sell_reserved.get(p.token_id, Decimal(0))+number(p.remaining_units)
            else:
                buy_reserved += number(p.remaining_units)*number(p.conservative_collateral_per_share)
        if any(qty > held.get(token, Decimal(0)) for token, qty in sell_reserved.items()):
            raise EvidenceError('PENDING_SALES_EXCEED_HELD_INVENTORY')
        for token in sorted(set(held) | {p.token_id for p in pending}):
            concentration[token] = dict(held_units=str(held.get(token, 0)),
                                        all_in_cost_basis=str(costs.get(token, 0)),
                                        reserved_sell_units=str(sell_reserved.get(token, 0)),
                                        pending_buy_units=str(sum(number(p.remaining_units) for p in pending
                                                                  if p.token_id == token and p.direction == 'BUY')))
        outcomes = []
        for outcome in buckets:
            market = outcome['market_id']
            def payout(token):
                token_market, yes = tokens[token]
                return Decimal(int((token_market == market) == yes))
            held_payout = sum(number(p.units)*payout(p.token_id) for p in positions)
            basis = sum(number(p.all_in_cost_basis) for p in positions)
            base = realized+held_payout-basis
            adverse, favorable = Decimal(0), Decimal(0)
            attribution = {}
            for p in positions:
                pnl = number(p.units)*payout(p.token_id)-number(p.all_in_cost_basis)
                for a in p.attribution:
                    attribution[a.strategy] = attribution.get(a.strategy, Decimal(0))+pnl*number(a.weight)
            effects = []
            for p in pending:
                delta = number(p.remaining_units)*(payout(p.token_id)-number(p.conservative_collateral_per_share))
                if p.direction == 'SELL':
                    delta = -delta
                worst, best = min(Decimal(0), delta), max(Decimal(0), delta)
                adverse += worst; favorable += best
                effects.append(dict(intent_id=p.intent_id, adverse_optional_fill_delta=str(worst),
                                    favorable_optional_fill_delta=str(best)))
                for a in p.attribution:
                    attribution[a.strategy] = attribution.get(a.strategy, Decimal(0))+worst*number(a.weight)
            outcomes.append(dict(market_id=market, held_payout=str(held_payout), all_in_cost_basis=str(basis),
                                 held_plus_realized_pnl=str(base),
                                 conservative_pnl=str(base+adverse), favorable_optional_fill_pnl=str(base+favorable),
                                 worst_optional_fill_effects=effects,
                                 strategy_open_exposure_pnl={k: str(v) for k, v in sorted(attribution.items())},
                                 realized_pnl_separately_attributed=str(realized)))
        worst = min(Decimal(row['conservative_pnl']) for row in outcomes)
        best = max(Decimal(row['favorable_optional_fill_pnl']) for row in outcomes)
        body = dict(version=VERSION, execution_namespace=execution_namespace, account_id=account_id,
                    event_id=rule.payload['event_id'], station=rule.payload['station'], city=rule.payload['city'],
                    target_date=rule.payload['target_date'], rule_fingerprint=rule.sha256,
                    metadata_fingerprint=rule.payload['metadata_fingerprint'],
                    outcomes=outcomes, worst_case_pnl=str(worst), worst_case_loss=str(max(Decimal(0), -worst)),
                    best_case_result=str(best), reserved_buy_cash=str(buy_reserved), concentration=concentration,
                    positions=[asdict(p) for p in positions], pending=[asdict(p) for p in pending],
                    realized_event_pnl=str(realized), hypothetical_payout_is_spendable_cash=False,
                    partial_fill_assumption='EACH_REMAINDER_MAY_FILL_OR_NOT_ADVERSELY_PER_OUTCOME',
                    financial_authority=False)
    return dict(body, sha256=digest(body))


def _verify_view(view: dict) -> None:
    if not isinstance(view, dict):
        raise EvidenceError('SCENARIO_VIEW_REQUIRED')
    raw = dict(view); claimed = raw.pop('sha256', None)
    if digest(raw) != claimed or raw.get('version') != VERSION:
        raise EvidenceError('SCENARIO_VIEW_INTEGRITY')


def incremental_scenarios(rule: RuleFingerprint, *, execution_namespace: str, account_id: str,
                          positions: tuple[Position, ...], pending: tuple[PendingOrder, ...],
                          proposed: tuple[PendingOrder, ...], realized_event_pnl: str = '0') -> dict:
    kw = dict(execution_namespace=execution_namespace, account_id=account_id, positions=positions,
              realized_event_pnl=realized_event_pnl)
    before = event_scenarios(rule, pending=pending, **kw)
    after = event_scenarios(rule, pending=pending+proposed, **kw)
    changes = [{"market_id": a['market_id'], 'conservative_pnl_change':
                str(Decimal(a['conservative_pnl'])-Decimal(b['conservative_pnl']))}
               for a, b in zip(after['outcomes'], before['outcomes'])]
    return dict(before=before, after=after, per_outcome_change=changes,
                incremental_worst_case_loss=str(Decimal(after['worst_case_loss'])-Decimal(before['worst_case_loss'])),
                financial_authority=False)


@dataclass(frozen=True)
class StationMembership:
    station: str
    city: str
    region: str
    weather_groups: tuple[str, ...]
    source_groups: tuple[str, ...]
    model_groups: tuple[str, ...]
    metadata_fingerprint: str

    def __post_init__(self):
        sha(self.metadata_fingerprint)
        for name in ('station', 'city', 'region'):
            identity(getattr(self, name))
        for values in (self.weather_groups, self.source_groups, self.model_groups):
            if type(values) is not tuple or not 1 <= len(values) <= 16 or len(set(values)) != len(values):
                raise EvidenceError('DEPENDENCE_GROUPS_REQUIRED_USE_SHARED_UNKNOWN_IF_UNRESOLVED')
            for value in values:
                identity(value)


@dataclass(frozen=True)
class CorrelationMap:
    version: str
    evidence_sha256: str
    memberships: tuple[StationMembership, ...]

    def __post_init__(self):
        identity(self.version); sha(self.evidence_sha256)
        if (type(self.memberships) is not tuple or not 1 <= len(self.memberships) <= 256
                or any(not isinstance(m, StationMembership) for m in self.memberships)
                or len({m.station for m in self.memberships}) != len(self.memberships)):
            raise EvidenceError('CORRELATION_MAP_INVALID')


@dataclass(frozen=True)
class ScenarioLimits:
    policy_version: str
    per_event: str
    per_city: str
    per_region: str
    per_weather_group: str
    per_source_group: str
    per_model_group: str
    portfolio: str
    max_position_units: str

    def __post_init__(self):
        identity(self.policy_version)
        for name, value in asdict(self).items():
            if name != 'policy_version' and number(value) <= 0:
                raise EvidenceError('SCENARIO_LIMIT_NONPOSITIVE')


def portfolio_risk(views: tuple[dict, ...], *, correlation: CorrelationMap, limits: ScenarioLimits,
                   execution_namespace: str, account_id: str) -> dict:
    namespace(execution_namespace); identity(account_id)
    if type(views) is not tuple or len(views) > 128:
        raise EvidenceError('PORTFOLIO_EVENT_BOUND')
    grouped = {kind: {} for kind in GROUPS}
    event_ids, seen_tokens, faults = set(), set(), []
    memberships = {m.station: m for m in correlation.memberships}
    for view in views:
        _verify_view(view)
        if view['execution_namespace'] != execution_namespace or view['account_id'] != account_id:
            raise EvidenceError('CROSS_NAMESPACE_OR_ACCOUNT_NETTING_REFUSED')
        event = view['event_id']
        if event in event_ids:
            raise EvidenceError('DUPLICATE_EVENT_VIEW')
        event_ids.add(event)
        membership = memberships.get(view['station'])
        if (membership is None or membership.metadata_fingerprint != view['metadata_fingerprint']
                or (view['city'] not in (None, '') and membership.city != view['city'])):
            raise EvidenceError('STATION_CITY_MAPPING_UNVERIFIED')
        loss = number(view['worst_case_loss'])
        keys = {'EVENT': [event], 'CITY': [membership.city], 'REGION': [membership.region],
                'WEATHER': membership.weather_groups, 'SOURCE': membership.source_groups,
                'MODEL': membership.model_groups, 'PORTFOLIO': ['ALL']}
        for kind, names in keys.items():
            for name in names:
                grouped[kind][name] = grouped[kind].get(name, Decimal(0))+loss
        for token, row in view['concentration'].items():
            if token in seen_tokens:
                raise EvidenceError('TOKEN_SHARED_ACROSS_EVENT_VIEWS')
            seen_tokens.add(token)
            if number(row['held_units'])+number(row['pending_buy_units']) > number(limits.max_position_units):
                faults.append(dict(gate='POSITION_UNITS', group=token))
    ceilings = dict(EVENT=limits.per_event, CITY=limits.per_city, REGION=limits.per_region,
                    WEATHER=limits.per_weather_group, SOURCE=limits.per_source_group,
                    MODEL=limits.per_model_group, PORTFOLIO=limits.portfolio)
    for kind in sorted(grouped):
        for name, loss in sorted(grouped[kind].items()):
            if loss > number(ceilings[kind]):
                faults.append(dict(gate=kind+'_LOSS_LIMIT', group=name, loss=str(loss), ceiling=ceilings[kind]))
    return dict(version=VERSION, execution_namespace=execution_namespace, account_id=account_id,
                correlation_sha256=digest(asdict(correlation)), limits_sha256=digest(asdict(limits)),
                groups={k: {n: str(v) for n, v in sorted(values.items())} for k, values in sorted(grouped.items())},
                views=[{'event_id': v['event_id'], 'sha256': v['sha256']} for v in views],
                accepted=not faults, faults=faults,
                aggregation='SUM_EVENT_WORST_LOSSES_NO_INTERCITY_INDEPENDENCE_CREDIT',
                mapping_status='DECLARED_VERSIONED_MAP_REQUIRES_PROTECTED_REVIEW', financial_authority=False)


def archive_scenarios(store: EvidenceStore, record_id: str, view: dict, *, evidence_ids: tuple[str, ...]) -> dict:
    _verify_view(view)
    if view['execution_namespace'] != store.namespace:
        raise EvidenceError('CROSS_NAMESPACE_SCENARIO_ARCHIVE')
    return store.audit(record_id, event_id=view['event_id'], kind='COORDINATOR_EVENT',
                       details=dict(version=VERSION, scenario=view), evidence_ids=evidence_ids)
