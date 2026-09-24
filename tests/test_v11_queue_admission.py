from dataclasses import replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.event_queue import EventQueue, EventRoute, TriggerPolicy, KINDS, KEY, VERSION
from test_v11_paper_coordinator import rig, proposal, coordinator


def queued_proposal(rig, *, finish=True):
    p=proposal(rig); r=rig['rule']; now=rig['now'][0]; store=rig['store']; raw=r.payload
    route=EventRoute(raw['event_id'],raw['station'],raw['target_date'],raw['family'],r.sha256,
                     tuple(t for b in raw['partition'] for t in (b['yes_token'],b['no_token'])),now+60,('MODEL',))
    policy=TriggerPolicy('fixture',1,1,4,16,30.,10.,100_000,60.,
                         tuple((k,60.) for k in sorted(KINDS)),((raw['station'],30.),))
    q=EventQueue(store,routes=(route,),policy=policy)
    q.publish('queued-book',kind='BOOK',evidence_id='book-proposal')
    if finish:
        with q.work('queued-work'):
            # Existing explicit downstream economics fixture, reused after the
            # claim with its unchanged book. It is not a calibrated opportunity.
            store.audit('queued-value',event_id=raw['event_id'],kind='MEASUREMENT',
                        details=store.get(p.valuation_id)['body']['details'],evidence_ids=(p.valuation_id,))
            p=replace(p,valuation_id='queued-value')
            q.finish('queued-finish',claim_id='queued-work',result_ids=('queued-value',))
    return q,p


def test_installed_queue_requires_a_completed_current_valuation_before_reservation(rig):
    q,p=queued_proposal(rig,finish=False)
    d=coordinator(rig).coordinate('batch',(p,))['body']['details']
    assert d['results'][0]['reason']=='EVENT_QUEUE_REEVALUATION_PENDING'
    assert Decimal(coordinator(rig).snapshot()['reserved_cash'])==0


def test_completed_queue_result_is_bound_through_reservation_and_paper_submit(rig):
    q,p=queued_proposal(rig)
    c=coordinator(rig)
    assert c.coordinate('batch',(p,))['body']['details']['reserved_intent_ids']==['proposal']
    intent=c._state(c._head())['intents']['proposal']
    assert intent['event_queue_completion_id']=='queued-finish'
    assert intent['expires_at']==rig['now'][0]+10
    assert not c.transition('submit',intent_id='proposal',status='SUBMITTING')['body']['details']['real_submission_performed']


def test_gap_after_reservation_blocks_submit_preserves_cash_and_allows_cancel_request(rig):
    q,p=queued_proposal(rig); c=coordinator(rig); c.coordinate('batch',(p,))
    before=c.snapshot()['reserved_cash']
    q.stream_gap('gap',event_id=rig['context'].event_id,reason='DISCONNECTED')
    with pytest.raises(EvidenceError,match='EVENT_QUEUE_REQUIRES_CENSUS'):
        c.transition('submit',intent_id='proposal',status='SUBMITTING')
    assert c.snapshot()['reserved_cash']==before
    c.transition('cancel',intent_id='proposal',status='CANCEL_REQUESTED')
    assert c.snapshot()['reserved_cash']==before


def test_queue_cannot_attest_a_different_valuation_for_same_event(rig):
    q,p=queued_proposal(rig)
    old=replace(p,valuation_id='value-proposal')
    d=coordinator(rig).coordinate('batch',(old,))['body']['details']
    assert d['results'][0]['reason']=='EVENT_QUEUE_VALUATION_NOT_EVALUATED'


def test_unnotified_raw_source_change_blocks_queue_backed_proposal(rig):
    q,p=queued_proposal(rig)
    rig['store'].capture('new-raw',event_id=rig['context'].event_id,kind='OFFICIAL_OBSERVATION',provider='fixture',
                        source_identity='KATL',revision='late',observed_at=rig['now'][0],payload={},evidence_class='SYNTHETIC')
    d=coordinator(rig).coordinate('batch',(p,))['body']['details']
    assert d['results'][0]['reason']=='EVENT_QUEUE_SOURCE_CHANGED_REEVALUATE'


def test_expired_completion_never_releases_reserved_cash(rig):
    q,p=queued_proposal(rig); c=coordinator(rig); c.coordinate('batch',(p,))
    rig['now'][0]+=10
    with pytest.raises(EvidenceError,match='EVENT_QUEUE_CURRENT_EVALUATION_REQUIRED'):
        c.transition('submit',intent_id='proposal',status='SUBMITTING')
    assert Decimal(c.snapshot()['reserved_cash'])==8


@pytest.mark.parametrize('during_submit',[False,True])
def test_queue_gap_racing_account_transaction_preserves_latest_safety_state(rig,monkeypatch,during_submit):
    q,p=queued_proposal(rig); c=coordinator(rig)
    if during_submit:
        c.coordinate('batch',(p,))
    original=rig['store'].audit
    def race(record_id,**kw):
        if record_id=='racing-command':
            monkeypatch.setattr(rig['store'],'audit',original)
            q.stream_gap('racing-gap',event_id=rig['context'].event_id,reason='DISCONNECTED')
        return original(record_id,**kw)
    monkeypatch.setattr(rig['store'],'audit',race)
    with pytest.raises(EvidenceError,match='AUDIT_GUARDED_STATE_CHANGED'):
        if during_submit:
            c.transition('racing-command',intent_id='proposal',status='SUBMITTING')
        else:
            c.coordinate('racing-command',(p,))
    assert q.snapshot()['needs_census']
    if during_submit:
        assert c._state(c._head())['intents']['proposal']['status']=='RESERVED'
    else:
        assert Decimal(c.snapshot()['reserved_cash'])==0


def test_queue_appearing_during_offline_reservation_is_not_ignored(rig,monkeypatch):
    p=proposal(rig); original=rig['store'].audit
    def race(record_id,**kw):
        if record_id=='batch':
            original('queue-appeared',event_id=KEY,kind='RUNTIME_STATUS',details={
                'version':VERSION,'state':{'needs_census':{rig['context'].event_id:'UNSYNCED'}}})
        return original(record_id,**kw)
    monkeypatch.setattr(rig['store'],'audit',race)
    with pytest.raises(EvidenceError,match='AUDIT_GUARDED_STATE_CHANGED'):
        coordinator(rig).coordinate('batch',(p,))
    assert Decimal(coordinator(rig).snapshot()['reserved_cash'])==0
