from copy import deepcopy
from decimal import Decimal

import pytest

from polymarket_scanner.v11.evidence import EvidenceError
from polymarket_scanner.v11.performance import PerformanceLab, distribution
from test_v11_maker_research import rig, factory, setup, bundle
from test_v11_position_management import rig as exit_rig, inventory, reserved_exit, proof, coordinator


def recorded(rig, pnls=('6','-4','3')):
    """Explicit synthetic accounting fixture, no economic admission claim."""
    c=rig['coordinator'];s=c._state(None);at=rig['now'][0]
    for i,pnl in enumerate(pnls):
        key='entry'+str(i);event='e'+str(i)
        s['contexts'][event]=dict(station_id='A' if i<2 else 'B',city_id='city'+str(i))
        s['intents'][key]=dict(proposal_id=key,event_id=event,direction='BUY',units='1',filled_units='1',status='FILLED',
                              conservative_ev_total='.1',binding={'bundle_sha256':str(i)*64})
        amount=Decimal(pnl)
        s['realized_entries'].append(dict(fill_id='sale'+str(i),event_id=event,pnl=pnl,at=at+i,
            exit={'attribution':[{'strategy':'EXIT_ONLY','weight':'1'}]},allocations=[dict(lot_id='lot'+str(i),
                units='1',allocated_basis='10',net_proceeds=str(10+amount),realized_pnl=pnl,
                entry={'intent_id':key},strategy_realized_pnl=[dict(strategy='ENTRY',pnl=pnl)])]))
        s['event_realized_pnl'][event]=pnl
    return c,s


def save(c,s,key='fixture-state'):
    return c._commit(key,{'action':'EXPLICIT_SYNTHETIC_REPORT_FIXTURE'},c._head(),s,{})


def report(rig,c,start=None,end=None):
    return PerformanceLab(c).build(start=rig['now'][0] if start is None else start,
                                   end=rig['now'][0]+10 if end is None else end)


def test_pnl_statistics_and_exclusions_preserve_entry_attribution(rig):
    c,s=recorded(rig);head=save(c,s);d=report(rig,c)
    assert d['paper_realized_pnl']=='5' and d['pnl_by_strategy']=={'ENTRY':'5'}
    assert 'EXIT_ONLY' not in d['pnl_by_strategy'] and c._head()==head
    assert d['closed_entry_trade_statistics']['positive']==2 and d['closed_entry_trade_statistics']['negative']==1
    stats=d['realization_statistics'];assert stats['median']=='3' and stats['max_realized_only_drawdown']=='4'
    assert stats['profit_factor']=='2.25'
    assert d['exclusions']['largest_winner']=='-1' and d['exclusions']['top_three_winners']=='-4'
    assert d['exclusions']['best_station']=='2'
    assert d['validated_live_capital'] is d['live_realized_pnl'] is d['ev_capture_ratio'] is None
    assert d['resolved_settlement_trades'] is None and d['rewards_in_trading_pnl'] is False
    assert all(sum(Decimal(v) for v in x.values())==5 for x in d['pnl_slices'].values())


def test_partial_sale_is_not_a_closed_winning_trade(rig):
    c,s=recorded(rig,('6',));s['lots']['remaining']=dict(entry={'intent_id':'entry0'})
    save(c,s);d=report(rig,c)
    assert d['paper_realized_pnl']=='6' and d['closed_entry_trade_statistics']['count']==0


def test_cancel_requested_without_fill_is_not_a_confirmed_no_fill(rig):
    c,s=recorded(rig,())
    for key,status in [('pending','CANCEL_REQUESTED'),('ambiguous','UNKNOWN'),('done','CANCELED')]:
        s['intents'][key]=dict(event_id='e',direction='BUY',units='2',filled_units='0',status=status)
    save(c,s);d=report(rig,c)
    assert d['no_fills_at_snapshot']==1 and d['pending_intents_at_snapshot']==2


def test_half_open_window_does_not_turn_partial_history_into_complete_trade(rig):
    c,s=recorded(rig,('2',));first=s['realized_entries'][0];first['allocations'][0]['units']='.5'
    second=deepcopy(first);second.update(fill_id='sale-later',at=rig['now'][0]+5)
    s['realized_entries'].append(second);s['event_realized_pnl']['e0']='4'
    save(c,s);d=report(rig,c,end=rig['now'][0]+5)
    assert d['paper_realized_pnl']=='2' and d['closed_entry_trade_statistics']['count']==0
    assert report(rig,c)['closed_entry_trade_statistics']['count']==1


def test_closed_trade_drawdown_uses_closure_sequence_not_first_partial_exit(rig):
    c,s=recorded(rig,('4','-3'))
    first=s['realized_entries'][0];first['allocations'][0]['units']='.5'
    final=deepcopy(first);final.update(fill_id='sale-final',pnl='-8',at=rig['now'][0]+2)
    final['allocations'][0].update(realized_pnl='-8',net_proceeds='2',strategy_realized_pnl=[dict(strategy='ENTRY',pnl='-8')])
    s['realized_entries'].append(final);s['event_realized_pnl']['e0']='-4'
    save(c,s);d=report(rig,c)
    assert d['closed_entry_trade_statistics']['max_realized_only_drawdown']=='7'


@pytest.mark.parametrize('fault',['duplicate','allocation','attribution'])
def test_accounting_inconsistency_is_not_published_as_clean_statistics(rig,fault):
    c,s=recorded(rig,('6',))
    if fault=='duplicate':s['realized_entries'].append(deepcopy(s['realized_entries'][0]))
    elif fault=='allocation':s['realized_entries'][0]['allocations'][0]['realized_pnl']='7'
    else:s['realized_entries'][0]['allocations'][0]['strategy_realized_pnl'][0]['pnl']='7'
    save(c,s)
    with pytest.raises(EvidenceError,match='DUPLICATE|NONCONSERVATION'):report(rig,c)


def test_net_zero_unknown_lineage_still_blocks_winner_exclusions(rig):
    c,s=recorded(rig,('6','-6'))
    for row in s['realized_entries']:row['allocations'][0]['entry']=None
    save(c,s);d=report(rig,c)
    assert d['attribution']['unknown_entry_pnl']=='0' and d['attribution']['unknown_entry_pieces']==2
    assert d['exclusions']['largest_winner'] is None and d['exclusions']['top_three_winners'] is None


def test_unexplained_historical_pnl_is_a_reconciliation_gap(rig):
    c,s=recorded(rig,('1',));s['event_realized_pnl']['legacy']='3';save(c,s)
    d=report(rig,c)
    assert d['paper_realized_pnl']=='1' and d['reconciliation']['unexplained_gap']=='3'
    assert not d['reconciliation']['complete']


def test_joint_basket_ev_is_never_summed_per_leg(rig):
    c,s=recorded(rig,())
    for index in range(3):s['intents'][str(index)]=dict(event_id='e',direction='BUY',units='1',filled_units='0',status='RESERVED',joint_ev_only=True,conservative_ev_total='.5')
    s['baskets']={'basket':dict(conservative_ev_total='.5')};save(c,s)
    d=report(rig,c)
    assert not d['expected_ev']['individual'] and d['expected_ev']['joint_baskets']=={'basket':'.5'}


def test_report_namespace_cannot_mix_another_ledger(rig):
    c,s=recorded(rig);s['execution_namespace']='CHALLENGER:other';save(c,s)
    with pytest.raises(EvidenceError,match='NAMESPACE'):report(rig,c)


def test_empty_and_no_loss_cohorts_do_not_emit_nonfinite_ratios():
    assert distribution([])['profit_factor'] is None
    d=distribution([Decimal('1')]);assert d['profit_factor'] is None and d['profit_factor_unbounded']


def test_real_account_partial_exit_lineage_flows_into_report(exit_rig):
    rig=exit_rig;inventory(rig);p,_=reserved_exit(rig);c=coordinator(rig)
    c.transition('submit',intent_id=p.proposal_id,status='SUBMITTING')
    key=proof(rig,p.proposal_id,'actual-paper-sale','PAPER_FILL',fill_id='actual-paper-sale',units='.5',all_in_collateral='.05',direction='SELL')
    head=c.record_fill('record-sale',key)
    d=PerformanceLab(c).build(start=rig['now'][0]-1,end=rig['now'][0]+1)
    assert Decimal(d['paper_realized_pnl'])==Decimal('-.05') and d['closed_entry_trade_statistics']['count']==0
    assert sum(Decimal(v) for v in d['pnl_by_strategy'].values())==Decimal('-.05')
    assert d['expected_ev']['joint_baskets'] and d['ev_capture_ratio'] is None
    assert c._head()==head
