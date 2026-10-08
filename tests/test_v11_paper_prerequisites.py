"""The offline prerequisite inventory cannot promote synthetic evidence."""
from copy import deepcopy

from polymarket_scanner.v11.paper_prerequisites import METRICS, RISKS, inventory
from polymarket_scanner.v11.valuation import ENTRY_RISKS
from polymarket_scanner.v11.evidence import digest


SCOPE = dict(account_id='account', event_id='event', rule_fingerprint='a'*64, at=100.)


def check_unknown(result):
    assert tuple(result['engine_metrics']) == METRICS
    assert set(result['entry_costs']) == ENTRY_RISKS == set(RISKS)
    assert all(row['status'] == 'UNKNOWN' and row['value'] is None
               for row in result['engine_metrics'].values())
    assert all(row['status'] == 'UNKNOWN' and row['per_share'] is None
               for row in result['entry_costs'].values())
    assert result['complete_cost_coverage'] is False
    assert result['financial_authority'] is False and result['admission_eligible'] is False
    assert result['manifest_sha256'] == digest({k: v for k, v in result.items() if k != 'manifest_sha256'})


def observation():
    return dict(version='alpha_v11_paper_risk_observation_v1', namespace='V11_PAPER',
                account_id='account', event_id='event', rule_fingerprint='a'*64,
                financial_authority=False, admission_eligible=False,
                event_metrics_adverse_fills=None, event_metrics_recent_markout_per_share=None,
                evidence_class='SYNTHETIC_PAPER_DIAGNOSTIC', evidence_ids=['proof'],
                observed_at=99., policy_sha256='b'*64, frontier_tip_sha256='c'*64,
                frontier_sha256='d'*64, replay_sha256='e'*64,
                execution_status='OBSERVED_SYNTHETIC_DIAGNOSTIC',
                diagnostic_adverse_fill_count=0, diagnostic_markout_collateral_per_share='0.1')


def test_empty_inventory_is_deterministic_and_unknown():
    one = inventory(**SCOPE)
    assert one == inventory(**SCOPE)
    assert one['diagnostic'] is None
    assert one['diagnostic_reason'] == 'NO_ARCHIVED_DIAGNOSTIC_SUPPLIED'
    check_unknown(one)


def test_supplied_synthetic_diagnostic_is_unverified_and_never_promoted():
    supplied = observation(); before = deepcopy(supplied)
    result = inventory(**SCOPE, observation=supplied)
    assert supplied == before
    assert result['diagnostic']['evidence_class'] == 'SYNTHETIC_PAPER_DIAGNOSTIC_UNVERIFIED'
    assert result['diagnostic']['evidence_ids'] == ['proof']
    assert result['diagnostic_reason'] is None
    check_unknown(result)


def test_wrong_scope_spoofed_authority_and_partial_provenance_remain_unknown():
    for change in ({'event_id': 'other'}, {'financial_authority': True},
                   {'admission_eligible': True}, {'event_metrics_adverse_fills': 0},
                   {'execution_status': 'MEASURED_LIVE'},
                   {'frontier_sha256': None}, {'evidence_ids': ['proof', 'proof']},
                   {'evidence_class': 'PUBLIC_OBSERVED'}):
        supplied = observation(); supplied.update(change)
        result = inventory(**SCOPE, observation=supplied)
        assert result['diagnostic'] is None
        assert result['diagnostic_reason'] is not None
        check_unknown(result)


def test_oversized_and_malformed_diagnostics_remain_unknown():
    for supplied in ([], dict.fromkeys(range(49)),
                     dict(observation(), evidence_ids=['x'] * 4097)):
        result = inventory(**SCOPE, observation=supplied)
        assert result['diagnostic'] is None
        check_unknown(result)
