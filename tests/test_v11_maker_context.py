from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11 import certification, maker_context as context_module, model_registry
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.event_risk import SafetyReductions
from polymarket_scanner.v11.maker_context import NOTICE_VERSION
from polymarket_scanner.v11.maker_research import MakerResearchPolicy, ResearchQuote
from polymarket_scanner.v11.microstructure import MakerMicrostructure, MicrostructurePolicy
from polymarket_scanner.v11.probability import FINAL_EXTREME, NEXT_OBSERVATION, target_identity
from polymarket_scanner.v11.strategy_admission import StrategyAdmission
from polymarket_scanner.v11.valuation import contract_target
from test_v11_certification_rules import setup, approve_fixture
from test_v11_maker_research import rig, maker, propose, book, features, account_inventory_fixture
from test_v11_model_artifacts import bundle
from test_v11_model_governance import authority, promote
from test_v11_pws_admission import coordinator
from test_v11_strategy_pipeline import factory


def measure(rig, key='context', **changes):
    return maker(rig).context(key, **dict({'quote_id':'quote', 'model_input_ids':('model2',)}, **changes))['body']['details']


def notice(rig, key='notice', *, evidence_class='SYNTHETIC', issued=None, **changes):
    p = dict(version=NOTICE_VERSION, station=rig['context'].station_id, rule_fingerprint=rig['rule'].sha256,
             release_kind='OFFICIAL_OBSERVATION', expected_release_at=rig['now'][0]+5, valid_until=rig['now'][0]+100)
    p.update(changes)
    return rig['store'].capture(key, event_id=rig['context'].event_id, kind='FEATURES', provider='fixture-notice',
                source_identity='weather-schedule', revision=key,
                issued_at=rig['now'][0] if issued is None else issued, payload=p, evidence_class=evidence_class)


def test_protected_final_payout_distance_retains_uncalibrated_bounds_without_economic_authority(rig):
    propose(rig); head = maker(rig)._head(); d = measure(rig)
    assert d['outcome'] == 'MEASURED_RESEARCH_CONTEXT', d['reason']
    fair = d['fair_value']
    assert fair['target'] == FINAL_EXTREME
    assert fair['lower'] == '0.0' and fair['upper'] == '1.0' and fair['calibration_status'] == 'UNCALIBRATED'
    assert Decimal(fair['quote_minus_point']) == Decimal('.1')-Decimal(fair['point'])
    assert list(map(Decimal, fair['quote_minus_probability_interval'])) == [Decimal('-.9'), Decimal('.1')]
    assert fair['artifact_refs'] and fair['model_epoch'] == 1 and fair['prediction_sha256']
    assert d['prediction']['model_inputs'][0]['evidence_sha256'] == rig['store'].get('model2')['sha256']
    assert d['release_context']['status'] == 'UNKNOWN'
    assert d['actual_trading_pnl'] is d['actual_settled_payout'] is d['executable_early_exit_value'] is None
    assert d['fill_probability'] is d['conservative_maker_net_ev'] is d['queue_position'] is None
    assert not d['financial_authority'] and not d['orders_submitted'] and not d['account_ledger_mutated']
    assert maker(rig)._head() == head and rig['coordinator']._head() is None


def test_no_contract_uses_complement_interval_and_exact_token(rig):
    target = contract_target(rig['rule'], rig['quote'].market_id, 'NO')
    book(rig, 'no-book', **target)
    MakerMicrostructure(rig['store']).evaluate('no-micro', rule=rig['rule'], market_id=target['market_id'], side='NO',
                    book_ids=('no-book',), trade_ids=(), policy=rig['micro_policy'])
    assert propose(rig, side='NO', microstructure_id='no-micro')['outcome'] == 'OBSERVING_RESEARCH_QUOTE'
    d = measure(rig); fair = d['fair_value']
    yes = next(b['point'] for b in d['prediction']['buckets'] if b['market_id'] == target['market_id'])
    assert float(fair['point']) == pytest.approx(1-yes) and fair['contract'] == target
    assert Decimal(fair['lower']) == 0 and Decimal(fair['upper']) == 1


@pytest.mark.parametrize('change', [dict(model_input_ids=()), dict(model_input_ids=('model2','model2')),
                         dict(model_input_ids=['model2']), dict(maximum_notice_age_seconds=86401)])
def test_input_bounds_do_not_start_evaluation(rig, change):
    with pytest.raises(EvidenceError, match='INPUT_BOUND'):
        measure(rig, **change)
    with pytest.raises(EvidenceError, match='EVIDENCE_MISSING'):
        rig['store'].get('context:start')


def test_unleased_models_cannot_supply_fair_value(rig):
    propose(rig); d = measure(rig, model_input_ids=('model0',))
    assert d['outcome'] == 'GATED' and d['reason'] == 'ALL_INFERENCE_MODELS_REQUIRE_ADMISSION_LEASE'
    assert d['fair_value'] is None


def test_next_observation_prediction_cannot_be_relabelled_as_payout(rig, monkeypatch):
    propose(rig); original = context_module._model_inputs
    def wrong_target(*args, **kw):
        return tuple(replace(c, target_sha256=target_identity(rig['rule'], NEXT_OBSERVATION))
                     for c in original(*args, **kw))
    monkeypatch.setattr(context_module, '_model_inputs', wrong_target)
    d = measure(rig)
    assert d['outcome'] == 'GATED' and d['fair_value'] is None
    assert d['executable_early_exit_value'] is None


@pytest.mark.parametrize('change', ['book', 'model', 'retired', 'expired'])
def test_changed_quote_or_source_is_not_current_context(rig, change):
    propose(rig)
    if change == 'book':
        book(rig, 'new-book')
    elif change == 'model':
        b = rig['store'].get('model2')['body']
        rig['store'].capture('model-new', event_id=rig['context'].event_id, kind='MODEL', provider=b['provider'],
                    source_identity=b['source_identity'], revision='new', issued_at=rig['now'][0],
                    payload=b['payload'], evidence_class='SYNTHETIC')
    elif change == 'retired':
        maker(rig).retire('withdraw', quote_id='quote', reason='END_RESEARCH')
    else:
        rig['now'][0] += 21
    d = measure(rig)
    assert d['outcome'] == 'GATED' and d['fair_value'] is None and d['valid_until'] is None


def test_model_epoch_change_during_inference_drops_all_context(rig, monkeypatch):
    propose(rig); original = context_module.predict_with_bundle
    def changed(*args, **kw):
        result = original(*args, **kw)
        state = rig['model_state'][0]
        rig['model_state'][0] = authority.transition(state, action='DEMOTE', expected_state_sha256=digest(state),
                        now=rig['now'][0], size_multiplier=.5, reason='synthetic-change')
        return result
    monkeypatch.setattr(context_module, 'predict_with_bundle', changed)
    d = measure(rig)
    assert d['outcome'] == 'GATED' and d['fair_value'] is None


def test_actual_account_inventory_is_shared_without_mutation(rig):
    account_inventory_fixture(rig); propose(rig); before = rig['coordinator']._head()
    d = measure(rig)
    assert d['outcome'] == 'MEASURED_RESEARCH_CONTEXT'
    assert d['projected_risk']['inventory_source'] == 'COMMON_PAPER_ACCOUNT'
    assert rig['coordinator']._head() == before


@pytest.mark.parametrize('race', ['book', 'account', 'retire', 'notice', 'operator'])
def test_atomic_changes_during_context_commit_require_recomputation(rig, monkeypatch, race):
    notice(rig); propose(rig); original = rig['store'].audit; once = [False]
    def changed(key, **kw):
        if key == 'context' and not once[0]:
            once[0] = True
            if race == 'book': book(rig, 'race-book')
            elif race == 'account': account_inventory_fixture(rig)
            elif race == 'notice': notice(rig, 'notice-revised')
            elif race == 'operator': SafetyReductions(rig['store']).apply('halt', scope='ACCOUNT', scope_id='account',
                                        action='CANCEL_AND_HALT', actor='fixture', reason='fixture')
            else: maker(rig).retire('race-retirement', quote_id='quote', reason='END_RESEARCH')
        return original(key, **kw)
    monkeypatch.setattr(rig['store'], 'audit', changed)
    d = measure(rig, release_notice_id='notice')
    assert d['outcome'] == 'GATED' and d['reason'] == 'AUDIT_GUARDED_STATE_CHANGED'
    assert d['fair_value'] is None and d['release_context']['status'] == 'UNKNOWN'


def test_received_schedule_has_signed_countdown_but_never_confirms_release(rig):
    notice(rig); propose(rig)
    d = measure(rig, release_notice_id='notice'); schedule = d['release_context']
    assert schedule['status'] == 'EXPECTED' and schedule['seconds_to_expected_release'] == 5.
    assert schedule['notice_sha256'] == rig['store'].get('notice')['sha256']
    assert not schedule['actual_release_confirmed'] and schedule['actual_release_at'] is None
    rig['now'][0] += 6
    overdue = measure(rig, 'overdue', release_notice_id='notice')['release_context']
    assert overdue['status'] == 'EXPECTED_TIME_PASSED_UNCONFIRMED'
    assert overdue['seconds_to_expected_release'] == -1 and overdue['next_observation_value'] is None


@pytest.mark.parametrize('change', ['revised', 'expired', 'stale', 'unknown', 'wrong_rule', 'wrong_kind', 'missing'])
def test_invalid_release_notice_is_unknown_without_inventing_zero_risk(rig, change):
    kw = {}
    if change == 'expired': kw.update(expected_release_at=rig['now'][0], valid_until=rig['now'][0]+.5)
    if change == 'stale': kw['issued'] = rig['now'][0]-301
    if change == 'unknown': kw['evidence_class'] = 'HISTORICAL_AVAILABILITY_UNKNOWN'
    if change == 'wrong_rule': kw['rule_fingerprint'] = '0'*64
    if change == 'wrong_kind': kw['release_kind'] = 'PAYOUT_CONFIRMED'
    notice(rig, **kw)
    if change == 'revised': notice(rig, 'revised')
    propose(rig)
    if change == 'expired': rig['now'][0] += 1
    d = measure(rig, release_notice_id='missing' if change == 'missing' else 'notice')
    assert d['outcome'] == 'MEASURED_RESEARCH_CONTEXT', d['reason']
    assert d['release_context']['status'] == 'UNKNOWN'
    assert d['release_context']['seconds_to_expected_release'] is None
    assert not d['release_context']['actual_release_confirmed']


def test_context_replay_preserves_original_cutoff_and_unknown_notice(rig):
    propose(rig); first = measure(rig); rig['now'][0] += 500
    assert measure(rig) == first
    with pytest.raises(EvidenceError, match='REPLAY_CONFLICT'):
        measure(rig, release_notice_id='new')


def test_distance_uses_reproduced_sorted_best_levels_and_shortest_feature_expiry(rig):
    book(rig, 'unsorted', bids=[dict(price='.05', size='1'), dict(price='.11', size='3')],
                         asks=[dict(price='.3', size='2'), dict(price='.19', size='2')])
    features(rig, 'unsorted', 'sorted-feature')
    propose(rig, microstructure_id='sorted-feature')
    d = measure(rig); fair = d['fair_value']
    assert Decimal(fair['midpoint_minus_point']) == Decimal('.15')-Decimal(fair['point'])
    assert d['valid_until'] == rig['now'][0]+10


def test_feature_expiry_during_inference_cannot_publish_stale_context(rig, monkeypatch):
    propose(rig); original = context_module.predict_with_bundle
    def delayed(*args, **kw):
        result = original(*args, **kw); rig['now'][0] += 11
        return result
    monkeypatch.setattr(context_module, 'predict_with_bundle', delayed)
    d = measure(rig)
    assert d['outcome'] == 'GATED' and d['reason'] == 'MAKER_CONTEXT_CHANGED_OR_EXPIRED_DURING_INFERENCE'
    assert d['fair_value'] is None


@pytest.mark.parametrize('after', ['unchanged', 'retired', 'late_notice'])
def test_interruption_preserves_cutoff_and_does_not_use_future_notice(rig, monkeypatch, after):
    propose(rig); original = context_module.predict_with_bundle
    def interrupted(*args, **kw):
        raise RuntimeError('SIMULATED_PROCESS_INTERRUPTION')
    monkeypatch.setattr(context_module, 'predict_with_bundle', interrupted)
    kwargs = dict(release_notice_id='notice') if after == 'late_notice' else {}
    with pytest.raises(RuntimeError, match='SIMULATED_PROCESS_INTERRUPTION'):
        measure(rig, **kwargs)
    cutoff = rig['store'].get('context:start')['body']['recorded_at']; rig['now'][0] += 1
    monkeypatch.setattr(context_module, 'predict_with_bundle', original)
    if after == 'retired': maker(rig).retire('retire', quote_id='quote', reason='END_RESEARCH')
    if after == 'late_notice': notice(rig)
    d = measure(rig, **kwargs)
    assert d['as_of'] == cutoff
    assert d['outcome'] == ('GATED' if after == 'retired' else 'MEASURED_RESEARCH_CONTEXT')
    if after == 'late_notice':
        assert d['release_context']['status'] == 'UNKNOWN'
        assert d['release_context']['reason'] == 'STRATEGY_INPUT_NOT_CAUSAL_OR_SCOPED'


def test_started_record_id_cannot_be_returned_as_completed_context(rig):
    propose(rig); measure(rig)
    with pytest.raises(EvidenceError, match='REPLAY_CONFLICT'):
        measure(rig, 'context:start')


@pytest.fixture
def same_day(factory, setup, bundle, monkeypatch):
    r = factory('SAME_DAY_LATE_LOCK')
    store, registry, _, metadata, now = setup
    payout_scope = r['scope']; maker_scope = replace(payout_scope, strategy='MAKER_RESEARCH')
    reviews = certification.protected_reviews()['reviews']
    m = approve_fixture(monkeypatch, (store, registry, maker_scope, metadata, now), stage='PAPER',
                        fingerprint=r['rule'].sha256, prefix='maker:')
    reviews += m['reviews']
    monkeypatch.setattr(certification, 'protected_reviews', lambda:deepcopy(dict(reviews=reviews)))
    states = {payout_scope.key:r['model_state'][0], maker_scope.key:promote(bundle, authority.empty_state(maker_scope.key, 'V11_PAPER'))}
    monkeypatch.setattr(model_registry, 'protected_state',
                        lambda *, scope_key, mode:dict(state=states[scope_key], sha256=digest(states[scope_key])))
    StrategyAdmission(store).pin('fair-pin', **r['admission_kw'])
    StrategyAdmission(store).pin('maker-pin', **dict(r['admission_kw'], scope=maker_scope))
    r['coordinator'] = coordinator(r); r['policy'] = MakerResearchPolicy('fixture', 30., 10., '10', 4, 16)
    r['micro_policy'] = MicrostructurePolicy('fixture', 'FIXTURE_COLLATERAL', 20., 60., 5., .01, 2)
    book(r, 'initial', sequence=1, previous=None); features(r, 'initial', 'micro')
    r['quote'] = ResearchQuote('quote', 'thesis', r['context'], r['rule'], r['binding'], 'maker-pin', 'state2',
                'micro', r['request'].market_id, 'YES', 'BUY', '.1', '2', '.01', now[0]+20)
    assert propose(r)['outcome'] == 'OBSERVING_RESEARCH_QUOTE'
    return r


def test_same_day_requires_exact_source_and_separately_reviewed_payout_capability(same_day):
    d = measure(same_day)
    assert d['outcome'] == 'GATED' and d['reason'] == 'MAKER_SAME_DAY_REVIEWED_PAYOUT_PIN_REQUIRED'
    d = measure(same_day, 'reviewed', payout_admission_id='fair-pin', observed_input_id='official2', coverage_input_id='coverage')
    assert d['outcome'] == 'MEASURED_RESEARCH_CONTEXT', d['reason']
    assert d['prediction']['revision_risk'] == 'CONDITIONAL_ON_ACCEPTED_REVISION_UNMODELED'
    assert d['fair_value']['inference_cutoff'] < d['as_of']
    assert Decimal(d['fair_value']['lower']) == 0 and Decimal(d['fair_value']['upper']) == 1


def test_same_day_missing_exact_condition_is_not_unconditional_final_fair_value(same_day):
    d = measure(same_day, payout_admission_id='fair-pin')
    assert d['reason'] == 'SAME_DAY_CONDITION_REQUIRES_ADMISSION_LEASE' and d['fair_value'] is None


def test_future_day_does_not_silently_accept_same_day_condition_inputs(rig):
    propose(rig); d = measure(rig, observed_input_id='official2')
    assert d['reason'] == 'MAKER_FUTURE_DAY_CONDITION_MISMATCH' and d['fair_value'] is None
