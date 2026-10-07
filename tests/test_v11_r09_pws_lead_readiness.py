"""Requirement 9 readiness probe regressions.

Reuses `test_v11_pws_admission.py`'s `joined`/`synthetic_proposal`/`coordinator`
fixtures: that is the one place in this repository where a real
`PWSObservationLead` + `PWSPreconfirmation` + `StrategyAdmission` +
`EventRiskEngine` chain is already exercised end to end (only the downstream
economic EV result is a labeled synthetic fixture, same established pattern
as `test_v11_r08_scenario_reservation_readiness.py`). This file does not
re-derive that chain; it proves the requirement-9 readiness probe correctly
recognizes it.
"""
from dataclasses import asdict

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_pws_admission import coordinator, joined, synthetic_proposal
from test_v11_strategy_pipeline import factory

from tools.v11_r09_pws_lead_readiness import (
    OUTCOME_DEMONSTRATED,
    OUTCOME_NOT_DEMONSTRATED,
    SCHEMA,
    PWSLeadReadiness,
    evaluate_pws_lead_readiness,
)


def probe(rig, c, **changes):
    kwargs = dict(preconfirmation_id='paired-pin', context=rig['context'], rule=rig['rule'],
                  binding=asdict(rig['binding']), payout_admission_ids=('payout-pin',))
    kwargs.update(changes)
    return evaluate_pws_lead_readiness(c, **kwargs)


def test_type_guard_rejects_non_coordinator(joined):
    with pytest.raises(EvidenceError, match='TYPED_COORDINATOR_REQUIRED'):
        evaluate_pws_lead_readiness(object(), preconfirmation_id='paired-pin', context=joined['context'],
                                    rule=joined['rule'], binding=asdict(joined['binding']),
                                    payout_admission_ids=('payout-pin',))


def test_no_pair_and_no_reservation_reports_not_demonstrated(joined):
    c = coordinator(joined)  # nothing coordinated yet: pair exists, reservation does not
    result = probe(joined, c)
    assert result.outcome == OUTCOME_NOT_DEMONSTRATED
    assert result.preconfirmation_revalidates is True
    assert result.pws_never_settlement_authority is True
    assert result.unpaired_pws_proposal_refused is True
    assert result.genuine_pws_reservation_present is False
    assert 'NO_PWS_ATTRIBUTED_RESERVED_INTENT' in result.reasons
    assert result.financial_authority is False


def test_missing_preconfirmation_pin_reports_not_demonstrated(joined):
    c = coordinator(joined)
    result = probe(joined, c, preconfirmation_id='never-pinned')
    assert result.outcome == OUTCOME_NOT_DEMONSTRATED
    assert result.preconfirmation_revalidates is False
    assert any(r.startswith('PRECONFIRMATION_DOES_NOT_REVALIDATE:') for r in result.reasons)


def test_expired_sensor_policy_is_not_laundered_into_demonstrated(joined):
    c = coordinator(joined)
    joined['now'][0] += 28  # test_v11_pws_admission.py: stricter PWS sensor-policy expiry fires here
    result = probe(joined, c)
    assert result.outcome == OUTCOME_NOT_DEMONSTRATED
    assert result.preconfirmation_revalidates is False
    assert any('SOURCE_POLICY_EXPIRED' in r for r in result.reasons)


def test_genuine_pws_lead_observed_and_netted_as_lead_only_is_demonstrated(joined):
    p = synthetic_proposal(joined)
    c = coordinator(joined)
    outcome = c.coordinate('batch', (p,))['body']['details']
    assert outcome['reserved_intent_ids'] == ['one']
    result = probe(joined, c)
    assert result.outcome == OUTCOME_DEMONSTRATED
    assert result.preconfirmation_revalidates is True
    assert result.pws_never_settlement_authority is True
    assert result.unpaired_pws_proposal_refused is True
    assert result.genuine_pws_reservation_present is True
    assert result.financial_authority is False
    assert result.reasons == ()
    assert result.schema == SCHEMA


def test_result_is_deterministic_on_replay(joined):
    p = synthetic_proposal(joined)
    c = coordinator(joined)
    c.coordinate('batch', (p,))
    first = probe(joined, c)
    second = probe(joined, c)
    assert first == second
    assert first.to_dict() == second.to_dict()


@pytest.mark.parametrize('kwargs,message', [
    (dict(schema='WRONG', outcome=OUTCOME_NOT_DEMONSTRATED, preconfirmation_revalidates=False,
          pws_never_settlement_authority=False, unpaired_pws_proposal_refused=False,
          genuine_pws_reservation_present=False, financial_authority=False, reasons=('X',)), 'schema'),
    (dict(schema=SCHEMA, outcome='NOT_A_REAL_OUTCOME', preconfirmation_revalidates=False,
          pws_never_settlement_authority=False, unpaired_pws_proposal_refused=False,
          genuine_pws_reservation_present=False, financial_authority=False, reasons=('X',)), 'outcome'),
    (dict(schema=SCHEMA, outcome=OUTCOME_NOT_DEMONSTRATED, preconfirmation_revalidates=False,
          pws_never_settlement_authority=False, unpaired_pws_proposal_refused=False,
          genuine_pws_reservation_present=False, financial_authority=True, reasons=('X',)), 'financial_authority'),
    (dict(schema=SCHEMA, outcome=OUTCOME_DEMONSTRATED, preconfirmation_revalidates=True,
          pws_never_settlement_authority=True, unpaired_pws_proposal_refused=True,
          genuine_pws_reservation_present=False, financial_authority=False, reasons=()), 'DEMONSTRATED'),
    (dict(schema=SCHEMA, outcome=OUTCOME_NOT_DEMONSTRATED, preconfirmation_revalidates=False,
          pws_never_settlement_authority=False, unpaired_pws_proposal_refused=False,
          genuine_pws_reservation_present=False, financial_authority=False, reasons=()), 'NOT_DEMONSTRATED'),
])
def test_dataclass_refuses_inconsistent_construction(kwargs, message):
    with pytest.raises(ValueError, match=message):
        PWSLeadReadiness(**kwargs)
