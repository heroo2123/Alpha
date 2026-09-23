from dataclasses import replace

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, ReleaseBinding
from polymarket_scanner.v11.event_risk import (
    EventContext, EventMetrics, EventPolicy, EventRiskEngine, SafetyReductions, StateGuard,
)


BINDING = ReleaseBinding('a'*40, 'b'*40, 'c'*64, 'd'*64, 'e'*64)
CONTEXT = EventContext('account', 'city', 'station', 'event')


def policy():
    # Synthetic engineering fixture, not calibrated trading thresholds.
    return EventPolicy('fixture-1', 60., 120., 60., 2., 5., 600., 1200.,
                       .01, .05, .1, .2, .2, .5, .1, .3, .5, .9, 3, .1, 60.,
                       3, 20., 10., StateGuard(1, 0, 1, 1, 30),
                       StateGuard(.5, .01, .5, 2, 10), StateGuard(.2, .02, .2, 3, 5),
                       StateGuard(.5, .01, .5, 2, 10))


def metrics(at):
    return EventMetrics(at, 0., 100., 0., .01, 0., 0., .1, 3600., 0, 0., True, True, True)


@pytest.fixture
def rig(tmp_path):
    tmp_path.chmod(0o700)
    now = [1000.]
    store = EvidenceStore(tmp_path/'evidence.sqlite', 'V11_PAPER', clock=lambda: now[0])
    return store, now


def captures(store, now, suffix, *, observed=None, provider='official', historical=False):
    at = now[0] if observed is None else observed
    for kind, name in [('BOOK', 'book'), ('OFFICIAL_OBSERVATION', 'source')]:
        store.capture(name+suffix, event_id='event', kind=kind, provider='clob' if name == 'book' else provider,
                      source_identity='token' if name == 'book' else 'station', revision=str(at),
                      observed_at=at, evidence_class='HISTORICAL_AVAILABILITY_UNKNOWN' if historical else 'SYNTHETIC',
                      payload={'stream_healthy': True})
    return ('book'+suffix,), ('source'+suffix,)


def step(store, now, suffix, **changes):
    b, s = captures(store, now, suffix)
    return EventRiskEngine(store).step('state'+suffix, context=CONTEXT, policy=policy(), binding=BINDING,
                                      metrics=replace(metrics(now[0]), **changes), book_ids=b, source_ids=s)['body']['details']


def test_bootstrap_recovery_requires_distinct_fresh_samples_and_span(rig):
    store, now = rig
    assert step(store, now, '1')['state'] == 'RECOVERY'
    now[0] += 10
    assert step(store, now, '2')['state'] == 'RECOVERY'
    now[0] += 10
    result = step(store, now, '3')
    assert result['state'] == 'NORMAL' and result['stability']['count'] == 3
    assert result['guard']['size_multiplier'] == 1
    assert result['ordinary_new_risk_research_allowed'] and not result['financial_authority']


def test_elapsed_time_and_rearchived_identical_observations_cannot_recover(rig):
    store, now = rig
    first = step(store, now, '1')
    now[0] += 30
    b, s = captures(store, now, 'again', observed=1000.)
    result = EventRiskEngine(store).step('again', context=CONTEXT, policy=policy(), binding=BINDING,
                                       metrics=metrics(now[0]), book_ids=b, source_ids=s)['body']['details']
    assert result['stability']['count'] == first['stability']['count'] == 1
    assert result['state'] == 'RECOVERY'


@pytest.mark.parametrize('changes,reason', [
    ({'new_model_run': True}, 'NEW_MODEL_RUN'), ({'official_forecast_release': True}, 'OFFICIAL_FORECAST_RELEASE'),
    ({'special_observation': True}, 'SPECIAL_OBSERVATION'), ({'source_revision': True}, 'SOURCE_REVISION'),
    ({'websocket_synchronized': False}, 'WEBSOCKET_SYNCHRONIZED'), ({'sources_healthy': False}, 'SOURCES_HEALTHY'),
    ({'clock_healthy': False}, 'CLOCK_HEALTHY'), ({'model_age_seconds': None}, 'MODEL_AGE_UNKNOWN'),
    ({'model_disagreement': 6}, 'MODEL_DISAGREEMENT'), ({'price_velocity': .06}, 'PRICE_VELOCITY'),
    ({'depth_loss': .6}, 'DEPTH_LOSS'), ({'spread': .3}, 'SPREAD'),
    ({'cross_bucket_motion': .4}, 'CROSS_BUCKET_MOTION'), ({'loss_utilization': 1}, 'LOSS_UTILIZATION'),
    ({'adverse_fills': 3}, 'ADVERSE_EXECUTION'), ({'recent_markout_per_share': -.2}, 'ADVERSE_EXECUTION'),
    ({'time_to_settlement_seconds': 0}, 'SETTLEMENT_WINDOW_UNKNOWN_OR_CLOSED'),
])
def test_event_hazards_request_cancellation_without_claiming_inventory_exit(rig, changes, reason):
    store, now = rig
    result = step(store, now, '1', **changes)
    assert result['state'] == 'EVENT' and reason in result['reasons']
    assert result['cancellation_status'] == 'REQUESTED_NOT_CONFIRMED'
    assert result['existing_inventory_unchanged'] and not result['passive_new_risk_research_allowed']
    assert result['source_shock_status'] == 'REQUIRES_EXACT_FRESH_SOURCE_CLOB_CERTIFICATION_AND_STRONGER_EV'


def test_caution_and_recovery_have_stricter_limits_and_hazard_resets_progress(rig):
    store, now = rig
    result = step(store, now, '1', model_disagreement=3)
    assert result['state'] == 'CAUTION'
    assert result['guard']['additional_ev_per_share'] > 0
    now[0] += 10
    assert step(store, now, '2')['stability']['count'] == 1
    now[0] += 10
    assert step(store, now, '3', routine_observation=True)['state'] == 'CAUTION'
    now[0] += 10
    assert step(store, now, '4')['stability']['count'] == 1


def test_policy_binding_or_source_identity_change_resets_recovery(rig):
    store, now = rig
    step(store, now, '1'); now[0] += 10
    step(store, now, '2'); now[0] += 10
    b, s = captures(store, now, '3', provider='other')
    changed = EventRiskEngine(store).step('state3', context=CONTEXT, policy=policy(), binding=BINDING,
                                        metrics=metrics(now[0]), book_ids=b, source_ids=s)['body']['details']
    assert changed['state'] == 'RECOVERY' and changed['stability']['count'] == 1
    now[0] += 10
    b, s = captures(store, now, '4', provider='other')
    changed = EventRiskEngine(store).step('state4', context=CONTEXT, policy=replace(policy(), policy_version='2'),
                                        binding=BINDING, metrics=metrics(now[0]), book_ids=b, source_ids=s)['body']['details']
    assert changed['stability']['count'] == 1


def test_stale_and_unknown_source_availability_fail_closed(rig):
    store, now = rig
    b, s = captures(store, now, 'old', observed=800.)
    result = EventRiskEngine(store).step('old', context=CONTEXT, policy=policy(), binding=BINDING,
                                       metrics=metrics(now[0]), book_ids=b, source_ids=s)['body']['details']
    assert result['state'] == 'EVENT' and 'BOOK_STALE_OR_UNKNOWN' in result['reasons']
    b, s = captures(store, now, 'unknown', historical=True)
    result = EventRiskEngine(store).step('unknown', context=CONTEXT, policy=policy(), binding=BINDING,
                                       metrics=metrics(now[0]), book_ids=b, source_ids=s)['body']['details']
    assert 'SOURCE_STALE_OR_UNKNOWN' in result['reasons']


def test_no_new_orders_reduce_only_quarantine_survive_restart_and_market_recovery(rig):
    store, now = rig
    safety = SafetyReductions(store)
    command = dict(scope='ACCOUNT', scope_id='account', action='REDUCE_ONLY', actor='fixture', reason='test')
    first = safety.apply('op1', **command)
    now[0] += 1
    assert SafetyReductions(store).apply('op1', **command) == first
    for scope, scope_id, action in [('CITY', 'city', 'NO_NEW_ORDERS'), ('STATION', 'station', 'QUARANTINE_STATION'),
                                  ('ACCOUNT', 'account', 'DISABLE_INVENTORY_OPERATIONS')]:
        safety.apply('op-'+action, scope=scope, scope_id=scope_id, action=action, actor='fixture', reason='test')
    for i in range(3):
        result = step(store, now, str(i)); now[0] += 10
    assert result['state'] == 'NORMAL' and not result['ordinary_new_risk_research_allowed']
    assert all(result['safety']['flags'].values())
    assert result['source_shock_status'] == 'GATED_BY_OPERATOR'
    with pytest.raises(EvidenceError, match='REPLAY_REQUEST_CONFLICT'):
        safety.apply('op1', **dict(command, action='NO_NEW_ORDERS'))
    with pytest.raises(EvidenceError, match='INVALID'):
        safety.apply('restore', **dict(command, action='RESTORE_NORMAL'))


def test_cancellation_only_does_not_change_positions_or_silently_halt(rig):
    store, _ = rig
    result = SafetyReductions(store).apply('cancel', scope='EVENT', scope_id='event', action='CANCEL_EVENT',
                                          actor='fixture', reason='test')['body']['details']
    assert result['existing_inventory_unchanged']
    assert not any(result['flags'].values())
    assert result['cancellation_status'] == 'REQUESTED_NOT_CONFIRMED'


def test_state_idempotency_changed_context_and_final_pin_revalidation(rig):
    store, now = rig
    b, s = captures(store, now, '1')
    engine = EventRiskEngine(store)
    kwargs = dict(context=CONTEXT, policy=policy(), binding=BINDING, metrics=metrics(now[0]), book_ids=b, source_ids=s)
    first = engine.step('one', **kwargs)
    now[0] += 1
    assert engine.step('one', **kwargs) == first
    assert engine.revalidate('one')['state'] == 'RECOVERY'
    with pytest.raises(EvidenceError, match='CONTEXT_CHANGED'):
        engine.step('other', **dict(kwargs, context=replace(CONTEXT, city_id='other')))
    SafetyReductions(store).apply('stop', scope='ACCOUNT', scope_id='account', action='CANCEL_AND_HALT',
                                  actor='fixture', reason='test')
    with pytest.raises(EvidenceError, match='STATE_CHANGED'):
        engine.revalidate('one')


def test_revalidation_expires_at_source_age_before_state_guard_timer(rig):
    store, now = rig
    b, s = captures(store, now, '1', observed=945.)
    engine = EventRiskEngine(store)
    result = engine.step('one', context=CONTEXT, policy=policy(), binding=BINDING, metrics=metrics(now[0]),
                         book_ids=b, source_ids=s)['body']['details']
    assert result['valid_until'] == 1005.
    now[0] = 1006.
    with pytest.raises(EvidenceError, match='EXPIRED'):
        engine.revalidate('one')


def test_concurrent_state_writer_is_detected_by_cas(rig, monkeypatch):
    store, now = rig
    b, s = captures(store, now, '1')
    original = store.audit
    def racing(record_id, **kwargs):
        if record_id == 'one':
            original('other', **kwargs)
        return original(record_id, **kwargs)
    monkeypatch.setattr(store, 'audit', racing)
    with pytest.raises(EvidenceError, match='AUDIT_STATE_CHANGED'):
        EventRiskEngine(store).step('one', context=CONTEXT, policy=policy(), binding=BINDING,
                                    metrics=metrics(now[0]), book_ids=b, source_ids=s)


def test_invalid_policy_or_metrics_do_not_create_authority(rig):
    with pytest.raises(EvidenceError, match='STRICTER'):
        replace(policy(), caution=policy().normal)
    with pytest.raises(EvidenceError, match='BOOLEAN'):
        replace(metrics(1000.), sources_healthy='yes')
    with pytest.raises(EvidenceError, match='SCOPE'):
        SafetyReductions(rig[0]).apply('one', scope='EVENT', scope_id='event', action='QUARANTINE_CITY',
                                       actor='fixture', reason='test')
