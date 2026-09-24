from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.event_risk import SafetyReductions
from polymarket_scanner.v11.position_management import PositionManager, ExitRequest
from polymarket_scanner.v11.position_attribution import consume_lots
from polymarket_scanner.v11.valuation import HOLD_RISKS, SALE_RISKS, SALE
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_model_governance import authority
from test_v11_strategy_pipeline import factory
from test_v11_basket_coordinator import rig, reserve, fill, proof, replace_book
from test_v11_pws_admission import coordinator
from test_v11_valuation import costs


def inventory(rig, *, complete=False):
    reserve(rig); fill(rig, 0, '2', '.4')
    for leg in (1,2):
        if complete:
            fill(rig, leg, '2', '.4')
        else:
            key = proof(rig, 'basket:leg:'+str(leg), 'terminal'+str(leg), 'PAPER_TERMINAL',
                        status='CANCELED', cumulative_fill_units='0', all_fills_reconciled=True,
                        terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL')
            coordinator(rig).reconcile_terminal('reconcile'+str(leg), key)
    rig['now'][0] += .01


def request(rig, **changes):
    return replace(ExitRequest('pin', 'state2', rig['rule'].payload['partition'][0]['market_id'], 'YES',
           '1', 'basket-book0', rig['now'][0]+10, rig['request'].valuation_policy,
           costs(HOLD_RISKS), costs(SALE_RISKS, SALE), 'NET_SALE_EXCEEDS_HOLD'), **changes)


def evaluate(rig, key='exit', **changes):
    return PositionManager(coordinator(rig)).evaluate(key, request(rig, **changes))['body']['details']


def reserved_exit(rig, key='exit', **changes):
    d = evaluate(rig, key, **changes)
    assert d['outcome'] == 'REDUCE_RESEARCH_CANDIDATE', d['reason']
    manager = PositionManager(coordinator(rig)); p = manager.proposal(key)
    result = coordinator(rig).coordinate('reserve-'+key, (p,))['body']['details']
    assert result['reserved_intent_ids'] == [p.proposal_id], result['results']
    return p, result


def test_unhedged_exit_uses_actual_inventory_and_protected_payout_model(rig):
    inventory(rig); d = evaluate(rig)
    assert d['outcome'] == 'REDUCE_RESEARCH_CANDIDATE'
    v = d['valuation']; joint = v['position_management']['joint']
    assert Decimal(joint['before_payout_floor']) == 0
    assert Decimal(joint['advantage_at_limit_total']) == Decimal('.1')
    assert v['held_units'] == '2' and Decimal(v['held_all_in_cost_basis']) == Decimal('.4')
    assert Decimal(v['hypothetical_lifetime_pnl']) == Decimal('-.1')
    assert v['entry_costs_sunk_for_choice'] and not d['actual_inventory_changed']
    assert v['model']['prediction']['calibration_status'] == 'UNCALIBRATED'
    assert not d['financial_authority']


def test_full_basket_cannot_sell_a_hedge_using_vacuous_individual_lower_bound(rig):
    inventory(rig, complete=True); d = evaluate(rig)
    assert d['outcome'] == 'HOLD_RESEARCH_ESTIMATE'
    v = d['valuation']; joint = v['position_management']['joint']
    assert Decimal(v['single_token_sale_advantage_per_share']) == Decimal('.1')
    assert Decimal(joint['before_payout_floor']) == 2 and Decimal(joint['residual_payout_floor']) == 1
    assert Decimal(v['sale_advantage_per_share']) == Decimal('-.9')
    with pytest.raises(EvidenceError, match='NO_ECONOMIC_PROPOSAL'):
        PositionManager(coordinator(rig)).proposal('exit')


def test_pending_or_cancel_requested_hedge_requires_actual_terminal_reconciliation(rig):
    reserve(rig); fill(rig, 0, '2', '.4')
    coordinator(rig).transition('cancel', intent_id='basket:leg:1', status='CANCEL_REQUESTED')
    d = evaluate(rig)
    assert d['outcome'] == 'GATED' and d['reason'] == 'EXIT_RECONCILE_OPEN_EVENT_INTENTS_FIRST'


def test_sale_limit_not_displayed_average_controls_exit_ev(rig):
    inventory(rig)
    replace_book(rig, 0, asks=[dict(price='.5', size='20')],
                 bids=[dict(price='.4', size='.5'), dict(price='.005', size='20')])
    d = evaluate(rig, book_id='new-book')
    assert Decimal(d['valuation']['single_token_sale_advantage_per_share']) > Decimal('.2')
    assert d['outcome'] == 'HOLD_RESEARCH_ESTIMATE'
    assert Decimal(d['valuation']['sale_advantage_per_share']) == Decimal('.005')


@pytest.mark.parametrize('change', ['fees', 'depth', 'size'])
def test_missing_fees_depth_and_inventory_are_explicit_gates(rig, change):
    inventory(rig); kw = {}
    if change == 'fees': kw['sale_costs'] = costs(SALE_RISKS, SALE, EXIT_FEES=None)
    if change == 'depth':
        replace_book(rig, 0, bids=[dict(price='.1', size='.1')]); kw['book_id'] = 'new-book'
    if change == 'size': kw['units'] = '3'
    d = evaluate(rig, **kw)
    assert d['outcome'] == 'GATED' and d['proposal'] is None


def test_partial_sale_preserves_entry_exit_lineage_and_residual_cost(rig):
    inventory(rig); p, result = reserved_exit(rig)
    assert Decimal(result['state']['cash']) == Decimal('9.6')
    c = coordinator(rig); c.transition('submit-exit', intent_id=p.proposal_id, status='SUBMITTING')
    key = proof(rig, p.proposal_id, 'sale', 'PAPER_FILL', fill_id='sale', units='.5', all_in_collateral='.05', direction='SELL')
    d = c.record_fill('record-sale', key)['body']['details']; state = d['state']
    assert Decimal(state['cash']) == Decimal('9.65')
    lot = state['lots']['fill-0']; assert Decimal(lot['units']) == Decimal('1.5')
    assert Decimal(lot['all_in_cost_basis']) == Decimal('.3') and lot['entry']['ev_unit'] == 'JOINT_BASKET_TOTAL'
    realized = state['realized_entries'][0]
    assert Decimal(realized['pnl']) == Decimal('-.05')
    assert realized['exit']['reason'] == 'NET_SALE_EXCEEDS_HOLD'
    assert realized['exit']['ev'] == '0.1'
    assert realized['allocations'][0]['entry']['intent_id'] == 'basket:leg:0'
    assert Decimal(realized['allocations'][0]['strategy_realized_pnl'][0]['pnl']) == Decimal('-.05')
    assert realized['exit_attribution_is_decision_metadata_not_extra_pnl']
    assert c.record_fill('duplicate-sale', key)['body']['details']['duplicate_fill']
    c.transition('cancel-exit', intent_id=p.proposal_id, status='CANCEL_REQUESTED')
    assert Decimal(c.snapshot()['cash']) == Decimal('9.65')


def test_restart_never_resubmits_ambiguous_exit(rig):
    inventory(rig); p,_ = reserved_exit(rig)
    c = coordinator(rig); c.transition('submit', intent_id=p.proposal_id, status='SUBMITTING')
    c.recover('recover')
    with pytest.raises(EvidenceError, match='CANNOT_BE_RETRIED'):
        c.transition('retry', intent_id=p.proposal_id, status='SUBMITTING')
    assert len(c._state(c._head())['lots']) == 1


@pytest.mark.parametrize('change', ['book', 'model', 'operator', 'expiry'])
def test_exit_revalidates_changes_between_reservation_and_submission(rig, change):
    inventory(rig); p,_ = reserved_exit(rig); c = coordinator(rig)
    if change == 'book': replace_book(rig, 0)
    elif change == 'model':
        state = rig['model_state'][0]
        rig['model_state'][0] = authority.transition(state, action='DEMOTE', expected_state_sha256=digest(state), now=21., reason='TEST', size_multiplier=.5)
    elif change == 'operator':
        SafetyReductions(rig['store']).apply('halt', scope='ACCOUNT', scope_id='account', action='NO_NEW_ORDERS', actor='fixture', reason='TEST')
    else: rig['now'][0] += 11
    with pytest.raises(EvidenceError):
        c.transition('submit', intent_id=p.proposal_id, status='SUBMITTING')
    assert len(c._state(c._head())['lots']) == 1


def test_tampered_exit_value_cannot_become_account_authority(rig):
    inventory(rig); evaluate(rig); m = PositionManager(coordinator(rig)); p = m.proposal('exit')
    value = deepcopy(rig['store'].get(p.valuation_id)['body']['details'])
    value['sale_advantage_per_share'] = '.9'
    rig['store'].audit('forged', event_id=rig['context'].event_id, kind='MEASUREMENT', details=value)
    d = coordinator(rig).coordinate('reject', (replace(p, valuation_id='forged'),))['body']['details']
    assert not d['reserved_intent_ids'] and 'RECOMPUTE' in d['results'][0]['reason']


def test_plain_single_token_comparison_cannot_bypass_inventory_exit_gate(rig):
    inventory(rig); evaluate(rig); p = PositionManager(coordinator(rig)).proposal('exit')
    value = deepcopy(rig['store'].get(p.valuation_id)['body']['details']); del value['position_management']
    rig['store'].audit('plain', event_id=rig['context'].event_id, kind='MEASUREMENT', details=value)
    d = coordinator(rig).coordinate('reject', (replace(p, valuation_id='plain'),))['body']['details']
    assert d['results'][0]['reason'] == 'CURRENT_POSITION_EXIT_EVALUATION_REQUIRED'


def test_completed_evaluation_replays_without_refreshing_its_expiry(rig):
    inventory(rig); r = request(rig); manager = PositionManager(coordinator(rig))
    first = manager.evaluate('exit', r); rig['now'][0] += 100
    assert manager.evaluate('exit', r) == first
    d = coordinator(rig).coordinate('reject', (manager.proposal('exit'),))['body']['details']
    assert not d['reserved_intent_ids']
    with pytest.raises(EvidenceError, match='COLLISION'):
        manager.evaluate('exit', replace(r, reason='EDGE_CAPTURED'))


def test_racing_account_fill_invalidates_exit_commit(rig, monkeypatch):
    inventory(rig); evaluate(rig); c = coordinator(rig); p = PositionManager(c).proposal('exit')
    original = c._commit
    def race(*args, **kwargs):
        coordinator(rig).recover('racing-account-change')
        return original(*args, **kwargs)
    monkeypatch.setattr(c, '_commit', race)
    with pytest.raises(EvidenceError, match='STATE_CHANGED'):
        c.coordinate('racing-reserve', (p,))
    assert p.proposal_id not in c._state(c._head())['intents']


def test_fifo_basis_and_pnl_partitions_survive_canonical_key_order_and_rounding():
    lots = {'a-new':dict(token_id='token', units='2', all_in_cost_basis='.4', acquired_sequence=30,
                         attribution=[dict(strategy='later',weight='1')]),
            'z-old':dict(token_id='token', units='3', all_in_cost_basis='1', acquired_sequence=10,
                         attribution=[dict(strategy='first',weight='.3'),dict(strategy='second',weight='.7')])}
    basis, parts = consume_lots(lots, token_id='token', quantity=Decimal('4'), net_proceeds=Decimal('.7'))
    assert [p['lot_id'] for p in parts] == ['z-old','a-new']
    assert basis == Decimal('1.2') and lots['a-new']['units'] == '1'
    assert sum(Decimal(p['net_proceeds']) for p in parts) == Decimal('.7')
    assert sum(Decimal(p['realized_pnl']) for p in parts) == Decimal('-.5')
    for p in parts:
        assert sum(Decimal(a['pnl']) for a in p['strategy_realized_pnl']) == Decimal(p['realized_pnl'])
        assert p['entry_lineage_status'] == 'LEGACY_UNKNOWN'


def test_legacy_lots_are_not_given_invented_entry_evidence_or_fifo_age():
    lots = {'legacy':dict(token_id='token',units='3',all_in_cost_basis='1',attribution=[dict(strategy='old',weight='1')])}
    total = Decimal(0)
    for _ in range(3):
        basis, parts = consume_lots(lots, token_id='token',quantity=Decimal(1),net_proceeds=Decimal('.2'))
        total += basis
        assert parts[0]['entry'] is None and parts[0]['acquisition_order'] == 'LEGACY_ORDER_UNKNOWN'
    assert not lots and total == 1


def test_two_exit_candidates_cannot_independently_consume_same_event_hedges(rig):
    reserve(rig); fill(rig, 0, '2', '.4'); fill(rig, 1, '2', '.4')
    terminal = proof(rig, 'basket:leg:2', 'terminal', 'PAPER_TERMINAL', status='CANCELED',
                    cumulative_fill_units='0', all_fills_reconciled=True, terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL')
    coordinator(rig).reconcile_terminal('reconcile', terminal)
    evaluate(rig, 'exit-first')
    evaluate(rig, 'exit-second', market_id=rig['rule'].payload['partition'][1]['market_id'], book_id='basket-book1')
    manager = PositionManager(coordinator(rig))
    result = coordinator(rig).coordinate('batch-exits', (manager.proposal('exit-first'), manager.proposal('exit-second')))['body']['details']
    assert len(result['reserved_intent_ids']) == 1
    assert result['results'][-1]['reason'] == 'EXIT_RECONCILE_OPEN_EVENT_INTENTS_FIRST'


def test_interrupted_exit_reuses_original_valuation_and_cutoff(rig, monkeypatch):
    inventory(rig); store = rig['store']; original = store.audit; req = request(rig)
    def interrupt(key, **kw):
        if key == 'exit': raise RuntimeError('SIMULATED_INTERRUPTION')
        return original(key, **kw)
    monkeypatch.setattr(store, 'audit', interrupt)
    with pytest.raises(RuntimeError, match='SIMULATED_INTERRUPTION'):
        PositionManager(coordinator(rig)).evaluate('exit', req)
    saved = store.get('exit:valuation')
    rig['now'][0] += .1; monkeypatch.setattr(store, 'audit', original)
    d = PositionManager(coordinator(rig)).evaluate('exit', req)['body']['details']
    assert d['outcome'] == 'REDUCE_RESEARCH_CANDIDATE' and store.get('exit:valuation') == saved
    assert d['valuation']['as_of'] < rig['now'][0]


def test_queue_claim_evaluates_exit_then_requires_completion_before_reservation(rig):
    from polymarket_scanner.v11.event_queue import EventQueue, EventRoute, TriggerPolicy, KINDS
    inventory(rig); r = rig['rule']; raw = r.payload; now = rig['now'][0]
    route = EventRoute(raw['event_id'],raw['station'],raw['target_date'],raw['family'],r.sha256,
                      tuple(t for b in raw['partition'] for t in (b['yes_token'],b['no_token'])),now+60,('MODEL',))
    policy = TriggerPolicy('fixture',1,1,4,16,30.,10.,100_000,60.,
                          tuple((k,60.) for k in sorted(KINDS)),((raw['station'],30.),))
    queue = EventQueue(rig['store'],routes=(route,),policy=policy)
    queue.publish('trigger',kind='BOOK',evidence_id='basket-book0')
    manager = PositionManager(coordinator(rig))
    with queue.work('work'):
        d = manager.evaluate('exit',request(rig))['body']['details']
        assert d['outcome'] == 'REDUCE_RESEARCH_CANDIDATE'
        # No account mutation here: evaluation completion must precede admission.
        queue.finish('done',claim_id='work',result_ids=('exit',))
    result = coordinator(rig).coordinate('exit-account',(manager.proposal('exit'),))['body']['details']
    assert result['reserved_intent_ids'] == ['exit:proposal']
    assert result['state']['intents']['exit:proposal']['event_queue_completion_id'] == 'done'


def test_different_entry_basis_changes_lifetime_pnl_but_not_prospective_exit_ev(rig):
    inventory(rig); before = evaluate(rig)
    c = coordinator(rig); head = c._head(); state = c._state(head)
    # Explicit synthetic historical-lot fixture, never a financial fill.
    state['lots']['fill-0']['all_in_cost_basis'] = '.6'
    c._commit('synthetic-basis-cohort', dict(action='TEST_FIXTURE'), head, state, {})
    after = evaluate(rig, 'second-cohort')
    assert after['valuation']['sale_advantage_per_share'] == before['valuation']['sale_advantage_per_share']
    assert Decimal(after['valuation']['hypothetical_lifetime_pnl']) == Decimal('-.2')


def test_no_complement_inventory_uses_joint_yes_no_payouts(rig):
    from polymarket_scanner.v11.position_management import _joint_value
    inventory(rig); value = evaluate(rig)['valuation']; c = coordinator(rig)
    state = c._state(c._head()); first = rig['rule'].payload['partition'][0]
    # Mathematical accounting fixture for a held YES+NO complete condition.
    state['lots']['no-leg'] = dict(lot_id='no-leg',token_id=first['no_token'],event_id=rig['context'].event_id,
         units='2',all_in_cost_basis='.4',attribution=[dict(strategy='STRUCTURAL',weight='1')])
    joint = _joint_value(c,state,rig['rule'],value)
    assert Decimal(joint['before_payout_floor']) == 2
    assert Decimal(joint['residual_payout_floor']) == 1
    assert Decimal(joint['advantage_at_limit_total']) == Decimal('-.9')
