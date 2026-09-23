from dataclasses import replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, canonical, digest
from polymarket_scanner.v11.rules import RuleFingerprint
from polymarket_scanner.v11.scenario_risk import (
    Attribution, Position, PendingOrder, StationMembership, CorrelationMap, ScenarioLimits,
    event_scenarios, incremental_scenarios, portfolio_risk,
)
from test_v11_probability import rule


A = (Attribution('forecast', '1'),)


def view(r, positions=(), pending=(), **kwargs):
    return event_scenarios(r, execution_namespace='V11_PAPER', account_id='account', positions=positions,
                           pending=pending, **kwargs)


def limits(**changes):
    return replace(ScenarioLimits('fixture-1', '10', '20', '30', '30', '30', '30', '50', '100'), **changes)


def mapping(*rules):
    return CorrelationMap('fixture-1', 'a'*64, tuple(StationMembership(
        r.payload['station'], r.payload['city'] or 'Fixture Atlanta', 'SHARED_REGION', ('SHARED_WEATHER',),
        ('SHARED_SOURCE',), ('SHARED_MODEL',), r.payload['metadata_fingerprint']
    ) for r in rules))


def distinct_rule(r):
    p = r.payload
    p.update(event_id='other-event', station='OTHER', city='Other City')
    for b in p['partition']:
        for field in ('market_id', 'condition_id', 'yes_token', 'no_token'):
            b[field] = 'other-'+b[field]
    return RuleFingerprint(canonical(p), digest(p), r.source_event_sha256)


def test_exact_yes_no_resolution_and_shared_attribution_do_not_double_count():
    r = rule(); first = r.payload['partition'][0]
    shares = (Attribution('forecast', '.75'), Attribution('pws', '.25'))
    positions = (Position('one', first['yes_token'], '10', '3', shares),
                 Position('two', first['no_token'], '10', '6', A))
    result = view(r, positions)
    assert result['worst_case_loss'] == '0' and Decimal(result['worst_case_pnl']) == 1
    assert all(Decimal(o['held_payout']) == 10 for o in result['outcomes'])
    assert all(sum(Decimal(v) for v in o['strategy_open_exposure_pnl'].values()) == 1 for o in result['outcomes'])
    assert not result['hypothetical_payout_is_spendable_cash']


def test_open_complete_set_has_partial_fill_risk_until_all_legs_actually_fill():
    r = rule()
    pending = tuple(PendingOrder(str(i), b['yes_token'], 'BUY', '10', '.3', A)
                    for i, b in enumerate(r.payload['partition']))
    result = view(r, pending=pending)
    assert Decimal(result['reserved_buy_cash']) == 9
    assert Decimal(result['worst_case_loss']) == 6
    held = tuple(Position(str(i), b['yes_token'], '10', '3', A) for i, b in enumerate(r.payload['partition']))
    filled = view(r, held)
    assert filled['worst_case_loss'] == '0' and Decimal(filled['worst_case_pnl']) == 1


def test_sale_can_break_a_hedge_and_does_not_automatically_reduce_risk():
    r = rule(); first = r.payload['partition'][0]
    held = (Position('yes', first['yes_token'], '10', '4', A), Position('no', first['no_token'], '10', '4', A))
    order = PendingOrder('sell', first['yes_token'], 'SELL', '10', '.1', A)
    result = incremental_scenarios(r, execution_namespace='V11_PAPER', account_id='account',
                                  positions=held, pending=(), proposed=(order,))
    assert result['before']['worst_case_loss'] == '0'
    assert Decimal(result['after']['worst_case_loss']) == 7
    assert Decimal(result['incremental_worst_case_loss']) == 7


def test_two_pending_sells_cannot_reuse_the_same_inventory():
    r = rule(); token = r.payload['partition'][0]['yes_token']
    with pytest.raises(EvidenceError, match='EXCEED_HELD'):
        view(r, (Position('one', token, '10', '4', A),),
             (PendingOrder('a', token, 'SELL', '6', '.5', A), PendingOrder('b', token, 'SELL', '6', '.5', A)))


def test_partial_basket_records_fees_basis_and_realized_pnl_separately():
    r = rule(); token = r.payload['partition'][0]['yes_token']
    result = view(r, (Position('one', token, '3', '1.5', A),),
                  (PendingOrder('remainder', token, 'BUY', '7', '.51', A),), realized_event_pnl='-.2')
    assert Decimal(result['worst_case_loss']) == Decimal('5.27')
    assert Decimal(result['reserved_buy_cash']) == Decimal('3.57')
    assert result['realized_event_pnl'] == '-0.2'


@pytest.mark.parametrize('ceiling,gate', [('per_region', 'REGION'), ('per_weather_group', 'WEATHER'),
                                         ('per_source_group', 'SOURCE'), ('per_model_group', 'MODEL'),
                                         ('portfolio', 'PORTFOLIO')])
def test_different_cities_with_shared_dependencies_do_not_get_independence_credit(ceiling, gate):
    a = rule(); b = distinct_rule(a)
    views = tuple(view(r, (Position('one', r.payload['partition'][0]['yes_token'], '10', '6', A),)) for r in (a, b))
    result = portfolio_risk(views, correlation=mapping(a, b), limits=limits(**{ceiling: '10'}),
                            execution_namespace='V11_PAPER', account_id='account')
    assert not result['accepted'] and any(f['gate'] == gate+'_LOSS_LIMIT' for f in result['faults'])
    assert result['groups']['PORTFOLIO']['ALL'] == '12'


def test_future_sale_or_optimistic_other_city_gain_cannot_release_risk():
    a = rule(); b = distinct_rule(a)
    good = tuple(Position(str(i), bucket['yes_token'], '100', '1', A) for i, bucket in enumerate(a.payload['partition']))
    bad = (Position('one', b.payload['partition'][0]['yes_token'], '10', '6', A),)
    result = portfolio_risk((view(a, good), view(b, bad)), correlation=mapping(a, b), limits=limits(),
                            execution_namespace='V11_PAPER', account_id='account')
    assert result['groups']['PORTFOLIO']['ALL'] == '6'


def test_missing_station_mapping_namespace_mixing_and_duplicate_event_refused():
    a = rule(); b = distinct_rule(a); av = view(a)
    with pytest.raises(EvidenceError, match='MAPPING_UNVERIFIED'):
        portfolio_risk((view(b),), correlation=mapping(a), limits=limits(), execution_namespace='V11_PAPER', account_id='account')
    with pytest.raises(EvidenceError, match='CROSS_NAMESPACE'):
        portfolio_risk((av,), correlation=mapping(a), limits=limits(), execution_namespace='CHALLENGER:other', account_id='account')
    with pytest.raises(EvidenceError, match='DUPLICATE_EVENT'):
        portfolio_risk((av, av), correlation=mapping(a), limits=limits(), execution_namespace='V11_PAPER', account_id='account')
    with pytest.raises(EvidenceError, match='NONFINANCIAL'):
        event_scenarios(a, execution_namespace='LIVE', account_id='account', positions=(), pending=())


def test_repeated_token_and_bad_attribution_cannot_inflate_or_hide_positions():
    r = rule(); token = r.payload['partition'][0]['yes_token']
    p = Position('one', token, '10', '3', A)
    with pytest.raises(EvidenceError, match='DUPLICATE_ECONOMIC'):
        view(r, (p, p))
    with pytest.raises(EvidenceError, match='PARTITION_ONE'):
        Position('one', token, '10', '3', (Attribution('a', '1'), Attribution('b', '1')))
    with pytest.raises(EvidenceError, match='TOKEN_MISMATCH'):
        view(r, (replace(p, token_id='unknown'),))


def test_actual_and_pending_units_share_position_cap_and_view_hash_is_checked():
    r = rule(); token = r.payload['partition'][0]['yes_token']
    v = view(r, (Position('one', token, '60', '3', A),), (PendingOrder('next', token, 'BUY', '60', '.01', A),))
    result = portfolio_risk((v,), correlation=mapping(r), limits=limits(), execution_namespace='V11_PAPER', account_id='account')
    assert any(f['gate'] == 'POSITION_UNITS' for f in result['faults'])
    v['worst_case_loss'] = '0'
    with pytest.raises(EvidenceError, match='INTEGRITY'):
        portfolio_risk((v,), correlation=mapping(r), limits=limits(), execution_namespace='V11_PAPER', account_id='account')


def test_legacy_missing_city_requires_exact_pinned_station_metadata_mapping():
    r = rule(); assert r.payload['city'] in (None, '')
    m = mapping(r)
    changed = replace(m, memberships=(replace(m.memberships[0], metadata_fingerprint='b'*64),))
    with pytest.raises(EvidenceError, match='MAPPING_UNVERIFIED'):
        portfolio_risk((view(r),), correlation=changed, limits=limits(), execution_namespace='V11_PAPER', account_id='account')
    result = portfolio_risk((view(r),), correlation=m, limits=limits(), execution_namespace='V11_PAPER', account_id='account')
    assert 'Fixture Atlanta' in result['groups']['CITY']
