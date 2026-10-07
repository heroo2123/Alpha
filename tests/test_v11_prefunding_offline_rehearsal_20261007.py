"""Offline disposable fixture rehearsal: durable operator stop, write-failure
admission closure, cancel-request versus confirmation, and new-process
no-repost recovery.

Scope note (2026-10-07): this rehearsal composes existing V11 safety/evidence
contracts under fresh tmp_path fixtures and a fake authorized operator
command. It carries no signer, credential, provider/network access, real
account or current Shadow database, and financial_authority stays False
throughout. It is a local rehearsal, not host, provider, guardian custody,
alert receipt or live cancellation proof, and it does not alter any funding
gate. Archive segment/daily-rollover continuity (map item 4) and the legacy
alert-outbox ambiguity lane (map item 3) are intentionally left unclaimed;
see the follow-up note at the end of this file.
"""
import json
import sqlite3
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import pytest

from polymarket_scanner.v11.evidence import EvidenceError, EvidenceStore
from polymarket_scanner.v11.event_risk import EventContext, SafetyReductions
from polymarket_scanner.v11.operator_safety_router import OperatorSafetyPolicy, OperatorSafetyRouter
from polymarket_scanner.v11.paper_coordinator import PaperCoordinator
from polymarket_scanner.v11 import paper_cancellation as cancellation
from polymarket_scanner.v11.microcanary_prep import CanaryJournal, CanaryRefusal
from polymarket_scanner.v11 import guardian_lease as lease

from test_v11_paper_coordinator import rig, coordinator, proposal, fill
from test_v11_paper_cancellation import bridge, opening, trigger, plan, terminal
from test_v11_microcanary_prep import scope, order
from test_v11_paper_guardian import worker, build, status


ROOT = Path(__file__).resolve().parents[1]
SUBPROCESS_ENV = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8'}

STOP_PROBE = '''
import json, sys
from polymarket_scanner.v11.evidence import EvidenceStore
from polymarket_scanner.v11.event_risk import EventContext, SafetyReductions

path, city, station, event, at = sys.argv[1:]
store = EvidenceStore(path, 'V11_PAPER', clock=lambda: float(at))
record = store.get('cmd-stop')
flags = SafetyReductions(store).view(EventContext('account', city, station, event))['flags']
print(json.dumps(dict(sha256=record['sha256'], no_new_orders=flags['no_new_orders'])))
'''

# A fresh OS process reopening a journal left mid-flight must independently
# derive RECOVERY_HOLD/refusal from the durable file, not from any state the
# original process held in memory.
RECOVERY_PROBE = '''
import json, sys
from polymarket_scanner.v11.microcanary_prep import CanaryJournal, CanaryRefusal

path, scope_json, original_json, different_json, expected_optimize = sys.argv[1:]
if sys.flags.optimize != int(expected_optimize):
    raise RuntimeError('RECOVERY_CHILD_OPTIMIZATION_MISMATCH')
journal = CanaryJournal(path, json.loads(scope_json))
status = journal.status(now=111)
before = journal._row()
refusals = {}
for label, raw in [('original', original_json), ('different', different_json)]:
    try:
        journal.prepare(json.loads(raw), now=111)
    except CanaryRefusal as exc:
        refusals[label] = exc.args[0]
    else:
        refusals[label] = None
after = journal._row()
after_status = journal.status(now=111)
print(json.dumps(dict(state=status["state"], remote_census_required=status["remote_census_required"],
                       cancel_required=status["cancel_required"], new_order_allowed=status["new_order_allowed"],
                       order_count=status["order_count"], after_order_count=after_status["order_count"],
                       client_key=status["client_key"], after_client_key=after_status["client_key"],
                       payload=before["payload"], after_payload=after["payload"],
                       after_state=after_status["state"], row_unchanged=after == before,
                       refusals=refusals, optimize=sys.flags.optimize)))
journal.close()
'''


def test_durable_operator_stop_survives_store_reopen_despite_write_failure(rig):
    store, now, context = rig['store'], rig['now'], rig['context']
    policy = OperatorSafetyPolicy(account_id='account', operators=(111,),
        allowed_scopes=('ACCOUNT',), allowed_actions=('CANCEL_AND_HALT', 'NO_NEW_ORDERS'))
    router = OperatorSafetyRouter(store, policy, clock=lambda: now[0])
    created = now[0]

    first = router.route('cmd-stop', actor=111, scope='ACCOUNT', scope_id='account',
                          action='CANCEL_AND_HALT', reason='synthetic_operator_stop',
                          created=created, expires=created + 10.)
    assert first['body']['details']['flags']['no_new_orders'] is True
    assert first['body']['details']['cancellation_status'] == 'REQUESTED_NOT_CONFIRMED'

    # Negative control: the reduction is scoped to this account, not a global
    # side effect of recording any reduction anywhere.
    unrelated = EventContext('unrelated-account', context.city_id, context.station_id, context.event_id)
    assert SafetyReductions(store).view(unrelated)['flags']['no_new_orders'] is False

    # Inject a storage failure on a distinct second command: it must not be
    # falsely acknowledged, and the already-durable first reduction must not
    # be lost or altered by the failed attempt.
    real_audit = store.audit
    def failing_audit(*a, **kw):
        raise sqlite3.OperationalError('synthetic disk failure')
    store.audit = failing_audit
    try:
        with pytest.raises(sqlite3.OperationalError):
            router.route('cmd-escalate', actor=111, scope='ACCOUNT', scope_id='account',
                         action='NO_NEW_ORDERS', reason='escalation', created=created, expires=created + 10.)
    finally:
        store.audit = real_audit
    assert len(store.records(kind='OPERATOR_EVENT', limit=10)) == 1

    # Idempotent replay of the original, already-committed command.
    replay = router.route('cmd-stop', actor=111, scope='ACCOUNT', scope_id='account',
                           action='CANCEL_AND_HALT', reason='synthetic_operator_stop',
                           created=created, expires=created + 10.)
    assert replay == first
    assert len(store.records(kind='OPERATOR_EVENT', limit=10)) == 1

    # An independent Python process rereads the durable stop record.
    fresh_store = EvidenceStore(store.path, 'V11_PAPER', clock=lambda: now[0])
    assert fresh_store.get('cmd-stop')['sha256'] == first['sha256']
    fresh_context = EventContext('account', context.city_id, context.station_id, context.event_id)
    assert SafetyReductions(fresh_store).view(fresh_context)['flags']['no_new_orders'] is True
    result = subprocess.run(
        [sys.executable, *(['-' + 'O' * sys.flags.optimize] if sys.flags.optimize else []),
         '-s', '-E', '-c', STOP_PROBE, str(store.path), context.city_id,
         context.station_id, context.event_id, str(now[0])],
        cwd=ROOT, env=SUBPROCESS_ENV, capture_output=True, timeout=10, text=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    assert json.loads(result.stdout) == {'sha256': first['sha256'], 'no_new_orders': True}


def test_operator_stop_closes_opening_admission_after_locked_write_failure(rig, worker, monkeypatch):
    before_proposal = proposal(rig, 'before', units='2', bucket=0)
    c = coordinator(rig)
    opened = c.coordinate('before-batch', (before_proposal,))['body']['details']
    # Control: opening works before any stop exists, so a later block is
    # caused by the stop, not a vacuous always-deny.
    assert opened['reserved_intent_ids'] == ['before']

    # Activate a real synthetic guardian lease. The second proposal is small
    # enough to reserve alongside the first, and is fully built before any
    # failure hook is installed.
    guardian = build(rig, worker)
    ready = status(guardian)
    anchor = [ready['body']['details']['stamp'], rig['now'][0]]
    def fixture_host_stamp(_store):
        # Keep lease age tied to the fixture clock. Real elapsed test time
        # must not expire the lease before the explicit inside-write race.
        elapsed = rig['now'][0] - anchor[1]
        return dict(boot_id=anchor[0]['boot_id'], wall=anchor[0]['wall'] + elapsed,
                    monotonic=anchor[0]['monotonic'] + elapsed)
    monkeypatch.setattr(lease, 'host_stamp', fixture_host_stamp)
    racing = proposal(rig, 'racing', units='2', bucket=1)
    assert c._prepare(racing, rig['now'][0])[0]['proposal_id'] == 'racing'
    head_before_failure = c._head()
    reserved_before_failure = Decimal(c.snapshot()['reserved_cash'])
    assert reserved_before_failure == Decimal('.8')

    # The pre-lock failure sees a would-be reservation, then aborts before
    # EvidenceStore can start its append transaction.
    real_audit = rig['store'].audit
    prelock_seen = []
    def fail_before_append(record_id, **kwargs):
        if record_id == 'before-lock-open':
            prelock_seen.append(kwargs['details']['reserved_intent_ids'])
            raise sqlite3.OperationalError('synthetic failure before append')
        return real_audit(record_id, **kwargs)
    monkeypatch.setattr(rig['store'], 'audit', fail_before_append)
    with pytest.raises(sqlite3.OperationalError, match='synthetic failure before append'):
        c.coordinate('before-lock-open', (racing,))
    monkeypatch.setattr(rig['store'], 'audit', real_audit)
    assert prelock_seen == [['racing']]
    assert c._head() == head_before_failure
    assert Decimal(c.snapshot()['reserved_cash']) == reserved_before_failure

    # Fail only this account append, after SQLite has acquired its write lock
    # and the guardian transaction check has succeeded. The lease expiry
    # attempt below separately detects a missing guardian check.
    real_check = lease.check_transaction
    checked = []
    def fail_after_check(store, db, kind, event_id, body, heads):
        real_check(store, db, kind, event_id, body, heads)
        if kind == 'COORDINATOR_EVENT' and body['details']['request'].get('action') == 'COORDINATE':
            checked.append((body['details']['request']['proposals'][0]['proposal_id'],
                            db.in_transaction))
            raise sqlite3.OperationalError('synthetic failure after locked guardian check')
    monkeypatch.setattr(lease, 'check_transaction', fail_after_check)
    with pytest.raises(sqlite3.OperationalError, match='synthetic failure after locked guardian check'):
        c.coordinate('racing-open', (racing,))
    monkeypatch.setattr(lease, 'check_transaction', real_check)
    assert checked == [('racing', True)]
    assert c._head() == head_before_failure
    assert Decimal(c.snapshot()['reserved_cash']) == reserved_before_failure
    with pytest.raises(EvidenceError, match='EVIDENCE_MISSING'):
        rig['store'].get('racing-open')

    # A second admissible opening expires the lease inside the write lock.
    # The rollback must leave the same account head and reservation intact.
    expiring = proposal(rig, 'expiring', units='2', bucket=1)
    assert c._prepare(expiring, rig['now'][0])[0]['proposal_id'] == 'expiring'
    real_budget = rig['store']._budget
    budget_calls = []
    def expire_inside_write(db, size):
        real_budget(db, size)
        budget_calls.append(db.in_transaction)
        rig['now'][0] += 3
    monkeypatch.setattr(rig['store'], '_budget', expire_inside_write)
    with pytest.raises(EvidenceError, match='LEASE_GATED'):
        c.coordinate('expired-open', (expiring,))
    monkeypatch.setattr(rig['store'], '_budget', real_budget)
    assert budget_calls == [True]
    assert c._head() == head_before_failure
    assert Decimal(c.snapshot()['reserved_cash']) == reserved_before_failure

    SafetyReductions(rig['store']).apply('halt', scope='ACCOUNT', scope_id='account',
        action='CANCEL_AND_HALT', actor='synthetic-operator', reason='fixture')
    # The expired lease itself closes opening. The stop is durable too, so
    # renewing the lease still leaves the account closed to a new opening.
    fresh_store = EvidenceStore(rig['store'].path, 'V11_PAPER', clock=lambda: rig['now'][0])
    fresh_coordinator = PaperCoordinator(fresh_store, policy=rig['policy'],
                                          correlation=rig['correlation'], limits=rig['limits'])
    fresh_rig = dict(rig, store=fresh_store)
    with pytest.raises(EvidenceError, match='LEASE_GATED'):
        fresh_coordinator.coordinate('expired-fresh', (proposal(fresh_rig, 'expired-fresh', units='2', bucket=1),))
    assert fresh_coordinator._head() == head_before_failure
    assert Decimal(fresh_coordinator.snapshot()['reserved_cash']) == reserved_before_failure
    renewed = status(guardian)
    anchor[:] = [renewed['body']['details']['stamp'], rig['now'][0]]
    fresh_blocked = fresh_coordinator.coordinate(
        'fresh-blocked-batch', (proposal(fresh_rig, 'fresh-blocked', units='2', bucket=1),))['body']['details']
    assert fresh_blocked['reserved_intent_ids'] == []
    assert fresh_blocked['results'][0]['reason'] == 'EVENT_OR_OPERATOR_STATE_CHANGED'
    assert Decimal(fresh_coordinator.snapshot()['reserved_cash']) == reserved_before_failure

    fresh_context = EventContext('account', rig['context'].city_id, rig['context'].station_id, rig['context'].event_id)
    assert SafetyReductions(fresh_store).view(fresh_context)['flags']['no_new_orders'] is True


def test_cancel_request_not_confirmed_survives_crash_before_telemetry_and_fresh_reopen(rig, monkeypatch):
    c = opening(rig)
    trigger(rig)
    plan(rig)
    b = bridge(rig)

    def account_state(coordinator, *, cash, reserved, units, basis):
        snapshot = coordinator.snapshot()
        lots = coordinator._state(coordinator._head())['lots'].values()
        assert Decimal(snapshot['cash']) == Decimal(cash)
        assert Decimal(snapshot['reserved_cash']) == Decimal(reserved)
        assert Decimal(snapshot['held_cost_basis']) == Decimal(basis)
        assert sum((Decimal(lot['units']) for lot in lots), Decimal(0)) == Decimal(units)
        assert sum((Decimal(lot['all_in_cost_basis']) for lot in lots), Decimal(0)) == Decimal(basis)

    account_state(c, cash='10', reserved='.8', units='0', basis='0')

    def crash(*a, **kw):
        raise RuntimeError('synthetic crash before cancellation telemetry commit')
    monkeypatch.setattr(b, '_commit', crash)
    with pytest.raises(RuntimeError):
        b.advance('dispatch', plan_id='cancel-plan')
    # The account-level effect (local cancel request) survived the crash even
    # though the cancellation telemetry commit never completed.
    assert c._state(c._head())['intents']['buy']['status'] == 'CANCEL_REQUESTED'
    account_state(c, cash='10', reserved='.8', units='0', basis='0')

    fresh_store = EvidenceStore(rig['store'].path, 'V11_PAPER', clock=lambda: rig['now'][0])
    fresh_coordinator = PaperCoordinator(fresh_store, policy=rig['policy'],
                                          correlation=rig['correlation'], limits=rig['limits'])
    fresh_bridge = cancellation.PaperCancellation(fresh_coordinator, cancellation.CancellationPolicy('fixture', 2, 10))
    account_state(fresh_coordinator, cash='10', reserved='.8', units='0', basis='0')

    resumed = fresh_bridge.advance('dispatch', plan_id='cancel-plan')['body']['details']
    assert resumed['locally_requested_count'] == 1 and resumed['local_requests_attempted_this_cycle'] == 0
    assert resumed['reports'][0]['current_account_status'] == 'CANCEL_REQUESTED'
    assert resumed['reports'][0]['actual_order_cancellation_confirmed'] is False
    assert not resumed['reports'][0]['confirmed_paper_cancellation']
    requests = [r for r in fresh_store.records(kind='COORDINATOR_EVENT', limit=50)
                if r['body']['details'].get('request', {}).get('status') == 'CANCEL_REQUESTED']
    # Exactly one local cancel request ever reached the account, despite the
    # interrupted telemetry and the fresh-store resume.
    assert len(requests) == 1
    account_state(fresh_coordinator, cash='10', reserved='.8', units='0', basis='0')

    rig['now'][0] += 1
    fill(rig, 'buy', units='1', collateral='.4')
    still_open = fresh_bridge.observe('still-open', plan_id='cancel-plan')['body']['details']
    assert still_open['reports'][0]['cumulative_fill_units'] == '1'
    assert not still_open['reports'][0]['confirmed_paper_cancellation']
    account_state(fresh_coordinator, cash='9.6', reserved='.4', units='1', basis='.4')

    terminal(rig, units='1')
    closed = fresh_bridge.observe('closed', plan_id='cancel-plan')['body']['details']
    assert closed['outcome'] == 'ALL_TARGETS_TERMINAL'
    assert closed['reports'][0]['confirmed_paper_cancellation'] is True
    account_state(fresh_coordinator, cash='9.6', reserved='0', units='1', basis='.4')
    # Local confirmation is never the same claim as an actual exchange
    # cancellation confirmation; the latter stays False permanently here.
    assert closed['reports'][0]['actual_order_cancellation_confirmed'] is False


def test_canary_recovery_hold_and_no_repost_survive_new_os_process(tmp_path):
    tmp_path.chmod(0o700)
    path = tmp_path / 'canary.sqlite'
    fixture_scope = scope()
    first_order = order()
    journal = CanaryJournal(path, fixture_scope)
    prepared = journal.prepare(first_order, now=110)
    journal.mark_dispatch_uncertain(prepared['client_key'])
    assert journal.status(now=110)['state'] == 'DISPATCH_UNCERTAIN'
    journal.close()

    different_order = dict(first_order, quantity='1')
    optimization_flag = ['-' + 'O' * sys.flags.optimize] if sys.flags.optimize else []
    result = subprocess.run(
        [sys.executable, *optimization_flag, '-s', '-E', '-c', RECOVERY_PROBE, str(path),
         json.dumps(fixture_scope), json.dumps(first_order), json.dumps(different_order),
         str(sys.flags.optimize)],
        cwd=ROOT, env=SUBPROCESS_ENV, capture_output=True, timeout=10, text=True)
    assert result.returncode == 0, (result.stdout, result.stderr)
    report = json.loads(result.stdout)

    # The NEW OS process, which never saw the first process's in-memory
    # state, must derive the hold and the refusal from the durable file
    # alone.
    assert report['state'] == 'RECOVERY_HOLD'
    assert report['optimize'] == sys.flags.optimize
    assert report['new_order_allowed'] is False
    assert report['remote_census_required'] is True
    assert report['refusals'] == {'original': 'ONE_ORDER_OR_HALT', 'different': 'ONE_ORDER_OR_HALT'}
    assert report['row_unchanged'] is True
    assert report['order_count'] == 1
    assert report['after_order_count'] == 1
    assert report['after_state'] == 'RECOVERY_HOLD'
    assert report['client_key'] == prepared['client_key']
    assert report['after_client_key'] == prepared['client_key']
    assert report['payload'] == prepared['serialized']
    assert report['after_payload'] == prepared['serialized']


# Follow-up (unclaimed by this rehearsal): archive continuity. Map item 4
# (tiny disposable V11 segment pair + committed WAL row, per-segment manifest
# verification, and an explicit refusal of unified causal replay without an
# original-record-ID/sequence provenance mapping under daily rollover
# compaction) needs its own disposable fixture and is out of scope for this
# bounded pass. A later writer should build it directly against
# tools/v11_daily_evidence_rollover.py and tools/v11_snapshot.py (noting the
# latter is a V10_CONTROL-only verifier, not a V11 segment validator) using
# fresh tmp_path-only segment files, and must keep
# accepted_for_unified_causal_replay=False unless an explicit record-ID
# provenance mapping is produced. The legacy alert-outbox ambiguity lane (map
# item 3, tests/test_operator_notifications.py) is likewise left unclaimed
# here since it was not among the prioritized areas for this pass.
