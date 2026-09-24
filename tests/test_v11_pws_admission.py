from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11 import certification, model_registry, strategy_pipeline as pipeline
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.paper_coordinator import PaperAccountPolicy, PaperCoordinator, Proposal
from polymarket_scanner.v11.probability import NEXT_OBSERVATION, FINAL_EXTREME, target_identity
from polymarket_scanner.v11.pws_admission import PWSPreconfirmation
from polymarket_scanner.v11.pws_lead import PWSObservationLead, LeadPolicy, REPORT_VERSION
from polymarket_scanner.v11.scenario_risk import Attribution
from polymarket_scanner.v11.strategy_admission import StrategyAdmission, SourceLease
from test_v11_certification_rules import setup, approve_fixture
from test_v11_model_artifacts import bundle
from test_v11_model_governance import authority, promote
from test_v11_scenario_risk import mapping, limits
from test_v11_strategy_pipeline import factory


@pytest.fixture
def joined(factory, setup, bundle, monkeypatch):
    rig = factory('SAME_DAY_LATE_LOCK')
    store, registry, _, metadata, now = setup
    rule = rig['rule']; event = rule.payload['event_id']
    old = store.get('official2')['body']
    report = dict(old['payload'], version=REPORT_VERSION, source_role='EXACT_CONTRACT_OBSERVATION',
                  reported_whole_degree=72)
    official = store.capture('anchor', event_id=event, kind='OFFICIAL_OBSERVATION', provider=old['provider'],
                source_identity=old['source_identity'], revision='report', observed_at=old['observed_at'],
                payload=report, evidence_class='SYNTHETIC')
    qc = dict(station='KATL', official_metadata_fingerprint=metadata.fingerprint, health='HEALTHY',
              as_of=now[0], observation_age_seconds=[2., 3.])
    pws = store.capture('pws', event_id=event, kind='PWS_OBSERVATION', provider='ALPHA_PWS_QC',
                       source_identity='KATL', revision='1', payload=qc, evidence_class='SYNTHETIC')
    coverage = deepcopy(store.get('coverage')['body']['payload'])
    coverage.update(as_of=now[0], observation_id=official['id'], observation_sha256=official['sha256'])
    coverage['accepted_intervals'][-1][1] = coverage['unresolved_intervals'][0][0] = now[0]
    store.capture('payout-coverage', event_id=event, kind='FEATURES', provider='fixture', source_identity='coverage',
                  revision='2', payload=coverage, evidence_class='SYNTHETIC')
    for key, parents, members in [('lead-model', (official, pws), [73.]), ('ablation-model', (official,), [71.])]:
        p = {k:rule.payload[k] for k in ('station', 'target_date', 'family', 'unit')}
        p.update(rule_fingerprint=rule.sha256, temperature_input=dict(version=pipeline.INPUT_VERSION, model_id='model-1',
                 target_sha256=target_identity(rule, NEXT_OBSERVATION), members=members),
                 observation_context=dict(official_anchor_id='anchor', official_anchor_sha256=official['sha256'],
                                          horizon_seconds=120., window_clock='FIRST_ALPHA_RECEIPT'),
                 dependencies=[dict(id=r['id'], sha256=r['sha256']) for r in parents])
        store.capture(key, event_id=event, kind='MODEL', provider='fixture', source_identity=key, revision='1',
                      issued_at=now[0]-10, payload=p, evidence_class='SYNTHETIC')
    objects = bundle[0]; values = deepcopy(bundle[1])
    for value in values.values():
        value['target'] = NEXT_OBSERVATION
        value['provenance']['model_version'] = 'lead-v1'
    values['CALIBRATION']['parameters']['probability_artifact_sha256'] = digest(values['PROBABILITY'])
    refs = {k:objects.put_artifact(v) for k, v in values.items()}
    key = objects.put_bundle(artifacts=refs, target=NEXT_OBSERVATION,
                            feature_schema_sha256=values['FEATURES']['feature_schema_sha256'])
    observation_bundle = (objects, values, refs, key)
    payout_scope = replace(rig['scope'], strategy='PWS_OBSERVATION_LEAD')
    observation_scope = replace(payout_scope, model_version='lead-v1', horizon='NEXT_120_SECONDS')
    reviews = []
    for prefix, scope in [('payout:', payout_scope), ('observation:', observation_scope)]:
        m = approve_fixture(monkeypatch, (store, registry, scope, metadata, now), stage='PAPER',
                            fingerprint=rule.sha256, prefix=prefix)
        reviews += m['reviews']
    monkeypatch.setattr(certification, 'protected_reviews', lambda:deepcopy(dict(reviews=reviews)))
    states = {scope.key:promote(b, authority.empty_state(scope.key, 'V11_PAPER')) for scope, b in
              ((observation_scope, observation_bundle), (payout_scope, bundle))}
    monkeypatch.setattr(model_registry, 'protected_state',
                        lambda *, scope_key, mode:dict(state=states[scope_key], sha256=digest(states[scope_key])))
    monkeypatch.setattr(model_registry, 'ApprovedArtifactReader', lambda:objects)
    shared = (SourceLease('anchor', 'OFFICIAL', 120.), SourceLease('pws', 'PWS', 120.))
    payout_kw = dict(rig['admission_kw'], scope=payout_scope, source_leases=(SourceLease('model2', 'MODEL', 120.),
                    SourceLease('payout-coverage', 'FEATURES', 120.), *shared))
    observation_binding = replace(rig['binding'], bundle_sha256=key)
    observation_kw = dict(payout_kw, scope=observation_scope, binding=observation_binding,
                         source_leases=(SourceLease('lead-model', 'MODEL', 120.), *shared))
    for admission_id, kw in [('payout-pin', payout_kw), ('observation-pin', observation_kw)]:
        StrategyAdmission(store).pin(admission_id, **kw)
    policy = LeadPolicy('test-policy', 120., 60., 30., 120., 120.)
    lead_kw = dict(rule=rule, binding=observation_binding, policy=policy, official_id='anchor', pws_id='pws',
                   model_ids=('lead-model',), without_pws_model_ids=('ablation-model',),
                   bundle=objects.pin(key), without_pws_bundle=objects.pin(key))
    PWSObservationLead(store).observe('lead', **lead_kw)
    pin_kw = dict(lead_id='lead', observation_admission_id='observation-pin', payout_admission_id='payout-pin')
    PWSPreconfirmation(store).pin('paired-pin', **pin_kw)
    request = replace(rig['request'], admission_id='payout-pin', observed_input_id='anchor',
                      coverage_input_id='payout-coverage', preconfirmation_id='paired-pin')
    rig.update(request=request, states=states, payout_scope=payout_scope, observation_scope=observation_scope,
               lead_kw=lead_kw, pin_kw=pin_kw, payout_kw=payout_kw, observation_kw=observation_kw)
    return rig


def verify(rig, key='paired-pin', **changes):
    return PWSPreconfirmation(rig['store']).revalidate(key, **{
               'context':rig['context'], 'rule':rig['rule'], 'binding':asdict(rig['binding']),
               'payout_admission_ids':('payout-pin',), **changes})


def evaluate(rig, **changes):
    return pipeline.TemperatureStrategies(rig['store']).evaluate('entry', replace(rig['request'], **changes))['body']['details']


def arrival(rig, key='official-arrival'):
    row = rig['store'].get('anchor'); b = row['body']
    return rig['store'].capture(key, event_id=row['event_id'], kind=row['kind'], provider=b['provider'],
                 source_identity=b['source_identity'], revision=key, observed_at=rig['now'][0],
                 payload=b['payload'], evidence_class='SYNTHETIC')


def coordinator(rig):
    p = PaperAccountPolicy('test-policy', 'account', 'FIXTURE_COLLATERAL', '10', '10', '10', '10', '.01', '0', 60., 10)
    corr = mapping(rig['rule'])
    corr = replace(corr, memberships=(replace(corr.memberships[0], city=rig['context'].city_id),))
    return PaperCoordinator(rig['store'], policy=p, correlation=corr, limits=limits())


def synthetic_proposal(rig, key='one'):
    # Only the downstream EV result is synthetic. Source, model, certification,
    # paired pin and account/risk revalidation run through their actual code.
    try:
        value = deepcopy(rig['store'].get('entry:valuation')['body']['details'])
    except EvidenceError:
        assert evaluate(rig)['outcome'] == 'REJECT'
        value = deepcopy(rig['store'].get('entry:valuation')['body']['details'])
    value.update(outcome='ACCEPT_RESEARCH', conservative_ev_per_share='.2', conservative_ev_total='.4',
                 synthetic_downstream_test_fixture=True)
    rig['store'].audit('fixture-value-'+key, event_id=rig['context'].event_id, kind='MEASUREMENT', details=value)
    return Proposal(key, 'same-thesis', rig['context'], rig['rule'], 'fixture-value-'+key,
                    rig['request'].event_state_id, (Attribution('PWS_OBSERVATION_LEAD', '1'),), rig['now'][0]+20,
                    '2', ('payout-pin',), 'paired-pin')


def test_two_separately_reviewed_targets_join_without_converting_observation_to_payout(joined):
    a = verify(joined)
    assert a['observation_bundle_sha256'] != a['payout_bundle_sha256']
    assert a['valuation_type'] == 'SETTLEMENT' and a['executable_exit_proceeds'] is None
    assert not a['financial_authority']
    result = evaluate(joined)
    assert result['outcome'] == 'REJECT' and result['reason'] == 'CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD'
    assert result['prediction']['target'] == FINAL_EXTREME
    assert result['prediction']['bundle_sha256'] == joined['binding'].bundle_sha256
    assert result['proposal'] is None and result['executable_exit_value'] is None
    assert all(b['lower'] == 0 for b in result['prediction']['buckets'])


def test_missing_pair_cannot_create_economic_entry(joined):
    assert evaluate(joined, preconfirmation_id=None)['reason'] == 'PWS_SEPARATE_OBSERVATION_AND_ECONOMICS_PIN_REQUIRED'


def test_observation_model_cannot_replace_payout_champion(joined):
    kw = dict(joined['pin_kw'], payout_admission_id='observation-pin')
    with pytest.raises(EvidenceError, match='SEPARATE_APPROVED_OBSERVATION_AND_PAYOUT'):
        PWSPreconfirmation(joined['store']).pin('wrong-pair', **kw)


def test_pair_cannot_be_reused_for_another_release_or_admission(joined):
    with pytest.raises(EvidenceError, match='PROPOSAL_MISMATCH'):
        verify(joined, payout_admission_ids=('unrelated',))
    with pytest.raises(EvidenceError, match='PROPOSAL_MISMATCH'):
        verify(joined, binding=asdict(replace(joined['binding'], config_sha256='f'*64)))


def test_sensor_policy_expiry_is_stricter_than_general_admission_lease(joined):
    original = verify(joined)
    assert original['valid_until'] == joined['now'][0]+27.
    joined['now'][0] += 28
    with pytest.raises(EvidenceError, match='SOURCE_POLICY_EXPIRED'):
        verify(joined)


@pytest.mark.parametrize('which', ['observation_scope', 'payout_scope'])
def test_demotion_of_either_model_invalidates_pair(joined, which):
    key = joined[which].key; state = joined['states'][key]
    joined['states'][key] = authority.transition(state, action='DEMOTE', expected_state_sha256=digest(state),
                            now=21., reason='TEST', size_multiplier=.5)
    with pytest.raises(EvidenceError, match='MODEL_MANUAL_REVIEW'):
        verify(joined)


def test_first_official_update_invalidates_pair_before_reservation(joined):
    p = synthetic_proposal(joined); arrival(joined)
    result = coordinator(joined).coordinate('batch', (p,))['body']['details']
    assert not result['reserved_intent_ids'] and 'NEW_OFFICIAL' in result['results'][0]['reason']


def test_pair_pin_append_guards_racing_official_receipt(joined, monkeypatch):
    store = joined['store']; original = store.audit
    def race(key, **kw):
        if key == 'new-pair':
            arrival(joined)
        return original(key, **kw)
    monkeypatch.setattr(store, 'audit', race)
    with pytest.raises(EvidenceError, match='AUDIT_GUARDED_STATE_CHANGED'):
        PWSPreconfirmation(store).pin('new-pair', **joined['pin_kw'])


def test_replayed_pin_preserves_old_expiry_and_does_not_refresh_authority(joined):
    gate = PWSPreconfirmation(joined['store']); old = joined['store'].get('paired-pin')
    joined['now'][0] += 121
    assert gate.pin('paired-pin', **joined['pin_kw']) == old
    with pytest.raises(EvidenceError, match='WINDOW_EXPIRED'):
        verify(joined)


def test_common_account_keeps_one_exposure_and_checks_pair_before_submission(joined):
    p, q = synthetic_proposal(joined), synthetic_proposal(joined, 'two')
    c = coordinator(joined); result = c.coordinate('batch', (p, q))['body']['details']
    assert result['reserved_intent_ids'] == ['one']
    before = c.snapshot()['reserved_cash']
    assert Decimal(before) > 0
    arrival(joined)
    with pytest.raises(EvidenceError, match='NEW_OFFICIAL'):
        c.transition('submit', intent_id='one', status='SUBMITTING')
    assert c.snapshot()['reserved_cash'] == before
    assert c._state(c._head())['intents']['one']['status'] == 'RESERVED'
    assert c.transition('cancel', intent_id='one', status='CANCEL_REQUESTED')['body']['details']['reservation_released'] is False


def test_manual_pws_proposal_without_pair_is_gated_by_common_account(joined):
    p = replace(synthetic_proposal(joined), preconfirmation_id=None)
    d = coordinator(joined).coordinate('batch', (p,))['body']['details']
    assert not d['reserved_intent_ids']
    assert d['results'][0]['reason'] == 'PWS_SEPARATE_OBSERVATION_AND_ECONOMICS_PIN_REQUIRED'


def test_new_book_before_submission_requires_new_exact_economics_preserving_reservation(joined):
    p = synthetic_proposal(joined); c = coordinator(joined); c.coordinate('batch', (p,))
    old = joined['store'].get(joined['request'].book_id); b = old['body']
    joined['store'].capture('new-book', event_id=old['event_id'], kind='BOOK', provider=b['provider'],
        source_identity=b['source_identity'], revision='new', observed_at=joined['now'][0], payload=b['payload'], evidence_class='SYNTHETIC')
    before = c.snapshot()['reserved_cash']
    with pytest.raises(EvidenceError, match='CURRENT_EXACT_BOOK'):
        c.transition('submit', intent_id='one', status='SUBMITTING')
    assert c.snapshot()['reserved_cash'] == before


def test_official_arrival_during_payout_inference_does_not_publish_stale_proposal(joined, monkeypatch):
    original = pipeline.predict_with_bundle
    def race(*args, **kw):
        result = original(*args, **kw); arrival(joined)
        return result
    monkeypatch.setattr(pipeline, 'predict_with_bundle', race)
    result = evaluate(joined)
    assert result['outcome'] == 'GATED' and result['proposal'] is None and 'NEW_OFFICIAL' in result['reason']


def test_new_book_racing_reservation_is_guarded_atomically(joined, monkeypatch):
    p = synthetic_proposal(joined); store = joined['store']; original = store.audit
    def race(key, **kw):
        if key == 'batch':
            old = store.get(joined['request'].book_id); b = old['body']
            store.capture('racing-book', event_id=old['event_id'], kind='BOOK', provider=b['provider'],
                source_identity=b['source_identity'], revision='racing', observed_at=joined['now'][0],
                payload=b['payload'], evidence_class='SYNTHETIC')
        return original(key, **kw)
    monkeypatch.setattr(store, 'audit', race)
    c = coordinator(joined)
    with pytest.raises(EvidenceError, match='AUDIT_GUARDED_STATE_CHANGED'):
        c.coordinate('batch', (p,))
    assert Decimal(c.snapshot()['reserved_cash']) == 0


def test_payout_cannot_use_observation_admission_from_a_different_account(joined):
    kw = dict(joined['observation_kw'], context=replace(joined['context'], account_id='another-account'))
    StrategyAdmission(joined['store']).pin('other-account-observation', **kw)
    with pytest.raises(EvidenceError, match='MODEL_PAIR_CONTEXT_OR_RELEASE'):
        PWSPreconfirmation(joined['store']).pin('other-pair',
            **dict(joined['pin_kw'], observation_admission_id='other-account-observation'))
