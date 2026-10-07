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
from copy import deepcopy
from dataclasses import asdict

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.pws_admission import PWSPreconfirmation
from polymarket_scanner.v11.strategy_admission import StrategyAdmission
from polymarket_scanner.v11.pws_lead import LeadPolicy, PWSObservationLead
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
    assert 'NO_PWS_ATTRIBUTED_RESERVED_INTENT_FOR_THIS_PRECONFIRMATION' in result.reasons
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


def test_r09_valid_unused_pair_cannot_rescue_expired_reservation_pair(joined):
    # The sole reservation ('one') is pinned against 'paired-pin'. A second,
    # genuinely-revalidating pair ('unused-pair', built under a looser
    # max_pws_age_seconds policy) exists but backs no reservation at all.
    # Probing with the unused pair must not launder it into evidence for a
    # reservation it was never actually bound to.
    p = synthetic_proposal(joined)
    c = coordinator(joined)
    outcome = c.coordinate('batch', (p,))['body']['details']
    assert outcome['reserved_intent_ids'] == ['one']
    joined['now'][0] += 28  # test_v11_pws_admission.py: stricter PWS sensor-policy expiry fires here
    store = joined['store']
    looser = LeadPolicy('looser-policy', 120., 60., 60., 120., 120.)
    PWSObservationLead(store).observe('unused-lead', **dict(joined['lead_kw'], policy=looser))
    PWSPreconfirmation(store).pin('unused-pair', lead_id='unused-lead',
                                  observation_admission_id='observation-pin', payout_admission_id='payout-pin')
    result = probe(joined, c, preconfirmation_id='unused-pair')
    assert result.outcome == OUTCOME_NOT_DEMONSTRATED
    assert result.preconfirmation_revalidates is True
    assert result.genuine_pws_reservation_present is False
    assert 'NO_PWS_ATTRIBUTED_RESERVED_INTENT_FOR_THIS_PRECONFIRMATION' in result.reasons


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


@pytest.mark.parametrize('defect', [
    'assessment_missing', 'assessment_null', 'assessment_list', 'assessment_context_missing',
    'request_missing', 'request_null', 'request_list', 'request_extra',
])
def test_malformed_pws_pin_envelope_is_typed_refusal(joined, defect):
    # A retained PWS preconfirmation row that is itself an ordinary,
    # hash-consistent EvidenceStore.audit() append (not a patched reader)
    # can still carry a malformed ``assessment``/``request`` envelope.
    # PWSPreconfirmation.revalidate indexes those fields directly and would
    # otherwise raise a raw KeyError/TypeError; the probe must translate that
    # into the same fail-closed, zero-authority refusal as any other
    # non-revalidating pin.
    p = synthetic_proposal(joined)
    c = coordinator(joined)
    c.coordinate('batch', (p,))
    original = c.store.get('paired-pin')
    d = deepcopy(original['body']['details'])
    if defect == 'assessment_missing':
        del d['assessment']
    elif defect == 'assessment_null':
        d['assessment'] = None
    elif defect == 'assessment_list':
        d['assessment'] = []
    elif defect == 'assessment_context_missing':
        del d['assessment']['context']
    elif defect == 'request_missing':
        del d['request']
    elif defect == 'request_null':
        d['request'] = None
    elif defect == 'request_list':
        d['request'] = []
    else:
        d['request']['unrecognized'] = True
    row = c.store.audit('malformed-pws-pin', event_id=original['event_id'], kind=original['kind'], details=d)
    assert c.store.get(row['id']) == row
    result = probe(joined, c, preconfirmation_id=row['id'])
    assert result.outcome == OUTCOME_NOT_DEMONSTRATED
    assert result.preconfirmation_revalidates is False
    assert any(r.startswith('PRECONFIRMATION_DOES_NOT_REVALIDATE:') for r in result.reasons)
    assert result.financial_authority is False


def test_malformed_pws_pin_sibling_does_not_affect_genuine_pair(joined):
    # Appending a malformed sibling row under a different record id must not
    # disturb the still-valid 'paired-pin' pin's own positive revalidation.
    p = synthetic_proposal(joined)
    c = coordinator(joined)
    outcome = c.coordinate('batch', (p,))['body']['details']
    assert outcome['reserved_intent_ids'] == ['one']
    original = c.store.get('paired-pin')
    d = deepcopy(original['body']['details'])
    del d['assessment']
    c.store.audit('malformed-pws-sibling', event_id=original['event_id'], kind=original['kind'], details=d)
    result = probe(joined, c)
    assert result.outcome == OUTCOME_DEMONSTRATED
    assert result.preconfirmation_revalidates is True
    assert result.genuine_pws_reservation_present is True
    assert result.financial_authority is False


@pytest.mark.parametrize('location', [
    'lead_id', 'observation_admission_id', 'horizon_seconds',
    'max_pws_age_seconds', 'feature_ready_at',
])
def test_wrong_type_reference_and_oversized_lead_number_refuse(joined, location):
    c = coordinator(joined)
    c.coordinate('batch', (synthetic_proposal(joined),))
    pin = c.store.get('paired-pin')
    details = deepcopy(pin['body']['details'])
    if location in {'lead_id', 'observation_admission_id'}:
        details['request'][location] = [] if location == 'lead_id' else {}
    else:
        lead = c.store.get('lead')
        lead_details = deepcopy(lead['body']['details'])
        if location == 'feature_ready_at':
            lead_details[location] = 10**400
        else:
            lead_details['request']['policy'][location] = 10**400
        bad_lead = c.store.audit('bad-lead', event_id=lead['event_id'], kind=lead['kind'], details=lead_details)
        assert c.store.get('bad-lead') == bad_lead
        details['request']['lead_id'] = 'bad-lead'
    bad_pin = c.store.audit('bad-pin', event_id=pin['event_id'], kind=pin['kind'], details=details)
    assert c.store.get('bad-pin') == bad_pin
    result = probe(joined, c, preconfirmation_id='bad-pin')
    assert result.outcome == OUTCOME_NOT_DEMONSTRATED
    assert result.preconfirmation_revalidates is False
    assert any(r.startswith('PRECONFIRMATION_DOES_NOT_REVALIDATE:') for r in result.reasons)
    assert result.financial_authority is False


@pytest.mark.parametrize('location', [
    'official_id', 'pws_id', 'binding', 'bundle_sha256', 'with_pws',
])
def test_malformed_nested_lead_field_refuses(joined, location):
    c = coordinator(joined)
    c.coordinate('batch', (synthetic_proposal(joined),))
    lead = c.store.get('lead')
    details = deepcopy(lead['body']['details'])
    if location == 'with_pws':
        del details['with_pws']
    elif location == 'official_id':
        details['request'][location] = []
    elif location == 'pws_id':
        details['request'][location] = {}
    else:
        del details['request'][location]
    bad_lead = c.store.audit('bad-lead', event_id=lead['event_id'], kind=lead['kind'], details=details)
    assert c.store.get('bad-lead') == bad_lead
    pin = c.store.get('paired-pin')
    pin_details = deepcopy(pin['body']['details'])
    pin_details['request']['lead_id'] = 'bad-lead'
    c.store.audit('bad-pin', event_id=pin['event_id'], kind=pin['kind'], details=pin_details)
    result = probe(joined, c, preconfirmation_id='bad-pin')
    assert result.outcome == OUTCOME_NOT_DEMONSTRATED
    assert result.preconfirmation_revalidates is False
    assert result.financial_authority is False


@pytest.mark.parametrize('field', ['evidence_id', 'role'])
def test_wrong_type_admission_lease_field_refuses(joined, field):
    c = coordinator(joined)
    c.coordinate('batch', (synthetic_proposal(joined),))
    admission = c.store.get('observation-pin')
    details = deepcopy(admission['body']['details'])
    details['request']['source_leases'][0][field] = []
    bad_admission = c.store.audit('bad-admission', event_id=admission['event_id'],
                                  kind=admission['kind'], details=details)
    assert c.store.get('bad-admission') == bad_admission
    pin = c.store.get('paired-pin')
    pin_details = deepcopy(pin['body']['details'])
    pin_details['request']['observation_admission_id'] = 'bad-admission'
    c.store.audit('bad-pin', event_id=pin['event_id'], kind=pin['kind'], details=pin_details)
    result = probe(joined, c, preconfirmation_id='bad-pin')
    assert result.outcome == OUTCOME_NOT_DEMONSTRATED
    assert result.preconfirmation_revalidates is False
    assert result.financial_authority is False


@pytest.mark.parametrize('error', [KeyError, TypeError, ValueError, AttributeError])
def test_valid_pin_does_not_hide_admission_revalidation_defect(joined, monkeypatch, error):
    c = coordinator(joined)
    c.coordinate('batch', (synthetic_proposal(joined),))
    sentinel = error('VALID_PIN_ADMISSION_DEFECT')

    def faulty(*args, **kwargs):
        raise sentinel

    monkeypatch.setattr(StrategyAdmission, 'revalidate', faulty)
    with pytest.raises(error) as caught:
        probe(joined, c)
    assert caught.value is sentinel


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
