from dataclasses import replace
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pytest

from polymarket_scanner.v11 import remaining_forecast as remaining
from polymarket_scanner.v11 import gefs_sources
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.forecast_sources import ForecastPlan
from polymarket_scanner.v11.probability import UNRESOLVED_EXTREME
from polymarket_scanner.v11.rules import fingerprint_event
from polymarket_scanner.v11.strategy_pipeline import _condition, _model_inputs, CONDITION_VERSION
from test_v11_gefs_sources import gefs, field
from test_v11_grib_fields import grib
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_strategy_pipeline import factory
from test_weather_final_gpt6_exact_replays import _event


def prepare(r, *, family='high', intervals=None, change=None, day=None):
    p = r['plan']; metadata = p.forecast.metadata
    rule = fingerprint_event(_event(station='KATL',family=family,target=day or date.fromisoformat(p.rule.payload['target_date'])),
        station_timezone=metadata.timezone,metadata_fingerprint=metadata.fingerprint)
    run = datetime.combine(date.fromisoformat(rule.payload['target_date']),time(),timezone.utc).timestamp()
    r['plan'] = plan = gefs_sources.GEFSPlan(ForecastPlan(rule,metadata,50.,86400.),run)
    r['now'][0] = plan.window[0]+12*3600
    fields = tuple(field(r,m,h,data=grib(member=m,hour=h,run=run,packing=1,
        values=(300. if family=='high' and h==9 else 270. if family=='low' and h==9 else 280.,290.,291.,292.)))['id']
        for m in range(31) for h in plan.hours)
    path = gefs_sources.assemble_path(r['store'],plan=plan,field_ids=fields,record_id='whole-path')
    official = observation(r,intervals=intervals,change=change)
    return path, official


def observation(r, *, intervals=None, change=None, key='exact'):
    p = r['plan'].rule.payload; now = r['now'][0]
    payload = {k:p[k] for k in ('station','target_date','family','unit','observation_population','source_family')}
    payload.update(rule_fingerprint=r['plan'].rule.sha256,exact_extreme=dict(version=CONDITION_VERSION,
        whole_degree_value=70 if p['family']=='daily_high_temperature' else 30,source_role='EXACT_CONTRACT_OBSERVATION'),
        observation_coverage=dict(version=remaining.OBSERVATION_COVERAGE_VERSION,
            accepted_intervals=intervals if intervals is not None else [[r['plan'].window[0],now]]))
    if change: change(payload)
    return r['store'].capture(key,event_id=r['plan'].event_id,kind='OFFICIAL_OBSERVATION',provider='exact-fixture',
        source_identity='KATL-exact-population',revision=key,observed_at=now,payload=payload,evidence_class='SYNTHETIC')


def derive(r, key='remaining', **kw):
    return remaining.archive_remaining_path(r['store'],key,plan=r['plan'],path_id='whole-path',observation_id='exact',**kw)


def conditioned(r, pair):
    request = SimpleNamespace(model_input_ids=(pair['model']['id'],),observed_input_id='exact',coverage_input_id=pair['coverage']['id'])
    at = pair['coverage']['body']['payload']['as_of']
    components = _model_inputs(r['store'],r['plan'].rule,request.model_input_ids,at,target=UNRESOLVED_EXTREME)
    observed, coverage = _condition(r['store'],r['plan'].rule,request,at,components,available_cutoff=r['now'][0])
    return components, observed, coverage


@pytest.mark.parametrize('family',['high','low'])
def test_remaining_path_excludes_accepted_extreme_and_retains_original_run(gefs,family):
    r = gefs; path, official = prepare(r,family=family); r['now'][0] += .1
    pair = derive(r); model = pair['model']; components, observed, coverage = conditioned(r,pair)
    assert components[0].members == pytest.approx((44.33,)*31)
    assert components[0].model_id == remaining.MODEL_ID
    assert model['body']['issued_at'] == path['body']['issued_at']
    assert model['body']['received_at'] == path['body']['received_at'] < model['body']['available_at']
    assert model['body']['published_at'] is None
    assert observed.evidence_sha256 == official['sha256']
    assert coverage.unresolved_intervals == ((official['body']['observed_at'],r['plan'].window[1]),)
    assert not model['body']['payload']['interpolation_uncertainty_calibrated']
    assert not model['body']['payload']['exact_population_independently_attested']
    assert not r['store'].records(kind='COORDINATOR_EVENT')


@pytest.mark.parametrize('family,expected',[('high',80.33),('low',26.33)])
def test_elapsed_observation_gap_is_still_part_of_unresolved_extreme(gefs,family,expected):
    r = gefs; start,end = r['plan'].window
    prepare(r,family=family,intervals=[[start,start+4*3600],[start+6*3600,start+12*3600]])
    pair = derive(r); components, _, coverage = conditioned(r,pair)
    assert components[0].members == pytest.approx((expected,)*31)
    assert len(coverage.unresolved_intervals) == 2
    assert coverage.unresolved_intervals[0] == (start+4*3600,start+6*3600)


@pytest.mark.parametrize('day,hours',[(date(2026,3,8),23),(date(2026,11,1),25)])
def test_same_day_coverage_preserves_dst_day_length(gefs,day,hours):
    r = gefs; prepare(r,day=day); _,_,coverage = conditioned(r,derive(r))
    assert sum(b-a for a,b in (*coverage.accepted_intervals,*coverage.unresolved_intervals)) == hours*3600


@pytest.mark.parametrize('fault',['missing','point_only','proxy','wrong_population','future','overlap','too_many'])
def test_incomplete_or_proxy_observation_coverage_never_becomes_accepted_prefix(gefs,fault):
    r = gefs
    def change(p):
        if fault == 'missing': p.pop('observation_coverage')
        if fault == 'point_only': p['observation_coverage']['accepted_intervals'] = []
        if fault == 'proxy': p['exact_extreme']['source_role'] = 'METAR_PROXY'
        if fault == 'wrong_population': p['observation_population'] = 'HOURLY_PROXY'
        if fault == 'future': p['observation_coverage']['accepted_intervals'][0][1] += 1
        if fault == 'overlap': p['observation_coverage']['accepted_intervals'] *= 2
        if fault == 'too_many': p['observation_coverage']['accepted_intervals'] *= 500
    prepare(r,change=change)
    with pytest.raises(EvidenceError): derive(r)
    with pytest.raises(EvidenceError,match='EVIDENCE_MISSING'): r['store'].get('remaining')


def test_current_official_or_parent_field_replacement_invalidates_derived_input(gefs):
    r = gefs; prepare(r); pair = derive(r)
    assert len(gefs_sources.current_path_heads(r['store'],pair['model'])) == 2
    observation(r,key='revision')
    with pytest.raises(EvidenceError,match='OFFICIAL_CHANGED'):
        gefs_sources.current_path_heads(r['store'],pair['model'])


def test_parent_field_change_is_detected_without_relabeling_old_path(gefs):
    r = gefs; prepare(r); pair = derive(r)
    original = r['store'].get('field-0-'+str(r['plan'].hours[0])); b = original['body']
    r['store'].capture('new-field-raw',event_id=r['plan'].event_id,kind='MODEL',provider=b['provider'],
        source_identity=b['source_identity'],revision='new',payload={})
    with pytest.raises(EvidenceError,match='CONSTITUENT_CHANGED'):
        gefs_sources.current_path_heads(r['store'],pair['model'])


def test_coverage_cannot_assign_another_observed_partition_to_remaining_members(gefs):
    r = gefs; prepare(r); pair = derive(r)
    c = pair['coverage']; p = c['body']['payload']; start,end = r['plan'].window
    p['accepted_intervals'][0][1] -= 1; p['unresolved_intervals'][0][0] -= 1
    changed = r['store'].capture('relabel',event_id=r['plan'].event_id,kind='FEATURES',provider='fixture',
        source_identity='coverage',revision='2',payload=p,evidence_class='SYNTHETIC')
    with pytest.raises(EvidenceError,match='CONDITION_MISMATCH'):
        conditioned(r,dict(model=pair['model'],coverage=changed))


def test_replay_preserves_source_times_and_completed_outputs(gefs):
    r = gefs; path,official = prepare(r); pair = derive(r); before = r['store'].pin_read_view()
    r['now'][0] += 100000
    assert derive(r) == pair and r['store'].pin_read_view() == before
    assert r['store'].get('whole-path') == path and r['store'].get('exact') == official
    with pytest.raises(EvidenceError,match='REPLAY_CONFLICT'):
        remaining.archive_remaining_path(r['store'],'remaining',plan=r['plan'],path_id='whole-path',observation_id='different')


def test_interruption_after_model_append_resumes_without_duplicate_or_refresh(gefs,monkeypatch):
    r = gefs; prepare(r); original = r['store']._append
    def interrupted(key,*args,**kw):
        if key == 'remaining:coverage': raise RuntimeError('INTERRUPTION')
        return original(key,*args,**kw)
    monkeypatch.setattr(r['store'],'_append',interrupted)
    with pytest.raises(RuntimeError,match='INTERRUPTION'): derive(r)
    saved = r['store'].get('remaining'); r['now'][0] += .1
    monkeypatch.setattr(r['store'],'_append',original)
    pair = derive(r)
    assert pair['model'] == saved
    assert pair['coverage']['body']['payload']['as_of'] == saved['body']['available_at']


@pytest.mark.parametrize('which',['remaining','remaining:coverage'])
def test_official_arrival_racing_either_append_gates_atomic_publication(gefs,monkeypatch,which):
    r = gefs; prepare(r); original = r['store']._append
    def race(key,*args,**kw):
        if key == which: observation(r,key='racing-official')
        return original(key,*args,**kw)
    monkeypatch.setattr(r['store'],'_append',race)
    with pytest.raises(EvidenceError,match='GUARDED_STATE_CHANGED'): derive(r)
    with pytest.raises(EvidenceError,match='EVIDENCE_MISSING'): r['store'].get('remaining:coverage')


def test_stale_fields_and_expired_bound_do_not_start_preparation(gefs):
    r = gefs; prepare(r)
    with pytest.raises(EvidenceError,match='TIME_BOUND'): derive(r,deadline=0.)
    r['now'][0] += 86400
    with pytest.raises(EvidenceError,match='SAME_DAY_WINDOW'): derive(r)


@pytest.mark.parametrize('changed',[False,True])
def test_shared_admission_guards_are_deduplicated_only_for_identical_versions(factory,monkeypatch,changed):
    from polymarket_scanner.v11.strategy_admission import StrategyAdmission
    rig = factory(); event = rig['context'].event_id
    head = rig['store'].latest(kind='MODEL',event_id=event)['seq']
    monkeypatch.setattr(gefs_sources,'current_path_heads',lambda store,row:(('MODEL',event,head-int(changed)),))
    if changed:
        with pytest.raises(EvidenceError,match='SOURCE_CHANGED_DURING_ASSESSMENT'):
            StrategyAdmission(rig['store']).pin('derived-pin',**rig['admission_kw'])
    else:
        pin = StrategyAdmission(rig['store']).pin('derived-pin',**rig['admission_kw'])
        heads = pin['body']['details']['assessment']['heads']
        assert [h for h in heads if h[0]=='MODEL'] == [['MODEL',event,head]]


def test_remaining_inputs_reach_protected_same_day_economics_without_implying_eligibility(tmp_path,monkeypatch):
    from test_v11_model_artifacts import make_artifacts
    from polymarket_scanner.v11.model_artifacts import ArtifactStore
    from polymarket_scanner.v11 import model_registry
    from polymarket_scanner.v11.certification import StationRegistry,CapabilityScope
    from polymarket_scanner.v11.event_risk import EventContext,EventRiskEngine
    from polymarket_scanner.v11.evidence import EvidenceStore,ReleaseBinding
    from polymarket_scanner.v11.rules import RuleGuard
    from polymarket_scanner.v11.strategy_admission import StrategyAdmission,SourceLease
    from polymarket_scanner.v11.strategy_pipeline import EntryRequest,TemperatureStrategies
    from polymarket_scanner.v11.valuation import ValuationPolicy,contract_target
    from test_v11_certification_rules import approve_fixture,observe_rule
    from test_v11_model_governance import authority,promote
    from test_v11_event_risk import metrics,policy
    from test_v11_valuation import costs
    from test_v11_pws_quality import official
    metadata = replace(official(),source_payload_sha256=digest({})); tmp_path.chmod(0o700); now = [1000.]
    store = EvidenceStore(tmp_path/'end-to-end.sqlite','V11_PAPER',clock=lambda:now[0])
    registry = StationRegistry(store)
    raw = store.capture('metadata-raw',event_id='station:KATL',kind='STATION_METADATA',provider='fixture',source_identity='KATL',
        revision='1',payload={},evidence_class='SYNTHETIC')
    registry.observe('metadata',metadata,raw_evidence_id=raw['id'])
    rule = fingerprint_event(_event(station='KATL'),station_timezone=metadata.timezone,metadata_fingerprint=metadata.fingerprint)
    run = datetime.combine(date.fromisoformat(rule.payload['target_date']),time(),timezone.utc).timestamp()
    r = dict(store=store,now=now,plan=gefs_sources.GEFSPlan(ForecastPlan(rule,metadata,50.,86400.),run))
    prepare(r); pair = derive(r); rule = observe_rule(store,RuleGuard(store),_event(station='KATL'),metadata,'rules')
    root = tmp_path/'bundles'; root.mkdir(mode=0o700); artifacts = ArtifactStore(root)
    values = make_artifacts(); values['PROBABILITY']['parameters']['models'][0]['model_id'] = remaining.MODEL_ID
    values['CALIBRATION']['parameters']['probability_artifact_sha256'] = digest(values['PROBABILITY'])
    refs = {k:artifacts.put_artifact(v) for k,v in values.items()}
    key = artifacts.put_bundle(artifacts=refs,target='FINAL_CONTRACT_PAYOUT',feature_schema_sha256=values['FEATURES']['feature_schema_sha256'])
    scope = CapabilityScope('KATL','HIGH',rule.payload['source_family'],'test-v1','0_24_HOURS','SAME_DAY_LATE_LOCK','AUTUMN','DAY')
    approve_fixture(monkeypatch,(store,registry,scope,metadata,now),stage='PAPER',fingerprint=rule.sha256)
    state = promote((artifacts,values,refs,key),authority.empty_state(scope.key,'V11_PAPER'))
    monkeypatch.setattr(model_registry,'protected_state',lambda **kw:dict(state=state,sha256=digest(state)))
    monkeypatch.setattr(model_registry,'ApprovedArtifactReader',lambda:artifacts)
    binding = ReleaseBinding('a'*40,'b'*40,'c'*64,key,rule.sha256)
    context = EventContext('account',metadata.city,metadata.station,rule.payload['event_id'])
    contract = contract_target(rule,rule.payload['partition'][0]['market_id'],'YES')
    for i in range(3):
        now[0] += 10
        book = store.capture('book'+str(i),event_id=context.event_id,kind='BOOK',provider='fixture',source_identity=contract['token_id'],
            revision=str(i),observed_at=now[0],evidence_class='SYNTHETIC',payload=dict(contract,rule_fingerprint=rule.sha256,
                collateral_asset='FIXTURE_COLLATERAL',stream_healthy=True,bids=[dict(price='.1',size='20')],asks=[dict(price='.2',size='20')]))
        event = EventRiskEngine(store).step('state'+str(i),context=context,
            policy=replace(policy(),max_source_age_seconds=86400.,model_age_caution_seconds=70000.,model_age_event_seconds=80000.),
            binding=binding,metrics=replace(metrics(now[0]),model_age_seconds=now[0]-run),
            book_ids=(book['id'],),source_ids=(pair['model']['id'],'exact'))
    StrategyAdmission(store).pin('admission',context=context,scope=scope,rule=rule,binding=binding,stage='PAPER',rule_max_age_seconds=120.,
        source_leases=(SourceLease(pair['model']['id'],'MODEL',86400.),SourceLease('exact','OFFICIAL',120.),SourceLease(pair['coverage']['id'],'FEATURES',120.)))
    request = EntryRequest('admission',event['id'],contract['market_id'],'YES','2','2',book['id'],now[0]+20,
        ValuationPolicy('fixture','FIXTURE_COLLATERAL',30.,60.,'.01','20'),costs(),(pair['model']['id'],),'exact',pair['coverage']['id'])
    result = TemperatureStrategies(store).evaluate('result',request)['body']['details']
    assert result['prediction']['target'] == 'FINAL_CONTRACT_EXTREME'
    assert result['prediction']['revision_risk'] == 'CONDITIONAL_ON_ACCEPTED_REVISION_UNMODELED'
    assert result['reason'] == 'CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD'
    assert result['prediction']['calibration_status'] == 'UNCALIBRATED'
    assert result['proposal'] is None and result['executable_exit_value'] is None
    assert len(store.records(kind='COORDINATOR_EVENT')) == 3  # Risk states only; no account mutation.
