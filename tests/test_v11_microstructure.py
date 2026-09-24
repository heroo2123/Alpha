from dataclasses import replace
from decimal import Decimal

import pytest

from polymarket_scanner.v11.evidence import EvidenceError,EvidenceStore
from polymarket_scanner.v11.microstructure import MakerMicrostructure,MicrostructurePolicy,STREAM_VERSION,TRADE_VERSION
from polymarket_scanner.v11.valuation import contract_target
from test_v11_probability import rule,T


@pytest.fixture
def rig(tmp_path):
    tmp_path.chmod(0o700);now=[T+100.]
    store=EvidenceStore(tmp_path/'micro.sqlite','V11_PAPER',clock=lambda:now[0]);r=rule()
    target=contract_target(r,r.payload['partition'][0]['market_id'],'YES')
    policy=MicrostructurePolicy('fixture','FIXTURE_COLLATERAL',10.,60.,5.,.01,2)
    return dict(store=store,now=now,rule=r,target=target,policy=policy)


def book(rig,key,*,seq=1,previous=None,epoch='first',**change):
    payload=dict(rig['target'],rule_fingerprint=rig['rule'].sha256,collateral_asset='FIXTURE_COLLATERAL',
                 stream_healthy=True,bids=[dict(price='.2',size='8'),dict(price='.1',size='2')],
                 asks=[dict(price='.4',size='2'),dict(price='.5',size='3')],
                 book_sequence=dict(version=STREAM_VERSION,epoch=epoch,sequence=seq,previous_sequence=previous))
    payload.update(change)
    return rig['store'].capture(key,event_id=rig['rule'].payload['event_id'],kind='BOOK',provider='fixture-book',
            source_identity=rig['target']['token_id'],revision=key,observed_at=rig['now'][0],
            evidence_class='SYNTHETIC',payload=payload)


def trade(rig,key,*,observed=None,provider='fixture-public',**change):
    payload=dict(rig['target'],version=TRADE_VERSION,record_type='PUBLIC_TRADE_PRINT',
                 rule_fingerprint=rig['rule'].sha256,collateral_asset='FIXTURE_COLLATERAL',trade_id=key,
                 price='.3',size='2',aggressor_side='BUY',side_semantics='TAKER_SIDE',taker_only_requested=True)
    payload.update(change)
    return rig['store'].capture(key,event_id=rig['rule'].payload['event_id'],kind='TRADE',provider=provider,
            source_identity=rig['target']['token_id'],revision=key,observed_at=rig['now'][0] if observed is None else observed,
            evidence_class='SYNTHETIC',payload=payload)


def evaluate(rig,books=('b1',),trades=(),key='features',**change):
    kw=dict(rule=rig['rule'],market_id=rig['target']['market_id'],side=rig['target']['side'],book_ids=books,trade_ids=trades,policy=rig['policy'])
    return MakerMicrostructure(rig['store']).evaluate(key,**dict(kw,**change))['body']['details']


def test_l1_microprice_imbalance_and_l2_depth_are_dimensioned_observations(rig):
    book(rig,'b1');d=evaluate(rig);f=d['features']
    assert d['outcome']=='MEASURED_RESEARCH_FEATURES'
    assert Decimal(f['midpoint'])==Decimal('.3') and Decimal(f['microprice'])==Decimal('.36')
    assert Decimal(f['spread'])==Decimal('.2') and Decimal(f['l1_imbalance'])==Decimal('.6')
    assert Decimal(f['bid_depth_units'])==10 and Decimal(f['ask_depth_units'])==5
    assert not d['financial_authority'] and d['actual_trading_pnl'] is None


def test_contiguous_sequence_produces_velocity_acceleration_and_sampled_variation(rig):
    book(rig,'b1');rig['now'][0]+=1
    book(rig,'b2',seq=2,previous=1,bids=[dict(price='.25',size='6')]);rig['now'][0]+=1
    book(rig,'b3',seq=3,previous=2,bids=[dict(price='.3',size='4')])
    d=evaluate(rig,('b1','b2','b3'));t=d['temporal']
    assert t['status']=='MEASURED_DECLARED_SEQUENCE'
    assert t['price_velocity_per_second']==pytest.approx(.025)
    assert t['price_acceleration_per_second_squared']==pytest.approx(0)
    assert t['rms_midpoint_change_per_update']==pytest.approx(.025)
    assert Decimal(t['bid_visible_depth_delta'])==-2
    assert t['cancellation_rate'] is t['addition_rate'] is None


@pytest.mark.parametrize('change', ['gap','epoch','missing','too_fast','too_slow','unhealthy_history'])
def test_gaps_reconnect_and_unobserved_sequences_reset_temporal_features(rig,change):
    book(rig,'b1',stream_healthy=change!='unhealthy_history')
    rig['now'][0]+= .001 if change=='too_fast' else 6 if change=='too_slow' else 1
    kw=dict(seq=2,previous=1)
    if change=='gap':kw.update(seq=3,previous=2)
    if change=='epoch':kw['epoch']='reconnected'
    if change=='missing':kw['book_sequence']=None
    book(rig,'b2',**kw)
    d=evaluate(rig,('b1','b2'))
    assert d['features'] is not None and d['temporal']['status']=='UNKNOWN'
    assert d['temporal']['price_velocity_per_second'] is None
    assert d['temporal']['resets']


def test_history_after_reconnect_can_recover_only_with_new_linked_updates(rig):
    book(rig,'b1');rig['now'][0]+=1
    book(rig,'b2',seq=1,epoch='new');rig['now'][0]+=1
    book(rig,'b3',seq=2,previous=1,epoch='new')
    d=evaluate(rig,('b1','b2','b3'))
    assert d['temporal']['contiguous_book_ids']==['b2','b3']
    assert d['temporal']['status']=='MEASURED_DECLARED_SEQUENCE'


@pytest.mark.parametrize('fault',['stale','crossed','missing_side','unhealthy','target','rule'])
def test_current_invalid_book_gates_all_features(rig,fault):
    kw={}
    if fault=='crossed':kw['bids']=[dict(price='.5',size='1')]
    if fault=='missing_side':kw['asks']=[]
    if fault=='unhealthy':kw['stream_healthy']=False
    if fault=='target':kw['token_id']='wrong'
    if fault=='rule':kw['rule_fingerprint']='0'*64
    book(rig,'b1',**kw)
    if fault=='stale':rig['now'][0]+=11
    d=evaluate(rig)
    assert d['outcome']=='GATED' and d['features'] is None


def test_newer_exact_book_cannot_be_hidden_by_supplying_only_older_inputs(rig):
    book(rig,'b1');rig['now'][0]+=1;book(rig,'b2',seq=2,previous=1)
    assert evaluate(rig)['reason']=='MICROSTRUCTURE_CURRENT_BOOK_REQUIRED'


@pytest.mark.parametrize('contract_side',['YES','NO'])
def test_public_print_dedupe_and_taker_direction_never_claim_our_fill(rig,contract_side):
    rig['target']=contract_target(rig['rule'],rig['target']['market_id'],contract_side)
    book(rig,'b1');trade(rig,'t1');trade(rig,'duplicate',trade_id='t1');trade(rig,'sell',aggressor_side='SELL',size='1')
    d=evaluate(rig,trades=('t1','duplicate','sell'));f=d['public_flow']
    assert f['distinct_public_prints']==2 and Decimal(f['reported_units'])==3
    assert Decimal(f['buy_taker_units'])==2 and Decimal(f['sell_taker_units'])==1
    assert f['our_fill_count'] is None and not d['public_trade_through_is_our_fill']
    assert d['fill_probability'] is d['fill_hazard'] is d['queue_position'] is d['conservative_maker_ev'] is None
    assert d['first_reviewed_canary_does_not_require_prior_live_fills']
    assert d['request']['target']['side']==contract_side


def test_legacy_overloaded_contract_side_cannot_bypass_target_binding(rig):
    book(rig,'b1');trade(rig,'t1',side='BUY')
    assert evaluate(rig,trades=('t1',))['reason']=='MICROSTRUCTURE_EXACT_SOURCE_TARGET_REQUIRED'


def test_unknown_side_semantics_do_not_become_aggressor_direction(rig):
    book(rig,'b1');trade(rig,'t1',side_semantics='UNKNOWN')
    d=evaluate(rig,trades=('t1',));flow=d['public_flow']
    assert Decimal(flow['unknown_aggressor_units'])==2 and flow['signed_volume_imbalance'] is None
    assert flow['last_reported_aggressor'] is None


@pytest.mark.parametrize('fault',['revision','cross_provider','account_fill'])
def test_ambiguous_or_wrong_trade_sources_fail_closed(rig,fault):
    book(rig,'b1');trade(rig,'t1')
    if fault=='revision':trade(rig,'t2',trade_id='t1',size='3')
    elif fault=='cross_provider':trade(rig,'t2',provider='another')
    else:trade(rig,'t2',record_type='PAPER_FILL')
    d=evaluate(rig,trades=('t1','t2'))
    assert d['outcome']=='GATED' and d['public_flow'] is None


def test_unknown_historical_availability_is_not_causal_feature_input(rig):
    row=book(rig,'b1');b=row['body']
    rig['store'].capture('history',event_id=row['event_id'],kind='BOOK',provider=b['provider'],source_identity=b['source_identity'],
          revision='history',observed_at=rig['now'][0],evidence_class='HISTORICAL_AVAILABILITY_UNKNOWN',payload=b['payload'])
    assert evaluate(rig,('history',))['reason']=='MICROSTRUCTURE_NONCAUSAL_OR_OUTSIDE_WINDOW'


def test_record_replay_is_historical_and_cannot_refresh_feature_clock(rig):
    book(rig,'b1');first=evaluate(rig);rig['now'][0]+=100
    assert evaluate(rig)==first
    with pytest.raises(EvidenceError,match='COLLISION'):
        evaluate(rig,policy=replace(rig['policy'],depth_levels=1))


def test_racing_new_book_invalidates_measurement_commit(rig,monkeypatch):
    book(rig,'b1');original=rig['store'].audit
    def race(key,**kw):
        book(rig,'racing',seq=2,previous=1)
        return original(key,**kw)
    monkeypatch.setattr(rig['store'],'audit',race)
    with pytest.raises(EvidenceError,match='GUARDED_STATE_CHANGED'):evaluate(rig)


def test_input_bounds_and_duplicate_ids_are_enforced(rig):
    book(rig,'b1')
    with pytest.raises(EvidenceError,match='INPUT_BOUND'):evaluate(rig,('b1','b1'))
    with pytest.raises(EvidenceError,match='POLICY_BOUND'):replace(rig['policy'],depth_levels=100)
