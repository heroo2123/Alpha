"""Receipt-bound public reward settings and explicitly conditional research scores.

No estimated reward, public print or local score is evidence of money received.
Program recipe review and actual exchange scoring remain separate dependencies.
"""
from dataclasses import dataclass
from datetime import date, datetime, time, timezone
from decimal import Decimal
import json

from .collection import SourceRequest
from .evidence import EvidenceError, digest, finite, identity
from .scenario_risk import number, precise


ENDPOINT = 'https://gamma-api.polymarket.com/markets'
PROVIDER = 'GAMMA_REWARDS'
RECIPE = 'POLYMARKET_LIQUIDITY_DOCUMENTATION_20260924'
DOCUMENTS = ('https://docs.polymarket.com/programs/liquidity-rewards',
             'https://docs.polymarket.com/programs/maker-rebates',
             'https://docs.polymarket.com/market-data/market-details')


@dataclass(frozen=True)
class RewardPolicy:
    version: str
    methodology_checked_at: float
    maximum_methodology_age_seconds: float
    maximum_parameter_age_seconds: float = 300.
    maximum_refresh_quotes: int = 8

    def __post_init__(self):
        identity(self.version); finite(self.methodology_checked_at)
        if (not 0 < finite(self.maximum_methodology_age_seconds) <= 86400
                or not 0 < finite(self.maximum_parameter_age_seconds) <= 3600
                or type(self.maximum_refresh_quotes) is not int or not 1 <= self.maximum_refresh_quotes <= 16):
            raise EvidenceError('REWARD_POLICY_BOUND')


def reward_request(*, event_id, market_id, revision):
    return SourceRequest(PROVIDER, ENDPOINT+'/'+market_id, event_id, 'RULES', 'reward-market:'+market_id, revision)


def _array(value):
    if isinstance(value, str):
        try: value = json.loads(value)
        except (ValueError, TypeError): raise EvidenceError('REWARD_MARKET_ARRAY_INVALID') from None
    if type(value) is not list or len(value) != 2: raise EvidenceError('REWARD_BINARY_MARKET_REQUIRED')
    return value


def _bounded(value, upper, *, positive=False):
    if type(value) in (int, float):
        finite(value); value = str(value)
    out = number(value)
    if not (0 < out <= upper if positive else 0 <= out <= upper):
        raise EvidenceError('REWARD_PARAMETER_BOUND')
    return str(out)


def _day(value):
    try:
        result = date.fromisoformat(value)
        if result.isoformat() != value: raise ValueError
        return datetime.combine(result, time.min, timezone.utc).timestamp()
    except (TypeError, ValueError): raise EvidenceError('REWARD_SCHEDULE_DATE_INVALID') from None


def parameters(store, record_id, *, rule, market_id, policy):
    """Reparse the raw exact-market response every time; reject stale revisions."""
    row = store.get(record_id); b = row['body']; p = b.get('payload', {}); now = finite(store.clock())
    member = next((m for m in rule.payload['partition'] if m['market_id'] == market_id), None)
    if member is None: raise EvidenceError('REWARD_MARKET_OUTSIDE_RULE')
    if (row['kind'] != 'RULES' or row['event_id'] != rule.payload['event_id']
            or b['provider'] != PROVIDER or b['source_identity'] != 'reward-market:'+market_id
            or b['evidence_class'] not in {'PUBLIC_OBSERVED', 'SYNTHETIC'}
            or p.get('endpoint') != ENDPOINT+'/'+market_id or p.get('request_params') != {}
            or p.get('http_status') != 200 or p.get('response_format') != 'JSON'):
        raise EvidenceError('REWARD_EXACT_PUBLIC_RECEIPT_REQUIRED')
    current = store.latest_source(kind='RULES', event_id=row['event_id'], provider=PROVIDER,
                                  source_identity=b['source_identity'])
    if current['id'] != row['id']: raise EvidenceError('REWARD_PARAMETERS_SUPERSEDED')
    if not 0 <= now-b['received_at'] <= policy.maximum_parameter_age_seconds:
        raise EvidenceError('REWARD_PARAMETERS_STALE')
    raw = p.get('response')
    if type(raw) is not dict:
        raise EvidenceError('REWARD_SINGLE_MARKET_RESPONSE_REQUIRED')
    labels = _array(raw.get('outcomes')); tokens = _array(raw.get('clobTokenIds'))
    if (str(raw.get('id')) != market_id or raw.get('conditionId') != member['condition_id']
            or labels != ['Yes', 'No'] or tokens != [member['yes_token'], member['no_token']]):
        raise EvidenceError('REWARD_EXACT_CONDITION_TOKENS_REQUIRED')
    result = dict(market_id=market_id, condition_id=member['condition_id'],
                  yes_token_id=tokens[0], no_token_id=tokens[1],
                  active=raw.get('active'), closed=raw.get('closed'),
                  accepting_orders=raw.get('acceptingOrders'), allocations=[],
                  minimum_size=None, maximum_distance=None, liquidity_status='UNKNOWN', fee_status='UNKNOWN', fees=None)
    try:
        result['minimum_size'] = _bounded(raw.get('rewardsMinSize'), Decimal('1000000000'), positive=True)
        result['maximum_distance'] = str(number(_bounded(raw.get('rewardsMaxSpread'), Decimal('100'), positive=True))/100)
        allocations = raw.get('clobRewards')
        if type(allocations) is not list or len(allocations) > 32: raise EvidenceError('REWARD_ALLOCATIONS_BOUND')
        seen = set()
        for allocation in allocations:
            if type(allocation) is not dict: raise EvidenceError('REWARD_ALLOCATION_SCHEMA')
            aid = identity(str(allocation.get('id', '')))
            if aid in seen or allocation.get('conditionId') != member['condition_id']:
                raise EvidenceError('REWARD_ALLOCATION_IDENTITY')
            seen.add(aid)
            start = _day(allocation.get('startDate'))
            end = _day(allocation['endDate']) if allocation.get('endDate') is not None else None
            if end is not None and end < start: raise EvidenceError('REWARD_ALLOCATION_TIME')
            result['allocations'].append(dict(id=aid, asset=identity(allocation.get('assetAddress')),
                amount=_bounded(allocation.get('rewardsAmount'), Decimal('1000000000000')),
                daily_rate=_bounded(allocation.get('rewardsDailyRate'), Decimal('1000000000000')),
                start=start, end=end))
        result['liquidity_status'] = 'PARAMETERS_PRESENT'
    except EvidenceError:
        result['allocations'] = []  # A malformed sibling cannot silently inflate a known pool.
    try:
        if raw.get('feesEnabled') is False:
            result.update(fee_status='DISABLED', fees=dict(rate='0', exponent=1, rebate_rate='0', taker_only=True))
        elif raw.get('feesEnabled') is True:
            f = raw.get('feeSchedule')
            if (type(f) is not dict or type(f.get('takerOnly')) is not bool
                    or type(f.get('exponent')) is not int or not 0 <= f['exponent'] <= 8):
                raise EvidenceError('REWARD_FEE_SCHEMA')
            result.update(fee_status='PARAMETERS_PRESENT', fees=dict(rate=_bounded(f.get('rate'), Decimal(1)),
                exponent=f['exponent'], rebate_rate=_bounded(f.get('rebateRate'), Decimal(1)), taker_only=f['takerOnly']))
    except EvidenceError: pass
    return dict(result, parameters_sha256=digest(result), source_id=row['id'], source_sha256=row['sha256'],
                source_class=b['evidence_class'], received_at=b['received_at'],
                valid_until=b['received_at']+policy.maximum_parameter_age_seconds)


@precise
def liquidity_score(orders, *, midpoint, minimum_size, maximum_distance):
    """Documented point-score recipe conditional on an explicit reference.

    A local midpoint is not the venue's size-adjusted scoring reference. Neither
    this score nor its normalization can establish an epoch reward entitlement.
    """
    mid = number(midpoint); minimum = number(minimum_size); width = number(maximum_distance)
    if not 0 <= mid <= 1 or not 0 < width <= 1 or minimum <= 0 or not 1 <= len(orders) <= 16:
        raise EvidenceError('REWARD_SCORE_BOUND')
    parts = {}; totals = [Decimal(0), Decimal(0)]
    for order in orders:
        key = identity(order['quote_id']); side = order['side']; direction = order['direction']
        price = number(order['price']); qty = number(order['units'])
        if key in parts or side not in {'YES','NO'} or direction not in {'BUY','SELL'} or not 0 < price < 1 or not 0 < qty <= 1000000:
            raise EvidenceError('REWARD_ORDER_BOUND')
        distance = abs(price-(mid if side == 'YES' else 1-mid))
        eligible = qty >= minimum and distance < width
        score = qty*((width-distance)/width)**2 if eligible else Decimal(0)
        group = 0 if (side, direction) in {('YES','BUY'),('NO','SELL')} else 1
        totals[group] += score
        parts[key] = dict(conditional_qualification=eligible, distance=str(distance), score=str(score), group=group)
    minimum_score = min(totals)
    if Decimal('.1') <= mid <= Decimal('.9'): minimum_score = max(minimum_score, max(totals)/3)
    return dict(orders=parts, side_scores=list(map(str, totals)), minimum_score=str(minimum_score),
                reference_midpoint=str(mid), recipe=RECIPE, multiplier='1', divisor='3',
                scope='WEATHER_RESEARCH_ONLY_NO_SPORTS_IN_GAME_MULTIPLIER',
                qualification_class='CONDITIONAL_ON_REFERENCE_AND_CURRENT_PROGRAM_RECIPE',
                official_scoring_confirmed=False, expected_epoch_share=None)


def pursuit_gate(trading_ev_lower, reward_lower):
    """Same-horizon collateral totals only; no optimistic reward rescues losses."""
    if trading_ev_lower is None or reward_lower is None: return 'GATED_UNKNOWN_ECONOMICS'
    return 'GATED_NONPOSITIVE_COMBINED_FLOOR' if number(trading_ev_lower, signed=True)+number(reward_lower) <= 0 else 'RESEARCH_ONLY_OTHER_GATES_REQUIRED'
