from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11 import model_registry, strategy_pipeline as pipeline
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.event_risk import EventRiskEngine, SafetyReductions
from polymarket_scanner.v11.paper_coordinator import Proposal
from polymarket_scanner.v11.pws_lead import REPORT_VERSION
from polymarket_scanner.v11.scenario_risk import Attribution
from polymarket_scanner.v11.source_release import SourceRelease
from polymarket_scanner.v11.strategy_admission import StrategyAdmission, SourceLease
from test_v11_certification_rules import setup, approve_fixture
from test_v11_event_risk import policy, metrics
from test_v11_model_artifacts import bundle
from test_v11_model_governance import authority, promote
from test_v11_pws_admission import coordinator
from test_v11_strategy_pipeline import factory


@pytest.fixture
def release_factory(factory, setup, bundle, monkeypatch):
    def build(strategy='SOURCE_SHOCK', *, revision=False, hazard=None, book_before=False,
              stale_model=False, duplicate=False, older=False):
        rig = factory('SAME_DAY_LATE_LOCK'); store, registry, _, metadata, now = setup
        r = rig['rule']; event = r.payload['event_id']
        original = store.get('official2')['body']
        p = dict(original['payload'], version=REPORT_VERSION, source_role='EXACT_CONTRACT_OBSERVATION',
                 reported_whole_degree=72)
        previous = store.capture('previous-exact', event_id=event, kind='OFFICIAL_OBSERVATION',
                     provider=original['provider'], source_identity=original['source_identity'], revision='previous',
                     observed_at=now[0], payload=p, evidence_class='SYNTHETIC')
        store.audit('schedule', event_id=event, kind='SOURCE_SCHEDULE', details=dict(
            version='alpha_v11_release_schedule_v1', station='KATL', expected_release_at=now[0]+1,
            reevaluate_at=now[0]+1, valid_until=now[0]+100))
        now[0] += 1
        payload = deepcopy(p)
        if not duplicate:
            payload['reported_whole_degree'] = 73
            payload['exact_extreme']['whole_degree_value'] = 73
        observed = previous['body']['observed_at'] if revision or duplicate else now[0]
        if older:
            observed = previous['body']['observed_at']-1
        current = store.capture('received-release', event_id=event, kind='OFFICIAL_OBSERVATION',
                    provider=original['provider'], source_identity=original['source_identity'], revision='current',
                    observed_at=observed, payload=payload, evidence_class='SYNTHETIC')
        old_model = store.get('model2'); model_payload = deepcopy(old_model['body']['payload'])
        parent = previous if stale_model else current
        model_payload['dependencies'] = [dict(id=parent['id'], sha256=parent['sha256'])]
        model = store.capture('recomputed-model', event_id=event, kind='MODEL', provider='fixture', source_identity='model-1',
                    revision='post-release', observed_at=now[0], issued_at=now[0]-30,
                    payload=model_payload, evidence_class='SYNTHETIC')
        coverage = deepcopy(store.get('coverage')['body']['payload'])
        coverage.update(as_of=now[0], observation_id=current['id'], observation_sha256=current['sha256'],
                        model_evidence_sha256=[model['sha256']])
        coverage['accepted_intervals'][-1][1] = coverage['unresolved_intervals'][0][0] = now[0]
        store.capture('release-coverage', event_id=event, kind='FEATURES', provider='fixture', source_identity='coverage',
                      revision='release', payload=coverage, evidence_class='SYNTHETIC')
        book = store.get('book2')
        if not book_before:
            book = store.capture('post-release-book', event_id=event, kind='BOOK', provider=book['body']['provider'],
                   source_identity=book['body']['source_identity'], revision='after-release', observed_at=now[0],
                   payload=book['body']['payload'], evidence_class='SYNTHETIC')
        flags = {'source_revision':True} if revision or duplicate else {'special_observation':True}
        flags.update(hazard or {})
        EventRiskEngine(store).step('release-event', context=rig['context'], policy=policy(), binding=rig['binding'],
                    metrics=replace(metrics(now[0]), **flags), book_ids=(book['id'],),
                    source_ids=(current['id'], model['id']))
        scope = replace(rig['scope'], strategy=strategy)
        approve_fixture(monkeypatch, (store, registry, scope, metadata, now), stage='PAPER', fingerprint=r.sha256, prefix='release:')
        state = [promote(bundle, authority.empty_state(scope.key, 'V11_PAPER'))]
        monkeypatch.setattr(model_registry, 'protected_state', lambda **kw:dict(state=state[0], sha256=digest(state[0])))
        kw = dict(rig['admission_kw'], scope=scope, source_leases=(SourceLease(model['id'], 'MODEL', 120.),
                  SourceLease(current['id'], 'OFFICIAL', 120.), SourceLease('release-coverage', 'FEATURES', 120.)))
        StrategyAdmission(store).pin('release-admission', **kw)
        pin_kw = dict(admission_id='release-admission', event_state_id='release-event',
                      previous_official_id=previous['id'], current_official_id=current['id'],
                      book_id=book['id'], schedule_id='schedule')
        request = replace(rig['request'], admission_id='release-admission', event_state_id='release-event',
                          model_input_ids=(model['id'],), observed_input_id=current['id'],
                          coverage_input_id='release-coverage', book_id=book['id'], source_release_id='release-pin')
        rig.update(request=request, pin_kw=pin_kw, scope=scope, model_state=state)
        return rig
    return build


def pin(rig, key='release-pin', **changes):
    return SourceRelease(rig['store']).pin(key, **{**rig['pin_kw'], **changes})['body']['details']['assessment']


def evaluate(rig):
    return pipeline.TemperatureStrategies(rig['store']).evaluate('evaluation', rig['request'])['body']['details']


def proposal(rig, *, ev='.2', units='2'):
    result = evaluate(rig)
    assert result['outcome'] == 'REJECT'
    value = deepcopy(rig['store'].get('evaluation:valuation')['body']['details'])
    value.update(outcome='ACCEPT_RESEARCH', conservative_ev_per_share=ev,
                 conservative_ev_total=str(Decimal(ev)*Decimal(units)), units=units,
                 synthetic_downstream_test_fixture=True)
    rig['store'].audit('positive-fixture', event_id=rig['context'].event_id, kind='MEASUREMENT', details=value)
    return Proposal('proposal', 'release-thesis', rig['context'], rig['rule'], 'positive-fixture', 'release-event',
                    (Attribution(rig['scope'].strategy, '1'),), rig['now'][0]+10, units,
                    ('release-admission',), source_release_id='release-pin')


@pytest.mark.parametrize('strategy', ['SOURCE_SHOCK', 'RELEASE_OPPORTUNITY'])
def test_received_official_release_feeds_distinct_attributed_payout_sleeve(release_factory, strategy):
    rig = release_factory(strategy); d = pin(rig)
    assert d['change_type'] == 'NEW_OFFICIAL_OBSERVATION' and d['reported_temperature_change'] == 1
    assert d['directional_event_data_eligible'] and d['event_state'] == 'EVENT'
    assert not d['passive_new_risk_permitted'] and not d['settlement_finality'] and not d['financial_authority']
    assert d['schedule']['proves_actual_release'] is False
    result = evaluate(rig)
    assert result['strategy'] == strategy and result['outcome'] == 'REJECT'
    assert result['reason'] == 'CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD' and result['proposal'] is None


def test_same_observation_revision_is_not_mislabeled_as_a_new_temperature_report(release_factory):
    rig = release_factory(revision=True)
    assert pin(rig)['change_type'] == 'OFFICIAL_REVISION'


@pytest.mark.parametrize('changes,reason', [
    ({'book_before':True}, 'BOOK_OBSERVED_AFTER_RECEIPT'),
    ({'stale_model':True}, 'PAYOUT_MODEL_MUST_INCLUDE_RECEIVED'),
    ({'duplicate':True}, 'DUPLICATE_RECEIPT'),
    ({'older':True}, 'LATE_OLDER_REPORT'),
    ({'hazard':{'clock_healthy':False}}, 'HEALTH_CANNOT_BYPASS'),
    ({'hazard':{'websocket_synchronized':False}}, 'HEALTH_CANNOT_BYPASS'),
    ({'hazard':{'depth_loss':.8}}, 'HAZARD_CANNOT_BYPASS'),
    ({'hazard':{'loss_utilization':1}}, 'HAZARD_CANNOT_BYPASS'),
    ({'hazard':{'adverse_fills':4}}, 'EXECUTION_OR_SETTLEMENT_HEALTH'),
])
def test_release_path_cannot_bypass_freshness_recomputation_or_health(release_factory, changes, reason):
    rig = release_factory(**changes)
    with pytest.raises(EvidenceError, match=reason):
        pin(rig)


def test_schedule_alone_cannot_stand_in_for_a_received_official_report(release_factory):
    rig = release_factory()
    with pytest.raises(EvidenceError, match='EXACT_OFFICIAL_REPORT'):
        pin(rig, current_official_id='schedule')


def test_source_strategy_without_received_release_pin_stays_gated(release_factory):
    rig = release_factory(); rig['request'] = replace(rig['request'], source_release_id=None)
    assert evaluate(rig)['reason'] == 'RECEIVED_SOURCE_RELEASE_PIN_REQUIRED'


def test_directional_event_uses_stronger_common_size_and_ev_limits(release_factory):
    rig = release_factory(); pin(rig)
    p = proposal(rig, units='21')  # Fixture ceiling 100 x EVENT multiplier .2.
    d = coordinator(rig).coordinate('size-gate', (p,))['body']['details']
    assert not d['reserved_intent_ids'] and d['results'][0]['reason'] == 'EVENT_STATE_SIZE_LIMIT'
    # A new synthetic EV fixture keeps the actual source and model admissions.
    v = deepcopy(rig['store'].get('positive-fixture')['body']['details'])
    v.update(units='2', conservative_ev_per_share='.02', conservative_ev_total='.04')
    rig['store'].audit('weak-ev', event_id=rig['context'].event_id, kind='MEASUREMENT', details=v)
    d = coordinator(rig).coordinate('ev-gate', (replace(p, proposal_id='weak', desired_total_units='2', valuation_id='weak-ev'),))['body']['details']
    assert not d['reserved_intent_ids'] and d['results'][0]['reason'] == 'STATE_ADJUSTED_EV_NOT_ABOVE_THRESHOLD'


def test_exact_directional_paper_boundary_can_reserve_but_does_not_send_an_order(release_factory):
    rig = release_factory(); pin(rig); p = proposal(rig); c = coordinator(rig)
    d = c.coordinate('batch', (p,))['body']['details']
    assert d['reserved_intent_ids'] == ['proposal']
    result = c.transition('submit', intent_id='proposal', status='SUBMITTING')['body']['details']
    assert not result['real_submission_performed'] and not result['reservation_released']
    assert Decimal(c.snapshot()['reserved_cash']) > 0


def test_operator_reduction_during_reserved_release_preserves_cash_hold(release_factory):
    rig = release_factory(); pin(rig); p = proposal(rig); c = coordinator(rig); c.coordinate('batch', (p,))
    reserved = c.snapshot()['reserved_cash']
    SafetyReductions(rig['store']).apply('stop', scope='EVENT', scope_id=rig['context'].event_id,
                         action='NO_NEW_ORDERS', actor='fixture', reason='TEST')
    with pytest.raises(EvidenceError, match='EVENT_OR_OPERATOR_STATE_CHANGED'):
        c.transition('submit', intent_id='proposal', status='SUBMITTING')
    assert c.snapshot()['reserved_cash'] == reserved


def test_expired_release_pin_is_not_refreshed_by_replay(release_factory):
    rig = release_factory(); old = pin(rig); rig['now'][0] += 6
    assert pin(rig) == old
    assert evaluate(rig)['outcome'] == 'GATED'


def test_new_source_racing_release_pin_append_is_rejected_atomically(release_factory, monkeypatch):
    rig = release_factory(); store = rig['store']; original = store.audit
    def race(key, **kw):
        if key == 'release-pin':
            row = store.get('received-release'); b = row['body']
            store.capture('racing-release', event_id=row['event_id'], kind=row['kind'], provider=b['provider'],
                source_identity=b['source_identity'], revision='racer', observed_at=rig['now'][0],
                payload=b['payload'], evidence_class='SYNTHETIC')
        return original(key, **kw)
    monkeypatch.setattr(store, 'audit', race)
    with pytest.raises(EvidenceError, match='AUDIT_GUARDED_STATE_CHANGED'):
        pin(rig)


def test_new_official_after_reservation_invalidates_release_and_retains_hold(release_factory):
    rig = release_factory(); pin(rig); p = proposal(rig); c = coordinator(rig); c.coordinate('batch', (p,))
    before = c.snapshot()['reserved_cash']; row = rig['store'].get('received-release'); b = row['body']
    rig['store'].capture('next-release', event_id=row['event_id'], kind=row['kind'], provider=b['provider'],
        source_identity=b['source_identity'], revision='next', observed_at=rig['now'][0],
        payload=b['payload'], evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError, match='NEW_OFFICIAL'):
        c.transition('submit', intent_id='proposal', status='SUBMITTING')
    assert c.snapshot()['reserved_cash'] == before


def test_release_payout_model_demotion_cannot_keep_old_event_permission(release_factory):
    rig = release_factory(); pin(rig); p = proposal(rig); c = coordinator(rig); c.coordinate('batch', (p,))
    state = rig['model_state'][0]
    rig['model_state'][0] = authority.transition(state, action='DEMOTE', expected_state_sha256=digest(state),
                                                 now=21., reason='TEST', size_multiplier=.5)
    before = c.snapshot()['reserved_cash']
    with pytest.raises(EvidenceError, match='MODEL_MANUAL_REVIEW'):
        c.transition('submit', intent_id='proposal', status='SUBMITTING')
    assert c.snapshot()['reserved_cash'] == before
