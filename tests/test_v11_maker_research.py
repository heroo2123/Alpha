from copy import deepcopy
from dataclasses import asdict, replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.event_risk import SafetyReductions
from polymarket_scanner.v11.maker_research import MakerResearch,MakerResearchPolicy,ResearchQuote
from polymarket_scanner.v11.microstructure import MakerMicrostructure,MicrostructurePolicy,STREAM_VERSION,TRADE_VERSION
from polymarket_scanner.v11.paper_coordinator import PaperCoordinator
from polymarket_scanner.v11.valuation import contract_target
from test_v11_certification_rules import setup
from test_v11_model_artifacts import bundle
from test_v11_strategy_pipeline import factory
from test_v11_pws_admission import coordinator


@pytest.fixture
def rig(factory):
    rig=factory('MAKER_RESEARCH');rig['coordinator']=coordinator(rig)
    rig['policy']=MakerResearchPolicy('fixture-only',30.,10.,'10',4,16)
    rig['micro_policy']=MicrostructurePolicy('fixture-only','FIXTURE_COLLATERAL',20.,60.,5.,.01,2)
    book(rig,'initial',sequence=1,previous=None)
    features(rig,'initial','micro')
    rig['quote']=ResearchQuote('quote','thesis',rig['context'],rig['rule'],rig['binding'],'pin','state2','micro',
            rig['request'].market_id,'YES','BUY','.1','2','.01',rig['now'][0]+20.)
    return rig


def maker(rig):return MakerResearch(rig['coordinator'],rig['policy'])


def book(rig,key,*,sequence=2,previous=1,epoch='one',market_id=None,evidence_class='SYNTHETIC',observed=None,**changes):
    base=rig['store'].get('book2')['body'];p=deepcopy(base['payload'])
    if market_id:p.update(contract_target(rig['rule'],market_id,'YES'))
    p['book_sequence']=dict(version=STREAM_VERSION,epoch=epoch,sequence=sequence,previous_sequence=previous)
    p.update(changes)
    return rig['store'].capture(key,event_id=rig['context'].event_id,kind='BOOK',provider=base['provider'],
        source_identity=p['token_id'],revision=key,observed_at=rig['now'][0] if observed is None else observed,
        evidence_class=evidence_class,payload=p)


def features(rig,book_id,key,*,trade_ids=()):
    market=rig['store'].get(book_id)['body']['payload']['market_id']
    return MakerMicrostructure(rig['store']).evaluate(key,rule=rig['rule'],market_id=market,side='YES',
          book_ids=(book_id,),trade_ids=trade_ids,policy=rig['micro_policy'])


def propose(rig,key='proposal',**changes):
    return maker(rig).propose(key,replace(rig['quote'],**changes))['body']['details']


def test_proposal_uses_real_scoped_admission_and_common_risk_without_reserving_cash(rig):
    d=propose(rig)
    assert d['outcome']=='OBSERVING_RESEARCH_QUOTE', d['reason']
    assert Decimal(d['projected_risk']['reserved_cash'])==Decimal('.22')
    assert rig['coordinator']._head() is None
    assert Decimal(rig['coordinator'].snapshot()['reserved_cash'])==0
    assert d['quotes']['quote']['fill_status']=='UNKNOWN_NOT_AN_ORDER'
    assert d['fill_probability'] is d['queue_position'] is d['actual_trading_pnl'] is None
    assert not d['financial_authority'] and not d['orders_submitted'] and not d['account_ledger_mutated']


@pytest.mark.parametrize('change,reason',[
    ({'limit_price':'.2'},'POST_ONLY_BOUNDARY'),({'units':'11'},'SIZE_BOUND'),
    ({'direction':'SELL','limit_price':'.3'},'SALES_EXCEED_HELD'),
    ({'admission_id':'missing'},'EVIDENCE_MISSING'),({'event_state_id':'state1'},'STATE_CHANGED'),
])
def test_unqualified_research_quote_is_gated_without_account_or_research_exposure(rig,change,reason):
    d=propose(rig,**change)
    assert d['outcome']=='GATED' and reason in d['reason']
    assert not d['quotes'] and rig['coordinator']._head() is None


def test_expired_features_and_hidden_newer_book_are_refused(rig):
    book(rig,'newer')
    assert propose(rig)['reason']=='MAKER_BOOK_NOT_CURRENT'
    rig['now'][0]+=11
    assert propose(rig,'stale')['reason']=='MAKER_CURRENT_EXACT_FEATURE_REQUIRED'


def test_source_arrival_requires_new_reviewed_thesis_and_cannot_reuse_old_pin(rig):
    old=rig['store'].get('official2')['body'];rig['now'][0]+=.1
    rig['store'].capture('revision',event_id=rig['context'].event_id,kind='OFFICIAL_OBSERVATION',
            provider=old['provider'],source_identity=old['source_identity'],revision='new',
            observed_at=rig['now'][0],payload=old['payload'],evidence_class='SYNTHETIC')
    assert 'SOURCE_CHANGED' in propose(rig)['reason']


def test_duplicate_thesis_and_token_do_not_multiply_research_exposure(rig):
    assert propose(rig)['outcome']=='OBSERVING_RESEARCH_QUOTE'
    d=propose(rig,'second',quote_id='second',thesis_id='new')
    assert d['reason']=='MAKER_EXISTING_THESIS_OR_TOKEN_RESEARCH_QUOTE' and len(d['quotes'])==1


def test_all_observing_quotes_share_current_account_cash_ceiling(rig):
    c=rig['coordinator'];p=replace(c.policy,initial_hypothetical_cash='.3',capital_limit='.3',
                                 per_intent_cash_limit='.3',daily_loss_limit='.3')
    rig['coordinator']=PaperCoordinator(rig['store'],policy=p,correlation=c.correlation,limits=c.limits)
    assert propose(rig)['outcome']=='OBSERVING_RESEARCH_QUOTE'
    market=rig['rule'].payload['partition'][1]['market_id']
    book(rig,'other',market_id=market);features(rig,'other','other-micro')
    d=propose(rig,'second',quote_id='second',thesis_id='second',market_id=market,microstructure_id='other-micro')
    assert d['reason']=='MAKER_PROJECTED_COMMON_ACCOUNT_RISK' and len(d['quotes'])==1


def account_inventory_fixture(rig):
    # Existing synthetic PAPER account state, not inferred from public prints.
    c=rig['coordinator'];s=c._state(c._head());r=rig['rule'];event=rig['context'].event_id
    s['cash']='9';s['rules'][event]=asdict(r);s['contexts'][event]=asdict(rig['context'])
    s['lots']['fixture-lot']=dict(lot_id='fixture-lot',event_id=event,
        token_id=contract_target(r,rig['quote'].market_id,'YES')['token_id'],units='3',all_in_cost_basis='1',
        attribution=[dict(strategy='fixture',weight='1')])
    return c._commit('fixture-existing-inventory',dict(action='SYNTHETIC_FIXTURE'),c._head(),s,{})


def test_sell_research_projects_actual_paper_inventory_without_selling_or_releasing_it(rig):
    original=account_inventory_fixture(rig)
    d=propose(rig,direction='SELL',limit_price='.3')
    assert d['outcome']=='OBSERVING_RESEARCH_QUOTE',d['reason']
    assert rig['coordinator']._head()==original
    scenario=d['projected_risk']['scenarios'][0]
    token=contract_target(rig['rule'],rig['quote'].market_id,'YES')['token_id']
    assert Decimal(scenario['concentration'][token]['reserved_sell_units'])==2
    maker(rig).retire('retire',quote_id='quote',reason='END')
    assert rig['coordinator']._head()==original


def test_unknown_existing_paper_order_keeps_its_cash_risk_in_research_projection(rig):
    original=account_inventory_fixture(rig);c=rig['coordinator'];s=c._state(original)
    event=rig['context'].event_id;token=contract_target(rig['rule'],rig['quote'].market_id,'YES')['token_id']
    s['intents']['uncertain']=dict(proposal_id='uncertain',event_id=event,token_id=token,direction='BUY',
        units='11',filled_units='0',unit_collateral_bound='.8',status='UNKNOWN',
        attribution=[dict(strategy='fixture',weight='1')])
    current=c._commit('fixture-ambiguous-intent',dict(action='SYNTHETIC_FIXTURE'),original,s,{})
    assert propose(rig)['reason']=='MAKER_PROJECTED_COMMON_ACCOUNT_RISK'
    assert c._head()==current


def test_bad_account_update_does_not_advance_sampled_eligibility_span(rig):
    propose(rig);account_inventory_fixture(rig)
    c=rig['coordinator'];head=c._head();state=c._state(head);state['faults'].append('SYNTHETIC_RECONCILIATION_FAULT')
    c._commit('fault',dict(action='SYNTHETIC_FIXTURE'),head,state,{})
    rig['now'][0]+=1;book(rig,'b2');features(rig,'b2','m2')
    d=maker(rig).observe('observe',quote_id='quote',microstructure_id='m2')['body']['details']
    assert d['outcome']=='RETIRED' and d['reason']=='MAKER_PROJECTED_COMMON_ACCOUNT_RISK'
    assert d['quotes']['quote']['sampled_span_seconds']==0 and d['quotes']['quote']['distinct_book_samples']==1


def test_linked_new_books_measure_sample_span_and_repeated_book_does_not(rig):
    propose(rig);rig['now'][0]+=1;book(rig,'b2');features(rig,'b2','m2')
    first=maker(rig).observe('observe',quote_id='quote',microstructure_id='m2')['body']['details']
    assert first['outcome']=='OBSERVING' and first['quotes']['quote']['sampled_span_seconds']==1
    rig['now'][0]+=1
    repeated=maker(rig).observe('repeat',quote_id='quote',microstructure_id='m2')['body']['details']
    assert repeated['quotes']['quote']['sampled_span_seconds']==1
    assert repeated['quotes']['quote']['distinct_book_samples']==2
    assert repeated['quote_age_seconds']==2 and repeated['actual_trading_pnl'] is None


@pytest.mark.parametrize('gap',['sequence','reconnect','sampling'])
def test_gap_resets_sampled_span_without_fabricating_quote_or_queue_survival(rig,gap):
    propose(rig);rig['now'][0]+=6 if gap=='sampling' else 1
    book(rig,'b2',sequence=3 if gap=='sequence' else 2,previous=2 if gap=='sequence' else 1,
         epoch='new' if gap=='reconnect' else 'one');features(rig,'b2','m2')
    d=maker(rig).observe('observe',quote_id='quote',microstructure_id='m2')['body']['details']
    assert d['outcome']=='OBSERVING' and d['continuity_reset']
    assert d['quotes']['quote']['sampled_span_seconds']==0 and d['quotes']['quote']['gap_count']==1


@pytest.mark.parametrize('cause',['expired','operator','crossing','source'])
def test_lifecycle_retires_on_invalidating_evidence_without_claiming_order_cancellation(rig,cause):
    propose(rig);rig['now'][0]+=.1
    if cause=='expired':rig['now'][0]+=20
    if cause=='operator':SafetyReductions(rig['store']).apply('halt',scope='ACCOUNT',scope_id='account',
                                      action='CANCEL_AND_HALT',actor='fixture',reason='fixture')
    if cause=='crossing':book(rig,'b2',bids=[dict(price='.05',size='20')],asks=[dict(price='.09',size='20')]);features(rig,'b2','m2')
    if cause=='source':
        old=rig['store'].get('official2')['body']
        rig['store'].capture('revised',event_id=rig['context'].event_id,kind='OFFICIAL_OBSERVATION',
                            provider=old['provider'],source_identity=old['source_identity'],revision='new',
                            observed_at=rig['now'][0],payload=old['payload'],evidence_class='SYNTHETIC')
    d=maker(rig).observe('observe',quote_id='quote',microstructure_id='m2' if cause=='crossing' else 'micro')['body']['details']
    assert d['outcome']=='RETIRED' and d['current_inventory_unchanged']
    assert d['cancellation_status']=='NOT_APPLICABLE_NO_ORDER'
    with pytest.raises(EvidenceError,match='CANNOT_RESUME'):maker(rig).observe('resume',quote_id='quote',microstructure_id='micro')


def test_public_trade_through_remains_public_flow_without_any_fill_or_cash_change(rig):
    propose(rig);rig['now'][0]+=1;row=book(rig,'b2');p=row['body']['payload']
    payload={k:p[k] for k in ('market_id','condition_id','token_id','side','rule_fingerprint','collateral_asset')}
    payload.update(version=TRADE_VERSION,record_type='PUBLIC_TRADE_PRINT',trade_id='print',price='.09',size='10',
                   aggressor_side='SELL',side_semantics='TAKER_SIDE',taker_only_requested=True)
    rig['store'].capture('trade',event_id=rig['context'].event_id,kind='TRADE',provider='fixture',
       source_identity=p['token_id'],revision='trade',observed_at=rig['now'][0],payload=payload,evidence_class='SYNTHETIC')
    features(rig,'b2','m2',trade_ids=('trade',))
    d=maker(rig).observe('observe',quote_id='quote',microstructure_id='m2')['body']['details']
    assert d['outcome']=='OBSERVING' and d['quotes']['quote']['fill_status']=='UNKNOWN_NOT_AN_ORDER'
    assert Decimal(rig['coordinator'].snapshot()['cash'])==10 and rig['coordinator']._head() is None


def test_restart_replay_and_retirement_preserve_original_clock_and_evidence(rig):
    first=propose(rig);rig['now'][0]+=50
    assert propose(rig)==first
    with pytest.raises(EvidenceError,match='REPLAY_CONFLICT'):propose(rig,units='1')
    d=maker(rig).retire('retire',quote_id='quote',reason='RESEARCH_END')['body']['details']
    assert d['quotes']['quote']['status']=='RETIRED'
    assert maker(rig).retire('retire',quote_id='quote',reason='RESEARCH_END')['body']['details']==d


def test_atomic_source_race_cannot_commit_an_observing_quote(rig,monkeypatch):
    original=rig['store'].audit;once=[False]
    def race(key,**kw):
        if key=='proposal' and not once[0]:once[0]=True;book(rig,'racing')
        return original(key,**kw)
    monkeypatch.setattr(rig['store'],'audit',race)
    d=propose(rig)
    assert d['outcome']=='GATED' and not d['quotes']
    assert d['reason']=='AUDIT_GUARDED_STATE_CHANGED'


def test_atomic_account_advance_requires_recomputation_and_preserves_new_account_state(rig,monkeypatch):
    original=rig['store'].audit;once=[False]
    def race(key,**kw):
        if key=='proposal' and not once[0]:once[0]=True;account_inventory_fixture(rig)
        return original(key,**kw)
    monkeypatch.setattr(rig['store'],'audit',race)
    d=propose(rig)
    assert d['outcome']=='GATED' and not d['quotes']
    assert d['reason']=='AUDIT_GUARDED_STATE_CHANGED'
    assert rig['coordinator']._head()['id']=='fixture-existing-inventory'


def test_first_horizon_book_controls_counterfactual_even_if_later_book_looks_better(rig):
    propose(rig);rig['now'][0]+=1
    book(rig,'horizon',bids=[dict(price='.12',size='20')],asks=[dict(price='.2',size='20')])
    rig['now'][0]+=1;book(rig,'better',bids=[dict(price='.19',size='20')])
    d=maker(rig).markout('mark',quote_id='quote',horizon_seconds=1,tolerance_seconds=5,fee_per_share='.005')['body']['details']
    assert d['status']=='MEASURED' and d['book_id']=='horizon' and Decimal(d['markout_per_share'])==Decimal('.005')
    assert d['actual_trading_pnl'] is None and d['source_class']=='SYNTHETIC'
    assert rig['coordinator']._head() is None


@pytest.mark.parametrize('fault',['fee','depth','unknown','delayed','wrong_target'])
def test_missing_horizon_economics_or_causality_is_unknown_not_zero_or_favorable_fill(rig,fault):
    propose(rig);old=rig['now'][0];rig['now'][0]+=1
    changes={}
    if fault=='depth':changes['bids']=[dict(price='.15',size='1')]
    if fault=='wrong_target':changes['rule_fingerprint']='0'*64
    book(rig,'horizon',evidence_class='HISTORICAL_AVAILABILITY_UNKNOWN' if fault=='unknown' else 'SYNTHETIC',
            observed=old if fault=='delayed' else None,**changes)
    d=maker(rig).markout('mark',quote_id='quote',horizon_seconds=1,tolerance_seconds=5,
                       fee_per_share=None if fault=='fee' else '0')['body']['details']
    assert d['status']=='UNKNOWN' and d['markout_per_share'] is None and d['actual_trading_pnl'] is None


def test_markout_replay_does_not_refresh_an_unknown_observation_with_later_data(rig):
    propose(rig)
    first=maker(rig).markout('mark',quote_id='quote',horizon_seconds=5,tolerance_seconds=0,fee_per_share='0')
    rig['now'][0]+=5;book(rig,'horizon')
    assert maker(rig).markout('mark',quote_id='quote',horizon_seconds=5,tolerance_seconds=0,fee_per_share='0')==first
    assert maker(rig).markout('new-mark',quote_id='quote',horizon_seconds=5,tolerance_seconds=0,fee_per_share='0')['body']['details']['status']=='MEASURED'
