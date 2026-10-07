"""Commissioning regressions use only synthetic stores and protected-reader doubles.

No fixture or completed candidate tick is qualifying forward evidence.
"""
import asyncio
from dataclasses import asdict, replace
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from polymarket_scanner.v11 import candidate_assembly as app, certification as cert
from polymarket_scanner.v11 import model_registry, shadow_commission as sc
from polymarket_scanner.v11.candidate_cohort import candidate_cohort
from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore, canonical, digest
from polymarket_scanner.v11.forecast_features import ForecastFeatureContract
from polymarket_scanner.v11.model_artifacts import ArtifactStore
from polymarket_scanner.v11.strategy_admission import StrategyAdmission
from test_v11_certification_rules import setup as certification_setup
from test_v11_strategy_pipeline import factory
from test_v11_model_artifacts import make_artifacts
from test_v11_model_governance import authority, promote
from test_v11_candidate_assembly import scoped_plan, synthetic_clock, transport
from test_v11_request_assembly import inputs, target
from test_v11_runtime_health import ready
from test_v11_maker_research import rig as maker_rig
from test_v11_maker_runtime import terms as maker_terms
from polymarket_scanner.v11.maker_telemetry import MakerTelemetryPolicy

RELEASE = 'a' * 40
NAMESPACE = 'CHALLENGER:shadow-test'
CONTRACT = ForecastFeatureContract((('model-1', 3),), 'F', 'daily_high_temperature')


@pytest.fixture
def setup(tmp_path):
    return certification_setup.__wrapped__(tmp_path, SimpleNamespace(param=NAMESPACE))


@pytest.fixture
def bundle(tmp_path):
    (tmp_path / 'objects').mkdir(mode=0o700)
    artifacts = ArtifactStore(tmp_path / 'objects')
    values = make_artifacts()
    for value in values.values():
        value['feature_schema_sha256'] = CONTRACT.schema.sha256
    values['FEATURES']['parameters'] = json.loads(canonical(asdict(CONTRACT.schema)))
    values['CALIBRATION']['parameters']['probability_artifact_sha256'] = digest(values['PROBABILITY'])
    refs = {k: artifacts.put_artifact(v) for k, v in values.items()}
    key = artifacts.put_bundle(artifacts=refs, target='FINAL_CONTRACT_PAYOUT', feature_schema_sha256=CONTRACT.schema.sha256)
    return artifacts, values, refs, key


@pytest.fixture
def rig(factory, monkeypatch):
    r = factory()
    lane = app.TemperatureLane('temperature', inputs(r), (target(r),), 'fixture', r['request'].valuation_policy, 10.)
    cfg = scoped_plan(r, lane)
    cfg = replace(cfg, candidate=replace(cfg.candidate, maximum_jobs=1, maximum_seconds=5.))
    synthetic_clock(r, monkeypatch)
    calls = []
    client = httpx.AsyncClient(transport=transport(r, calls))
    runner = app.assemble_candidate(r['store'], client, cfg, generation='shadow-test')
    ready(r, runner.runtime.health)
    cohort = candidate_cohort(runner)
    t = sc.ShadowScopeTarget(r['scope'], 'F', 10, r['context'].event_id,
        r['model_state'][0]['epoch'], digest(r['model_state'][0]), CONTRACT)
    plan = sc.ShadowCommissionPlan(sc.PLAN_VERSION, NAMESPACE, 'worker', RELEASE,
        r['now'][0], 'synthetic exact cohort', (t,), cohort)
    r.update(runner=runner, plan=plan, cfg=cfg, client=client, calls=calls)
    yield r
    asyncio.run(client.aclose())


def wrapper(r, **kw):
    return sc.ShadowCommissionRunner(r['plan'], r['runner'], release_git_sha=kw.get('release', RELEASE),
        lifecycle=sc.ShadowLifecyclePolicy('test', maximum_iterations=kw.get('iterations', 1), interval_seconds=.001))


def report(r, **kw):
    return sc.preflight(r['plan'], r['store'], current_release_git_sha=kw.get('release', RELEASE))


def test_positive_typed_binding_and_exact_eligibility(rig):
    pf = wrapper(rig).preflight()
    assert pf['passed'], pf
    assert pf['identities'][0]['model_state_sha256'] == rig['plan'].targets[0].model_state_sha256
    assert not pf['financial_authority'] and not pf['real_orders_sent']


def test_plan_loader_roundtrip_and_schema_guards(rig, tmp_path):
    path = tmp_path / 'plan.json'
    path.write_text(canonical(asdict(rig['plan'])))
    assert sc.load_plan(path) == rig['plan']
    bad = asdict(rig['plan']); bad['unknown'] = True
    path.write_text(canonical(bad))
    with pytest.raises(EvidenceError, match='SCHEMA'):
        sc.load_plan(path)
    path.write_text('{"x":1,"x":2}')
    with pytest.raises(EvidenceError, match='DUPLICATE_KEY'):
        sc.load_plan(path)
    path.write_bytes(b'x' * (sc.MAX_PLAN_BYTES + 1))
    with pytest.raises(EvidenceError, match='BYTES_BOUND'):
        sc.load_plan(path)
    link = tmp_path / 'link'; link.symlink_to(path)
    with pytest.raises(EvidenceError, match='SYMLINK'):
        sc.load_plan(link)
    with pytest.raises(EvidenceError, match='PATH_INVALID'):
        sc.load_plan(Path('relative'))


@pytest.mark.parametrize('change', ['station', 'event', 'unit', 'missing', 'extra', 'release'])
def test_plan_rejects_changed_or_unrelated_scope_bindings(rig, change):
    p, t = rig['plan'], rig['plan'].targets[0]
    with pytest.raises(EvidenceError):
        if change == 'station':
            replace(p, targets=(replace(t, scope=replace(t.scope, station='KJFK')),))
        elif change == 'event':
            replace(p, targets=(replace(t, event_id='unrelated-event'),))
        elif change == 'unit':
            replace(p, targets=(replace(t, unit='C', feature_contract=replace(CONTRACT, unit='C')),))
        elif change == 'missing':
            replace(p, targets=())
        elif change == 'extra':
            replace(p, targets=(t, replace(t, event_id='extra-event')))
        else:
            replace(p, release_git_sha='b' * 40)


@pytest.mark.parametrize('change', ['route', 'scope', 'rule', 'bundle', 'worker', 'worker_plan'])
def test_actual_graph_mutation_is_detected_before_work(rig, change):
    w = wrapper(rig)
    runner = rig['runner']
    event = rig['context'].event_id
    a = runner.runtime.evaluator.inputs[event].assembler
    if change == 'route':
        runner.runtime.queue.routes[event] = replace(runner.runtime.queue.routes[event], station='KJFK')
    elif change == 'scope':
        a.inputs = replace(a.inputs, scope=replace(a.inputs.scope, station='KJFK'))
    elif change == 'rule':
        a.inputs = replace(a.inputs, rule=replace(a.inputs.rule, source_event_sha256='f' * 64))
    elif change == 'bundle':
        a.inputs = replace(a.inputs, binding=replace(a.inputs.binding, bundle_sha256='f' * 64))
    elif change == 'worker':
        runner.runtime.worker_id = 'other'
    else:
        runner.census.plans.clear()
    with pytest.raises(EvidenceError, match='PREFLIGHT_FAILED'):
        asyncio.run(w.run_once('mutated'))
    assert not rig['calls']
    assert sc.evidence_status(rig['plan'], rig['store'])['total_commission_runs_recorded'] == 0


def test_unregistered_runner_cannot_claim_another_contract(rig):
    from copy import copy
    other = copy(rig['runner'])
    with pytest.raises(EvidenceError, match='TYPED_COHORT_REQUIRED'):
        sc.ShadowCommissionRunner(rig['plan'], other, release_git_sha=RELEASE,
                                 lifecycle=sc.ShadowLifecyclePolicy('test'))


@pytest.mark.parametrize('change', ['expired', 'wrong_rule', 'missing_proof', 'demoted', 'epoch', 'state', 'version', 'features'])
def test_preflight_rejects_ineligible_or_incompatible_state(rig, monkeypatch, bundle, change):
    if change in ('expired', 'wrong_rule', 'missing_proof'):
        manifest = cert.protected_reviews()
        review = manifest['reviews'][0]
        if change == 'expired':
            review['expires_at'] = rig['now'][0] - 1
        elif change == 'wrong_rule':
            review['rule_fingerprint'] = 'f' * 64
        else:
            review['capability_proofs'] = {}
        monkeypatch.setattr(cert, 'protected_reviews', lambda: manifest)
    elif change == 'demoted':
        state = rig['model_state'][0]
        rig['model_state'][0] = authority.transition(state, action='DEMOTE', expected_state_sha256=digest(state),
            now=21., reason='SYNTHETIC_TEST', size_multiplier=0.)
    elif change in ('epoch', 'state', 'features'):
        t = rig['plan'].targets[0]
        changes = dict(model_epoch=2) if change == 'epoch' else dict(model_state_sha256='f' * 64)
        if change == 'features':
            changes = dict(feature_contract=replace(CONTRACT, model_widths=(('model-1', 4),)))
        rig['plan'] = replace(rig['plan'], targets=(replace(t, **changes),))
    else:
        artifacts, values, refs, _ = bundle
        values = json.loads(canonical(values))
        for value in values.values():
            value['provenance']['model_version'] = 'wrong-version'
        values['CALIBRATION']['parameters']['probability_artifact_sha256'] = digest(values['PROBABILITY'])
        refs = {k: artifacts.put_artifact(v) for k, v in values.items()}
        key = artifacts.put_bundle(artifacts=refs, target='FINAL_CONTRACT_PAYOUT', feature_schema_sha256=CONTRACT.schema.sha256)
        rig['model_state'][0] = promote((artifacts, values, refs, key),
            authority.empty_state(rig['scope'].key, 'V11_SHADOW'))
    pf = report(rig)
    assert not pf['passed'], pf


def test_unavailable_protected_state_is_refused_and_audited(rig, monkeypatch):
    def missing():
        raise EvidenceError('PROTECTED_REVIEW_UNAVAILABLE')
    monkeypatch.setattr(cert, 'protected_reviews', missing)
    with pytest.raises(EvidenceError, match='PREFLIGHT_FAILED'):
        asyncio.run(wrapper(rig).run_once('missing-state'))
    status = sc.evidence_status(rig['plan'], rig['store'])
    assert status['refused_attempts_recorded'] == 1
    assert status['forward_commission_runs_recorded'] == status['total_commission_runs_recorded'] == 0
    assert not rig['calls']


def test_release_refusal_never_counts_as_completed_or_forward_run(rig):
    w = wrapper(rig, release='b' * 40)
    for _ in range(2):
        with pytest.raises(EvidenceError, match='PREFLIGHT_FAILED'):
            asyncio.run(w.run_once('refused'))
    status = sc.evidence_status(rig['plan'], rig['store'])
    assert status['attempts_recorded'] == status['refused_attempts_recorded'] == 1
    assert status['total_commission_runs_recorded'] == status['forward_commission_runs_recorded'] == 0
    assert not rig['calls']


def test_repeated_synthetic_admission_pins_and_other_plan_are_never_forward(rig):
    for i in range(12):
        StrategyAdmission(rig['store']).pin('repeat-' + str(i), **rig['admission_kw'])
    for plan in (rig['plan'], replace(rig['plan'], description='unrelated plan', created_at=1.)):
        status = sc.evidence_status(plan, rig['store'])
        assert status['targets'][0]['unqualified_admission_rows_scanned'] == 13
        assert status['targets'][0]['forward_admission_count'] == 0
        assert status['targets'][0]['qualifying_forward_sample_count'] == 0
        assert not status['targets'][0]['sample_target_reached']
        assert not status['forward_evidence_available'] and status['reason'] == sc.FORWARD_UNAVAILABLE


def test_recorded_forward_group_requires_original_plan_admission_and_counts_once(rig, monkeypatch):
    """Exercise status publication after the separate grouped verifier succeeds."""
    store = rig['store']
    admission = store.get('pin')
    plan = replace(rig['plan'], created_at=admission['body']['recorded_at'])
    decision = store.decision('forward-test-decision', event_id=rig['context'].event_id,
                              strategy='FUTURE_FORECAST', binding=rig['binding'],
                              evidence_ids=('model2',), feature_ready_at=rig['now'][0],
                              valuation_type='SETTLEMENT', target='FINAL_CONTRACT_PAYOUT',
                              outcome='GATED', reason='TEST_ONLY', explanation={},
                              expires_at=rig['now'][0] + 10)
    detail = dict(context=asdict(rig['context']), rule=asdict(rig['rule']),
                  binding=asdict(rig['binding']),
                  feature_schema_sha256=CONTRACT.schema.sha256,
                  admission_ref=dict(id=admission['id'], sha256=admission['sha256']),
                  rows=[dict(decision_id=decision['id'])])
    capture = store.audit('forward-test-capture', event_id=rig['context'].event_id,
                          kind='MEASUREMENT', details=detail)
    label_ids = {'market-1': 'local-label-1'}
    def verified_group(source, capture_id, labels):
        assert source is store and labels == label_ids
        chosen = source.get(capture_id)
        return dict(capture_id=chosen['id'], capture_sha256=chosen['sha256'],
                    admission_ref=detail['admission_ref'], labels=[dict(knowable_at=rig['now'][0])],
                    model_source_ids=['model2'], financial_authority=False)
    monkeypatch.setattr(sc, 'grouped_outcome', verified_group)
    row = sc.record_forward_admission(plan, store, capture_id=capture['id'], label_ids=label_ids)
    assert sc.record_forward_admission(plan, store, capture_id=capture['id'], label_ids=label_ids) == row
    status = sc.evidence_status(plan, store)
    assert status['forward_evidence_available']
    assert status['targets'][0]['forward_admission_count'] == 1
    assert status['targets'][0]['qualifying_forward_sample_count'] == 1
    assert status['financial_authority'] is False
    other = replace(plan, description='other forward intent')
    assert sc.evidence_status(other, store)['targets'][0]['forward_admission_count'] == 0
    changed = store.audit('forward-test-drift', event_id=rig['context'].event_id,
                          kind='MEASUREMENT', details=dict(detail, binding=dict(detail['binding'],
                                                                               code_commit='f' * 40)))
    with pytest.raises(EvidenceError, match='ADMISSION_PLAN_LINEAGE_MISMATCH'):
        sc.record_forward_admission(plan, store, capture_id=changed['id'], label_ids=label_ids)
    cross = store.audit('forward-test-cross-event', event_id='other-event',
                        kind='MEASUREMENT', details=detail)
    with pytest.raises(EvidenceError, match='PLAN_TARGET_MISMATCH'):
        sc.record_forward_admission(plan, store, capture_id=cross['id'], label_ids=label_ids)
    store.audit('forward-test-rule-change', event_id=rig['context'].event_id,
                kind='RULE_STATE', details={'test_only': True})
    rule_drift = store.audit('forward-test-rule-drift', event_id=rig['context'].event_id,
                             kind='MEASUREMENT', details=detail)
    with pytest.raises(EvidenceError, match='ADMISSION_PLAN_LINEAGE_MISMATCH'):
        sc.record_forward_admission(plan, store, capture_id=rule_drift['id'], label_ids=label_ids)
    rig['now'][0] = admission['body']['details']['assessment']['valid_until'] + 1
    stale = store.audit('forward-test-stale', event_id=rig['context'].event_id,
                        kind='MEASUREMENT', details=detail)
    with pytest.raises(EvidenceError, match='ADMISSION_PLAN_LINEAGE_MISMATCH'):
        sc.record_forward_admission(plan, store, capture_id=stale['id'], label_ids=label_ids)


def test_bounded_real_runner_is_idempotent_linked_and_not_forward(rig):
    w = wrapper(rig, iterations=2)
    async def run():
        rows = await w.run_bounded('bounded')
        repeat = await w.run_once('bounded:iteration:0')
        assert repeat['id'] == rows[0]['id'] and repeat['sha256'] == rows[0]['sha256']
        return rows
    rows = asyncio.run(run())
    status = sc.evidence_status(rig['plan'], rig['store'], config_sha256=w.config)
    assert status['total_commission_runs_recorded'] == 2, status
    assert status['completed_commission_runs_recorded'] == sum(r['body']['details']['outcome'] == 'BOUNDED_RUN_FINISHED' for r in rows)
    assert status['degraded_commission_runs_recorded'] == sum(r['body']['details']['outcome'] == 'DEGRADED' for r in rows)
    assert status['forward_commission_runs_recorded'] == 0
    assert status['history_complete'] and not status['totals_are_lower_bounds']
    assert sc.evidence_status(replace(rig['plan'], description='other'), rig['store'])['total_commission_runs_recorded'] == 0
    assert not rig['store'].records(kind='TRADE')
    assert all(not r['body']['details']['financial_authority'] for r in rows)


@pytest.mark.parametrize('field', ['queue', 'books'])
def test_mutated_runtime_policy_refused_before_collection(rig, field):
    w = wrapper(rig)
    if field == 'queue':
        q = rig['runner'].runtime.queue
        q.policy = replace(q.policy, max_pending_events=q.policy.max_pending_events + 1)
    else:
        census = rig['runner'].census
        census.book_policy = replace(census.book_policy,
                                     maximum_age_seconds=census.book_policy.maximum_age_seconds + 1)
    with pytest.raises(EvidenceError, match='COHORT_GRAPH_CHANGED'):
        w.preflight()
    with pytest.raises(EvidenceError, match='SHADOW_COMMISSION_PREFLIGHT_FAILED'):
        asyncio.run(w.run_once('changed-policy'))
    assert not rig['calls']
    assert sc.evidence_status(rig['plan'], rig['store'])['total_commission_runs_recorded'] == 0


@pytest.mark.parametrize('field', ['runtime', 'health', 'feed', 'account'])
def test_mutated_direct_runtime_policy_refused_before_collection(rig, field):
    w = wrapper(rig)
    rt = rig['runner'].runtime
    if field == 'runtime':
        rt.policy = replace(rt.policy, maximum_events=rt.policy.maximum_events + 1)
    elif field == 'health':
        rt.health.policy = replace(rt.health.policy,
            maximum_sample_age_seconds=rt.health.policy.maximum_sample_age_seconds + 1)
    elif field == 'feed':
        rt.feed.policy = replace(rt.feed.policy, maximum_reads=rt.feed.policy.maximum_reads + 1)
    else:
        rt.coordinator.policy = replace(rt.coordinator.policy, minimum_ev_per_share='0.09')
    with pytest.raises(EvidenceError, match='COHORT_GRAPH_CHANGED'):
        w.preflight()
    with pytest.raises(EvidenceError, match='SHADOW_COMMISSION_PREFLIGHT_FAILED'):
        asyncio.run(w.run_once('changed-' + field))
    assert not rig['calls']
    assert sc.evidence_status(rig['plan'], rig['store'])['total_commission_runs_recorded'] == 0


def test_mutated_maker_research_policy_refused_before_collection(maker_rig, monkeypatch):
    r = maker_rig
    scope = inputs(r)
    ordinary = app.TemperatureLane('temperature', replace(scope,
        scope=replace(scope.scope, strategy='FUTURE_FORECAST')),
        (target(r),), 'fixture', r['request'].valuation_policy, 10.)
    cfg = scoped_plan(r, ordinary)
    lane = app.MakerLane('maker', scope, (maker_terms(r),), 'fixture',
        r['request'].valuation_policy, 10., r['micro_policy'])
    event = replace(cfg.events[0], risk_inputs=scope, lanes=(lane,))
    cfg = replace(cfg, events=(event,),
        maker=app.MakerTelemetryPlan(r['policy'], MakerTelemetryPolicy('fixture'), (scope,)))
    synthetic_clock(r, monkeypatch)
    calls = []
    async def check():
        async with httpx.AsyncClient(transport=transport(r, calls)) as client:
            runner = app.assemble_candidate(r['store'], client, cfg, generation='shadow-maker-binding')
            cohort = candidate_cohort(runner)
            target_scope = sc.ShadowScopeTarget(scope.scope, 'F', 10, r['context'].event_id,
                r['model_state'][0]['epoch'], digest(r['model_state'][0]), CONTRACT)
            plan = sc.ShadowCommissionPlan(sc.PLAN_VERSION, NAMESPACE, 'worker', RELEASE,
                r['now'][0], 'synthetic maker policy binding', (target_scope,), cohort)
            w = sc.ShadowCommissionRunner(plan, runner, release_git_sha=RELEASE,
                lifecycle=sc.ShadowLifecyclePolicy('test'))
            factory = runner.runtime.evaluator.evaluator.adapters[r['context'].event_id].adapters[0][1].requests_for_event
            old_sha = factory.research.policy_sha
            factory.research.policy = replace(factory.research.policy, maximum_units='1')
            assert factory.research.policy_sha == old_sha
            with pytest.raises(EvidenceError, match='COHORT_GRAPH_CHANGED'):
                w.preflight()
            with pytest.raises(EvidenceError, match='SHADOW_COMMISSION_PREFLIGHT_FAILED'):
                await w.run_once('changed-maker-policy')
            assert not calls
            assert sc.evidence_status(plan, r['store'])['total_commission_runs_recorded'] == 0
    asyncio.run(check())


def test_telemetry_only_maker_policy_is_bound(maker_rig, monkeypatch):
    r = maker_rig
    maker_scope = inputs(r)
    ordinary = app.TemperatureLane('temperature', replace(maker_scope,
        scope=replace(maker_scope.scope, strategy='FUTURE_FORECAST')),
        (target(r),), 'fixture', r['request'].valuation_policy, 10.)
    cfg = replace(scoped_plan(r, ordinary),
        maker=app.MakerTelemetryPlan(r['policy'], MakerTelemetryPolicy('fixture'), (maker_scope,)))
    synthetic_clock(r, monkeypatch)
    async def check():
        async with httpx.AsyncClient() as client:
            runner = app.assemble_candidate(r['store'], client, cfg, generation='maker-telemetry-binding')
            assert runner.runtime.maker is not None
            assert candidate_cohort(runner)
            maker = runner.runtime.maker
            maker.policy = replace(maker.policy, maximum_units='1')
            with pytest.raises(EvidenceError, match='COHORT_GRAPH_CHANGED'):
                candidate_cohort(runner)
    asyncio.run(check())


def test_advancing_clock_bounded_restart_reuses_completed_audit(rig):
    w = wrapper(rig, iterations=2)
    first = asyncio.run(w.run_once('restart:iteration:0'))
    first_status = rig['store'].get('shadow-result:' + digest([w.config, 'restart:iteration:0']))
    first_calls = len(rig['calls'])
    rig['now'][0] += 1.
    results = asyncio.run(w.run_bounded('restart'))
    assert len(results) == 2 and results[1]['id'] != first['id']
    assert results[0] == first
    assert rig['store'].get(first_status['id']) == first_status
    assert len(rig['calls']) >= first_calls
    status = sc.evidence_status(rig['plan'], rig['store'])
    assert status['total_commission_runs_recorded'] == 2
    assert status['forward_commission_runs_recorded'] == 0


def test_advancing_clock_repeated_refusal_keeps_one_audit(rig):
    w = wrapper(rig, release='b' * 40)
    for _ in range(2):
        with pytest.raises(EvidenceError, match='SHADOW_COMMISSION_PREFLIGHT_FAILED'):
            asyncio.run(w.run_once('retry'))
        rig['now'][0] += 1.
    assert not rig['calls']
    status = sc.evidence_status(rig['plan'], rig['store'])
    assert status['refused_attempts_recorded'] == 1
    assert status['total_commission_runs_recorded'] == 0


def test_advancing_clock_repeated_freeze_refusal_keeps_one_audit(rig, monkeypatch):
    w = wrapper(rig)
    w._freeze(w.preflight())
    manifest = cert.protected_reviews()
    manifest['reviews'][0]['review_id'] = 'new-review'
    monkeypatch.setattr(cert, 'protected_reviews', lambda: manifest)
    for _ in range(2):
        with pytest.raises(EvidenceError, match='FROZEN_AUTHORITY_CHANGED'):
            asyncio.run(w.run_once('changed-review'))
        rig['now'][0] += 1.
    assert not rig['calls']
    assert sc.evidence_status(rig['plan'], rig['store'])['refused_attempts_recorded'] == 1


def test_existing_audit_with_changed_semantics_still_conflicts(rig):
    w = wrapper(rig)
    w._save('shadow-test-conflict', outcome='RUN_STARTED', run_id='one')
    rig['now'][0] += 1.
    with pytest.raises(EvidenceError, match='RECORD_ID_CONFLICT'):
        w._save('shadow-test-conflict', outcome='RUN_STARTED', run_id='two')


def test_authority_change_after_freeze_requires_review(rig, monkeypatch):
    w = wrapper(rig)
    w._freeze(w.preflight())
    manifest = cert.protected_reviews()
    manifest['reviews'][0]['review_id'] = 'new-review'
    monkeypatch.setattr(cert, 'protected_reviews', lambda: manifest)
    with pytest.raises(EvidenceError, match='FROZEN_AUTHORITY_CHANGED'):
        asyncio.run(w.run_once('changed-review'))
    assert not rig['calls']


def test_status_paginates_both_histories_and_exposes_a_bound(rig):
    store, plan = rig['store'], rig['plan']
    for i in range(1005):
        store.audit('history-' + str(i), event_id=sc.KEY, kind='RUNTIME_STATUS', details={'old': True})
    w = wrapper(rig, release='b' * 40)
    with pytest.raises(EvidenceError):
        asyncio.run(w.run_once('new-refusal'))
    for i in range(65):
        store.audit('old-pin-' + str(i), event_id='admission:' + plan.targets[0].scope.key,
                    kind='REGISTRY', details={'synthetic_stub': True})
    status = sc.evidence_status(plan, store)
    assert status['refused_attempts_recorded'] == 1
    assert status['targets'][0]['unqualified_admission_rows_scanned'] == 66
    assert status['history_complete']
    limited = sc.evidence_status(plan, store, maximum_rows=64)
    assert not limited['history_complete'] and limited['totals_are_lower_bounds']
    assert limited['run_history_coverage']['scanned_through_seq'] < limited['run_history_coverage']['frontier_seq']
    assert not limited['targets'][0]['sample_target_reached']


def test_unlinked_status_stub_does_not_count_as_run(rig):
    w = wrapper(rig)
    w._save('fake-result', run_id='fake', outcome='RUN_RECORDED', candidate_outcome='BOUNDED_RUN_FINISHED')
    status = sc.evidence_status(rig['plan'], rig['store'])
    assert status['total_commission_runs_recorded'] == status['forward_commission_runs_recorded'] == 0


def test_namespace_target_and_lifecycle_guards(rig):
    p, t = rig['plan'], rig['plan'].targets[0]
    with pytest.raises(EvidenceError, match='NAMESPACE'):
        replace(p, namespace='V11_PAPER')
    with pytest.raises(EvidenceError, match='DUPLICATE'):
        replace(p, targets=(t, t))
    for n in (0, 501, True):
        with pytest.raises(EvidenceError, match='SAMPLE_BOUND'):
            replace(t, sample_target=n)
    with pytest.raises(EvidenceError, match='WILDCARD'):
        replace(t.scope, station='*')
    with pytest.raises(EvidenceError, match='STATUS_NAMESPACE_MISMATCH'):
        sc.evidence_status(replace(p, namespace='ABLATION:other'), rig['store'])
    with pytest.raises(EvidenceError, match='ITERATION_BOUND'):
        sc.ShadowLifecyclePolicy('test', maximum_iterations=501)


def test_static_import_guard(tmp_path, monkeypatch):
    assert sc._no_financial_or_v10_imports()[0]
    fake = tmp_path / 'fake.py'; fake.write_text('import subprocess')
    monkeypatch.setattr(sc, '__file__', str(fake))
    assert not sc._no_financial_or_v10_imports()[0]


def test_plan_cannot_replace_actual_runner_contract(rig):
    wrong = replace(rig['plan'], cohort=replace(rig['plan'].cohort, graph_sha256='f' * 64))
    with pytest.raises(EvidenceError, match='COHORT_MISMATCH'):
        sc.ShadowCommissionRunner(wrong, rig['runner'], release_git_sha=RELEASE,
                                 lifecycle=sc.ShadowLifecyclePolicy('test'))


@pytest.mark.parametrize('widths', [(('gefs31', 31),), ((sc.GEFS_MODEL_ID, 30),), ((sc.GEFS_MODEL_ID, 31),)])
def test_gefs_cohort_requires_native_31_member_exact_day_contract(rig, setup, widths):
    from datetime import datetime, timezone
    from polymarket_scanner.v11.forecast_sources import ForecastPlan
    from polymarket_scanner.v11.gefs_sources import GEFSPlan
    initialized = datetime.fromtimestamp(rig['now'][0], timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    gefs = GEFSPlan(ForecastPlan(rig['rule'], setup[3], 50., 3600.), initialized)
    cfg = replace(rig['cfg'], gefs=(gefs,))
    # No run was started in this store; construction only inspects existing heads.
    runner = app.assemble_candidate(rig['store'], rig['client'], cfg, generation='shadow-gefs')
    cohort = candidate_cohort(runner)
    t = replace(rig['plan'].targets[0], feature_contract=replace(CONTRACT, model_widths=widths))
    if widths != ((sc.GEFS_MODEL_ID, 31),):
        with pytest.raises(EvidenceError, match='GEFS_FEATURE_CONTRACT_MISMATCH'):
            replace(rig['plan'], cohort=cohort, targets=(t,))
    else:
        plan = replace(rig['plan'], cohort=cohort, targets=(t,))
        # The old three-member bundle remains incompatible despite a correct new contract.
        assert not sc.preflight(plan, rig['store'], current_release_git_sha=RELEASE)['passed']
    assert not rig['calls']


def test_live_shaped_gefs_bundle_passes_without_claiming_forward_evidence(rig, setup, bundle):
    from datetime import datetime, timezone
    from polymarket_scanner.v11.forecast_sources import ForecastPlan
    from polymarket_scanner.v11.gefs_sources import GEFSPlan, PROVIDER
    from polymarket_scanner.v11.request_assembly import SourceSelector
    contract = replace(CONTRACT, model_widths=((sc.GEFS_MODEL_ID, 31),))
    artifacts, values, _, _ = bundle
    values = json.loads(canonical(values))
    for value in values.values():
        value['feature_schema_sha256'] = contract.schema.sha256
    values['FEATURES']['parameters'] = json.loads(canonical(asdict(contract.schema)))
    values['PROBABILITY']['parameters']['models'][0]['model_id'] = sc.GEFS_MODEL_ID
    values['CALIBRATION']['parameters']['probability_artifact_sha256'] = digest(values['PROBABILITY'])
    refs = {k: artifacts.put_artifact(v) for k, v in values.items()}
    key = artifacts.put_bundle(artifacts=refs, target='FINAL_CONTRACT_PAYOUT', feature_schema_sha256=contract.schema.sha256)
    state = promote((artifacts, values, refs, key), authority.empty_state(rig['scope'].key, 'V11_SHADOW'))
    rig['model_state'][0] = state
    initialized = datetime.fromtimestamp(rig['now'][0], timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    gefs = GEFSPlan(ForecastPlan(rig['rule'], setup[3], 50., 3600.), initialized)
    event = rig['cfg'].events[0]
    scoped = replace(event.risk_inputs, binding=replace(event.risk_inputs.binding, bundle_sha256=key),
        sources=(SourceSelector('MODEL', PROVIDER, gefs.source_identity, 3600.),))
    event = replace(event, risk_inputs=scoped, lanes=(replace(event.lanes[0], inputs=scoped),))
    cfg = replace(rig['cfg'], events=(event,), gefs=(gefs,))
    runner = app.assemble_candidate(rig['store'], rig['client'], cfg, generation='live-shaped-gefs')
    t = replace(rig['plan'].targets[0], feature_contract=contract, model_epoch=state['epoch'], model_state_sha256=digest(state))
    plan = replace(rig['plan'], cohort=candidate_cohort(runner), targets=(t,))
    w = sc.ShadowCommissionRunner(plan, runner, release_git_sha=RELEASE, lifecycle=sc.ShadowLifecyclePolicy('test'))
    pf = w.preflight()
    assert pf['passed'] and pf['runner_binding_verified'], pf
    assert pf['identities'][0]['model_bundle_sha256'] == key
    assert not pf['source_readiness_or_forward_acceptance']
    assert not sc.evidence_status(plan, rig['store'])['forward_evidence_available']
    assert not rig['calls']
