"""`settlement_window.py` and its opt-in `risk_inputs.py` wiring.

Every `DERIVED` result here is a conservative NEW-RISK observation-window
close, never settlement/resolution finality -- see both modules' own
docstrings. These tests never touch `paper_execution_health_promotion
.promote_settlement_timing` or `AUTHORIZED_SETTLEMENT_PROVIDERS`, which stay
untouched and DISABLED.
"""
import copy
from dataclasses import asdict, replace
from datetime import date, datetime, time, timedelta, timezone

import pytest
from zoneinfo import ZoneInfo

import test_v11_basket_coordinator as _basket_coordinator
import test_v11_strategy_pipeline as _strategy_pipeline
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.event_risk import EventRiskEngine
from polymarket_scanner.v11.risk_inputs import EventRiskInputs
from polymarket_scanner.v11.rules import RuleGuard
from polymarket_scanner.v11.settlement_window import (
    ACCEPTED_FINALITY_AND_DEADLINE_POLICY, ACCEPTED_OBSERVATION_POPULATION, BASIS,
    SettlementWindowPolicy, VERSION as SW_VERSION, derive_time_to_observation_close,
)
from test_v11_basket_coordinator import rig
from test_v11_certification_rules import observe_rule, setup
from test_v11_event_risk import BINDING, CONTEXT, captures as event_captures, metrics as event_metrics, policy as event_policy
from test_v11_model_artifacts import bundle
from test_v11_risk_inputs import books, components, evaluate
from test_v11_strategy_pipeline import factory
from test_weather_final_gpt6_exact_replays import _event


def _compact_description(*, target, family, unit):
    """The whitelisted "compact" NWS WRH-hourly template.

    `weather_only_contract_strict._supported_nws_rule_structure` only accepts
    a short, closed list of complete, exact recurring-contract grammars --
    appending a clause to `_event()`'s own (`public_template`) text fails its
    strict whole-text match. This is the other whitelisted template
    (`compact_template`), the same one `tests/test_production_weather.py`'s
    own `_hourly_event()` already uses; it is the one supported NWS grammar
    that actually binds `observation_population == 'WRH_HOURLY_DATA'`.
    """
    extreme = 'highest' if family == 'high' else 'lowest'
    unit_word = 'Fahrenheit' if unit == 'F' else 'Celsius'
    return (
        f"Observation date {target.strftime('%d %b')} '{target.year % 100:02d}, in whole degrees {unit_word}. "
        f'The market resolves using the {extreme} reading in the "Temp" column across all times on this day. '
        "On WRH select hourly data and show hourly data. "
        "If WRH is unavailable, use the Weather Underground Daily Observations table by 11:59 PM ET on the day following "
        "the observation date. If there is no data, the market resolves to the lowest bracket. Revisions are accepted "
        "until the first data point for the following date, whichever comes first, after which any alterations will not be considered."
    )


def _hourly_event(**kwargs):
    """A drop-in replacement for `_event()` that binds `WRH_HOURLY_DATA`.

    Accepts the exact same keyword arguments (`family`, `target`, `station`,
    `unit`, `eid`, `labels`), defaulted identically, so it can directly
    replace `_event` wherever that is called -- including inside
    `test_v11_strategy_pipeline.factory`'s own `build()` closure.
    """
    kwargs.setdefault('family', 'high')
    kwargs.setdefault('target', date(2026, 9, 14))
    kwargs.setdefault('unit', 'F')
    event = _event(**kwargs)
    event['description'] = _compact_description(target=kwargs['target'], family=kwargs['family'], unit=kwargs['unit'])
    return event


def _expected_close(target_date, tz_name):
    """Independent (test-side) reimplementation of the conservative close pick."""
    tz = ZoneInfo(tz_name)
    civil = datetime.combine(target_date+timedelta(days=1), time.min, tz)
    offset, dst = civil.utcoffset(), civil.dst()
    standard = civil.replace(tzinfo=timezone(offset-dst))
    return min(civil.timestamp(), standard.timestamp())


@pytest.fixture
def hourly_rig(request, factory, bundle, monkeypatch):
    """The exact `test_v11_basket_coordinator.rig` build, with an hourly rule.

    `factory`'s own `build()` closure looks up `_event` as a module global of
    `test_v11_strategy_pipeline` at call time, so patching that name before
    `rig`'s own body (which is what actually calls `factory(...)`) resolves
    it is enough -- no new store plumbing, only the input text `fingerprint_
    event` compiles changes.
    """
    monkeypatch.setattr(_strategy_pipeline, '_event', _hourly_event)
    return _basket_coordinator.rig.__wrapped__(request, factory, bundle)


# -- SettlementWindowPolicy validation -------------------------------------

def test_policy_requires_exact_version_and_positive_finite_age():
    with pytest.raises(EvidenceError) as exc:
        SettlementWindowPolicy('not-the-real-version', 10.)
    assert str(exc.value) == 'SETTLEMENT_WINDOW_POLICY_VERSION'
    for bad in (0., -1.):
        with pytest.raises(EvidenceError) as exc:
            SettlementWindowPolicy(SW_VERSION, bad)
        assert str(exc.value) == 'SETTLEMENT_WINDOW_POLICY_BOUND'
    for bad in (float('inf'), float('nan')):
        with pytest.raises(EvidenceError):
            SettlementWindowPolicy(SW_VERSION, bad)
    SettlementWindowPolicy(SW_VERSION, 1.)  # does not raise


# -- derive_time_to_observation_close: argument misuse raises -------------

def test_bad_argument_types_raise_evidence_error(setup):
    store, registry, scope, metadata, now = setup
    policy = SettlementWindowPolicy(SW_VERSION, 86400.)
    with pytest.raises(EvidenceError) as exc:
        derive_time_to_observation_close(store, event_id=123, rule_fingerprint='a'*64, at=now[0], policy=policy)
    assert str(exc.value) == 'SETTLEMENT_WINDOW_ARGUMENT_TYPE'
    with pytest.raises(EvidenceError) as exc:
        derive_time_to_observation_close(store, event_id='event', rule_fingerprint='a'*64, at=now[0], policy=object())
    assert str(exc.value) == 'SETTLEMENT_WINDOW_ARGUMENT_TYPE'


def test_at_after_store_clock_raises_evidence_error(setup):
    store, registry, scope, metadata, now = setup
    guard = RuleGuard(store)
    f = observe_rule(store, guard, _hourly_event(target=date(2026, 9, 14), station='KATL'), metadata, 'future-at')
    policy = SettlementWindowPolicy(SW_VERSION, 86400.)
    with pytest.raises(EvidenceError) as exc:
        derive_time_to_observation_close(store, event_id=f.payload['event_id'], rule_fingerprint=f.sha256,
                                          at=now[0]+1., policy=policy)
    assert str(exc.value) == 'SETTLEMENT_WINDOW_AT_IN_FUTURE'


# -- rule-state data problems stay UNKNOWN, never fabricated ---------------

def test_unsupported_rule_family_stays_unknown(setup):
    store, registry, scope, metadata, now = setup
    guard = RuleGuard(store)
    event = _event(target=date(2026, 9, 14), station='KATL')
    f = observe_rule(store, guard, event, metadata, 'plain')
    assert f.payload['observation_population'] != ACCEPTED_OBSERVATION_POPULATION
    policy = SettlementWindowPolicy(SW_VERSION, 86400.)
    result = derive_time_to_observation_close(store, event_id=f.payload['event_id'], rule_fingerprint=f.sha256,
                                               at=now[0], policy=policy)
    assert result.status == 'UNKNOWN'
    assert result.reason == 'SETTLEMENT_WINDOW_RULE_FAMILY_UNSUPPORTED'
    assert result.seconds is None and result.close_utc is None
    assert result.evidence_ids and result.basis == BASIS


def test_changed_rule_is_quarantined_and_stays_unknown(setup):
    store, registry, scope, metadata, now = setup
    guard = RuleGuard(store)
    event = _hourly_event(target=date(2026, 9, 14), station='KATL')
    f = observe_rule(store, guard, event, metadata, 'base')
    drifted = copy.deepcopy(event)
    drifted['markets'][0]['clobTokenIds'] = '["different-yes","different-no"]'
    observe_rule(store, guard, drifted, metadata, 'drift')
    policy = SettlementWindowPolicy(SW_VERSION, 86400.)
    result = derive_time_to_observation_close(store, event_id=f.payload['event_id'], rule_fingerprint=f.sha256,
                                               at=now[0], policy=policy)
    assert result.status == 'UNKNOWN'
    assert result.reason == 'SETTLEMENT_WINDOW_RULE_DRIFT_QUARANTINED'
    assert result.seconds is None and result.close_utc is None


def test_invalidated_rule_is_quarantined_and_stays_unknown(setup):
    store, registry, scope, metadata, now = setup
    guard = RuleGuard(store)
    event = _hourly_event(target=date(2026, 9, 14), station='KATL')
    f = observe_rule(store, guard, event, metadata, 'revoked')
    guard.invalidate('reject', event_id=f.payload['event_id'], raw_evidence_id='revoked:raw', reason='TEST_REJECTED')
    policy = SettlementWindowPolicy(SW_VERSION, 86400.)
    result = derive_time_to_observation_close(store, event_id=f.payload['event_id'], rule_fingerprint=f.sha256,
                                               at=now[0], policy=policy)
    assert result.status == 'UNKNOWN'
    assert result.reason == 'SETTLEMENT_WINDOW_RULE_DRIFT_QUARANTINED'


def test_stale_rule_stays_unknown(setup):
    store, registry, scope, metadata, now = setup
    guard = RuleGuard(store)
    event = _hourly_event(target=date(2026, 9, 14), station='KATL')
    f = observe_rule(store, guard, event, metadata, 'stale-base')
    now[0] += 1000.
    policy = SettlementWindowPolicy(SW_VERSION, 10.)
    result = derive_time_to_observation_close(store, event_id=f.payload['event_id'], rule_fingerprint=f.sha256,
                                               at=now[0], policy=policy)
    assert result.status == 'UNKNOWN'
    assert result.reason == 'SETTLEMENT_WINDOW_RULE_EVIDENCE_STALE'


# -- the accepted rule family: correct, DST-safe close computation ---------

@pytest.mark.parametrize('target_date', [
    date(2026, 10, 31),  # US fall-back Sunday is 2026-11-01; local midnight that day is still EDT.
    date(2026, 3, 8),    # US spring-forward Sunday is 2026-03-08; the next local midnight is EDT.
])
def test_dst_boundary_close_uses_the_earlier_of_civil_and_standard_offset_midnight(setup, target_date):
    store, registry, scope, metadata, now = setup
    guard = RuleGuard(store)
    event = _hourly_event(target=target_date, station='KATL')
    f = observe_rule(store, guard, event, metadata, 'dst')
    policy = SettlementWindowPolicy(SW_VERSION, 86400.)
    result = derive_time_to_observation_close(store, event_id=f.payload['event_id'], rule_fingerprint=f.sha256,
                                               at=now[0], policy=policy)
    assert result.status == 'DERIVED', result
    expected = _expected_close(target_date, metadata.timezone)
    assert result.close_utc == pytest.approx(expected)
    assert result.seconds == pytest.approx(expected-now[0])
    assert result.basis == BASIS
    assert result.evidence_ids == (store.latest(kind='RULE_STATE', event_id=f.payload['event_id'])['id'],)


def test_close_in_the_past_yields_nonpositive_seconds_and_event_engine_sees_closed(setup):
    store, registry, scope, metadata, now = setup
    guard = RuleGuard(store)
    target = date(2026, 9, 14)
    event = _hourly_event(target=target, station='KATL')
    f = observe_rule(store, guard, event, metadata, 'past-close')
    close = _expected_close(target, metadata.timezone)
    now[0] = close+3600.
    policy = SettlementWindowPolicy(SW_VERSION, 10.**10)
    result = derive_time_to_observation_close(store, event_id=f.payload['event_id'], rule_fingerprint=f.sha256,
                                               at=now[0], policy=policy)
    assert result.status == 'DERIVED' and result.seconds <= 0
    book_ids, source_ids = event_captures(store, now, 'past-close')
    state = EventRiskEngine(store).step('past-close-state', context=CONTEXT, policy=event_policy(), binding=BINDING,
        metrics=replace(event_metrics(now[0]), time_to_settlement_seconds=result.seconds),
        book_ids=book_ids, source_ids=source_ids)['body']['details']
    assert state['state'] == 'EVENT'
    assert 'SETTLEMENT_WINDOW_UNKNOWN_OR_CLOSED' in state['reasons']


# -- risk_inputs.py wiring: default stays byte-identical -------------------

def test_settlement_window_default_keeps_existing_config_byte_identical(rig, monkeypatch):
    rt, worker, _ = components(rig, monkeypatch)
    # The exact pre-change `description()` shape: no `settlement_window_policy`
    # (or `execution_health_policy`) key at all when neither is configured.
    pre_change_description = dict(assembler=worker.assembler.config, account=rt.coordinator.policy_sha,
                                   policy=asdict(worker.policy), targets=[asdict(t) for t in worker.targets])
    assert digest(pre_change_description) == worker.config
    assert EventRiskInputs(worker.assembler, rt.coordinator, worker.policy, None, None).config == worker.config
    configured = EventRiskInputs(worker.assembler, rt.coordinator, worker.policy, None,
                                  SettlementWindowPolicy(SW_VERSION, 3600.))
    assert configured.config != worker.config
    with pytest.raises(EvidenceError) as exc:
        EventRiskInputs(worker.assembler, rt.coordinator, worker.policy, None, object())
    assert str(exc.value) == 'RISK_INPUT_COMPONENT_SCOPE'
    d, _ = evaluate(rig, rt, worker, books(rig, 'sw-default'))
    assert 'settlement_window' not in d
    assert d['unknown_inputs'] == ['SETTLEMENT_TIMING', 'OWN_EXECUTION_ADVERSE_FILLS', 'OWN_EXECUTION_MARKOUT']
    assert d['metrics']['time_to_settlement_seconds'] is None


# -- end-to-end: a DERIVED window plus a promoted execution health ---------
# removes SETTLEMENT_WINDOW_UNKNOWN_OR_CLOSED (other genuine gates may remain).

def test_settlement_window_end_to_end_removes_the_unknown_or_closed_reason(hourly_rig, monkeypatch):
    from test_v11_paper_risk_observation import policy as observation_policy, sequenced_fill
    rig_ = hourly_rig
    payload = rig_['rule'].payload
    assert payload['observation_population'] == ACCEPTED_OBSERVATION_POPULATION
    assert payload['finality_and_deadline_policy'] == ACCEPTED_FINALITY_AND_DEADLINE_POLICY
    sequenced_fill(rig_, monkeypatch)
    rt, worker, _ = components(rig_, monkeypatch)
    worker = EventRiskInputs(worker.assembler, rt.coordinator, worker.policy, observation_policy(),
                              SettlementWindowPolicy(SW_VERSION, 3600.*24*30))
    rt.health.sample('settlement-health')
    d, state = evaluate(rig_, rt, worker, books(rig_, 'settlement'))
    assert d['settlement_window']['status'] == 'DERIVED', d['settlement_window']
    assert d['settlement_window']['basis'] == BASIS
    assert d['metrics']['time_to_settlement_seconds'] is not None
    assert d['metrics']['time_to_settlement_seconds'] > 0
    assert 'SETTLEMENT_TIMING' not in d['unknown_inputs']
    assert d['execution_health'] == {'status': 'PROMOTED', 'reason': None}
    assert 'SETTLEMENT_WINDOW_UNKNOWN_OR_CLOSED' not in state['reasons']
    assert not d['settlement_finality'] and state['financial_authority'] is False
