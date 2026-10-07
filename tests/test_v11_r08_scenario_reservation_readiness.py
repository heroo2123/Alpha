"""Requirement 8 readiness probe regressions.

The positive-path fixture below only overrides the downstream economic
*outcome* field of a real valuation -- exactly the established pattern already
used by `test_v11_paper_coordinator.py`'s `proposal()` and
`test_v11_pws_admission.py`'s `synthetic_proposal()` -- so that the current
deliberately-conservative model's REJECT does not stand in for a genuine
economic opportunity. Every freshness/settlement/execution-health gate this
probe cares about (`StrategyAdmission.revalidate`, `EventRiskEngine.revalidate`,
current-book-head checks, account scenario/cash/loss/intent limits) runs for
real, unmodified, inside `PaperCoordinator.coordinate()`: nothing here
monkeypatches any of them.
"""
from dataclasses import replace

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.paper_coordinator import (
    ACCOUNT_KEY, PaperAccountPolicy, PaperCoordinator, Proposal, VERSION as COORDINATOR_VERSION,
)
from polymarket_scanner.v11.scenario_risk import Attribution
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_scenario_risk import limits, mapping
from test_v11_strategy_pipeline import evaluate, factory

from tools.v11_r08_scenario_reservation_readiness import (
    OUTCOME_DEMONSTRATED,
    OUTCOME_NO_RESERVATION,
    SCHEMA,
    ScenarioReservationReadiness,
    evaluate_scenario_reservation_readiness,
    genuine_reserved_intents,
)


def coordinator(rig):
    corr = mapping(rig['rule'])
    corr = replace(corr, memberships=(replace(corr.memberships[0], city=rig['context'].city_id),))
    policy = PaperAccountPolicy('r08-fixture', 'account', 'FIXTURE_COLLATERAL', '10', '10', '10', '10',
                                '.01', '0', 60., 10)
    return PaperCoordinator(rig['store'], policy=policy, correlation=corr, limits=limits())


def genuine_proposal(rig, pid='proposal'):
    result = evaluate(rig)
    assert result['outcome'] == 'REJECT'  # the deliberately vacuous conservative model never self-admits
    value = dict(rig['store'].get('evaluation:valuation')['body']['details'])
    value.update(outcome='ACCEPT_RESEARCH', conservative_ev_per_share='.2', conservative_ev_total='.4',
                 synthetic_downstream_test_fixture=True)
    rig['store'].audit('fixture-value-'+pid, event_id=rig['context'].event_id, kind='MEASUREMENT', details=value)
    return Proposal(pid, 'thesis-'+pid, rig['context'], rig['rule'], 'fixture-value-'+pid,
                    rig['request'].event_state_id, (Attribution(rig['scope'].strategy, '1'),),
                    rig['now'][0]+20, '2', ('pin',))


def test_type_guard_rejects_non_coordinator():
    with pytest.raises(EvidenceError, match='TYPED_COORDINATOR_REQUIRED'):
        evaluate_scenario_reservation_readiness(object())


def test_empty_account_has_no_genuine_reservation(factory):
    rig = factory()
    result = evaluate_scenario_reservation_readiness(coordinator(rig))
    assert result.outcome == OUTCOME_NO_RESERVATION
    assert result.reserved_intent_count == 0
    assert 'NO_UNRESOLVED_RESERVED_INTENT' in result.reasons
    assert result.financial_authority is False


def test_genuine_admission_and_event_risk_backed_reservation_is_demonstrated(factory):
    rig = factory()
    c = coordinator(rig)
    p = genuine_proposal(rig)
    outcome = c.coordinate('batch', (p,))['body']['details']
    assert outcome['reserved_intent_ids'] == [p.proposal_id]
    result = evaluate_scenario_reservation_readiness(c)
    assert result.outcome == OUTCOME_DEMONSTRATED
    assert result.reserved_intent_count == 1
    assert result.risk_accepted is True
    assert float(result.reserved_cash) > 0
    assert result.financial_authority is False
    assert result.reasons == ()
    assert result.schema == SCHEMA


def test_result_is_deterministic_on_replay(factory):
    rig = factory()
    c = coordinator(rig)
    c.coordinate('batch', (genuine_proposal(rig),))
    first = evaluate_scenario_reservation_readiness(c)
    second = evaluate_scenario_reservation_readiness(c)
    assert first == second
    assert first.to_dict() == second.to_dict()


def test_real_gates_were_not_bypassed_to_reach_demonstrated(factory):
    # The genuine_proposal() fixture only overrides the downstream EV outcome.
    # A stale admission (one that no longer revalidates) must still refuse the
    # whole coordinate() call before any reservation can exist, proving this
    # probe cannot be satisfied by evidence whose freshness gate was skipped.
    rig = factory()
    p = genuine_proposal(rig)
    rig['now'][0] += 10000.  # expire the admission's certification/rule window
    c = coordinator(rig)
    result = c.coordinate('batch', (p,))['body']['details']
    assert result['reserved_intent_ids'] == []
    probe = evaluate_scenario_reservation_readiness(c)
    assert probe.outcome == OUTCOME_NO_RESERVATION


def _forge_head(rig, c, state, record_id):
    # Bypasses coordinate() entirely: a direct, otherwise-ordinary audit()
    # call under the account key, exactly how an attacker (or a corrupted
    # caller) with store access -- but no coordinator admission path -- would
    # try to make a fabricated/mutated account state look like the real head.
    head = c._head()
    rig['store'].audit(record_id, event_id=ACCOUNT_KEY, kind='COORDINATOR_EVENT',
                       details=dict(version=COORDINATOR_VERSION, policy_sha256=c.policy_sha,
                                    request={}, state=state),
                       expected_previous_seq=head['seq'] if head else 0)


def test_r08_fabricated_snapshot_is_not_genuine(factory):
    # No coordinate() call ever happened: a minimal RESERVED intent is
    # appended directly to an otherwise-empty, correctly-versioned/hashed
    # account record. It carries none of coordinate()'s admission/event/
    # valuation/authority lineage and must not be classified genuine.
    rig = factory()
    c = coordinator(rig)
    state = c._state(c._head())
    state['intents']['forged'] = dict(
        proposal_id='forged', event_id=rig['context'].event_id, token_id='token',
        direction='BUY', units='2', filled_units='0', unit_collateral_bound='.5',
        attribution=[dict(strategy=rig['scope'].strategy, weight='1')],
        status='RESERVED', cancel_requested=False,
    )
    _forge_head(rig, c, state, 'forged-head')
    result = evaluate_scenario_reservation_readiness(c)
    assert result.outcome == OUTCOME_NO_RESERVATION
    assert 'RESERVED_INTENT_PROVENANCE_UNVERIFIED' in result.reasons
    assert genuine_reserved_intents(c) == ()


@pytest.mark.parametrize('field,value', [
    ('financial_authority', True),
    ('admission_ids', []),
    ('event_state_id', 'missing'),
    ('valuation_id', 'missing'),
])
def test_r08_mutated_intent_is_not_genuine(factory, field, value):
    # Start from a reservation coordinate() genuinely admitted, then forge a
    # replacement head whose only change is one lineage/authority field on
    # the contributing intent. Each mutation independently must still refuse
    # genuine classification -- the authority check must cover every intent,
    # not just the account/risk-level flags.
    rig = factory()
    c = coordinator(rig)
    p = genuine_proposal(rig)
    outcome = c.coordinate('batch', (p,))['body']['details']
    assert outcome['reserved_intent_ids'] == [p.proposal_id]
    state = c._state(c._head())
    state['intents'][p.proposal_id][field] = value
    _forge_head(rig, c, state, 'forged-mutation-' + field)
    result = evaluate_scenario_reservation_readiness(c)
    assert result.outcome == OUTCOME_NO_RESERVATION
    assert genuine_reserved_intents(c) == ()


@pytest.mark.parametrize('kwargs,message', [
    (dict(schema='WRONG', outcome=OUTCOME_NO_RESERVATION, reserved_intent_count=0, reserved_cash='0',
          risk_accepted=False, financial_authority=False, reasons=('X',)), 'schema'),
    (dict(schema=SCHEMA, outcome='NOT_A_REAL_OUTCOME', reserved_intent_count=0, reserved_cash='0',
          risk_accepted=False, financial_authority=False, reasons=('X',)), 'outcome'),
    (dict(schema=SCHEMA, outcome=OUTCOME_NO_RESERVATION, reserved_intent_count=0, reserved_cash='0',
          risk_accepted=False, financial_authority=True, reasons=('X',)), 'financial_authority'),
    (dict(schema=SCHEMA, outcome=OUTCOME_DEMONSTRATED, reserved_intent_count=0, reserved_cash='0',
          risk_accepted=True, financial_authority=False, reasons=()), 'DEMONSTRATED'),
    (dict(schema=SCHEMA, outcome=OUTCOME_NO_RESERVATION, reserved_intent_count=0, reserved_cash='0',
          risk_accepted=False, financial_authority=False, reasons=()), 'NO_RESERVATION'),
])
def test_dataclass_refuses_inconsistent_construction(kwargs, message):
    with pytest.raises(ValueError, match=message):
        ScenarioReservationReadiness(**kwargs)
