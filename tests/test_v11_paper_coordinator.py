from dataclasses import asdict, replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11.allocation import SizingFactors, size_within_ceiling, rank_candidates
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, ReleaseBinding
from polymarket_scanner.v11.event_risk import EventContext, EventRiskEngine, SafetyReductions
from polymarket_scanner.v11.paper_coordinator import PaperAccountPolicy, PaperCoordinator, Proposal
from polymarket_scanner.v11.strategy_admission import StrategyAdmission
from polymarket_scanner.v11.scenario_risk import Attribution
from polymarket_scanner.v11.valuation import ValuationPolicy, settlement_entry, compare_hold_sale, contract_target, HOLD_RISKS, SALE_RISKS, SALE
from test_v11_event_risk import policy as event_policy, metrics
from test_v11_probability import rule, predict, T
from test_v11_valuation import costs
from test_v11_scenario_risk import mapping, limits, distinct_rule


@pytest.fixture
def rig(tmp_path, monkeypatch):
    tmp_path.chmod(0o700)
    now = [T+110.]
    store = EvidenceStore(tmp_path/'paper.sqlite', 'V11_PAPER', clock=lambda: now[0])
    # Isolated downstream account fixtures; real scoped review/epoch/source
    # admission is exercised without this stub in test_v11_strategy_admission.
    def fixture_admission(self, record_id, *, context, rule, binding, strategies):
        assert record_id.startswith('fixture-admission-') and strategies == ('fixture',)
        assert binding['rule_fingerprint'] == rule.sha256
        return dict(strategy='fixture', heads=[], model_size_multiplier=1., valid_until=now[0]+60,
                    admission_id=record_id, financial_authority=False)
    monkeypatch.setattr(StrategyAdmission, 'revalidate', fixture_admission)
    r = rule()
    corr = mapping(r)
    p = PaperAccountPolicy('fixture-1', 'account', 'FIXTURE_COLLATERAL', '10', '10', '10', '10', '.01', '0', 60., 10)
    lim = limits(per_event='100', portfolio='100', per_city='100', per_region='100', per_weather_group='100',
                 per_source_group='100', per_model_group='100')
    ctx = EventContext('account', corr.memberships[0].city, r.payload['station'], r.payload['event_id'])
    binding = ReleaseBinding('a'*40, 'b'*40, 'c'*64, 'c'*64, r.sha256)
    state_id = None
    for i in range(3):
        book_id, source_id, state_id = 'risk-book'+str(i), 'risk-source'+str(i), 'risk-state'+str(i)
        store.capture(book_id, event_id=ctx.event_id, kind='BOOK', provider='fixture', source_identity='token',
                      revision=str(i), observed_at=now[0], payload={'stream_healthy': True}, evidence_class='SYNTHETIC')
        store.capture(source_id, event_id=ctx.event_id, kind='OFFICIAL_OBSERVATION', provider='fixture', source_identity=ctx.station_id,
                      revision=str(i), observed_at=now[0], payload={}, evidence_class='SYNTHETIC')
        EventRiskEngine(store).step(state_id, context=ctx, policy=event_policy(), binding=binding,
                                    metrics=metrics(now[0]), book_ids=(book_id,), source_ids=(source_id,))
        if i < 2:
            now[0] += 10
    return dict(store=store, now=now, rule=r, context=ctx, binding=binding, policy=p, limits=lim,
                correlation=corr, state_id=state_id, book_id=book_id, source_id=source_id)


def coordinator(rig, **kw):
    return PaperCoordinator(rig['store'], policy=kw.get('policy', rig['policy']),
                            correlation=kw.get('correlation', rig['correlation']), limits=kw.get('limits', rig['limits']))


def proposal(rig, pid='proposal', *, units='20', bucket=0, ev='.2', direction='BUY', thesis=None, desired=None):
    store, r, now = rig['store'], rig['rule'], rig['now'][0]
    target = contract_target(r, r.payload['partition'][bucket]['market_id'], 'YES')
    book_id = 'book-'+pid
    store.capture(book_id, event_id=r.payload['event_id'], kind='BOOK', provider='fixture', source_identity=target['token_id'],
                  revision=pid, observed_at=now, payload=dict(target, rule_fingerprint=r.sha256,
                  collateral_asset='FIXTURE_COLLATERAL', stream_healthy=True,
                  asks=[{'price': '.4', 'size': '100'}], bids=[{'price': '.3', 'size': '100'}]), evidence_class='SYNTHETIC')
    kw = dict(rule=r, prediction=predict(r), binding=rig['binding'], market_id=target['market_id'], side='YES',
              units=units, book_id=book_id, policy=ValuationPolicy('fixture', 'FIXTURE_COLLATERAL', 10., 120., '.01', '100'))
    if direction == 'BUY':
        calculated = settlement_entry(store, 'unqualified-'+pid, **kw, costs=costs())['body']['details']
        assert calculated['outcome'] == 'REJECT'  # Actual current vacuous model cannot admit.
        calculated.update(outcome='ACCEPT_RESEARCH', conservative_ev_per_share=ev,
                          conservative_ev_total=str(Decimal(ev)*Decimal(units)))
    else:
        calculated = compare_hold_sale(store, 'comparison-'+pid, **kw, held_units='100', held_all_in_cost_basis='40',
                                        hold_costs=costs(HOLD_RISKS), sale_costs=costs(SALE_RISKS, SALE),
                                        thesis_reason='SYNTHETIC_TEST')['body']['details']
        calculated.update(outcome='REDUCE_RESEARCH_CANDIDATE', sale_advantage_per_share=ev)
    # Explicit synthetic fixture for downstream account mechanics, not evidence
    # that the current uncalibrated model qualified an economic opportunity.
    calculated['synthetic_downstream_test_fixture'] = True
    store.audit('value-'+pid, event_id=r.payload['event_id'], kind='MEASUREMENT', details=calculated, evidence_ids=(book_id,))
    if desired is None:
        held = sum(Decimal(p['units']) for p in coordinator(rig)._state(coordinator(rig)._head())['lots'].values()
                   if p['token_id'] == target['token_id'])
        desired = units if direction == 'BUY' else str(max(Decimal(0), held-Decimal(units)))
    return Proposal(pid, thesis or 'thesis-'+pid, rig['context'], r, 'value-'+pid, rig['state_id'],
                    (Attribution('fixture', '1'),), now+30, desired, ('fixture-admission-'+pid,))


def proof(rig, pid, key, kind, **fields):
    intent = coordinator(rig)._state(coordinator(rig)._head())['intents'][pid]
    rig['store'].capture(key, event_id=intent['event_id'], kind='TRADE', provider='fixture-paper-engine',
                          source_identity=pid, revision=key, observed_at=rig['now'][0], evidence_class='SYNTHETIC',
                          payload=dict(record_type=kind, execution_namespace='V11_PAPER', account_id='account',
                                       intent_id=pid, token_id=intent['token_id'], **fields))
    return key


def fill(rig, pid, key='fill', units='5', collateral='2', direction='BUY'):
    return coordinator(rig).record_fill('record-'+key, proof(rig, pid, key, 'PAPER_FILL', fill_id=key,
                                                            units=units, all_in_collateral=collateral, direction=direction))


def test_ranks_before_reserving_and_reserves_only_within_whole_account_cash(rig):
    weak = proposal(rig, 'weak', ev='.1', bucket=1)
    strong = proposal(rig, 'strong', ev='.2')
    result = coordinator(rig).coordinate('batch', (weak, strong))['body']['details']
    assert [p['proposal_id'] for p in result['ranking']] == ['strong', 'weak']
    assert result['reserved_intent_ids'] == ['strong']
    assert Decimal(result['risk']['reserved_cash']) == 8
    assert result['results'][-1]['reason'] == 'ACCOUNT_SCENARIO_OR_RESERVATION_LIMIT'
    assert result['execution_status'] == 'NOT_SUBMITTED'


def test_duplicate_thesis_or_explanation_does_not_duplicate_economic_intent(rig):
    p = proposal(rig, 'one', units='5')
    q = proposal(rig, 'two', units='5', thesis=p.thesis_id)
    c = coordinator(rig)
    first = c.coordinate('batch', (p, q))
    assert len(first['body']['details']['reserved_intent_ids']) == 1
    assert c.coordinate('batch', (p, q)) == first
    again = c.coordinate('another', (p,))['body']['details']
    assert not again['reserved_intent_ids']
    assert Decimal(c.snapshot()['reserved_cash']) == 2


def test_actual_vacuous_model_valuation_stays_rejected_by_coordinator(rig):
    p = proposal(rig, units='5')
    result = coordinator(rig).coordinate('batch', (replace(p, valuation_id='unqualified-'+p.proposal_id),))['body']['details']
    assert result['reserved_intent_ids'] == []
    assert result['results'][0]['reason'] == 'PROPOSAL_ECONOMICS_NOT_QUALIFIED'


def test_expiry_timeout_restart_and_cancel_request_never_release_reservations(rig):
    p = proposal(rig)
    c = coordinator(rig); c.coordinate('batch', (p,))
    c.transition('submit', intent_id=p.proposal_id, status='SUBMITTING')
    coordinator(rig).recover('restart')
    with pytest.raises(EvidenceError, match='CANNOT_BE_RETRIED'):
        c.transition('retry', intent_id=p.proposal_id, status='SUBMITTING')
    c.transition('cancel', intent_id=p.proposal_id, status='CANCEL_REQUESTED')
    rig['now'][0] += 600
    assert Decimal(coordinator(rig).snapshot()['reserved_cash']) == 8
    terminal = proof(rig, p.proposal_id, 'terminal', 'PAPER_TERMINAL', status='CANCELED',
                     cumulative_fill_units='0', all_fills_reconciled=True, terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL')
    c.reconcile_terminal('reconcile', terminal)
    assert Decimal(c.snapshot()['reserved_cash']) == 0


def test_partial_fill_cash_inventory_and_remaining_reservation_are_atomic_and_idempotent(rig):
    p = proposal(rig); c = coordinator(rig); c.coordinate('batch', (p,))
    c.transition('submit', intent_id=p.proposal_id, status='SUBMITTING')
    result = fill(rig, p.proposal_id)['body']['details']
    assert Decimal(result['risk']['cash']) == 8
    assert Decimal(result['risk']['reserved_cash']) == 6
    assert Decimal(result['risk']['held_cost_basis']) == 2
    assert Decimal(result['risk']['unreserved_cash']) == 2
    c.record_fill('again', 'fill')
    assert len(c._state(c._head())['lots']) == 1 and Decimal(c.snapshot()['cash']) == 8


def test_cancel_remains_requested_after_partial_fill_and_late_ack(rig):
    p = proposal(rig); c = coordinator(rig); c.coordinate('batch', (p,))
    c.transition('submit', intent_id=p.proposal_id, status='SUBMITTING')
    c.transition('cancel', intent_id=p.proposal_id, status='CANCEL_REQUESTED')
    fill(rig, p.proposal_id)
    c.transition('ack', intent_id=p.proposal_id, status='ACKNOWLEDGED')
    assert c._state(c._head())['intents'][p.proposal_id]['status'] == 'CANCEL_REQUESTED'
    assert Decimal(c.snapshot()['reserved_cash']) == 6


def test_actual_paper_fee_overrun_is_preserved_and_faults_new_admission(rig):
    p = proposal(rig); c = coordinator(rig); c.coordinate('batch', (p,))
    result = fill(rig, p.proposal_id, collateral='3')['body']['details']
    assert 'ACTUAL_PAPER_COST_EXCEEDED_RESERVED_BOUND' in result['state']['faults']
    assert Decimal(c.snapshot()['cash']) == 7 and Decimal(c.snapshot()['held_cost_basis']) == 3
    second = proposal(rig, 'next', units='1', bucket=1)
    assert c.coordinate('blocked', (second,))['body']['details']['results'][0]['reason'] == 'PAPER_ACCOUNT_FAULT_ACTIVE'


def test_terminal_requires_all_fills_reconciled_not_local_expiry(rig):
    p = proposal(rig); c = coordinator(rig); c.coordinate('batch', (p,))
    fill(rig, p.proposal_id)
    bad = proof(rig, p.proposal_id, 'bad-terminal', 'PAPER_TERMINAL', status='CANCELED',
                cumulative_fill_units='0', all_fills_reconciled=True, terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL')
    with pytest.raises(EvidenceError, match='COMPLETE_MATCHED_FILL'):
        c.reconcile_terminal('bad', bad)
    assert Decimal(c.snapshot()['reserved_cash']) == 6


def test_concurrent_account_reservation_uses_cas_and_preserves_winning_writer(rig, monkeypatch):
    p = proposal(rig, 'first'); q = proposal(rig, 'racer', bucket=1)
    c = coordinator(rig); original = rig['store'].audit; once = [False]
    def racing(record_id, **kwargs):
        if record_id == 'batch' and not once[0]:
            once[0] = True; c.coordinate('other-batch', (q,))
        return original(record_id, **kwargs)
    monkeypatch.setattr(rig['store'], 'audit', racing)
    with pytest.raises(EvidenceError, match='AUDIT_STATE_CHANGED'):
        c.coordinate('batch', (p,))
    assert set(c._state(c._head())['intents']) == {'racer'}
    assert Decimal(c.snapshot()['reserved_cash']) == 8


def test_operator_reduction_racing_admission_is_guarded_inside_same_transaction(rig, monkeypatch):
    p = proposal(rig); original = rig['store'].audit; once = [False]
    def racing(record_id, **kwargs):
        if record_id == 'batch' and not once[0]:
            once[0] = True
            SafetyReductions(rig['store']).apply('halt', scope='ACCOUNT', scope_id='account', action='NO_NEW_ORDERS',
                                                actor='fixture', reason='test')
        return original(record_id, **kwargs)
    monkeypatch.setattr(rig['store'], 'audit', racing)
    with pytest.raises(EvidenceError, match='AUDIT_GUARDED_STATE_CHANGED'):
        coordinator(rig).coordinate('batch', (p,))
    assert Decimal(coordinator(rig).snapshot()['reserved_cash']) == 0


def test_policy_cannot_be_changed_in_place_after_a_reservation(rig):
    p = proposal(rig, units='5'); coordinator(rig).coordinate('batch', (p,))
    changed = replace(rig['policy'], capital_limit='20')
    with pytest.raises(EvidenceError, match='POLICY_OR_IDENTITY_CHANGED'):
        coordinator(rig, policy=changed).snapshot()


def test_new_signal_does_not_bypass_active_operator_reduction_or_expired_state(rig):
    p = proposal(rig, units='5')
    SafetyReductions(rig['store']).apply('halt', scope='ACCOUNT', scope_id='account', action='REDUCE_ONLY',
                                        actor='fixture', reason='test')
    result = coordinator(rig).coordinate('batch', (p,))['body']['details']
    assert result['reserved_intent_ids'] == [] and result['results'][0]['reason'] == 'EVENT_OR_OPERATOR_STATE_CHANGED'


def test_sells_reserve_inventory_and_realized_paper_pnl_uses_original_basis(rig):
    buy = proposal(rig, 'buy', units='5'); c = coordinator(rig); c.coordinate('buy-batch', (buy,))
    fill(rig, 'buy')
    sell = proposal(rig, 'sell', units='3', direction='SELL')
    result = c.coordinate('sell-batch', (sell,))['body']['details']
    assert result['reserved_intent_ids'] == ['sell']
    too_much = proposal(rig, 'second-sell', units='3', direction='SELL')
    result = c.coordinate('too-much', (too_much,))['body']['details']
    assert result['results'][0]['reason'] == 'DESIRED_POSITION_ALREADY_COVERED_OR_REVALUE_SMALLER_DELTA'
    fill(rig, 'sell', 'sale-fill', units='3', collateral='.9', direction='SELL')
    state = c._state(c._head())
    assert Decimal(state['cash']) == Decimal('8.9')
    assert Decimal(state['event_realized_pnl'][rig['context'].event_id]) == Decimal('-.3')
    assert Decimal(c.snapshot()['held_cost_basis']) == Decimal('.8')
    assert sum(Decimal(p['units']) for p in state['lots'].values()) == 2


def test_public_or_cross_namespace_trade_cannot_mutate_paper_ledger(rig):
    p = proposal(rig); c = coordinator(rig); c.coordinate('batch', (p,))
    rig['store'].capture('public', event_id=rig['context'].event_id, kind='TRADE', provider='clob', source_identity='public',
                          revision='1', observed_at=rig['now'][0], payload=dict(record_type='PAPER_FILL',
                          execution_namespace='LIVE', account_id='account', intent_id=p.proposal_id,
                          token_id='token', units='5', all_in_collateral='2', direction='BUY', fill_id='bad'))
    with pytest.raises(EvidenceError, match='SYNTHETIC_PAPER'):
        c.record_fill('public-fill', 'public')
    assert Decimal(c.snapshot()['cash']) == 10


def test_sizing_is_bounded_and_rounds_down_without_increasing_any_hard_limit():
    factors = SizingFactors('1', '.5', '1', '1', '1', '1', '.5', '1', '.5', 'a'*64)
    args = dict(base_units='10', approved_max_units='10', quantity_step='.1', factors=factors, policy_sha256='b'*64)
    result = size_within_ceiling(**args)
    assert Decimal(result['units']) == Decimal('1.2') and not result['hard_limits_changed']
    assert size_within_ceiling(**dict(args, base_units='11'))['outcome'] == 'SKIP'
    with pytest.raises(EvidenceError, match='ONLY_REDUCE'):
        replace(factors, strategy_quality='1.1')


def test_ranking_uses_full_capital_density_then_deterministic_ties():
    a = dict(proposal_id='a', conservative_ev_total='2', capital_at_risk='10')
    b = dict(proposal_id='b', conservative_ev_total='3', capital_at_risk='30')
    assert [r['proposal_id'] for r in rank_candidates((b, a))] == ['a', 'b']


def test_new_thesis_cannot_duplicate_a_position_already_targeted_by_another_strategy(rig):
    p = proposal(rig, 'one', units='5'); c = coordinator(rig); c.coordinate('first', (p,))
    q = proposal(rig, 'two', units='5')
    result = c.coordinate('second', (q,))['body']['details']
    assert result['reserved_intent_ids'] == []
    assert result['results'][0]['reason'] == 'DESIRED_POSITION_ALREADY_COVERED_OR_REVALUE_SMALLER_DELTA'
    assert Decimal(c.snapshot()['reserved_cash']) == 2


def test_shared_cash_applies_across_distinct_city_day_events(rig):
    other = distinct_rule(rig['rule'])
    corr = mapping(rig['rule'], other); rig['correlation'] = corr
    alternate = dict(rig, rule=other, correlation=corr,
                      context=EventContext('account', other.payload['city'], other.payload['station'], other.payload['event_id']),
                      binding=replace(rig['binding'], rule_fingerprint=other.sha256))
    for i in range(3):
        for kind, prefix in [('BOOK', 'other-book'), ('OFFICIAL_OBSERVATION', 'other-source')]:
            rig['store'].capture(prefix+str(i), event_id=other.payload['event_id'], kind=kind, provider='fixture',
                                  source_identity=prefix, revision=str(i), observed_at=rig['now'][0],
                                  payload={'stream_healthy': True}, evidence_class='SYNTHETIC')
        alternate['state_id'] = 'other-state'+str(i)
        EventRiskEngine(rig['store']).step(alternate['state_id'], context=alternate['context'], policy=event_policy(),
                                            binding=alternate['binding'], metrics=metrics(rig['now'][0]),
                                            book_ids=('other-book'+str(i),), source_ids=('other-source'+str(i),))
        if i < 2:
            rig['now'][0] += 10
    # The original NORMAL pin still has ten seconds of declared validity; both
    # valuations below capture new books at the shared current decision epoch.
    a = proposal(rig, 'city-a'); b = proposal(alternate, 'city-b')
    c = coordinator(rig); assert c.coordinate('a', (a,))['body']['details']['reserved_intent_ids'] == ['city-a']
    assert c.coordinate('b', (b,))['body']['details']['reserved_intent_ids'] == []
    assert Decimal(c.snapshot()['reserved_cash']) == 8


def test_repeated_fractional_sales_conserve_all_original_basis_and_numeric_full_fill(rig):
    p = proposal(rig, 'buy', units='3.0'); c = coordinator(rig); c.coordinate('buy-batch', (p,))
    fill(rig, 'buy', units='3', collateral='1')
    assert c._state(c._head())['intents']['buy']['status'] == 'FILLED'
    for i in range(3):
        sell = proposal(rig, 'sell'+str(i), units='1', direction='SELL')
        assert c.coordinate('sell-batch'+str(i), (sell,))['body']['details']['reserved_intent_ids'] == [sell.proposal_id]
        fill(rig, sell.proposal_id, 'sell-fill'+str(i), units='1', collateral='.3', direction='SELL')
    state = c._state(c._head())
    assert not state['lots']
    assert Decimal(state['cash']) == Decimal('9.9')
    assert Decimal(state['event_realized_pnl'][rig['context'].event_id]) == Decimal('-.1')


def test_city_safety_scope_cannot_be_changed_to_bypass_the_metadata_bound_map(rig):
    p = proposal(rig, units='5')
    changed = replace(p, context=replace(p.context, city_id='unrelated-city'))
    result = coordinator(rig).coordinate('wrong-scope', (changed,))['body']['details']
    assert result['results'][0]['reason'] == 'PROPOSAL_CITY_METADATA_SCOPE_MISMATCH'


def test_admission_demotion_between_reservation_and_submit_retains_reservation(rig, monkeypatch):
    p = proposal(rig, units='5'); c = coordinator(rig); c.coordinate('batch', (p,))
    def demoted(*args, **kwargs):
        raise EvidenceError('STRATEGY_AUTHORITY_OR_SOURCE_CHANGED_RECOMPUTE')
    monkeypatch.setattr(StrategyAdmission, 'revalidate', demoted)
    with pytest.raises(EvidenceError, match='SOURCE_CHANGED'):
        c.transition('submit', intent_id=p.proposal_id, status='SUBMITTING')
    assert Decimal(c.snapshot()['reserved_cash']) == 2
