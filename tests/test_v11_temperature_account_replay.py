"""Prepared entry joins with an explicitly synthetic payout oracle, never calibration."""
from copy import deepcopy
from dataclasses import replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11 import causal_replay, strategy_pipeline, valuation
from polymarket_scanner.v11.account_replay import replay_account_command
from polymarket_scanner.v11.audit_reports import AuditPolicy, AuditScheduler, AuditWorker
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.learning_sources import LearningSourceView
from polymarket_scanner.v11.model_registry import ActiveModelRegistry
from polymarket_scanner.v11.paper_coordinator import PaperCoordinator
from test_v11_audit_reports import finish
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_model_governance import new_bundle, promote
from test_v11_pws_admission import joined, coordinator
from test_v11_source_release import release_factory, pin
from test_v11_strategy_pipeline import factory

STRATEGIES = ('FUTURE_FORECAST', 'SAME_DAY_LATE_LOCK', 'PWS_OBSERVATION_LEAD',
              'SOURCE_SHOCK', 'RELEASE_OPPORTUNITY')


@pytest.fixture
def entry_rig(request, factory, release_factory, monkeypatch):
    predict = valuation._prediction

    def synthetic_payout(*args, **kwargs):
        model, payout = predict(*args, **kwargs)
        # Run the real binding/age/target/bound checks first. The production
        # contract deliberately permits only vacuous bounds, so the accepted
        # branch needs this explicit test-only payout oracle. Do not relabel the
        # real prediction as calibrated or claim production accepted entries.
        return dict(model, synthetic_payout_test_oracle=True), Decimal('.7')

    monkeypatch.setattr(valuation, '_prediction', synthetic_payout)
    strategy = getattr(request, 'param', 'FUTURE_FORECAST')
    if strategy == 'PWS_OBSERVATION_LEAD':
        r = request.getfixturevalue('joined')
    elif strategy in {'SOURCE_SHOCK', 'RELEASE_OPPORTUNITY'}:
        r = release_factory(strategy); pin(r)
    else:
        r = factory(strategy)
    r['actual_prediction_value'] = predict
    return r


def prepare(r, *, key='entry', c=None):
    c = c or coordinator(r)
    if c._head() is None:
        c.recover('initial-account')
    engine = strategy_pipeline.TemperatureStrategies(r['store'])
    decision = engine.evaluate(key, r['request'])['body']['details']
    assert decision['outcome'] == 'ACCEPT_RESEARCH', (decision['outcome'], decision['reason'])
    return c, engine.proposal(key)


def replay(r, key='batch', c=None, **kwargs):
    return replay_account_command(c or coordinator(r), key,
        policy=causal_replay.ReplayPolicy('entry-account', 5.), replay_valuations=True, **kwargs)


def row(result):
    assert result['status'] == 'EFFECTS_REPRODUCED', result
    assert result['prepared_valuations']['prepared_count'] == 1, result
    return result['prepared_valuations']['rows'][0]


@pytest.mark.parametrize('entry_rig', STRATEGIES, indirect=True)
def test_five_original_entry_proposals_reach_read_only_account_replay(entry_rig, monkeypatch):
    r = entry_rig; c, p = prepare(r); command = c.coordinate('batch', (p,))
    assert command['body']['details']['reserved_intent_ids'] == ['entry:proposal']
    before = r['store'].pin_read_view()
    def forbidden(*args, **kwargs):
        pytest.fail('Replay invoked writes, a nested snapshot, or current admission')
    monkeypatch.setattr(ActiveModelRegistry, 'pin', forbidden)
    monkeypatch.setattr(ActiveModelRegistry, 'revalidate', forbidden)
    monkeypatch.setattr('polymarket_scanner.v11.strategy_admission.StrategyAdmission.revalidate', forbidden)
    monkeypatch.setattr('polymarket_scanner.v11.evidence.EvidenceStore.audit', forbidden)
    monkeypatch.setattr(causal_replay, 'learning_source_view', forbidden)
    result = replay(r); proof = row(result)
    assert proof['status'] == 'ECONOMICS_REPRODUCED', proof
    assert all(proof['comparisons'].values())
    assert proof['decision_ref']['id'] == 'entry' and proof['valuation_ref']['id'] == p.valuation_id
    assert proof['original_valuation_sha256'] == proof['recomputed_valuation_sha256']
    assert proof['original_prediction_sha256'] == proof['recomputed_prediction_sha256']
    assert proof['account_snapshot_ref']['id'] == 'initial-account'
    assert not result['prepared_valuations']['full_preparation_replayed']
    assert not result['financial_authority'] and r['store'].pin_read_view() == before
    if p.preconfirmation_id:
        assert proof['pws_observation']['observation_model']['bundle_sha256'] != proof['model']['bundle_sha256']
        assert not proof['pws_observation']['observation_is_payout_or_exit']
    if p.source_release_id:
        assert proof['source_release']['previous_official_ref']['id'] == 'previous-exact'
        assert not proof['source_release']['settlement_finality']


@pytest.mark.parametrize('entry_rig', STRATEGIES, indirect=True)
def test_unmodified_uncalibrated_model_still_emits_no_entry(entry_rig, monkeypatch):
    r = entry_rig
    monkeypatch.setattr(valuation, '_prediction', r['actual_prediction_value'])
    decision = strategy_pipeline.TemperatureStrategies(r['store']).evaluate('uncalibrated', r['request'])['body']['details']
    assert decision['outcome'] == 'REJECT' and decision['proposal'] is None
    assert decision['reason'] == 'CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD'


@pytest.mark.parametrize('change', ['proposal_id', 'thesis_id', 'expires_at'])
def test_valid_account_candidate_without_exact_original_proposal_is_gated(entry_rig, change):
    r = entry_rig; c, p = prepare(r)
    p = replace(p, **{change:p.expires_at-1 if change == 'expires_at' else 'different'})
    c.coordinate('batch', (p,))
    proof = row(replay(r))
    assert proof['status'] == 'GATED' and proof['reason'] == 'PORTFOLIO_REPLAY_ORIGINAL_ENTRY_PROPOSAL_BINDING'
    assert not proof['economic_match']


@pytest.mark.parametrize('damage', ['missing', 'sequence', 'timestamp'])
def test_original_decision_must_precede_command(entry_rig, monkeypatch, damage):
    r = entry_rig; c, p = prepare(r); command = c.coordinate('batch', (p,)); get = LearningSourceView.get
    def changed(self, key):
        original = get(self, key)
        if key == 'entry':
            if damage == 'missing':
                raise EvidenceError('ORIGINAL_DECISION_UNAVAILABLE')
            original = deepcopy(original)
            if damage == 'sequence': original['seq'] = command['seq']+1
            else: original['body']['recorded_at'] = command['body']['recorded_at']+1
        return original
    monkeypatch.setattr(LearningSourceView, 'get', changed)
    proof = row(replay(r))
    assert proof['status'] == 'GATED' and not proof['economic_match']
    assert not proof['comparisons'] and 'recomputed_valuation_sha256' not in proof


def test_later_model_source_book_and_account_cannot_replace_originals(entry_rig, bundle):
    r = entry_rig; c, p = prepare(r); c.coordinate('batch', (p,)); original = replay(r)
    r['model_state'][0] = promote(new_bundle(bundle), r['model_state'][0], review_id='later')
    for key in ('model2', 'book2'):
        source = r['store'].get(key); b = source['body']
        r['store'].capture(key+'-later', event_id=source['event_id'], kind=source['kind'],
            provider=b['provider'], source_identity=b['source_identity'], revision='later',
            observed_at=b['observed_at'], issued_at=b['issued_at'], payload=b['payload'], evidence_class='SYNTHETIC')
    r['now'][0] += 86400; c.recover('later-account')
    assert replay(r) == original


def test_numeric_regression_is_mismatch_even_when_account_effects_match(entry_rig, monkeypatch):
    r = entry_rig; c, p = prepare(r); c.coordinate('batch', (p,))
    value = causal_replay.settlement_entry_details
    monkeypatch.setattr(causal_replay, 'settlement_entry_details',
        lambda *a, **kw:dict(value(*a, **kw), conservative_ev_total='999'))
    proof = row(replay(r))
    assert proof['status'] == 'MISMATCH' and proof['comparisons']['prediction']
    assert not proof['comparisons']['valuation'] and not proof['economic_match']


@pytest.mark.parametrize('entry_rig', ['PWS_OBSERVATION_LEAD', 'SOURCE_SHOCK'], indirect=True)
def test_auxiliary_mismatch_cannot_be_hidden_by_matching_payout(entry_rig, monkeypatch):
    r = entry_rig; c, p = prepare(r); c.coordinate('batch', (p,))
    name = '_pws_pair' if p.preconfirmation_id else '_received_release'
    original = getattr(causal_replay, name)
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        result['comparisons'][next(iter(result['comparisons']))] = False
        return result
    monkeypatch.setattr(causal_replay, name, changed)
    proof = row(replay(r))
    assert proof['comparisons']['prediction'] and proof['comparisons']['valuation']
    assert proof['status'] == 'MISMATCH' and not proof['economic_match']


def test_allocation_rejects_and_preparation_rejects_keep_distinct_denominators(entry_rig):
    r = entry_rig; base = coordinator(r)
    c = PaperCoordinator(r['store'], policy=replace(base.policy, initial_hypothetical_cash='.1'),
                         correlation=base.correlation, limits=base.limits)
    c, p = prepare(r, c=c)
    c.coordinate('batch', (p, replace(p, proposal_id='expired', expires_at=r['now'][0]-1)))
    result = replay(r, c=c); proof = row(result)
    assert proof['status'] == 'ECONOMICS_REPRODUCED'
    assert result['prepared_valuations']['rejected_preparation_count'] == 1
    assert not result['preparation_recomputed'] and not c._state(c._head())['intents']


def test_scheduled_audit_retains_entry_and_early_rejections_in_distinct_populations(entry_rig, monkeypatch):
    r = entry_rig; c, p = prepare(r); c.coordinate('batch', (p,))
    engine = strategy_pipeline.TemperatureStrategies(r['store'])
    monkeypatch.setattr(valuation, '_prediction', r['actual_prediction_value'])
    assert engine.evaluate('rejected', r['request'])['body']['details']['outcome'] == 'REJECT'
    assert engine.evaluate('expired', replace(r['request'], expires_at=r['now'][0]-1))['body']['details']['outcome'] == 'GATED'
    policy = AuditPolicy('entry-account', records_per_step=256,
        replay=causal_replay.ReplayPolicy('entry-account', 5.),
        account_replay=causal_replay.ReplayPolicy('entry-account', 5.), account_valuation_replay=True)
    r['now'][0] = (int(r['now'][0]//86400)+1)*86400+1
    AuditScheduler(r['store'], policy).request_due()
    report = finish(AuditWorker(c, policy))['body']['details']
    coverage = report['account_replay']['prepared_valuation_coverage']
    # Removing the synthetic payout oracle also proves that ordinary production
    # numerics do not accept/reproduce its hypothetical positive value.
    assert coverage['prepared_count'] == 1 and coverage['economic_matches'] == 0
    assert not report['coverage']['retained_prepared_valuations_reproduced']
    assert report['economic_replay']['retained_decision_count'] == 3
    assert report['economic_replay']['economic_matches'] == 1
    assert report['economic_replay']['complete_retained_selection']
    assert not report['acceptance_granted']


def test_temperature_budget_exhaustion_discards_preceding_audit_matches(entry_rig, monkeypatch):
    from polymarket_scanner.v11 import portfolio_replay
    from test_v11_portfolio_replay import audit
    r = entry_rig; c, p = prepare(r); c.coordinate('batch', (p,))
    original = portfolio_replay.replay_temperature_in_view; elapsed = [0.]
    def spent(*args, **kwargs):
        result = original(*args, **kwargs); elapsed[0] = 6.; return result
    monkeypatch.setattr(portfolio_replay, 'replay_temperature_in_view', spent)
    result = audit(r, monotonic=lambda:elapsed[0])
    assert result['status'] == 'GATED' and not result['rows'] and not result['effect_matches']
    assert not result['prepared_valuation_coverage']['complete']
    assert not result['prepared_valuation_coverage']['economic_matches']


@pytest.mark.parametrize('missing', ['entry:start', 'pin', 'model2', 'book2'])
def test_missing_original_inputs_never_receive_positive_valuation_credit(entry_rig, monkeypatch, missing):
    r = entry_rig; c, p = prepare(r); c.coordinate('batch', (p,)); get = LearningSourceView.get
    def absent(self, key):
        if key == missing: raise EvidenceError('ORIGINAL_ENTRY_INPUT_UNAVAILABLE')
        return get(self, key)
    monkeypatch.setattr(LearningSourceView, 'get', absent)
    result = replay(r)
    if result['status'] == 'GATED':
        assert not result['effects_match'] and 'prepared_valuations' not in result
    else:
        proof = row(result)
        assert proof['status'] == 'GATED' and not proof['economic_match']


def test_entry_report_recovers_without_replaying_or_reserving(entry_rig, monkeypatch):
    from polymarket_scanner.v11 import account_replay
    r = entry_rig; c, p = prepare(r); c.coordinate('batch', (p,)); head = c._head()
    config = AuditPolicy('entry-account', records_per_step=256,
        account_replay=causal_replay.ReplayPolicy('entry-account', 5.), account_valuation_replay=True)
    r['now'][0] = (int(r['now'][0]//86400)+1)*86400+1
    AuditScheduler(r['store'], config).request_due(); worker = AuditWorker(c, config); save = worker._save
    def crash(head, state, **kwargs):
        if kwargs.get('outcome') == 'AUDIT_COMPLETE': raise OSError('SYNTHETIC_AFTER_REPORT')
        return save(head, state, **kwargs)
    monkeypatch.setattr(worker, '_save', crash)
    with pytest.raises(OSError, match='SYNTHETIC_AFTER_REPORT'): finish(worker)
    report = r['store'].latest(kind='RUNTIME_STATUS', event_id='v11-audit-report:DAILY')
    assert report['body']['details']['account_replay']['prepared_valuation_coverage']['all_prepared_valuations_reproduced']
    monkeypatch.setattr(account_replay, 'replay_account_command',
        lambda *a, **kw:pytest.fail('Recomputed already published account report'))
    assert finish(AuditWorker(c, config)) == report and c._head() == head


def test_finite_candidate_schedules_prepared_temperature_audit(entry_rig, monkeypatch):
    import asyncio
    import httpx
    from polymarket_scanner.v11 import candidate_assembly as app
    from test_v11_candidate_assembly import scoped_plan, synthetic_clock, transport
    from test_v11_request_assembly import inputs, target
    from test_v11_runtime_health import ready, advance
    r = entry_rig; c, p = prepare(r); c.coordinate('batch', (p,))
    lane = app.TemperatureLane('temperature', inputs(r), (target(r),), 'fixture', r['request'].valuation_policy, 10.)
    cfg = scoped_plan(r, lane)
    cfg = replace(cfg, audits=replace(cfg.audits, records_per_step=256,
        replay=causal_replay.ReplayPolicy('entry-account', 5.),
        account_replay=causal_replay.ReplayPolicy('entry-account', 5.), account_valuation_replay=True))
    synthetic_clock(r, monkeypatch); calls = []
    async def run():
        async with httpx.AsyncClient(transport=transport(r, calls)) as client:
            candidate = app.assemble_candidate(r['store'], client, cfg, generation='entry-audit')
            ready(r, candidate.runtime.health)
            await candidate.run('entry-source-decision')
            assert set(c._state(c._head())['intents']) == {p.proposal_id}
            advance(r, (int(r['now'][0]//86400)+1)*86400+1-r['now'][0])
            for i in range(12):
                await candidate.run('entry-audit-'+str(i))
                report = r['store'].latest(kind='RUNTIME_STATUS', event_id='v11-audit-report:DAILY')
                if report and report['body']['details']['account_replay']['retained_command_count']:
                    return report['body']['details']
            pytest.fail('Candidate did not finish scheduled temperature account audit')
    report = asyncio.run(run()); coverage = report['account_replay']['prepared_valuation_coverage']
    assert coverage['complete'] and coverage['prepared_count'] == coverage['economic_matches'] == 1
    assert report['coverage']['retained_prepared_valuations_reproduced']
    assert report['economic_replay']['retained_decision_count'] >= 1
    assert calls and all(req.method == 'GET' for req in calls)
    assert not r['store'].records(kind='TRADE') and not report['acceptance_granted']
