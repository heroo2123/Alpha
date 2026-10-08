"""Adversarial coverage for the execution-health/settlement promotion contract.

Most "rejected" fixtures below are still hand-built synthetic rows, exercising
one structural/scope/freshness check each; per the module's own scope, a
synthetic fixture only proves the code logic and never qualifies live
acceptance. Two tests are different on purpose: `promote_execution_health`'s
real forgery defense (F1) is an independent full re-derivation of a row's
claimed `observe()` result from the store's own genuine archive history, so
proving that defense requires at least one *genuinely* `observe()`'d archive,
built with the same `rig`/`sequenced_fill`/`sample`/`policy` fixtures
`test_v11_paper_risk_observation.py` already uses for exactly this purpose.
"""
from copy import deepcopy
from dataclasses import asdict

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, digest
from polymarket_scanner.v11.paper_execution_health_promotion import (
    AUTHORIZED_SETTLEMENT_PROVIDERS, ExecutionHealthPromotion, MAXIMUM_OBSERVATION_AGE_SECONDS,
    SETTLEMENT_FINALITY_VERSION, SettlementFinalityObservation,
    promote_execution_health, promote_settlement_timing,
)
from polymarket_scanner.v11.paper_risk_observation import VERSION as OBSERVATION_VERSION

# Reused, not reinvented: this is the same genuine-archive fixture machinery
# `test_v11_paper_risk_observation.py` already uses to build a real pinned
# EvidenceStore archive and observe() it for real. `rig` is imported under an
# alias because this file also keeps its own minimal single-row `rig` fixture
# (below) for the many structural/scope/freshness tests that never need a
# full genuine archive to exercise the check they target.
from test_v11_basket_coordinator import rig as basket_rig
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_strategy_pipeline import factory
from test_v11_paper_risk_observation import policy as observation_policy, sample, sequenced_fill


ACCOUNT_ID = 'account'
EVENT_ID = 'event'
RULE_FINGERPRINT = 'a' * 64
COLLATERAL_ASSET = 'FIXTURE_COLLATERAL'

# A real `ObservationPolicy` (same shape `promote_execution_health` now
# requires via its `policy` parameter), used both to populate the
# hand-built `details()` fixture's self-declared `policy_sha256`/
# `policy_config_sha256` and as the exact object passed to `promote()` below.
# Using the same real instance for both sides is what lets every test that
# is NOT about policy identity itself (scope, shape, freshness, markout,
# fill-count checks) reach the specific check it targets instead of failing
# earlier on a policy mismatch the repaired contract now also verifies.
POLICY = observation_policy()
POLICY_CONFIG_SHA256 = digest(asdict(POLICY))


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
        collateral_asset=COLLATERAL_ASSET, policy_sha256=POLICY.policy_sha256,
        policy_config_sha256=POLICY_CONFIG_SHA256, frontier_tip_sha256='d' * 64, frontier_sha256='e' * 64,
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
                                    policy=POLICY)


# -- The real contract: genuine archive in, genuine replay out --------------

def test_genuine_observation_promotes_through_real_replay(basket_rig, monkeypatch):
    """End-to-end proof the F1 repair's replay path is real, not a stub: a
    genuine `observe()` result over a genuine archive of real synthetic PAPER
    fills/book updates, written as an unmodified passthrough MEASUREMENT row,
    independently re-derives to the exact same numbers and is PROMOTED.
    """
    sequenced_fill(basket_rig, monkeypatch)
    result = sample(basket_rig)
    assert result['execution_status'] == 'OBSERVED_SYNTHETIC_DIAGNOSTIC', result['reason']
    assert result['fill_count'] == 1 and result['diagnostic_adverse_fill_count'] == 1
    assert result['diagnostic_markout_collateral_per_share'] == '-0.10'

    store = basket_rig['store']
    store.audit('genuine-obs', event_id=result['event_id'], kind='MEASUREMENT', details=result,
               evidence_ids=tuple(result['evidence_ids']))
    promoted = promote_execution_health(store, 'genuine-obs', account_id='account', event_id=result['event_id'],
                                        rule_fingerprint=basket_rig['rule'].sha256,
                                        collateral_asset='FIXTURE_COLLATERAL', policy=observation_policy())
    assert promoted == ExecutionHealthPromotion(1, -0.10, 'PROMOTED', None, tuple(result['evidence_ids']))
    # Deterministic and non-mutating against a real store, same as the
    # synthetic-fixture determinism test below.
    again = promote_execution_health(store, 'genuine-obs', account_id='account', event_id=result['event_id'],
                                     rule_fingerprint=basket_rig['rule'].sha256,
                                     collateral_asset='FIXTURE_COLLATERAL', policy=observation_policy())
    assert promoted == again


def test_fabricated_row_with_a_real_frontier_tip_is_rejected_not_promoted(rig):
    """The prior HIGH-severity hole this module's F1 repair closes: a
    hand-built MEASUREMENT row that only hashes consistently with itself,
    now anchored to one real archive row's genuine sha256 (so the lineage
    scan does find a tip to start from), used to be accepted as PROMOTED by
    the old design's self-referential `replay_sha256` check -- which only
    re-hashed fields the row declared about itself and never re-verified
    anything against the store's real history. The repaired contract
    independently reconstructs this exact archive slice with `observe()` and
    requires an EXACT dict match; a fabricated `OBSERVED_SYNTHETIC_DIAGNOSTIC`
    claim over an archive that in genuine truth has no RULE_STATE/account
    lineage at all can never match, so it stays UNKNOWN.
    """
    store, now = rig
    real_tip = store.get('anchor')['sha256']
    record(store, 'obs', details(frontier_tip_sha256=real_tip))
    result = promote(store, 'obs')
    assert result.status == 'UNKNOWN' and result.reason == 'EXECUTION_HEALTH_OBSERVATION_REPLAY_MISMATCH'
    assert result.adverse_fills is None and result.recent_markout_per_share is None


def test_tampered_diagnostic_numbers_in_an_otherwise_genuine_observation_is_rejected(basket_rig, monkeypatch):
    """Documents the exact boundary the old design got wrong, now closed:
    `replay_sha256` (mirroring `paper_risk_observation._result`'s own formula)
    never covered `diagnostic_adverse_fill_count`/
    `diagnostic_markout_collateral_per_share` themselves, so a row that
    tampered only those two numbers after an otherwise-genuine `observe()`
    result still hashed consistently with the old check and used to promote
    with the tampered number. F1's full-dict replay-equality check closes
    exactly this gap: the independently recomputed result still has the real
    numbers, so it no longer matches the tampered row at all.
    """
    sequenced_fill(basket_rig, monkeypatch)
    result = sample(basket_rig)
    # fill_count is 1 and the genuine adverse count is 1; 0 is the only other
    # value that still passes this module's own `0 <= adverse <= fill_count`
    # shape check, so it is the only tamper that reaches the F1 replay check
    # instead of being caught earlier by that shape check.
    tampered = dict(result, diagnostic_adverse_fill_count=0)
    store = basket_rig['store']
    store.audit('tampered-obs', event_id=tampered['event_id'], kind='MEASUREMENT', details=tampered,
               evidence_ids=tuple(tampered['evidence_ids']))
    promoted = promote_execution_health(store, 'tampered-obs', account_id='account', event_id=tampered['event_id'],
                                        rule_fingerprint=basket_rig['rule'].sha256,
                                        collateral_asset='FIXTURE_COLLATERAL', policy=observation_policy())
    assert promoted.status == 'UNKNOWN' and promoted.reason == 'EXECUTION_HEALTH_OBSERVATION_REPLAY_MISMATCH'
    assert promoted.adverse_fills is None and promoted.recent_markout_per_share is None


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
    # F4/F5 (see module docstring): a duplicate evidence id is no longer
    # rejected by this shape check alone -- genuine multi-fill observe()
    # output can legally repeat an anchor/horizon-book id. The
    # ['proof', 'proof'] case below still ends up UNKNOWN, but now via the
    # F1 replay check (this fixture's frontier_tip_sha256 is still the
    # fake default and can never be found in the store's real history),
    # not via a duplicate-id rejection.
    store, now = rig
    record(store, 'obs', details(**change))
    assert promote(store, 'obs').status == 'UNKNOWN'


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
