from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11.basket_valuation import BasketLeg, BasketPolicy, analyze_basket
from polymarket_scanner.v11.evidence import EvidenceError, ReleaseBinding, canonical, digest
from polymarket_scanner.v11.probability import BucketPrediction, NEXT_OBSERVATION
from polymarket_scanner.v11.rules import RuleGuard
from polymarket_scanner.v11.scenario_risk import Attribution, Position, PendingOrder
from polymarket_scanner.v11.valuation import ValuationPolicy, contract_target
from test_v11_certification_rules import setup, observe_rule
from test_v11_probability import component, predict, T
from test_v11_valuation import costs
from test_weather_final_gpt6_exact_replays import _event


@pytest.fixture
def rig(setup):
    store, registry, scope, metadata, now = setup
    now[0] = T+110
    rule = observe_rule(store, RuleGuard(store), _event(station='KATL'), metadata, 'rules')
    legs = []
    for i, bucket in enumerate(rule.payload['partition']):
        target = contract_target(rule, bucket['market_id'], 'YES')
        key = 'book'+str(i)
        store.capture(key, event_id=rule.payload['event_id'], kind='BOOK', provider='fixture', source_identity=target['token_id'],
                revision='1', observed_at=now[0], evidence_class='SYNTHETIC', payload=dict(target,
                rule_fingerprint=rule.sha256, collateral_asset='FIXTURE_COLLATERAL', stream_healthy=True,
                asks=[dict(price='.2', size='10')], bids=[dict(price='.1', size='10')]))
        legs.append(BasketLeg(bucket['market_id'], 'YES', '2', key, costs()))
    kwargs = dict(rule=rule, prediction=predict(rule), binding=ReleaseBinding('a'*40, 'b'*40, 'c'*64, 'c'*64, rule.sha256),
                  strategy='CROSS_TEMP_RELATIVE_VALUE', account_id='account', legs=tuple(legs),
                  policy=BasketPolicy(ValuationPolicy('test-policy', 'FIXTURE_COLLATERAL', 30., 120., '.01', '20'), 2., 120., '.01'))
    return dict(store=store, now=now, kw=kwargs)


def analyze(rig, key='basket', **changes):
    return analyze_basket(rig['store'], key, **{**rig['kw'], **changes})['body']['details']


def replace_book(rig, index, *, observed=None, **payload_change):
    legs = list(rig['kw']['legs']); row = rig['store'].get(legs[index].book_id); b = row['body']
    key = row['id']+'-new'
    rig['store'].capture(key, event_id=row['event_id'], kind='BOOK', provider=b['provider'], source_identity=b['source_identity'],
            revision='new', observed_at=rig['now'][0] if observed is None else observed,
            payload=dict(b['payload'], **payload_change), evidence_class='SYNTHETIC')
    legs[index] = replace(legs[index], book_id=key)
    return tuple(legs)


def test_complete_basket_floor_does_not_remove_unfilled_leg_risk(rig):
    d = analyze(rig)
    assert Decimal(d['conditional_full_fill_payout_floor']) == 2
    assert Decimal(d['full_fill_all_in_cost']) == Decimal('1.2')
    assert Decimal(d['conservative_full_fill_ev_total']) == Decimal('.8')
    assert d['constant_payout_after_all_legs_filled'] and d['full_fill_economics'] == 'CANDIDATE'
    partial = d['adverse_partial_fill_scenarios']
    assert Decimal(partial['after']['worst_case_loss']) == Decimal('.8')
    assert Decimal(partial['after']['reserved_buy_cash']) == Decimal('1.2')
    assert all(row['conservative_single_leg_payout_per_share'] == '0' for row in d['legs'])
    assert d['outcome'] == 'GATED' and d['proposal'] is None and d['locked_executable_profit'] is None
    assert d['trading_pnl'] is None and not d['financial_authority']


def test_missing_bucket_is_not_an_exhaustive_floor_or_independent_hedge(rig):
    d = analyze(rig, legs=rig['kw']['legs'][:2])
    assert Decimal(d['conditional_full_fill_payout_floor']) == 0
    assert Decimal(d['conservative_full_fill_ev_total']) == Decimal('-.8')
    assert d['full_fill_economics'] == 'REJECT' and not d['constant_payout_after_all_legs_filled']


def test_binary_yes_no_pair_uses_same_market_complement(rig):
    rule = rig['kw']['rule']; first = rig['kw']['legs'][0]
    target = contract_target(rule, first.market_id, 'NO')
    old = rig['store'].get(first.book_id)['body']['payload']
    rig['store'].capture('no-book', event_id=rule.payload['event_id'], kind='BOOK', provider='fixture',
            source_identity=target['token_id'], revision='1', observed_at=rig['now'][0],
            payload={**old, **target}, evidence_class='SYNTHETIC')
    no = replace(first, side='NO', book_id='no-book')
    d = analyze(rig, strategy='STRUCTURAL', legs=(first, no))
    assert d['constant_payout_after_all_legs_filled']
    assert Decimal(d['conditional_full_fill_payout_floor']) == 2
    assert Decimal(d['adverse_partial_fill_scenarios']['after']['worst_case_loss']) == Decimal('.4')


def test_unequal_complete_set_quantities_have_only_minimum_common_floor(rig):
    legs = list(rig['kw']['legs']); legs[0] = replace(legs[0], units='3')
    d = analyze(rig, legs=tuple(legs))
    assert Decimal(d['conditional_full_fill_payout_floor']) == 2
    assert not d['constant_payout_after_all_legs_filled']
    assert Decimal(d['conservative_full_fill_ev_total']) == Decimal('.6')


def test_depth_walk_and_per_leg_costs_are_not_replaced_by_best_ask_underround(rig):
    legs = replace_book(rig, 0, asks=[dict(price='.2', size='1'), dict(price='.8', size='1')])
    legs = tuple(replace(leg, costs=costs(ACQUISITION_FEES='.01', POST_SNAPSHOT_SLIPPAGE='.02')) for leg in legs)
    d = analyze(rig, legs=legs)
    assert Decimal(d['full_fill_all_in_cost']) == Decimal('1.98')
    assert Decimal(d['conservative_full_fill_ev_total']) == Decimal('.02')
    assert Decimal(d['adverse_partial_fill_scenarios']['after']['reserved_buy_cash']) == Decimal('2.58')
    assert Decimal(d['adverse_partial_fill_scenarios']['after']['worst_case_loss']) > 0


@pytest.mark.parametrize('change,reason', [
    ({'asks':[dict(price='.2', size='1')]}, 'INSUFFICIENT_VISIBLE_DEPTH'),
    ({'stream_healthy':False}, 'BOOK_STREAM_UNSYNCHRONIZED'),
    ({'bids':[]}, 'BOOK_SIDE_MISSING'),
    ({'bids':[dict(price='.3', size='10')]}, 'BOOK_CROSSED_OR_LOCKED'),
])
def test_one_unexecutable_leg_gates_entire_full_fill_economics(rig, change, reason):
    d = analyze(rig, legs=replace_book(rig, 1, **change))
    assert d['full_fill_economics'] == 'GATED' and d['full_fill_scenarios'] is None
    assert any(reason in s for s in d['reasons'])


def test_unknown_fee_never_becomes_zero_when_other_legs_look_profitable(rig):
    legs = list(rig['kw']['legs']); legs[1] = replace(legs[1], costs=costs(ACQUISITION_FEES=None))
    d = analyze(rig, legs=tuple(legs))
    assert d['full_fill_all_in_cost'] is None and d['conditional_full_fill_payout_floor'] is None
    assert d['reasons'] == ['LEG_1:UNKNOWN_OR_MISSING_COST_COVERAGE']


def test_books_from_different_observation_windows_do_not_form_a_current_basket(rig):
    d = analyze(rig, legs=replace_book(rig, 1, observed=rig['now'][0]-3))
    assert d['full_fill_economics'] == 'GATED' and 'BASKET_BOOK_OBSERVATION_SKEW' in d['reasons']


def test_superseded_leg_is_gated_even_if_old_book_is_still_recent(rig):
    replace_book(rig, 0)
    assert 'LEG_0:CURRENT_EXACT_BASKET_BOOK_REQUIRED' in analyze(rig)['reasons']


def test_cross_contract_token_or_currency_is_not_a_basket_leg(rig):
    with pytest.raises(EvidenceError, match='BOOK_TARGET_MISMATCH'):
        analyze(rig, legs=replace_book(rig, 0, token_id='other-token'))


def test_duplicate_token_cannot_count_twice_toward_complete_set(rig):
    with pytest.raises(EvidenceError, match='DUPLICATE_ECONOMIC_LEG'):
        analyze(rig, legs=(rig['kw']['legs'][0],)*2)


def test_observation_only_vector_is_not_joint_settlement_value(rig):
    with pytest.raises(EvidenceError, match='OBSERVATION_IS_NOT_SETTLEMENT'):
        analyze(rig, prediction=predict(rig['kw']['rule'], target=NEXT_OBSERVATION,
                                       components=(component(rig['kw']['rule'], target=NEXT_OBSERVATION),)))


def test_incoherent_point_vector_cannot_be_renormalized_to_hide_input_error(rig):
    p = deepcopy(rig['kw']['prediction'].payload); p['buckets'][0]['point'] += .01
    with pytest.raises(EvidenceError, match='COHERENT_WHOLE_EVENT'):
        analyze(rig, prediction=BucketPrediction(canonical(p), digest(p)))


def test_existing_held_and_pending_exposure_is_one_event_scenario(rig):
    token = rig['kw']['rule'].payload['partition'][0]['yes_token']; a = (Attribution('existing', '1'),)
    held = (Position('held', token, '2', '1', a),)
    pending = (PendingOrder('ambiguous', token, 'BUY', '1', '.5', a),)
    d = analyze(rig, positions=held, pending=pending)
    before, after = (d['adverse_partial_fill_scenarios'][k] for k in ('before', 'after'))
    assert Decimal(before['worst_case_loss']) == Decimal('1.5')
    assert Decimal(after['worst_case_loss']) == Decimal('2.3')
    assert after['execution_namespace'] == 'V11_PAPER' and after['account_id'] == 'account'


def test_historical_replay_does_not_refresh_books_or_expiry(rig):
    old = analyze(rig); rig['now'][0] += 300
    assert analyze(rig) == old
    with pytest.raises(EvidenceError, match='REQUEST_ID_COLLISION'):
        analyze(rig, legs=rig['kw']['legs'][:2])


def test_racing_book_arrival_invalidates_joint_measurement_append(rig, monkeypatch):
    store = rig['store']; old = store.audit
    def race(key, **kw):
        if key == 'basket':
            replace_book(rig, 0)
        return old(key, **kw)
    monkeypatch.setattr(store, 'audit', race)
    with pytest.raises(EvidenceError, match='AUDIT_GUARDED_STATE_CHANGED'):
        analyze(rig)
