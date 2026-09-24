import asyncio
from dataclasses import replace

import httpx
import pytest

from polymarket_scanner.v11 import candidate_assembly as app
from polymarket_scanner.v11.audit_reports import AuditPolicy
from polymarket_scanner.v11.book_inputs import BookPolicy, PROVIDER
from polymarket_scanner.v11.candidate_runner import CandidatePolicy
from polymarket_scanner.v11.census_worker import CensusPolicy, CensusPlan
from polymarket_scanner.v11.discovery import DiscoveryPolicy
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.microstructure import MicrostructurePolicy
from polymarket_scanner.v11.paper_runtime import RuntimePolicy
from polymarket_scanner.v11.request_assembly import TargetPlan, SourceSelector
from polymarket_scanner.v11.risk_inputs import RiskInputPolicy, VERSION as RISK_VERSION
from polymarket_scanner.v11.runtime_feed import FeedPolicy
from polymarket_scanner.v11.runtime_health import HealthPolicy
from polymarket_scanner.v11.candidate_runner import ObservationBatch
from polymarket_scanner.v11.pws_runtime import PWSQualityPlan, PWSQualitySettings
from polymarket_scanner.v11.weather_sources import madis_request
from polymarket_scanner.v11.forecast_sources import ForecastPlan, PROVIDER as FORECAST_PROVIDER, OPEN_METEO_ENSEMBLE, request_parameters
from polymarket_scanner.v11.strategy_admission import SourceLease
from polymarket_scanner.v11.valuation import HOLD_RISKS, SALE_RISKS, SALE
from test_v11_basket_coordinator import rig, reserve
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_strategy_pipeline import factory
from test_v11_request_assembly import inputs, target, evaluate, state_for
from test_v11_pws_admission import coordinator, joined
from test_v11_source_release import release_factory
from test_v11_position_management import inventory
from test_v11_valuation import costs
from test_v11_paper_runtime import queue
from test_v11_runtime_health import monitor, ready
from test_v11_event_risk import policy as event_policy
from test_v11_book_inputs import response


def scoped_plan(r,lane):
    c=coordinator(r);q=queue(r);scope=lane.inputs
    risk=RiskInputPolicy('fixture',MicrostructurePolicy('fixture','FIXTURE_COLLATERAL',30.,60.,5.,.01,2),event_policy())
    event=app.CandidateEvent(q.routes[r['context'].event_id],CensusPlan(r['rule'],'FIXTURE_COLLATERAL'),
        scope,PROVIDER,r['request'].valuation_policy,risk,(lane,))
    return app.CandidatePlan('fixture',c.policy,c.correlation,c.limits,(event,),q.policy,
        HealthPolicy('fixture',5.,5.,.25,2,.5,('worker',)),RuntimePolicy('fixture'),
        CandidatePolicy('fixture',maximum_jobs=3,maximum_seconds=5.,safety_interval_seconds=.05,minimum_job_spacing_seconds=.05),
        CensusPolicy('fixture'),BookPolicy('fixture'),DiscoveryPolicy('fixture'),AuditPolicy('fixture'),FeedPolicy('fixture'),'worker')


def plan(r):
    targets=tuple(TargetPlan(l.market_id,l.side,l.units,l.units,l.costs) for l in r['kw']['legs'])
    return scoped_plan(r,app.RelativeValueLane('relative',inputs(r),targets,PROVIDER,
        r['request'].valuation_policy,10.,r['kw']['policy']))


def synthetic_clock(r,monkeypatch):
    def health(store,p,*,account_id,scopes,sources):
        assert store is r['store'] and account_id==r['context'].account_id
        return monitor(r,monkeypatch,policy=p,scopes=scopes,sources=sources)
    monkeypatch.setattr(app,'RuntimeHealth',health)


def transport(r,calls):
    def handle(req):
        calls.append(req)
        if req.url.host=='gamma-api.polymarket.com':return httpx.Response(200,json=dict(events=[],next_cursor=None))
        if req.url.host=='aviationweather.gov':return httpx.Response(200,json=[dict(icaoId=r['context'].station_id,obsTime=r['now'][0]-1,temp=25)])
        return httpx.Response(200,json=response(r,req.url.params['token_id']))
    return httpx.MockTransport(handle)


def evaluate_built_lane(r,cfg,monkeypatch):
    """Exercise the assembled dispatcher using the fixture's existing risk pin.

    The full run/derived-risk path is tested separately. No HTTP or synthetic
    successful risk measurement is introduced to make this lane test pass.
    """
    synthetic_clock(r,monkeypatch)
    async def run():
        async with httpx.AsyncClient() as client:
            candidate=app.assemble_candidate(r['store'],client,cfg,generation='lane-composition')
            ready(r,candidate.runtime.health)
            return evaluate(r,candidate.runtime.queue,candidate.runtime.evaluator.evaluator)
    return asyncio.run(run())


@pytest.mark.parametrize('strategy',['FUTURE_FORECAST','SAME_DAY_LATE_LOCK'])
def test_temperature_lanes_build_current_requests_and_keep_uncalibrated_bounds(factory,monkeypatch,strategy):
    r=factory(strategy)
    lane=app.TemperatureLane('temperature',inputs(r),(target(r),),'fixture',r['request'].valuation_policy,10.)
    result=evaluate_built_lane(r,scoped_plan(r,lane),monkeypatch)
    d=r['store'].get(result.result_ids[0])['body']['details']
    assert d['outcome']=='REJECT' and d['prediction']['calibration_status']=='UNCALIBRATED'
    assert d['request']['admission_id']!='pin' and not result.proposals


def test_pws_lane_retains_separate_observation_and_payout_models(joined,monkeypatch):
    r=joined;state_for(r,('book2',),('model2','anchor','pws'))
    lane=app.PWSLeadLane('pws',inputs(r,r['payout_kw']),(target(r),),'fixture',r['request'].valuation_policy,10.,
        inputs(r,r['observation_kw']),(SourceSelector('MODEL','fixture','ablation-model',120.),),r['lead_kw']['policy'])
    result=evaluate_built_lane(r,scoped_plan(r,lane),monkeypatch)
    d=r['store'].get(result.result_ids[0])['body']['details']
    assert d['outcome']=='REJECT' and not result.proposals,d
    pair=r['store'].get(d['request']['preconfirmation_id'])['body']['details']['assessment']
    assert pair['observation_bundle_sha256']!=pair['payout_bundle_sha256']
    assert d['executable_exit_value'] is None


@pytest.mark.parametrize('strategy',['SOURCE_SHOCK','RELEASE_OPPORTUNITY'])
def test_release_lanes_resolve_actual_receipts_and_do_not_invent_finality(release_factory,monkeypatch,strategy):
    r=release_factory(strategy)
    kw=dict(r['admission_kw'],scope=r['scope'],source_leases=(SourceLease('recomputed-model','MODEL',120.),
        SourceLease('received-release','OFFICIAL',120.),SourceLease('release-coverage','FEATURES',120.)))
    lane=app.SourceReleaseLane('release',inputs(r,kw),(target(r),),'fixture',r['request'].valuation_policy,10.)
    result=evaluate_built_lane(r,scoped_plan(r,lane),monkeypatch)
    d=r['store'].get(result.result_ids[0])['body']['details']
    assert d['outcome']=='REJECT' and not result.proposals,d
    release=r['store'].get(d['request']['source_release_id'])['body']['details']['assessment']
    assert release['current_official_id']=='received-release' and release['schedule'] is None
    assert not release['settlement_finality']


def test_exit_lane_uses_existing_inventory_and_does_not_create_a_fill(rig,monkeypatch):
    inventory(rig);c=coordinator(rig);before=c._head()
    lane=app.ExitLane('exit',inputs(rig),(target(rig,units='1'),),'fixture',rig['request'].valuation_policy,10.,
        costs(HOLD_RISKS),costs(SALE_RISKS,SALE),'NET_SALE_EXCEEDS_HOLD')
    result=evaluate_built_lane(rig,scoped_plan(rig,lane),monkeypatch)
    d=rig['store'].get(result.result_ids[0])['body']['details']
    assert d['outcome']=='REDUCE_RESEARCH_CANDIDATE' and len(result.proposals)==1,d
    assert c._head()==before and not c._state(before)['realized_entries']
    assert d['proposal']['valuation_id'] and not d['actual_inventory_changed']


def test_special_exit_scope_cannot_silently_skip_required_pws_join(joined,monkeypatch):
    r=joined;synthetic_clock(r,monkeypatch)
    lane=app.ExitLane('pws-exit',inputs(r,r['payout_kw']),(target(r),),'fixture',r['request'].valuation_policy,10.,
        costs(HOLD_RISKS),costs(SALE_RISKS,SALE),'NET_SALE_EXCEEDS_HOLD')
    before=r['store'].pin_read_view()
    async def run():
        async with httpx.AsyncClient() as client:
            with pytest.raises(EvidenceError,match='SPECIAL_EXIT_JOIN_NOT_CONFIGURED'):
                app.assemble_candidate(r['store'],client,scoped_plan(r,lane),generation='unconfigured')
    asyncio.run(run());assert r['store'].pin_read_view()==before


def test_typed_builder_runs_census_derived_risk_strategies_discovery_and_audit_in_one_candidate(rig,monkeypatch):
    cfg=plan(rig);synthetic_clock(rig,monkeypatch);calls=[]
    async def run():
        async with httpx.AsyncClient(transport=transport(rig,calls)) as client:
            before=rig['store'].pin_read_view();candidate=app.assemble_candidate(rig['store'],client,cfg,generation='composed')
            assert rig['store'].pin_read_view()==before and not calls
            ready(rig,candidate.runtime.health)
            result=await candidate.run('composed');head=candidate.runtime.coordinator._head()
            assert await candidate.run('composed')==result and candidate.runtime.coordinator._head()==head
            return candidate,result['body']['details']
    candidate,d=asyncio.run(run())
    assert [job['kind'] for job in d['worker_results']]==['CENSUS','DISCOVERY','AUDIT']
    assert len(calls)==8 and all(req.method=='GET' for req in calls)
    assert d['all_async_jobs_drained'] and not d['forward_acceptance'] and not d['financial_authority']
    assert candidate.assembly_sha256 and candidate.runtime.evaluator.config
    rows=rig['store'].records(kind='MEASUREMENT',limit=1000)
    assert any(r['body']['details'].get('version')==RISK_VERSION for r in rows)
    risk=candidate.runtime.coordinator.snapshot()
    assert risk['reserved_cash']=='0' and not rig['store'].records(kind='TRADE')
    assert not candidate.runtime.queue.snapshot()['needs_census']


def test_missing_real_clock_verification_causes_no_public_requests(rig,monkeypatch):
    cfg=replace(plan(rig),candidate=CandidatePolicy('clock',maximum_seconds=.1,maximum_jobs=1,maximum_safety_ticks=2,
        safety_interval_seconds=.05,job_timeout_seconds=.1,minimum_job_spacing_seconds=.05))
    synthetic_clock(rig,monkeypatch);calls=[]
    async def run():
        async with httpx.AsyncClient(transport=transport(rig,calls)) as client:
            candidate=app.assemble_candidate(rig['store'],client,cfg,generation='clock-unknown')
            rig['sync'][0]=False
            return await candidate.run('unknown-clock')
    d=asyncio.run(run())['body']['details']
    assert not calls and not d['worker_results'] and not d['financial_authority']


def test_candidate_collects_madis_then_quality_checks_and_routes_it(rig,setup,monkeypatch):
    from test_v11_pws_runtime import xml
    from test_v11_pws_quality import policy as pws_policy
    r=rig;cfg=plan(r);e=r['context'].event_id;meta=setup[3];calls=[]
    request=madis_request(event_id=e,station=meta.station,latitude=meta.latitude,longitude=meta.longitude)
    batch=ObservationBatch((request,),((e,meta.station),),(r['scope'].strategy,),
        ((r['scope'].strategy,('NOAA_MADIS_CWOP',)),))
    cfg=replace(cfg,pws_quality=PWSQualitySettings((PWSQualityPlan(e,meta,pws_policy()),)),observation=batch,
        candidate=replace(cfg.candidate,maximum_jobs=5,maximum_seconds=8.,maximum_safety_ticks=128),
        trigger=replace(cfg.trigger,pws_station_age_seconds=((meta.station,600.),),
                        source_age_seconds=tuple((k,600. if k=='PWS_OBSERVATION' else t) for k,t in cfg.trigger.source_age_seconds)))
    synthetic_clock(r,monkeypatch)
    base=transport(r,calls)
    def public(req):
        if req.url.host=='madis-data.ncep.noaa.gov':
            calls.append(req)
            content=xml(r['now'][0]).replace('lat="33.01"','lat="33.64"').replace('lat="33.03"','lat="33.66"').replace('lat="33.05"','lat="33.68"').replace('lon="-84"','lon="-84.44"')
            return httpx.Response(200,content=content)
        return base.handle_request(req)
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(public)) as client:
            candidate=app.assemble_candidate(r['store'],client,cfg,generation='pws-composed')
            ready(r,candidate.runtime.health)
            row=await candidate.run('pws-composed')
            return candidate,row['body']['details']
    candidate,d=asyncio.run(run())
    assert [j['kind'] for j in d['worker_results']]==['CENSUS','DISCOVERY','AUDIT','OBSERVATION','PWS_QUALITY'],d
    assert d['worker_results'][-1]['outcome']=='PWS_QC_RECORDED',d
    qc=r['store'].latest_source(kind='PWS_OBSERVATION',event_id=e,provider='ALPHA_PWS_QC',source_identity=meta.station)
    assert qc['body']['payload']['health']=='HEALTHY' and not qc['body']['payload']['lead_advantage_verified']
    routed=r['store'].get('feed-route:'+app.digest(qc['id']))
    assert routed['body']['details']['result']['affected_events']==[e]
    assert len(calls)==9 and all(req.method=='GET' for req in calls)
    assert candidate.runtime.coordinator.snapshot()['reserved_cash']=='0' and not r['store'].records(kind='TRADE')
    assert not d['forward_acceptance'] and not d['financial_authority']


def test_pws_plan_cannot_use_same_named_station_with_different_metadata(rig,setup):
    from test_v11_pws_quality import policy as pws_policy
    before=rig['store'].pin_read_view()
    with pytest.raises(EvidenceError,match='PWS_QUALITY_SCOPE'):
        replace(plan(rig),pws_quality=PWSQualitySettings((PWSQualityPlan(rig['context'].event_id,
            replace(setup[3],elevation_m=400.),pws_policy()),)))
    assert rig['store'].pin_read_view()==before


def test_census_and_periodic_qc_cannot_silently_alternate_policies(rig,setup):
    from test_v11_pws_quality import policy as pws_policy
    cfg=plan(rig);p=PWSQualityPlan(rig['context'].event_id,setup[3],pws_policy())
    event=replace(cfg.events[0],census=replace(cfg.events[0].census,pws=p))
    with pytest.raises(EvidenceError,match='PWS_QUALITY_SCOPE'):
        replace(cfg,events=(event,),pws_quality=PWSQualitySettings((replace(p,policy=pws_policy(fresh_seconds=300.)),)))


def test_candidate_normalizes_existing_forecast_without_new_network_or_unrelated_census_failure(rig,setup,monkeypatch):
    from test_weather_only_forecast import _payload
    from polymarket_scanner.v11.runtime_feed import EvidenceFeed
    r=rig;cfg=plan(r);fp=ForecastPlan(r['rule'],setup[3],50.,600.);calls=[];payload=_payload(family=r['rule'].payload['family'])
    payload['daily']['time']=[r['rule'].payload['target_date']]
    payload.update(latitude=setup[3].latitude,longitude=setup[3].longitude)
    r['store'].capture('archived-gefs',event_id=fp.event_id,kind='MODEL',provider=FORECAST_PROVIDER,source_identity=fp.source_identity,
        revision='archive',payload=dict(response=payload,request_url=OPEN_METEO_ENSEMBLE,request_params=request_parameters(fp),
                                       source_time_status='NOT_YET_NORMALIZED'),evidence_class='SYNTHETIC')
    cfg=replace(cfg,forecasts=(fp,),candidate=replace(cfg.candidate,maximum_jobs=4))
    synthetic_clock(r,monkeypatch)
    async def run():
        async with httpx.AsyncClient(transport=transport(r,calls)) as client:
            candidate=app.assemble_candidate(r['store'],client,cfg,generation='forecast-composed')
            ready(r,candidate.runtime.health)
            return candidate,(await candidate.run('forecast-composed'))['body']['details']
    candidate,d=asyncio.run(run())
    assert [j['kind'] for j in d['worker_results']]==['CENSUS','DISCOVERY','AUDIT','FORECAST_NORMALIZATION'],d
    assert d['worker_results'][-1]['outcome']=='FORECAST_MEMBERS_ARCHIVED_RUN_UNVERIFIED'
    assert len(calls)==8 and all(req.url.host!='ensemble-api.open-meteo.com' for req in calls)
    normalized=r['store'].latest_source(kind='MODEL',event_id=fp.event_id,provider=FORECAST_PROVIDER,source_identity=fp.source_identity)
    assert normalized['body']['issued_at'] is None and not d['forward_acceptance']
    assert EvidenceFeed._classification(normalized)=='FORECAST_RUN_PROVENANCE_REQUIRED'
    assert not candidate.runtime.queue.snapshot()['needs_census']
    assert not r['store'].records(kind='TRADE') and candidate.runtime.coordinator.snapshot()['reserved_cash']=='0'


def test_authenticated_client_is_rejected_before_any_candidate_write(rig):
    cfg=plan(rig);before=rig['store'].pin_read_view()
    async def run():
        async with httpx.AsyncClient(headers={'Authorization':'SYNTHETIC_FORBIDDEN_HEADER'}) as client:
            with pytest.raises(EvidenceError,match='ANONYMOUS_PUBLIC_CLIENT_REQUIRED'):
                app.assemble_candidate(rig['store'],client,cfg,generation='blocked')
    asyncio.run(run());assert rig['store'].pin_read_view()==before


def test_existing_account_policy_conflict_does_not_reset_or_replace_account(rig,monkeypatch):
    reserve(rig);cfg=plan(rig);before=coordinator(rig)._head();synthetic_clock(rig,monkeypatch)
    cfg=replace(cfg,account=replace(cfg.account,per_intent_cash_limit='5'))
    async def run():
        async with httpx.AsyncClient() as client:
            with pytest.raises(EvidenceError,match='ACCOUNT_POLICY_OR_IDENTITY_CHANGED'):
                app.assemble_candidate(rig['store'],client,cfg,generation='changed')
    asyncio.run(run());assert coordinator(rig)._head()==before


def test_existing_pending_queue_is_preserved_when_reconfiguration_is_refused(rig,monkeypatch):
    cfg=plan(rig);q=queue(rig);q.stream_gap('existing-gap',event_id=rig['context'].event_id,reason='SYNTHETIC_GAP')
    before=q.snapshot();synthetic_clock(rig,monkeypatch);cfg=replace(cfg,trigger=replace(cfg.trigger,max_sources_per_event=9))
    async def run():
        async with httpx.AsyncClient() as client:
            with pytest.raises(EvidenceError,match='CONFIGURATION_CHANGED_REQUIRES_RECONCILIATION'):
                app.assemble_candidate(rig['store'],client,cfg,generation='changed')
    asyncio.run(run());assert q.snapshot()==before


@pytest.mark.parametrize('defect',['duplicate_event','duplicate_lane','proposal_cap','wrong_worker','wrong_account'])
def test_typed_candidate_rejects_inconsistent_or_overlarge_plan_before_running(rig,defect):
    cfg=plan(rig);event=cfg.events[0];lane=event.lanes[0]
    with pytest.raises(EvidenceError):
        if defect=='duplicate_event':replace(cfg,events=(event,event))
        elif defect=='duplicate_lane':replace(event,lanes=(lane,lane))
        elif defect=='proposal_cap':replace(event,lanes=(replace(lane,maximum_proposals=6),replace(lane,name='second')))
        elif defect=='wrong_worker':replace(cfg,worker_id='unconfigured')
        else:replace(cfg,account=replace(cfg.account,account_id='another-account'))
