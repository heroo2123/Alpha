from dataclasses import replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, ReleaseBinding, canonical, digest
from polymarket_scanner.v11.probability import BucketPrediction, NEXT_OBSERVATION
from polymarket_scanner.v11.valuation import (
    CostComponent, ENTRY_RISKS, HOLD_RISKS, SALE_RISKS, PAYOUT, SALE,
    ValuationPolicy, contract_target, settlement_entry, compare_hold_sale, repricing_status, buy_fee_cost,
)
from test_v11_probability import rule, predict, component, T


def costs(risks=ENTRY_RISKS, horizon=PAYOUT, **values):
    return tuple(CostComponent(r, horizon, values.get(r, '0'), (r,), 'd'*64) for r in sorted(risks))


@pytest.fixture
def rig(tmp_path):
    tmp_path.chmod(0o700)
    now = [T+110.]
    store = EvidenceStore(tmp_path/'evidence.sqlite', 'V11_PAPER', clock=lambda: now[0])
    r = rule()
    target = contract_target(r, r.payload['partition'][0]['market_id'], 'YES')
    payload = dict(target, rule_fingerprint=r.sha256, collateral_asset='FIXTURE_COLLATERAL', stream_healthy=True,
                   asks=[{'price': '.2', 'size': '2'}, {'price': '.4', 'size': '3'}],
                   bids=[{'price': '.1', 'size': '10'}])
    store.capture('book', event_id=r.payload['event_id'], kind='BOOK', provider='fixture',
                  source_identity=target['token_id'], revision='1', observed_at=now[0], payload=payload,
                  evidence_class='SYNTHETIC')
    kwargs = dict(rule=r, prediction=predict(r), binding=ReleaseBinding('a'*40, 'b'*40, 'c'*64, 'c'*64, r.sha256),
                  market_id=target['market_id'], side='YES', units='5', book_id='book',
                  policy=ValuationPolicy('fixture-1', 'FIXTURE_COLLATERAL', 10., 120., '.01', '20'))
    return store, now, kwargs, payload


def entry(rig, **changes):
    store, _, kwargs, _ = rig
    return settlement_entry(store, 'entry', **{**kwargs, 'costs': costs(), **changes})['body']['details']


def test_depth_walk_and_costs_are_charged_once_not_midpoint(rig):
    result = entry(rig, costs=costs(ACQUISITION_FEES='.01', POST_SNAPSHOT_SLIPPAGE='.02',
                                   SETTLEMENT_REVISION='.03', SOURCE_FALLBACK='.04',
                                   OPPORTUNITY_RISK='.05', EXECUTION_UNCERTAINTY='.06', REDEMPTION_COST='.01'))
    assert result['book']['gross_value'] == '1.6'
    assert Decimal(result['conservative_ev_per_share']) == Decimal('-.54')
    assert Decimal(result['conservative_ev_total']) == Decimal('-2.7')
    assert result['outcome'] == 'REJECT'
    assert result['conservative_payout_per_share'] == '0.0'
    assert result['costs']['already_included'] == ['DEPTH_WALK', 'MODEL_UNCERTAINTY']
    assert result['trading_pnl'] is None and not result['financial_authority']


@pytest.mark.parametrize('risk', sorted(ENTRY_RISKS))
def test_each_unknown_cost_gates_economics_and_never_defaults_to_zero(rig, risk):
    result = entry(rig, costs=costs(**{risk: None}))
    assert result['outcome'] == 'GATED' and result['conservative_ev_per_share'] is None
    assert result['costs']['unknown'] == [risk]


def test_missing_coverage_and_duplicate_risk_are_distinct(rig):
    result = entry(rig, costs=costs(ENTRY_RISKS-{'SETTLEMENT_REVISION'}))
    assert result['costs']['missing'] == ['SETTLEMENT_REVISION']
    assert result['outcome'] == 'GATED'
    store, _, kwargs, _ = rig
    duplicate = CostComponent('another', PAYOUT, '.1', ('MODEL_UNCERTAINTY',), 'e'*64)
    with pytest.raises(EvidenceError, match='DOUBLE_COUNT'):
        settlement_entry(store, 'duplicate', **kwargs, costs=costs()+(duplicate,))


def test_partial_depth_never_produces_full_size_ev(rig):
    result = entry(rig, units='6')
    assert result['book']['visible_units'] == '5' and result['conservative_ev_per_share'] is None
    assert result['reasons'] == ['INSUFFICIENT_VISIBLE_DEPTH']


def test_oversize_skips_without_silently_clamping_or_using_rewards(rig):
    result = entry(rig, policy=replace(rig[2]['policy'], maximum_units='4'))
    assert result['units'] == '5' and result['outcome'] == 'GATED'
    assert result['rewards_for_spendable_cash'] == '0' and result['incremental_reward_ev'] is None
    assert 'REQUEST_EXCEEDS_APPROVED_SIZE_SKIP' in result['reasons']


@pytest.mark.parametrize('update,reason', [
    ({'token_id': 'wrong'}, 'TARGET_MISMATCH'), ({'condition_id': 'wrong'}, 'TARGET_MISMATCH'),
    ({'rule_fingerprint': 'f'*64}, 'TARGET_MISMATCH'), ({'collateral_asset': 'other'}, 'TARGET_MISMATCH'),
])
def test_exact_token_condition_rule_and_collateral_binding(rig, update, reason):
    store, now, kwargs, payload = rig
    store.capture('wrong', event_id=kwargs['rule'].payload['event_id'], kind='BOOK', provider='fixture',
                  source_identity='fixture', revision='2', observed_at=now[0], payload=dict(payload, **update),
                  evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError, match=reason):
        entry(rig, book_id='wrong')


@pytest.mark.parametrize('update,reason', [
    ({'stream_healthy': False}, 'BOOK_STREAM_UNSYNCHRONIZED'),
    ({'bids': []}, 'BOOK_SIDE_MISSING'),
    ({'bids': [{'price': '.3', 'size': '10'}]}, 'BOOK_CROSSED_OR_LOCKED'),
])
def test_broken_book_is_gated(rig, update, reason):
    store, now, kwargs, payload = rig
    store.capture('bad', event_id=kwargs['rule'].payload['event_id'], kind='BOOK', provider='fixture',
                  source_identity='fixture', revision='2', observed_at=now[0], payload=dict(payload, **update),
                  evidence_class='SYNTHETIC')
    result = entry(rig, book_id='bad')
    assert result['reasons'] == [reason] and result['conservative_ev_per_share'] is None


def test_stale_book_and_stale_model_have_no_current_valuation(rig):
    rig[1][0] += 11
    assert entry(rig)['reasons'] == ['BOOK_STALE_OR_NONCAUSAL']
    rig[1][0] += 120
    with pytest.raises(EvidenceError, match='PREDICTION_STALE'):
        entry(rig)


def test_observation_forecast_is_not_final_payout_or_executable_exit(rig):
    r = rig[2]['rule']
    p = predict(r, (component(r, target=NEXT_OBSERVATION),), target=NEXT_OBSERVATION)
    with pytest.raises(EvidenceError, match='OBSERVATION_IS_NOT_SETTLEMENT_OR_EXIT'):
        entry(rig, prediction=p)
    assert repricing_status()['outcome'] == 'GATED'
    assert repricing_status()['expected_exit_value'] is None


def test_vacuous_bounds_cannot_be_relabelled_as_confidence(rig):
    p = rig[2]['prediction'].payload
    p['buckets'][0]['lower'] = .1
    p['buckets'][0]['point'] = max(.1, p['buckets'][0]['point'])
    changed = BucketPrediction(canonical(p), digest(p))
    with pytest.raises(EvidenceError, match='VACUOUS_BOUND_CONTRACT'):
        entry(rig, prediction=changed)


def test_no_uses_correct_token_and_one_minus_upper(rig):
    store, now, kwargs, payload = rig
    target = contract_target(kwargs['rule'], kwargs['market_id'], 'NO')
    store.capture('no', event_id=kwargs['rule'].payload['event_id'], kind='BOOK', provider='fixture',
                  source_identity=target['token_id'], revision='1', observed_at=now[0],
                  payload=dict(payload, **target), evidence_class='SYNTHETIC')
    result = entry(rig, side='NO', book_id='no')
    assert result['model']['interval']['lower'] == 0
    assert result['target'] == target


def test_sale_compares_prospective_values_but_preserves_sunk_basis_in_lifetime_pnl(rig):
    store, _, kwargs, _ = rig
    common = dict(kwargs, held_units='10', hold_costs=costs(HOLD_RISKS),
                  sale_costs=costs(SALE_RISKS, SALE, EXIT_FEES='.01', POST_SNAPSHOT_SLIPPAGE='.01'),
                  thesis_reason='SOURCE_REVISION')
    a = compare_hold_sale(store, 'sale1', **common, held_all_in_cost_basis='8')['body']['details']
    b = compare_hold_sale(store, 'sale2', **common, held_all_in_cost_basis='6')['body']['details']
    assert a['net_sale_per_share'] == b['net_sale_per_share'] == '0.08'
    assert a['sale_advantage_per_share'] == b['sale_advantage_per_share']
    assert Decimal(a['hypothetical_lifetime_pnl']) == Decimal('-3.6')
    assert Decimal(b['hypothetical_lifetime_pnl']) == Decimal('-2.6')
    assert a['remaining_units_if_fully_sold'] == '5'
    assert a['outcome'] == 'REDUCE_RESEARCH_CANDIDATE'
    assert a['realized_pnl'] is None and not a['actual_inventory_changed'] and not a['cancellation_is_exit']


def test_sale_requires_held_inventory_and_future_costs_not_entry_fee(rig):
    store, _, kwargs, _ = rig
    common = dict(kwargs, held_units='4', held_all_in_cost_basis='1',
                  hold_costs=costs(HOLD_RISKS), sale_costs=costs(SALE_RISKS, SALE), thesis_reason='SOURCE_REVISION')
    with pytest.raises(EvidenceError, match='HELD_INVENTORY'):
        compare_hold_sale(store, 'sale', **common)
    with pytest.raises(EvidenceError, match='UNEXPECTED'):
        compare_hold_sale(store, 'entry-fee', **dict(common, held_units='10', hold_costs=costs()))


def test_wrong_horizon_costs_cannot_enter_sale(rig):
    store, _, kwargs, _ = rig
    with pytest.raises(EvidenceError, match='HORIZON'):
        compare_hold_sale(store, 'wrong', **kwargs, held_units='10', held_all_in_cost_basis='1',
                          hold_costs=costs(HOLD_RISKS), sale_costs=costs(SALE_RISKS, PAYOUT),
                          thesis_reason='SOURCE_REVISION')


def test_existing_buy_fee_policy_is_reused_without_sell_or_maker_assumptions(rig):
    from polymarket_scanner.production.fees import make_fee_evidence, EXCHANGE_PUBLISHED_SCHEDULE
    store, now, kwargs, _ = rig
    target = contract_target(kwargs['rule'], kwargs['market_id'], 'YES')
    proof = make_fee_evidence(EXCHANGE_PUBLISHED_SCHEDULE, token=target['token_id'], condition=target['condition_id'],
                             exchange='fixture', observed_at=now[0], fd={'r': '.05', 'e': '1', 'to': True},
                             max_fee_bps=0, max_fee_block={'number': 100, 'hash': 'fixture'},
                             maker_base_fee_bps=0, taker_base_fee_bps=0)
    snapshot = dict(fee_policy=EXCHANGE_PUBLISHED_SCHEDULE, fee_evidence=proof, token=target['token_id'],
                    condition=target['condition_id'], exchange='fixture', max_fee_bps=0, received_at=now[0])
    args = dict(target=target, as_of=now[0], max_age_seconds=10., limit_price='.4', post_only=False)
    fee = buy_fee_cost(snapshot, **args)
    assert Decimal(fee.per_share) == Decimal('.024')
    result = entry(rig, costs=costs(ENTRY_RISKS-{'ACQUISITION_FEES'})+(fee,))
    assert Decimal(result['conservative_ev_per_share']) == Decimal('-.344')
    wrong = buy_fee_cost(snapshot, **dict(args, post_only=True))
    result = settlement_entry(store, 'maker-fee', **kwargs,
                              costs=costs(ENTRY_RISKS-{'ACQUISITION_FEES'})+(wrong,))['body']['details']
    assert result['outcome'] == 'GATED' and 'BUY_FEE_EXECUTION_SCOPE_MISMATCH' in result['reasons']
    with pytest.raises(EvidenceError, match='STALE_OR_FUTURE'):
        buy_fee_cost(snapshot, **dict(args, as_of=now[0]+11))
