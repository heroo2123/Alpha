"""Account-derived PAPER performance with conserved entry attribution.

Reports do not promote models, infer fills, validate capital or mark inventory to
a midpoint. Exit fragments, flat entry intents and settlement are distinct units.
"""
from collections import defaultdict
from decimal import Decimal
import time

from .evidence import EvidenceError, finite
from .paper_coordinator import ACCOUNT_KEY, TERMINAL, VERSION as ACCOUNT_VERSION
from .scenario_risk import number, precise


VERSION = 'alpha_v11_performance_v1'
UNKNOWN = 'UNKNOWN'
_CURRENT = object()
DIMENSIONS = ('station','city','entry_price','event_state','model_bundle','horizon','model_confidence','market_liquidity')


def _add(groups, key, amount):
    groups[key] = groups.get(key, Decimal(0))+amount


@precise
def distribution(values):
    ordered = sorted(values); n = len(values); total = sum(values, Decimal(0))
    wins = sum((v for v in values if v > 0), Decimal(0))
    losses = -sum((v for v in values if v < 0), Decimal(0))
    equity = peak = drawdown = Decimal(0)
    for value in values:
        equity += value; peak = max(peak,equity); drawdown = max(drawdown,peak-equity)
    median = None if not n else ordered[n//2] if n%2 else (ordered[n//2-1]+ordered[n//2])/2
    return dict(count=n, total=str(total), positive=sum(v>0 for v in values), negative=sum(v<0 for v in values),
        flat=sum(v==0 for v in values), average=str(total/n) if n else None,
        median=str(median) if median is not None else None,
        profit_factor=str(wins/losses) if losses else None, profit_factor_unbounded=bool(wins and not losses),
        max_realized_only_drawdown=str(drawdown), mark_to_market_drawdown=None,
        worst_realization=str(ordered[0]) if n else None,
        worst_three_losses_total=str(sum((v for v in ordered[:3] if v<0),Decimal(0))))


class PerformanceLab:
    def __init__(self, coordinator):
        self.coordinator, self.store = coordinator, coordinator.store

    def _metadata(self, state, intent_id, deadline, cache):
        if intent_id in cache: return cache[intent_id]
        intent = state['intents'].get(intent_id,{})
        context = state['contexts'].get(intent.get('event_id'),{})
        result = dict.fromkeys(DIMENSIONS, UNKNOWN)
        result.update(station=context.get('station_id',UNKNOWN), city=context.get('city_id',UNKNOWN),
                      entry_price=intent.get('limit_price',UNKNOWN),
                      model_bundle=intent.get('binding',{}).get('bundle_sha256',UNKNOWN))
        if time.monotonic() >= deadline:
            result['metadata_status'] = 'METADATA_BUDGET_EXHAUSTED'; cache[intent_id] = result; return result
        try:
            if intent.get('event_state_id'):
                result['event_state'] = self.store.get(intent['event_state_id'])['body']['details'].get('state',UNKNOWN)
            if intent.get('admission_ids'):
                scopes = [self.store.get(k)['body']['details']['request']['scope'] for k in intent['admission_ids'][:8]]
                result['horizon'] = '|'.join(sorted({s['horizon'] for s in scopes}))
            if intent.get('valuation_id'):
                value = self.store.get(intent['valuation_id'])['body']['details']
                result['model_confidence'] = value.get('model',{}).get('prediction',{}).get('calibration_status',UNKNOWN)
                result['market_liquidity'] = value.get('book',{}).get('reason',UNKNOWN)
            result['metadata_status'] = 'PINNED_ENTRY_RECORDS_ONLY_NOT_CURRENT_CHAMPION'
        except (EvidenceError,KeyError,TypeError): result['metadata_status'] = 'INCOMPLETE_PINNED_METADATA'
        cache[intent_id] = result
        return result

    @precise
    def build(self, *, start, end, account_row=_CURRENT, maximum_metadata_seconds=1.):
        start,end = finite(start),finite(end)
        if start >= end or not 0 < finite(maximum_metadata_seconds) <= 3:
            raise EvidenceError('PERFORMANCE_WINDOW_OR_BUDGET_BOUND')
        c = self.coordinator
        if account_row is _CURRENT: account_row = c._head()
        if account_row is not None:
            d = account_row['body'].get('details',{})
            if (account_row['kind'] != 'COORDINATOR_EVENT' or account_row['event_id'] != ACCOUNT_KEY
                    or d.get('version') != ACCOUNT_VERSION or d.get('policy_sha256') != c.policy_sha):
                raise EvidenceError('PERFORMANCE_ACCOUNT_IDENTITY')
        state = c._state(account_row)
        if state['execution_namespace'] != self.store.namespace or state['account_id'] != c.policy.account_id:
            raise EvidenceError('PERFORMANCE_NAMESPACE_ACCOUNT_MISMATCH')
        if len(state['realized_entries']) > 2048 or len(state['intents']) > 512:
            raise EvidenceError('PERFORMANCE_ACCOUNT_SIZE_BOUND')
        entries = [e for e in state['realized_entries'] if start <= finite(e['at']) < end]
        if len({e['fill_id'] for e in state['realized_entries']}) != len(state['realized_entries']):
            raise EvidenceError('PERFORMANCE_DUPLICATE_REALIZATION')
        dims = {d:{} for d in DIMENSIONS}; strategy = {}; entry_pnl = {}; sold = {}; cache = {}; unknown = Decimal(0)
        deadline = time.monotonic()+maximum_metadata_seconds; count = 0; unknown_count = 0
        for entry in entries:
            pnl = number(entry['pnl'],signed=True); allocations = entry.get('allocations',[])
            if not allocations:
                unknown_count += 1
                unknown += pnl; _add(strategy,UNKNOWN,pnl)
                for dimension in DIMENSIONS: _add(dims[dimension],UNKNOWN,pnl)
                continue
            if sum((number(a['realized_pnl'],signed=True) for a in allocations),Decimal(0)) != pnl:
                raise EvidenceError('PERFORMANCE_ENTRY_ALLOCATION_NONCONSERVATION')
            for allocation in allocations:
                count += 1
                if count > 8192: raise EvidenceError('PERFORMANCE_ALLOCATION_SIZE_BOUND')
                amount = number(allocation['realized_pnl'],signed=True)
                entry_info = allocation.get('entry') or {}; intent_id = entry_info.get('intent_id',UNKNOWN)
                if intent_id == UNKNOWN: unknown += amount; unknown_count += 1
                _add(entry_pnl,intent_id,amount); _add(sold,intent_id,number(allocation['units']))
                split = allocation.get('strategy_realized_pnl')
                if split is None: _add(strategy,UNKNOWN,amount)
                else:
                    if sum((number(s['pnl'],signed=True) for s in split),Decimal(0)) != amount:
                        raise EvidenceError('PERFORMANCE_STRATEGY_ATTRIBUTION_NONCONSERVATION')
                    for part in split: _add(strategy,part['strategy'],number(part['pnl'],signed=True))
                metadata = dict(self._metadata(state,intent_id,deadline,cache))
                quantity = number(allocation['units'])
                if quantity <= 0: raise EvidenceError('PERFORMANCE_ALLOCATION_UNITS')
                metadata['entry_price'] = str(number(allocation['allocated_basis'])/quantity)
                for dimension in DIMENSIONS: _add(dims[dimension],str(metadata[dimension]),amount)
        values = [number(e['pnl'],signed=True) for e in entries]; total = sum(values,Decimal(0))
        for parts in (strategy,*dims.values()):
            if sum(parts.values(),Decimal(0)) != total: raise EvidenceError('PERFORMANCE_SLICE_NONCONSERVATION')
        # Window fragments cannot prove complete lifecycle closure. Closure below
        # therefore also requires all of that entry's realizations in this window.
        all_sold = defaultdict(Decimal); all_entry_pnl = defaultdict(Decimal); closing_sequence = {}
        for ordinal,entry in enumerate(state['realized_entries']):
            for allocation in entry.get('allocations',[]):
                key = (allocation.get('entry') or {}).get('intent_id',UNKNOWN)
                all_sold[key] += number(allocation['units'])
                all_entry_pnl[key] += number(allocation['realized_pnl'],signed=True)
                closing_sequence[key] = ordinal
        open_entries = {(lot.get('entry') or {}).get('intent_id',UNKNOWN) for lot in state['lots'].values()}
        closed = []
        for key,pnl in entry_pnl.items():
            intent = state['intents'].get(key,{})
            if (key != UNKNOWN and intent.get('direction') == 'BUY' and intent.get('status') in TERMINAL
                    and key not in open_entries and number(intent.get('filled_units','0')) > 0
                    and sold[key] == all_sold[key] == number(intent['filled_units'])):
                closed.append((closing_sequence[key],key,all_entry_pnl[key]))
        no_fills = sum(p['status'] in TERMINAL and p['status'] != 'FILLED' and number(p['filled_units']) == 0 for p in state['intents'].values())
        partials = sum(0 < number(p['filled_units']) < number(p['units']) for p in state['intents'].values())
        winners = sorted((p for k,p in entry_pnl.items() if k != UNKNOWN and p>0),reverse=True)
        known_stations = [p for station,p in dims['station'].items() if station != UNKNOWN and p>0]
        whole_total = sum((number(v,signed=True) for v in state['event_realized_pnl'].values()),Decimal(0))
        all_entry_total = sum((number(e['pnl'],signed=True) for e in state['realized_entries']),Decimal(0))
        expected = {}; joint = {}
        for key,basket in state.get('baskets',{}).items(): joint[key] = basket.get('conservative_ev_total')
        for key,intent in state['intents'].items():
            if intent.get('joint_ev_only'): continue
            expected[key] = dict(amount=intent.get('conservative_ev_total'), direction=intent['direction'],
                                comparison_to_realized='UNMATCHED_HORIZON_NOT_EV_CAPTURE')
        return dict(version=VERSION, execution_namespace=self.store.namespace, account_id=c.policy.account_id,
            window=dict(start_inclusive=start,end_exclusive=end),
            account_head_id=account_row['id'] if account_row else None,
            account_snapshot_at=account_row['body']['recorded_at'] if account_row else None,
            realization_statistics=distribution(values), closed_entry_trade_statistics=distribution([p for _,_,p in sorted(closed)]),
            resolved_settlement_trades=None, settlement_status='NO_INDEPENDENT_FINAL_SETTLEMENT_LABELS_IN_ACCOUNT',
            no_fills_at_snapshot=no_fills, partial_fills_at_snapshot=partials,
            pending_intents_at_snapshot=sum(p['status'] not in TERMINAL for p in state['intents'].values()),
            paper_realized_pnl=str(total), live_realized_pnl=None, validated_live_capital=None,
            hypothetical_initial_capital=c.policy.initial_hypothetical_cash,
            paper_roi_on_initial_hypothetical_capital=str(total/number(c.policy.initial_hypothetical_cash)), live_roi=None,
            fees=None, slippage=None, cost_status='ALL_IN_LEDGER_COSTS_NOT_SEPARATELY_IDENTIFIED',
            expected_ev=dict(individual=expected,joint_baskets=joint), realized_ev=None, ev_capture_ratio=None,
            markout=None, calibration=None, quality_status='SEPARATE_TARGET_ALIGNED_EVIDENCE_REQUIRED',
            pnl_by_strategy={k:str(v) for k,v in sorted(strategy.items())},
            pnl_slices={d:{k:str(v) for k,v in sorted(parts.items())} for d,parts in dims.items()},
            exclusions=dict(unit='ENTRY_INTENT_REALIZATIONS_IN_WINDOW_INCLUDING_PARTIAL_EXITS',
                largest_winner=None if unknown_count else str(total-(winners[0] if winners else 0)),
                top_three_winners=None if unknown_count else str(total-sum(winners[:3],Decimal(0))),
                best_station=None if UNKNOWN in dims['station'] else str(total-max(known_stations,default=Decimal(0)))),
            current_account_values=dict(cash=state['cash'], claimable=state['claimable_collateral'], redeemed=state['redeemed_collateral']),
            attribution=dict(unknown_entry_pnl=str(unknown), unknown_entry_pieces=unknown_count, slice_totals_conserved=True,
                exit_attribution_not_counted_twice=True, entry_price_basis='ALLOCATED_ALL_IN_BASIS_PER_SHARE',
                metadata_statuses=sorted({m['metadata_status'] for m in cache.values()})),
            reconciliation=dict(all_time_event_pnl=str(whole_total), all_time_entry_pnl=str(all_entry_total),
                unexplained_gap=str(whole_total-all_entry_total), complete=whole_total==all_entry_total),
            faults=list(state['faults']), financial_authority=False, independent_acceptance=False,
            rewards_in_trading_pnl=False, current_champion_authority='UNVERIFIED_ACCOUNT_ENTRY_PINS_ARE_HISTORICAL')
