"""Account-derived PAPER performance with conserved entry attribution.

Reports do not promote models, infer fills, validate capital or mark inventory to
a midpoint. Exit fragments, flat entry intents and settlement are distinct units.
"""
from collections import defaultdict
from dataclasses import asdict
from decimal import Decimal, ROUND_FLOOR
import time

from .evidence import EvidenceError, canonical, finite, sha
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

    def replay_temperature(self, evaluation_id, *, policy, **options):
        """Original model/receipts through shared economics and account context."""
        from .causal_replay import replay_temperature
        return replay_temperature(self.coordinator, evaluation_id, policy=policy, **options)

    def replay_account_command(self, command_id, *, policy, **options):
        """Read-only conditional numeric effects; no account command invocation."""
        from .account_replay import replay_account_command
        return replay_account_command(self.coordinator, command_id, policy=policy, **options)

    def scoped_fill_markouts(self, **request):
        """Pinned reconciled PAPER fills and hypothetical horizon depth, read-only."""
        from .fill_markout import measure_fill_window
        return measure_fill_window(self.coordinator,**request)

    def execution_costs(self, *, start, end, policy, account_row=_CURRENT, **options):
        """Separately attributed conserved receipt costs; no second P&L debit."""
        from .execution_costs import measure_execution_costs
        if account_row is _CURRENT: account_row = self.coordinator._head()
        return measure_execution_costs(self.coordinator, start=start, end=end,
            account_row=account_row, policy=policy, **options)

    @precise
    def scoped_realized(self, *, scope, bundle_sha256, start, end, account_ref, monotonic=time.monotonic):
        """All realized fragments for one original admission scope in a pinned account.

        This reports realized-only PAPER loss, with actual ledger entry attribution.
        It does not estimate open inventory value, final settlement, EV capture or
        independent sample size. Unknown or inconsistent lineage gates the cohort.
        """
        from .certification import CapabilityScope
        from .learning_sources import learning_source_view
        from .position_attribution import intent_lineage
        from .rules import RuleFingerprint
        from .strategy_admission import VERSION as ADMISSION_VERSION
        if not isinstance(scope,CapabilityScope) or self.store.namespace!='V11_PAPER':
            raise EvidenceError('PERFORMANCE_SCOPED_PAPER_REQUIRED')
        sha(bundle_sha256); start,end=finite(start),finite(end)
        if (start>=end or end>self.store.clock() or not isinstance(account_ref,dict) or set(account_ref)!={'id','sha256','seq'}
                or type(account_ref['seq']) is not int):
            raise EvidenceError('PERFORMANCE_SCOPED_WINDOW_OR_SNAPSHOT')
        deadline=monotonic()+2.; rows=[]; cache={}; events=set(); days=set(); fill_totals={}
        with learning_source_view(self.store,deadline=deadline,monotonic=monotonic) as view:
            account=view.get(account_ref['id'])
            if (dict(id=account['id'],sha256=account['sha256'],seq=account['seq'])!=account_ref
                    or account['body']['recorded_at']>end):
                raise EvidenceError('PERFORMANCE_ACCOUNT_SNAPSHOT_BINDING')
            report=self.build(start=start,end=end,account_row=account)
            state=self.coordinator._state(account)
            if (state.get('financial_authority') is not False or report['faults'] or not report['reconciliation']['complete']
                    or report['attribution']['unknown_entry_pieces']):
                raise EvidenceError('PERFORMANCE_SCOPED_RECONCILIATION_REQUIRED')
            by_event=defaultdict(Decimal)
            for item in state['realized_entries']:by_event[item['event_id']]+=number(item['pnl'],signed=True)
            if any(by_event[e]!=number(state['event_realized_pnl'].get(e,'0'),signed=True)
                   for e in set(by_event)|state['event_realized_pnl'].keys()):
                raise EvidenceError('PERFORMANCE_SCOPED_EVENT_RECONCILIATION')

            def proof(fill_id,intent,direction):
                row=view.by_hash(kind='TRADE',event_id=intent['event_id'],sha256=state['fills'][fill_id]);b=row['body'];p=b['payload']
                if (row['seq']>=account['seq'] or b.get('evidence_class')!='SYNTHETIC'
                        or p.get('record_type')!='PAPER_FILL' or p.get('account_id')!=self.coordinator.policy.account_id
                        or p.get('execution_namespace')!=self.store.namespace or p.get('fill_id')!=fill_id
                        or p.get('intent_id')!=intent['proposal_id'] or p.get('token_id')!=intent['token_id']
                        or p.get('direction')!=direction or number(p['units'])<=0):
                    raise EvidenceError('PERFORMANCE_SCOPED_FILL_PROOF')
                return row

            def admissions(intent):
                key=intent['proposal_id']
                if key in cache:return cache[key]
                if not 1<=len(intent['admission_ids'])<=4:raise EvidenceError('PERFORMANCE_SCOPED_ADMISSIONS_BOUND')
                context=state['contexts'][intent['event_id']];rule=RuleFingerprint(**state['rules'][intent['event_id']])
                result={}
                for ref in intent['admission_ids']:
                    row=view.get(ref);d=row['body']['details'];r=d['request'];a=d['assessment'];s=CapabilityScope(**r['scope'])
                    if (row['kind']!='REGISTRY' or d.get('version')!=ADMISSION_VERSION or row['seq']>=account['seq']
                            or r['context']!=context or r['rule']!=asdict(rule) or r['binding']!=intent['binding']
                            or context['account_id']!=self.coordinator.policy.account_id or r['stage']!='PAPER'
                            or context['station_id']!=s.station or rule.payload['station']!=s.station
                            or rule.payload['source_family']!=s.source_rule_family
                            or {'daily_high_temperature':'HIGH','daily_low_temperature':'LOW'}.get(rule.payload['family'])!=s.family
                            or a['model_bundle_sha256']!=intent['binding']['bundle_sha256'] or s.strategy in result):
                        raise EvidenceError('PERFORMANCE_ORIGINAL_ADMISSION_SCOPE')
                    result[s.strategy]=(row,s,a)
                if set(result)!={a['strategy'] for a in intent['attribution']}:
                    raise EvidenceError('PERFORMANCE_ATTRIBUTION_ADMISSION_MISMATCH')
                cache[key]=(result,rule,context)
                return cache[key]

            last_at=-1.
            for realization in state['realized_entries']:
                view.check();at=finite(realization['at'])
                if at<last_at or at>account['body']['recorded_at']:
                    raise EvidenceError('PERFORMANCE_REALIZATION_CHRONOLOGY')
                last_at=at
                if not start<=at<end:continue
                exit_intent=state['intents'][realization['exit']['intent_id']]
                if (exit_intent['direction']!='SELL' or realization['exit']!=intent_lineage(view,exit_intent)
                        or realization['event_id']!=exit_intent['event_id']):
                    raise EvidenceError('PERFORMANCE_EXIT_LINEAGE')
                sale=proof(realization['fill_id'],exit_intent,'SELL');payload=sale['body']['payload'];pieces=realization['allocations']
                if (sale['body']['available_at']>at or view.get(exit_intent['valuation_id'])['seq']>=sale['seq']
                        or sum((number(a['units']) for a in pieces),Decimal(0))!=number(payload['units'])
                        or sum((number(a['net_proceeds']) for a in pieces),Decimal(0))!=number(payload['all_in_collateral'])):
                    raise EvidenceError('PERFORMANCE_EXIT_FILL_ALLOCATION')
                for piece in pieces:
                    view.check();intent=state['intents'][piece['entry']['intent_id']]
                    if (intent['direction']!='BUY' or intent['event_id']!=realization['event_id']
                            or intent['token_id']!=exit_intent['token_id'] or piece['entry']!=intent_lineage(view,intent)
                            or piece.get('entry_lineage_status')!='RECORDED' or piece.get('acquisition_order')!='RECEIPT_SEQUENCE_FIFO'):
                        raise EvidenceError('PERFORMANCE_ENTRY_LINEAGE')
                    buy=proof(piece['lot_id'],intent,'BUY');original,rule,context=admissions(intent)
                    if (buy['seq']>=sale['seq'] or view.get(intent['valuation_id'])['seq']>=buy['seq']
                            or number(piece['units'])>number(buy['body']['payload']['units'])
                            or any(row['seq']>=buy['seq'] for row,_,_ in original.values())):
                        raise EvidenceError('PERFORMANCE_ENTRY_FILL_CHRONOLOGY')
                    amount=number(piece['realized_pnl'],signed=True)
                    if number(piece['net_proceeds'])-number(piece['allocated_basis'])!=amount:
                        raise EvidenceError('PERFORMANCE_ALLOCATION_BASIS')
                    weights=intent['attribution'];remaining=amount;expected=[]
                    if len({w['strategy'] for w in weights})!=len(weights) or sum((number(w['weight']) for w in weights),Decimal(0))!=1:
                        raise EvidenceError('PERFORMANCE_ENTRY_ATTRIBUTION_WEIGHTS')
                    for index,weight in enumerate(weights):
                        share=remaining if index==len(weights)-1 else (amount*number(weight['weight'])).quantize(Decimal('1e-18'),rounding=ROUND_FLOOR)
                        remaining-=share;expected.append(dict(strategy=weight['strategy'],pnl=str(share)))
                    if canonical(expected)!=canonical(piece['strategy_realized_pnl']):
                        raise EvidenceError('PERFORMANCE_REALIZED_ATTRIBUTION_MISMATCH')
                    for part in expected:
                        admission,entry_scope,assessment=original[part['strategy']]
                        if entry_scope!=scope or intent['binding']['bundle_sha256']!=bundle_sha256:continue
                        city_day=context['city_id']+':'+rule.payload['target_date'];events.add(intent['event_id']);days.add(city_day)
                        fill_totals.setdefault(realization['fill_id'],Decimal(0));fill_totals[realization['fill_id']]+=number(part['pnl'],signed=True)
                        rows.append(dict(fill_id=realization['fill_id'],entry_intent_id=intent['proposal_id'],
                            event_id=intent['event_id'],city_day=city_day,at=at,pnl=part['pnl'],
                            entry_fill_ref=dict(id=buy['id'],sha256=buy['sha256']),exit_fill_ref=dict(id=sale['id'],sha256=sale['sha256']),
                            admission_ref=dict(id=admission['id'],sha256=admission['sha256']),
                            model_state_sha256=assessment['model_state_sha256']))
            view.check();stats=distribution(list(fill_totals.values()))
        result=dict(account_ref=account_ref,rows=rows,realization_statistics=stats,n_realizations=len(fill_totals),
            n_events=len(events),n_city_days=len(days),all_account_window_pnl=report['paper_realized_pnl'],
            other_known_scope_pnl=str(number(report['paper_realized_pnl'],signed=True)-number(stats['total'],signed=True)),
            remaining_inventory_lots=len(state['lots']),reconciliation=report['reconciliation'],
            mark_to_market_drawdown=None,net_ev_capture=None,live_pnl=None,financial_authority=False,
            independent_acceptance=False,evidence_class='SYNTHETIC',entry_attribution_preserved=True)
        if len(canonical(result).encode())>512*1024:raise EvidenceError('PERFORMANCE_SCOPED_RESULT_BOUND')
        return result

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
    def build(self, *, start, end, account_row=_CURRENT, maximum_metadata_seconds=1., execution_policy=None):
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
        execution = (self.execution_costs(start=start, end=end, policy=execution_policy, account_row=account_row)
                     if execution_policy is not None else None)
        return dict(version=VERSION, execution_namespace=self.store.namespace, account_id=c.policy.account_id,
            **({'execution_costs':execution} if execution is not None else {}),
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
