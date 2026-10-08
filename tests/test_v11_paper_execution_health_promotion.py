"""Adversarial coverage for the execution-health/settlement promotion contract.

Every "promoted" fixture here is a hand-built synthetic row, not a genuine
archive observe()'d from real fills: per the module's own scope, a synthetic
fixture proves the code logic and never qualifies live acceptance.
"""
from copy import deepcopy

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, digest
from polymarket_scanner.v11.paper_execution_health_promotion import (
    AUTHORIZED_SETTLEMENT_PROVIDERS, ExecutionHealthPromotion, MAXIMUM_OBSERVATION_AGE_SECONDS,
    SETTLEMENT_FINALITY_VERSION, SettlementFinalityObservation,
    promote_execution_health, promote_settlement_timing,
)
from polymarket_scanner.v11.paper_risk_observation import VERSION as OBSERVATION_VERSION


ACCOUNT_ID = 'account'
EVENT_ID = 'event'
RULE_FINGERPRINT = 'a' * 64
COLLATERAL_ASSET = 'FIXTURE_COLLATERAL'
POLICY_SHA256 = 'b' * 64


@pytest.fixture
def rig(tmp_path):
    tmp_path.chmod(0o700)
    now = [1000.]
    store = EvidenceStore(tmp_path / 'evidence.sqlite', 'V11_PAPER', clock=lambda: now[0])
    store.capture('anchor', event_id=EVENT_ID, kind='BOOK', provider='clob', source_identity='token',
                  revision='1', observed_at=now[0], evidence_class='SYNTHETIC',
                  payload={'stream_healthy': True})
    return store, now


def details(**overrides):
    base = dict(
        version=OBSERVATION_VERSION, namespace='V11_PAPER', financial_authority=False,
        evidence_class='SYNTHETIC_PAPER_DIAGNOSTIC', admission_eligible=False,
        event_metrics_adverse_fills=None, event_metrics_recent_markout_per_share=None,
        new_risk_cutoff_at=None, seconds_to_new_risk_cutoff=None,
        settlement_finality_status='UNKNOWN', cutoff_reason='REVIEWED_CUTOFF_POLICY_UNAVAILABLE',
        execution_status='OBSERVED_SYNTHETIC_DIAGNOSTIC', reason=None, observed_at=990.,
        account_id=ACCOUNT_ID, event_id=EVENT_ID, rule_fingerprint=RULE_FINGERPRINT,
        collateral_asset=COLLATERAL_ASSET, policy_sha256=POLICY_SHA256,
        policy_config_sha256='c' * 64, frontier_tip_sha256='d' * 64, frontier_sha256='e' * 64,
        fill_count=1, diagnostic_adverse_fill_count=0, diagnostic_markout_collateral_per_share='0.01',
        evidence_ids=['proof'], valid_until=1100.,
    )
    base.update(overrides)
    if 'replay_sha256' not in overrides:
        base['replay_sha256'] = digest([OBSERVATION_VERSION, base['policy_config_sha256'], base['observed_at'],
                                        base['frontier_sha256'], base['frontier_tip_sha256'],
                                        base['account_id'], base['event_id'], base['rule_fingerprint'],
                                        base['collateral_asset']])
    return base


def record(store, record_id, body, evidence_ids=('anchor',)):
    return store.audit(record_id, event_id=body['event_id'], kind='MEASUREMENT', details=body,
                       evidence_ids=evidence_ids)


def promote(store, record_id):
    return promote_execution_health(store, record_id, account_id=ACCOUNT_ID, event_id=EVENT_ID,
                                    rule_fingerprint=RULE_FINGERPRINT, collateral_asset=COLLATERAL_ASSET,
                                    policy_sha256=POLICY_SHA256)


def test_well_formed_genuine_fixture_promotes_exact_numbers(rig):
    store, now = rig
    record(store, 'obs', details())
    result = promote(store, 'obs')
    assert result == ExecutionHealthPromotion(0, 0.01, 'PROMOTED', None, ('proof',))


def test_genuine_adverse_negative_markout_promotes(rig):
    store, now = rig
    record(store, 'obs', details(fill_count=3, diagnostic_adverse_fill_count=2,
                                 diagnostic_markout_collateral_per_share='-0.07'))
    result = promote(store, 'obs')
    assert result.status == 'PROMOTED' and result.adverse_fills == 2 and result.recent_markout_per_share == -0.07


def test_missing_record_is_unknown(rig):
    store, now = rig
    result = promote(store, 'does-not-exist')
    assert result.status == 'UNKNOWN' and result.reason == 'EXECUTION_HEALTH_OBSERVATION_MISSING'
    assert result.adverse_fills is None and result.recent_markout_per_share is None


def test_wrong_kind_is_unknown(rig):
    store, now = rig
    store.audit('obs', event_id=EVENT_ID, kind='RUNTIME_STATUS', details=details(), evidence_ids=('anchor',))
    assert promote(store, 'obs').reason == 'EXECUTION_HEALTH_OBSERVATION_SCOPE_MISMATCH'


@pytest.mark.parametrize('change', [
    {'event_id': 'other-event'}, {'account_id': 'other-account'},
    {'rule_fingerprint': 'f' * 64}, {'collateral_asset': 'OTHER'}, {'policy_sha256': 'f' * 64},
])
def test_cross_scope_mismatch_is_unknown(rig, change):
    store, now = rig
    record(store, 'obs', details(**change))
    assert promote(store, 'obs').status == 'UNKNOWN'


def test_cross_market_record_bound_to_different_store_event_id_is_unknown(rig):
    store, now = rig
    store.capture('anchor2', event_id='other-event', kind='BOOK', provider='clob', source_identity='token',
                  revision='1', observed_at=now[0], evidence_class='SYNTHETIC', payload={'stream_healthy': True})
    store.audit('obs', event_id='other-event', kind='MEASUREMENT', details=details(event_id='other-event'),
               evidence_ids=('anchor2',))
    assert promote(store, 'obs').status == 'UNKNOWN'


@pytest.mark.parametrize('change', [
    {'financial_authority': True}, {'admission_eligible': True},
    {'event_metrics_adverse_fills': 0}, {'event_metrics_recent_markout_per_share': 0.0},
    {'evidence_class': 'PUBLIC_OBSERVED'},
])
def test_spoofed_authority_or_class_is_unknown(rig, change):
    store, now = rig
    record(store, 'obs', details(**change))
    assert promote(store, 'obs').status == 'UNKNOWN'


@pytest.mark.parametrize('change', [
    {'settlement_finality_status': 'FINAL'}, {'cutoff_reason': 'SOMETHING_ELSE'},
    {'new_risk_cutoff_at': 2000.}, {'seconds_to_new_risk_cutoff': 10.},
])
def test_record_cannot_smuggle_a_foreign_settlement_or_cutoff_claim(rig, change):
    store, now = rig
    record(store, 'obs', details(**change))
    assert promote(store, 'obs').reason == 'EXECUTION_HEALTH_OBSERVATION_FOREIGN_CLAIM'


def test_zero_genuine_orders_is_unknown_never_zero(rig):
    store, now = rig
    record(store, 'obs', details(execution_status='NO_RECONCILED_RECENT_PAPER_FILLS',
                                 reason='NO_RECONCILED_RECENT_PAPER_FILLS', fill_count=0,
                                 diagnostic_adverse_fill_count=0,
                                 diagnostic_markout_collateral_per_share='0'))
    result = promote(store, 'obs')
    assert result.status == 'UNKNOWN' and result.adverse_fills is None


def test_any_nonsuccess_reason_present_is_unknown(rig):
    store, now = rig
    record(store, 'obs', details(reason='OBSERVATION_FRONTIER_INCOMPLETE'))
    assert promote(store, 'obs').status == 'UNKNOWN'


@pytest.mark.parametrize('change', [
    {'fill_count': 0}, {'fill_count': None}, {'fill_count': -1},
])
def test_inconsistent_fill_count_is_unknown(rig, change):
    store, now = rig
    record(store, 'obs', details(**change))
    assert promote(store, 'obs').status == 'UNKNOWN'


@pytest.mark.parametrize('change', [
    {'diagnostic_adverse_fill_count': -1}, {'diagnostic_adverse_fill_count': 5},
    {'diagnostic_adverse_fill_count': None}, {'diagnostic_adverse_fill_count': '0'},
])
def test_adverse_count_out_of_bound_or_wrong_type_is_unknown(rig, change):
    store, now = rig
    record(store, 'obs', details(**change))
    assert promote(store, 'obs').status == 'UNKNOWN'


@pytest.mark.parametrize('markout', ['nan', 'inf', '-inf', 'not-a-number', None, 1.5, 'E400'])
def test_malformed_markout_is_unknown(rig, markout):
    store, now = rig
    record(store, 'obs', details(diagnostic_markout_collateral_per_share=markout))
    assert promote(store, 'obs').status == 'UNKNOWN'


@pytest.mark.parametrize('change', [{'frontier_sha256': None}, {'frontier_tip_sha256': None},
                                    {'policy_config_sha256': None}, {'evidence_ids': []},
                                    {'evidence_ids': ['x'] * 4097}, {'evidence_ids': ['proof', 'proof']}])
def test_anonymous_or_oversized_or_duplicated_evidence_is_unknown(rig, change):
    store, now = rig
    record(store, 'obs', details(**change))
    assert promote(store, 'obs').status == 'UNKNOWN'


def test_forged_replay_hash_is_unknown(rig):
    store, now = rig
    tampered = details()
    tampered['frontier_sha256'] = 'f' * 64  # edited after replay_sha256 was pinned for the real frontier
    record(store, 'obs', tampered)
    result = promote(store, 'obs')
    assert result.status == 'UNKNOWN' and result.reason == 'EXECUTION_HEALTH_OBSERVATION_FORGED_OR_DUPLICATED'


def test_replay_hash_does_not_cover_the_two_diagnostic_numbers_by_design(rig):
    """Documented boundary, not a regression: replay_sha256 (mirroring
    paper_risk_observation._result's own formula) pins context/config/frontier
    identity, never the diagnostic numbers themselves. This module trusts that
    a future writer's MEASUREMENT row is an unmodified passthrough of
    observe()'s return value; reviewing that writer is a separate, necessary
    step this reader cannot substitute for without rescanning the archive.
    """
    store, now = rig
    tampered = details()
    tampered['diagnostic_adverse_fill_count'] = 1
    record(store, 'obs', tampered)
    result = promote(store, 'obs')
    assert result.status == 'PROMOTED' and result.adverse_fills == 1


def test_future_dated_observation_is_unknown(rig):
    store, now = rig
    record(store, 'obs', details(observed_at=now[0] + 50, valid_until=now[0] + 200))
    assert promote(store, 'obs').reason == 'EXECUTION_HEALTH_OBSERVATION_IN_FUTURE'


def test_stale_observation_beyond_age_bound_is_unknown(rig):
    store, now = rig
    record(store, 'obs', details())
    now[0] += MAXIMUM_OBSERVATION_AGE_SECONDS + 1
    assert promote(store, 'obs').reason == 'EXECUTION_HEALTH_OBSERVATION_STALE'


def test_expired_valid_until_is_unknown(rig):
    store, now = rig
    record(store, 'obs', details(valid_until=now[0] - 1))
    assert promote(store, 'obs').reason == 'EXECUTION_HEALTH_OBSERVATION_STALE'


def test_promotion_is_deterministic_and_does_not_mutate_the_record(rig):
    store, now = rig
    body = details(); before = deepcopy(body)
    record(store, 'obs', body)
    assert body == before
    first, second = promote(store, 'obs'), promote(store, 'obs')
    assert first == second


def test_result_dataclass_rejects_internally_inconsistent_construction():
    with pytest.raises(EvidenceError):
        ExecutionHealthPromotion(None, None, 'PROMOTED', None, ())
    with pytest.raises(EvidenceError):
        ExecutionHealthPromotion(1, None, 'PROMOTED', None, ())
    with pytest.raises(EvidenceError):
        ExecutionHealthPromotion(0, 0.0, 'UNKNOWN', 'reason', ())
    with pytest.raises(EvidenceError):
        ExecutionHealthPromotion(-1, 0.0, 'PROMOTED', None, ())
    with pytest.raises(EvidenceError):
        ExecutionHealthPromotion(None, None, 'SOMETHING_ELSE', 'reason', ())


# -- Settlement finality: a distinct, currently-unauthorized contract --------

def settlement_observation(**overrides):
    base = dict(version=SETTLEMENT_FINALITY_VERSION, provider='uma-fixture', event_id=EVENT_ID,
               market_id='market', source_identity='market-token', observed_at=990.,
               disputed=False, finalized=True, evidence_ids=('proof',))
    base.update(overrides)
    return SettlementFinalityObservation(**base)


def test_authorized_settlement_providers_is_empty_today():
    assert AUTHORIZED_SETTLEMENT_PROVIDERS == frozenset()


def test_settlement_timing_is_never_promoted_without_an_authorized_provider():
    value, reason = promote_settlement_timing(settlement_observation(), event_id=EVENT_ID, now=1000.,
                                              maximum_observation_age_seconds=3600.)
    assert value is None and reason == 'SETTLEMENT_FINALITY_SOURCE_NOT_AUTHORIZED'


def test_settlement_timing_rejects_non_typed_input():
    value, reason = promote_settlement_timing({'provider': 'uma-fixture'}, event_id=EVENT_ID, now=1000.,
                                              maximum_observation_age_seconds=3600.)
    assert value is None and reason == 'SETTLEMENT_FINALITY_OBSERVATION_REQUIRED'


def test_settlement_timing_cross_event_scope_is_rejected():
    value, reason = promote_settlement_timing(settlement_observation(event_id='other'), event_id=EVENT_ID,
                                              now=1000., maximum_observation_age_seconds=3600.)
    assert value is None and reason == 'SETTLEMENT_FINALITY_SCOPE_MISMATCH'


def test_settlement_timing_future_observation_is_rejected():
    value, reason = promote_settlement_timing(settlement_observation(observed_at=1500.), event_id=EVENT_ID,
                                              now=1000., maximum_observation_age_seconds=3600.)
    assert value is None and reason == 'SETTLEMENT_FINALITY_OBSERVATION_IN_FUTURE'


def test_settlement_timing_stale_observation_is_rejected():
    value, reason = promote_settlement_timing(settlement_observation(observed_at=0.), event_id=EVENT_ID,
                                              now=10000., maximum_observation_age_seconds=3600.)
    assert value is None and reason == 'SETTLEMENT_FINALITY_OBSERVATION_STALE'


def test_settlement_finality_observation_rejects_contradictory_or_malformed_fields():
    with pytest.raises(EvidenceError):
        settlement_observation(version='wrong-version')
    with pytest.raises(EvidenceError):
        settlement_observation(disputed=True, finalized=True)
    with pytest.raises(EvidenceError):
        settlement_observation(evidence_ids=())
    with pytest.raises(EvidenceError):
        settlement_observation(disputed='yes')
