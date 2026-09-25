"""Explicit synthetic engine evidence; never an actual venue fill attestation."""
from copy import deepcopy
from decimal import Decimal

import pytest

from polymarket_scanner.v11.fill_evidence import VERSION, execution_details, valuation_book
from test_v11_basket_coordinator import rig, reserve, fill, proof, setup, bundle, factory
from test_v11_pws_admission import coordinator
from test_v11_position_management import inventory, reserved_exit


def book(r,key,original,**changes):
    b=original['body']
    return r['store'].capture(key,event_id=original['event_id'],kind='BOOK',provider=b['provider'],
        source_identity=b['source_identity'],revision=key,observed_at=r['now'][0],evidence_class=b['evidence_class'],
        payload=dict(b['payload'],**changes))


def detailed_fill(r,*,intent_id='basket:leg:0',key='explicit',units='1',price='.18',fees='.01',other='.01',mutate=None):
    c=coordinator(r);intent=c._state(c._head())['intents'][intent_id]
    original=valuation_book(r['store'].get(intent['valuation_id'])['body']['details'],intent)
    signal=r['store'].get(original['book_id']);r['now'][0]+=.01
    post=book(r,key+'-post',signal);r['now'][0]+=.01
    d=dict(version=VERSION,timing_authority='SYNTHETIC_PAPER_ENGINE',executed_at=r['now'][0],
        price_per_share=price,fee_collateral=fees,other_cost_collateral=other,collateral_asset=c.policy.collateral_asset,
        signal_book_ref=dict(id=signal['id'],sha256=signal['sha256']),post_validation_book_ref=dict(id=post['id'],sha256=post['sha256']))
    all_in=Decimal(price)*Decimal(units)+(Decimal(fees)+Decimal(other))*(1 if intent['direction']=='BUY' else -1)
    if mutate:mutate(d)
    proof(r,intent_id,key,'PAPER_FILL',fill_id=key,units=units,all_in_collateral=str(all_in),direction=intent['direction'],execution_details=d)
    return c.record_fill('record-'+key,key)


def test_explicit_cost_timing_and_original_basket_leg_are_additive(rig):
    reserve(rig);d=detailed_fill(rig)['body']['details'];e=d['execution_evidence']
    assert e['status']=='VALIDATED_SYNTHETIC_DETAILS'
    assert Decimal(e['gross_collateral'])==Decimal('.18') and Decimal(e['all_in_collateral'])==Decimal('.2')
    assert e['signal_book_ref']['id']=='basket-book0' and e['post_validation_book_ref']['id']=='explicit-post'
    assert not e['venue_execution_attested'] and e['slippage'] is None and not e['financial_authority']
    assert d['state']['lots']['explicit']['entry']['ev_unit']=='JOINT_BASKET_TOTAL'
    assert Decimal(d['state']['cash'])==Decimal('9.8') and not d['state']['faults']


@pytest.mark.parametrize('field,value',[
    ('executed_at',99999.),('price_per_share','.7'),('fee_collateral','-.01'),('fee_collateral',None),
    ('timing_authority','VENUE'),('collateral_asset','OTHER'),('version','UNKNOWN'),
    ('signal_book_ref',{'id':'basket-book1','sha256':'a'*64}),('post_validation_book_ref',{}),
])
def test_bad_optional_telemetry_cannot_hide_reconciled_cash_or_units(rig,field,value):
    reserve(rig);d=detailed_fill(rig,mutate=lambda e:e.update({field:value}))['body']['details']
    assert d['execution_evidence']['status']=='GATED' and d['execution_evidence']['reconciliation_preserved']
    assert Decimal(d['state']['cash'])==Decimal('9.8') and d['state']['lots']['explicit']['units']=='1'
    assert not d['state']['faults']


def test_legacy_proof_and_replayed_result_keep_their_original_hashes(rig):
    reserve(rig);first=fill(rig,0);c=coordinator(rig);old=deepcopy(rig['store'].get('fill-0'))
    intent=first['state']['intents']['basket:leg:0']
    result=execution_details(rig['store'],old,intent,account_id='account',collateral_asset=c.policy.collateral_asset)
    assert result['status']=='UNKNOWN' and result['executed_at'] is None
    assert 'execution_evidence' not in first
    assert c.record_fill('record-fill-0','fill-0')['body']['details']==first and rig['store'].get('fill-0')==old


def test_explicit_sell_proceeds_preserve_entry_basis_and_exit_attribution(rig):
    inventory(rig);p,_=reserved_exit(rig)
    d=detailed_fill(rig,intent_id=p.proposal_id,units='.5',price='.14')['body']['details']
    assert d['execution_evidence']['status']=='VALIDATED_SYNTHETIC_DETAILS'
    assert Decimal(d['execution_evidence']['all_in_collateral'])==Decimal('.05')
    assert Decimal(d['state']['cash'])==Decimal('9.65')
    assert Decimal(d['state']['realized_entries'][0]['pnl'])==Decimal('-.05')
    assert d['state']['realized_entries'][0]['exit_attribution_is_decision_metadata_not_extra_pnl']
