"""Exact public REST books, with original receipt and server time kept distinct.

A complete REST response is a point-in-time book, not websocket continuity,
queue position, a trade, or a fill. No sequence numbers are manufactured.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal
import re

from .collection import SourceRequest
from .evidence import EvidenceError, digest, finite, identity
from .measurement import executable_depth
from .scenario_risk import number, precise
from .valuation import contract_target


VERSION = 'alpha_v11_public_book_v1'
PROVIDER = 'POLYMARKET_PUBLIC_CLOB'
ENDPOINT = 'https://clob.polymarket.com/book'


@dataclass(frozen=True)
class BookPolicy:
    version: str
    maximum_age_seconds: float = 30.
    maximum_levels_per_side: int = 1000

    def __post_init__(self):
        identity(self.version)
        if (not 0 < finite(self.maximum_age_seconds) <= 120
                or type(self.maximum_levels_per_side) is not int
                or not 1 <= self.maximum_levels_per_side <= 1000):
            raise EvidenceError('PUBLIC_BOOK_POLICY_BOUND')


def book_request(*, event_id, token_id, revision):
    identity(token_id)
    return SourceRequest(PROVIDER, ENDPOINT, event_id, 'BOOK', token_id, revision,
                         (('token_id', token_id),))


def _target(rule, token_id):
    matches = [contract_target(rule, b['market_id'], side)
               for b in rule.payload['partition'] for side in ('YES', 'NO')
               if b[side.lower()+'_token'] == token_id]
    if len(matches) != 1:
        raise EvidenceError('PUBLIC_BOOK_EXACT_RULE_TOKEN_REQUIRED')
    return matches[0]


@precise
def normalize_book_capture(store, raw_id, *, rule, token_id, collateral_asset, policy):
    """One immutable normalized row per raw receipt and interpretation.

    Returning an existing row never refreshes its time. Current source checks
    remain the responsibility of each later consumer, including census/admission.
    """
    if not isinstance(policy, BookPolicy):
        raise EvidenceError('PUBLIC_BOOK_POLICY_REQUIRED')
    identity(collateral_asset)
    target = _target(rule, token_id)
    request = dict(raw_id=raw_id, rule_sha256=rule.sha256, token_id=token_id,
                   collateral_asset=collateral_asset, policy=asdict(policy))
    request_sha = digest(request)
    record_id = 'book-normalized:'+digest([VERSION, request_sha])
    try:
        prior = store.get(record_id)
    except EvidenceError as exc:
        if str(exc) != 'EVIDENCE_MISSING':
            raise
    else:
        if (prior['kind'] != 'BOOK' or prior['event_id'] != rule.payload['event_id']
                or prior['body']['payload'].get('normalization_sha256') != request_sha):
            raise EvidenceError('PUBLIC_BOOK_NORMALIZATION_CONFLICT')
        return prior
    head = store.latest(kind='BOOK', event_id=rule.payload['event_id'])
    raw = store.get(raw_id); body = raw['body']; payload = body.get('payload', {})
    now = finite(store.clock())
    if (raw['kind'] != 'BOOK' or raw['event_id'] != rule.payload['event_id']
            or body.get('provider') != PROVIDER or body.get('source_identity') != token_id
            or body.get('evidence_class') not in {'PUBLIC_OBSERVED', 'SYNTHETIC'}
            or payload.get('endpoint') != ENDPOINT or payload.get('request_params') != {'token_id':token_id}
            or payload.get('http_status') != 200 or payload.get('response_format') != 'JSON'
            or payload.get('source_time_status') != 'NOT_YET_NORMALIZED'):
        raise EvidenceError('PUBLIC_BOOK_EXACT_RAW_RECEIPT_REQUIRED')
    current = store.latest_source(kind='BOOK', event_id=raw['event_id'], provider=PROVIDER, source_identity=token_id)
    if current['id'] != raw_id:
        raise EvidenceError('PUBLIC_BOOK_RAW_SUPERSEDED')
    if not 0 <= now-body['received_at'] < policy.maximum_age_seconds:
        raise EvidenceError('PUBLIC_BOOK_RECEIPT_STALE_OR_FUTURE')
    response = payload.get('response')
    if (type(response) is not dict or response.get('market') != target['condition_id']
            or response.get('asset_id') != token_id):
        raise EvidenceError('PUBLIC_BOOK_RESPONSE_TARGET_MISMATCH')
    stamp = response.get('timestamp')
    if not isinstance(stamp, str) or not re.fullmatch(r'[0-9]{1,16}', stamp):
        raise EvidenceError('PUBLIC_BOOK_SERVER_TIMESTAMP_REQUIRED')
    observed = float(Decimal(stamp)/1000)
    if not 0 <= observed <= body['received_at'] <= now or now-observed >= policy.maximum_age_seconds:
        raise EvidenceError('PUBLIC_BOOK_SERVER_TIME_STALE_OR_FUTURE')
    exchange_hash = identity(response.get('hash'), maximum=256)
    tick, minimum = number(response.get('tick_size')), number(response.get('min_order_size'))
    if tick not in {Decimal('.1'), Decimal('.01'), Decimal('.001'), Decimal('.0001')} or not 0 < minimum <= 1_000_000:
        raise EvidenceError('PUBLIC_BOOK_TICK_OR_MINIMUM_UNKNOWN')
    if type(response.get('neg_risk')) is not bool:
        raise EvidenceError('PUBLIC_BOOK_NEGATIVE_RISK_FLAG_UNKNOWN')
    sides = {}
    for name, direction in (('bids', 'SELL'), ('asks', 'ACQUIRE')):
        levels = response.get(name)
        if type(levels) is not list or len(levels) > policy.maximum_levels_per_side:
            raise EvidenceError('PUBLIC_BOOK_DEPTH_BOUND')
        for level in levels:
            if type(level) is not dict or set(level) != {'price', 'size'}:
                raise EvidenceError('PUBLIC_BOOK_LEVEL_SCHEMA')
            price, size = number(level['price']), number(level['size'])
            if price % tick or not 0 < size <= 1_000_000_000:
                raise EvidenceError('PUBLIC_BOOK_LEVEL_BOUND_OR_TICK_MISMATCH')
        executable_depth(levels, '1', direction=direction, fee_per_share=None)
        sides[name] = sorted(levels, key=lambda x: number(x['price']), reverse=name == 'bids')
    if sides['bids'] and sides['asks'] and number(sides['bids'][0]['price']) >= number(sides['asks'][0]['price']):
        raise EvidenceError('PUBLIC_BOOK_CROSSED_OR_LOCKED')
    normalized = dict(target, **sides, version=VERSION, normalization_sha256=request_sha,
        rule_fingerprint=rule.sha256, collateral_asset=collateral_asset,
        snapshot_type='FULL', stream_healthy=True, stream_health_scope='VALIDATED_REST_SNAPSHOT_ONLY',
        continuous_stream_verified=False, exchange_book_hash=exchange_hash,
        minimum_order_size=str(minimum), tick_size=str(tick), negative_risk=response['neg_risk'],
        raw_evidence_id=raw_id, raw_evidence_sha256=raw['sha256'], feature_ready_at=now,
        market_event_to_receipt_seconds=body['received_at']-observed,
        network_latency_seconds=None, fees=None, financial_authority=False)
    # Empty sides are valid absence of liquidity, never an executable price.
    # The response's opaque hash is not a sequence or a proof of our execution.
    derived = dict(provider=PROVIDER, source_identity=token_id, revision=body['revision'],
        payload=normalized, observed_at=observed, issued_at=None, published_at=None,
        received_at=body['received_at'], evidence_class=body['evidence_class'], source_kind='BOOK')
    return store._append(record_id, 'BOOK', raw['event_id'], derived, now, now,
                         expected_previous_seq=head['seq'] if head else 0)
