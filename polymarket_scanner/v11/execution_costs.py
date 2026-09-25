"""Receipt-bound PAPER cost attribution and visible-depth price comparisons.

Synthetic execution details do not attest a venue fill, market impact, fill
probability or matched EV capture. Costs are already in the common ledger.
"""
from collections import defaultdict
from dataclasses import asdict, dataclass
from decimal import Decimal, InvalidOperation
import time

from .evidence import EvidenceError, canonical, digest, finite, identity
from .fill_evidence import execution_details
from .learning_sources import learning_source_view, source_derivation
from .measurement import executable_depth
from .scenario_risk import number, precise


VERSION = 'alpha_v11_paper_execution_costs_v1'
SELECTION = 'ALL_RECONCILED_FILLS_POSSIBLY_IN_EXECUTION_WINDOW'


@dataclass(frozen=True)
class ExecutionCostPolicy:
    version: str
    maximum_signal_age_seconds: float
    maximum_post_validation_age_seconds: float
    maximum_fills: int = 128
    maximum_seconds: float = 2.

    def __post_init__(self):
        identity(self.version)
        if (not 0 < finite(self.maximum_signal_age_seconds) <= 300
                or not 0 < finite(self.maximum_post_validation_age_seconds) <= 300
                or type(self.maximum_fills) is not int or not 1 <= self.maximum_fills <= 128
                or not .05 <= finite(self.maximum_seconds) <= 2):
            raise EvidenceError('EXECUTION_COST_POLICY_BOUND')


def _ref(row): return dict(id=row['id'], sha256=row['sha256'], seq=row['seq'])


def _unknown(exc):
    return str(exc) if isinstance(exc, EvidenceError) else 'EXECUTION_COST_MALFORMED_EVIDENCE'


@precise
def _benchmark(view, book, *, units, prior_units, direction, at, maximum_age):
    b = book['body']; p = b['payload']
    if p.get('stream_healthy') is not True: raise EvidenceError('EXECUTION_COST_BOOK_UNHEALTHY')
    if any(not 0 <= at-b[key] < maximum_age for key in ('observed_at', 'received_at', 'available_at')):
        raise EvidenceError('EXECUTION_COST_BOOK_STALE')
    derivation = source_derivation(view, (book,), event_id=book['event_id'], cutoff=b['available_at'])
    wanted = prior_units+units
    bid = executable_depth(p.get('bids'), wanted, direction='SELL', fee_per_share=None)
    ask = executable_depth(p.get('asks'), wanted, direction='ACQUIRE', fee_per_share=None)
    if (not p['bids'] or not p['asks'] or max(number(x['price']) for x in p['bids']) >= min(number(x['price']) for x in p['asks'])):
        raise EvidenceError('EXECUTION_COST_BOOK_EMPTY_OR_CROSSED')
    side = 'asks' if direction == 'BUY' else 'bids'; mode = 'ACQUIRE' if direction == 'BUY' else 'SELL'
    depth = ask if direction == 'BUY' else bid
    if not depth.full_depth: raise EvidenceError('EXECUTION_COST_INSUFFICIENT_CUMULATIVE_DEPTH')
    prior = (executable_depth(p[side], prior_units, direction=mode, fee_per_share=None).gross_value
             if prior_units else Decimal(0))
    gross = depth.gross_value-prior
    return dict(status='MATCHED_VISIBLE_DEPTH', book_ref=_ref(book), evidence_class=b['evidence_class'],
        prior_units=str(prior_units), units=str(units), gross_collateral=str(gross),
        gross_per_share=str(gross/units), source_derivation_sha256=derivation['sha256'] if derivation else None)


def _proofs(view, c, account, state):
    rows = []; quantities = defaultdict(Decimal)
    for fill_id, hash_ in sorted(state['fills'].items()):
        view.check(); proof = view.by_hash(kind='TRADE', sha256=hash_); b = proof['body']; p = b['payload']
        intent = state['intents'][p['intent_id']]; value = view.get(intent['valuation_id']); vd = value['body']['details']
        if (proof['seq'] >= account['seq'] or b.get('evidence_class') != 'SYNTHETIC'
                or p.get('record_type') != 'PAPER_FILL' or p.get('execution_namespace') != view.namespace
                or p.get('account_id') != c.policy.account_id or p.get('fill_id') != fill_id
                or p.get('intent_id') != intent['proposal_id'] or p.get('token_id') != intent['token_id']
                or p.get('direction') != intent['direction'] or proof['event_id'] != intent['event_id']
                or number(p['units']) <= 0 or value['kind'] != 'MEASUREMENT' or value['event_id'] != intent['event_id']
                or value['seq'] >= proof['seq'] or vd.get('binding') != intent['binding']
                or not value['body']['recorded_at'] <= b['received_at'] <= b['available_at'] <= account['body']['recorded_at']):
            raise EvidenceError('EXECUTION_COST_RECONCILED_PROOF_REQUIRED')
        number(p['all_in_collateral']); quantities[intent['proposal_id']] += number(p['units'])
        try: details = execution_details(view, proof, intent, account_id=c.policy.account_id, collateral_asset=c.policy.collateral_asset)
        except (EvidenceError, KeyError, TypeError, ValueError, InvalidOperation) as exc:
            details = dict(status='UNKNOWN', reason=_unknown(exc))
        rows.append((proof, intent, value, details))
    if any(quantities[k] != number(i['filled_units']) for k, i in state['intents'].items()):
        raise EvidenceError('EXECUTION_COST_RECONCILED_QUANTITY_MISMATCH')
    return rows


@precise
def measure_execution_costs(c, *, start, end, account_row, policy, monotonic=time.monotonic):
    if not isinstance(policy, ExecutionCostPolicy) or c.store.namespace != 'V11_PAPER':
        raise EvidenceError('EXECUTION_COST_PAPER_POLICY_REQUIRED')
    start, end = finite(start), finite(end)
    if not start < end <= finite(c.store.clock()): raise EvidenceError('EXECUTION_COST_WINDOW_INVALID')
    result = dict(version=VERSION, selection=SELECTION, policy=asdict(policy), policy_sha256=digest(asdict(policy)),
        account_ref=_ref(account_row) if account_row else None, account_id=c.policy.account_id,
        execution_namespace=c.store.namespace, collateral_asset=c.policy.collateral_asset,
        window=dict(start_inclusive=start, end_exclusive=end), rows=[], status='GATED', reason=None,
        complete_cost_population=False, complete_price_comparisons=False, fees=None, other_costs=None,
        known_cost_subtotals=None, price_comparison_groups=[], financial_authority=False,
        execution_class='SYNTHETIC_PAPER_FILL', venue_execution_attested=False, realized_ev=None, ev_capture_ratio=None,
        costs_already_in_ledger=True, additional_pnl_adjustment='0', market_impact_attribution=None,
        price_sign='POSITIVE_IS_ADVERSE_FOR_BUY_AND_SELL',
        depth_allocation='CUMULATIVE_FILLED_UNITS_PER_INTENT_AND_EXACT_BOOK_IN_EXECUTION_TIME_THEN_RECEIPT_ORDER')
    try:
        with learning_source_view(c.store, deadline=monotonic()+policy.maximum_seconds, monotonic=monotonic) as view:
            if account_row is None:
                result.update(status='NO_RECONCILED_FILLS', reason='NO_ACCOUNT_SNAPSHOT', complete_cost_population=True,
                              complete_price_comparisons=True, fees='0', other_costs='0', reconciled_fill_count=0)
                return result
            from .paper_coordinator import ACCOUNT_KEY, VERSION as ACCOUNT_VERSION
            account = view.get(account_row['id']); d = account['body']['details']; state = d['state']
            if (account != account_row or account['kind'] != 'COORDINATOR_EVENT' or account['event_id'] != ACCOUNT_KEY
                    or d.get('version') != ACCOUNT_VERSION or d.get('policy_sha256') != c.policy_sha
                    or state['account_id'] != c.policy.account_id or state['execution_namespace'] != view.namespace
                    or state.get('financial_authority') is not False or len(state['fills']) > 2048
                    or len(state['intents']) > 512 or account['body']['recorded_at'] > c.store.clock()):
                raise EvidenceError('EXECUTION_COST_ACCOUNT_SNAPSHOT')
            proofs = _proofs(view, c, account, state)
            unknown_order = {i['proposal_id'] for _, i, _, x in proofs if x['status'] != 'VALIDATED_SYNTHETIC_DETAILS'}
            proofs.sort(key=lambda r:(r[3].get('executed_at') if r[3].get('executed_at') is not None else float('inf'), r[0]['seq']))
            signal_used = defaultdict(Decimal); post_used = defaultdict(Decimal)
            known_fees = known_other = Decimal(0); known_count = 0; groups = {}
            for proof, intent, value, details in proofs:
                view.check(); p = proof['body']['payload']; qty = number(p['units']); key = intent['proposal_id']
                valid = details['status'] == 'VALIDATED_SYNTHETIC_DETAILS'; at = details.get('executed_at') if valid else None
                selected = (start <= at < end if valid else
                    value['body']['recorded_at'] < end and proof['body']['received_at'] >= start)
                before = signal_used[key]; post_key = (key, details['post_validation_book_ref']['id']) if valid else None
                post_before = post_used[post_key] if valid else Decimal(0)
                signal_used[key] += qty
                if valid: post_used[post_key] += qty
                if not selected: continue
                if len(result['rows']) >= policy.maximum_fills: raise EvidenceError('EXECUTION_COST_COHORT_BOUND')
                row = dict(fill_id=p['fill_id'], proof_ref=_ref(proof), intent_id=key, event_id=intent['event_id'],
                    token_id=intent['token_id'], direction=intent['direction'], units=p['units'],
                    original_decision_ref=_ref(value), model_bundle_sha256=intent['binding']['bundle_sha256'],
                    decision_attribution=intent['attribution'], joint_ev_is_not_allocated=bool(intent.get('joint_ev_only')),
                    executed_at=at, time_status='EXACT_SYNTHETIC_RECEIPT' if valid else 'POSSIBLE_WINDOW_OVERLAP',
                    cost_status='VALIDATED_SYNTHETIC_DETAILS' if valid else 'UNKNOWN', reason=details['reason'],
                    all_in_collateral=p['all_in_collateral'], fee_collateral=None, other_cost_collateral=None,
                    gross_collateral=None, signal=dict(status='UNKNOWN'), post_validation=dict(status='UNKNOWN'),
                    price_shortfall_vs_signal=None, price_shortfall_vs_post_validation=None,
                    signal_to_post_price_change=None, total_cost_vs_signal=None)
                if valid:
                    fee = number(details['fee_collateral']); other = number(details['other_cost_collateral'])
                    gross = Decimal(details['gross_collateral']); known_fees += fee; known_other += other; known_count += 1
                    row.update(fee_collateral=str(fee), other_cost_collateral=str(other), gross_collateral=str(gross))
                    for label, reference, used, stamp, age in (
                        ('signal', details['signal_book_ref'], before, value['body']['recorded_at'], policy.maximum_signal_age_seconds),
                        ('post_validation', details['post_validation_book_ref'], post_before, at, policy.maximum_post_validation_age_seconds)):
                        try:
                            if key in unknown_order: raise EvidenceError('EXECUTION_COST_PARTIAL_FILL_ORDER_UNKNOWN')
                            row[label] = _benchmark(view, view.get(reference['id']), units=qty, prior_units=used,
                                direction=intent['direction'], at=stamp, maximum_age=age)
                        except (EvidenceError, KeyError, TypeError, ValueError, InvalidOperation) as exc:
                            row[label] = dict(status='UNKNOWN', reason=_unknown(exc), book_ref=reference)
                    sign = Decimal(1 if intent['direction'] == 'BUY' else -1)
                    for label, field in (('signal','price_shortfall_vs_signal'), ('post_validation','price_shortfall_vs_post_validation')):
                        if row[label]['status'] == 'MATCHED_VISIBLE_DEPTH':
                            row[field] = str(sign*(gross-Decimal(row[label]['gross_collateral'])))
                    if row['price_shortfall_vs_signal'] is not None:
                        row['total_cost_vs_signal'] = str(Decimal(row['price_shortfall_vs_signal'])+fee+other)
                    if (row['price_shortfall_vs_signal'] is not None and row['price_shortfall_vs_post_validation'] is not None
                            and row['signal']['evidence_class'] == row['post_validation']['evidence_class']):
                        movement = sign*(Decimal(row['post_validation']['gross_collateral'])-Decimal(row['signal']['gross_collateral']))
                        row['signal_to_post_price_change'] = str(movement)
                        if Decimal(row['price_shortfall_vs_signal']) != movement+Decimal(row['price_shortfall_vs_post_validation']):
                            raise EvidenceError('EXECUTION_COST_PRICE_DECOMPOSITION')
                        gkey = (intent['direction'], row['signal']['evidence_class'])
                        group = groups.setdefault(gkey, dict(direction=gkey[0],book_evidence_class=gkey[1],population='MATCHED_ROWS_ONLY',fills=0,units=Decimal(0),
                            price_shortfall_vs_signal=Decimal(0), price_shortfall_vs_post_validation=Decimal(0),
                            signal_to_post_price_change=Decimal(0), total_cost_vs_signal=Decimal(0)))
                        group['fills'] += 1; group['units'] += qty
                        for field in ('price_shortfall_vs_signal','price_shortfall_vs_post_validation','signal_to_post_price_change','total_cost_vs_signal'):
                            group[field] += Decimal(row[field])
                result['rows'].append(row)
            view.check()
            total = len(result['rows']); complete = known_count == total
            comparisons = all(r['signal_to_post_price_change'] is not None for r in result['rows'])
            result.update(status='NO_RECONCILED_FILLS' if not total else 'COMPLETE_SYNTHETIC_COSTS_AND_COMPARISONS' if complete and comparisons else 'PARTIAL',
                reason='RETAINED_RECONCILED_POPULATION_ONLY', complete_cost_population=complete, complete_price_comparisons=comparisons,
                reconciled_fill_count=len(proofs), selected_fill_count=total, known_cost_count=known_count,
                unknown_cost_count=total-known_count, unknown_price_comparison_count=sum(r['signal_to_post_price_change'] is None for r in result['rows']),
                known_cost_subtotals=dict(fees=str(known_fees), other_costs=str(known_other), fills=known_count, is_full_window_total=complete),
                fees=str(known_fees) if complete else None, other_costs=str(known_other) if complete else None,
                price_comparison_groups=[{k:str(v) if isinstance(v,Decimal) else v for k,v in g.items()} for _,g in sorted(groups.items())],
                account_faults=list(state['faults']), retained_population_is_not_universe_coverage=True)
            if len(canonical(result).encode()) > 512*1024: raise EvidenceError('EXECUTION_COST_OUTPUT_BOUND')
            return result
    except (EvidenceError, KeyError, TypeError, ValueError, InvalidOperation) as exc:
        # A capped or interrupted scan cannot publish its favorable prefix.
        result.update(status='GATED', reason=_unknown(exc), rows=[], complete_cost_population=False,
                      complete_price_comparisons=False, fees=None, other_costs=None,
                      known_cost_subtotals=None, price_comparison_groups=[])
        return result
