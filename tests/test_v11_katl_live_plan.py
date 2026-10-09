"""Focused no-network tests for the KATL economic plan builder (req8/req9).

These exercise polymarket_scanner.v11.katl_live_plan.build_plan through the
real, unmodified admission/risk/scenario-reservation code -- no mocked gate,
no smoke shortcut. Only the final EV/price threshold is ever synthesized
(same convention as test_v11_pws_admission.synthetic_proposal), and only to
demonstrate the scenario-reservation path; every upstream freshness,
certification and model-pin check runs for real.
"""
import asyncio
from copy import deepcopy
from dataclasses import asdict, replace

import httpx
import pytest

from polymarket_scanner.v11 import candidate_assembly as app
from polymarket_scanner.v11 import certification, katl_live_plan
from polymarket_scanner.v11.candidate_cohort import candidate_cohort
from polymarket_scanner.v11.candidate_assembly import PWSLeadLane, TemperatureLane
from polymarket_scanner.v11.evidence import EvidenceError, canonical, digest
from polymarket_scanner.v11.forecast_features import ForecastFeatureContract
from polymarket_scanner.v11.paper_coordinator import Proposal
from polymarket_scanner.v11.request_assembly import SourceSelector
from polymarket_scanner.v11.shadow_commission import (PLAN_VERSION, PinnedFeatureContract,
                                                       ShadowCommissionPlan, ShadowScopeTarget, load_plan)
from polymarket_scanner.v11.scenario_risk import Attribution
from polymarket_scanner.v11.valuation import CostComponent, ENTRY_RISKS, PAYOUT, contract_target

from test_v11_candidate_assembly import synthetic_clock
from test_v11_certification_rules import approve_fixture, setup
from test_v11_model_artifacts import bundle
from test_v11_pws_admission import coordinator, joined
from test_v11_pws_quality import policy as pws_policy
from test_v11_request_assembly import evaluate
from test_v11_runtime_health import ready
from test_v11_strategy_pipeline import factory


COLLATERAL = 'FIXTURE_COLLATERAL'


def economic_plan(r, *, main_sources=None, pws=None, temperature_costs=(),
                  use_default_settlement_window=False):
    model = r['store'].get('model2')['body']
    reviewed_risk = coordinator(r)
    return katl_live_plan.build_plan(
        context=r['context'], scope=r['scope'], rule=r['rule'],
        metadata_fingerprint=r['rule'].payload['metadata_fingerprint'], model=model,
        route_valid_until=r['now'][0] + 10 * 86400, release='a' * 40, tree='b' * 40,
        bundle_sha256=r['binding'].bundle_sha256, collateral=COLLATERAL, worker='worker', stage='PAPER',
        book_provider='fixture', account_policy=reviewed_risk.policy,
        correlation=reviewed_risk.correlation, scenario_limits=reviewed_risk.limits,
        main_sources=main_sources, pws=pws, temperature_costs=temperature_costs,
        **({} if use_default_settlement_window else {'risk_settlement_window': None}))


def selectors(r, source_leases):
    result = []
    for lease in source_leases:
        body = r['store'].get(lease.evidence_id)['body']
        result.append(SourceSelector(lease.role, body['provider'], body['source_identity'], lease.maximum_age_seconds))
    return tuple(result)


def pws_sleeve(r, metadata):
    ablation = r['store'].get('ablation-model')['body']
    return katl_live_plan.PWSSleeve(
        official=metadata, quality_policy=pws_policy(),
        payout_scope=r['payout_scope'], observation_scope=r['observation_scope'],
        observation_bundle_sha256=r['observation_kw']['binding'].bundle_sha256,
        payout_sources=selectors(r, r['payout_kw']['source_leases']),
        observation_sources=selectors(r, r['observation_kw']['source_leases']),
        without_pws_sources=(SourceSelector('MODEL', ablation['provider'], ablation['source_identity'], 120.),),
        lead_policy=r['lead_kw']['policy'])


def capture_full_partition_books(r, revision='full-partition'):
    """The EventRiskInputs microstructure check needs a book for every bucket
    and side in the whole event, not just the lane's own target; the live
    CensusWorker fetches all of them (`_requests()` loops `route.tokens`),
    this fixture's own `factory()` captures only the lane's one target."""
    store, rule = r['store'], r['rule']
    for bucket in rule.payload['partition']:
        for side in ('YES', 'NO'):
            contract = contract_target(rule, bucket['market_id'], side)
            key = 'book-full:' + digest([bucket['market_id'], side])
            store.capture(key, event_id=r['context'].event_id, kind='BOOK', provider='fixture',
                         source_identity=contract['token_id'], revision=revision, observed_at=r['now'][0],
                         evidence_class='SYNTHETIC', payload=dict(contract, rule_fingerprint=rule.sha256,
                         collateral_asset=COLLATERAL, stream_healthy=True,
                         bids=[{'price': '.1', 'size': '20'}], asks=[{'price': '.2', 'size': '20'}]))


def main_sources_for(r):
    """SAME_DAY_LATE_LOCK (used by the `joined` fixture) needs OFFICIAL+FEATURES
    leases too, unlike the live FUTURE_FORECAST lane's single MODEL selector."""
    return selectors(r, r['admission_kw']['source_leases'])


# ---------------------------------------------------------------------------
# Plan assembly / PWS sleeve wiring
# ---------------------------------------------------------------------------

def test_plan_assembles_and_tuning_stays_inside_the_microstructure_ceiling(factory):
    r = factory('FUTURE_FORECAST')
    plan, scope, model = economic_plan(r)
    assert scope is r['scope'] and model['provider'] == 'fixture'
    assert plan.pws_quality is None
    assert len(plan.events) == 1 and len(plan.events[0].lanes) == 1
    assert type(plan.events[0].lanes[0]) is TemperatureLane
    assert plan.events[0].census.pws is None
    # MicrostructurePolicy.maximum_book_age_seconds<=120 is a hard ceiling
    # enforced by that dataclass itself, not a tunable smoke value -- every
    # other book-freshness window here is set at or inside it.
    assert plan.events[0].risk_policy.microstructure.maximum_book_age_seconds <= 120
    assert plan.events[0].lanes[0].valuation.max_book_age_seconds <= 120
    assert plan.books.maximum_age_seconds <= 120
    # The live smoke script's maximum_jobs=1 starved the CensusWorker job
    # behind safety ticks; this plan gives it real budget.
    assert plan.candidate.maximum_jobs > 1
    assert plan.account == coordinator(r).policy
    assert plan.limits == coordinator(r).limits


def test_plan_construction_has_no_store_side_effects(factory):
    r = factory('FUTURE_FORECAST')
    before = r['store'].pin_read_view()
    economic_plan(r)
    assert r['store'].pin_read_view() == before


def test_pws_sleeve_wires_census_and_pws_quality_consistently(joined, setup):
    r = joined; metadata = setup[3]
    sleeve = pws_sleeve(r, metadata)
    plan, _, _ = economic_plan(r, main_sources=main_sources_for(r), pws=sleeve)
    event = plan.events[0]
    assert event.census.pws is not None
    assert plan.pws_quality.plans == (event.census.pws,)
    assert len(event.lanes) == 2
    kinds = {type(l) for l in event.lanes}
    assert kinds == {TemperatureLane, PWSLeadLane}
    pws_lane = next(l for l in event.lanes if type(l) is PWSLeadLane)
    assert pws_lane.inputs.scope.strategy == 'PWS_OBSERVATION_LEAD'
    assert pws_lane.observation_inputs.scope is r['observation_scope']
    assert 'PWS_OBSERVATION' in plan.events[0].route.required_source_kinds
    assert {s.role for s in event.risk_inputs.sources} >= {'MODEL', 'OFFICIAL', 'PWS'}
    assert {s.role for s in event.lanes[0].inputs.sources} == {'MODEL', 'OFFICIAL', 'FEATURES'}


def test_host_commission_targets_bind_distinct_model_identities(joined, setup):
    r = joined
    plan, _, _ = economic_plan(r, main_sources=main_sources_for(r), pws=pws_sleeve(r, setup[3]))
    contract = ForecastFeatureContract((('model-1', 1),), 'F', r['rule'].payload['family'])
    base = ShadowScopeTarget(r['scope'], 'F', 1, r['context'].event_id, 1, 'd'*64, contract)
    config = {'commission': {
        'payout': dict(model_epoch=2, model_state_sha256='e'*64, feature_contract=asdict(contract)),
        'observation': dict(model_epoch=3, model_state_sha256='f'*64,
                            feature_contract=asdict(replace(contract, prediction_target='NEXT_OFFICIAL_OBSERVATION')))}}
    targets = katl_live_plan.commission_targets(plan, base, config)
    assert len(targets) == 3
    assert {t.scope.key for t in targets} == {r['scope'].key, r['payout_scope'].key, r['observation_scope'].key}
    assert targets[2].feature_contract.prediction_target == 'NEXT_OFFICIAL_OBSERVATION'
    bad = deepcopy(config)
    bad['commission']['observation']['feature_contract']['prediction_target'] = 'FINAL_CONTRACT_PAYOUT'
    with pytest.raises(EvidenceError, match='KATL_PLAN_COMMISSION_FEATURE_TARGET_MISMATCH'):
        katl_live_plan.commission_targets(plan, base, bad)


def test_three_scope_cohort_constructs_with_exact_pinned_champions(joined, setup, bundle, tmp_path):
    r = joined
    plan, _, _ = economic_plan(r, main_sources=main_sources_for(r), pws=pws_sleeve(r, setup[3]))
    event = plan.events[0]
    lanes = tuple(replace(lane, inputs=replace(lane.inputs, stage='SHADOW'),
                          **({'observation_inputs': replace(lane.observation_inputs, stage='SHADOW')}
                             if isinstance(lane, PWSLeadLane) else {})) for lane in event.lanes)
    event = replace(event, risk_inputs=replace(event.risk_inputs, stage='SHADOW'), lanes=lanes)
    plan = replace(plan, events=(event,))
    def contract(bundle_sha, target):
        pinned = bundle[0].pin(bundle_sha)
        return PinnedFeatureContract('F', r['rule'].payload['family'], target,
                                     pinned.payload['bundle']['feature_schema_sha256'], ('model-1',))
    payout_bundle = r['payout_kw']['binding'].bundle_sha256
    observation_bundle = r['observation_kw']['binding'].bundle_sha256
    base = ShadowScopeTarget(r['scope'], 'F', 1, r['context'].event_id, 1, 'd'*64,
                             contract(payout_bundle, 'FINAL_CONTRACT_PAYOUT'))
    config = {'commission': {
        'payout': dict(model_epoch=2, model_state_sha256='e'*64,
                       feature_contract=asdict(contract(payout_bundle, 'FINAL_CONTRACT_PAYOUT'))),
        'observation': dict(model_epoch=3, model_state_sha256='f'*64,
                            feature_contract=asdict(contract(observation_bundle, 'NEXT_OFFICIAL_OBSERVATION')))}}
    targets = katl_live_plan.commission_targets(plan, base, config)
    async def assemble():
        async with httpx.AsyncClient() as client:
            return app.assemble_candidate(r['store'], client, plan, generation='katl-three-targets')
    cohort = candidate_cohort(asyncio.run(assemble()))
    shadow = ShadowCommissionPlan(PLAN_VERSION, 'CHALLENGER:fixture', 'worker', 'a'*40,
                                  r['now'][0], 'three protected targets', targets, cohort)
    assert len(shadow.targets) == 3
    for target, bundle_sha in ((targets[1], payout_bundle), (targets[2], observation_bundle)):
        target.feature_contract.require_bundle(bundle[0].pin(bundle_sha))
    path = tmp_path / 'commission-plan.json'
    path.write_text(canonical(asdict(shadow)))
    assert load_plan(path) == shadow


def test_observation_contract_checks_actual_bundle_target(joined, bundle):
    r = joined
    observation = bundle[0].pin(r['observation_kw']['binding'].bundle_sha256)
    contract = PinnedFeatureContract('F', r['rule'].payload['family'], 'NEXT_OFFICIAL_OBSERVATION',
                                     observation.payload['bundle']['feature_schema_sha256'], ('model-1',))
    contract.require_bundle(bundle[0].pin(r['observation_kw']['binding'].bundle_sha256))
    with pytest.raises(EvidenceError, match='SHADOW_PINNED_FEATURE_CONTRACT_MISMATCH'):
        contract.require_bundle(bundle[0].pin(r['payout_kw']['binding'].bundle_sha256))


def test_costs_are_target_specific_and_preserved(joined, setup):
    r = joined
    unknown = CostComponent('review-pending', PAYOUT, None, tuple(sorted(ENTRY_RISKS)), 'a'*64)
    sleeve = replace(pws_sleeve(r, setup[3]), payout_costs=(unknown,))
    plan, _, _ = economic_plan(r, main_sources=main_sources_for(r), pws=sleeve,
                               temperature_costs=(unknown,))
    assert plan.events[0].lanes[0].targets[0].costs == (unknown,)
    assert plan.events[0].lanes[1].targets[0].costs == (unknown,)
    base, _, _ = economic_plan(r, main_sources=main_sources_for(r), temperature_costs=(unknown,))
    config = asdict(sleeve); config.pop('official')
    upgraded = katl_live_plan.upgrade_host_plan(base, official=setup[3], pws_config=config)
    assert upgraded.events[0].lanes[0].targets[0].costs == (unknown,)
    assert upgraded.events[0].lanes[1].targets[0].costs == (unknown,)
    assert upgraded.events[0].risk_inputs.binding.config_sha256 != base.events[0].risk_inputs.binding.config_sha256


def test_host_accepts_reviewed_temperature_cost_input_without_erasing_base(joined, setup):
    r = joined
    base, _, _ = economic_plan(r, main_sources=main_sources_for(r))
    config = asdict(pws_sleeve(r, setup[3])); config.pop('official')
    unknown = CostComponent('temperature-pending', PAYOUT, None, tuple(sorted(ENTRY_RISKS)), 'b'*64)
    config['temperature_costs'] = [asdict(unknown)]
    upgraded = katl_live_plan.upgrade_host_plan(base, official=setup[3], pws_config=config)
    assert upgraded.events[0].lanes[0].targets[0].costs == (unknown,)
    assert upgraded.events[0].lanes[1].targets[0].costs == ()
    retained, _, _ = economic_plan(r, main_sources=main_sources_for(r), temperature_costs=(unknown,))
    config['temperature_costs'] = []
    with pytest.raises(EvidenceError, match='KATL_PLAN_HOST_TEMPERATURE_COST_CONFLICT'):
        katl_live_plan.upgrade_host_plan(retained, official=setup[3], pws_config=config)
    config['temperature_costs'] = [asdict(unknown)] * 17
    with pytest.raises(EvidenceError, match='KATL_PLAN_COST_CONFIG_BOUND'):
        katl_live_plan.upgrade_host_plan(base, official=setup[3], pws_config=config)


def test_pws_payout_requires_causal_features(joined, setup):
    r = joined
    sleeve = pws_sleeve(r, setup[3])
    with pytest.raises(EvidenceError, match='KATL_PLAN_PWS_PAYOUT_FEATURES_REQUIRED'):
        replace(sleeve, payout_sources=tuple(s for s in sleeve.payout_sources if s.role != 'FEATURES'))


def test_host_upgrade_requires_reviewed_pws_config_and_retains_account_limits(joined, setup):
    r = joined
    base, _, _ = economic_plan(r, main_sources=main_sources_for(r))
    sleeve = pws_sleeve(r, setup[3])
    config = asdict(sleeve)
    config.pop('official')
    upgraded = katl_live_plan.upgrade_host_plan(base, official=setup[3], pws_config=config)
    assert upgraded.account == base.account
    assert upgraded.correlation == base.correlation
    assert upgraded.limits == base.limits
    assert upgraded.events[0].census.pws is not None
    assert any(type(l) is PWSLeadLane for l in upgraded.events[0].lanes)
    with pytest.raises(EvidenceError, match='KATL_PLAN_PWS_CONFIG_SCHEMA'):
        katl_live_plan.upgrade_host_plan(base, official=setup[3], pws_config={})


def test_host_upgrade_without_pws_config_builds_economic_lane_only(joined, setup):
    r = joined
    base, _, _ = economic_plan(r, main_sources=main_sources_for(r))
    upgraded = katl_live_plan.upgrade_host_plan(base, official=setup[3], pws_config=None)
    assert [type(l) for l in upgraded.events[0].lanes] == [TemperatureLane]
    assert upgraded.events[0].census.pws is None and upgraded.pws_quality is None
    assert 'PWS_OBSERVATION' not in upgraded.events[0].route.required_source_kinds
    assert upgraded.account == base.account and upgraded.limits == base.limits
    assert upgraded.events[0].lanes[0].targets[0].costs == base.events[0].lanes[0].targets[0].costs
    # An empty or partial config is never read as "no PWS sleeve".
    with pytest.raises(EvidenceError, match='KATL_PLAN_PWS_CONFIG_SCHEMA'):
        katl_live_plan.upgrade_host_plan(base, official=setup[3], pws_config={})
    contract = ForecastFeatureContract((('model-1', 1),), 'F', r['rule'].payload['family'])
    target = ShadowScopeTarget(r['scope'], 'F', 1, r['context'].event_id, 1, 'd'*64, contract)
    assert katl_live_plan.commission_targets(upgraded, target, None) == (target,)
    with pytest.raises(EvidenceError, match='KATL_PLAN_COMMISSION_SHAPE_REQUIRED'):
        katl_live_plan.commission_targets(upgraded, target, {'commission': {}})
    with_pws, _, _ = economic_plan(r, main_sources=main_sources_for(r), pws=pws_sleeve(r, setup[3]))
    with pytest.raises(EvidenceError, match='KATL_PLAN_COMMISSION_SHAPE_REQUIRED'):
        katl_live_plan.commission_targets(with_pws, target, None)


def test_risk_policy_opt_ins_reach_candidate_event_and_release_binding(joined, setup):
    from polymarket_scanner.v11.settlement_window import SettlementWindowPolicy, VERSION as SW_VERSION
    from test_v11_paper_risk_observation import policy as observation_policy
    r = joined
    base, _, _ = economic_plan(r, main_sources=main_sources_for(r))
    event = base.events[0]
    assert event.risk_execution_health is None and event.risk_settlement_window is None
    health, window = observation_policy(), SettlementWindowPolicy(SW_VERSION, 86400.)
    model = r['store'].get('model2')['body']
    reviewed_risk = coordinator(r)
    common = dict(
        context=r['context'], scope=r['scope'], rule=r['rule'],
        metadata_fingerprint=r['rule'].payload['metadata_fingerprint'], model=model,
        route_valid_until=r['now'][0] + 10 * 86400, release='a' * 40, tree='b' * 40,
        bundle_sha256=r['binding'].bundle_sha256, collateral=COLLATERAL, worker='worker', stage='PAPER',
        book_provider='fixture', account_policy=reviewed_risk.policy,
        correlation=reviewed_risk.correlation, scenario_limits=reviewed_risk.limits,
        main_sources=main_sources_for(r))
    # Explicit None is the existing plan, byte for byte (release config digest included).
    unset, _, _ = katl_live_plan.build_plan(**common, risk_execution_health=None, risk_settlement_window=None)
    assert unset == base
    # Literal pre-opt-in digest of this fixture's release config (computed at 91949a4).
    assert event.risk_inputs.binding.config_sha256 == (
        '0ce411252b688224dd6980106b5af15c5fab699a32ef9d16fdd045a48b53c496')
    opted, _, _ = katl_live_plan.build_plan(**common, risk_execution_health=health, risk_settlement_window=window)
    assert opted.events[0].risk_execution_health is health and opted.events[0].risk_settlement_window is window
    assert opted.events[0].risk_inputs.binding.config_sha256 != event.risk_inputs.binding.config_sha256
    only_window, _, _ = katl_live_plan.build_plan(**common, risk_settlement_window=window)
    assert only_window.events[0].risk_execution_health is None
    assert len({only_window.events[0].risk_inputs.binding.config_sha256,
                opted.events[0].risk_inputs.binding.config_sha256,
                event.risk_inputs.binding.config_sha256}) == 3
    for wrong in (dict(risk_execution_health=window), dict(risk_settlement_window=health),
                  dict(risk_execution_health=asdict(health)), dict(risk_settlement_window={})):
        with pytest.raises(EvidenceError, match='KATL_PLAN_RISK_POLICY_TYPE'):
            katl_live_plan.build_plan(**common, **wrong)
    upgraded = katl_live_plan.upgrade_host_plan(base, official=setup[3], pws_config=None,
                                                risk_execution_health=health, risk_settlement_window=window)
    assert upgraded.events[0].risk_execution_health is health
    assert upgraded.events[0].risk_settlement_window is window
    assert upgraded.events[0].risk_inputs.binding.config_sha256 == opted.events[0].risk_inputs.binding.config_sha256
    assert katl_live_plan.upgrade_host_plan(base, official=setup[3], pws_config=None) == base


def test_default_window_derives_through_candidate_event_and_event_risk_inputs(factory, monkeypatch):
    import test_v11_strategy_pipeline as strategy_pipeline
    from polymarket_scanner.v11.settlement_window import BASIS, SettlementWindowPolicy, VERSION as SW_VERSION
    from test_v11_settlement_window import _hourly_event

    monkeypatch.setattr(strategy_pipeline, '_event', _hourly_event)
    r = factory('FUTURE_FORECAST')
    capture_full_partition_books(r)
    plan, _, _ = economic_plan(r, use_default_settlement_window=True)
    event = plan.events[0]
    assert event.risk_settlement_window == SettlementWindowPolicy(SW_VERSION, 86400.)
    assert event.risk_execution_health is None

    # Explicit None is the original fail-closed plan, including its binding.
    fallback, _, _ = economic_plan(r)
    assert fallback.events[0].risk_settlement_window is None
    assert event.risk_inputs.binding.config_sha256 != fallback.events[0].risk_inputs.binding.config_sha256

    candidate, result = _evaluate_plan(r, plan, monkeypatch, generation='katl-hourly-window')
    risk_adapter = candidate.runtime.evaluator.inputs[r['context'].event_id]
    assert risk_adapter.settlement_window_policy == event.risk_settlement_window
    assert risk_adapter.execution_health_policy is None
    assert risk_adapter.description()['settlement_window_policy'] == asdict(event.risk_settlement_window)
    assert 'execution_health_policy' not in risk_adapter.description()

    # Only the single top-level risk-input record, not the per-target
    # MakerMicrostructure ':book:N' sub-records this same key also prefixes.
    measured = [row for row in r['store'].records(kind='MEASUREMENT')
               if row['id'].startswith('risk-input:') and ':book:' not in row['id']]
    assert len(measured) == 1
    details = measured[0]['body']['details']
    assert details['settlement_window']['status'] == 'DERIVED'
    assert details['settlement_window']['basis'] == BASIS
    assert details['metrics']['time_to_settlement_seconds'] > 0
    assert details['unknown_inputs'] == ['OWN_EXECUTION_ADVERSE_FILLS', 'OWN_EXECUTION_MARKOUT']
    assert details['metrics']['adverse_fills'] is None
    assert details['metrics']['recent_markout_per_share'] is None
    assert details['settlement_finality'] is False
    state = r['store'].get(measured[0]['id'] + ':state')['body']['details']
    assert 'SETTLEMENT_WINDOW_UNKNOWN_OR_CLOSED' not in state['reasons']
    assert state['financial_authority'] is False
    value = r['store'].get(result.result_ids[0] + ':valuation')['body']['details']
    assert value['outcome'] == 'GATED'
    assert 'UNKNOWN_OR_MISSING_COST_COVERAGE' in value['reasons']
    assert not result.proposals
    assert not r['store'].records(kind='TRADE')


def test_pws_policy_change_changes_release_config_binding(joined, setup):
    r = joined
    sleeve = pws_sleeve(r, setup[3])
    first, _, _ = economic_plan(r, main_sources=main_sources_for(r), pws=sleeve)
    changed = replace(sleeve, lead_policy=replace(sleeve.lead_policy,
                                                  horizon_seconds=sleeve.lead_policy.horizon_seconds + 1))
    second, _, _ = economic_plan(r, main_sources=main_sources_for(r), pws=changed)
    assert first.events[0].risk_inputs.binding.config_sha256 != second.events[0].risk_inputs.binding.config_sha256


def test_pws_sleeve_requires_reviewed_pws_observation_lead_scopes(joined, setup):
    r = joined; metadata = setup[3]
    sleeve = pws_sleeve(r, metadata)
    with pytest.raises(EvidenceError, match='KATL_PLAN_PWS_SCOPE_REQUIRED'):
        replace(sleeve, payout_scope=replace(r['payout_scope'], strategy='FUTURE_FORECAST'))


# ---------------------------------------------------------------------------
# Real admission/risk evaluation: req8's actual blocker was never reaching
# this point (EVENT_REVALIDATION_EXPIRED / ASSEMBLY_BOOK_IDENTITY_OR_FRESHNESS
# / RUNTIME_FRESH_CENSUS_ADAPTER_REQUIRED fired first). This plan must reach
# the real EV decision instead, without bypassing any gate.
# ---------------------------------------------------------------------------

def _evaluate_plan(r, plan, monkeypatch, *, generation):
    synthetic_clock(r, monkeypatch)
    async def run():
        async with httpx.AsyncClient() as client:
            candidate = app.assemble_candidate(r['store'], client, plan, generation=generation)
            ready(r, candidate.runtime.health)
            # Use the real RiskAwareEventAdapter (not its inner dispatcher) so
            # a fresh COORDINATOR_EVENT is derived against this plan's own
            # binding/sources before strategy dispatch -- the real req8 path.
            result = evaluate(r, candidate.runtime.queue, candidate.runtime.evaluator)
            return candidate, result
    return asyncio.run(run())


def test_combined_lanes_reach_real_pws_lead_with_risk_source_coverage(joined, setup, monkeypatch):
    r = joined
    reviews = deepcopy(certification.protected_reviews()['reviews'])
    main = approve_fixture(monkeypatch, (setup[0], setup[1], r['scope'], setup[3], setup[4]),
                           stage=r['admission_kw']['stage'], fingerprint=r['rule'].sha256,
                           prefix='combined-main:')
    reviews += main['reviews']
    monkeypatch.setattr(certification, 'protected_reviews', lambda: deepcopy({'reviews': reviews}))
    r['states'][r['scope'].key] = r['model_state'][0]
    capture_full_partition_books(r)
    sleeve = pws_sleeve(r, setup[3])
    assert {s.role for s in sleeve.payout_sources} == {'MODEL', 'OFFICIAL', 'PWS', 'FEATURES'}
    assert {s.role for s in sleeve.observation_sources} == {'MODEL', 'OFFICIAL', 'PWS'}
    plan, _, _ = economic_plan(r, main_sources=main_sources_for(r), pws=sleeve)
    candidate, result = _evaluate_plan(r, plan, monkeypatch, generation='katl-combined-risk-sources')
    details = [r['store'].get(key)['body']['details'] for key in result.result_ids]
    assert len(details) == 2
    assert all(d['reason'] == 'EVENT_STATE_SUPPRESSES_TEMPERATURE_ENTRY' for d in details)
    leads = [row['body']['details'] for row in r['store'].records(kind='MEASUREMENT')
             if row['id'].endswith(':lead')]
    assert len(leads) == 1
    assert leads[0]['valuation_type'] == 'OBSERVATION_ONLY'
    assert leads[0]['settlement_prediction'] is None
    assert not result.proposals
    assert candidate.runtime.coordinator.snapshot()['reserved_cash'] == '0'
    assert not r['store'].records(kind='TRADE')


def test_plan_reaches_real_admission_and_risk_evaluation_not_a_staleness_gate(factory, monkeypatch):
    r = factory('FUTURE_FORECAST')
    capture_full_partition_books(r)
    plan, _, _ = economic_plan(r)
    candidate, result = _evaluate_plan(r, plan, monkeypatch, generation='katl-economic-fresh')
    d = r['store'].get(result.result_ids[0])['body']['details']
    assert d['reason'] not in {'EVENT_REVALIDATION_EXPIRED', 'ASSEMBLY_BOOK_IDENTITY_OR_FRESHNESS',
                               'RUNTIME_FRESH_CENSUS_ADAPTER_REQUIRED'}, d
    # Admission reaches a real valuation, but absent cost evidence keeps it
    # GATED. EventRisk separately suppresses new entry on unknown health.
    assert d['reason'] == 'EVENT_STATE_SUPPRESSES_TEMPERATURE_ENTRY', d
    assert 'valuation' in d and d['valuation'] is not None
    value = r['store'].get(result.result_ids[0] + ':valuation')['body']['details']
    assert value['outcome'] == 'GATED'
    assert value['reasons'] == ['UNKNOWN_OR_MISSING_COST_COVERAGE']
    assert set(value['costs']['missing']) == ENTRY_RISKS
    assert value['conservative_ev_per_share'] is None
    risk = r['store'].records(kind='MEASUREMENT')
    measured = [row['body']['details'] for row in risk if row['id'].startswith('risk-input:')]
    assert measured and measured[-1]['metrics']['time_to_settlement_seconds'] is None
    assert measured[-1]['metrics']['adverse_fills'] is None
    assert measured[-1]['metrics']['recent_markout_per_share'] is None
    assert d['financial_authority'] is False
    assert not r['store'].records(kind='TRADE')


def test_declared_unknown_costs_never_become_free(factory, monkeypatch):
    r = factory('FUTURE_FORECAST')
    capture_full_partition_books(r)
    unknown = CostComponent('all-risks-unknown', PAYOUT, None, tuple(sorted(ENTRY_RISKS)), 'a'*64)
    plan, _, _ = economic_plan(r, temperature_costs=(unknown,))
    _, result = _evaluate_plan(r, plan, monkeypatch, generation='katl-unknown-costs')
    value = r['store'].get(result.result_ids[0] + ':valuation')['body']['details']
    assert value['outcome'] == 'GATED'
    assert value['costs']['missing'] == []
    assert set(value['costs']['unknown']) == ENTRY_RISKS
    assert value['conservative_ev_per_share'] is None
    assert not result.proposals
    assert not r['store'].records(kind='TRADE')


def test_stale_book_still_refuses_despite_relaxed_freshness(factory, monkeypatch):
    r = factory('FUTURE_FORECAST')
    plan, _, _ = economic_plan(r)
    # Push the clock past the relaxed (but still <=120s) book/valuation
    # freshness window -- the gate must still fire; loosening smoke values
    # toward the real microstructure ceiling must never mean "no ceiling".
    r['now'][0] += katl_live_plan.BOOK_FRESHNESS_SECONDS + 5
    candidate, result = _evaluate_plan(r, plan, monkeypatch, generation='katl-economic-stale')
    d = r['store'].get(result.result_ids[0])['body']['details']
    assert d['outcome'] == 'GATED', d
    assert d['reason'] in {'ASSEMBLY_BOOK_IDENTITY_OR_FRESHNESS', 'ASSEMBLY_FRESH_CENSUS_REQUIRED',
                           'RUNTIME_FRESH_CENSUS_ADAPTER_REQUIRED', 'RISK_WHOLE_EVENT_BOOK_COVERAGE_INCOMPLETE'}, d
    assert not r['store'].records(kind='TRADE')


def test_deterministic_replay_produces_identical_result_and_no_additional_effect(factory, monkeypatch):
    r = factory('FUTURE_FORECAST')
    plan, _, _ = economic_plan(r)
    synthetic_clock(r, monkeypatch)
    async def run():
        async with httpx.AsyncClient() as client:
            one = app.assemble_candidate(r['store'], client, plan, generation='katl-economic-replay')
            first_view = r['store'].pin_read_view()
            two = app.assemble_candidate(r['store'], client, plan, generation='katl-economic-replay')
            second_view = r['store'].pin_read_view()
            return one, two, first_view, second_view
    one, two, first_view, second_view = asyncio.run(run())
    assert one.assembly_sha256 == two.assembly_sha256
    assert one.runtime.evaluator.config == two.runtime.evaluator.config
    assert first_view == second_view
    assert not r['store'].records(kind='TRADE')


# ---------------------------------------------------------------------------
# Scenario reservation (req8): only the final EV outcome is synthesized, the
# same established convention as test_v11_pws_admission.synthetic_proposal --
# but PaperCoordinator.coordinate() independently re-derives the SAME
# EventRiskEngine state and refuses new risk for the identical honest reason.
# This is not a bug this plan can route around without bypassing a real
# gate; see docs/V11_KATL_LIVE_PLAN_DEPLOYMENT_HANDOFF_20261007.md.
# ---------------------------------------------------------------------------

def test_scenario_reservation_consistently_refuses_while_event_state_is_suppressed(factory, monkeypatch):
    r = factory('FUTURE_FORECAST')
    capture_full_partition_books(r)
    plan, _, _ = economic_plan(r)
    candidate, result = _evaluate_plan(r, plan, monkeypatch, generation='katl-economic-reserve')
    assert plan.events[0].risk_settlement_window is None
    evaluation_id = result.result_ids[0]
    details = r['store'].get(evaluation_id)['body']['details']
    admission_id, event_state_id = details['admission_id'], details['request']['event_state_id']
    # The real evaluation already computed a genuine settlement valuation at
    # `evaluation_id+':valuation'` (settlement_entry runs before the EVENT_
    # STATE_SUPPRESSES_TEMPERATURE_ENTRY check); its real outcome is GATED
    # because all seven cost categories are missing. This downstream fixture
    # overrides the valuation outcome and EV to isolate coordinator refusal.
    real_value = r['store'].get(evaluation_id + ':valuation')['body']['details']
    assert real_value['outcome'] == 'GATED'
    assert set(real_value['costs']['missing']) == ENTRY_RISKS
    fixture_value = dict(real_value, outcome='ACCEPT_RESEARCH', conservative_ev_per_share='.2',
                         conservative_ev_total='.4', synthetic_downstream_test_fixture=True)
    valuation_id = 'fixture-value'
    proposal = Proposal('reserve', 'economic-thesis', r['context'], r['rule'], valuation_id, event_state_id,
                        (Attribution('FUTURE_FORECAST', '1'),), r['now'][0] + 20, '1', (admission_id,), None, None)
    pointer = dict(synthetic_downstream_test_fixture=True, proposal=dict(valuation_id=valuation_id))
    q = candidate.runtime.queue
    q.publish('reserve:update', kind='BOOK', evidence_id=details['request']['book_id'])
    with q.work('reserve:claim') as claim:
        r['store'].audit(valuation_id, event_id=r['context'].event_id, kind='MEASUREMENT', details=fixture_value)
        r['store'].audit('fixture-pointer', event_id=r['context'].event_id, kind='MEASUREMENT', details=pointer)
        q.finish('reserve:finish', claim_id=claim['claim_id'], result_ids=(valuation_id, 'fixture-pointer'))
    before = candidate.runtime.coordinator.snapshot()['reserved_cash']
    reservation = candidate.runtime.coordinator.coordinate('batch', (proposal,))['body']['details']
    measured = [row['body']['details'] for row in r['store'].records(kind='MEASUREMENT')
               if row['id'].startswith('risk-input:')]
    assert measured[-1]['metrics']['time_to_settlement_seconds'] is None
    assert 'SETTLEMENT_TIMING' in measured[-1]['unknown_inputs']
    # Even with a synthesized ACCEPT_RESEARCH valuation, the coordinator
    # independently refuses -- for the same risk_inputs.py-documented reason
    # (settlement timing/execution quality left UNKNOWN) as the strategy
    # layer. This is the genuine, honest, un-bypassable remaining req8 gate.
    assert reservation['results'] == [dict(outcome='REJECT', proposal_id='reserve',
                                           reason='EVENT_OR_OPERATOR_SUPPRESSES_NEW_RISK')]
    assert not reservation['reserved_intent_ids']
    assert candidate.runtime.coordinator.snapshot()['reserved_cash'] == before == '0'
    assert not r['store'].records(kind='TRADE')
    assert plan.account == coordinator(r).policy
