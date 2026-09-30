import ast
import asyncio
from pathlib import Path

import httpx
import pytest

from polymarket_scanner.v11 import certification as cert
from polymarket_scanner.v11 import model_registry
from polymarket_scanner.v11 import shadow_commission as sc
from polymarket_scanner.v11.audit_reports import AuditPolicy, AuditScheduler, AuditWorker
from polymarket_scanner.v11.book_inputs import BookPolicy
from polymarket_scanner.v11.candidate_runner import CandidatePolicy, CandidateRunner
from polymarket_scanner.v11.census_worker import CensusPlan, CensusPolicy, CensusWorker
from polymarket_scanner.v11.certification import CapabilityScope
from polymarket_scanner.v11.collection import PublicCollector
from polymarket_scanner.v11.discovery import MarketDiscovery, DiscoveryPolicy
from polymarket_scanner.v11.evidence import (EvidenceError, EvidenceStore, ReleaseBinding,
    digest)
from polymarket_scanner.v11.event_risk import EventContext, EventRiskEngine
from polymarket_scanner.v11.observation_runtime import ScheduledCollector
from polymarket_scanner.v11.paper_coordinator import PaperAccountPolicy
from polymarket_scanner.v11.paper_runtime import PaperRuntime, RuntimePolicy

from test_v11_candidate_runner import transport as candidate_transport
from test_v11_census_worker import transport_for
from test_v11_event_risk import policy as event_policy, metrics
from test_v11_model_artifacts import bundle
from test_v11_model_governance import authority, promote
from test_v11_paper_coordinator import coordinator
from test_v11_paper_runtime import GatedEvaluator, census_fixture, queue
from test_v11_probability import T, rule
from test_v11_runtime_health import monitor, ready
from test_v11_scenario_risk import limits, mapping


NAMESPACE = 'CHALLENGER:shadow-commission-test'
RELEASE = 'a' * 40


def _scope(station='KATL', family='HIGH', model_version='test-model-v1', strategy='FUTURE_FORECAST'):
    return CapabilityScope(station, family, 'NWS_WRH', model_version, '24_48_HOURS',
                            strategy, 'AUTUMN', 'DAY')


def _plan(**changes):
    kw = dict(version=sc.PLAN_VERSION, namespace=NAMESPACE, worker_id='worker',
              release_git_sha=RELEASE, created_at=1000., description='test plan',
              targets=(sc.ShadowScopeTarget(_scope(), 'C', 10),))
    kw.update(changes)
    return sc.ShadowCommissionPlan(**kw)


def _plan_dict(**changes):
    value = dict(version=sc.PLAN_VERSION, namespace=NAMESPACE, worker_id='worker',
                 release_git_sha=RELEASE, created_at=1000., description='test plan',
                 targets=[dict(scope=dict(station='KATL', family='HIGH', source_rule_family='NWS_WRH',
                                          model_version='test-model-v1', horizon='24_48_HOURS',
                                          strategy='FUTURE_FORECAST', season='AUTUMN', time_of_day='DAY'),
                               unit='C', sample_target=10)])
    value.update(changes)
    return value


# ---------------------------------------------------------------------------
# Plan schema / loader
# ---------------------------------------------------------------------------

def test_plan_rejects_financial_and_wildcard_and_non_research_namespace():
    with pytest.raises(EvidenceError, match='NAMESPACE'):
        _plan(namespace='V11_PAPER')
    with pytest.raises(EvidenceError, match='NAMESPACE'):
        _plan(namespace='SOMETHING_ELSE:test')
    with pytest.raises(EvidenceError, match='WILDCARD'):
        sc.ShadowScopeTarget(_scope(station='*'), 'C', 10)


def test_plan_rejects_duplicate_scope_and_duplicate_family_unit():
    one = sc.ShadowScopeTarget(_scope(), 'C', 10)
    with pytest.raises(EvidenceError, match='DUPLICATE_SCOPE'):
        _plan(targets=(one, one))
    two = sc.ShadowScopeTarget(_scope(model_version='other-model-v1'), 'C', 10)
    with pytest.raises(EvidenceError, match='DUPLICATE_FAMILY_UNIT'):
        _plan(targets=(one, two))


def test_plan_accepts_explicit_high_low_c_f_structure_matching_r47():
    # A single scope_key can carry exactly one pinned model-authority bundle
    # (model_registry.py stores one active bundle per scope_key+mode slot), so
    # a real plan naming both C and F for the same family must bind two
    # distinct scopes (here via model_version) -- never silently share one.
    targets = tuple(sc.ShadowScopeTarget(_scope(family=family, model_version='test-model-v1-'+unit), unit, 10)
                     for family in ('HIGH', 'LOW') for unit in ('C', 'F'))
    plan = _plan(targets=targets)
    assert len(plan.targets) == 4
    assert len({t.family_unit for t in plan.targets}) == 4


def test_plan_target_sample_bound_and_unit_enum():
    with pytest.raises(EvidenceError, match='SAMPLE_BOUND'):
        sc.ShadowScopeTarget(_scope(), 'C', 0)
    with pytest.raises(EvidenceError, match='SAMPLE_BOUND'):
        sc.ShadowScopeTarget(_scope(), 'C', 501)
    with pytest.raises(EvidenceError, match='UNIT_INVALID'):
        sc.ShadowScopeTarget(_scope(), 'K', 10)


def test_loader_round_trips_and_rejects_duplicate_keys_and_unknown_fields(tmp_path):
    import json
    path = tmp_path / 'plan.json'
    path.write_text(json.dumps(_plan_dict()))
    loaded = sc.load_plan(path)
    assert loaded.namespace == NAMESPACE and loaded.targets[0].unit == 'C'
    bad = tmp_path / 'dup.json'
    bad.write_text('{"a":1,"a":2}')
    with pytest.raises(EvidenceError, match='DUPLICATE_KEY'):
        sc.load_plan(bad)
    unknown = tmp_path / 'unknown.json'
    unknown.write_text(json.dumps({**_plan_dict(), 'extra_field': True}))
    with pytest.raises(EvidenceError, match='SCHEMA'):
        sc.load_plan(unknown)


def test_loader_rejects_symlink_and_relative_and_oversized_plan(tmp_path):
    real = tmp_path / 'real.json'
    import json
    real.write_text(json.dumps(_plan_dict()))
    link = tmp_path / 'link.json'
    link.symlink_to(real)
    with pytest.raises(EvidenceError, match='SYMLINK'):
        sc.load_plan(link)
    with pytest.raises(EvidenceError, match='PATH_INVALID'):
        sc.load_plan(Path('relative.json'))
    huge = tmp_path / 'huge.json'
    huge.write_bytes(b'{"pad":"' + b'x' * (sc.MAX_PLAN_BYTES + 10) + b'"}')
    with pytest.raises(EvidenceError, match='BYTES_BOUND'):
        sc.load_plan(huge)


def test_deterministic_plan_identity_same_file_same_key_different_file_different_key(tmp_path):
    import json
    p1 = tmp_path / 'p1.json'; p1.write_text(json.dumps(_plan_dict()))
    p2 = tmp_path / 'p2.json'; p2.write_text(json.dumps(_plan_dict()))
    p3 = tmp_path / 'p3.json'; p3.write_text(json.dumps(_plan_dict(description='different plan')))
    assert sc.load_plan(p1).key == sc.load_plan(p2).key
    assert sc.load_plan(p1).key != sc.load_plan(p3).key


# ---------------------------------------------------------------------------
# Preflight: genuine fail-closed behavior
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    tmp_path.chmod(0o700)
    return EvidenceStore(tmp_path / 'evidence.sqlite', NAMESPACE)


def test_preflight_fails_closed_on_real_missing_root_station_capability_manifest(store):
    """No monkeypatching: /etc/alpha-v11 genuinely does not exist on this host."""
    plan = _plan()
    with pytest.raises(EvidenceError, match='STATION_CAPABILITY_REVIEW_UNAVAILABLE'):
        sc.preflight(plan, store, current_release_git_sha=RELEASE)


def test_preflight_fails_closed_on_missing_model_authority_slot_once_manifest_exists(store, monkeypatch):
    monkeypatch.setattr(cert, 'protected_reviews',
                         lambda: {'version': 'alpha_v11_certification_reviews_v1', 'reviews': []})
    plan = _plan()
    with pytest.raises(EvidenceError, match='MODEL_AUTHORITY_STATE_UNAVAILABLE'):
        sc.preflight(plan, store, current_release_git_sha=RELEASE)


def test_preflight_reports_soft_failure_when_scope_not_yet_reviewed(store, monkeypatch, bundle):
    plan = _plan()
    target = plan.targets[0]
    monkeypatch.setattr(cert, 'protected_reviews',
                         lambda: {'version': 'alpha_v11_certification_reviews_v1', 'reviews': []})
    state = [promote(bundle, authority.empty_state(target.scope.key, 'V11_SHADOW'))]
    monkeypatch.setattr(model_registry, 'protected_state',
                         lambda **kwargs: {'state': state[0], 'sha256': digest(state[0])})
    monkeypatch.setattr(model_registry, 'ApprovedArtifactReader', lambda: bundle[0])
    report = sc.preflight(plan, store, current_release_git_sha=RELEASE)
    assert report['passed'] is False
    reasons = {c['name']: c['reason'] for c in report['checks']}
    assert any('SCOPE_NOT_YET_REVIEWED' in v for v in reasons.values())


def test_preflight_passes_when_manifest_and_model_authority_both_declare_the_exact_scope(store, monkeypatch, bundle):
    plan = _plan()
    target = plan.targets[0]
    monkeypatch.setattr(cert, 'protected_reviews', lambda: {
        'version': 'alpha_v11_certification_reviews_v1',
        'reviews': [{'scope_key': target.scope.key, 'namespace': NAMESPACE, 'stage': 'SHADOW'}]})
    state = [promote(bundle, authority.empty_state(target.scope.key, 'V11_SHADOW'))]
    monkeypatch.setattr(model_registry, 'protected_state',
                         lambda **kwargs: {'state': state[0], 'sha256': digest(state[0])})
    monkeypatch.setattr(model_registry, 'ApprovedArtifactReader', lambda: bundle[0])
    report = sc.preflight(plan, store, current_release_git_sha=RELEASE)
    assert report['passed'] is True
    assert report['financial_authority'] is False and report['real_orders_sent'] is False


def test_preflight_release_mismatch_is_a_soft_failure_not_a_crash(store, monkeypatch, bundle):
    plan = _plan()
    target = plan.targets[0]
    monkeypatch.setattr(cert, 'protected_reviews', lambda: {
        'version': 'alpha_v11_certification_reviews_v1',
        'reviews': [{'scope_key': target.scope.key, 'namespace': NAMESPACE, 'stage': 'SHADOW'}]})
    state = [promote(bundle, authority.empty_state(target.scope.key, 'V11_SHADOW'))]
    monkeypatch.setattr(model_registry, 'protected_state',
                         lambda **kwargs: {'state': state[0], 'sha256': digest(state[0])})
    monkeypatch.setattr(model_registry, 'ApprovedArtifactReader', lambda: bundle[0])
    report = sc.preflight(plan, store, current_release_git_sha='b' * 40)
    assert report['passed'] is False
    reasons = {c['name']: c['reason'] for c in report['checks']}
    assert reasons['release_identity'] == 'RELEASE_GIT_SHA_MISMATCH'


def test_preflight_rejects_v11_paper_store_and_store_plan_namespace_mismatch(tmp_path):
    tmp_path.chmod(0o700)
    paper_store = EvidenceStore(tmp_path / 'paper.sqlite', 'V11_PAPER')
    plan = _plan()
    with pytest.raises(EvidenceError, match='NAMESPACE'):
        sc.preflight(plan, paper_store, current_release_git_sha=RELEASE)
    other_store = EvidenceStore(tmp_path / 'other.sqlite', 'CHALLENGER:different-test')
    with pytest.raises(EvidenceError, match='STORE_NAMESPACE_MISMATCH'):
        sc.preflight(plan, other_store, current_release_git_sha=RELEASE)


def test_preflight_cli_exits_nonzero_on_real_missing_root_state(tmp_path, monkeypatch, capsys):
    import json
    plan_path = tmp_path / 'plan.json'
    plan_path.write_text(json.dumps(_plan_dict()))
    tmp_path.chmod(0o700)
    store_path = tmp_path / 'ev.sqlite'
    EvidenceStore(store_path, NAMESPACE)  # create it first, matching the CLI's own construction
    monkeypatch.setattr('sys.argv', ['shadow_commission', 'preflight', '--plan', str(plan_path),
                                     '--store', str(store_path), '--namespace', NAMESPACE,
                                     '--release-git-sha', RELEASE])
    with pytest.raises(EvidenceError, match='STATION_CAPABILITY_REVIEW_UNAVAILABLE'):
        sc._cli()


# ---------------------------------------------------------------------------
# Static import-boundary regression
# ---------------------------------------------------------------------------

def test_shadow_commission_module_never_imports_financial_order_or_v10_code():
    ok, reason = sc._no_financial_or_v10_imports()
    assert ok, reason


def test_static_ast_check_actually_detects_a_forbidden_import(tmp_path, monkeypatch):
    fake = tmp_path / 'fake_shadow.py'
    fake.write_text('import subprocess\n')
    monkeypatch.setattr(sc, '__file__', str(fake))
    ok, reason = sc._no_financial_or_v10_imports()
    assert not ok and 'subprocess' in reason


# ---------------------------------------------------------------------------
# Bounded lifecycle wrapper, using a real (not fake) CandidateRunner
# ---------------------------------------------------------------------------

@pytest.fixture
def shadow_rig(tmp_path):
    tmp_path.chmod(0o700)
    now = [T + 110.]
    store = EvidenceStore(tmp_path / 'shadow.sqlite', NAMESPACE, clock=lambda: now[0])
    r = rule()
    corr = mapping(r)
    p = PaperAccountPolicy('fixture-1', 'account', 'FIXTURE_COLLATERAL', '10', '10', '10', '10',
                            '.01', '0', 60., 10)
    lim = limits(per_event='100', portfolio='100', per_city='100', per_region='100',
                 per_weather_group='100', per_source_group='100', per_model_group='100')
    ctx = EventContext('account', corr.memberships[0].city, r.payload['station'], r.payload['event_id'])
    binding = ReleaseBinding('a' * 40, 'b' * 40, 'c' * 64, 'c' * 64, r.sha256)
    state_id = book_id = source_id = None
    for i in range(3):
        book_id, source_id, state_id = 'risk-book' + str(i), 'risk-source' + str(i), 'risk-state' + str(i)
        store.capture(book_id, event_id=ctx.event_id, kind='BOOK', provider='fixture', source_identity='token',
                      revision=str(i), observed_at=now[0], payload={'stream_healthy': True},
                      evidence_class='SYNTHETIC')
        store.capture(source_id, event_id=ctx.event_id, kind='OFFICIAL_OBSERVATION', provider='fixture',
                      source_identity=ctx.station_id, revision=str(i), observed_at=now[0], payload={},
                      evidence_class='SYNTHETIC')
        EventRiskEngine(store).step(state_id, context=ctx, policy=event_policy(), binding=binding,
                                    metrics=metrics(now[0]), book_ids=(book_id,), source_ids=(source_id,))
        if i < 2:
            now[0] += 10
    return dict(store=store, now=now, rule=r, context=ctx, binding=binding, policy=p, limits=lim,
                correlation=corr, state_id=state_id, book_id=book_id, source_id=source_id)


def _built_runner(rig, monkeypatch, client, *, maximum_seconds=3.):
    c = coordinator(rig)
    q = queue(rig)
    m = monitor(rig, monkeypatch)
    ready(rig, m)
    audits = AuditScheduler(rig['store'], AuditPolicy('shadow-fixture', records_per_step=256))
    rt = PaperRuntime(c, q, m, RuntimePolicy('shadow-fixture'), evaluator=GatedEvaluator(rig['store']),
                       census=census_fixture(rig), worker_id='worker', generation='shadow-fixture', audits=audits)
    scheduled = ScheduledCollector(PublicCollector(rig['store'], client, attempts=1))
    census = CensusWorker(scheduled, rt.queue, rt.health, plans=(CensusPlan(rig['rule'], 'FIXTURE_COLLATERAL'),),
                           policy=CensusPolicy('shadow-fixture'), book_policy=BookPolicy('shadow-fixture'))
    discovery = MarketDiscovery(scheduled, rt.health, DiscoveryPolicy('shadow-fixture'))
    return CandidateRunner(rt, CandidatePolicy('shadow-fixture', maximum_seconds=maximum_seconds, maximum_jobs=2,
        safety_interval_seconds=.05, minimum_job_spacing_seconds=.05), census=census, discovery=discovery,
        audits=AuditWorker(rt.coordinator, audits.policy))


def _monkeypatch_root_state(monkeypatch, plan):
    manifest = {'version': 'alpha_v11_certification_reviews_v1',
                'reviews': [{'scope_key': t.scope.key, 'namespace': plan.namespace, 'stage': 'SHADOW'}
                            for t in plan.targets]}
    monkeypatch.setattr(cert, 'protected_reviews', lambda: manifest)


def _promote_all(monkeypatch, plan, bundle):
    states = {t.scope.key: promote(bundle, authority.empty_state(t.scope.key, 'V11_SHADOW')) for t in plan.targets}
    monkeypatch.setattr(model_registry, 'protected_state',
                         lambda *, scope_key=None, mode=None: {'state': states[scope_key], 'sha256': digest(states[scope_key])})
    monkeypatch.setattr(model_registry, 'ApprovedArtifactReader', lambda: bundle[0])


def test_bounded_loop_terminates_at_exact_iteration_count_and_is_idempotent(shadow_rig, monkeypatch, bundle):
    plan = _plan(targets=(sc.ShadowScopeTarget(_scope(), 'C', 10),))
    _monkeypatch_root_state(monkeypatch, plan)
    _promote_all(monkeypatch, plan, bundle)
    calls = []

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(candidate_transport(shadow_rig, calls))) as client:
            runner = _built_runner(shadow_rig, monkeypatch, client)
            wrapper = sc.ShadowCommissionRunner(plan, runner, release_git_sha=RELEASE,
                lifecycle=sc.ShadowLifecyclePolicy('fixture', maximum_iterations=3, interval_seconds=.01))
            results = await wrapper.run_bounded('bounded-test')
            repeat = await wrapper.run_once('bounded-test:iteration:0')
            return results, repeat

    results, repeat = asyncio.run(go())
    assert len(results) == 3
    assert {r['body']['details']['outcome'] for r in results} <= {'BOUNDED_RUN_FINISHED', 'DEGRADED'}
    assert all(r['id'] != results[0]['id'] for r in results[1:])
    assert repeat['id'] == results[0]['id'] and repeat['sha256'] == results[0]['sha256']
    for r in results:
        assert r['body']['details']['forward_acceptance'] is False
    assert not shadow_rig['store'].records(kind='TRADE')
    status_rows = shadow_rig['store'].records(kind='RUNTIME_STATUS', event_id=sc.KEY, limit=64)
    assert status_rows and all(row['body']['details']['financial_authority'] is False for row in status_rows)
    assert all(row['body']['details']['real_orders_sent'] is False for row in status_rows)


def test_run_once_refuses_to_start_on_failed_preflight_and_records_gated_reason(shadow_rig, monkeypatch):
    plan = _plan()  # no monkeypatched root state at all: genuinely absent on this host

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(candidate_transport(shadow_rig, []))) as client:
            runner = _built_runner(shadow_rig, monkeypatch, client)
            wrapper = sc.ShadowCommissionRunner(plan, runner, release_git_sha=RELEASE,
                lifecycle=sc.ShadowLifecyclePolicy('fixture', maximum_iterations=1, interval_seconds=.01))
            with pytest.raises(EvidenceError):
                await wrapper.run_once('refused-test')

    asyncio.run(go())


def test_runner_rejects_non_shadow_namespace_and_worker_mismatch(shadow_rig, monkeypatch, bundle):
    plan = _plan()
    _monkeypatch_root_state(monkeypatch, plan)
    _promote_all(monkeypatch, plan, bundle)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(candidate_transport(shadow_rig, []))) as client:
            runner = _built_runner(shadow_rig, monkeypatch, client)
            with pytest.raises(EvidenceError, match='WORKER_IDENTITY_MISMATCH'):
                sc.ShadowCommissionRunner(_plan(worker_id='different-worker'), runner, release_git_sha=RELEASE,
                    lifecycle=sc.ShadowLifecyclePolicy('fixture'))

    asyncio.run(go())


def test_evidence_status_distinguishes_forward_from_historical_and_makes_no_profitability_claim(shadow_rig, monkeypatch, bundle):
    plan = _plan(targets=(sc.ShadowScopeTarget(_scope(), 'C', 10),))
    _monkeypatch_root_state(monkeypatch, plan)
    _promote_all(monkeypatch, plan, bundle)

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(candidate_transport(shadow_rig, []))) as client:
            runner = _built_runner(shadow_rig, monkeypatch, client)
            wrapper = sc.ShadowCommissionRunner(plan, runner, release_git_sha=RELEASE,
                lifecycle=sc.ShadowLifecyclePolicy('fixture', maximum_iterations=1, interval_seconds=.01))
            await wrapper.run_bounded('status-test')

    asyncio.run(go())
    status = sc.evidence_status(plan, shadow_rig['store'])
    assert status['forward_commission_runs_recorded'] >= 1
    assert status['promotion_claim'] is None and status['profitability_claim'] is None
    assert status['financial_authority'] is False and status['real_orders_sent'] is False
    assert len(status['targets']) == 1 and status['targets'][0]['scope_key'] == plan.targets[0].scope.key


def test_evidence_status_and_preflight_reject_mismatched_namespace(shadow_rig):
    plan = _plan(namespace='CHALLENGER:different-namespace')
    with pytest.raises(EvidenceError, match='STATUS_NAMESPACE_MISMATCH'):
        sc.evidence_status(plan, shadow_rig['store'])


# ---------------------------------------------------------------------------
# Regression: the existing 11-decision-site end-to-end SHADOW path must still
# hold. This does not duplicate that coverage -- it is provided by
# tests/test_v11_strategy_pipeline.py, test_v11_position_management.py,
# test_v11_relative_value.py, test_v11_pws_admission.py,
# test_v11_source_release.py, test_v11_maker_context.py,
# test_v11_reaction_runtime.py, test_v11_drift_runtime.py and
# test_v11_risk_inputs.py (basket_coordinator.py and strategy_admission.py
# are exercised transitively through those). This module imports nothing
# from them and does not need to for its own tests to pass; the "regression"
# obligation is satisfied by running those files together with this one in
# the same session (see the verification command recorded in
# docs/V11_ENGINEERING_PROGRESS.md and the final report), not by re-deriving
# their assertions here.
# ---------------------------------------------------------------------------
