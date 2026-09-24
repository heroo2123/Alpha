import ast
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_scanner.v11 import paper_cancellation as cancellation
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.event_risk import EventRiskEngine, SafetyReductions
from polymarket_scanner.v11.paper_coordinator import cancel_identity
from test_v11_paper_coordinator import rig, coordinator, proposal, proof, fill
from test_v11_event_risk import policy as event_policy, metrics


def bridge(rig, maximum=2):
    return cancellation.PaperCancellation(coordinator(rig), cancellation.CancellationPolicy('fixture', maximum, 10))


def opening(rig, pid='buy', bucket=0, units='2'):
    p = proposal(rig, pid, bucket=bucket, units=units)
    c = coordinator(rig); d = c.coordinate('batch-'+pid, (p,))['body']['details']
    assert d['reserved_intent_ids'] == [pid], d
    return c


def trigger(rig, key='halt', *, scope='ACCOUNT', scope_id='account', action='CANCEL_AND_HALT'):
    return SafetyReductions(rig['store']).apply(key, scope=scope, scope_id=scope_id, action=action,
                                               actor='synthetic-operator', reason='fixture')


def plan(rig, key='cancel-plan', trigger_id='halt'):
    return bridge(rig).plan(key, trigger_id=trigger_id)['body']['details']


def advance(rig, key='dispatch', plan_id='cancel-plan'):
    return bridge(rig).advance(key, plan_id=plan_id)['body']['details']


def terminal(rig, pid='buy', status='CANCELED', units='0', key='terminal'):
    c = coordinator(rig)
    receipt = proof(rig, pid, key, 'PAPER_TERMINAL', status=status, cumulative_fill_units=units,
                    all_fills_reconciled=True, terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL')
    return c.reconcile_terminal('reconcile-'+key, receipt)


def test_cancel_plan_is_non_effectful_and_delivery_retains_all_account_cash_and_inventory(rig):
    c = opening(rig); before = c.snapshot(); trigger(rig)
    p = plan(rig)
    assert p['selected_count'] == 1 and c.snapshot() == before
    d = advance(rig)
    assert d['outcome'] == 'PENDING_TERMINAL_RECONCILIATION' and d['locally_requested_count'] == 1
    assert d['reports'][0]['current_account_status'] == 'CANCEL_REQUESTED'
    assert not d['reports'][0]['confirmed_paper_cancellation'] and d['reports'][0]['reservation_retained_by_account']
    assert c.snapshot() == before and c._state(c._head())['lots'] == {}
    assert not d['financial_authority'] and not d['real_cancel_sent'] and not d['independent_guardian_commissioned']


@pytest.mark.parametrize('scope', ['ACCOUNT', 'CITY', 'STATION', 'EVENT'])
def test_exact_operator_scope_uses_existing_account_managed_intents(rig, scope):
    opening(rig); sid = dict(rig['context'].scopes)[scope]
    trigger(rig, scope=scope, scope_id=sid)
    assert plan(rig)['selected_count'] == 1


def test_wrong_account_is_rejected_and_unmatched_event_cannot_cancel_other_events(rig):
    c = opening(rig); trigger(rig, scope_id='another-account')
    with pytest.raises(EvidenceError, match='ACCOUNT_MISMATCH'): plan(rig)
    trigger(rig, 'other-event', scope='EVENT', scope_id='unmanaged-event', action='CANCEL_EVENT')
    p = plan(rig, 'empty', 'other-event')
    assert p['outcome'] == 'NO_MATCHING_MANAGED_INTENTS'
    assert c._state(c._head())['intents']['buy']['status'] == 'RESERVED'


@pytest.mark.parametrize('action', ['NO_NEW_ORDERS', 'REDUCE_ONLY', 'REQUIRE_MANUAL_REVIEW'])
def test_non_cancel_safety_record_does_not_silently_become_cancel_all(rig, action):
    opening(rig); trigger(rig, action=action)
    with pytest.raises(EvidenceError, match='CANCELLATION_TRIGGER'): plan(rig)


def test_expired_model_and_intent_and_sticky_account_fault_do_not_block_local_cancel(rig):
    c = opening(rig); head = c._head(); s = c._state(head); s['faults'] = ['SYNTHETIC_RECONCILIATION_FAULT']
    c._commit('fault-fixture', dict(action='SYNTHETIC_FIXTURE'), head, s, {})
    trigger(rig); rig['now'][0] += 600
    plan(rig); d = advance(rig)
    assert d['locally_requested_count'] == 1 and d['current_account_faults'] == ['SYNTHETIC_RECONCILIATION_FAULT']
    assert Decimal(d['risk']['reserved_cash']) == Decimal('.8')
    assert not d['reports'][0]['confirmed_paper_cancellation']


def test_late_fill_and_ack_keep_cancel_requested_and_terminal_confirmation_preserves_inventory(rig):
    c = opening(rig); c.transition('submit', intent_id='buy', status='SUBMITTING')
    trigger(rig); plan(rig); advance(rig)
    rig['now'][0] += 1; fill(rig, 'buy', units='1', collateral='.4')
    c.transition('late-ack', intent_id='buy', status='ACKNOWLEDGED')
    rig['now'][0] += 1
    d = bridge(rig).observe('still-open', plan_id='cancel-plan')['body']['details']
    assert d['reports'][0]['cumulative_fill_units'] == '1'
    assert not d['reports'][0]['confirmed_paper_cancellation']
    assert Decimal(d['risk']['reserved_cash']) == Decimal('.4')
    terminal(rig, units='1'); rig['now'][0] += 1
    d = bridge(rig).observe('closed', plan_id='cancel-plan')['body']['details']
    assert d['outcome'] == 'ALL_TARGETS_TERMINAL' and d['reports'][0]['confirmed_paper_cancellation']
    assert d['reports'][0]['local_request_to_terminal_observed_seconds'] == 3
    assert d['reports'][0]['exchange_cancel_latency_seconds'] is None
    assert not d['reports'][0]['actual_order_cancellation_confirmed']
    assert Decimal(d['risk']['reserved_cash']) == 0
    assert sum(Decimal(l['units']) for l in c._state(c._head())['lots'].values()) == 1
    assert d['cancelled_inventory_is_not_an_exit'] and not d['inventory_exit_performed']


def test_full_fill_is_terminal_without_being_called_cancelled(rig):
    c = opening(rig); trigger(rig); plan(rig); advance(rig)
    fill(rig, 'buy', units='2', collateral='.8')
    d = bridge(rig).observe('filled', plan_id='cancel-plan')['body']['details']
    assert d['reports'][0]['terminal_kind'] == 'FILLED' and not d['reports'][0]['confirmed_paper_cancellation']
    assert Decimal(c.snapshot()['held_cost_basis']) == Decimal('.8')


def test_public_trade_or_unapplied_terminal_receipt_cannot_confirm_cancel(rig):
    c = opening(rig); trigger(rig); plan(rig); advance(rig)
    proof(rig, 'buy', 'unapplied', 'PAPER_TERMINAL', status='CANCELED', cumulative_fill_units='0',
          all_fills_reconciled=True, terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL')
    rig['store'].capture('public-print', event_id=rig['context'].event_id, kind='TRADE', provider='public',
                        source_identity='public', revision='1', payload={'price':'.9'}, evidence_class='PUBLIC_OBSERVED')
    d = bridge(rig).observe('unconfirmed', plan_id='cancel-plan')['body']['details']
    assert not d['reports'][0]['confirmed_paper_cancellation'] and Decimal(c.snapshot()['reserved_cash']) == Decimal('.8')


def test_terminal_before_dispatch_needs_no_request_and_has_no_cancel_latency(rig):
    c = opening(rig); trigger(rig); plan(rig); terminal(rig)
    head = c._head(); d = advance(rig)
    assert d['local_requests_attempted_this_cycle'] == 0 and d['reports'][0]['confirmed_paper_cancellation']
    assert d['reports'][0]['local_request_to_terminal_observed_seconds'] is None and c._head() == head


def test_restart_after_account_write_resumes_same_local_request_without_double_delivery(rig, monkeypatch):
    c = opening(rig); trigger(rig); plan(rig); b = bridge(rig)
    monkeypatch.setattr(b, '_commit', lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('INTERRUPTED')))
    with pytest.raises(RuntimeError, match='INTERRUPTED'): b.advance('dispatch', plan_id='cancel-plan')
    account_head = c._head(); assert account_head['body']['details']['state']['intents']['buy']['status'] == 'CANCEL_REQUESTED'
    d = advance(rig)
    assert d['locally_requested_count'] == 1 and d['local_requests_attempted_this_cycle'] == 0
    assert c._head() == account_head
    rig['now'][0] += 100
    assert advance(rig) == d
    with pytest.raises(EvidenceError, match='REPLAY_CONFLICT'): bridge(rig).observe('dispatch', plan_id='cancel-plan')


def test_per_cycle_bound_leaves_undispatched_targets_visible_for_next_cycle(rig):
    opening(rig, 'one', 0); opening(rig, 'two', 1); trigger(rig)
    b = bridge(rig, 1); b.plan('bounded', trigger_id='halt')
    d = b.advance('first', plan_id='bounded')['body']['details']
    assert d['local_requests_attempted_this_cycle'] == 1 and d['locally_requested_count'] == 1
    assert sorted(i['stage'] for i in d['state']['items'].values()) == ['PLANNED', 'REQUESTED']
    d = b.advance('second', plan_id='bounded')['body']['details']
    assert d['local_requests_attempted_this_cycle'] == 1 and d['locally_requested_count'] == 2


def test_plan_limit_never_silently_omits_managed_orders(rig):
    opening(rig, 'one', 0); opening(rig, 'two', 1); trigger(rig)
    b = cancellation.PaperCancellation(coordinator(rig), cancellation.CancellationPolicy('tiny', 1, 1))
    with pytest.raises(EvidenceError, match='SMALLER_SCOPE'): b.plan('bounded', trigger_id='halt')
    assert b._head('bounded') is None


def test_atomic_economic_identity_pin_prevents_cancelling_repurposed_intent(rig, monkeypatch):
    c = opening(rig); trigger(rig); plan(rig); b = bridge(rig); original = b.coordinator.transition
    def race(*args, **kw):
        head = c._head(); state = c._state(head); state['intents']['buy']['units'] = '3'
        c._commit('identity-change', dict(action='SYNTHETIC_CORRUPTION_FIXTURE'), head, state, {})
        return original(*args, **kw)
    monkeypatch.setattr(b.coordinator, 'transition', race)
    d = b.advance('dispatch', plan_id='cancel-plan')['body']['details']
    assert d['outcome'] == 'RECONCILIATION_REQUIRED'
    assert d['state']['items']['buy']['fault'] == 'PAPER_CANCEL_MANAGED_IDENTITY_CHANGED'
    assert c._state(c._head())['intents']['buy']['status'] == 'RESERVED'


def test_cancel_identity_pin_cannot_authorize_opening_transition(rig):
    c = opening(rig); pin = cancel_identity(c._state(c._head())['intents']['buy'])
    with pytest.raises(EvidenceError, match='ONLY_FOR_CANCELLATION'):
        c.transition('open', intent_id='buy', status='SUBMITTING', expected_cancel_identity=pin)


def test_event_cancels_new_risk_but_retains_nonpassive_inventory_reduction(rig):
    c = opening(rig, 'entry'); fill(rig, 'entry', units='2', collateral='.8')
    exit_proposal = proposal(rig, 'exit', units='1', direction='SELL')
    assert c.coordinate('exit-batch', (exit_proposal,))['body']['details']['reserved_intent_ids'] == ['exit']
    opening(rig, 'new-risk', bucket=1)
    EventRiskEngine(rig['store']).step('hazard', context=rig['context'], policy=event_policy(), binding=rig['binding'],
             metrics=replace(metrics(rig['now'][0]), source_revision=True),
             book_ids=(rig['book_id'],), source_ids=(rig['source_id'],))
    p = plan(rig, trigger_id='hazard')
    assert set(p['state']['items']) == {'new-risk'}
    advance(rig)
    state = c._state(c._head())
    assert state['intents']['exit']['status'] == 'RESERVED' and state['intents']['new-risk']['status'] == 'CANCEL_REQUESTED'


def test_account_cas_failure_cannot_publish_stale_confirmation(rig, monkeypatch):
    opening(rig); trigger(rig); plan(rig); advance(rig); c = coordinator(rig)
    original = rig['store'].audit; once = [False]
    def race(key, **kw):
        if key == 'observe' and not once[0]:
            once[0] = True; terminal(rig)
        return original(key, **kw)
    monkeypatch.setattr(rig['store'], 'audit', race)
    with pytest.raises(EvidenceError, match='GUARDED_STATE_CHANGED'):
        bridge(rig).observe('observe', plan_id='cancel-plan')
    assert c._state(c._head())['intents']['buy']['status'] == 'CANCELED'
    assert bridge(rig).observe('observe', plan_id='cancel-plan')['body']['details']['terminal_confirmation_count'] == 1


def test_local_request_race_is_visible_and_bounded_retry_does_not_clear_account_faults(rig, monkeypatch):
    c = opening(rig); head = c._head(); state = c._state(head); state['faults'] = ['STICKY_FIXTURE_FAULT']
    c._commit('fault-fixture', dict(action='SYNTHETIC_FIXTURE'), head, state, {})
    trigger(rig); plan(rig); b = bridge(rig); original = b.coordinator.transition; once = [False]
    def race(*args, **kw):
        if not once[0]:
            once[0] = True; raise EvidenceError('AUDIT_STATE_CHANGED')
        return original(*args, **kw)
    monkeypatch.setattr(b.coordinator, 'transition', race)
    d = b.advance('first', plan_id='cancel-plan')['body']['details']
    assert d['outcome'] == 'RECONCILIATION_REQUIRED' and d['state']['items']['buy']['stage'] == 'PLANNED'
    assert d['state']['items']['buy']['last_delivery_error'] == 'AUDIT_STATE_CHANGED'
    d = b.advance('retry-local', plan_id='cancel-plan')['body']['details']
    assert d['locally_requested_count'] == 1 and d['local_requests_attempted_this_cycle'] == 1
    assert d['current_account_faults'] == ['STICKY_FIXTURE_FAULT']


def test_late_fill_after_terminal_revokes_clean_cancel_reporting_and_keeps_new_exposure(rig):
    c = opening(rig); trigger(rig); plan(rig); advance(rig); terminal(rig)
    bridge(rig).observe('confirmed', plan_id='cancel-plan')
    fill(rig, 'buy', units='1', collateral='.4')
    d = bridge(rig).observe('late-fill', plan_id='cancel-plan')['body']['details']
    assert d['outcome'] == 'RECONCILIATION_REQUIRED' and not d['reports'][0]['confirmed_paper_cancellation']
    assert d['state']['items']['buy']['fault'] == 'PAPER_CANCEL_TERMINAL_RECONCILIATION_REGRESSED'
    assert 'LATE_FILL_AFTER_TERMINAL_RECONCILIATION' in d['current_account_faults']
    assert Decimal(c.snapshot()['held_cost_basis']) == Decimal('.4')
    assert Decimal(c.snapshot()['reserved_cash']) == Decimal('.4')


@pytest.mark.parametrize('per_cycle, total', [(0,10), (17,20), (2,1), (1,257)])
def test_cancellation_work_cannot_be_unbounded(per_cycle, total):
    with pytest.raises(EvidenceError, match='POLICY_BOUND'):
        cancellation.CancellationPolicy('fixture', per_cycle, total)


def test_plan_publication_cas_cannot_hide_new_account_intents(rig, monkeypatch):
    c = opening(rig); trigger(rig); original = rig['store'].audit; once = [False]
    def changed(key, **kw):
        if key == 'cancel-plan' and not once[0]:
            once[0] = True
            head = c._head(); state = c._state(head); state['faults'].append('RACING_ACCOUNT_UPDATE')
            c._commit('account-race', dict(action='SYNTHETIC_FIXTURE'), head, state, {})
        return original(key, **kw)
    monkeypatch.setattr(rig['store'], 'audit', changed)
    with pytest.raises(EvidenceError, match='GUARDED_STATE_CHANGED'): plan(rig)
    assert bridge(rig)._head('cancel-plan') is None
    assert plan(rig)['selected_count'] == 1


def test_adapter_has_no_network_or_fill_terminal_opening_mutation_surface():
    tree = ast.parse(Path(cancellation.__file__).read_text())
    forbidden = {'coordinate','record_fill','reconcile_terminal','request','get_session','post','delete','system','Popen','place_order'}
    calls = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not calls & forbidden
    transitions = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == 'transition']
    assert len(transitions) == 1
    assert next(k.value.value for k in transitions[0].keywords if k.arg == 'status') == 'CANCEL_REQUESTED'
