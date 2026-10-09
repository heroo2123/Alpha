"""Offline PAPER fee evidence joined to one current normalized public book.

Importing an observed snapshot does not attest its origin or enable orders.
The fee is a conditional published-policy requirement, not a venue guarantee.
"""
from dataclasses import replace
from decimal import Decimal

from ..production.chain import NEG_RISK_EXCHANGE, STANDARD_EXCHANGE
from ..production.fees import EXCHANGE_PUBLISHED_SCHEDULE, ONCHAIN_BOUND
from .book_inputs import PROVIDER, VERSION as BOOK_VERSION
from .evidence import EvidenceError, digest, finite
from .valuation import buy_fee_cost, contract_target


def _book(store, book_id, rule, target, max_age_seconds):
    row = store.get(book_id)
    body = row['body']
    p = body.get('payload', {})
    now = finite(store.clock())
    if (row['kind'] != 'BOOK' or row['event_id'] != rule.payload['event_id']
            or body.get('provider') != PROVIDER or body.get('evidence_class') != 'PUBLIC_OBSERVED'
            or body.get('source_identity') != target['token_id']
            or p.get('version') != BOOK_VERSION or p.get('snapshot_type') != 'FULL'
            or p.get('raw_evidence_id') is None or p.get('rule_fingerprint') != rule.sha256
            or any(p.get(k) != v for k, v in target.items())
            or type(p.get('negative_risk')) is not bool
            or not 0 <= now-finite(body.get('received_at')) <= max_age_seconds
            or body.get('observed_at') != body.get('received_at')):
        raise EvidenceError('PAPER_FEE_EXACT_FRESH_BOOK_REQUIRED')
    if store.latest_source(kind='BOOK', event_id=row['event_id'], provider=PROVIDER,
                           source_identity=target['token_id'])['id'] != book_id:
        raise EvidenceError('PAPER_FEE_BOOK_SUPERSEDED')
    return row


def import_fee_snapshot(store, record_id, *, rule, market_id, side, book_id,
                        snapshot, max_age_seconds):
    """Archive a caller supplied PublicMarketReader snapshot; provenance remains offline.

    The caller must retain the original public/chain observation separately.
    This importer only validates its internal digest, receipt, and exact scope.
    """
    target = contract_target(rule, market_id, side)
    book = _book(store, book_id, rule, target, finite(max_age_seconds))
    if (not isinstance(snapshot, dict)
            or snapshot.get('fee_policy') not in {EXCHANGE_PUBLISHED_SCHEDULE, ONCHAIN_BOUND}
            or snapshot.get('exchange') != (NEG_RISK_EXCHANGE if book['body']['payload']['negative_risk']
                                            else STANDARD_EXCHANGE)
            or snapshot.get('market') != target['condition_id']
            or snapshot.get('neg_risk') != book['body']['payload']['negative_risk']
            or snapshot.get('token') != target['token_id']):
        raise EvidenceError('PAPER_FEE_MARKET_OR_RISK_MISMATCH')
    # Validates the published policy, model/version, digest, chain metadata and receipt.
    buy_fee_cost(snapshot, target=target, as_of=store.clock(),
                 max_age_seconds=max_age_seconds, limit_price='.5', post_only=False)
    if snapshot['received_at'] < book['body']['received_at']:
        raise EvidenceError('PAPER_FEE_SNAPSHOT_PRECEDES_BOOK')
    return store.audit(record_id, event_id='paper-fee:'+book_id, kind='MEASUREMENT',
                       details={'status': 'OFFLINE_IMPORTED_NOT_LIVE_ATTESTED',
                                'book_id': book_id, 'book_sha256': book['sha256'],
                                'snapshot': snapshot}, evidence_ids=(book_id,))


def current_buy_fee_cost(store, *, rule, market_id, side, units, book_id, max_age_seconds):
    """Price a BUY at the worst visible ask consumed by this exact PAPER request."""
    row = store.latest(kind='MEASUREMENT', event_id='paper-fee:'+book_id)
    if row is None:
        return None
    target = contract_target(rule, market_id, side)
    book = _book(store, book_id, rule, target, finite(max_age_seconds))
    details = row['body']['details']
    if (details.get('status') != 'OFFLINE_IMPORTED_NOT_LIVE_ATTESTED'
            or details.get('book_id') != book_id or details.get('book_sha256') != book['sha256']
            or row['body']['evidence'] != [{'id': book_id, 'sha256': book['sha256']}]):
        raise EvidenceError('PAPER_FEE_IMPORT_BINDING_INVALID')
    remaining, worst = Decimal(str(units)), None
    if remaining <= 0:
        raise EvidenceError('PAPER_FEE_UNITS_INVALID')
    for level in book['body']['payload']['asks']:
        remaining -= min(remaining, Decimal(level['size']))
        worst = level['price']
        if remaining == 0:
            break
    if remaining or worst is None:
        return None
    component = buy_fee_cost(details['snapshot'], target=target, as_of=store.clock(),
                             max_age_seconds=max_age_seconds, limit_price=worst, post_only=False)
    return replace(component, assumption_sha256=digest({
        'component': component.assumption_sha256, 'fee_import_sha256': row['sha256'],
        'book_sha256': book['sha256'], 'units': str(units)}),
        source_evidence_id=row['id'], source_evidence_sha256=row['sha256'])
