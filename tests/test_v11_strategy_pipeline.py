from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from polymarket_scanner.v11 import model_registry, strategy_pipeline as pipeline
from polymarket_scanner.v11.event_risk import EventContext, EventRiskEngine, SafetyReductions
from polymarket_scanner.v11.evidence import EvidenceError, ReleaseBinding, digest
from polymarket_scanner.v11.rules import RuleGuard, fingerprint_event
from polymarket_scanner.v11.probability import FINAL_EXTREME, NEXT_OBSERVATION, UNRESOLVED_EXTREME, target_identity
from polymarket_scanner.v11.strategy_admission import StrategyAdmission, SourceLease
from polymarket_scanner.v11.strategy_pipeline import EntryRequest, TemperatureStrategies
from polymarket_scanner.v11.valuation import ValuationPolicy, contract_target
from test_v11_certification_rules import setup, approve_fixture, observe_rule
from test_v11_model_artifacts import bundle
from test_v11_model_governance import authority, promote
from test_v11_event_risk import policy, metrics
from test_v11_valuation import costs
from test_weather_final_gpt6_exact_replays import _event


@pytest.fixture
def factory(setup, bundle, monkeypatch):
    def build(sleeve='FUTURE_FORECAST', *, offset=None, target=None, model_change=None,
              observed_change=None, coverage_change=None, family='high', event_hazard=False):
        store, registry, scope, metadata, now = setup
        event = _event(station='KATL', family=family)
        r = fingerprint_event(event, station_timezone=metadata.timezone, metadata_fingerprint=metadata.fingerprint)
        day = date.fromisoformat(r.payload['target_date']); tz = ZoneInfo(r.payload['timezone'])
        start = datetime.combine(day, time.min, tz).timestamp()
        end = datetime.combine(day+timedelta(days=1), time.min, tz).timestamp()
        same = sleeve == 'SAME_DAY_LATE_LOCK'
        now[0] = start+(12*3600 if same else -30*3600) if offset is None else start+offset
        r = observe_rule(store, RuleGuard(store), event, metadata, 'rules')
        scope = replace(scope, strategy=sleeve, family='HIGH' if family == 'high' else 'LOW',
                        horizon='0_24_HOURS' if same else '24_48_HOURS',
                        source_rule_family=r.payload['source_family'], model_version='test-v1')
        context = EventContext('account', metadata.city, metadata.station, r.payload['event_id'])
        binding = ReleaseBinding('a'*40, 'b'*40, 'c'*64, bundle[3], r.sha256)
        contract = contract_target(r, r.payload['partition'][0]['market_id'], 'YES')
        source_payload = {k:r.payload[k] for k in ('station', 'target_date', 'family', 'unit')}
        source_payload.update(rule_fingerprint=r.sha256, temperature_input={
            'version':pipeline.INPUT_VERSION, 'model_id':'model-1',
            'target_sha256':target_identity(r, target or (UNRESOLVED_EXTREME if same else FINAL_EXTREME)),
            'members':[70., 71., 72.]})
        if model_change:
            source_payload['temperature_input'].update(model_change)
        official_payload = {k:r.payload[k] for k in
                     ('station', 'target_date', 'family', 'unit', 'observation_population', 'source_family')}
        official_payload.update(rule_fingerprint=r.sha256, exact_extreme={
            'version':pipeline.CONDITION_VERSION, 'whole_degree_value':72 if family == 'high' else 69,
            'source_role':'EXACT_CONTRACT_OBSERVATION'})
        if observed_change:
            official_payload.update(observed_change)
        for i in range(3):
            if i:
                now[0] += 10
            model_id, official_id, book_id, state_id = (f'{name}{i}' for name in ('model', 'official', 'book', 'state'))
            model_row = store.capture(model_id, event_id=context.event_id, kind='MODEL', provider='fixture',
                 source_identity='model-1', revision=str(i), observed_at=now[0], issued_at=now[0]-30,
                 payload=source_payload, evidence_class='SYNTHETIC')
            official_row = store.capture(official_id, event_id=context.event_id, kind='OFFICIAL_OBSERVATION',
                 provider='fixture', source_identity='KATL', revision=str(i), observed_at=now[0],
                 payload=official_payload, evidence_class='SYNTHETIC')
            store.capture(book_id, event_id=context.event_id, kind='BOOK', provider='fixture',
                 source_identity=contract['token_id'], revision=str(i), observed_at=now[0], evidence_class='SYNTHETIC',
                 payload=dict(contract, rule_fingerprint=r.sha256, collateral_asset='FIXTURE_COLLATERAL',
                    stream_healthy=True, bids=[{'price':'.1','size':'20'}], asks=[{'price':'.2','size':'20'}]))
            EventRiskEngine(store).step(state_id, context=context, policy=policy(), binding=binding,
                     metrics=replace(metrics(now[0]), source_revision=event_hazard),
                     book_ids=(book_id,), source_ids=(model_id, official_id))
        leases = [SourceLease(model_id, 'MODEL', 120.)]
        if same:
            coverage = dict(version=pipeline.COVERAGE_VERSION, rule_fingerprint=r.sha256, as_of=now[0],
                        accepted_intervals=[[start, now[0]]], unresolved_intervals=[[now[0], end]],
                        model_evidence_sha256=[model_row['sha256']], observation_id=official_id,
                        observation_sha256=official_row['sha256'])
            if coverage_change:
                coverage.update(coverage_change)
            store.capture('coverage', event_id=context.event_id, kind='FEATURES', provider='fixture',
                          source_identity='coverage', revision='1', payload=coverage, evidence_class='SYNTHETIC')
            leases += [SourceLease(official_id, 'OFFICIAL', 120.), SourceLease('coverage', 'FEATURES', 120.)]
        now[0] += .01  # Inference starts after source/feature archival, not at its exact clock tick.
        approve_fixture(monkeypatch, (store, registry, scope, metadata, now), stage='PAPER', fingerprint=r.sha256)
        state = [promote(bundle, authority.empty_state(scope.key, 'V11_PAPER'))]
        monkeypatch.setattr(model_registry, 'protected_state', lambda:{'state':state[0], 'sha256':digest(state[0])})
        monkeypatch.setattr(model_registry, 'ApprovedArtifactReader', lambda:bundle[0])
        admission_kw = dict(context=context, scope=scope, rule=r, binding=binding, stage='PAPER',
                            rule_max_age_seconds=120., source_leases=tuple(leases))
        StrategyAdmission(store).pin('pin', **admission_kw)
        request = EntryRequest('pin', state_id, contract['market_id'], 'YES', '2', '2', book_id, now[0]+20,
                              ValuationPolicy('fixture', 'FIXTURE_COLLATERAL', 30., 60., '.01', '20'), costs(),
                              (model_id,), official_id if same else None, 'coverage' if same else None)
        return dict(store=store, now=now, request=request, rule=r, scope=scope, context=context, binding=binding,
                    admission_kw=admission_kw, model_state=state)
    return build


def evaluate(rig, key='evaluation', **change):
    return TemperatureStrategies(rig['store']).evaluate(key, replace(rig['request'], **change))['body']['details']


def test_future_forecast_uses_archived_members_frozen_bundle_and_executable_costs(factory):
    rig = factory(); result = evaluate(rig)
    assert result['outcome'] == 'REJECT' and result['reason'] == 'CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD'
    assert result['prediction']['calibration_status'] == 'UNCALIBRATED'
    assert result['prediction']['model_inputs'][0]['evidence_sha256'] == rig['store'].get('model2')['sha256']
    assert result['valuation']['book']['gross_value'] == '0.4'
    assert result['artifact_refs'] and result['model_epoch'] == 1 and result['proposal'] is None
    assert result['executable_exit_value'] is None and not result['financial_authority']
    with pytest.raises(EvidenceError, match='NO_ECONOMIC_PROPOSAL'):
        TemperatureStrategies(rig['store']).proposal('evaluation')


@pytest.mark.parametrize('family,indices', [('high',(0,1)), ('low',(1,2))])
def test_same_day_conditions_exact_revision_and_entire_unresolved_day(factory, family, indices):
    result = evaluate(factory('SAME_DAY_LATE_LOCK', family=family))
    assert result['outcome'] == 'REJECT'
    p = result['prediction']
    assert all(p['buckets'][i]['point'] == 0 for i in indices)
    assert all(b['lower'] == 0 and b['upper'] == 1 for b in p['buckets'])
    assert p['revision_risk'] == 'CONDITIONAL_ON_ACCEPTED_REVISION_UNMODELED'
    assert result['inference_cutoff'] < result['evaluation_started_at']


@pytest.mark.parametrize('strategy,offset', [('FUTURE_FORECAST',0), ('FUTURE_FORECAST',86400),
                                          ('SAME_DAY_LATE_LOCK',-3600), ('SAME_DAY_LATE_LOCK',86400)])
def test_sleeve_is_selected_by_exact_local_contract_day_not_utc_or_legacy_hour(factory, strategy, offset):
    result = evaluate(factory(strategy, offset=offset))
    assert result['reason'] == 'STRATEGY_LOCAL_CONTRACT_DAY_MISMATCH'
    assert result['prediction'] is None


@pytest.mark.parametrize('target', [NEXT_OBSERVATION, UNRESOLVED_EXTREME])
def test_next_official_or_remaining_path_cannot_be_whole_day_forecast(factory, target):
    result = evaluate(factory(target=target))
    assert result['reason'] == 'ARCHIVED_MODEL_INPUT_SCHEMA_OR_TARGET'


def test_same_day_cannot_relabel_whole_day_model_as_remaining_extreme(factory):
    assert evaluate(factory('SAME_DAY_LATE_LOCK', target=FINAL_EXTREME))['reason'] == 'ARCHIVED_MODEL_INPUT_SCHEMA_OR_TARGET'


@pytest.mark.parametrize('change', [{'model_id':'unapproved-model'}, {'members':[]}, {'members':[float('inf')]},
                                  {'probability':.99}])
def test_model_members_and_identity_are_data_only_not_caller_probability(factory, change):
    if change == {'members':[float('inf')]}:
        with pytest.raises(EvidenceError, match='INVALID_JSON'):
            factory(model_change=change)
    else:
        assert evaluate(factory(model_change=change))['outcome'] == 'GATED'


def test_pws_or_metar_proxy_cannot_become_exact_extreme_by_agreement(factory):
    result = evaluate(factory('SAME_DAY_LATE_LOCK', observed_change={
        'exact_extreme':{'version':pipeline.CONDITION_VERSION, 'whole_degree_value':72, 'source_role':'METAR_PROXY'}}))
    assert result['reason'] == 'SAME_DAY_EXACT_SOURCE_POPULATION_REQUIRED'


@pytest.mark.parametrize('change', [{'observation_sha256':'f'*64}, {'model_evidence_sha256':['e'*64]},
                                  {'unresolved_intervals':[]}, {'accepted_intervals':[]}, {'as_of':1000.}])
def test_coverage_cannot_omit_elapsed_gaps_or_use_another_model_revision(factory, change):
    if 'as_of' in change:
        with pytest.raises(EvidenceError, match='STRATEGY_SOURCE_STALE_OR_AGE_UNKNOWN'):
            factory('SAME_DAY_LATE_LOCK', coverage_change=change)
        return
    result = evaluate(factory('SAME_DAY_LATE_LOCK', coverage_change=change))
    assert result['outcome'] == 'GATED' and result['prediction'] is None


def test_unleased_model_cannot_enter_inference(factory):
    rig = factory()
    assert evaluate(rig, model_input_ids=('model1',))['reason'] == 'ALL_INFERENCE_MODELS_REQUIRE_ADMISSION_LEASE'


def test_unknown_cost_stays_a_durable_gate(factory):
    result = evaluate(factory(), costs=costs(EXECUTION_UNCERTAINTY=None))
    assert result['outcome'] == 'GATED' and result['reason'] == 'UNKNOWN_OR_MISSING_COST_COVERAGE'


def test_new_official_arrival_after_admission_prevents_reusing_preconfirmation_thesis(factory):
    rig = factory(); store = rig['store']
    store.capture('revision', event_id=rig['context'].event_id, kind='OFFICIAL_OBSERVATION', provider='fixture',
                  source_identity='KATL', revision='3', observed_at=rig['now'][0], payload={'station':'KATL'},
                  evidence_class='SYNTHETIC')
    result = evaluate(rig)
    assert 'SOURCE_CHANGED' in result['reason'] and result['prediction'] is None


def test_event_hazard_and_operator_reduction_remain_effective(factory):
    rig = factory(event_hazard=True)
    assert evaluate(rig)['reason'] == 'EVENT_STATE_SUPPRESSES_TEMPERATURE_ENTRY'
    SafetyReductions(rig['store']).apply('stop', scope='EVENT', scope_id=rig['context'].event_id,
                          action='NO_NEW_ORDERS', actor='fixture', reason='TEST')
    assert evaluate(rig, 'after-stop')['reason'] == 'EVENT_OR_OPERATOR_STATE_CHANGED'


def test_completed_evaluation_replay_never_refreshes_time_or_creates_duplicate_records(factory):
    rig = factory(); result = evaluate(rig); count = len(rig['store'].records(kind='MEASUREMENT', limit=1000))
    rig['now'][0] += 1000
    assert evaluate(rig) == result and len(rig['store'].records(kind='MEASUREMENT', limit=1000)) == count
    with pytest.raises(EvidenceError, match='REQUEST_ID_COLLISION'):
        evaluate(rig, units='1')


def test_interrupted_after_valuation_resumes_pinned_input_without_duplicate_value(factory, monkeypatch):
    rig = factory(); store = rig['store']; original = store.audit
    def interrupted(record_id, **kwargs):
        if record_id == 'evaluation':
            raise RuntimeError('SIMULATED_INTERRUPTION')
        return original(record_id, **kwargs)
    monkeypatch.setattr(store, 'audit', interrupted)
    with pytest.raises(RuntimeError, match='SIMULATED_INTERRUPTION'):
        evaluate(rig)
    value = store.get('evaluation:valuation')
    rig['now'][0] += 1
    monkeypatch.setattr(store, 'audit', original)
    result = evaluate(rig)
    assert result['outcome'] == 'REJECT' and store.get('evaluation:valuation') == value
    assert result['inference_cutoff'] == rig['now'][0]-1


def test_model_demotion_during_inference_gates_the_final_result(factory, monkeypatch):
    rig = factory(); original = pipeline.predict_with_bundle
    def demote(*args, **kwargs):
        result = original(*args, **kwargs)
        state = rig['model_state'][0]
        rig['model_state'][0] = authority.transition(state, action='DEMOTE', expected_state_sha256=digest(state),
                                                   now=21., reason='TEST_DRIFT', size_multiplier=.5)
        return result
    monkeypatch.setattr(pipeline, 'predict_with_bundle', demote)
    result = evaluate(rig)
    assert result['outcome'] == 'GATED' and result['reason'] == 'MODEL_MANUAL_REVIEW'


def test_bounded_event_work_runs_real_strategy_evaluation_without_creating_a_fill(factory):
    from polymarket_scanner.v11.event_queue import EventQueue, EventRoute, TriggerPolicy, KINDS
    rig=factory(); store=rig['store']; rule=rig['rule']; p=rule.payload; now=rig['now'][0]
    route=EventRoute(p['event_id'],p['station'],p['target_date'],p['family'],rule.sha256,
                     tuple(t for b in p['partition'] for t in (b['yes_token'],b['no_token'])),now+100,('MODEL',))
    trigger=TriggerPolicy('fixture',1,1,4,16,30.,10.,100_000,60.,
                         tuple((k,60.) for k in sorted(KINDS)),((p['station'],30.),))
    q=EventQueue(store,routes=(route,),policy=trigger)
    q.publish('enqueue',kind='MODEL',evidence_id='model2')
    with q.work('work') as claim:
        assert claim['event_id']==p['event_id']
        assert evaluate(rig)['outcome']=='REJECT'
        finished=q.finish('finished',claim_id='work',result_ids=('evaluation',))
        assert finished['body']['details']['result']['outcome']=='RESEARCH_EVALUATED'
    assert not store.records(kind='TRADE',event_id=p['event_id'])
    assert store.get('evaluation')['body']['details']['proposal'] is None
