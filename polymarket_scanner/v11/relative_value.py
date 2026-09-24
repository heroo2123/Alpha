"""Bounded whole-event discovery feeding exact joint basket valuation.

Point-price gaps are diagnostics, not conservative alpha. No probability is
inferred by normalizing quoted prices, and no leg is treated as independent risk.
"""
from dataclasses import asdict, dataclass
from decimal import Decimal
import math

from .basket_coordinator import BasketProposal
from .basket_valuation import BasketLeg, BasketPolicy, STRATEGIES, analyze_basket
from .certification import CapabilityScope
from .event_risk import EventContext, EventRiskEngine, SafetyReductions
from .evidence import EvidenceError, EvidenceStore, ReleaseBinding, canonical, digest, finite, identity
from .model_artifacts import predict_with_bundle
from .model_registry import ActiveModelRegistry
from .probability import FINAL_EXTREME, _partition
from .rules import RuleFingerprint
from .scenario_risk import Attribution, number
from .strategy_admission import StrategyAdmission, VERSION as ADMISSION_VERSION
from .strategy_pipeline import _model_inputs
from .valuation import contract_target


VERSION = 'alpha_v11_whole_event_relative_value_v1'


@dataclass(frozen=True)
class DiscoveryRequest:
    admission_id: str
    event_state_id: str
    instruments: tuple[BasketLeg, ...]
    desired_positions: tuple[tuple[str, str], ...]
    model_input_ids: tuple[str, ...]
    policy: BasketPolicy
    expires_at: float
    maximum_proposals: int = 6

    def __post_init__(self):
        identity(self.admission_id); identity(self.event_state_id); finite(self.expires_at)
        if (type(self.instruments) is not tuple or not 1 <= len(self.instruments) <= 32
                or any(not isinstance(l, BasketLeg) for l in self.instruments)
                or not isinstance(self.policy, BasketPolicy)):
            raise EvidenceError('DISCOVERY_INSTRUMENT_BOUND')
        if (type(self.model_input_ids) is not tuple or not 1 <= len(self.model_input_ids) <= 16
                or len(set(self.model_input_ids)) != len(self.model_input_ids)):
            raise EvidenceError('DISCOVERY_MODEL_INPUT_BOUND')
        for key in self.model_input_ids:
            identity(key)
        if (type(self.desired_positions) is not tuple or len(self.desired_positions) != len(self.instruments)
                or any(type(pair) is not tuple or len(pair) != 2 for pair in self.desired_positions)):
            raise EvidenceError('DISCOVERY_DESIRED_POSITIONS_REQUIRED')
        for token, quantity in self.desired_positions:
            identity(token)
            if not 0 < number(quantity) <= 1_000_000:
                raise EvidenceError('DISCOVERY_DESIRED_POSITION_BOUND')
        if len(dict(self.desired_positions)) != len(self.desired_positions):
            raise EvidenceError('DISCOVERY_DUPLICATE_TOKEN')
        if type(self.maximum_proposals) is not int or not 1 <= self.maximum_proposals <= 6:
            raise EvidenceError('DISCOVERY_PROPOSAL_BOUND')


def _plans(rule, instruments):
    buckets = _partition(rule)
    if len(buckets) > 16:
        raise EvidenceError('DISCOVERY_EVENT_BUCKET_BOUND')
    by_key = {(l.market_id,l.side):l for l in instruments}
    if len(by_key) != len(instruments):
        raise EvidenceError('DISCOVERY_DUPLICATE_TOKEN')
    for leg in instruments:
        contract_target(rule, leg.market_id, leg.side)
    plans = {}
    def add(kind, keys):
        if not all(k in by_key for k in keys):
            return
        economic = tuple(sorted(keys))
        if economic in plans:
            plans[economic]['diagnostics'].append(kind)
        else:
            plans[economic] = dict(diagnostics=[kind], legs=tuple(by_key[k] for k in keys))
    for side in ('YES', 'NO'):
        add('EXHAUSTIVE_'+side, [(b['market_id'],side) for b in buckets])
    for b in buckets:
        market = b['market_id']
        add('CONDITION_COMPLEMENT', [(market,'YES'),(market,'NO')])
        for side in ('YES','NO'):
            add('INDIVIDUAL_POINT_PRICE_GAP', [(market,side)])
    for a,b in zip(buckets,buckets[1:]):
        add('ADJACENT_RELATIVE_VALUE', [(a['market_id'],'YES'),(b['market_id'],'YES')])
    if len(plans) > 65:
        raise EvidenceError('DISCOVERY_PLAN_BOUND')
    return list(plans.values())


def _unique_heads(heads):
    unique = {}
    for kind,event,seq in heads:
        if (kind,event) in unique and unique[(kind,event)] != seq:
            raise EvidenceError('DISCOVERY_STATE_CHANGED_RECOMPUTE')
        unique[(kind,event)] = seq
    return tuple((k,e,n) for (k,e),n in unique.items())


class RelativeValueStrategies:
    def __init__(self, store: EvidenceStore):
        self.store = store

    def evaluate(self, record_id: str, request: DiscoveryRequest) -> dict:
        identity(record_id, maximum=60); store = self.store
        request_data = asdict(request); request_sha = digest(request_data)
        a = store.get(request.admission_id)
        if a['kind'] != 'REGISTRY' or a['body'].get('details', {}).get('version') != ADMISSION_VERSION:
            raise EvidenceError('STRATEGY_ADMISSION_RECORD_REQUIRED')
        original = a['body']['details']['request']; scope = CapabilityScope(**original['scope'])
        if scope.strategy not in STRATEGIES:
            raise EvidenceError('RELATIVE_VALUE_STRATEGY_SCOPE_REQUIRED')
        context = EventContext(**original['context']); rule = RuleFingerprint(**original['rule'])
        binding = ReleaseBinding(**original['binding'])
        def prior(key):
            try:return store.get(key)
            except EvidenceError as exc:
                if str(exc) != 'EVIDENCE_MISSING':raise
        old = prior(record_id)
        if old:
            if old['kind'] != 'MEASUREMENT' or old['body']['details'].get('version') != VERSION or old['body']['details'].get('request_sha256') != request_sha:
                raise EvidenceError('DISCOVERY_REQUEST_ID_COLLISION')
            return old
        start = prior(record_id+':start')
        if start is None:
            start = store.audit(record_id+':start', event_id=context.event_id, kind='MEASUREMENT',
                    details=dict(version=VERSION, stage='STARTED', request_sha256=request_sha), evidence_ids=(request.admission_id,))
        if start['body']['details'].get('request_sha256') != request_sha:
            raise EvidenceError('DISCOVERY_REQUEST_ID_COLLISION')
        cutoff = start['body']['recorded_at']
        model = prediction = None; rows = []; proposals = []; heads = (); instrument_metrics = {}
        outcome, reason = 'GATED', None
        trace = [dict(stage='DISCOVERED', count=len(request.instruments))]
        try:
            plans = _plans(rule, request.instruments)
            tokens = {contract_target(rule,l.market_id,l.side)['token_id'] for l in request.instruments}
            if tokens != set(dict(request.desired_positions)):
                raise EvidenceError('DISCOVERY_DESIRED_TOKEN_SET_MISMATCH')
            trace.append(dict(stage='SEMANTICALLY_SUPPORTED', count=len(plans)))
            admission = StrategyAdmission(store).revalidate(request.admission_id, context=context, rule=rule,
                              binding=asdict(binding), strategies=(scope.strategy,))
            heads = tuple(tuple(h) for h in admission['heads'])+SafetyReductions(store).atomic_heads(context)
            event = EventRiskEngine(store).revalidate(request.event_state_id)
            event_row = store.get(request.event_state_id)
            heads += (('COORDINATOR_EVENT',event_row['event_id'],event_row['seq']),)
            if event['request']['context'] != asdict(context) or event['request']['binding'] != asdict(binding):
                raise EvidenceError('DISCOVERY_EVENT_BINDING')
            if not event['ordinary_new_risk_research_allowed']:
                raise EvidenceError('EVENT_STATE_SUPPRESSES_BASKET_DISCOVERY')
            leased = {s['evidence_id']:s for s in original['source_leases'] if s['role']=='MODEL'}
            if set(leased) != set(request.model_input_ids):
                raise EvidenceError('ALL_INFERENCE_MODELS_REQUIRE_ADMISSION_LEASE')
            if not cutoff <= finite(store.clock()) < min(request.expires_at,admission['valid_until'],event['valid_until']):
                raise EvidenceError('DISCOVERY_EXPIRED')
            inputs = _model_inputs(store,rule,request.model_input_ids,cutoff,target=FINAL_EXTREME)
            model = ActiveModelRegistry().pin(scope_key=scope.key, mode='V11_PAPER' if original['stage']=='PAPER' else 'V11_SHADOW')
            if model.state_sha256 != admission['model_state_sha256'] or model.bundle.sha256 != binding.bundle_sha256:
                raise EvidenceError('DISCOVERY_MODEL_CHANGED')
            prediction = predict_with_bundle(model.bundle, rule, inputs, as_of=cutoff,
                               max_source_age_seconds=min(s['maximum_age_seconds'] for s in leased.values()))
            vector = prediction.payload['buckets']
            if (len(vector)!=len(rule.payload['partition'])
                    or {b['market_id'] for b in vector}!={b['market_id'] for b in rule.payload['partition']}
                    or any(not 0<=finite(b['point'])<=1 for b in vector)
                    or not math.isclose(math.fsum(b['point'] for b in vector),1.,rel_tol=0,abs_tol=1e-12)):
                raise EvidenceError('DISCOVERY_WHOLE_EVENT_VECTOR_REQUIRED')
            trace.append(dict(stage='SOURCE_READY', count=len(inputs), target=FINAL_EXTREME))
            desired = dict(request.desired_positions)
            for index,plan in enumerate(plans):
                key = record_id+':value:'+str(index)
                try:
                    value = analyze_basket(store,key,rule=rule,prediction=prediction,binding=binding,
                           strategy=scope.strategy,account_id=context.account_id,legs=plan['legs'],policy=request.policy)['body']['details']
                    heads += tuple(tuple(h) for h in value['source_heads'])
                    for leg in value['legs']:
                        if leg['all_in_acquisition_cost'] is not None and not leg['reasons']:
                            cost = number(leg['all_in_acquisition_cost'])/number(leg['units'])
                            point = number(leg['point_payout_per_share'])
                            instrument_metrics[leg['target']['token_id']] = dict(target=leg['target'],
                               units=leg['units'],point_payout_per_share=str(point),
                               acquisition_per_share_at_requested_size=str(cost),point_price_gap=str(point-cost),
                               point_underpricing_diagnostic=point>cost,conservative_single_leg_payout_per_share='0')
                    candidate = dict(index=index,diagnostics=plan['diagnostics'],valuation_id=key,
                              point_ev_total=str(number(value['point_expected_payout'])-number(value['full_fill_all_in_cost']))
                                    if value['point_expected_payout'] is not None else None,
                              conservative_joint_ev_total=value['conservative_full_fill_ev_total'],
                              economics=value['full_fill_economics'],reasons=value['reasons'],proposal=None)
                    if 'ADJACENT_RELATIVE_VALUE' in plan['diagnostics'] and len(value['legs'])==2:
                        left,right = [instrument_metrics.get(l['target']['token_id']) for l in value['legs']]
                        if left and right:
                            fair = number(left['point_payout_per_share'])-number(right['point_payout_per_share'])
                            price = number(left['acquisition_per_share_at_requested_size'])-number(right['acquisition_per_share_at_requested_size'])
                            candidate['adjacent_comparison'] = dict(model_probability_difference=str(fair),
                                 acquisition_cost_difference=str(price),relative_gap=str(fair-price),
                                 ordered_bucket_probabilities_required=False,executable_spread_profit=None)
                    if value['full_fill_economics']=='CANDIDATE':
                        expiry = min(request.expires_at, admission['valid_until'], event['valid_until'],
                                     cutoff+request.policy.valuation.max_prediction_age_seconds)
                        for leg in value['legs']:
                            b = store.get(leg['book']['book_id'])['body']
                            expiry = min(expiry, b['observed_at']+request.policy.valuation.max_book_age_seconds,
                                         b['received_at']+request.policy.valuation.max_book_age_seconds)
                            for cost in leg['costs']['components']:
                                if cost['valid_until'] is not None:expiry = min(expiry,cost['valid_until'])
                        capital = sum(number(l['units'])*number(l['pending_unit_collateral_bound']) for l in value['legs'])
                        bounded_ev = number(value['conditional_full_fill_payout_floor'])-capital
                        if bounded_ev > number(request.policy.minimum_total_ev) and finite(store.clock()) < expiry:
                            thesis = digest(dict(strategy=scope.strategy, rule=rule.sha256, prediction=prediction.sha256,
                                            legs=[(l['target']['token_id'],l['units']) for l in value['legs']]))
                            p = BasketProposal(record_id+':proposal:'+str(index),thesis,context,rule,key,request.event_state_id,
                                  (Attribution(scope.strategy,'1'),),expiry,
                                  tuple((l['target']['token_id'],desired[l['target']['token_id']]) for l in value['legs']),
                                  (request.admission_id,))
                            candidate.update(proposal=asdict(p),ranking_ev_per_capital=str(bounded_ev/capital),
                                             ranking_ev_total=str(bounded_ev))
                        else:
                            candidate['reasons'] = ['LIMIT_PRICE_JOINT_EV_OR_EXPIRY_NOT_QUALIFIED']
                    rows.append(candidate)
                except EvidenceError as exc:
                    rows.append(dict(index=index,diagnostics=plan['diagnostics'],valuation_id=None,
                                economics='GATED',reasons=[str(exc)],proposal=None,point_ev_total=None))
            trace.append(dict(stage='EVALUATED', count=len(rows)))
            after = StrategyAdmission(store).revalidate(request.admission_id,context=context,rule=rule,
                                      binding=asdict(binding),strategies=(scope.strategy,))
            heads += tuple(tuple(h) for h in after['heads'])
            EventRiskEngine(store).revalidate(request.event_state_id)
            if not ActiveModelRegistry().revalidate(model)['passed']:
                raise EvidenceError('DISCOVERY_MODEL_CHANGED')
            _unique_heads(heads)
            eligible = sorted((r for r in rows if r['proposal']),key=lambda r:
                     (-Decimal(r['ranking_ev_per_capital']),-Decimal(r['ranking_ev_total']),r['index']))
            proposals = [r['proposal'] for r in eligible[:request.maximum_proposals]]
            outcome = 'CANDIDATES' if proposals else 'NO_QUALIFIED_OPPORTUNITY' if any(r['economics']!='GATED' for r in rows) else 'GATED'
            reason = 'COMMON_ACCOUNT_ADMISSION_REQUIRED' if proposals else 'NO_POSITIVE_CONSERVATIVE_JOINT_ECONOMICS' if outcome!='GATED' else 'INPUT_OR_ECONOMIC_EVIDENCE_UNAVAILABLE'
        except EvidenceError as exc:
            outcome,reason,proposals = 'GATED',str(exc),[]
            for row in rows:
                row['proposal'] = None
        trace.append(dict(stage='CANDIDATE',count=len(proposals),state=outcome,reason=reason))
        result_ids = []
        # Queue completion may use these bounded, individually linked outputs.
        # It must not claim a scan summary evaluated a different valuation ID.
        for i,p in enumerate(proposals):
            key = record_id+':candidate:'+str(i)
            d = dict(version=VERSION,request_sha256=request_sha,proposal=p,financial_authority=False)
            saved = prior(key)
            if saved is not None:
                if saved['kind']!='MEASUREMENT' or canonical(saved['body'].get('details'))!=canonical(d):
                    raise EvidenceError('DISCOVERY_PARTIAL_RESULT_BINDING')
            else:
                store.audit(key,event_id=context.event_id,kind='MEASUREMENT',details=d,evidence_ids=(p['valuation_id'],),
                            expected_heads=_unique_heads(heads))
            result_ids.append(key)
        curve = []; model_cumulative = Decimal(0); acquisition_cumulative = Decimal(0)
        if prediction is not None and reason!='DISCOVERY_WHOLE_EVENT_VECTOR_REQUIRED':
            by_market = {b['market_id']:b for b in prediction.payload['buckets']}
            for bucket in _partition(rule):
                model_cumulative += Decimal(str(by_market[bucket['market_id']]['point']))
                metric = instrument_metrics.get(bucket['yes_token'])
                acquisition_cumulative = (acquisition_cumulative+number(metric['acquisition_per_share_at_requested_size'])
                                          if metric and acquisition_cumulative is not None else None)
                curve.append(dict(market_id=bucket['market_id'], model_cumulative_probability=str(model_cumulative),
                         quoted_acquisition_cumulative_cost=str(acquisition_cumulative) if acquisition_cumulative is not None else None))
        details = dict(version=VERSION,request=request_data,request_sha256=request_sha,strategy=scope.strategy,
            scope=asdict(scope),binding=asdict(binding),as_of=cutoff,outcome=outcome,reason=reason,opportunities=rows,
            proposals=proposals,queue_result_ids=result_ids or [record_id],funnel=trace,
            prediction=prediction.payload if prediction else None,
            artifact_refs=model.bundle.payload['bundle']['artifacts'] if model else None,model_epoch=model.epoch if model else None,
            candidate_limit=request.maximum_proposals,point_gaps_are_not_calibrated_alpha=True,
            instruments=list(instrument_metrics.values()),
            whole_event_curve=curve,quoted_curve_is_not_a_probability_distribution=True,
            unselected_proposal_count=max(0,sum(r['proposal'] is not None for r in rows)-len(proposals)),
            quotes_normalized_as_probabilities=False,independent_bucket_risk=False,
            execution_status='NOT_SUBMITTED',financial_authority=False)
        # Individual values are transitively tied to books; keeping references
        # bounded avoids silently dropping capture provenance in a large scan.
        refs = tuple(dict.fromkeys([start['id'],request.admission_id,request.event_state_id,*request.model_input_ids,*result_ids]))
        available = tuple(key for key in refs if prior(key) is not None)
        return store.audit(record_id,event_id=context.event_id,kind='MEASUREMENT',details=details,evidence_ids=available,
                           expected_heads=_unique_heads(heads) if outcome!='GATED' else ())

    def proposals(self, record_id: str) -> tuple[BasketProposal, ...]:
        row = self.store.get(record_id); d = row['body'].get('details',{})
        if row['kind']!='MEASUREMENT' or d.get('version')!=VERSION or d.get('outcome')!='CANDIDATES':
            raise EvidenceError('DISCOVERY_HAS_NO_QUALIFIED_PROPOSAL')
        return tuple(BasketProposal.from_dict(p) for p in d['proposals'])
