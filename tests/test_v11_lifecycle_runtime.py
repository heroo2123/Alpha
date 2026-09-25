"""Actual protected admissions through bounded PAPER withdrawal/reconciliation.

Model/review fixtures and prices are synthetic; no calibrated or live acceptance.
"""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11 import certification, model_registry
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.paper_cancellation import PaperCancellation, CancellationPolicy
from polymarket_scanner.v11.paper_runtime import PaperRuntime, RuntimePolicy
from polymarket_scanner.v11.runtime_health import SourceNeed
from polymarket_scanner.v11.strategy_admission import StrategyAdmission
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_model_governance import authority, review
from test_v11_strategy_pipeline import factory
from test_v11_basket_coordinator import rig, reserve, fill, proof
from test_v11_pws_admission import coordinator, joined, synthetic_proposal
from test_v11_paper_runtime import queue, GatedEvaluator
from test_v11_runtime_health import monitor, ready
from test_v11_position_management import inventory, reserved_exit
from test_v11_maker_research import rig as maker_rig, maker, propose


def runtime(rig, monkeypatch, *, policy=None, research=None):
    c = coordinator(rig); event = rig['context'].event_id
    strategy = rig.get('payout_scope', rig['scope']).strategy
    m = monitor(rig, monkeypatch, scopes={event:(strategy,)}, sources=(
        SourceNeed(event, strategy, 'OFFICIAL_OBSERVATION', 'fixture', rig['context'].station_id, 60.),))
    ready(rig, m)
    return PaperRuntime(c, queue(rig), m, policy or RuntimePolicy('lifecycle-test'),
        evaluator=GatedEvaluator(rig['store']), maker=research, generation='same-test-generation')


def demote(rig):
    state = rig['model_state'][0]
    rig['model_state'][0] = authority.transition(state, action='DEMOTE', expected_state_sha256=digest(state),
        now=21., reason='SYNTHETIC_DEGRADATION', size_multiplier=.5)


def state(rt): return rt.coordinator._state(rt.coordinator._head())


def checks(rt, row):
    return [rt.store.get(key)['body']['details'] for key in row['body']['details']['resting_admission_check_ids']]


@pytest.mark.parametrize('failure,reason', [
    ('model', 'MODEL_MANUAL_REVIEW'), ('station', 'QUARANTINE_REQUIRES_NEW_REVIEW'),
    ('capability', 'NEW_CAPABILITY_FAILURE_REQUIRES_REVIEW'),
    ('metadata', 'STATION_IDENTITY_MISSING_OR_CHANGED'),
    ('review', 'NO_REVIEWED_CERTIFICATION'), ('missing_model', 'PROTECTED_MODEL_STATE_UNAVAILABLE')])
def test_protected_degradation_reaches_existing_basket_cancellation(rig, setup, monkeypatch, failure, reason):
    reserve(rig); rt = runtime(rig, monkeypatch)
    before = deepcopy(state(rt)); pin = rt.store.get('pin'); original_model = deepcopy(rig['model_state'][0])
    registry = certification.StationRegistry(rt.store); scope = rig['scope']; metadata = setup[3]
    if failure == 'model': demote(rig)
    elif failure == 'station':
        registry.demote('drift', scope, state='CALIBRATION_DEGRADED', reason='EXPLICIT_EVIDENCE_FAILURE', evidence_ids=('model2',))
    elif failure == 'capability':
        registry.proof('failed', scope, capability='SOURCE_INTEGRITY', metadata_fingerprint=metadata.fingerprint,
            rule_fingerprint=rig['rule'].sha256, evidence_ids=('model2',), result='FAIL', checker_version='SYNTHETIC')
    elif failure == 'metadata': registry.observe('relocation', replace(metadata, elevation_m=metadata.elevation_m+1), raw_evidence_id='metadata-raw')
    elif failure == 'review': monkeypatch.setattr(certification, 'protected_reviews', lambda:dict(reviews=[]))
    else:
        monkeypatch.setattr(model_registry, 'protected_state', lambda **kw:(_ for _ in ()).throw(EvidenceError('PROTECTED_MODEL_STATE_UNAVAILABLE')))
    row = rt.tick('degraded')
    assert len(checks(rt, row)) == 3
    assert all(c['reason'] == reason for c in checks(rt, row))
    assert {i['status'] for i in state(rt)['intents'].values()} == {'CANCEL_REQUESTED'}
    assert Decimal(rt.coordinator.snapshot()['reserved_cash']) == Decimal('1.2')
    assert state(rt)['cash'] == before['cash'] and state(rt)['lots'] == before['lots']
    assert rt.store.get('pin') == pin and rig['model_state'][0]['active_bundle_sha256'] == original_model['active_bundle_sha256']
    for key in row['body']['details']['cancellation_report_ids']:
        d = rt.store.get(key)['body']['details']
        assert not d['real_cancel_sent']
        assert not d['independent_guardian_commissioned']
    assert rt.tick('degraded') == row


def test_healthy_pins_are_not_renewed_or_canceled(rig, monkeypatch):
    reserve(rig); rt = runtime(rig, monkeypatch); original = rt.store.get('pin')
    first = rt.tick('healthy'); assert all(c['passed'] for c in checks(rt, first))
    assert {i['status'] for i in state(rt)['intents'].values()} == {'RESERVED'}
    assert not first['body']['details']['state']['active_plans'] and rt.store.get('pin') == original


def test_late_fills_and_terminal_reconciliation_preserve_inventory(rig, monkeypatch):
    reserve(rig); rt = runtime(rig, monkeypatch); demote(rig); rt.tick('withdraw')
    fill(rig, 0, '1', '.2')
    for leg in range(3):
        key = proof(rig, 'basket:leg:'+str(leg), 'terminal:'+str(leg), 'PAPER_TERMINAL', status='CANCELED',
            cumulative_fill_units='1' if leg == 0 else '0', all_fills_reconciled=True,
            terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL')
        rt.coordinator.reconcile_terminal('reconcile:'+str(leg), key)
    row = rt.tick('observed')
    assert not row['body']['details']['state']['active_plans']
    assert Decimal(rt.coordinator.snapshot()['reserved_cash']) == 0
    assert Decimal(rt.coordinator.snapshot()['held_cost_basis']) == Decimal('.2')
    assert state(rt)['lots']['fill-0']['units'] == '1'


def test_independent_recovery_never_resurrects_canceled_intent(rig, bundle, monkeypatch):
    reserve(rig); rt = runtime(rig, monkeypatch); demote(rig); rt.tick('withdraw')
    s = rig['model_state'][0]
    rig['model_state'][0] = authority.transition(s, action='RESTORE_OVERLAY', expected_state_sha256=digest(s),
        now=22., reason='INDEPENDENT_REVIEW_FIXTURE', review=review(bundle,s,action='RESTORE_OVERLAY',review_id='restore',size=.5))
    rt.tick('after-review')
    assert {i['status'] for i in state(rt)['intents'].values()} == {'CANCEL_REQUESTED'}
    assert Decimal(rt.coordinator.snapshot()['reserved_cash']) == Decimal('1.2')
    with pytest.raises(EvidenceError, match='CHANGED_RECOMPUTE'):
        StrategyAdmission(rt.store).revalidate('pin',context=rig['context'],rule=rig['rule'],
            binding=rt.store.get('pin')['body']['details']['request']['binding'],
            strategies=(rig['scope'].strategy,))


def test_registration_interruption_resumes_exact_plan_without_duplicate_request(rig, monkeypatch):
    reserve(rig); rt = runtime(rig, monkeypatch); demote(rig); real = rt.cancellation.plan
    def interrupt(key, **kw):
        if key.startswith('admission-plan:'): raise RuntimeError('POWER_LOSS')
        return real(key, **kw)
    monkeypatch.setattr(rt.cancellation, 'plan', interrupt)
    with pytest.raises(RuntimeError, match='POWER_LOSS'): rt.tick('interrupted')
    pending = deepcopy(rt._head()['body']['details']['state']['active_plans'])
    assert len(pending) == 1
    monkeypatch.setattr(rt.cancellation, 'plan', real)
    row = rt.tick('resume'); assert len(row['body']['details']['state']['active_plans']) == 3
    assert set(pending) <= row['body']['details']['state']['active_plans'].keys()
    requests = [r for r in rt.store.records(kind='COORDINATOR_EVENT') if r['body']['details'].get('request',{}).get('status') == 'CANCEL_REQUESTED']
    assert len(requests) == 3
    rt.tick('again')
    assert len([r for r in rt.store.records(kind='COORDINATOR_EVENT') if r['body']['details'].get('request',{}).get('status') == 'CANCEL_REQUESTED']) == 3


def test_rotation_covers_healthy_prefix_and_pending_plans_do_not_block_siblings(rig, monkeypatch):
    reserve(rig); rt = runtime(rig, monkeypatch, policy=RuntimePolicy('tiny',maximum_updates=1,maximum_cancel_plans=1))
    one = rt.tick('healthy'); assert len(checks(rt,one)) == 1
    demote(rig)
    for tick in range(3):
        row = rt.tick('degraded:'+str(tick)); assert len(checks(rt,row)) == 1
    assert {i['status'] for i in state(rt)['intents'].values()} == {'CANCEL_REQUESTED'}
    assert len(row['body']['details']['state']['active_plans']) == 3


def test_reducing_sell_is_preserved_when_model_demotes(rig, monkeypatch):
    inventory(rig); p,_ = reserved_exit(rig); rt = runtime(rig, monkeypatch); demote(rig)
    row = rt.tick('keep-exit')
    assert not checks(rt,row) and state(rt)['intents'][p.proposal_id]['status'] == 'RESERVED'


@pytest.mark.parametrize('which', ['observation_scope','payout_scope'])
def test_pws_either_separate_model_demotion_withdraws_same_single_risk(joined, monkeypatch, which):
    p = synthetic_proposal(joined); c = coordinator(joined)
    assert c.coordinate('reserve',(p,))['body']['details']['reserved_intent_ids'] == ['one']
    rt = runtime(joined, monkeypatch); key=joined[which].key; old=joined['states'][key]
    joined['states'][key] = authority.transition(old, action='DEMOTE', expected_state_sha256=digest(old),
        now=21., reason='SYNTHETIC_LEAD_DEGRADATION', size_multiplier=.5)
    row=rt.tick('withdraw')
    assert checks(rt,row)[0]['reason'] == 'MODEL_MANUAL_REVIEW'
    assert state(rt)['intents']['one']['status']=='CANCEL_REQUESTED'
    assert len(state(rt)['intents']) == 1 and Decimal(c.snapshot()['reserved_cash']) > 0


def test_maker_retires_without_new_books_or_telemetry(maker_rig, monkeypatch):
    assert propose(maker_rig)['outcome']=='OBSERVING_RESEARCH_QUOTE'
    research=maker(maker_rig)
    # The runtime requires the same common-account object as research.
    rt=runtime(maker_rig,monkeypatch)
    rt=PaperRuntime(research.coordinator,rt.queue,rt.health,rt.policy,evaluator=rt.evaluator,maker=research)
    demote(maker_rig); row=rt.tick('maker-drift')
    quote=research._state(research._head())['quote']
    assert quote['status']=='RETIRED' and quote['retirement_reason']=='MODEL_MANUAL_REVIEW'
    assert len(row['body']['details']['retired_quote_ids']) == 1
    assert research.coordinator._head() is None


def test_check_cas_rejects_account_change_and_historical_replay_does_not_refresh(rig, monkeypatch):
    reserve(rig); bridge=PaperCancellation(coordinator(rig),CancellationPolicy('test',2,10))
    check=bridge.check_admission('healthy-check',intent_id='basket:leg:0')
    demote(rig); assert bridge.check_admission('healthy-check',intent_id='basket:leg:0') == check
    with pytest.raises(EvidenceError,match='REPLAY_CONFLICT'):bridge.check_admission('healthy-check',intent_id='basket:leg:1')
    original=rig['store'].safety_audit
    def race(key,**kw):
        if key=='raced-check':bridge.coordinator.transition('raced-cancel',intent_id='basket:leg:1',status='CANCEL_REQUESTED')
        return original(key,**kw)
    monkeypatch.setattr(rig['store'],'safety_audit',race)
    with pytest.raises(EvidenceError,match='GUARDED_STATE_CHANGED'):bridge.check_admission('raced-check',intent_id='basket:leg:0')


def test_delayed_failure_plan_keeps_exact_identity_after_review(rig, bundle, monkeypatch):
    reserve(rig); bridge=PaperCancellation(coordinator(rig),CancellationPolicy('test',2,10)); demote(rig)
    old=bridge.check_admission('failed-check',intent_id='basket:leg:0')
    s=rig['model_state'][0]
    rig['model_state'][0]=authority.transition(s,action='RESTORE_OVERLAY',expected_state_sha256=digest(s),now=22.,
        reason='REVIEW_FIXTURE',review=review(bundle,s,action='RESTORE_OVERLAY',review_id='restore',size=.5))
    plan=bridge.plan('delayed-plan',trigger_id=old['id'])
    assert list(plan['body']['details']['state']['items'])==['basket:leg:0']
    bridge.advance('deliver',plan_id='delayed-plan')
    assert bridge.coordinator._state(bridge.coordinator._head())['intents']['basket:leg:1']['status']=='RESERVED'


def test_lifecycle_withdrawal_reaches_pinned_daily_audit(rig, monkeypatch):
    from polymarket_scanner.v11.audit_reports import AuditScheduler, AuditWorker
    from test_v11_audit_reports import finish
    reserve(rig); rt=runtime(rig,monkeypatch); demote(rig); rt.tick('withdraw')
    rig['now'][0]=(int(rig['now'][0]//86400)+1)*86400+1
    AuditScheduler(rt.store,rt.audits.policy).request_due()
    worker=AuditWorker(rt.coordinator,rt.audits.policy)
    for _ in range(3):
        report=finish(worker)['body']['details']
        if report['operations']['resting_admission_checks'].get('MODEL_MANUAL_REVIEW'): break
    else: pytest.fail('degradation missing from bounded audit')
    assert report['operations']['resting_admission_checks']['MODEL_MANUAL_REVIEW']==3
    assert report['operations']['paper_cancellation_outcomes']['PENDING_TERMINAL_RECONCILIATION']==3
    assert report['coverage']['archive_scan_complete'] and not report['financial_authority']


def test_failed_checks_during_clock_regression_cannot_repair_capture_chronology(rig, monkeypatch):
    reserve(rig); rt=runtime(rig,monkeypatch); demote(rig)
    old=rig['now'][0]; rig['now'][0]-=10
    row=rt.cancellation.check_admission('backward-failure',intent_id='basket:leg:0')
    assert row['body']['recorded_at']==old-10 and not row['body']['details']['passed']
    rt.cancellation.plan('backward-plan',trigger_id=row['id'])
    rt.cancellation.advance('backward-delivery',plan_id='backward-plan')
    assert state(rt)['intents']['basket:leg:0']['status']=='CANCEL_REQUESTED'
    with pytest.raises(EvidenceError,match='CLOCK_REGRESSION'):
        rt.store.capture('cannot-repair',event_id=rig['context'].event_id,kind='MODEL',provider='fixture',
            source_identity='test',revision='1',payload={},evidence_class='SYNTHETIC')


def test_altered_intent_cannot_receive_delayed_cancellation(rig):
    reserve(rig); bridge=PaperCancellation(coordinator(rig),CancellationPolicy('test',2,10)); demote(rig)
    row=bridge.check_admission('failed',intent_id='basket:leg:0')
    head=bridge.coordinator._head(); altered=bridge.coordinator._state(head)
    altered['intents']['basket:leg:0']['thesis_id']='different-intent'
    bridge.coordinator._commit('synthetic-corruption',dict(action='SYNTHETIC_FAULT'),head,altered,{})
    with pytest.raises(EvidenceError,match='IDENTITY_CHANGED'):bridge.plan('delayed',trigger_id=row['id'])


@pytest.mark.parametrize('field,value',[('financial_authority',True),('authority_restored',True),('independent_guardian_commissioned',True)])
def test_clock_tolerant_check_telemetry_never_accepts_authority_claims(rig,field,value):
    reserve(rig); bridge=PaperCancellation(coordinator(rig),CancellationPolicy('test',2,10))
    row=bridge.check_admission('healthy',intent_id='basket:leg:0'); d=deepcopy(row['body']['details']); d[field]=value
    with pytest.raises(EvidenceError,match='SAFETY_AUDIT_CANNOT_AUTHORIZE_ACTION'):
        rig['store'].safety_audit('forged-authority',event_id=row['event_id'],kind='MEASUREMENT',details=d)


def test_changed_runtime_safety_policy_does_not_migrate_existing_state_silently(rig,monkeypatch):
    rt=runtime(rig,monkeypatch); rt.tick('current'); head=rt._head(); before=head['body']['details']
    legacy=deepcopy(before); legacy['config_sha256']='0'*64
    rt.store.safety_audit('old-runtime-policy',event_id=head['event_id'],kind='RUNTIME_STATUS',details=legacy)
    with pytest.raises(EvidenceError,match='CONFIGURATION_CHANGED_REVIEW_REQUIRED'):rt.tick('changed')
    assert rt.store.get(head['id'])['body']['details']==before
