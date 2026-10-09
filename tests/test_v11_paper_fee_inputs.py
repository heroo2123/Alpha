"""Offline fee import through the actual normalized book and valuation path."""
from copy import deepcopy
from decimal import Decimal

import pytest

from polymarket_scanner.production.chain import ExchangeError, STANDARD_EXCHANGE, NEG_RISK_EXCHANGE
from polymarket_scanner.production.fees import EXCHANGE_PUBLISHED_SCHEDULE, make_fee_evidence
from polymarket_scanner.v11.book_inputs import BookPolicy, ENDPOINT, PROVIDER, normalize_book_capture
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.paper_fee_inputs import import_fee_snapshot, current_buy_fee_cost
from polymarket_scanner.v11.request_assembly import EntryRequestFactory, RequestAssembler, TargetPlan
from polymarket_scanner.v11.valuation import ENTRY_RISKS, contract_target, settlement_entry_details
from test_v11_request_assembly import inputs, state_for
from test_v11_paper_runtime import queue
from test_v11_strategy_pipeline import factory
from test_v11_valuation import rig
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle


def observed(rig):
    store, now, kwargs, _ = rig
    rule = kwargs['rule']
    target = contract_target(rule, kwargs['market_id'], 'YES')
    received = now[0]
    response = dict(market=target['condition_id'], asset_id=target['token_id'],
        timestamp=str(int(received*1000)), hash='observed-hash',
        bids=[dict(price='.1', size='10')], asks=[dict(price='.4', size='10')],
        tick_size='.01', min_order_size='1', neg_risk=False)
    raw = store.capture('public-raw', event_id=rule.payload['event_id'], kind='BOOK',
        provider=PROVIDER, source_identity=target['token_id'], revision='observed',
        evidence_class='PUBLIC_OBSERVED', payload=dict(endpoint=ENDPOINT,
            request_params={'token_id': target['token_id']}, http_status=200,
            response_format='JSON', source_time_status='NOT_YET_NORMALIZED', response=response))
    book = normalize_book_capture(store, raw['id'], rule=rule, token_id=target['token_id'],
        collateral_asset='FIXTURE_COLLATERAL', policy=BookPolicy('paper-fee-test'))
    proof = make_fee_evidence(EXCHANGE_PUBLISHED_SCHEDULE, token=target['token_id'],
        condition=target['condition_id'], exchange=STANDARD_EXCHANGE, observed_at=received,
        fd={'r': '.05', 'e': '1', 'to': True}, max_fee_bps=0,
        max_fee_block={'number': 100, 'hash': 'observed-block'},
        maker_base_fee_bps=0, taker_base_fee_bps=0)
    snapshot = dict(fee_policy=EXCHANGE_PUBLISHED_SCHEDULE, fee_evidence=proof,
        token=target['token_id'], condition=target['condition_id'], market=target['condition_id'],
        exchange=STANDARD_EXCHANGE, max_fee_bps=0, received_at=received, neg_risk=False)
    return book, snapshot


def import_observed(rig, book, snapshot):
    store, _, kwargs, _ = rig
    return import_fee_snapshot(store, 'fee-import', rule=kwargs['rule'],
        market_id=kwargs['market_id'], side='YES', book_id=book['id'],
        snapshot=snapshot, max_age_seconds=10.)


def priced(rig, book):
    store, _, kwargs, _ = rig
    return current_buy_fee_cost(store, rule=kwargs['rule'], market_id=kwargs['market_id'],
        side='YES', units='5', book_id=book['id'], max_age_seconds=10.)


def test_observed_exact_fee_reaches_valuation_but_six_categories_remain_gated(rig):
    book, snapshot = observed(rig)
    assert priced(rig, book) is None
    imported = import_observed(rig, book, snapshot)
    assert imported['body']['details']['status'] == 'OFFLINE_IMPORTED_NOT_LIVE_ATTESTED'
    fee = priced(rig, book)
    assert fee.covers == ('ACQUISITION_FEES',) and Decimal(fee.per_share) == Decimal('.024')
    assert fee.priced_buy_limit == '.4' and fee.post_only is False
    assert fee.source_evidence_id == imported['id'] and fee.source_evidence_sha256 == imported['sha256']
    assert fee.fee_policy == EXCHANGE_PUBLISHED_SCHEDULE and fee.fee_model_version == 1
    store, _, kwargs, _ = rig
    result = settlement_entry_details(store, **dict(kwargs, book_id=book['id'], costs=(fee,)))
    assert result['costs']['missing'] == sorted(ENTRY_RISKS-{'ACQUISITION_FEES'})
    assert result['costs']['known_total_per_share'] == fee.per_share
    assert result['outcome'] == 'GATED' and result['conservative_ev_per_share'] is None
    assert 'UNKNOWN_OR_MISSING_COST_COVERAGE' in result['reasons']


@pytest.mark.parametrize('mutation', ['token', 'condition', 'market', 'risk', 'exchange', 'digest', 'source', 'model', 'future'])
def test_import_rejects_wrong_or_unsubstantiated_fee_snapshot(rig, mutation):
    book, snapshot = observed(rig)
    snapshot = deepcopy(snapshot)
    if mutation == 'token': snapshot['token'] = 'other-token'
    if mutation == 'condition': snapshot['condition'] = 'other-condition'
    if mutation == 'market': snapshot['market'] = 'other-condition'
    if mutation == 'risk': snapshot['neg_risk'] = True
    if mutation == 'exchange': snapshot['exchange'] = NEG_RISK_EXCHANGE
    if mutation == 'digest': snapshot['fee_evidence']['fd']['r'] = '.01'
    if mutation == 'source': snapshot['fee_evidence']['source'] = 'unsupported'
    if mutation == 'model': snapshot['fee_evidence']['version'] = 2
    if mutation == 'future': snapshot['received_at'] += 1
    with pytest.raises((EvidenceError, ExchangeError)):
        import_observed(rig, book, snapshot)
    assert priced(rig, book) is None


def test_fee_expiry_fails_closed(rig):
    book, snapshot = observed(rig)
    import_observed(rig, book, snapshot)
    rig[1][0] += 11
    with pytest.raises(EvidenceError):
        priced(rig, book)


def test_new_book_receipt_cannot_reuse_prior_fee_import(rig):
    book, snapshot = observed(rig)
    import_observed(rig, book, snapshot)
    store, _, kwargs, _ = rig
    target = contract_target(kwargs['rule'], kwargs['market_id'], 'YES')
    store.capture('new-public-raw', event_id=kwargs['rule'].payload['event_id'],
        kind='BOOK', provider=PROVIDER, source_identity=target['token_id'],
        revision='new', evidence_class='PUBLIC_OBSERVED', payload={})
    with pytest.raises(EvidenceError, match='SUPERSEDED'):
        priced(rig, book)


def test_current_request_factory_adds_only_the_imported_fee(factory):
    r = factory()
    store, now, rule = r['store'], r['now'], r['rule']
    target = contract_target(rule, r['request'].market_id, 'YES')
    response = dict(market=target['condition_id'], asset_id=target['token_id'],
        timestamp=str(int(now[0]*1000)), hash='public-hash',
        bids=[dict(price='.1', size='20')], asks=[dict(price='.2', size='20')],
        tick_size='.01', min_order_size='1', neg_risk=False)
    raw = store.capture('fee-public-raw', event_id=rule.payload['event_id'], kind='BOOK',
        provider=PROVIDER, source_identity=target['token_id'], revision='paper',
        evidence_class='PUBLIC_OBSERVED', payload=dict(endpoint=ENDPOINT,
            request_params={'token_id': target['token_id']}, http_status=200,
            response_format='JSON', source_time_status='NOT_YET_NORMALIZED', response=response))
    book = normalize_book_capture(store, raw['id'], rule=rule, token_id=target['token_id'],
        collateral_asset='FIXTURE_COLLATERAL', policy=BookPolicy('paper-fee-test'))
    proof = make_fee_evidence(EXCHANGE_PUBLISHED_SCHEDULE, token=target['token_id'],
        condition=target['condition_id'], exchange=STANDARD_EXCHANGE, observed_at=now[0],
        fd={'r': '.05', 'e': '1', 'to': True}, max_fee_bps=0,
        max_fee_block={'number': 100, 'hash': 'observed-block'},
        maker_base_fee_bps=0, taker_base_fee_bps=0)
    snapshot = dict(fee_policy=EXCHANGE_PUBLISHED_SCHEDULE, fee_evidence=proof,
        token=target['token_id'], condition=target['condition_id'], market=target['condition_id'],
        exchange=STANDARD_EXCHANGE, max_fee_bps=0, received_at=now[0], neg_risk=False)
    import_fee_snapshot(store, 'factory-fee', rule=rule, market_id=target['market_id'],
        side='YES', book_id=book['id'], snapshot=snapshot, max_age_seconds=10.)
    q = queue(r)
    a = RequestAssembler(q, inputs(r), book_provider=PROVIDER,
        valuation_policy=r['request'].valuation_policy, lifetime_seconds=10.)
    planned = TargetPlan(target['market_id'], 'YES', '2', '2', ())
    f = EntryRequestFactory(a, (planned,))
    q.publish('fee-book-update', kind='BOOK', evidence_id=book['id'])
    with q.work('fee-claim') as claim:
        state_for(r, (book['id'],), ('model2', 'official2'), key='fee-risk')
        request, = f(claim)
    assert request.book_id == book['id']
    assert len(request.costs) == 1 and request.costs[0].covers == ('ACQUISITION_FEES',)
    assert Decimal(request.costs[0].per_share) == Decimal('.016')
