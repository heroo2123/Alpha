import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from polymarket_scanner.v11.collection import PublicCollector, SourceRequest
from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.maker_rewards import MakerRewards, PAYMENT_VERSION
from polymarket_scanner.v11.paper_runtime import PaperRuntime
from polymarket_scanner.v11.reward_rules import ENDPOINT, PROVIDER, RewardPolicy, liquidity_score, parameters, pursuit_gate, reward_request
from test_v11_maker_research import rig, factory, setup, bundle, maker, propose
from test_v11_runtime_maker import integrated


def policy(rig, **changes):
    return replace(RewardPolicy('SYNTHETIC_REVIEW_ONLY', rig['now'][0], 300., 120., 8), **changes)


def raw(rig, key='rewards', **changes):
    member = next(m for m in rig['rule'].payload['partition'] if m['market_id'] == rig['quote'].market_id)
    day = datetime.fromtimestamp(rig['now'][0], timezone.utc).date()
    p = dict(id=member['market_id'], conditionId=member['condition_id'], outcomes=['Yes','No'],
        clobTokenIds=[member['yes_token'],member['no_token']], active=True, closed=False, acceptingOrders=True,
        rewardsMinSize=1, rewardsMaxSpread=10, clobRewards=[dict(id='pool',conditionId=member['condition_id'],
            assetAddress='FIXTURE_COLLATERAL',rewardsAmount=1000,rewardsDailyRate=100,
            startDate=(day-timedelta(days=1)).isoformat(),endDate=(day+timedelta(days=1)).isoformat())],
        feesEnabled=True, feeSchedule=dict(rate=.05,exponent=1,takerOnly=True,rebateRate=.25))
    p.update(changes)
    return rig['store'].capture(key,event_id=rig['context'].event_id,kind='RULES',provider=PROVIDER,
        source_identity='reward-market:'+member['market_id'], revision=key, evidence_class='SYNTHETIC',
        payload=dict(response=p,endpoint=ENDPOINT+'/'+member['market_id'],request_params={},http_status=200,response_format='JSON'))


def engine(rig): return MakerRewards(maker(rig), policy(rig))


def track(rig):
    assert propose(rig)['outcome'] == 'OBSERVING_RESEARCH_QUOTE'
    raw(rig); rewards = engine(rig)
    record = rewards.track('reward-track',quote_id='quote',parameters_id='rewards')
    return rewards,record


def parsed(rig, key='rewards', p=None):
    return parameters(rig['store'],key,rule=rig['rule'],market_id=rig['quote'].market_id,policy=p or policy(rig))


def test_public_parameters_bind_raw_exact_market_and_separate_missing_fees(rig):
    raw(rig); p=parsed(rig)
    assert p['liquidity_status']=='PARAMETERS_PRESENT' and p['minimum_size']=='1'
    assert p['maximum_distance']=='0.1' and p['fees']['rebate_rate']=='0.25'
    assert p['source_class']=='SYNTHETIC' and p['source_id']=='rewards'
    raw(rig,'missing-fees',feeSchedule=None)
    assert parsed(rig,'missing-fees')['fee_status']=='UNKNOWN'


@pytest.mark.parametrize('fault',['condition','tokens','labels','endpoint','scope','historical'])
def test_wrong_or_uncausal_settings_cannot_be_reused(rig,fault):
    changes={'conditionId':'wrong'} if fault=='condition' else {'clobTokenIds':['wrong','tokens']} if fault=='tokens' else {'outcomes':['No','Yes']} if fault=='labels' else {}
    original=raw(rig,**changes); p=deepcopy(original['body']['payload']); kw={}
    if fault=='endpoint':p['endpoint']='https://unreviewed.invalid/markets'
    if fault=='scope':p['request_params']={'id':'different'}
    if fault=='historical':kw['evidence_class']='HISTORICAL_AVAILABILITY_UNKNOWN'
    rig['store'].capture('bad',event_id=original['event_id'],kind='RULES',provider=PROVIDER,
        source_identity=original['body']['source_identity'],revision='bad',payload=p,**kw)
    with pytest.raises(EvidenceError,match='REWARD_'):parsed(rig,'bad')


def test_current_revision_and_receipt_expiry_do_not_redate_old_rules(rig):
    raw(rig); raw(rig,'revision')
    with pytest.raises(EvidenceError,match='SUPERSEDED'):parsed(rig)
    p=policy(rig);rig['now'][0]+=121
    with pytest.raises(EvidenceError,match='STALE'):parsed(rig,'revision',p)


@pytest.mark.parametrize('field,value',[('rewardsMinSize',None),('rewardsMaxSpread',True),('rewardsMinSize',-1),('clobRewards',None)])
def test_unknown_and_malformed_liquidity_parameters_are_unknown_not_free_rewards(rig,field,value):
    raw(rig,**{field:value});p=parsed(rig)
    assert p['liquidity_status']=='UNKNOWN' and not p['allocations']
    assert p['fee_status']=='PARAMETERS_PRESENT'


def test_duplicate_pool_identity_cannot_double_count_market_pool(rig):
    a=raw(rig)['body']['payload']['response']['clobRewards'][0]
    raw(rig,'duplicate',clobRewards=[a,a])
    assert parsed(rig,'duplicate')['liquidity_status']=='UNKNOWN'


def order(key,side='YES',direction='BUY',price='.49',units='100'):
    return dict(quote_id=key,side=side,direction=direction,price=price,units=units)


def score(orders,mid='.5'):
    return liquidity_score(orders,midpoint=mid,minimum_size='10',maximum_distance='.03')


def test_complementary_quote_groups_and_quadratic_contributions():
    d=score((order('yes-bid'),order('no-ask','NO','SELL','.51'),order('yes-ask','YES','SELL','.51'),order('no-bid','NO')))
    assert d['orders']['yes-bid']['group']==d['orders']['no-ask']['group']==0
    assert d['orders']['yes-ask']['group']==d['orders']['no-bid']['group']==1
    assert abs(Decimal(d['minimum_score'])-Decimal(800)/9)<Decimal('1e-24')
    assert d['expected_epoch_share'] is None and not d['official_scoring_confirmed']


@pytest.mark.parametrize('mid,price,positive',[('.1','.09',True),('.9','.89',True),('.09','.08',False),('.91','.9',False)])
def test_single_sided_scoring_midpoint_boundary_is_explicit(mid,price,positive):
    assert (Decimal(score((order('q',price=price),),mid)['minimum_score'])>0)==positive


def test_exact_maximum_distance_and_small_size_have_zero_conditional_score():
    d=score((order('edge',price='.47'),order('small',units='9')))
    assert all(Decimal(p['score'])==0 for p in d['orders'].values())


def test_reward_estimates_never_fabricate_queue_execution_or_cash(rig):
    rewards,row=track(rig);d=row['body']['details'];a=d['state']['tracked']['quote']['latest_assessment']
    assert a['outcome']=='MEASURED_RESEARCH_ONLY'
    assert a['reward_ranges_by_asset']['FIXTURE_COLLATERAL']['lower']=='0'
    assert a['reward_ranges_by_asset']['FIXTURE_COLLATERAL']['upper']=='100'
    assert a['trading_ev_excluding_rewards'] is a['actual_received_reward'] is a['expected_maker_rebate'] is None
    assert a['pursuit']=='GATED_UNKNOWN_ECONOMICS'
    assert a['conditional_score']['reference_class']=='LOCAL_BOOK_MIDPOINT_NOT_OFFICIAL_SIZE_ADJUSTED_REFERENCE'
    assert Decimal(a['maker_rebate_if_fully_filled']['proportional_rebate'])==Decimal('.00225')
    assert rig['coordinator']._head() is None and Decimal(rig['coordinator'].snapshot()['cash'])==10


def test_end_day_does_not_silently_assume_schedule_inclusivity(rig):
    propose(rig);a=raw(rig)['body']['payload']['response']['clobRewards'][0]
    a['endDate']=datetime.fromtimestamp(rig['now'][0],timezone.utc).date().isoformat()
    raw(rig,'boundary',clobRewards=[a]);r=engine(rig).track('track',quote_id='quote',parameters_id='boundary')
    d=r['body']['details']['state']['tracked']['quote']['latest_assessment']
    assert d['outcome']=='UNKNOWN' and d['reason']=='REWARD_END_DATE_BOUNDARY_UNVERIFIED'
    assert not d['reward_ranges_by_asset']


def test_same_parameter_refresh_does_not_retire_or_renew_quote_lifetime(rig):
    rewards,initial=track(rig);expiry=maker(rig)._state(maker(rig)._head())['quote']['expires_at']
    raw(rig,'new-receipt');r=rewards.refresh('refresh')
    assert r['body']['details']['results']==[dict(quote_id='quote',outcome='RECOMPUTED')]
    q=maker(rig)._state(maker(rig)._head())['quote']
    assert q['status']=='OBSERVING' and q['expires_at']==expiry


def test_parameter_change_retires_research_without_cancelling_real_order_or_mutating_cash(rig):
    rewards,_=track(rig);raw(rig,'changed',rewardsMinSize=500)
    d=rewards.refresh('refresh')['body']['details']
    assert d['results'][0]['reason']=='REWARD_RULES_CHANGED_RECOMPUTE_NEW_QUOTE'
    assert maker(rig)._state(maker(rig)._head())['quote']['status']=='RETIRED'
    assert rig['coordinator']._head() is None


def test_expired_program_review_retires_tracked_reward_research(rig):
    propose(rig);raw(rig);rewards=MakerRewards(maker(rig),policy(rig,maximum_methodology_age_seconds=1))
    rewards.track('track',quote_id='quote',parameters_id='rewards');rig['now'][0]+=2
    assert rewards.refresh('refresh')['body']['details']['results'][0]['reason']=='REWARD_PROGRAM_REVIEW_STALE'


def test_historical_replay_does_not_refresh_or_change_reward_pin(rig):
    rewards,old=track(rig);raw(rig,'changed',rewardsMinSize=500)
    assert rewards.track('reward-track',quote_id='quote',parameters_id='rewards')==old
    with pytest.raises(EvidenceError,match='REPLAY_CONFLICT'):
        rewards.track('reward-track',quote_id='quote',parameters_id='changed')


def test_rules_race_cannot_commit_a_fresh_qualification(rig,monkeypatch):
    propose(rig);raw(rig);rewards=engine(rig);original=rig['store'].audit;once=[False]
    def race(key,**kw):
        if key=='track' and not once[0]:once[0]=True;raw(rig,'race',rewardsMaxSpread=1)
        return original(key,**kw)
    monkeypatch.setattr(rig['store'],'audit',race)
    with pytest.raises(EvidenceError,match='GUARDED_STATE_CHANGED'):
        rewards.track('track',quote_id='quote',parameters_id='rewards')
    assert rewards._head() is None


@pytest.mark.parametrize('trading,reward,result',[(None,'0','GATED_UNKNOWN_ECONOMICS'),('-2','1','GATED_NONPOSITIVE_COMBINED_FLOOR'),('1','0','RESEARCH_ONLY_OTHER_GATES_REQUIRED'),('0','0','GATED_NONPOSITIVE_COMBINED_FLOOR')])
def test_only_same_horizon_conservative_economics_can_support_reward_pursuit(trading,reward,result):
    assert pursuit_gate(trading,reward)==result


def receipts(rewards,*,prefix='income',amount='2',asset='FIXTURE_COLLATERAL',program='LIQUIDITY_REWARD',transaction='a'*64,changes=None,evidence_class='SYNTHETIC'):
    base=dict(version=PAYMENT_VERSION,account_id='account',program=program,asset=asset,amount=amount,
        chain_id=137,transaction_hash=transaction,log_index=0,recipient='SYNTHETIC_RECIPIENT',epoch_utc='2026-09-24',
        market_id='fixture',finalized=True)
    keys=[]
    for index,role in enumerate(('PROGRAM_STATEMENT','FINAL_TRANSFER')):
        p=dict(base,role=role)
        if index and changes:p.update(changes)
        key=prefix+str(index);keys.append(key)
        rewards.store.capture(key,event_id=rewards.key,kind='FEATURES',provider='fixture-'+str(index),source_identity=key,
            revision='1',payload=p,evidence_class=evidence_class)
    return dict(statement_id=keys[0],transfer_id=keys[1])


def test_matched_synthetic_income_stays_outside_trading_pnl_and_cash(rig):
    rewards,_=track(rig);ids=receipts(rewards)
    row=rewards.reconcile_synthetic_payment('receive',**ids)
    assert row['body']['details']['outcome']=='SYNTHETIC_RECEIPT_MATCHED'
    d=rewards.report('report')['body']['details']
    assert d['TRADING_PNL']['realized']=='0' and d['COMBINED_NET_RESULT']['realized']=='2'
    assert d['REWARD_REBATE_INCOME']['actual_independently_verified'] is None
    assert Decimal(d['hard_cash_unchanged'])==10 and d['estimated_rewards_in_cash']=='0'
    assert rig['coordinator']._head() is None


def test_same_transfer_cannot_be_counted_again_as_rebate_or_new_receipt(rig):
    rewards,_=track(rig);rewards.reconcile_synthetic_payment('first',**receipts(rewards))
    with pytest.raises(EvidenceError,match='ALREADY_RECONCILED'):
        rewards.reconcile_synthetic_payment('duplicate',**receipts(rewards,prefix='new',program='MAKER_REBATE'))


@pytest.mark.parametrize('changes',[{'amount':'3'},{'asset':'another'},{'finalized':False},{'transaction_hash':'b'*64}])
def test_payment_disagreement_or_unfinalized_transfer_is_not_income(rig,changes):
    rewards,_=track(rig)
    with pytest.raises(EvidenceError,match='PAYMENT_PROOF|MISMATCH'):
        rewards.reconcile_synthetic_payment('receive',**receipts(rewards,changes=changes))
    assert not rewards._state(rewards._head())['payments']


def test_public_capture_claim_is_not_independent_actual_payment_proof(rig):
    rewards,_=track(rig)
    with pytest.raises(EvidenceError,match='NO_LIVE_ATTESTOR'):
        rewards.reconcile_synthetic_payment('receive',**receipts(rewards,evidence_class='PUBLIC_OBSERVED'))


def test_different_assets_are_not_added_to_collateral_result(rig):
    rewards,_=track(rig);rewards.reconcile_synthetic_payment('receive',**receipts(rewards,asset='OTHER'))
    d=rewards.report('report')['body']['details']
    assert d['COMBINED_NET_RESULT']['realized']=='0' and d['COMBINED_NET_RESULT']['foreign_assets_excluded']==['OTHER']


def test_bounded_runtime_detects_reward_rule_change_and_retires(rig,monkeypatch):
    rt,mk=integrated(rig,monkeypatch);assert propose(rig)['outcome']=='OBSERVING_RESEARCH_QUOTE'
    raw(rig);rewards=MakerRewards(mk,policy(rig));rewards.track('track',quote_id='quote',parameters_id='rewards')
    rt=PaperRuntime(rt.coordinator,rt.queue,rt.health,rt.policy,evaluator=rt.evaluator,maker=mk,rewards=rewards)
    raw(rig,'changed',rewardsMaxSpread=1)
    d=rt.tick('reward-change')['body']['details']
    assert len(d['reward_report_ids'])==1,d
    assert mk._state(mk._head())['quote']['status']=='RETIRED'
    assert rig['coordinator']._head() is None


def test_reward_collection_uses_bounded_anonymous_get_only(rig):
    calls=[]
    def transport(req):
        calls.append(req)
        return httpx.Response(200,json=dict(id='123'))
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            return await PublicCollector(rig['store'],client).cycle('collect-reward',(
                reward_request(event_id='event',market_id='123',revision='now'),))
    d=asyncio.run(run())
    assert d['sources'][0]['state']=='SUCCESS' and len(calls)==1
    assert calls[0].method=='GET' and not calls[0].url.params and calls[0].url.path=='/markets/123'


@pytest.mark.parametrize('params',[(('id','123'),('limit','100')), (('id','123'),), (('limit','1'),)])
def test_reward_endpoint_cannot_become_an_unbounded_market_scan(params):
    with pytest.raises(EvidenceError,match='EXACT_PUBLIC_QUERY'):
        SourceRequest(PROVIDER,ENDPOINT+'/123','event','RULES','reward-market:123','now',params)
