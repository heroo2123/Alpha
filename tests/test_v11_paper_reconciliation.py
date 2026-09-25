from dataclasses import replace
from decimal import Decimal
import fcntl
import os

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.paper_reconciliation import PaperReconciliation, ReconciliationPolicy, admission_heads
from test_v11_paper_coordinator import rig, coordinator, proposal, proof


def worker(r, **kw):
    return PaperReconciliation(coordinator(r), ReconciliationPolicy('fixture', **kw))


def reserved(r):
    c=coordinator(r);p=proposal(r,units='5');c.coordinate('initial',(p,))
    return c,p


def fill_receipt(r,key='receipt',**kw):
    fields=dict(fill_id=key,units='1',all_in_collateral='.4',direction='BUY');fields.update(kw)
    return proof(r,'proposal',key,'PAPER_FILL',**fields)


def terminal(r,key='terminal',**kw):
    fields=dict(status='CANCELED',all_fills_reconciled=True,terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL',
                cumulative_fill_units='1');fields.update(kw)
    return proof(r,'proposal',key,'PAPER_TERMINAL',**fields)


def state(r):return coordinator(r)._state(coordinator(r)._head())


def test_archived_fill_then_terminal_are_reconciled_once_and_release_only_proven_remainder(rig):
    c,p=reserved(rig);fill_receipt(rig);terminal(rig);w=worker(rig)
    result=w.step('one');d=result['body']['details']
    assert d['outcome']=='RECONCILED' and d['attempted']==2
    assert Decimal(state(rig)['cash'])==Decimal('9.6') and len(state(rig)['lots'])==1
    assert state(rig)['intents'][p.proposal_id]['status']=='CANCELED'
    assert c.snapshot()['reserved_cash']=='0'
    head=c._head();assert worker(rig).step('one')==result
    assert worker(rig).step('two')['body']['details']['attempted']==0 and c._head()==head


def test_terminal_before_fill_remains_pending_until_cumulative_quantity_matches(rig):
    c,p=reserved(rig);terminal(rig);fill_receipt(rig);w=worker(rig)
    first=w.step('one')['body']['details']
    assert first['outcome']=='RECONCILIATION_REQUIRED' and first['pending_count']==1
    assert Decimal(c.snapshot()['reserved_cash'])==Decimal('1.6')
    with pytest.raises(EvidenceError,match='RECEIPTS_REQUIRE_RECONCILIATION'):
        c.coordinate('blocked',(replace(p,proposal_id='new'),))
    assert worker(rig).step('two')['body']['details']['outcome']=='RECONCILED'
    assert c.snapshot()['reserved_cash']=='0' and state(rig)['intents'][p.proposal_id]['status']=='CANCELED'


@pytest.mark.parametrize('changes',[{'units':'-1'},{'units':[]},{'fill_id':None},{'direction':'SELL'},
    {'token_id':'foreign-token'},{'execution_namespace':None},{'intent_id':[]},{'record_type':'PAPER_UNKNOWN'},
    {'account_id':None}])
def test_malformed_receipt_gates_admission_without_hiding_later_valid_fill(rig,changes):
    c,p=reserved(rig);s=rig['store'];intent=state(rig)['intents'][p.proposal_id]
    payload=dict(record_type='PAPER_FILL',execution_namespace='V11_PAPER',account_id='account',intent_id=p.proposal_id,
        token_id=intent['token_id'],fill_id='bad',units='1',all_in_collateral='.4',direction='BUY');payload.update(changes)
    s.capture('bad',kind='TRADE',event_id=p.context.event_id,provider='fixture',source_identity='bad',revision='1',
        evidence_class='SYNTHETIC',payload=payload)
    fill_receipt(rig);d=worker(rig).step('one')['body']['details']
    assert d['pending_count']==1 and list(state(rig)['fills'])==['receipt']
    assert Decimal(state(rig)['cash'])==Decimal('9.6')
    with pytest.raises(EvidenceError,match='RECEIPTS_REQUIRE_RECONCILIATION'):
        coordinator(rig).coordinate('blocked',(replace(p,proposal_id='another'),))
    c.transition('cancel',intent_id=p.proposal_id,status='CANCEL_REQUESTED')
    assert state(rig)['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED'
    assert Decimal(c.snapshot()['reserved_cash'])==Decimal('1.6')


def test_foreign_receipt_and_public_print_never_mutate_account(rig):
    c,p=reserved(rig);s=rig['store'];head=c._head()
    for key,payload in [('public',dict(price='.4',size='1')),('foreign',dict(record_type='PAPER_FILL',
            execution_namespace='V11_PAPER',account_id='other-account'))]:
        s.capture(key,kind='TRADE',event_id=p.context.event_id,provider='fixture',source_identity=key,revision='1',payload=payload)
    d=worker(rig).step('one')['body']['details']
    assert d['outcome']=='RECONCILED' and d['attempted']==1
    assert d['receipt_outcomes'][0]['outcome']=='FOREIGN_RECEIPT' and c._head()==head


def test_receipt_with_public_evidence_class_remains_pending(rig):
    c,p=reserved(rig);fill_receipt(rig);source=rig['store'].get('receipt')
    rig['store'].capture('public-claimed-fill',kind='TRADE',event_id=p.context.event_id,provider='fixture',
        source_identity='public',revision='1',payload=dict(source['body']['payload'],fill_id='fake'))
    d=worker(rig).step('one')['body']['details']
    assert d['pending_count']==1 and d['state']['pending']['public-claimed-fill']['reason']=='EXPLICIT_SYNTHETIC_PAPER_RECONCILIATION_REQUIRED'
    assert list(state(rig)['fills'])==['receipt']


def test_crash_after_account_commit_replays_same_receipt_without_double_cash(rig,monkeypatch):
    c,p=reserved(rig);fill_receipt(rig);w=worker(rig);original=w._save
    def crash(*a,**kw):
        if 'request' in kw:raise RuntimeError('SYNTHETIC_CRASH')
        return original(*a,**kw)
    monkeypatch.setattr(w,'_save',crash)
    with pytest.raises(RuntimeError,match='SYNTHETIC_CRASH'):w.step('one')
    head=c._head();assert Decimal(state(rig)['cash'])==Decimal('9.6')
    assert worker(rig).step('one')['body']['details']['outcome']=='RECONCILED'
    assert c._head()==head and len(state(rig)['fills'])==1


def test_bounded_cursor_resumes_and_pending_capacity_never_discards_receipt(rig):
    c,p=reserved(rig)
    for i in range(3):fill_receipt(rig,'fill'+str(i))
    w=worker(rig,maximum_receipts=2)
    assert w.step('one')['body']['details']['outcome']=='RECONCILIATION_REQUIRED'
    assert len(state(rig)['fills'])==2
    assert w.step('two')['body']['details']['outcome']=='RECONCILED'
    assert len(state(rig)['fills'])==3
    for i in range(3):fill_receipt(rig,'bad'+str(i),units='-1')
    # Different policy cannot replace an activated worker to bypass its journal.
    with pytest.raises(EvidenceError,match='CONFIG_CHANGED'):worker(rig,maximum_pending=1).step('three')
    assert w.step('three')['body']['details']['pending_count']==2


@pytest.mark.parametrize('operation',['coordinate','submit'])
def test_new_receipt_racing_account_commit_is_atomically_fenced(rig,monkeypatch,operation):
    c,p=reserved(rig);worker(rig).step('ready');original=rig['store']._append;fired=[]
    def race(*args,**kw):
        if args[0]=='racing' and not fired:
            fired.append(True);fill_receipt(rig)
        return original(*args,**kw)
    monkeypatch.setattr(rig['store'],'_append',race)
    with pytest.raises(EvidenceError,match='PAPER_RECEIPT_FRONTIER_CHANGED'):
        if operation=='coordinate':c.coordinate('racing',(replace(p,proposal_id='next',thesis_id='next'),))
        else:c.transition('racing',intent_id=p.proposal_id,status='SUBMITTING')
    assert state(rig)['intents'][p.proposal_id]['status']=='RESERVED' and not state(rig)['fills']
    assert 'next' not in state(rig)['intents']


def test_concurrent_activation_invalidates_admission_and_public_append_does_not(rig,monkeypatch):
    c,p=reserved(rig);original=rig['store']._append;fired=[]
    def race(*args,**kw):
        if args[0]=='racing' and not fired:fired.append(True);worker(rig).step('activate')
        return original(*args,**kw)
    monkeypatch.setattr(rig['store'],'_append',race)
    with pytest.raises(EvidenceError,match='AUDIT_GUARDED_STATE_CHANGED'):
        c.transition('racing',intent_id=p.proposal_id,status='SUBMITTING')
    heads,seq=admission_heads(rig['store'],account_id='account',account_policy_sha=c.policy_sha)
    rig['store'].capture('print',kind='TRADE',event_id=p.context.event_id,provider='fixture',source_identity='print',revision='1',payload={})
    rig['store'].audit('guarded',event_id='fixture',kind='RUNTIME_STATUS',details={},expected_heads=heads,expected_receipt_seq=seq)


def test_arrival_racing_ready_publication_preserves_account_and_requires_rescan(rig,monkeypatch):
    c,p=reserved(rig);w=worker(rig);original=w._save;fired=[]
    def race(*a,**kw):
        if kw.get('outcome')=='RECONCILED' and not fired:fired.append(True);fill_receipt(rig)
        return original(*a,**kw)
    monkeypatch.setattr(w,'_save',race)
    with pytest.raises(EvidenceError,match='PAPER_RECEIPT_FRONTIER_CHANGED'):w.step('one')
    assert not state(rig)['fills']
    assert worker(rig).step('two')['body']['details']['outcome']=='RECONCILED'


def test_clock_regression_cannot_advance_receipts_or_release_reservations(rig):
    c,p=reserved(rig);worker(rig).step('ready');fill_receipt(rig);head=c._head();rig['now'][0]-=1
    with pytest.raises(EvidenceError,match='CLOCK_REGRESSION'):worker(rig).step('regressed')
    assert c._head()==head
    c.transition('cancel',intent_id=p.proposal_id,status='CANCEL_REQUESTED')
    assert Decimal(c.snapshot()['reserved_cash'])==Decimal('2')


def test_pending_capacity_and_time_exhaustion_preserve_unconsumed_receipts(rig):
    c,p=reserved(rig);w=worker(rig,maximum_pending=1)
    for i in range(3):fill_receipt(rig,'bad'+str(i),units='-1')
    paused=w.step('budget',deadline=0.)['body']['details']
    assert paused['budget_exhausted'] and paused['state']['cursor']==0 and not state(rig)['fills']
    d=w.step('one')['body']['details'];cursor=d['state']['cursor']
    assert cursor==rig['store'].get('bad0')['seq'] and d['pending_count']==1 and d['attempted']==2
    again=w.step('two')['body']['details']
    assert again['state']['cursor']==cursor and again['receipt_outcomes'][-1]['receipt_id']=='bad1'
    assert Decimal(c.snapshot()['reserved_cash'])==Decimal('2') and not state(rig)['fills']


def test_duplicate_id_conflict_retains_cash_and_reservation(rig):
    c,p=reserved(rig);fill_receipt(rig);fill_receipt(rig,'conflict',fill_id='receipt')
    d=worker(rig).step('one')['body']['details']
    assert d['pending_count']==1 and d['state']['pending']['conflict']['reason']=='FILL_ID_CONFLICT'
    assert Decimal(state(rig)['cash'])==Decimal('9.6') and len(state(rig)['fills'])==1


def test_account_race_is_pending_then_retries_without_losing_cancellation(rig,monkeypatch):
    c,p=reserved(rig);fill_receipt(rig);original=rig['store']._append;fired=[]
    def race(*a,**kw):
        if a[0].startswith('paper-receipt-account:') and not fired:
            fired.append(True);c.transition('concurrent-cancel',intent_id=p.proposal_id,status='CANCEL_REQUESTED')
        return original(*a,**kw)
    monkeypatch.setattr(rig['store'],'_append',race)
    first=worker(rig).step('one')['body']['details']
    assert first['state']['pending']['receipt']['reason']=='AUDIT_STATE_CHANGED' and not state(rig)['fills']
    assert worker(rig).step('two')['body']['details']['outcome']=='RECONCILED'
    assert state(rig)['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED'
    assert Decimal(state(rig)['cash'])==Decimal('9.6')


def test_busy_or_symlink_lock_does_not_mutate_archive(rig):
    c,p=reserved(rig);w=worker(rig);path=str(rig['store'].path)+'.'+w.key.replace(':','-')+'.lock'
    before=rig['store'].pin_read_view();fd=os.open(path,os.O_CREAT|os.O_RDWR,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(EvidenceError,match='WORKER_BUSY'):w.step('busy')
    finally:os.close(fd)
    assert rig['store'].pin_read_view()==before
    os.unlink(path);os.symlink(rig['store'].path,path)
    with pytest.raises(OSError):w.step('symlink')
    assert rig['store'].pin_read_view()==before


def test_configured_coordinator_requires_activation_even_if_worker_cannot_start(rig):
    c,p=reserved(rig);w=PaperReconciliation(c,ReconciliationPolicy('required'))
    before=c._head()
    with pytest.raises(EvidenceError,match='RECEIPTS_REQUIRE_RECONCILIATION'):
        c.transition('submit',intent_id=p.proposal_id,status='SUBMITTING')
    with pytest.raises(EvidenceError,match='RECEIPTS_REQUIRE_RECONCILIATION'):
        c.coordinate('new',(replace(p,proposal_id='new'),))
    assert c._head()==before
    w.step('ready')
    c.transition('submit',intent_id=p.proposal_id,status='SUBMITTING')
    assert state(rig)['intents'][p.proposal_id]['status']=='SUBMITTING'


def test_runtime_reconciles_known_fills_under_health_loss_and_keeps_cancellation(rig,monkeypatch):
    from test_v11_paper_runtime import assembled
    from test_v11_runtime_health import advance
    from polymarket_scanner.v11.paper_runtime import PaperRuntime
    c,p=reserved(rig);base=assembled(rig,monkeypatch)
    w=PaperReconciliation(base.coordinator,ReconciliationPolicy('runtime'))
    rt=PaperRuntime(base.coordinator,base.queue,base.health,base.policy,evaluator=base.evaluator,reconciliation=w)
    w.step('ready');advance(rig,6);fill_receipt(rig)
    d=rt.tick('health-lost')['body']['details']
    assert d['receipt_reconciliation_ids'] and Decimal(state(rig)['cash'])==Decimal('9.6')
    assert state(rig)['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED'
    assert Decimal(c.snapshot()['reserved_cash'])==Decimal('1.6')
    assert not d['evaluation_ids'] and not d['real_orders_sent']


def test_runtime_receipt_error_does_not_skip_pending_cancellation(rig,monkeypatch):
    from test_v11_paper_runtime import assembled
    from test_v11_runtime_health import advance
    from polymarket_scanner.v11.paper_runtime import PaperRuntime
    c,p=reserved(rig);base=assembled(rig,monkeypatch)
    w=PaperReconciliation(base.coordinator,ReconciliationPolicy('runtime'))
    rt=PaperRuntime(base.coordinator,base.queue,base.health,base.policy,evaluator=base.evaluator,reconciliation=w)
    w.step('ready');advance(rig,6)
    def failed(*a,**kw):raise EvidenceError('SYNTHETIC_RECEIPT_WORKER_FAILURE')
    monkeypatch.setattr(w,'step',failed)
    d=rt.tick('receipt-error')['body']['details']
    assert any(e['stage']=='RECONCILIATION' for e in d['errors'])
    assert state(rig)['intents'][p.proposal_id]['status']=='CANCEL_REQUESTED'
    assert Decimal(c.snapshot()['reserved_cash'])==Decimal('2')


@pytest.mark.parametrize('payload',[{'record_type':[],'account_id':'account'}, {'intent_id':None},
    {'execution_namespace':None}, {'record_type':'PAPER_UNKNOWN'}, {'account_id':None}])
def test_malformed_account_markers_match_archive_and_public_feed_classification(rig,payload):
    from polymarket_scanner.v11.runtime_feed import EvidenceFeed, FeedPolicy
    from test_v11_paper_runtime import queue
    c,p=reserved(rig)
    row=rig['store'].capture('malformed-envelope',kind='TRADE',event_id=p.context.event_id,provider='fixture',
        source_identity='envelope',revision='1',payload=payload,evidence_class='SYNTHETIC')
    assert EvidenceFeed._classification(row)=='ACCOUNT_RECEIPT_NOT_PUBLIC_MARKET_TRADE'
    assert rig['store'].paper_receipt_window()['records']==[row]
    d=worker(rig).step('one')['body']['details'];assert d['pending_count']==1
    feed=EvidenceFeed(queue(rig),FeedPolicy('envelopes'))
    result=feed.drain('continue');assert result['body']['details']['state']['cursors']['TRADE']==row['seq']
    assert not state(rig)['fills']
