"""Linked account suffixes must reproduce before R08 or R09 credits them."""

from copy import deepcopy
from decimal import Decimal
import pytest
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_strategy_pipeline import factory
from test_v11_r08_scenario_reservation_readiness import coordinator, genuine_proposal
from test_v11_pws_admission import joined, coordinator as pws_coordinator, synthetic_proposal
from test_v11_r09_pws_lead_readiness import probe
from polymarket_scanner.v11.account_effects import EffectInputs
from polymarket_scanner.v11.account_replay import replay_account_command
from polymarket_scanner.v11.causal_replay import ReplayPolicy
from polymarket_scanner.v11.evidence import EvidenceError, canonical, KINDS, AUDIT_KINDS
from polymarket_scanner.v11.paper_coordinator import ACCOUNT_KEY, VERSION
from tools.v11_r08_scenario_reservation_readiness import evaluate_scenario_reservation_readiness as r08, OUTCOME_NO_RESERVATION as NO8, OUTCOME_DEMONSTRATED as YES8
from tools.v11_r09_pws_lead_readiness import OUTCOME_NOT_DEMONSTRATED as NO9


def append(c, request, change, key='review-forged-step', extra=None):
    head=c._head(); before=c._state(head); state=deepcopy(before)
    change(state)
    effects=EffectInputs(c,before)
    risk=effects.risk(state)
    details=dict(version=VERSION,policy_sha256=c.policy_sha,request=request,state=state,
                 risk=risk,effect_inputs=effects.payload(head,request,heads=(),receipt_seq=None))
    if extra: details.update(extra)
    c.store.audit(key,event_id=ACCOUNT_KEY,kind='COORDINATOR_EVENT',details=details,
                  expected_previous_seq=head['seq'])
    return key


def proof(c, pid, key, kind, **fields):
    intent=c._state(c._head())['intents'][pid]
    c.store.capture(key,event_id=intent['event_id'],kind='TRADE',provider='fixture-paper-engine',
        source_identity=pid,revision=key,observed_at=c.store.clock(),evidence_class='SYNTHETIC',
        payload=dict(record_type=kind,execution_namespace=c.store.namespace,account_id=c.policy.account_id,
                     intent_id=pid,token_id=intent['token_id'],**fields))
    return key


def terminate(c,pid):
    c.transition('cancel',intent_id=pid,status='CANCEL_REQUESTED')
    key=proof(c,pid,'terminal-proof','PAPER_TERMINAL',status='CANCELED',cumulative_fill_units='0',
              all_fills_reconciled=True,terminal_authority='SYNTHETIC_PAPER_ENGINE_FINAL')
    c.reconcile_terminal('terminal',key)


def show(c,key,result):
    replay=replay_account_command(c,key,policy=ReplayPolicy('independent-review'))
    assert replay['status']!='EFFECTS_REPRODUCED'


@pytest.mark.parametrize('action',['TRANSITION','RECOVER','FILL','TERMINAL'])
def test_canceled_reservation_cannot_be_resurrected(factory,action):
    rig=factory(); c=coordinator(rig); c.coordinate('batch',(genuine_proposal(rig),))
    terminate(c,'proposal')
    assert r08(c).outcome==NO8
    with pytest.raises(EvidenceError,match='UNRESOLVED_INTENT_REQUIRED'):
        c.transition('real-refusal',intent_id='proposal',status='SUBMITTING')
    request={'action':action}
    if action=='TRANSITION': request.update(intent_id='proposal',status='SUBMITTING')
    elif action in ('FILL','TERMINAL'): request.update(evidence_id='missing-proof')
    key=append(c,request,lambda s:s['intents']['proposal'].update(status='SUBMITTING',cancel_requested=False))
    result=r08(c); show(c,key,result)
    assert result.outcome==NO8
    assert result.risk_accepted is False


def test_pws_canceled_reservation_cannot_be_resurrected(joined):
    c=pws_coordinator(joined); c.coordinate('batch',(synthetic_proposal(joined),))
    terminate(c,'one')
    assert probe(joined,c).outcome==NO9
    key=append(c,dict(action='RECOVER'),lambda s:s['intents']['one'].update(status='RESERVED',cancel_requested=False))
    result=probe(joined,c); show(c,key,result)
    assert result.outcome==NO9


def test_filled_units_cannot_be_rolled_back_by_transition(factory):
    rig=factory(); c=coordinator(rig); c.coordinate('batch',(genuine_proposal(rig),))
    c.transition('submit',intent_id='proposal',status='SUBMITTING')
    evidence=proof(c,'proposal','fill-proof','PAPER_FILL',fill_id='fill-proof',units='1',all_in_collateral='.2',direction='BUY')
    c.record_fill('fill',evidence)
    before=r08(c); assert before.outcome==YES8
    key=append(c,dict(action='TRANSITION',intent_id='proposal',status='ACKNOWLEDGED'),
        lambda s:s['intents']['proposal'].update(status='ACKNOWLEDGED',filled_units='0'))
    result=r08(c); show(c,key,result)
    assert Decimal(before.reserved_cash) > 0
    assert result.reserved_cash == '0'
    assert result.outcome==NO8
    assert result.risk_accepted is False


def test_fill_without_evidence_cannot_clear_account_fault(factory):
    rig=factory(); c=coordinator(rig); c.coordinate('batch',(genuine_proposal(rig),))
    evidence=proof(c,'proposal','overrun-proof','PAPER_FILL',fill_id='overrun-proof',units='1',all_in_collateral='1',direction='BUY')
    c.record_fill('overrun',evidence)
    assert r08(c).outcome==NO8
    assert 'ACTUAL_PAPER_COST_EXCEEDED_RESERVED_BOUND' in c._state(c._head())['faults']
    key=append(c,dict(action='FILL',evidence_id='missing-proof'),lambda s:s.update(cash='100',faults=[]))
    result=r08(c); show(c,key,result)
    assert result.outcome==NO8
    assert result.risk_accepted is False


@pytest.mark.parametrize('field,value',[('intents',None),('rules',None)])
def test_partial_account_shape_is_typed_failure(factory,field,value):
    rig=factory(); c=coordinator(rig); c.coordinate('batch',(genuine_proposal(rig),))
    head=c._head(); details=deepcopy(head['body']['details']); details['state'][field]=value
    c.store.audit('partial-account',event_id=ACCOUNT_KEY,kind='COORDINATOR_EVENT',details=details)
    try: result=r08(c)
    except EvidenceError: return
    assert result.outcome==NO8


@pytest.mark.parametrize('field', ['intents', 'rules'])
def test_pws_partial_account_shape_is_negative(joined, field):
    c=pws_coordinator(joined); c.coordinate('batch',(synthetic_proposal(joined),))
    details=deepcopy(c._head()['body']['details'])
    details['state'][field]=None
    c.store.audit('partial-account',event_id=ACCOUNT_KEY,kind='COORDINATOR_EVENT',details=details)
    assert probe(joined,c).outcome==NO9


def test_actual_lifecycle_readiness_and_idempotence(factory,monkeypatch):
    rig=factory(); c=coordinator(rig); p=genuine_proposal(rig)
    row=c.coordinate('batch',(p,)); assert c.coordinate('batch',(p,))==row
    c.transition('submit',intent_id='proposal',status='SUBMITTING'); c.recover('recover')
    c.transition('ack',intent_id='proposal',status='ACKNOWLEDGED')
    evidence=proof(c,'proposal','good-fill','PAPER_FILL',fill_id='good-fill',units='1',all_in_collateral='.2',direction='BUY')
    c.record_fill('record-fill',evidence); c.record_fill('duplicate-fill',evidence)
    c.transition('cancel',intent_id='proposal',status='CANCEL_REQUESTED')
    before=canonical({k:c.store.records(kind=k,limit=1000) for k in KINDS | AUDIT_KINDS}); first=r08(c)
    def forbidden(*a,**k): pytest.fail('readiness attempted mutation')
    for name in ('audit','safety_audit','capture'): monkeypatch.setattr(c.store,name,forbidden)
    for name in ('coordinate','transition','recover','record_fill','reconcile_terminal','_commit'): monkeypatch.setattr(c,name,forbidden)
    for _ in range(3): assert r08(c)==first
    assert canonical({k:c.store.records(kind=k,limit=1000) for k in KINDS | AUDIT_KINDS})==before
    assert first.outcome==YES8 and Decimal(first.reserved_cash)>0


def test_r09_uses_one_head_and_does_not_write(joined,monkeypatch):
    c=pws_coordinator(joined); c.coordinate('batch',(synthetic_proposal(joined),))
    head=c._head(); count=[]; before=canonical({k:c.store.records(kind=k,limit=1000) for k in KINDS | AUDIT_KINDS})
    def once():
        count.append(True)
        if len(count)>1: pytest.fail('second account head read')
        return head
    monkeypatch.setattr(c,'_head',once)
    def forbidden(*a,**k): pytest.fail('readiness attempted mutation')
    for name in ('audit','safety_audit','capture'): monkeypatch.setattr(c.store,name,forbidden)
    result=probe(joined,c)
    assert result.genuine_pws_reservation_present is True
    assert count==[True]
    assert canonical({k:c.store.records(kind=k,limit=1000) for k in KINDS | AUDIT_KINDS})==before
