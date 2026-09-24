from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11.basket_coordinator import BasketProposal
from polymarket_scanner.v11.basket_valuation import BasketLeg, BasketPolicy, analyze_basket
from polymarket_scanner.v11.evidence import EvidenceError, canonical, digest
from polymarket_scanner.v11.event_risk import SafetyReductions
from polymarket_scanner.v11.model_artifacts import predict_with_bundle
from polymarket_scanner.v11.scenario_risk import Attribution
from polymarket_scanner.v11.paper_coordinator import PaperCoordinator, Proposal
from polymarket_scanner.v11.probability import BucketPrediction, FINAL_EXTREME
from polymarket_scanner.v11.strategy_pipeline import _model_inputs
from polymarket_scanner.v11.valuation import contract_target
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_model_governance import authority
from test_v11_strategy_pipeline import factory
from test_v11_pws_admission import coordinator
from test_v11_valuation import costs


@pytest.fixture
def rig(request, factory, bundle):
    r = factory(getattr(request, 'param', 'CROSS_TEMP_RELATIVE_VALUE'))
    store, rule, now = r['store'], r['rule'], r['now'][0]
    prediction = predict_with_bundle(bundle[0].pin(bundle[3]), rule,
        _model_inputs(store, rule, ('model2',), now, target=FINAL_EXTREME), as_of=now, max_source_age_seconds=120.)
    legs = []
    for i, bucket in enumerate(rule.payload['partition']):
        target = contract_target(rule, bucket['market_id'], 'YES'); key = 'basket-book'+str(i)
        store.capture(key, event_id=rule.payload['event_id'], kind='BOOK', provider='basket-fixture',
              source_identity=target['token_id'], revision='1', observed_at=now, evidence_class='SYNTHETIC',
              payload=dict(target, rule_fingerprint=rule.sha256, collateral_asset='FIXTURE_COLLATERAL',
                           stream_healthy=True, asks=[dict(price='.2', size='20')], bids=[dict(price='.1', size='20')]))
        legs.append(BasketLeg(bucket['market_id'], 'YES', '2', key, costs()))
    kw = dict(rule=rule, prediction=prediction, binding=r['binding'], strategy=r['scope'].strategy,
              account_id='account', legs=tuple(legs), policy=BasketPolicy(r['request'].valuation_policy, 2., 120., '.01'))
    analyze_basket(store, 'basket-value', **kw)
    p = BasketProposal('basket', 'joint-thesis', r['context'], rule, 'basket-value', 'state2',
           (Attribution(r['scope'].strategy, '1'),),
           now+20, tuple((contract_target(rule, l.market_id, l.side)['token_id'], '2') for l in legs), ('pin',))
    r.update(kw=kw, proposal=p)
    return r


def reserve(rig, *, key='batch', proposals=None, c=None):
    return (c or coordinator(rig)).coordinate(key, proposals or (rig['proposal'],))['body']['details']


def proof(rig, intent, key, kind, **payload):
    c = coordinator(rig); state = c._state(c._head()); p = state['intents'][intent]
    rig['store'].capture(key, event_id=p['event_id'], kind='TRADE', provider='synthetic-paper-engine',
            source_identity=intent, revision=key, observed_at=rig['now'][0], evidence_class='SYNTHETIC',
            payload=dict(record_type=kind, execution_namespace='V11_PAPER', account_id='account',
                         intent_id=intent, token_id=p['token_id'], **payload))
    return key


def fill(rig, leg, units='1', cost='.2'):
    key = 'fill-'+str(leg)
    return coordinator(rig).record_fill('record-'+key, proof(rig, 'basket:leg:'+str(leg), key, 'PAPER_FILL',
                       fill_id=key, units=units, all_in_collateral=cost, direction='BUY'))['body']['details']


@pytest.mark.parametrize('rig', ['CROSS_TEMP_RELATIVE_VALUE', 'STRUCTURAL'], indirect=True)
def test_all_legs_share_one_account_reservation_and_do_not_invent_individual_ev(rig):
    d = reserve(rig)
    assert d['reserved_intent_ids'] == ['basket:leg:0', 'basket:leg:1', 'basket:leg:2']
    assert Decimal(d['risk']['reserved_cash']) == Decimal('1.2')
    assert Decimal(d['risk']['active_worst_loss']) == Decimal('.8')
    assert Decimal(d['ranking'][0]['conservative_ev_total']) == Decimal('.8')
    assert all(p['conservative_ev_total'] is None and p['joint_ev_only'] for p in d['state']['intents'].values())
    assert d['state']['cash'] == '10' and d['state']['redeemed_collateral'] == '0'
    assert not d['state']['financial_authority'] and d['execution_status'] == 'NOT_SUBMITTED'


@pytest.mark.parametrize('change', ['cash', 'scenario', 'intent_count'])
def test_one_account_limit_rejects_entire_group_without_partial_reservations(rig, change):
    c = coordinator(rig)
    if change == 'cash':
        c.policy = replace(c.policy, initial_hypothetical_cash='1')
    elif change == 'scenario':
        c.limits = replace(c.limits, per_event='.5')
    else:
        c.policy = replace(c.policy, max_active_intents=2)
    c = PaperCoordinator(rig['store'], policy=c.policy, correlation=c.correlation, limits=c.limits)
    d = reserve(rig, c=c)
    assert d['results'][0]['reason'] == 'ACCOUNT_SCENARIO_OR_RESERVATION_LIMIT'
    assert d['reserved_intent_ids'] == [] and d['state']['intents'] == {} and not d['state'].get('baskets')


def test_group_cannot_split_per_intent_cash_ceiling_across_legs(rig):
    c = coordinator(rig); c.policy = replace(c.policy, per_intent_cash_limit='.5')
    c = PaperCoordinator(rig['store'], policy=c.policy, correlation=c.correlation, limits=c.limits)
    d = reserve(rig, c=c)
    assert d['results'][0]['reason'] == 'BASKET_AGGREGATE_INTENT_CASH_LIMIT' and not d['reserved_intent_ids']


def test_competing_baskets_are_ranked_jointly_without_double_reservation(rig):
    second = replace(rig['proposal'], proposal_id='second', thesis_id='different-explanation')
    d = reserve(rig, proposals=(second, rig['proposal']))
    assert [r['proposal_id'] for r in d['ranking']] == ['basket', 'second']
    assert len(d['reserved_intent_ids']) == 3 and Decimal(d['risk']['reserved_cash']) == Decimal('1.2')
    assert d['results'][-1]['reason'] == 'TOKEN_CONFLICT_OR_DUPLICATE_KEEP_STRONGER_PROPOSAL'


def test_changed_desired_position_rejects_whole_basket_instead_of_dropping_one_hedge(rig):
    p = rig['proposal']; desired = list(p.desired_positions); desired[1] = (desired[1][0], '1')
    d = reserve(rig, proposals=(replace(p, desired_positions=tuple(desired)),))
    assert d['results'][0]['reason'] == 'DESIRED_POSITION_ALREADY_COVERED_OR_REVALUE_SMALLER_DELTA'
    assert not d['reserved_intent_ids']


def test_replay_and_new_explanation_do_not_repeat_existing_group(rig):
    first = reserve(rig); rig['now'][0] += 1
    assert reserve(rig) == first
    d = reserve(rig, key='other', proposals=(replace(rig['proposal'], proposal_id='again'),))
    assert d['results'][0]['reason'] == 'THESIS_ALREADY_HAS_ECONOMIC_INTENT'
    assert Decimal(coordinator(rig).snapshot()['reserved_cash']) == Decimal('1.2')


def test_partial_fill_spends_only_actual_cash_and_keeps_other_leg_risk(rig):
    reserve(rig); d = fill(rig, 0)
    assert Decimal(d['state']['cash']) == Decimal('9.8')
    assert Decimal(d['risk']['reserved_cash']) == 1
    assert Decimal(d['risk']['active_worst_loss']) == Decimal('.8')
    assert d['state']['intents']['basket:leg:0']['status'] == 'PARTIAL'
    assert len(d['state']['lots']) == 1 and not d['state']['realized_entries']
    c = coordinator(rig)
    d = c.transition('submit-next', intent_id='basket:leg:1', status='SUBMITTING')['body']['details']
    assert not d['real_submission_performed']


def test_cancellation_and_terminal_reconcile_only_one_leg_without_phantom_sale(rig):
    reserve(rig); fill(rig, 0)
    c = coordinator(rig)
    d = c.transition('cancel', intent_id='basket:leg:0', status='CANCEL_REQUESTED')['body']['details']
    assert Decimal(d['risk']['reserved_cash']) == 1 and not d['reservation_released']
    with pytest.raises(EvidenceError, match='GROUP_REQUIRES_REPLAN'):
        c.transition('submit-next', intent_id='basket:leg:1', status='SUBMITTING')
    key = proof(rig, 'basket:leg:0', 'terminal', 'PAPER_TERMINAL', status='CANCELED', cumulative_fill_units='1',
                all_fills_reconciled=True, terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL')
    d = c.reconcile_terminal('reconcile', key)['body']['details']
    assert Decimal(d['risk']['reserved_cash']) == Decimal('.8')
    assert len(d['state']['lots']) == 1 and Decimal(d['state']['cash']) == Decimal('9.8')
    assert d['state']['intents']['basket:leg:1']['status'] == 'RESERVED'


def test_full_basket_fill_is_inventory_not_payout_or_redeemed_cash(rig):
    reserve(rig)
    for i in range(3):
        d = fill(rig, i, '2', '.4')
    assert Decimal(d['state']['cash']) == Decimal('8.8') and len(d['state']['lots']) == 3
    assert Decimal(d['risk']['reserved_cash']) == 0 and Decimal(d['risk']['active_worst_loss']) == 0
    assert not d['state']['realized_entries'] and d['state']['claimable_collateral'] == d['state']['redeemed_collateral'] == '0'


def test_restart_preserves_unknown_leg_and_prevents_further_group_submission(rig):
    reserve(rig); c = coordinator(rig)
    c.transition('submit', intent_id='basket:leg:0', status='SUBMITTING')
    coordinator(rig).recover('restart')
    with pytest.raises(EvidenceError, match='CANNOT_BE_RETRIED'):
        c.transition('retry', intent_id='basket:leg:0', status='SUBMITTING')
    with pytest.raises(EvidenceError, match='GROUP_REQUIRES_REPLAN'):
        c.transition('next', intent_id='basket:leg:1', status='SUBMITTING')
    assert Decimal(c.snapshot()['reserved_cash']) == Decimal('1.2')


def replace_book(rig, index, **change):
    store = rig['store']; row = store.get('basket-book'+str(index)); b = row['body']
    return store.capture('new-book', event_id=row['event_id'], kind='BOOK', provider=b['provider'],
          source_identity=b['source_identity'], revision='new', observed_at=rig['now'][0],
          payload=dict(b['payload'], **change), evidence_class='SYNTHETIC')


def test_new_book_on_unsubmitted_sibling_blocks_entire_old_basket(rig):
    reserve(rig); replace_book(rig, 2)
    with pytest.raises(EvidenceError, match='VALUE_CHANGED|CURRENT_EXACT'):
        coordinator(rig).transition('submit', intent_id='basket:leg:0', status='SUBMITTING')
    assert Decimal(coordinator(rig).snapshot()['reserved_cash']) == Decimal('1.2')


def test_fee_depth_average_cannot_hide_negative_joint_value_at_allowed_limits(rig):
    replace_book(rig, 0, asks=[dict(price='.2', size='1'), dict(price='.8', size='20')])
    legs = list(rig['kw']['legs']); legs[0] = replace(legs[0], book_id='new-book')
    analyze_basket(rig['store'], 'deep-value', **dict(rig['kw'], legs=tuple(legs)))
    d = reserve(rig, proposals=(replace(rig['proposal'], valuation_id='deep-value'),))
    assert d['results'][0]['reason'] == 'BASKET_LIMIT_PRICE_JOINT_EV_NOT_ABOVE_THRESHOLD'


@pytest.mark.parametrize('fault', ['prediction', 'summary'])
def test_valid_record_hash_does_not_certify_unreproduced_prediction_or_economics(rig, fault):
    store = rig['store']; value = deepcopy(store.get('basket-value')['body']['details'])
    if fault == 'prediction':
        p = value['prediction']; p['buckets'][0]['point'] += .001; p['buckets'][1]['point'] -= .001
        prediction = BucketPrediction(canonical(p), digest(p))
        analyze_basket(store, 'changed-value', **dict(rig['kw'], prediction=prediction))
    else:
        value['conditional_full_fill_payout_floor'] = '200'
        store.audit('changed-value', event_id=rig['context'].event_id, kind='MEASUREMENT', details=value)
    d = reserve(rig, proposals=(replace(rig['proposal'], valuation_id='changed-value'),))
    assert not d['reserved_intent_ids']
    assert ('PREDICTION_NOT_REPRODUCED' if fault == 'prediction' else 'VALUE_CHANGED_OR_NOT_REPRODUCIBLE') in d['results'][0]['reason']


def test_model_demotion_and_operator_stop_preserve_reservations(rig):
    reserve(rig); state = rig['model_state'][0]
    rig['model_state'][0] = authority.transition(state, action='DEMOTE', expected_state_sha256=digest(state),
                                                now=21., reason='TEST', size_multiplier=.5)
    with pytest.raises(EvidenceError, match='MODEL_MANUAL_REVIEW'):
        coordinator(rig).transition('submit', intent_id='basket:leg:0', status='SUBMITTING')
    SafetyReductions(rig['store']).apply('stop', scope='ACCOUNT', scope_id='account', action='NO_NEW_ORDERS',
                                       actor='fixture', reason='TEST')
    assert Decimal(coordinator(rig).snapshot()['reserved_cash']) == Decimal('1.2')


def test_racing_operator_reduction_rolls_back_all_legs(rig, monkeypatch):
    c = coordinator(rig); original = c._commit
    def race(*args, **kwargs):
        SafetyReductions(rig['store']).apply('stop', scope='ACCOUNT', scope_id='account', action='NO_NEW_ORDERS',
                                           actor='fixture', reason='TEST')
        return original(*args, **kwargs)
    monkeypatch.setattr(c, '_commit', race)
    with pytest.raises(EvidenceError, match='AUDIT_GUARDED_STATE_CHANGED'):
        reserve(rig, c=c)
    assert c._head() is None and Decimal(c.snapshot()['reserved_cash']) == 0


def test_racing_common_account_writer_cannot_spend_cash_reserved_by_another_batch(rig, monkeypatch):
    c = coordinator(rig); original = c._commit
    def race(*args, **kwargs):
        coordinator(rig).coordinate('winner', (replace(rig['proposal'], proposal_id='winner'),))
        return original(*args, **kwargs)
    monkeypatch.setattr(c, '_commit', race)
    with pytest.raises(EvidenceError, match='AUDIT_STATE_CHANGED'):
        reserve(rig, c=c)
    state = c._state(c._head())
    assert set(state['intents']) == {'winner:leg:0', 'winner:leg:1', 'winner:leg:2'}
    assert Decimal(c.snapshot()['reserved_cash']) == Decimal('1.2')


def test_single_leg_and_basket_compete_in_same_ranking_without_partial_group_admission(rig):
    from polymarket_scanner.v11.valuation import settlement_entry
    kw = rig['kw']; leg = kw['legs'][0]; store = rig['store']
    value = settlement_entry(store, 'unqualified-single', rule=kw['rule'], prediction=kw['prediction'],
              binding=kw['binding'], market_id=leg.market_id, side=leg.side, units=leg.units, book_id=leg.book_id,
              policy=kw['policy'].valuation, costs=leg.costs)['body']['details']
    assert value['outcome'] == 'REJECT'
    # Explicit downstream ranking fixture only; vacuous single-leg economics
    # remain rejected by the real valuation engine.
    value.update(outcome='ACCEPT_RESEARCH', conservative_ev_per_share='.9', conservative_ev_total='1.8',
                 synthetic_downstream_test_fixture=True)
    store.audit('single-fixture', event_id=rig['context'].event_id, kind='MEASUREMENT', details=value)
    p = rig['proposal']
    single = Proposal('single', 'single-fixture-thesis', p.context, p.rule, 'single-fixture', p.event_state_id,
                      p.attribution, p.expires_at, '2', p.admission_ids)
    d = reserve(rig, proposals=(p, single))
    assert [row['proposal_id'] for row in d['ranking']] == ['single', 'basket']
    assert d['reserved_intent_ids'] == ['single'] and Decimal(d['risk']['reserved_cash']) == Decimal('.4')
    assert not d['state'].get('baskets')


def test_one_cost_expiry_caps_every_leg_and_never_releases_reservations(rig):
    kw = rig['kw']; legs = list(kw['legs'])
    cs = list(legs[1].costs); cs[0] = replace(cs[0], valid_until=rig['now'][0]+1)
    legs[1] = replace(legs[1], costs=tuple(cs))
    analyze_basket(rig['store'], 'short-cost-value', **dict(kw, legs=tuple(legs)))
    p = replace(rig['proposal'], valuation_id='short-cost-value')
    d = reserve(rig, proposals=(p,))
    assert all(i['expires_at'] == rig['now'][0]+1 for i in d['state']['intents'].values())
    rig['now'][0] += 1
    with pytest.raises(EvidenceError, match='EXPIRED'):
        coordinator(rig).transition('submit', intent_id='basket:leg:0', status='SUBMITTING')
    assert Decimal(coordinator(rig).snapshot()['reserved_cash']) == Decimal('1.2')


def test_new_official_receipt_requires_new_joint_thesis_before_submission(rig):
    reserve(rig); store = rig['store']; row = store.get('official2'); b = row['body']
    store.capture('new-official', event_id=row['event_id'], kind=row['kind'], provider=b['provider'],
            source_identity=b['source_identity'], revision='3', observed_at=rig['now'][0],
            payload=b['payload'], evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError, match='SOURCE_CHANGED'):
        coordinator(rig).transition('submit', intent_id='basket:leg:0', status='SUBMITTING')
    assert Decimal(coordinator(rig).snapshot()['reserved_cash']) == Decimal('1.2')


def test_racing_sibling_book_invalidates_atomic_account_append(rig, monkeypatch):
    c = coordinator(rig); original = c._commit
    def race(*args, **kwargs):
        replace_book(rig, 2)
        return original(*args, **kwargs)
    monkeypatch.setattr(c, '_commit', race)
    with pytest.raises(EvidenceError, match='AUDIT_GUARDED_STATE_CHANGED'):
        reserve(rig, c=c)
    assert c._head() is None
