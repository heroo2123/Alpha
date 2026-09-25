"""One account-wide research coordinator with atomic, durable reservations.

This module cannot open LIVE/V10 namespaces and has no network/order adapter.
Paper fills and terminal evidence must be explicitly synthetic. Production
entitlement, scoped strategy certification and external-account reconciliation
are separate commissioning requirements, not granted by these local checks.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from decimal import Decimal, localcontext

from .allocation import rank_candidates
from .account_effects import EffectInputs
from .basket_coordinator import BasketProposal
from .event_risk import EventContext, EventRiskEngine, SafetyReductions
from .evidence import EvidenceError, EvidenceStore, canonical, digest, finite, identity
from .rules import RuleFingerprint
from .scenario_risk import (Attribution, Position, PendingOrder, CorrelationMap, ScenarioLimits,
                            event_scenarios, portfolio_risk, number, _attribution, precise)
from .valuation import VERSION as EV_VERSION, contract_target
from .strategy_admission import StrategyAdmission
from .event_queue import admission_heads as event_queue_admission
from .position_attribution import intent_lineage, consume_lots
from .runtime_health import admission_heads as runtime_health_admission
from .paper_reconciliation import admission_heads as receipt_admission


VERSION = 'alpha_v11_paper_coordinator_v1'
ACCOUNT_KEY = 'v11-paper-account-state'
UNRESOLVED = {'RESERVED', 'SUBMITTING', 'UNKNOWN', 'ACKNOWLEDGED', 'PARTIAL', 'CANCEL_REQUESTED'}
TERMINAL = {'FILLED', 'CANCELED', 'EXPIRED', 'REJECTED'}


def cancel_identity(intent):
    """Pin managed intent economics while allowing fills/status to advance."""
    return digest({k:v for k,v in intent.items() if k not in {'status', 'filled_units', 'cancel_requested'}})


@dataclass(frozen=True)
class PaperAccountPolicy:
    policy_version: str
    account_id: str
    collateral_asset: str
    initial_hypothetical_cash: str
    capital_limit: str
    per_intent_cash_limit: str
    daily_loss_limit: str
    minimum_ev_per_share: str
    retained_cash: str
    maximum_intent_lifetime_seconds: float
    max_active_intents: int

    def __post_init__(self):
        for value in (self.policy_version, self.account_id, self.collateral_asset):
            identity(value)
        for name in ('initial_hypothetical_cash', 'capital_limit', 'per_intent_cash_limit', 'daily_loss_limit'):
            if number(getattr(self, name)) <= 0:
                raise EvidenceError('PAPER_ACCOUNT_LIMIT_INVALID')
        if (number(self.initial_hypothetical_cash) > number(self.capital_limit)
                or number(self.per_intent_cash_limit) > number(self.capital_limit)
                or number(self.daily_loss_limit) > number(self.capital_limit)
                or number(self.retained_cash) >= number(self.initial_hypothetical_cash)
                or number(self.minimum_ev_per_share) > 1
                or not 0 < finite(self.maximum_intent_lifetime_seconds) <= 3600
                or type(self.max_active_intents) is not int or not 1 <= self.max_active_intents <= 256):
            raise EvidenceError('PAPER_ACCOUNT_LIMIT_INVALID')


@dataclass(frozen=True)
class Proposal:
    proposal_id: str
    thesis_id: str
    context: EventContext
    rule: RuleFingerprint
    valuation_id: str
    event_state_id: str
    attribution: tuple[Attribution, ...]
    expires_at: float
    desired_total_units: str
    admission_ids: tuple[str, ...]
    preconfirmation_id: str | None = None
    source_release_id: str | None = None

    def __post_init__(self):
        for v in (self.proposal_id, self.thesis_id, self.valuation_id, self.event_state_id):
            identity(v)
        _attribution(self.attribution)
        finite(self.expires_at)
        if number(self.desired_total_units) > 1_000_000:
            raise EvidenceError('DESIRED_POSITION_BOUND')
        if (type(self.admission_ids) is not tuple or not 1 <= len(self.admission_ids) <= 4
                or len(set(self.admission_ids)) != len(self.admission_ids)):
            raise EvidenceError('SCOPED_STRATEGY_ADMISSION_REQUIRED')
        for key in self.admission_ids:
            identity(key)
        if self.preconfirmation_id is not None:
            identity(self.preconfirmation_id)
        if self.source_release_id is not None:
            identity(self.source_release_id)
        if self.context.event_id != self.rule.payload['event_id'] or self.context.station_id != self.rule.payload['station']:
            raise EvidenceError('PROPOSAL_RULE_CONTEXT')


class PaperCoordinator:
    def __init__(self, store: EvidenceStore, *, policy: PaperAccountPolicy,
                 correlation: CorrelationMap, limits: ScenarioLimits):
        self.store, self.policy, self.correlation, self.limits = store, policy, correlation, limits
        self.policy_sha = digest(dict(account=asdict(policy), correlation=asdict(correlation), limits=asdict(limits)))
        self.reconciliation_config = None

    def _head(self):
        row = self.store.latest(kind='COORDINATOR_EVENT', event_id=ACCOUNT_KEY)
        if row:
            details = row['body']['details']
            if (details.get('version') != VERSION or details['policy_sha256'] != self.policy_sha
                    or details['state']['account_id'] != self.policy.account_id):
                raise EvidenceError('PAPER_ACCOUNT_POLICY_OR_IDENTITY_CHANGED')
        return row

    def _state(self, row):
        if row:
            return deepcopy(row['body']['details']['state'])
        return dict(account_id=self.policy.account_id, execution_namespace=self.store.namespace,
                    cash=self.policy.initial_hypothetical_cash, rules={}, contexts={}, intents={}, lots={}, fills={},
                    event_realized_pnl={}, realized_entries=[], claimable_collateral='0', redeemed_collateral='0',
                    faults=[], financial_authority=False)

    def _replay(self, record_id, request):
        try:
            row = self.store.get(record_id)
        except EvidenceError as exc:
            if str(exc) == 'EVIDENCE_MISSING':
                return None
            raise
        if (row['event_id'] != ACCOUNT_KEY or row['kind'] != 'COORDINATOR_EVENT'
                or canonical(row['body']['details'].get('request')) != canonical(request)
                or row['body']['details'].get('policy_sha256') != self.policy_sha):
            raise EvidenceError('ACCOUNT_COMMAND_REPLAY_CONFLICT')
        return row

    @staticmethod
    def _rule(raw):
        return RuleFingerprint(raw['canonical_json'], raw['sha256'], raw['source_event_sha256'])

    @staticmethod
    def _position(raw):
        return Position(raw['lot_id'], raw['token_id'], raw['units'], raw['all_in_cost_basis'],
                        tuple(Attribution(**a) for a in raw['attribution']))

    def _views(self, state, *, include_realized=True):
        views = []
        for event, raw in sorted(state['rules'].items()):
            positions = tuple(self._position(p) for p in state['lots'].values() if p['event_id'] == event)
            pending = tuple(PendingOrder(p['proposal_id'], p['token_id'], p['direction'],
                                         str(number(p['units'])-number(p['filled_units'])), p['unit_collateral_bound'],
                                         tuple(Attribution(**a) for a in p['attribution']))
                            for p in state['intents'].values() if p['event_id'] == event and p['status'] in UNRESOLVED
                            and number(p['units']) > number(p['filled_units']))
            views.append(event_scenarios(self._rule(raw), execution_namespace=self.store.namespace,
                                        account_id=self.policy.account_id, positions=positions, pending=pending,
                                        realized_event_pnl=state['event_realized_pnl'].get(event, '0') if include_realized else '0'))
        return tuple(views)

    @precise
    def _risk(self, state, *, at=None, clock=None):
        views = self._views(state)
        risk = portfolio_risk(views, correlation=self.correlation, limits=self.limits,
                              execution_namespace=self.store.namespace, account_id=self.policy.account_id)
        holds = sum(Decimal(v['reserved_buy_cash']) for v in views)
        held_cost = sum(number(p['all_in_cost_basis']) for p in state['lots'].values())
        all_losses = sum(max(Decimal(0), -number(r['pnl'], signed=True)) for r in state['realized_entries'])
        now = finite((clock or self.store.clock)() if at is None else at); day_start = int(now//86400)*86400
        day_losses = sum(max(Decimal(0), -number(r['pnl'], signed=True)) for r in state['realized_entries'] if r['at'] >= day_start)
        open_loss = sum(Decimal(v['worst_case_loss']) for v in self._views(state, include_realized=False))
        active = sum(p['status'] in UNRESOLVED for p in state['intents'].values())
        cash = number(state['cash'], signed=True)
        faults = list(risk['faults'])
        if holds+self.policy_amount('retained_cash') > cash:
            faults.append(dict(gate='UNRESERVED_PAPER_CASH'))
        if held_cost+holds+all_losses > self.policy_amount('capital_limit'):
            faults.append(dict(gate='FIXED_CAPITAL_LIMIT'))
        if day_losses+open_loss > self.policy_amount('daily_loss_limit'):
            faults.append(dict(gate='DAILY_LOSS_PLUS_OPEN_DOWNSIDE'))
        if active > self.policy.max_active_intents:
            faults.append(dict(gate='ACTIVE_INTENT_LIMIT'))
        return dict(scenarios=views, portfolio=risk, cash=str(cash), reserved_cash=str(holds),
                    unreserved_cash=str(cash-holds), held_cost_basis=str(held_cost),
                    claimable_collateral=state['claimable_collateral'], redeemed_collateral=state['redeemed_collateral'],
                    daily_realized_losses=str(day_losses), active_worst_loss=str(open_loss),
                    faults=faults, accepted=not faults and not state['faults'], financial_authority=False)

    def policy_amount(self, name):
        return number(getattr(self.policy, name))

    def _commit(self, record_id, request, row, state, details, *, evidence_ids=(), heads=(), receipt_seq=None, effect_inputs=None):
        if (len(state['rules']) > 32 or len(state['intents']) > 512 or len(state['fills']) > 2048
                or len(state['lots']) > 512 or len(state.get('baskets', {})) > 128):
            raise EvidenceError('PAPER_ACCOUNT_RETENTION_BOUND_NO_UNSAFE_PRUNING')
        if effect_inputs is not None:
            details = dict(details, effect_inputs=effect_inputs.payload(row, request, heads=heads, receipt_seq=receipt_seq))
        audit = self.store.safety_audit if request.get('action') == 'TRANSITION' and request.get('status') == 'CANCEL_REQUESTED' else self.store.audit
        return audit(record_id, event_id=ACCOUNT_KEY, kind='COORDINATOR_EVENT',
                                details=dict(version=VERSION, policy_sha256=self.policy_sha,
                                             request=request, state=state, **details), evidence_ids=evidence_ids,
                                expected_previous_seq=row['seq'] if row else 0, expected_heads=heads,
                                **({'expected_receipt_seq': receipt_seq} if receipt_seq is not None else {}))

    def snapshot(self):
        row = self._head()
        return self._risk(self._state(row))

    def _preconfirmation(self, key, *, strategies, context, rule, binding, admissions):
        if 'PWS_OBSERVATION_LEAD' not in strategies:
            if key is not None:
                raise EvidenceError('PWS_PRECONFIRMATION_ATTRIBUTION_REQUIRED')
            return None
        if key is None:
            raise EvidenceError('PWS_SEPARATE_OBSERVATION_AND_ECONOMICS_PIN_REQUIRED')
        from .pws_admission import PWSPreconfirmation
        return PWSPreconfirmation(self.store).revalidate(key, context=context, rule=rule,
                    binding=binding, payout_admission_ids=admissions)

    def _current_book_heads(self, value, event_id):
        # Guard the head read before inspecting the exact latest source. A new
        # book racing the account commit requires a new valuation, not reuse.
        head = self.store.latest(kind='BOOK', event_id=event_id)
        row = self.store.get(value['book']['book_id']); b = row['body']
        latest = self.store.latest_source(kind='BOOK', event_id=event_id,
                                          provider=b['provider'], source_identity=b['source_identity'])
        now = finite(self.store.clock()); age = value['policy']['max_book_age_seconds']
        if (row['kind'] != 'BOOK' or row['event_id'] != event_id or row['sha256'] != value['book']['book_sha256']
                or not latest or latest['id'] != row['id'] or b['payload'].get('stream_healthy') is not True
                or b['observed_at'] is None or b['available_at'] > now
                or not 0 <= now-b['observed_at'] < age or not 0 <= now-b['received_at'] < age):
            raise EvidenceError('CURRENT_EXACT_BOOK_REVALUATION_REQUIRED')
        return (('BOOK', event_id, head['seq'] if head else 0),)

    def _source_release(self, key, *, strategies, context, rule, binding, admissions, event_state_id, value):
        if not {'SOURCE_SHOCK', 'RELEASE_OPPORTUNITY'} & set(strategies):
            if key is not None:
                raise EvidenceError('SOURCE_RELEASE_ATTRIBUTION_REQUIRED')
            return None
        if key is None:
            raise EvidenceError('RECEIVED_SOURCE_RELEASE_PIN_REQUIRED')
        from .source_release import SourceRelease
        release = SourceRelease(self.store).revalidate(key, context=context, rule=rule, binding=binding,
                   admission_ids=admissions, event_state_id=event_state_id, book_id=value['book']['book_id'], strategies=strategies)
        prediction = value['model']['prediction']
        if (prediction['as_of'] < release['received_at']
                or sorted(m['evidence_sha256'] for m in prediction['model_inputs']) != release['model_input_sha256']):
            raise EvidenceError('RELEASE_VALUATION_REQUIRES_POST_RECEIPT_MODEL_INPUTS')
        return release

    @precise
    def _prepare(self, proposal, now):
        if isinstance(proposal, BasketProposal):
            from .basket_coordinator import prepare
            return prepare(self, proposal, now)
        if proposal.context.account_id != self.policy.account_id:
            raise EvidenceError('PROPOSAL_ACCOUNT_MISMATCH')
        membership = next((m for m in self.correlation.memberships if m.station == proposal.context.station_id), None)
        if (membership is None or membership.city != proposal.context.city_id
                or membership.metadata_fingerprint != proposal.rule.payload['metadata_fingerprint']):
            raise EvidenceError('PROPOSAL_CITY_METADATA_SCOPE_MISMATCH')
        # Read reduction heads BEFORE validation; a racing new reduction then
        # invalidates either the state pin or the final transaction's head guards.
        heads = SafetyReductions(self.store).atomic_heads(proposal.context)
        queue = event_queue_admission(self.store, event_id=proposal.context.event_id, valuation_id=proposal.valuation_id)
        heads += tuple(tuple(h) for h in queue['heads'])
        event = EventRiskEngine(self.store).revalidate(proposal.event_state_id)
        event_row = self.store.get(proposal.event_state_id)
        heads += (('COORDINATOR_EVENT', event_row['event_id'], event_row['seq']),)
        if event['request']['context'] != asdict(proposal.context):
            raise EvidenceError('PROPOSAL_EVENT_CONTEXT_MISMATCH')
        value_row = self.store.get(proposal.valuation_id)
        value = value_row['body'].get('details', {})
        if (value_row['kind'] != 'MEASUREMENT' or value_row['event_id'] != proposal.context.event_id
                or value.get('version') != EV_VERSION or value['binding'] != event['request']['binding']
                or value['binding']['rule_fingerprint'] != proposal.rule.sha256
                or value['collateral_asset'] != self.policy.collateral_asset):
            raise EvidenceError('PROPOSAL_VALUATION_BINDING')
        strategies = tuple(a.strategy for a in proposal.attribution)
        admissions = [StrategyAdmission(self.store).revalidate(key, context=proposal.context, rule=proposal.rule,
                       binding=value['binding'], strategies=strategies) for key in proposal.admission_ids]
        if {a['strategy'] for a in admissions} != set(strategies):
            raise EvidenceError('EVERY_ATTRIBUTED_STRATEGY_REQUIRES_SCOPED_ADMISSION')
        for admission in admissions:
            heads += tuple(tuple(h) for h in admission['heads'])
        preconfirmation = self._preconfirmation(proposal.preconfirmation_id, strategies=strategies,
                     context=proposal.context, rule=proposal.rule, binding=value['binding'], admissions=proposal.admission_ids)
        if preconfirmation is not None:
            heads += tuple(tuple(h) for h in preconfirmation['heads'])
        release = self._source_release(proposal.source_release_id, strategies=strategies, context=proposal.context,
                     rule=proposal.rule, binding=value['binding'], admissions=proposal.admission_ids,
                     event_state_id=proposal.event_state_id, value=value)
        if release is not None:
            heads += tuple(tuple(h) for h in release['heads'])
        heads += self._current_book_heads(value, proposal.context.event_id)
        if value['valuation_type'] == 'SETTLEMENT' and value['outcome'] == 'ACCEPT_RESEARCH':
            direction, ev, cost_rows = 'BUY', value['conservative_ev_per_share'], value['costs']
        elif value['valuation_type'] == 'EXIT_COMPARISON' and value['outcome'] == 'REDUCE_RESEARCH_CANDIDATE':
            direction, ev, cost_rows = 'SELL', value['sale_advantage_per_share'], value['sale_costs']
            from .position_management import revalidate_exit
            heads += revalidate_exit(self, proposal, value)
        else:
            raise EvidenceError('PROPOSAL_ECONOMICS_NOT_QUALIFIED')
        target = contract_target(proposal.rule, value['target']['market_id'], value['target']['side'])
        if value['target'] != target:
            raise EvidenceError('PROPOSAL_EXACT_TARGET_MISMATCH')
        flags = event['safety']['flags']
        if flags['no_new_orders'] or flags['manual_review'] or flags['quarantined']:
            raise EvidenceError('OPERATOR_SUPPRESSES_NEW_ORDER')
        directional = release is not None and release['directional_event_data_eligible']
        if direction == 'BUY' and (flags['reduce_only'] or not (event['ordinary_new_risk_research_allowed'] or directional)):
            raise EvidenceError('EVENT_OR_OPERATOR_SUPPRESSES_NEW_RISK')
        quantity = number(value['units'])
        model_size = min(Decimal(str(a['model_size_multiplier'])) for a in admissions)
        if preconfirmation is not None:
            model_size = min(model_size, Decimal(str(preconfirmation['model_size_multiplier'])))
        if quantity > number(self.limits.max_position_units)*Decimal(str(event['guard']['size_multiplier']))*model_size:
            raise EvidenceError('EVENT_STATE_SIZE_LIMIT')
        minimum_ev = self.policy_amount('minimum_ev_per_share')+Decimal(str(event['guard']['additional_ev_per_share']))
        if number(ev) <= minimum_ev or not cost_rows['complete']:
            raise EvidenceError('STATE_ADJUSTED_EV_NOT_ABOVE_THRESHOLD')
        book = self.store.get(value['book']['book_id'])
        if book['sha256'] != value['book']['book_sha256'] or book['body']['payload'].get('stream_healthy') is not True:
            raise EvidenceError('PROPOSAL_BOOK_PIN_INVALID')
        book_age = value['policy']['max_book_age_seconds']
        bound_at = value['model']['prediction']['as_of']+value['policy']['max_prediction_age_seconds']
        expiry = min(proposal.expires_at, value['as_of']+self.policy.maximum_intent_lifetime_seconds*
                     event['guard']['lifetime_multiplier'], event['valid_until'], bound_at,
                     book['body']['observed_at']+book_age, book['body']['received_at']+book_age)
        expiry = min(expiry, *(a['valid_until'] for a in admissions))
        if preconfirmation is not None:
            expiry = min(expiry, preconfirmation['valid_until'])
        if release is not None:
            expiry = min(expiry, release['valid_until'])
        if queue['valid_until'] is not None:
            expiry = min(expiry, queue['valid_until'])
        if not value['as_of'] <= now < expiry:
            raise EvidenceError('PROPOSAL_EVIDENCE_OR_SIGNAL_EXPIRED')
        if any(c['valid_until'] is not None and now > c['valid_until'] for c in cost_rows['components']):
            raise EvidenceError('PROPOSAL_COST_EVIDENCE_EXPIRED')
        price = number(value['book']['worst_consumed_price'])
        costs = number(cost_rows['known_total_per_share'])
        bound = price+costs if direction == 'BUY' else price-costs
        if bound < 0 or bound > 2:
            raise EvidenceError('PROPOSAL_COLLATERAL_BOUND')
        levels = book['body']['payload']['asks' if direction == 'BUY' else 'bids']
        supported = sum(number(l['size']) for l in levels if (number(l['price']) <= price if direction == 'BUY' else number(l['price']) >= price))
        if supported < quantity*Decimal(str(event['guard']['liquidity_multiplier'])):
            raise EvidenceError('STATE_ADJUSTED_LIQUIDITY_INSUFFICIENT')
        capital = quantity*bound if direction == 'BUY' else quantity
        if direction == 'BUY' and capital > self.policy_amount('per_intent_cash_limit'):
            raise EvidenceError('PER_INTENT_CASH_LIMIT')
        candidate = dict(proposal_id=proposal.proposal_id, thesis_id=proposal.thesis_id,
                         desired_total_units=proposal.desired_total_units,
                         event_id=proposal.context.event_id, token_id=target['token_id'], target=target,
                         direction=direction, units=str(quantity), filled_units='0', unit_collateral_bound=str(bound),
                         attribution=[asdict(a) for a in proposal.attribution], expires_at=expiry,
                         valuation_id=proposal.valuation_id, event_state_id=proposal.event_state_id,
                         admission_ids=list(proposal.admission_ids), binding=value['binding'],
                         preconfirmation_id=proposal.preconfirmation_id,
                         source_release_id=proposal.source_release_id,
                         event_queue_completion_id=queue['completion_id'],
                         rule_fingerprint=proposal.rule.sha256, conservative_ev_total=str(number(ev)*quantity),
                         capital_at_risk=str(capital), status='RESERVED', cancel_requested=False, financial_authority=False)
        return candidate, heads

    def coordinate(self, batch_id: str, proposals: tuple[Proposal | BasketProposal, ...]) -> dict:
        if type(proposals) is not tuple or not 1 <= len(proposals) <= 6:
            raise EvidenceError('COORDINATOR_BATCH_BOUND')
        if len({p.proposal_id for p in proposals}) != len(proposals):
            raise EvidenceError('DUPLICATE_PROPOSAL_ID')
        request = dict(action='COORDINATE', proposals=[asdict(p) for p in proposals])
        replay = self._replay(batch_id, request)
        if replay:
            return replay
        row = self._head(); state = self._state(row); now = finite(self.store.clock())
        evaluated, prepared, guards, references = [], [], {}, []
        receipt_heads, receipt_seq = receipt_admission(self.store, account_id=self.policy.account_id,
            account_policy_sha=self.policy_sha, required_config=self.reconciliation_config)
        guards.update({(k, e): n for k, e, n in receipt_heads})
        for proposal in proposals:
            try:
                try:
                    self.store.get(proposal.valuation_id)
                    references.append(proposal.valuation_id)
                except EvidenceError as exc:
                    if str(exc) != 'EVIDENCE_MISSING':
                        raise
                if state['faults']:
                    raise EvidenceError('PAPER_ACCOUNT_FAULT_ACTIVE')
                if proposal.proposal_id in state['intents'] or proposal.proposal_id in state.get('baskets', {}):
                    raise EvidenceError('INTENT_ALREADY_RECORDED_RECONCILE_EXISTING_ID')
                runtime_heads = runtime_health_admission(self.store, account_id=self.policy.account_id,
                    event_id=proposal.context.event_id, strategies=tuple(a.strategy for a in proposal.attribution))
                candidate, heads = self._prepare(proposal, now)
                heads = tuple(heads)+runtime_heads
                for kind, event, seq in heads:
                    if (kind, event) in guards and guards[(kind, event)] != seq:
                        raise EvidenceError('BATCH_STATE_CHANGED_RECOMPUTE')
                    guards[(kind, event)] = seq
                prepared.append(candidate)
            except EvidenceError as exc:
                evaluated.append(dict(proposal_id=proposal.proposal_id, outcome='REJECT', reason=str(exc)))
        effects = EffectInputs(self, state)
        effects.preparation = deepcopy(dict(prepared=prepared, rejected=evaluated))
        state, details = self._coordinate_effects(state, proposals, prepared, evaluated, effects)
        return self._commit(batch_id, request, row, state, details,
                            evidence_ids=tuple(dict.fromkeys(references)),
                            heads=tuple((k, e, n) for (k, e), n in guards.items()), receipt_seq=receipt_seq,
                            effect_inputs=effects)

    def _coordinate_effects(self, state, proposals, prepared, evaluated, effects):
        """Shared numerical allocation from original, already prepared inputs.

        Mutates private state only; does not prepare, admit, commit or submit.
        """
        ranking = rank_candidates(tuple(prepared))
        chosen, batch_tokens = [], set()
        by_id = {p.proposal_id: p for p in proposals}
        for candidate in ranking:
            proposal = by_id[candidate['proposal_id']]
            reason = None
            legs = candidate.get('basket_legs', [candidate])
            if any(leg['token_id'] in batch_tokens for leg in legs):
                reason = 'TOKEN_CONFLICT_OR_DUPLICATE_KEEP_STRONGER_PROPOSAL'
            if any(p['thesis_id'] == candidate['thesis_id'] and p['event_id'] == candidate['event_id']
                   for p in state['intents'].values()):
                reason = 'THESIS_ALREADY_HAS_ECONOMIC_INTENT'
            test = deepcopy(state)
            event = candidate['event_id']
            if event in test['rules'] and test['rules'][event]['sha256'] != proposal.rule.sha256:
                reason = 'RULE_CHANGED_RECONCILIATION_REQUIRED'
            if event in test['contexts'] and test['contexts'][event] != asdict(proposal.context):
                reason = 'EVENT_CONTEXT_CHANGED_REVIEW_REQUIRED'
            for leg in legs:
                if leg['proposal_id'] in test['intents'] or leg['proposal_id'] in test.get('baskets', {}):
                    reason = 'INTENT_ALREADY_RECORDED_RECONCILE_EXISTING_ID'
                held = sum(number(p['units']) for p in state['lots'].values() if p['token_id'] == leg['token_id'])
                reserved = sum(number(p['units'])-number(p['filled_units']) for p in state['intents'].values()
                               if p['token_id'] == leg['token_id'] and p['direction'] == leg['direction']
                               and p['status'] in UNRESOLVED)
                desired = number(leg['desired_total_units'])
                available_delta = desired-held-reserved if leg['direction'] == 'BUY' else held-reserved-desired
                if reason is None and (available_delta <= 0 or number(leg['units']) > available_delta):
                    reason = 'DESIRED_POSITION_ALREADY_COVERED_OR_REVALUE_SMALLER_DELTA'
                if isinstance(proposal, BasketProposal) and any(
                        p['token_id'] == leg['token_id'] and p['direction'] != leg['direction'] and p['status'] in UNRESOLVED
                        for p in state['intents'].values()):
                    reason = 'BASKET_OPPOSING_PENDING_INTENT_REQUIRES_REPLAN'
            if reason is None and candidate['direction'] == 'SELL':
                # A different candidate in this same batch may already have
                # reserved a hedge. Recompute against the proposed account state,
                # not just the pre-batch inventory used during ranking.
                try:
                    effects.exit_check(proposal, test)
                except EvidenceError as exc:
                    reason = str(exc)
            if reason is None:
                test['rules'][event] = asdict(proposal.rule)
                test['contexts'][event] = asdict(proposal.context)
                for leg in legs:
                    test['intents'][leg['proposal_id']] = leg
                if isinstance(proposal, BasketProposal):
                    test.setdefault('baskets', {})[proposal.proposal_id] = dict(
                        proposal=asdict(proposal), intent_ids=[leg['proposal_id'] for leg in legs],
                        conservative_ev_total=candidate['conservative_ev_total'], financial_authority=False)
                try:
                    assessed = effects.risk(test)
                    if not assessed['accepted']:
                        reason = 'ACCOUNT_SCENARIO_OR_RESERVATION_LIMIT'
                    elif effects.reduce_only(proposal.context):
                        before = effects.risk(state)
                        before_loss = Decimal(before['portfolio']['groups']['PORTFOLIO'].get('ALL', '0'))
                        after_loss = Decimal(assessed['portfolio']['groups']['PORTFOLIO'].get('ALL', '0'))
                        if candidate['direction'] != 'SELL' or after_loss > before_loss:
                            reason = 'REDUCE_ONLY_CANNOT_INCREASE_SCENARIO_LOSS'
                except EvidenceError as exc:
                    reason = str(exc)
            if reason is None:
                state = test
                chosen.extend(leg['proposal_id'] for leg in legs)
                batch_tokens.update(leg['token_id'] for leg in legs)
            evaluated.append(dict(proposal_id=candidate['proposal_id'], outcome='RESERVED_RESEARCH' if reason is None else 'REJECT',
                                  reason=reason or 'ATOMIC_ACCOUNT_CASH_INVENTORY_AND_SCENARIO_RESERVATION'))
        return state, dict(ranking=ranking, results=evaluated, reserved_intent_ids=chosen, risk=effects.risk(state),
                           execution_status='NOT_SUBMITTED', economic_attribution_is_not_multiple_fills=True)

    def transition(self, command_id: str, *, intent_id: str, status: str, expected_cancel_identity: str | None = None) -> dict:
        if status not in {'SUBMITTING', 'UNKNOWN', 'ACKNOWLEDGED', 'CANCEL_REQUESTED'}:
            raise EvidenceError('NONTERMINAL_RESEARCH_TRANSITION_REQUIRED')
        request = dict(action='TRANSITION', intent_id=intent_id, status=status)
        if expected_cancel_identity is not None:
            if (status != 'CANCEL_REQUESTED' or not isinstance(expected_cancel_identity, str)
                    or len(expected_cancel_identity) != 64 or any(c not in '0123456789abcdef' for c in expected_cancel_identity)):
                raise EvidenceError('CANCEL_IDENTITY_PIN_ONLY_FOR_CANCELLATION')
            request['expected_cancel_identity'] = expected_cancel_identity
        replay = self._replay(command_id, request)
        if replay:
            return replay
        row = self._head(); state = self._state(row)
        intent = self._transition_intent(state, request)
        heads = (); receipt_seq = None
        if status == 'SUBMITTING':
            heads, receipt_seq = receipt_admission(self.store, account_id=self.policy.account_id,
                account_policy_sha=self.policy_sha, required_config=self.reconciliation_config)
        if status == 'SUBMITTING' and intent.get('basket_id') is not None:
            from .basket_coordinator import submission_heads
            heads += submission_heads(self, state, intent)
            unique = {}
            for kind, event_id, seq in heads:
                if (kind, event_id) in unique and unique[(kind, event_id)] != seq:
                    raise EvidenceError('SUBMISSION_AUTHORITY_CHANGED_RECOMPUTE')
                unique[(kind, event_id)] = seq
            heads = tuple((k, e, seq) for (k, e), seq in unique.items())
        elif status == 'SUBMITTING':
            context = EventContext(**state['contexts'][intent['event_id']])
            heads += SafetyReductions(self.store).atomic_heads(context)
            queue = event_queue_admission(self.store, event_id=intent['event_id'], valuation_id=intent['valuation_id'])
            heads += tuple(tuple(h) for h in queue['heads'])
            if queue['completion_id'] != intent.get('event_queue_completion_id'):
                raise EvidenceError('EVENT_QUEUE_EVALUATION_CHANGED_RECOMPUTE')
            event = EventRiskEngine(self.store).revalidate(intent['event_state_id'])
            event_row = self.store.get(intent['event_state_id'])
            heads += (('COORDINATOR_EVENT', event_row['event_id'], event_row['seq']),)
            strategies = tuple(a['strategy'] for a in intent['attribution'])
            rule = self._rule(state['rules'][intent['event_id']])
            for key in intent['admission_ids']:
                admission = StrategyAdmission(self.store).revalidate(key, context=context, rule=rule,
                              binding=intent['binding'], strategies=strategies)
                heads += tuple(tuple(h) for h in admission['heads'])
            preconfirmation = self._preconfirmation(intent.get('preconfirmation_id'), strategies=strategies,
                         context=context, rule=rule, binding=intent['binding'], admissions=tuple(intent['admission_ids']))
            if preconfirmation is not None:
                heads += tuple(tuple(h) for h in preconfirmation['heads'])
            value = self.store.get(intent['valuation_id'])['body']['details']
            release = self._source_release(intent.get('source_release_id'), strategies=strategies, context=context,
                         rule=rule, binding=intent['binding'], admissions=tuple(intent['admission_ids']),
                         event_state_id=intent['event_state_id'], value=value)
            if release is not None:
                heads += tuple(tuple(h) for h in release['heads'])
            heads += self._current_book_heads(value, intent['event_id'])
            if intent['direction'] == 'SELL':
                from .position_management import revalidate_exit
                proposal = Proposal(intent['proposal_id'], intent['thesis_id'], context, rule,
                        intent['valuation_id'], intent['event_state_id'],
                        tuple(Attribution(**a) for a in intent['attribution']), intent['expires_at'],
                        intent['desired_total_units'], tuple(intent['admission_ids']),
                        intent.get('preconfirmation_id'), intent.get('source_release_id'))
                heads += revalidate_exit(self, proposal, value, state=state, own_intent_id=intent['proposal_id'])
            # Shared station/source scopes may be referenced by several sleeves.
            unique = {}
            for kind, event_id, seq in heads:
                if (kind, event_id) in unique and unique[(kind, event_id)] != seq:
                    raise EvidenceError('SUBMISSION_AUTHORITY_CHANGED_RECOMPUTE')
                unique[(kind, event_id)] = seq
            heads = tuple((k, e, seq) for (k, e), seq in unique.items())
            flags = event['safety']['flags']
            if (state['faults'] or finite(self.store.clock()) >= intent['expires_at']
                    or flags['no_new_orders'] or flags['manual_review'] or flags['quarantined']
                    or (intent['direction'] == 'BUY' and (flags['reduce_only'] or not (event['ordinary_new_risk_research_allowed']
                         or release is not None and release['directional_event_data_eligible'])))):
                raise EvidenceError('SUBMISSION_PIN_EXPIRED_OR_SUPPRESSED')
        if status == 'SUBMITTING':
            heads += runtime_health_admission(self.store, account_id=self.policy.account_id,
                event_id=intent['event_id'], strategies=tuple(a['strategy'] for a in intent['attribution']))
            heads = tuple(dict.fromkeys(heads))
        effects = EffectInputs(self, state)
        details = self._transition_effects(state, request, effects)
        return self._commit(command_id, request, row, state, details,
                            heads=heads, receipt_seq=receipt_seq, effect_inputs=effects)

    @staticmethod
    def _transition_intent(state, request):
        intent_id, status = request['intent_id'], request['status']
        expected_cancel_identity = request.get('expected_cancel_identity')
        if status not in {'SUBMITTING', 'UNKNOWN', 'ACKNOWLEDGED', 'CANCEL_REQUESTED'}:
            raise EvidenceError('NONTERMINAL_RESEARCH_TRANSITION_REQUIRED')
        if expected_cancel_identity is not None and status != 'CANCEL_REQUESTED':
            raise EvidenceError('CANCEL_IDENTITY_PIN_ONLY_FOR_CANCELLATION')
        intent = state['intents'].get(intent_id)
        if intent is None or intent['status'] not in UNRESOLVED:
            raise EvidenceError('UNRESOLVED_INTENT_REQUIRED')
        if expected_cancel_identity is not None and cancel_identity(intent) != expected_cancel_identity:
            raise EvidenceError('PAPER_CANCEL_MANAGED_IDENTITY_CHANGED')
        if status == 'SUBMITTING' and intent['status'] != 'RESERVED':
            raise EvidenceError('AMBIGUOUS_SUBMISSION_CANNOT_BE_RETRIED_AS_NEW')
        if status in {'UNKNOWN', 'ACKNOWLEDGED'} and intent['status'] == 'RESERVED':
            raise EvidenceError('SUBMISSION_NOT_STARTED')
        return intent

    def _transition_effects(self, state, request, effects):
        intent = self._transition_intent(state, request)
        status = request['status']
        if status == 'CANCEL_REQUESTED':
            intent['cancel_requested'] = True
        intent['status'] = ('CANCEL_REQUESTED' if intent['cancel_requested'] else
                            'PARTIAL' if status == 'ACKNOWLEDGED' and number(intent['filled_units']) > 0 else status)
        return dict(risk=effects.risk(state), reservation_released=False, real_submission_performed=False)

    def recover(self, command_id: str) -> dict:
        request = dict(action='RECOVER')
        replay = self._replay(command_id, request)
        if replay:
            return replay
        row = self._head(); state = self._state(row)
        effects = EffectInputs(self, state)
        details = self._recover_effects(state, effects)
        return self._commit(command_id, request, row, state, details, effect_inputs=effects)

    @staticmethod
    def _recover_effects(state, effects):
        for intent in state['intents'].values():
            if intent['status'] == 'SUBMITTING':
                intent['status'] = 'UNKNOWN'
        return dict(risk=effects.risk(state), reservation_released=False, ambiguous_resubmission_allowed=False)

    def _proof(self, evidence_id, state, record_type):
        row = self.store.get(evidence_id); b = row['body']; p = b.get('payload', {})
        if (row['kind'] != 'TRADE' or b.get('evidence_class') != 'SYNTHETIC'
                or p.get('execution_namespace') != self.store.namespace or p.get('account_id') != self.policy.account_id
                or p.get('record_type') != record_type):
            raise EvidenceError('EXPLICIT_SYNTHETIC_PAPER_RECONCILIATION_REQUIRED')
        intent = state['intents'].get(p.get('intent_id'))
        if intent is None or row['event_id'] != intent['event_id'] or p.get('token_id') != intent['token_id']:
            raise EvidenceError('RECONCILIATION_INTENT_TARGET_MISMATCH')
        return row, p, intent

    def record_fill(self, command_id: str, evidence_id: str) -> dict:
        request = dict(action='FILL', evidence_id=evidence_id)
        replay = self._replay(command_id, request)
        if replay:
            return replay
        row = self._head(); state = self._state(row)
        effects = EffectInputs(self, state)
        details, refs = self._fill_effects(state, evidence_id, effects)
        return self._commit(command_id, request, row, state, details, evidence_ids=refs, effect_inputs=effects)

    def _fill_effects(self, state, evidence_id, effects):
        proof, payload, intent = self._proof(evidence_id, state, 'PAPER_FILL')
        fill_id = identity(payload['fill_id'])
        if fill_id in state['fills']:
            if state['fills'][fill_id] != proof['sha256']:
                raise EvidenceError('FILL_ID_CONFLICT')
            return dict(duplicate_fill=True, risk=effects.risk(state)), ()
        quantity, collateral = number(payload['units']), number(payload['all_in_collateral'])
        remaining = number(intent['units'])-number(intent['filled_units'])
        if quantity <= 0 or quantity > remaining or payload.get('direction') != intent['direction']:
            raise EvidenceError('PAPER_FILL_QUANTITY_OR_DIRECTION_INVALID')
        if intent['status'] in TERMINAL:
            state['faults'].append('LATE_FILL_AFTER_TERMINAL_RECONCILIATION')
        with localcontext() as context:
            context.prec = 80
            event = intent['event_id']
            if intent['direction'] == 'BUY':
                state['cash'] = str(number(state['cash'], signed=True)-collateral)
                state['lots'][fill_id] = dict(lot_id=fill_id, token_id=intent['token_id'], event_id=event,
                                             units=str(quantity), all_in_cost_basis=str(collateral), attribution=intent['attribution'],
                                             entry=intent_lineage(self.store, intent), acquired_sequence=proof['seq'])
                if collateral > quantity*number(intent['unit_collateral_bound']):
                    state['faults'].append('ACTUAL_PAPER_COST_EXCEEDED_RESERVED_BOUND')
            else:
                held = sum(number(p['units']) for p in state['lots'].values() if p['token_id'] == intent['token_id'])
                if quantity > held:
                    raise EvidenceError('PAPER_SALE_EXCEEDS_ACTUAL_HELD_INVENTORY')
                basis, allocations = consume_lots(state['lots'], token_id=intent['token_id'],
                                                   quantity=quantity, net_proceeds=collateral)
                state['cash'] = str(number(state['cash'], signed=True)+collateral)
                pnl = collateral-basis
                state['event_realized_pnl'][event] = str(number(state['event_realized_pnl'].get(event, '0'), signed=True)+pnl)
                state['realized_entries'].append(dict(fill_id=fill_id, event_id=event, pnl=str(pnl), at=effects.clock(),
                          exit=intent_lineage(self.store, intent), allocations=allocations,
                          attribution_basis='ENTRY_LOTS_PARTITION_ONE_REALIZED_RESULT',
                          exit_attribution_is_decision_metadata_not_extra_pnl=True))
                if collateral < quantity*number(intent['unit_collateral_bound']):
                    state['faults'].append('ACTUAL_PAPER_SALE_BELOW_RESERVED_BOUND')
            intent['filled_units'] = str(number(intent['filled_units'])+quantity)
            intent['status'] = ('FILLED' if number(intent['filled_units']) == number(intent['units']) else
                                'CANCEL_REQUESTED' if intent['cancel_requested'] else 'PARTIAL')
            state['fills'][fill_id] = proof['sha256']
        risk = effects.risk(state)
        if risk['faults']:
            state['faults'].append('POST_FILL_ACCOUNT_RISK_BREACH')
        state['faults'] = sorted(set(state['faults']))
        extra={}
        if 'execution_details' in payload:
            # The existing fill proof controls ledger reconciliation. Optional
            # metric metadata must never hide a known position or cash movement.
            from .fill_evidence import execution_details
            try:
                extra['execution_evidence']=execution_details(self.store,proof,intent,
                    account_id=self.policy.account_id,collateral_asset=self.policy.collateral_asset)
            except (EvidenceError,KeyError,TypeError,ValueError) as exc:
                extra['execution_evidence']=dict(status='GATED',
                    reason=str(exc) if isinstance(exc,EvidenceError) else 'FILL_EXECUTION_DETAILS_MALFORMED',
                    reconciliation_preserved=True,financial_authority=False)
        return dict(risk=risk, duplicate_fill=False, realized_pnl_class='SYNTHETIC_PAPER_ONLY', **extra), (evidence_id,)

    def reconcile_terminal(self, command_id: str, evidence_id: str) -> dict:
        request = dict(action='TERMINAL', evidence_id=evidence_id)
        replay = self._replay(command_id, request)
        if replay:
            return replay
        row = self._head(); state = self._state(row)
        effects = EffectInputs(self, state)
        details = self._terminal_effects(state, evidence_id, effects)
        return self._commit(command_id, request, row, state, details, evidence_ids=(evidence_id,), effect_inputs=effects)

    def _terminal_effects(self, state, evidence_id, effects):
        _, payload, intent = self._proof(evidence_id, state, 'PAPER_TERMINAL')
        if (payload.get('status') not in TERMINAL or payload.get('all_fills_reconciled') is not True
                or payload.get('terminal_authority') != 'SYNTHETIC_PAPER_ENGINE_FINAL'
                or number(payload.get('cumulative_fill_units')) != number(intent['filled_units'])):
            raise EvidenceError('TERMINAL_REQUIRES_COMPLETE_MATCHED_FILL_RECONCILIATION')
        if payload['status'] == 'FILLED' and number(intent['filled_units']) != number(intent['units']):
            raise EvidenceError('TERMINAL_FILLED_QUANTITY_MISMATCH')
        if (payload['status'] == 'REJECTED' and number(intent['filled_units']) > 0) or (
                intent['status'] in TERMINAL and intent['status'] != payload['status']):
            raise EvidenceError('TERMINAL_STATE_CONFLICT')
        intent['status'] = payload['status']
        return dict(risk=effects.risk(state), reservation_released=True, existing_fills_preserved=True)
