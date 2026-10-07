from copy import deepcopy
from dataclasses import asdict, replace
import json
import pytest
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_strategy_pipeline import factory, evaluate
from test_v11_pws_admission import joined, coordinator as pc, synthetic_proposal, verify
from test_v11_r08_scenario_reservation_readiness import coordinator, genuine_proposal
from test_v11_r09_pws_lead_readiness import probe
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.paper_coordinator import ACCOUNT_KEY, VERSION
from polymarket_scanner.v11.event_risk import VERSION as EVENT_VERSION
from polymarket_scanner.v11.strategy_admission import VERSION as ADMISSION_VERSION, StrategyAdmission
from polymarket_scanner.v11.valuation import VERSION as EV_VERSION
from polymarket_scanner.v11.scenario_risk import Attribution
from tools.v11_r08_scenario_reservation_readiness import evaluate_scenario_reservation_readiness as r08, OUTCOME_NO_RESERVATION as NO8
from tools.v11_r09_pws_lead_readiness import OUTCOME_NOT_DEMONSTRATED as NO9

def append(c, state):
    return c.store.audit('review-head', event_id=ACCOUNT_KEY, kind='COORDINATOR_EVENT',
        details=dict(version=VERSION, policy_sha256=c.policy_sha, state=state, request={'action':'REVIEW_UNTRUSTED'}))

def clone(c, old, new, mutate):
    row=c.store.get(old); d=deepcopy(row['body']['details']); mutate(d)
    c.store.audit(new,event_id=row['event_id'],kind=row['kind'],details=d)
    return new

def negative(result, outcome):
    print(json.dumps(result.to_dict(),sort_keys=True))
    assert result.outcome == outcome

@pytest.mark.parametrize('lineage', ['fabricated', 'real_rejected'])
def test_no_coordinate_with_lineage(factory,lineage):
    rig=factory(); c=coordinator(rig); event=rig['context'].event_id
    state=c._state(None); b=asdict(rig['binding']); context=asdict(rig['context'])
    state['rules'][event]=asdict(rig['rule']); state['contexts'][event]=context
    if lineage=='fabricated':
        c.store.audit('fake-admission',event_id='not-an-admission',kind='REGISTRY',details={
            'version':ADMISSION_VERSION,'request':{'binding':b,'scope':{'strategy':rig['scope'].strategy}}})
        c.store.audit('fake-event',event_id='not-the-event',kind='COORDINATOR_EVENT',details={
            'version':EVENT_VERSION,'request':{'context':context}})
        c.store.audit('fake-value',event_id=event,kind='MEASUREMENT',details={'version':EV_VERSION,'binding':b})
        aid,eid,vid='fake-admission','fake-event','fake-value'
    else:
        result=evaluate(rig)
        assert result['outcome']=='REJECT'
        assert result['reason']=='CONSERVATIVE_EV_NOT_ABOVE_THRESHOLD'
        aid,eid,vid='pin',rig['request'].event_state_id,'evaluation:valuation'
    state['intents']['invented']=dict(proposal_id='invented',event_id=event,
        token_id=rig['rule'].payload['partition'][0]['yes_token'],direction='BUY',units='2',filled_units='0',
        unit_collateral_bound='.2',status='RESERVED',attribution=[dict(strategy=rig['scope'].strategy,weight='1')],
        financial_authority=False,binding=b,admission_ids=[aid],event_state_id=eid,valuation_id=vid)
    assert c._head() is None
    append(c,state)
    negative(r08(c),NO8)

@pytest.mark.parametrize('change', ['admission_context','admission_rule','admission_no_assessment',
    'admission_authority','event_binding','event_suppression','value_rejected','value_target',
    'value_authority','unadmitted_attribution','conflicting_intent','intent_rule'])
def test_semantic_lineage_mutations(factory,change):
    rig=factory(); c=coordinator(rig); p=genuine_proposal(rig)
    result=c.coordinate('batch',(p,))['body']['details']
    assert result['reserved_intent_ids']==['proposal']
    state=c._state(c._head()); intent=state['intents']['proposal']
    if change.startswith('admission_'):
        def mutate(d):
            if change=='admission_context': d['request']['context']['account_id']='other-account'
            elif change=='admission_rule': d['request']['rule']['sha256']='f'*64
            elif change=='admission_no_assessment': del d['assessment']
            else: d['assessment']['financial_authority']=True
        intent['admission_ids']=[clone(c,'pin','bad-pin',mutate)]
        if change in ('admission_context','admission_rule'):
            with pytest.raises(EvidenceError) as exc:
                StrategyAdmission(c.store).revalidate('bad-pin',context=p.context,rule=p.rule,
                    binding=intent['binding'],strategies=(rig['scope'].strategy,))
            assert str(exc.value)=='STRATEGY_ADMISSION_PROPOSAL_MISMATCH'
    elif change.startswith('event_'):
        def mutate(d):
            if change=='event_binding': d['request']['binding']['bundle_sha256']='e'*64
            else: d['ordinary_new_risk_research_allowed']=False; d['safety']['flags']['no_new_orders']=True
        intent['event_state_id']=clone(c,p.event_state_id,'bad-event',mutate)
    elif change.startswith('value_'):
        def mutate(d):
            if change=='value_rejected': d['outcome']='REJECT'
            elif change=='value_target': d['target']['token_id']='another-token'
            else: d['financial_authority']=True
        intent['valuation_id']=clone(c,p.valuation_id,'bad-value',mutate)
        if change=='value_rejected':
            real=c.coordinate('real-reject',(replace(p,proposal_id='rejected',valuation_id='bad-value'),))['body']['details']
            assert real['reserved_intent_ids']==[]
            assert real['results']==[dict(proposal_id='rejected',outcome='REJECT',reason='PROPOSAL_ECONOMICS_NOT_QUALIFIED')]
    elif change=='unadmitted_attribution':
        intent['attribution']=[dict(strategy=rig['scope'].strategy,weight='.5'),dict(strategy='PWS_OBSERVATION_LEAD',weight='.5')]
    elif change=='conflicting_intent':
        q=replace(p,proposal_id='conflict')
        rejection=c.coordinate('conflict-batch',(q,))['body']['details']
        assert rejection['reserved_intent_ids']==[]
        assert rejection['results']==[dict(proposal_id='conflict',outcome='REJECT',reason='THESIS_ALREADY_HAS_ECONOMIC_INTENT')]
        extra=deepcopy(intent); extra['proposal_id']='conflict'; state['intents']['conflict']=extra
        intent['status']='CANCELED' # only the rejected, never-admitted intent contributes
    else: intent['rule_fingerprint']='e'*64
    append(c,state); negative(r08(c),NO8)

@pytest.mark.parametrize('change',['context','rule_fingerprint','rule_source'])
def test_pws_exact_stored_context_and_rule(joined,change):
    c=pc(joined); p=synthetic_proposal(joined)
    assert c.coordinate('batch',(p,))['body']['details']['reserved_intent_ids']==['one']
    state=c._state(c._head()); intent=state['intents']['one']; event=p.context.event_id
    if change=='context':
        state['contexts'][event]['city_id']='different-city'
        def mutate(d): d['request']['context']=deepcopy(state['contexts'][event])
        intent['event_state_id']=clone(c,p.event_state_id,'other-context',mutate)
    elif change=='rule_fingerprint': intent['rule_fingerprint']='f'*64
    else: state['rules'][event]['source_event_sha256']='f'*64
    append(c,state)
    assert verify(joined)['financial_authority'] is False
    negative(probe(joined,c),NO9)

@pytest.mark.parametrize('value',[True,0,1,None,'false'])
def test_nonfalse_intent_authority_exact_reason(factory,value):
    rig=factory(); c=coordinator(rig); c.coordinate('batch',(genuine_proposal(rig),))
    state=c._state(c._head()); state['intents']['proposal']['financial_authority']=value
    append(c,state); result=r08(c)
    assert result.outcome==NO8
    assert result.reasons==('RESERVED_INTENT_PROVENANCE_UNVERIFIED',)
    assert result.reserved_intent_count==0

@pytest.mark.parametrize('change',['unhashable_admission','null_request'])
def test_malformed_records_typed_negative(factory,change):
    rig=factory(); c=coordinator(rig); p=genuine_proposal(rig); c.coordinate('batch',(p,))
    state=c._state(c._head()); intent=state['intents']['proposal']
    if change=='unhashable_admission': intent['admission_ids']=[{}]
    else: intent['admission_ids']=[clone(c,'pin','null-request',lambda d:d.update(request=None))]
    append(c,state)
    negative(r08(c),NO8)


def test_accepted_reservation_survives_real_cancel_request(factory):
    rig = factory()
    c = coordinator(rig)
    c.coordinate('batch', (genuine_proposal(rig),))
    c.transition('cancel-request', intent_id='proposal', status='CANCEL_REQUESTED')
    result = r08(c)
    assert result.reserved_intent_count == 1
    assert result.outcome != NO8
