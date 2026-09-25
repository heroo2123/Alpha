from copy import deepcopy
from dataclasses import replace

import pytest

from polymarket_scanner.v11 import account_replay as replay
from polymarket_scanner.v11.audit_reports import AuditPolicy, AuditScheduler, AuditWorker
from polymarket_scanner.v11.causal_replay import ReplayPolicy
from polymarket_scanner.v11.evidence import EvidenceError, digest
from polymarket_scanner.v11.learning_sources import LearningSourceView
from polymarket_scanner.v11.paper_coordinator import ACCOUNT_KEY, PaperCoordinator
from polymarket_scanner.v11.performance import PerformanceLab
from test_v11_paper_coordinator import rig, coordinator, proposal, proof, fill
from test_v11_audit_reports import finish


def run(rig, key, **kw):
    return PerformanceLab(coordinator(rig)).replay_account_command(key, policy=ReplayPolicy('fixture'), **kw)


def commands(rig):
    c = coordinator(rig)
    c.coordinate('batch', (proposal(rig, 'weak', bucket=1, ev='.1'), proposal(rig, 'strong')))
    c.transition('submit', intent_id='strong', status='SUBMITTING')
    c.recover('restart')
    c.transition('ack', intent_id='strong', status='ACKNOWLEDGED')
    fill(rig, 'strong')
    c.transition('cancel', intent_id='strong', status='CANCEL_REQUESTED')
    c.transition('late-ack', intent_id='strong', status='ACKNOWLEDGED')
    fill(rig, 'strong', key='late-fill', units='1', collateral='.4')
    c.reconcile_terminal('terminal', proof(rig, 'strong', 'terminal-proof', 'PAPER_TERMINAL',
        status='CANCELED', cumulative_fill_units='6', all_fills_reconciled=True,
        terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL'))
    c.record_fill('duplicate', 'fill')
    return rig['store'].records(kind='COORDINATOR_EVENT', event_id=ACCOUNT_KEY)


def test_lifecycle_replays_original_reservations_ambiguity_partial_fills_cancel_and_terminal_read_only(rig, monkeypatch):
    rows = commands(rig); store = rig['store']; before = store.pin_read_view(())
    original_get = LearningSourceView.get
    def readonly(self, key):
        assert self._db.execute('PRAGMA query_only').fetchone()[0] == 1
        return original_get(self, key)
    monkeypatch.setattr(LearningSourceView, 'get', readonly)
    def forbidden(*a, **kw): pytest.fail('Replay entered a command or current admission')
    for name in ('coordinate','transition','recover','record_fill','reconcile_terminal','_commit','_prepare'):
        monkeypatch.setattr(PaperCoordinator, name, forbidden)
    from polymarket_scanner.v11.strategy_admission import StrategyAdmission
    monkeypatch.setattr(StrategyAdmission, 'revalidate', forbidden)
    rig['now'][0] += 3*86400
    for row in rows:
        d = run(rig, row['id'])
        assert d['status'] == 'EFFECTS_REPRODUCED', d
        assert all(d['comparisons'].values()) and d['new_economic_commands'] == 0
        assert not d['full_control_flow_replayed'] and not d['preparation_recomputed'] and not d['financial_authority']
    assert store.pin_read_view(()) == before
    assert rows[0]['body']['details']['reserved_intent_ids'] == ['strong']
    assert rows[-1]['body']['details']['risk']['reserved_cash'] == '0'


def test_prepared_inputs_are_before_ranking_and_include_every_preparation_rejection(rig):
    c = coordinator(rig); bad = replace(proposal(rig, 'bad'), valuation_id='missing-value')
    p = proposal(rig, 'good'); row = c.coordinate('batch', (bad, p))
    inputs = row['body']['details']['effect_inputs']['preparation']
    assert [p['proposal_id'] for p in inputs['prepared']] == ['good']
    assert 'conservative_ev_per_capital' not in inputs['prepared'][0]
    assert [r['proposal_id'] for r in inputs['rejected']] == ['bad']
    assert run(rig, 'batch')['effects_match']
    c.coordinate('second', (p,))  # New batch must reject the existing intent.
    assert run(rig, 'second')['effects_match']


def test_later_source_operator_and_account_records_cannot_replace_original_inputs(rig):
    from polymarket_scanner.v11.event_risk import SafetyReductions
    c = coordinator(rig); c.coordinate('batch', (proposal(rig, 'one'),))
    before = run(rig, 'batch'); assert before['effects_match']
    rig['now'][0] += 1
    SafetyReductions(rig['store']).apply('new-block', scope='ACCOUNT', scope_id='account',
        action='NO_NEW_ORDERS', actor='fixture', reason='fixture')
    c.recover('later')
    assert run(rig, 'batch') == before


def test_replay_requires_original_policy_even_for_first_command(rig):
    c = coordinator(rig); c.recover('first')
    assert run(rig, 'first')['effects_match']
    other = PaperCoordinator(rig['store'], policy=replace(c.policy, initial_hypothetical_cash='9'),
        correlation=c.correlation, limits=c.limits)
    d = replay.replay_account_command(other, 'first', policy=ReplayPolicy('fixture'))
    assert d['status'] == 'GATED' and d['reason'] == 'ACCOUNT_REPLAY_ORIGINAL_POLICY_REQUIRED'


def test_clock_regression_still_allows_cancel_but_cannot_claim_causal_replay(rig):
    c=coordinator(rig);c.coordinate('batch',(proposal(rig,'one'),))
    rig['now'][0]-=30
    row=c.transition('clock-unsafe-cancel',intent_id='one',status='CANCEL_REQUESTED')
    assert row['body']['details']['state']['intents']['one']['status']=='CANCEL_REQUESTED'
    assert row['body']['details']['risk']['reserved_cash']=='8.0'
    d=run(rig,row['id'])
    assert d['status']=='GATED' and not d['effects_match']


def test_recomputed_cash_is_not_copied_from_original_output(rig, monkeypatch):
    c = coordinator(rig); p = proposal(rig); original = c._coordinate_effects
    def defect(*args, **kw):
        state, details = original(*args, **kw)
        state['cash'] = '9'  # Synthetic historical engine defect, not a ledger edit.
        return state, details
    monkeypatch.setattr(c, '_coordinate_effects', defect)
    c.coordinate('wrong', (p,))
    d = run(rig, 'wrong')
    assert d['status'] == 'MISMATCH' and not d['effects_match'] and not d['comparisons']['cash']


@pytest.mark.parametrize('defect', ['legacy','before','state_hash','request','clock_missing','clock_extra',
    'future_clock','preparation_missing','omit_rejection','duplicate_prepared','guard','unused_exit'])
def test_missing_or_inconsistent_original_inputs_gate_whole_comparison(rig, monkeypatch, defect):
    c = coordinator(rig); p = proposal(rig); bad = replace(p, proposal_id='bad', valuation_id='missing')
    from polymarket_scanner.v11.account_effects import EffectInputs
    original = EffectInputs.payload
    def broken(self, *a, **kw):
        d = deepcopy(original(self, *a, **kw))
        if defect == 'legacy': return None
        if defect == 'before': d['before_ref'] = dict(id='missing', seq=1, sha256='a'*64)
        if defect == 'state_hash': d['before_state_sha256'] = 'a'*64
        if defect == 'request': d['request_sha256'] = 'a'*64
        if defect == 'clock_missing': d['times'] = []
        if defect == 'clock_extra': d['times'].append(d['times'][-1])
        if defect == 'future_clock': d['times'][0] += 86400
        if defect == 'preparation_missing': d['preparation'] = None
        if defect == 'omit_rejection': d['preparation']['rejected'] = []
        if defect == 'duplicate_prepared': d['preparation']['prepared'] *= 2
        if defect == 'guard': d['expected_heads'] = [('OPERATOR_EVENT', 'missing', 1)]
        if defect == 'unused_exit': d['exit_checks'] = [dict(reason=None)]
        return d
    with monkeypatch.context() as m:
        m.setattr(EffectInputs, 'payload', broken); c.coordinate('batch', (bad, p))
    d = run(rig, 'batch')
    assert d['status'] == 'GATED' and not d['effects_match'] and not d['comparisons'], d
    assert 'recomputed_state_sha256' not in d


@pytest.mark.parametrize('missing', ['valuation','proof','predecessor','guard'])
def test_missing_original_receipt_or_head_cannot_be_filled_from_newer_state(rig, monkeypatch, missing):
    rows = commands(rig)
    key = dict(valuation='value-strong', proof='fill', predecessor='ack', guard=rig['state_id'])[missing]
    target = 'record-fill' if missing in {'proof','predecessor'} else 'batch'
    original = LearningSourceView.get
    def get(self, record_id):
        if record_id == key: raise EvidenceError('EVIDENCE_MISSING')
        return original(self, record_id)
    monkeypatch.setattr(LearningSourceView, 'get', get)
    d = run(rig, target)
    assert d['status'] == 'GATED' and not d['effects_match'], d


def audit_job(rig, **changes):
    p = replace(AuditPolicy('accounts', records_per_step=4, account_replay=ReplayPolicy('fixture')), **changes)
    rig['now'][0] = (int(rig['now'][0]//86400)+1)*86400+1
    AuditScheduler(rig['store'], p).request_due()
    return AuditWorker(coordinator(rig), p)


def test_audit_joins_complete_lifecycle_and_resumes_original_pinned_selection(rig):
    rows = commands(rig); worker = audit_job(rig)
    assert worker.step()['outcome'] == 'AUDIT_PARTIAL_PROGRESS'
    coordinator(rig).recover('later')
    report = finish(AuditWorker(worker.coordinator, worker.policy))['body']['details']
    d = report['account_replay']
    assert d['status'] == 'EFFECTS_REPRODUCED', d
    assert [r['command_ref']['id'] for r in d['rows']] == [r['id'] for r in rows]
    assert d['complete_retained_selection'] and report['coverage']['retained_account_effects_reproduced']
    assert not report['acceptance_granted'] and not d['full_control_flow_replayed']


def test_unsupported_and_legacy_commands_stay_in_audit_denominator(rig):
    c = coordinator(rig); original = c.recover('supported')['body']['details']
    legacy = deepcopy(original); legacy.pop('effect_inputs')
    rig['store'].audit('legacy', kind='COORDINATOR_EVENT', event_id=ACCOUNT_KEY, details=legacy)
    unknown = deepcopy(original); unknown['request'] = dict(action='UNKNOWN_COMMAND')
    rig['store'].audit('unknown', kind='COORDINATOR_EVENT', event_id=ACCOUNT_KEY, details=unknown)
    d = finish(audit_job(rig, records_per_step=256))['body']['details']['account_replay']
    assert d['status'] == 'PARTIAL' and d['retained_command_count'] == 3 and d['effect_matches'] == 1
    assert d['complete_retained_selection'] and not d['all_effects_reproduced']


@pytest.mark.parametrize('cap', [True,False])
def test_capped_or_overflowed_audit_cannot_credit_favorable_prefix(rig, cap):
    c = coordinator(rig)
    for i in range(replay.AUDIT_MAX_COMMANDS+1): c.recover('record-'+str(i))
    d = finish(audit_job(rig, records_per_step=256, maximum_job_records=16 if cap else 20000))['body']['details']['account_replay']
    assert d['status'] == 'GATED' and d['rows'] == [] and not d['complete_retained_selection']


def test_report_written_before_cursor_recovers_without_replay(rig, monkeypatch):
    commands(rig); worker = audit_job(rig, records_per_step=256); save = worker._save
    def crash(head, state, **kw):
        if kw.get('outcome') == 'AUDIT_COMPLETE': raise OSError('SYNTHETIC_AFTER_REPORT')
        return save(head,state,**kw)
    monkeypatch.setattr(worker, '_save', crash)
    with pytest.raises(OSError, match='SYNTHETIC_AFTER_REPORT'): finish(worker)
    saved = rig['store'].latest(kind='RUNTIME_STATUS', event_id='v11-audit-report:DAILY')
    monkeypatch.setattr(replay, 'replay_account_command', lambda *a,**kw:pytest.fail('replayed published report'))
    assert finish(AuditWorker(worker.coordinator,worker.policy)) == saved


def test_default_audit_identity_is_preserved_and_replay_requires_typed_policy():
    p = AuditPolicy('old')
    assert p.payload() == dict(version='old', records_per_step=64, maximum_step_seconds=2., maximum_job_records=20000)
    with pytest.raises(EvidenceError, match='AUDIT_ACCOUNT_REPLAY_POLICY_REQUIRED'):
        replace(p, account_replay={})
    assert digest(replace(p, account_replay=ReplayPolicy('new')).payload()) != digest(p.payload())


def test_audit_time_budget_discards_all_prefix_results(rig):
    c = coordinator(rig); row = c.recover('one'); ticks = iter([0.,0.,3.])
    d = replay.account_replay_audit(c, selection=dict(count=1, refs=[replay.ref(row)], overflow=False),
        through_seq=row['seq'], window=dict(start=0,end=rig['now'][0]+1), archive_complete=True,
        policy=ReplayPolicy('fixture'), monotonic=lambda:next(ticks,3.))
    assert d['status'] == 'GATED' and d['rows'] == [] and not d['effect_matches']
