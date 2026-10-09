"""Offline, no-network proof for the KATL economic-only template patch (req8 unblock).

`deploy/katl_continuous_shadow_economic.patch` is reviewed here, not executed
against the real host. The host template text is reproduced verbatim below
and pinned by `sha256` against the exact preimage checksum recorded in
`docs/V11_KATL_LIVE_PLAN_DEPLOYMENT_HANDOFF_20261007.md`
(`cb6862fdfa3eddad134d70ecc8d4061d29726751e006639ffaa7156713b053f4`), so patch
application can be proven without opening any file under
`AlphaV11_ForwardShadow` (protected; never read or modified by this suite).

These tests never touch a `*.sqlite` EvidenceStore and make no network call.
The `economic_plan`/`joined`/`setup`/`factory`/`hourly_rig` fixtures are the
same offline fixtures `test_v11_katl_live_plan.py` and
`test_v11_settlement_window.py` already use for the real
`katl_live_plan`/`settlement_window` admission/risk code -- nothing here is
mocked out.
"""
import pathlib
import subprocess

from polymarket_scanner.v11 import katl_live_plan
from polymarket_scanner.v11.candidate_assembly import TemperatureLane
from polymarket_scanner.v11.evidence import digest, sha
from polymarket_scanner.v11.settlement_window import (
    ACCEPTED_OBSERVATION_POPULATION, BASIS, SettlementWindowPolicy, VERSION as SW_VERSION,
    derive_time_to_observation_close,
)

from test_v11_katl_live_plan import economic_plan, main_sources_for
from test_v11_pws_admission import coordinator, joined
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_settlement_window import hourly_rig
from test_v11_strategy_pipeline import factory


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PATCH_PATH = REPO_ROOT / 'deploy' / 'katl_continuous_shadow_economic.patch'
PREIMAGE_SHA256 = 'cb6862fdfa3eddad134d70ecc8d4061d29726751e006639ffaa7156713b053f4'

# Verbatim preimage of /home/alphaadmin/AlphaV11_ForwardShadow/continuous-shadow-v2/
# katl_continuous_shadow.py, reproduced here (never read live by this test) so
# the patch can be proven to apply without opening the protected host tree.
TEMPLATE_PREIMAGE = r'''#!/usr/bin/env python3
from __future__ import annotations
import asyncio, json, time
from dataclasses import asdict
from pathlib import Path
import httpx

ROOT=Path('/home/alphaadmin/AlphaV11_ForwardShadow/continuous-shadow-v2')
DB=ROOT/'alpha-shadow.sqlite'
NAMESPACE='CHALLENGER:katl-shadow'
RELEASE='0dd809ea4b42cce9026225e390856509d0b2041c'
TREE='92c5bde0415e7eeeb76c9340f14d4ccba39fd1ae'
EVENT_ID='1118070'
WORKER='shadow-katl-worker'
ACCOUNT='shadow-katl-account'
COLLATERAL='USDC'

from polymarket_scanner.v11.evidence import EvidenceStore,Limits,ReleaseBinding,digest
from polymarket_scanner.v11.certification import StationMetadata,CapabilityScope
from polymarket_scanner.v11.rules import RuleFingerprint
from polymarket_scanner.v11.event_risk import EventContext,EventPolicy,StateGuard
from polymarket_scanner.v11.scenario_risk import StationMembership,CorrelationMap,ScenarioLimits
from polymarket_scanner.v11.paper_coordinator import PaperAccountPolicy
from polymarket_scanner.v11.event_queue import EventRoute,TriggerPolicy,KINDS
from polymarket_scanner.v11.runtime_health import HealthPolicy
from polymarket_scanner.v11.paper_runtime import RuntimePolicy
from polymarket_scanner.v11.candidate_runner import CandidatePolicy
from polymarket_scanner.v11.census_worker import CensusPlan,CensusPolicy
from polymarket_scanner.v11.book_inputs import BookPolicy,PROVIDER as BOOK_PROVIDER
from polymarket_scanner.v11.discovery import DiscoveryPolicy
from polymarket_scanner.v11.audit_reports import AuditPolicy
from polymarket_scanner.v11.runtime_feed import FeedPolicy
from polymarket_scanner.v11.microstructure import MicrostructurePolicy
from polymarket_scanner.v11.risk_inputs import RiskInputPolicy
from polymarket_scanner.v11.valuation import ValuationPolicy
from polymarket_scanner.v11.request_assembly import ScopeInputs,SourceSelector,TargetPlan
from polymarket_scanner.v11.candidate_assembly import TemperatureLane,CandidateEvent,CandidatePlan,assemble_candidate
from polymarket_scanner.v11.forecast_sources import ForecastPlan
from polymarket_scanner.v11.gefs_sources import GEFSPlan,MODEL_ID,PROVIDER as GEFS_PROVIDER
from polymarket_scanner.v11.forecast_features import ForecastFeatureContract
from polymarket_scanner.v11.candidate_cohort import candidate_cohort
from polymarket_scanner.v11.model_registry import ActiveModelRegistry
from polymarket_scanner.v11.shadow_commission import ShadowScopeTarget,ShadowCommissionPlan,PLAN_VERSION,ShadowLifecyclePolicy,ShadowCommissionRunner

def load_context(store):
    md=store.get('decision-shadow:station:KATL:metadata')['body']['details']['metadata']
    md=dict(md); md['observation_providers']=tuple(md['observation_providers']); md['forecast_providers']=tuple(md['forecast_providers'])
    metadata=StationMetadata(**md)
    rd=store.get('decision-shadow:rule:1118070')['body']['details']
    rule=RuleFingerprint(json.dumps(rd['preimage'],sort_keys=True,separators=(',',':')),rd['fingerprint'],rd['source_event_sha256'])
    # Constructor requires the exact canonical preimage used by the fingerprint.
    from polymarket_scanner.v11.evidence import canonical
    rule=RuleFingerprint(canonical(rd['preimage']),rd['fingerprint'],rd['source_event_sha256'])
    scope=CapabilityScope('KATL','HIGH','NWS_WRH_TIMESERIES','v11-exact-day-historical-v2','D0','FUTURE_FORECAST','FALL','ALL_DAY')
    return metadata,rule,scope

def smoke_event_policy():
    return EventPolicy('shadow-smoke-engineering-v1',
        60.,120.,60., 2.,5., 600.,1200., .01,.05, .1,.2, .2,.5, .1,.3, .5,.9,
        3,.1,60., 3,20.,10.,
        StateGuard(1,0,1,1,30),
        StateGuard(.5,.01,.5,2,10),
        StateGuard(.2,.02,.2,3,5),
        StateGuard(.5,.01,.5,2,10))

def build_plan(store):
    metadata,rule,scope=load_context(store)
    now=store.clock(); p=rule.payload
    static=json.loads((ROOT/'candidate-static-config.json').read_text())
    context=EventContext(ACCOUNT,p['city'] or 'atlanta','KATL',EVENT_ID)
    binding_cfg={'purpose':'KATL_CONTINUOUS_DECISION_SHADOW_V1','namespace':NAMESPACE,'release':RELEASE,
                 'economic_policy':'SMOKE_ONLY_NOT_LIVE_ACCEPTED'}
    binding=ReleaseBinding(RELEASE,TREE,digest(binding_cfg),
        'fd9a32aa12ac7c8018ec524d1b74514bb57c42c0e60241672a4446b95908e641',rule.sha256)
    source=SourceSelector('MODEL',GEFS_PROVIDER,'GEFS_LINEAR:KATL:2026-10-04:daily_high_temperature',86400.)
    inputs=ScopeInputs(context,scope,rule,binding,'SHADOW',43200.,(source,))
    first=p['partition'][0]
    target=TargetPlan(first['market_id'],'YES','1','1',())
    valuation=ValuationPolicy('shadow-smoke-engineering-v1',COLLATERAL,30.,120.,'1','1')
    lane=TemperatureLane('future-forecast-smoke',inputs,(target,),BOOK_PROVIDER,valuation,10.)

    route=EventRoute(EVENT_ID,'KATL',p['target_date'],p['family'],rule.sha256,
        tuple(t for b in p['partition'] for t in (b['yes_token'],b['no_token'])),
        static['route_valid_until'],('MODEL',))
    census=CensusPlan(rule,COLLATERAL,False,None)
    micro=MicrostructurePolicy('shadow-smoke-engineering-v1',COLLATERAL,30.,60.,5.,.01,2)
    risk=RiskInputPolicy('shadow-smoke-engineering-v1',micro,smoke_event_policy())
    event=CandidateEvent(route,census,inputs,BOOK_PROVIDER,valuation,risk,(lane,))

    account=PaperAccountPolicy('shadow-smoke-engineering-v1',ACCOUNT,COLLATERAL,
        '1','1','0.01','0.01','1','0.99',60.,1)
    membership=StationMembership('KATL',p['city'] or 'atlanta','ATLANTA',
        ('SINGLE_STATION_WEATHER',),('NOAA_PUBLIC_SOURCES',),('NOAA_GEFS',),p['metadata_fingerprint'])
    corr_preimage={'policy':'SINGLE_STATION_NO_INDEPENDENCE_CREDIT','station':'KATL','metadata_fingerprint':p['metadata_fingerprint']}
    correlation=CorrelationMap('shadow-smoke-engineering-v1',digest(corr_preimage),(membership,))
    limits=ScenarioLimits('shadow-smoke-engineering-v1','.01','.01','.01','.01','.01','.01','.01','1')

    source_ages={k:300. for k in KINDS}
    source_ages.update({'BOOK':60.,'OFFICIAL_OBSERVATION':300.,'PWS_OBSERVATION':300.,
                        'MODEL':86400.,'RULES':43200.,'STATION_METADATA':86400.,'LABEL':86400.})
    trigger=TriggerPolicy('shadow-smoke-engineering-v1',4,1,32,64,1800.,30.,200000,43200.,
        tuple((k,source_ages[k]) for k in sorted(KINDS)),(('KATL',300.),))
    health=HealthPolicy('shadow-katl-health-v1',30.,30.,2.,2,.05,(WORKER,))
    runtime=RuntimePolicy('shadow-continuous-engineering-v1',32,1,2,20.,3600.,30.)
    candidate=CandidatePolicy('shadow-continuous-engineering-v1',120.,12,256,.5,30.,.1,300.)
    cp=CensusPolicy('shadow-smoke-engineering-v1',20.,30.,63)
    books=BookPolicy('shadow-smoke-engineering-v1',30.,1000)
    discovery=DiscoveryPolicy('shadow-smoke-engineering-v1',page_size=10,maximum_pages=1,
        maximum_event_hits=100,maximum_events_per_step=8,maximum_processing_seconds=2.,
        maximum_scan_seconds=300.,maximum_page_age_seconds=300.,maximum_metadata_age_seconds=86400.,
        maximum_page_failures=3)
    audits=AuditPolicy('shadow-smoke-engineering-v1',records_per_step=64,maximum_step_seconds=2.,maximum_job_records=1000)
    feed=FeedPolicy('shadow-smoke-engineering-v1',48,32,3.)
    forecast=ForecastPlan(rule,metadata,50.,86400.)
    gefs=GEFSPlan(forecast,1791072000,86400.)
    plan=CandidatePlan('shadow-continuous-engineering-v1',account,correlation,limits,(event,),trigger,health,runtime,
        candidate,cp,books,discovery,audits,feed,WORKER,gefs=(gefs,))
    return plan,metadata,rule,scope,binding,gefs

def build_runner(client):
    store=EvidenceStore(DB,NAMESPACE,limits=Limits(max_database_bytes=512 * 1024 * 1024))
    plan,metadata,rule,scope,binding,gefs=build_plan(store)
    runner=assemble_candidate(store,client,plan,generation='katl-continuous-shadow-v1')
    contract=ForecastFeatureContract(((MODEL_ID,31),),'F','daily_high_temperature')
    expected=json.loads((ROOT/'candidate-static-config.json').read_text())
    model_epoch=expected['model_epoch']
    model_state_sha256=expected['model_state_sha256']
    try:
        model=ActiveModelRegistry().pin(scope_key=scope.key,mode='V11_SHADOW')
    except Exception:
        model=None
    if model is not None:
        contract.require_bundle(model.bundle)
        if model.bundle.sha256!=expected['bundle_sha256'] or model.epoch!=model_epoch or model.state_sha256!=model_state_sha256:
            raise RuntimeError('INSTALLED_CORRECT_SCOPE_MODEL_DIFFERS_FROM_STAGED_REVIEW')
    target=ShadowScopeTarget(scope,'F',1,EVENT_ID,model_epoch,model_state_sha256,contract)
    shadow=ShadowCommissionPlan(PLAN_VERSION,NAMESPACE,WORKER,RELEASE,expected['shadow_plan_created_at'],
        'KATL continuous decision Shadow; nonfinancial and uncalibrated', (target,),candidate_cohort(runner))
    return store,plan,runner,shadow,gefs

async def inspect():
    async with httpx.AsyncClient(headers={'User-Agent':'Alpha-V11-KATL-decision-shadow/1'},
                                 timeout=10.0,follow_redirects=False) as client:
        store,plan,runner,shadow,gefs=build_runner(client)
        out={'candidate_config_sha256':runner.config,'assembly_sha256':runner.assembly_sha256,
             'shadow_plan_key':shadow.key,'cohort_key':shadow.cohort.key,
             'gefs_hours':list(gefs.hours),'gefs_fields':31*len(gefs.hours),
             'namespace':store.namespace,'database':str(store.path),
             'financial_authority':False,'economic_policy_status':'SMOKE_ONLY_NOT_LIVE_ACCEPTED'}
        (ROOT/'candidate-plan-status.json').write_text(json.dumps(out,indent=2,sort_keys=True)+'\n')
        print(json.dumps(out,sort_keys=True))

if __name__=='__main__':
    asyncio.run(inspect())
'''


def test_preimage_sha256_matches_the_recorded_host_checksum():
    import hashlib
    assert hashlib.sha256(TEMPLATE_PREIMAGE.encode()).hexdigest() == PREIMAGE_SHA256
    assert sha(PREIMAGE_SHA256) == PREIMAGE_SHA256


def _apply_patch(tmp_path):
    template = tmp_path / 'katl_continuous_shadow.py'
    template.write_text(TEMPLATE_PREIMAGE)
    patch_text = PATCH_PATH.read_text()
    dry = subprocess.run(['patch', '--dry-run', '-p1', '--directory', str(tmp_path)],
                         input=patch_text, capture_output=True, text=True)
    assert dry.returncode == 0, dry.stderr
    applied = subprocess.run(['patch', '-p1', '--directory', str(tmp_path)],
                             input=patch_text, capture_output=True, text=True)
    assert applied.returncode == 0, applied.stderr
    return template.read_text()


def test_patch_applies_cleanly_to_a_verified_preimage_copy_and_selects_economic_only(tmp_path):
    patched = _apply_patch(tmp_path)
    # Still valid Python; the patched file is never executed against a real
    # store here (that needs the protected host DB/config, out of scope).
    compile(patched, 'katl_continuous_shadow.py', 'exec')
    assert 'from polymarket_scanner.v11.katl_live_plan import commission_targets, upgrade_host_plan' in patched
    assert 'from polymarket_scanner.v11.settlement_window import SettlementWindowPolicy' in patched
    assert 'pws_config=None' in patched
    assert 'risk_settlement_window=SettlementWindowPolicy(SETTLEMENT_WINDOW_VERSION,86400.)' in patched
    assert 'commission_targets(plan,target,pws_config)' in patched
    # The economic-only lane must not depend on the still-missing per-day PWS
    # artifact (handoff doc: "absent currently") -- that file read is gone.
    assert 'candidate-pws-config-' not in patched
    # risk_execution_health is never passed (no keyword call site at all) --
    # it stays unset -> EXECUTION_HEALTH_UNKNOWN, documented in the
    # commissioning note, not merely absent-by-omission of some other name.
    assert 'risk_execution_health=' not in patched


# ---------------------------------------------------------------------------
# Semantic proof: the exact upgrade_host_plan(pws_config=None,
# risk_settlement_window=...) call the patch performs really does build the
# economic + EventRisk lane, through the real katl_live_plan/candidate_
# assembly code (no mock of build_plan/upgrade_host_plan itself).
# ---------------------------------------------------------------------------

def test_economic_lane_builds_with_pws_config_none_and_the_patchs_settlement_window(joined, setup):
    r = joined
    base, _, _ = economic_plan(r, main_sources=main_sources_for(r))
    window = SettlementWindowPolicy(SW_VERSION, 86400.)  # the exact value the patch passes
    upgraded = katl_live_plan.upgrade_host_plan(
        base, official=setup[3], pws_config=None, risk_settlement_window=window)
    assert [type(l) for l in upgraded.events[0].lanes] == [TemperatureLane]
    assert upgraded.events[0].census.pws is None and upgraded.pws_quality is None
    assert 'PWS_OBSERVATION' not in upgraded.events[0].route.required_source_kinds
    assert upgraded.events[0].risk_settlement_window is window
    assert upgraded.events[0].risk_execution_health is None  # fail-closed: no sourced ObservationPolicy window
    assert upgraded.account == base.account and upgraded.correlation == base.correlation
    assert upgraded.limits == base.limits


# ---------------------------------------------------------------------------
# Semantic proof: that same SettlementWindowPolicy(SW_VERSION, 86400.) really
# derives a settlement window (not SETTLEMENT_WINDOW_UNKNOWN_OR_CLOSED) for a
# WRH_HOURLY_DATA rule at an in-window instant, and stays fail-closed
# (UNKNOWN, typed reason) for any other rule family -- the real
# settlement_window.derive_time_to_observation_close code path, not a stub.
# ---------------------------------------------------------------------------

def test_patchs_settlement_window_policy_derives_in_window_for_the_accepted_rule_family(hourly_rig):
    r = hourly_rig
    assert r['rule'].payload['observation_population'] == ACCEPTED_OBSERVATION_POPULATION
    window = SettlementWindowPolicy(SW_VERSION, 86400.)
    result = derive_time_to_observation_close(
        r['store'], event_id=r['rule'].payload['event_id'], rule_fingerprint=r['rule'].sha256,
        at=r['now'][0], policy=window)
    assert result.status == 'DERIVED', result
    assert result.basis == BASIS
    assert result.seconds is not None and result.seconds > 0
    assert result.close_utc is not None


def test_patchs_settlement_window_policy_stays_fail_closed_for_an_unsupported_rule_family(factory):
    r = factory('FUTURE_FORECAST')
    assert r['rule'].payload['observation_population'] != ACCEPTED_OBSERVATION_POPULATION
    window = SettlementWindowPolicy(SW_VERSION, 86400.)
    result = derive_time_to_observation_close(
        r['store'], event_id=r['rule'].payload['event_id'], rule_fingerprint=r['rule'].sha256,
        at=r['now'][0], policy=window)
    assert result.status == 'UNKNOWN'
    assert result.reason == 'SETTLEMENT_WINDOW_RULE_FAMILY_UNSUPPORTED'
    assert result.seconds is None and result.close_utc is None
