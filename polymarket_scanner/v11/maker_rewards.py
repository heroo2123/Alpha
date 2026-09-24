"""Nonfinancial maker reward diagnostics, rule invalidation and separate income.

Current research quotes are not exchange orders. Synthetic two-record payment
reconciliation exercises accounting only; there is no production payment attestor.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal

from .evidence import EvidenceError, canonical, digest, finite, identity, sha
from .maker_research import _heads, _restore
from .paper_coordinator import ACCOUNT_KEY
from .reward_rules import PROVIDER, RECIPE, RewardPolicy, liquidity_score, parameters, pursuit_gate
from .scenario_risk import number, precise


VERSION = 'alpha_v11_maker_rewards_v1'
PAYMENT_VERSION = 'alpha_v11_synthetic_reward_receipt_v1'


class MakerRewards:
    def __init__(self, research, policy):
        if not isinstance(policy, RewardPolicy): raise EvidenceError('REWARD_POLICY_REQUIRED')
        self.research, self.store, self.policy = research, research.store, policy
        self.key = 'maker-rewards:'+digest([self.store.namespace, research.coordinator.policy.account_id])
        self.config = digest(dict(policy=asdict(policy), research=research.policy_sha, recipe=RECIPE))

    def _head(self):
        row = self.store.latest(kind='MEASUREMENT', event_id=self.key)
        if row and row['body']['details'].get('config_sha256') != self.config:
            raise EvidenceError('REWARD_POLICY_CHANGED_REVIEW_REQUIRED')
        return row

    def _state(self, row):
        return deepcopy(row['body']['details']['state']) if row else dict(tracked={}, payments={}, last_quote='')

    def _replay(self, key, request):
        identity(key, maximum=100)
        try: row = self.store.get(key)
        except EvidenceError as exc:
            if str(exc) == 'EVIDENCE_MISSING': return None
            raise
        d = row['body'].get('details', {})
        if (row['event_id'] != self.key or row['kind'] != 'MEASUREMENT' or d.get('version') != VERSION
                or d.get('config_sha256') != self.config or canonical(d.get('request')) != canonical(request)):
            raise EvidenceError('REWARD_REPLAY_CONFLICT')
        return row

    def _save(self, key, request, head, state, *, refs=(), heads=(), **details):
        return self.store.audit(key, event_id=self.key, kind='MEASUREMENT', details=dict(
            version=VERSION, config_sha256=self.config, request=request, state=state, **details,
            financial_authority=False, orders_submitted=False, account_ledger_mutated=False,
            estimates_count_as_cash=False, rewards_in_trading_alpha=False), evidence_ids=tuple(dict.fromkeys(refs)),
            expected_previous_seq=head['seq'] if head else 0, expected_heads=_heads(heads))

    def _current(self, q):
        quote = _restore(q['request'])
        return self.store.latest_source(kind='RULES', event_id=quote.context.event_id,
                     provider=PROVIDER, source_identity='reward-market:'+quote.market_id)

    @precise
    def _assessment(self, quote_id, raw_id):
        maker_head = self.research._head(); q = self.research._state(maker_head).get(quote_id)
        if not q: raise EvidenceError('REWARD_MANAGED_RESEARCH_QUOTE_REQUIRED')
        quote = _restore(q['request']); now = finite(self.store.clock())
        p = parameters(self.store, raw_id, rule=quote.rule, market_id=quote.market_id, policy=self.policy)
        refs = [maker_head['id'], raw_id]; heads = [('MEASUREMENT', self.research.key, maker_head['seq'])]
        rules_head = self.store.latest(kind='RULES', event_id=quote.context.event_id)
        heads.append(('RULES', quote.context.event_id, rules_head['seq']))
        result = dict(quote_id=quote_id, quote_origin_id=q['origin_record_id'], market_id=quote.market_id,
            condition_id=p['condition_id'], epoch_utc=datetime.fromtimestamp(now, timezone.utc).date().isoformat(),
            parameters=p, trading_ev_excluding_rewards=None, trading_ev_status='UNVALIDATED_MAKER_EXECUTION_ECONOMICS',
            actual_received_reward=None, actual_received_rebate=None, estimate_actual_discrepancy=None,
            official_qualification='UNKNOWN_NOT_AN_EXCHANGE_ORDER', conditional_score=None,
            reward_ranges_by_asset={}, maker_rebate_if_fully_filled=None, expected_maker_rebate=None,
            expected_fill_probability=None, reward_share_range=['0','1'],
            outcome='UNKNOWN', reason='RESEARCH_QUOTE_NOT_RECEIVED_REWARD',
            pursuit=pursuit_gate(None, '0'), as_of=now, valid_until=None)
        if q['status'] != 'OBSERVING' or now >= q['expires_at']:
            result['reason'] = 'REWARD_QUOTE_RETIRED_OR_EXPIRED'; return result, refs, heads
        if not 0 <= now-self.policy.methodology_checked_at <= self.policy.maximum_methodology_age_seconds:
            result['reason'] = 'REWARD_PROGRAM_REVIEW_STALE'; return result, refs, heads
        if p['active'] is not True or p['closed'] is not False or p['accepting_orders'] is not True:
            result['reason'] = 'REWARD_MARKET_NOT_KNOWN_OPEN'; return result, refs, heads
        event, admission, expiry, guards = self.research._admission(quote, now); heads.extend(guards)
        features, book, book_policy, guard = self.research._feature(q['last_microstructure_id'], quote, now)
        heads.append(guard); refs.extend((book['id'], q['last_microstructure_id']))
        result['valid_until'] = min(expiry, p['valid_until'],
            self.policy.methodology_checked_at+self.policy.maximum_methodology_age_seconds,
            features['as_of']+self.research.policy.maximum_feature_age_seconds,
            book['body']['observed_at']+book_policy.maximum_book_age_seconds)
        if p['fees'] is not None:
            f = p['fees']; price = number(quote.limit_price)
            # This is conditional on a complete maker fill, never an expected
            # rebate. No fill probability or pool participation is invented.
            fee_equivalent = number(quote.units)*number(f['rate'])*(price*(1-price))**f['exponent']
            result['maker_rebate_if_fully_filled'] = dict(fee_equivalent=str(fee_equivalent),
                proportional_rebate=str(fee_equivalent*number(f['rebate_rate'])),
                fraction=f['rebate_rate'], maker_fee_zero=f['taker_only'],
                payment_confirmed=False, classification='UNROUNDED_CONDITIONAL_NOT_EXPECTED_OR_RECEIVED')
        if p['liquidity_status'] != 'PARAMETERS_PRESENT':
            result['reason'] = 'REWARD_PARAMETERS_INCOMPLETE'; return result, refs, heads
        pools = {}; boundary = False
        for allocation in p['allocations']:
            # Calendar end-date inclusivity is not specified by this adapter.
            # Do not grant the final day's pool without precise schedule proof.
            if allocation['end'] is not None and allocation['end'] <= now < allocation['end']+86400:
                boundary = True; continue
            if now < allocation['start'] or allocation['end'] is not None and now >= allocation['end']: continue
            amount = min(number(allocation['amount']), number(allocation['daily_rate']))
            pools[allocation['asset']] = pools.get(allocation['asset'], Decimal(0))+amount
        if boundary:
            result['reason'] = 'REWARD_END_DATE_BOUNDARY_UNVERIFIED'; return result, refs, heads
        midpoint = number(features['features']['midpoint'])
        if quote.side == 'NO': midpoint = 1-midpoint
        result['conditional_score'] = liquidity_score((dict(quote_id=quote_id, side=quote.side,
                direction=quote.direction, price=quote.limit_price, units=quote.units),),
                midpoint=str(midpoint), minimum_size=p['minimum_size'], maximum_distance=p['maximum_distance'])
        result['conditional_score']['reference_class'] = 'LOCAL_BOOK_MIDPOINT_NOT_OFFICIAL_SIZE_ADJUSTED_REFERENCE'
        result['conditional_score']['aggregation'] = 'ISOLATED_QUOTE_DO_NOT_SUM_MINIMUM_SCORES'
        result['reward_ranges_by_asset'] = {asset:dict(lower='0', upper=str(value),
            classification='WHOLE_MARKET_DAILY_CAP_NOT_AN_EXPECTED_SHARE') for asset,value in sorted(pools.items())}
        result.update(outcome='MEASURED_RESEARCH_ONLY', reason='VENUE_REFERENCE_COMPETITION_AND_EPOCH_SAMPLING_UNKNOWN')
        return result, refs, heads

    def track(self, key, *, quote_id, parameters_id):
        request = dict(action='TRACK', quote_id=identity(quote_id), parameters_id=identity(parameters_id))
        prior = self._replay(key, request)
        if prior: return prior
        head = self._head(); state = self._state(head)
        if quote_id in state['tracked']: raise EvidenceError('REWARD_QUOTE_ALREADY_TRACKED')
        if len(state['tracked']) >= 128: raise EvidenceError('REWARD_RETENTION_BOUND_NO_PRUNING')
        result, refs, heads = self._assessment(quote_id, parameters_id)
        state['tracked'][quote_id] = dict(parameters_sha256=result['parameters']['parameters_sha256'],
            quote_origin_id=result['quote_origin_id'], latest_assessment=result, retired=False)
        return self._save(key, request, head, state, refs=refs, heads=heads, outcome=result['outcome'])

    def refresh(self, key):
        request = dict(action='REFRESH'); prior = self._replay(key, request)
        if prior: return prior
        head = self._head(); state = self._state(head); results = []; refs = []
        keys = sorted(state['tracked']); keys = [q for q in keys if q > state['last_quote']]+[q for q in keys if q <= state['last_quote']]
        heads = []
        for quote_id in keys[:self.policy.maximum_refresh_quotes]:
            item = state['tracked'][quote_id]; state['last_quote'] = quote_id
            if item['retired']: continue
            q = self.research._state(self.research._head()).get(quote_id)
            if not q or q['origin_record_id'] != item['quote_origin_id']: raise EvidenceError('REWARD_QUOTE_IDENTITY_CHANGED')
            if q['status'] != 'OBSERVING': item['retired'] = True; continue
            try:
                current = self._current(q)
                if current is None: raise EvidenceError('REWARD_PARAMETERS_MISSING')
                result, evidence, guards = self._assessment(quote_id, current['id'])
                if result['parameters']['parameters_sha256'] != item['parameters_sha256']:
                    raise EvidenceError('REWARD_RULES_CHANGED_RECOMPUTE_NEW_QUOTE')
                if result['outcome'] != 'MEASURED_RESEARCH_ONLY': raise EvidenceError(result['reason'])
                item['latest_assessment'] = result; refs.extend(evidence); heads.extend(guards)
                results.append(dict(quote_id=quote_id, outcome='RECOMPUTED'))
            except EvidenceError as exc:
                row = self.research.retire('reward-retire:'+digest([self.config,quote_id,item['quote_origin_id']]),
                                         quote_id=quote_id, reason=str(exc))
                refs.append(row['id']); item['retired'] = True
                results.append(dict(quote_id=quote_id, outcome='RETIRED_NO_ORDER', reason=str(exc)))
                # Retirement advances the shared maker head; rebuild that CAS
                # below. No opening or cash movement can happen in this method.
        maker_head = self.research._head()
        heads = [h for h in heads if h[:2] != ('MEASUREMENT', self.research.key)]
        if maker_head: heads.append(('MEASUREMENT', self.research.key, maker_head['seq']))
        return self._save(key, request, head, state, refs=refs, heads=heads, results=results,
                          outcome='BOUNDED_REWARD_REFRESH', quotes_checked=min(len(keys),self.policy.maximum_refresh_quotes))

    def reconcile_synthetic_payment(self, key, *, statement_id, transfer_id):
        """Exercise independent-record matching in PAPER; never attest real funds."""
        request = dict(action='SYNTHETIC_PAYMENT', statement_id=identity(statement_id), transfer_id=identity(transfer_id))
        prior = self._replay(key, request)
        if prior: return prior
        statement = self.store.get(statement_id); transfer = self.store.get(transfer_id)
        rows = (statement, transfer); payloads = []
        for row, role in zip(rows, ('PROGRAM_STATEMENT', 'FINAL_TRANSFER')):
            p = row['body'].get('payload', {})
            if (row['kind'] != 'FEATURES' or row['body'].get('evidence_class') != 'SYNTHETIC'
                    or p.get('version') != PAYMENT_VERSION or p.get('role') != role
                    or p.get('account_id') != self.research.coordinator.policy.account_id
                    or row['event_id'] != self.key or p.get('finalized') is not True):
                raise EvidenceError('SYNTHETIC_PAYMENT_PROOF_REQUIRED_NO_LIVE_ATTESTOR')
            payloads.append(p)
        if statement_id == transfer_id or statement['body']['provider'] == transfer['body']['provider']:
            raise EvidenceError('REWARD_DISTINCT_STATEMENT_TRANSFER_REQUIRED')
        a, b = payloads
        fields = ('account_id','program','asset','amount','chain_id','transaction_hash','log_index','recipient','epoch_utc','market_id')
        if any(not set(fields) <= p.keys() for p in payloads): raise EvidenceError('REWARD_PAYMENT_SCHEMA')
        if any(a.get(k) != b.get(k) for k in fields): raise EvidenceError('REWARD_STATEMENT_TRANSFER_MISMATCH')
        if (a['program'] not in {'LIQUIDITY_REWARD','MAKER_REBATE'} or not 0 < number(a['amount']) <= 1000000
                or type(a['chain_id']) is not int or a['chain_id'] <= 0
                or type(a['log_index']) is not int or a['log_index'] < 0):
            raise EvidenceError('REWARD_PAYMENT_BOUND')
        sha(a['transaction_hash'])
        for value in (a['asset'],a['recipient'],a['epoch_utc'],a['market_id']): identity(value)
        payment_id = digest([a['chain_id'],a['transaction_hash'],a['log_index']])
        head = self._head(); state = self._state(head)
        if payment_id in state['payments']: raise EvidenceError('REWARD_TRANSFER_ALREADY_RECONCILED')
        if len(state['payments']) >= 512: raise EvidenceError('REWARD_PAYMENT_RETENTION_BOUND')
        state['payments'][payment_id] = {k:a[k] for k in fields}
        return self._save(key, request, head, state, refs=(statement_id,transfer_id),
             outcome='SYNTHETIC_RECEIPT_MATCHED', actual_independent_reconciliation=False,
             payment_id=payment_id, income_class='SYNTHETIC_PAPER_ONLY')

    @precise
    def report(self, key):
        request = dict(action='REPORT'); prior = self._replay(key, request)
        if prior: return prior
        head = self._head(); state = self._state(head); coordinator = self.research.coordinator
        account_head = coordinator._head(); account = coordinator._state(account_head)
        pnl = sum((number(v,signed=True) for v in account['event_realized_pnl'].values()),Decimal(0))
        income = {}
        for payment in state['payments'].values():
            income[payment['asset']] = income.get(payment['asset'],Decimal(0))+number(payment['amount'])
        asset = coordinator.policy.collateral_asset
        # Only equal explicitly named units can be combined; no currency guess.
        received = income.get(asset,Decimal(0))
        refs = (account_head['id'],) if account_head else ()
        heads = (('COORDINATOR_EVENT',ACCOUNT_KEY,account_head['seq'] if account_head else 0),)
        return self._save(key, request, head, state, refs=refs, heads=heads, outcome='SYNTHETIC_ACCOUNT_REPORT',
            TRADING_PNL=dict(realized=str(pnl), asset=asset, unrealized=None, classification='COMMON_PAPER_ACCOUNT_ONLY'),
            REWARD_REBATE_INCOME=dict(recorded_synthetic_by_asset={a:str(v) for a,v in sorted(income.items())},
                                    actual_independently_verified=None, coverage='RECORDED_SYNTHETIC_RECEIPTS_ONLY'),
            COMBINED_NET_RESULT=dict(realized=str(pnl+received), asset=asset, classification='SYNTHETIC_RECORDED_ONLY',
                                     foreign_assets_excluded=sorted(set(income)-{asset})),
            hard_cash_unchanged=account['cash'], claimable_unchanged=account['claimable_collateral'],
            estimated_rewards_in_cash='0', combined_result_is_available_cash=False,
            estimate_actual_discrepancy=None, discrepancy_status='NO_INDEPENDENT_ACTUAL_RECEIPTS',
            estimate_aggregation='QUOTE_POOL_CAPS_OVERLAP_AND_ARE_NEVER_SUMMED')
