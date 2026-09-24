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
