"""Explicit synthetic PAPER execution details; no live attestation or inference.

Ancillary details never replace ledger units/all-in collateral. Invalid details
gate metrics, not reconciliation of an otherwise valid PAPER fill proof.
"""
from .evidence import EvidenceError, finite, identity, sha
from .scenario_risk import number, precise


VERSION='alpha_v11_synthetic_fill_execution_v1'


def valuation_book(value,intent):
    """Select the actual admitted leg without allocating a basket's joint EV."""
    if intent.get('joint_ev_only'):
        legs=[leg for leg in value['legs'] if leg['target']==intent['target']]
        if len(legs)!=1:raise EvidenceError('FILL_EXACT_BASKET_LEG_REQUIRED')
        return legs[0]['book']
    if value['target']!=intent['target']:raise EvidenceError('FILL_VALUATION_TARGET_MISMATCH')
    return value['book']


def _book(store,ref,*,proof,intent,collateral_asset,rule_fingerprint):
    if type(ref) is not dict or set(ref)!={'id','sha256'}:raise EvidenceError('FILL_BOOK_REFERENCE_SCHEMA')
    identity(ref['id']);sha(ref['sha256']);row=store.get(ref['id']);b=row['body'];p=b.get('payload',{})
    if (row['kind']!='BOOK' or row['event_id']!=intent['event_id'] or row['sha256']!=ref['sha256']
            or b.get('evidence_class') not in {'PUBLIC_OBSERVED','SYNTHETIC'}
            or any(p.get(k)!=v for k,v in intent['target'].items())
            or p.get('rule_fingerprint')!=rule_fingerprint or p.get('collateral_asset')!=collateral_asset
            or row['seq']>=proof['seq'] or b.get('observed_at') is None
            or not b['observed_at']<=b['received_at']<=b['available_at']<=b['recorded_at']):
        raise EvidenceError('FILL_BOOK_IDENTITY_OR_CHRONOLOGY')
    return row


@precise
def execution_details(store,proof,intent,*,account_id,collateral_asset):
    """Validate optional exact timing/cost evidence without changing any records."""
    b=proof['body'];p=b.get('payload',{})
    if (store.namespace!='V11_PAPER' or proof['kind']!='TRADE' or proof['event_id']!=intent['event_id']
            or b.get('evidence_class')!='SYNTHETIC' or p.get('record_type')!='PAPER_FILL'
            or p.get('execution_namespace')!=store.namespace or p.get('account_id')!=account_id
            or p.get('intent_id')!=intent['proposal_id'] or p.get('token_id')!=intent['token_id']
            or p.get('direction')!=intent['direction']):
        raise EvidenceError('FILL_SYNTHETIC_PROOF_SCOPE')
    if 'execution_details' not in p:
        return dict(status='UNKNOWN',reason='LEGACY_FILL_TIMING_AND_PRICE_COST_UNIDENTIFIED',
            executed_at=None,price_per_share=None,fee_collateral=None,other_cost_collateral=None,
            signal_book_ref=None,post_validation_book_ref=None,financial_authority=False)
    d=p['execution_details']
    fields={'version','timing_authority','executed_at','price_per_share','fee_collateral','other_cost_collateral',
            'collateral_asset','signal_book_ref','post_validation_book_ref'}
    if (type(d) is not dict or set(d)!=fields or d['version']!=VERSION
            or d['timing_authority']!='SYNTHETIC_PAPER_ENGINE' or d['collateral_asset']!=collateral_asset):
        raise EvidenceError('FILL_EXECUTION_DETAILS_SCHEMA')
    at=finite(d['executed_at']);price=number(d['price_per_share']);qty=number(p['units'])
    fees=number(d['fee_collateral']);costs=number(d['other_cost_collateral']);all_in=number(p['all_in_collateral'])
    if not 0<price<1 or not 0<qty<=1_000_000:raise EvidenceError('FILL_EXECUTION_PRICE_OR_QUANTITY_BOUND')
    expected=price*qty+(fees+costs)*(1 if intent['direction']=='BUY' else -1)
    if expected!=all_in:raise EvidenceError('FILL_EXECUTION_COST_CONSERVATION')
    value=store.get(intent['valuation_id']);vd=value['body'].get('details',{})
    if (value['kind']!='MEASUREMENT' or value['event_id']!=intent['event_id']
            or vd.get('binding')!=intent['binding'] or value['seq']>=proof['seq']
            or not value['body']['recorded_at']<=at<=b['received_at']<=b['available_at']<=b['recorded_at']
            or b.get('observed_at')!=at):
        raise EvidenceError('FILL_EXECUTION_VALUATION_OR_TIME')
    original=valuation_book(vd,intent)
    if d['signal_book_ref']!=dict(id=original['book_id'],sha256=original['book_sha256']):
        raise EvidenceError('FILL_ORIGINAL_SIGNAL_BOOK_REQUIRED')
    signal=_book(store,d['signal_book_ref'],proof=proof,intent=intent,collateral_asset=collateral_asset,
        rule_fingerprint=intent['binding']['rule_fingerprint'])
    post=_book(store,d['post_validation_book_ref'],proof=proof,intent=intent,collateral_asset=collateral_asset,
        rule_fingerprint=intent['binding']['rule_fingerprint'])
    if (not signal['seq']<value['seq']<post['seq']<proof['seq']
            or signal['body']['available_at']>value['body']['recorded_at']
            or not value['body']['recorded_at']<=post['body']['received_at']<=post['body']['recorded_at']<=at
            or (signal['body']['provider'],signal['body']['source_identity'])!=(post['body']['provider'],post['body']['source_identity'])):
        raise EvidenceError('FILL_POST_VALIDATION_BOOK_REQUIRED')
    return dict(d,status='VALIDATED_SYNTHETIC_DETAILS',reason='EXPLICIT_SYNTHETIC_TIMING_AND_CONSERVED_COST',
        gross_collateral=str(price*qty),all_in_collateral=p['all_in_collateral'],
        venue_execution_attested=False,slippage=None,financial_authority=False)
