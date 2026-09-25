"""Bounded cancel-request delivery and telemetry in the common PAPER account.

No exchange transport or order-opening API. This is an off-host integration, not
the independently isolated production guardian or authenticated live cancellation.
"""
from copy import deepcopy
from dataclasses import asdict, dataclass

from .event_risk import VERSION as EVENT_VERSION, EventContext
from .evidence import EvidenceError, canonical, digest, finite, identity
from .paper_coordinator import ACCOUNT_KEY, UNRESOLVED, TERMINAL, VERSION as ACCOUNT_VERSION, cancel_identity
from .runtime_health import VERSION as HEALTH_VERSION, KEY as HEALTH_KEY, cancellation_required
from .rules import GUARD_VERSION
from .strategy_admission import StrategyAdmission


VERSION = 'alpha_v11_paper_cancellation_v1'
ADMISSION_VERSION = 'alpha_v11_resting_admission_check_v1'


def managed_opening(intent):
    return intent['direction'] == 'BUY' or any(a['strategy'] == 'MAKER_RESEARCH' for a in intent['attribution'])


@dataclass(frozen=True)
class CancellationPolicy:
    version: str
    maximum_requests_per_cycle: int
    maximum_plan_intents: int

    def __post_init__(self):
        identity(self.version)
        if (type(self.maximum_requests_per_cycle) is not int or not 1 <= self.maximum_requests_per_cycle <= 16
                or type(self.maximum_plan_intents) is not int
                or not self.maximum_requests_per_cycle <= self.maximum_plan_intents <= 256):
            raise EvidenceError('PAPER_CANCELLATION_POLICY_BOUND')


class PaperCancellation:
    def __init__(self, coordinator, policy):
        if not isinstance(policy, CancellationPolicy):
            raise EvidenceError('PAPER_CANCELLATION_POLICY_REQUIRED')
        self.coordinator = coordinator; self.store = coordinator.store; self.policy = policy
        self.policy_sha = digest(dict(account=coordinator.policy_sha, cancellation=asdict(policy)))

    def _key(self, plan_id):
        return 'paper-cancellation:'+digest([self.store.namespace, self.coordinator.policy.account_id, plan_id])

    def _head(self, plan_id):
        row = self.store.latest(kind='MEASUREMENT', event_id=self._key(plan_id))
        if row and (row['body']['details'].get('version') != VERSION
                    or row['body']['details'].get('policy_sha256') != self.policy_sha):
            raise EvidenceError('PAPER_CANCELLATION_POLICY_CHANGED')
        return row

    def _replay(self, key, request, plan_id):
        identity(key, maximum=100)
        try:
            row = self.store.get(key)
        except EvidenceError as exc:
            if str(exc) == 'EVIDENCE_MISSING': return None
            raise
        d = row['body'].get('details', {})
        if (row['kind'] != 'MEASUREMENT' or row['event_id'] != self._key(plan_id)
                or d.get('version') != VERSION or d.get('policy_sha256') != self.policy_sha
                or canonical(d.get('request')) != canonical(request)):
            raise EvidenceError('PAPER_CANCELLATION_REPLAY_CONFLICT')
        return row

    def _commit(self, key, request, previous, state, *, refs=(), heads=(), **details):
        return self.store.safety_audit(key, event_id=self._key(state['plan_id']), kind='MEASUREMENT', details=dict(
            version=VERSION, policy_sha256=self.policy_sha, request=request, state=state,
            execution_namespace=self.store.namespace, account_id=self.coordinator.policy.account_id,
            financial_authority=False, real_cancel_sent=False, orders_created=False, replacement_orders_created=False,
            inventory_exit_performed=False, independent_guardian_commissioned=False, **details),
            evidence_ids=tuple(dict.fromkeys(refs)), expected_previous_seq=previous['seq'] if previous else 0,
            expected_heads=tuple(heads))

    def check_admission(self, key, *, intent_id):
        """Read existing authority; request withdrawal only, never restore a pin.

        This also revalidates the separate PWS observation admission. A healthy
        result is a point observation, not renewed eligibility or an order lease.
        The account snapshot/signature prevents a delayed check targeting a new
        intent. Terminal receipts and fills remain the coordinator's concern.
        """
        identity(key, maximum=100); identity(intent_id)
        request = dict(action='CHECK_RESTING_ADMISSION', intent_id=intent_id)
        event = 'resting-admission:'+digest([self.store.namespace, self.coordinator.policy.account_id, intent_id])
        try:
            prior = self.store.get(key)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING': raise
        else:
            d = prior['body'].get('details', {})
            if (prior['kind'] != 'MEASUREMENT' or prior['event_id'] != event
                    or d.get('version') != ADMISSION_VERSION or d.get('policy_sha256') != self.policy_sha
                    or d.get('request') != request):
                raise EvidenceError('RESTING_ADMISSION_REPLAY_CONFLICT')
            return prior
        head = self.coordinator._head(); account = self.coordinator._state(head)
        intent = account['intents'].get(intent_id)
        if intent is None or intent['status'] not in UNRESOLVED or not managed_opening(intent):
            raise EvidenceError('RESTING_MANAGED_OPENING_REQUIRED')
        context = EventContext(**account['contexts'][intent['event_id']])
        rule = self.coordinator._rule(account['rules'][intent['event_id']])
        if context.account_id != self.coordinator.policy.account_id:
            raise EvidenceError('PAPER_CANCEL_ACCOUNT_MISMATCH')
        strategies = tuple(a['strategy'] for a in intent['attribution'])
        reason = None
        try:
            if finite(self.store.clock()) >= intent['expires_at']:
                raise EvidenceError('RESTING_INTENT_EXPIRED')
            admissions = [StrategyAdmission(self.store).revalidate(ref, context=context, rule=rule,
                binding=intent['binding'], strategies=strategies) for ref in intent['admission_ids']]
            if not admissions or {a['strategy'] for a in admissions} != set(strategies):
                raise EvidenceError('RESTING_ADMISSION_STRATEGIES_MISMATCH')
            self.coordinator._preconfirmation(intent.get('preconfirmation_id'), strategies=strategies,
                context=context, rule=rule, binding=intent['binding'], admissions=tuple(intent['admission_ids']))
        except (EvidenceError, OSError) as exc:
            reason = str(exc) if isinstance(exc, EvidenceError) else 'RESTING_AUTHORITY_UNAVAILABLE'
        return self.store.safety_audit(key, event_id=event, kind='MEASUREMENT', details=dict(
            version=ADMISSION_VERSION, policy_sha256=self.policy_sha, request=request,
            account_id=self.coordinator.policy.account_id, account_snapshot_id=head['id'],
            intent_id=intent_id, intent_signature=cancel_identity(intent),
            admission_ids=intent['admission_ids'], preconfirmation_id=intent.get('preconfirmation_id'),
            passed=reason is None, reason=reason or 'RESTING_ADMISSION_VALID_AT_CHECK',
            cancellation_status='REQUESTED_NOT_CONFIRMED' if reason else 'NOT_REQUESTED',
            financial_authority=False, authority_restored=False, independent_guardian_commissioned=False),
            evidence_ids=(head['id'],), expected_heads=(('COORDINATOR_EVENT', ACCOUNT_KEY, head['seq']),))

    def plan(self, key, *, trigger_id):
        """Freeze existing managed intents affected by a recorded safety check."""
        request = dict(action='PLAN', trigger_id=identity(trigger_id))
        prior = self._replay(key, request, key)
        if prior: return prior
        source = self.store.get(trigger_id); d = source['body'].get('details', {}); r = d.get('request', {})
        health_trigger = source['kind'] == 'RUNTIME_STATUS' and source['event_id'] == HEALTH_KEY and d.get('version') == HEALTH_VERSION
        from .guardian_lease import VERSION as GUARDIAN_VERSION, journal_key, validate_details
        guardian_trigger = source['kind'] == 'RUNTIME_STATUS' and d.get('version') == GUARDIAN_VERSION
        admission_trigger = (source['kind'] == 'MEASUREMENT' and d.get('version') == ADMISSION_VERSION
                             and d.get('passed') is False and d.get('cancellation_status') == 'REQUESTED_NOT_CONFIRMED')
        rule_trigger = (source['kind'] == 'RULE_STATE' and d.get('version') in {None, GUARD_VERSION}
                        and d.get('quarantined') is True and d.get('cancel_managed_new_risk_requested') is True
                        and d.get('preimage', {}).get('event_id') == source['event_id'])
        if not guardian_trigger and not health_trigger and not rule_trigger and not admission_trigger and (d.get('version') != EVENT_VERSION or d.get('cancellation_status') != 'REQUESTED_NOT_CONFIRMED'):
            raise EvidenceError('RECORDED_CANCELLATION_TRIGGER_REQUIRED')
        trigger_head = self.store.latest(kind=source['kind'], event_id=source['event_id'])
        passive_or_new_risk_only = False; superseded_event = False
        if guardian_trigger:
            validate_details(source['event_id'], d)
            if (source['event_id'] != journal_key(self.coordinator.policy.account_id)
                    or d['account_policy_sha256'] != self.coordinator.policy_sha
                    or d['status'] != 'GATED' or not d['cancel_intents'] or d['pending_trigger'] != source['id']):
                raise EvidenceError('GUARDIAN_CANCEL_BINDING')
            current = trigger_head['body']['details']
            validate_details(trigger_head['event_id'], current)
            if (current['config_sha256'] != d['config_sha256'] or current['pending_trigger'] != source['id']
                    or current['cancel_intents'] != d['cancel_intents']):
                raise EvidenceError('GUARDIAN_PENDING_TRIGGER_CHANGED')
            snapshot = self.store.get(d['account_snapshot_id'])
            if (snapshot['kind'] != 'COORDINATOR_EVENT' or snapshot['event_id'] != ACCOUNT_KEY
                    or snapshot['seq'] >= source['seq'] or snapshot['body']['details'].get('policy_sha256') != self.coordinator.policy_sha):
                raise EvidenceError('GUARDIAN_CANCEL_SNAPSHOT')
            saved = self.coordinator._state(snapshot)
            if any(pid not in saved['intents'] or cancel_identity(saved['intents'][pid]) != signature
                   for pid,signature in d['cancel_intents'].items()):
                raise EvidenceError('GUARDIAN_CANCEL_SIGNATURE')
            scope, scope_id = 'ACCOUNT', self.coordinator.policy.account_id
            passive_or_new_risk_only = True
        elif admission_trigger:
            snapshot = self.store.get(d['account_snapshot_id'])
            saved = self.coordinator._state(snapshot)
            expected_event = 'resting-admission:'+digest([self.store.namespace, self.coordinator.policy.account_id, d['intent_id']])
            if (source['event_id'] != expected_event or d.get('policy_sha256') != self.policy_sha
                    or d.get('account_id') != self.coordinator.policy.account_id
                    or snapshot['kind'] != 'COORDINATOR_EVENT' or snapshot['event_id'] != ACCOUNT_KEY
                    or snapshot['body']['details'].get('policy_sha256') != self.coordinator.policy_sha
                    or snapshot['seq'] >= source['seq'] or d['intent_id'] not in saved['intents']
                    or cancel_identity(saved['intents'][d['intent_id']]) != d.get('intent_signature')):
                raise EvidenceError('RESTING_ADMISSION_CANCEL_BINDING')
            scope, scope_id = 'INTENT', d['intent_id']; passive_or_new_risk_only = True
        elif health_trigger:
            if trigger_head['id'] != trigger_id or d.get('account_id') != self.coordinator.policy.account_id:
                raise EvidenceError('CURRENT_RUNTIME_HEALTH_ACCOUNT_REQUIRED')
            scope, scope_id = 'ACCOUNT', self.coordinator.policy.account_id
        elif rule_trigger:
            scope, scope_id = 'EVENT', source['event_id']; passive_or_new_risk_only = True
            superseded_event = trigger_head['id'] != trigger_id
        elif source['kind'] == 'OPERATOR_EVENT':
            scope, scope_id = r.get('scope'), r.get('scope_id')
            if (d.get('cancellation_request_id') != trigger_id or scope not in {'ACCOUNT', 'CITY', 'STATION', 'EVENT'}
                    or not r.get('action', '').startswith(('CANCEL_', 'QUARANTINE_'))):
                raise EvidenceError('PAPER_CANCEL_OPERATOR_SCOPE_REQUIRED')
        elif source['kind'] == 'COORDINATOR_EVENT':
            if (d.get('state') != 'EVENT'
                    or d.get('cancellation_scope') != 'MANAGED_PASSIVE_AND_NEW_RISK_RESTING_ORDERS'):
                raise EvidenceError('CURRENT_EVENT_CANCELLATION_TRIGGER_REQUIRED')
            superseded_event = trigger_head['id'] != trigger_id
            if superseded_event and trigger_head['body']['details'].get('request', {}).get('context') != r.get('context'):
                raise EvidenceError('RECORDED_EVENT_CANCELLATION_CONTEXT_CHANGED')
            context = EventContext(**r['context'])
            if context.account_id != self.coordinator.policy.account_id:
                raise EvidenceError('PAPER_CANCEL_ACCOUNT_MISMATCH')
            scope, scope_id = 'EVENT', context.event_id; passive_or_new_risk_only = True
        else:
            raise EvidenceError('RECORDED_CANCELLATION_TRIGGER_REQUIRED')
        if scope == 'ACCOUNT' and scope_id != self.coordinator.policy.account_id:
            raise EvidenceError('PAPER_CANCEL_ACCOUNT_MISMATCH')
        account_head = self.coordinator._head(); account = self.coordinator._state(account_head)
        selected = []
        for intent_id, intent in sorted(account['intents'].items()):
            if intent['status'] not in UNRESOLVED: continue
            if guardian_trigger:
                if intent_id not in d['cancel_intents'] or intent.get('cancel_requested') is True: continue
                if cancel_identity(intent) != d['cancel_intents'][intent_id]:
                    raise EvidenceError('PAPER_CANCEL_MANAGED_IDENTITY_CHANGED')
            if health_trigger and intent.get('cancel_requested') is True: continue
            context = EventContext(**account['contexts'][intent['event_id']])
            if context.account_id != self.coordinator.policy.account_id:
                raise EvidenceError('PAPER_CANCEL_ACCOUNT_MISMATCH')
            if scope == 'INTENT':
                if intent_id != scope_id: continue
                if cancel_identity(intent) != d['intent_signature']:
                    raise EvidenceError('PAPER_CANCEL_MANAGED_IDENTITY_CHANGED')
            elif (scope, scope_id) not in context.scopes: continue
            # A durable earlier EVENT cancel can be delivered after recovery,
            # but cannot target a newly valued post-event intent through it.
            if superseded_event and self.store.get(intent['valuation_id'])['seq'] > source['seq']: continue
            if health_trigger and not cancellation_required(d, context.event_id, tuple(a['strategy'] for a in intent['attribution'])):
                continue
            if passive_or_new_risk_only and not managed_opening(intent): continue
            selected.append((intent_id, intent))
        # Do not claim cancellation of an omitted suffix or prune managed risk.
        if len(selected) > self.policy.maximum_plan_intents:
            raise EvidenceError('PAPER_CANCEL_PLAN_BOUND_REQUIRES_SMALLER_SCOPE')
        now = finite(self.store.clock())
        # A known cancellation request remains actionable during wall-clock
        # regression. Sequence and immutable intent identity preserve causality.
        items = {pid:dict(signature=cancel_identity(i), event_id=i['event_id'], token_id=i['token_id'], stage='PLANNED',
                         account_request_id='paper-cancel:'+digest([self._key(key), pid]),
                         requested_at=None, first_terminal_observed_at=None, terminal_status=None, fault=None, last_delivery_error=None,
                         cumulative_fill_units_at_plan=i['filled_units']) for pid, i in selected}
        state = dict(plan_id=key, trigger_id=trigger_id, trigger_at=source['body']['recorded_at'], planned_at=now,
                     scope=scope, scope_id=scope_id, passive_or_new_risk_only=passive_or_new_risk_only,
                     items=items, omitted_intents=0, superseded_event_request=superseded_event)
        refs = [trigger_id]+([account_head['id']] if account_head else [])
        return self._commit(key, request, None, state, refs=refs,
                    heads=((source['kind'], source['event_id'], trigger_head['seq']),
                           ('COORDINATOR_EVENT', ACCOUNT_KEY, account_head['seq'] if account_head else 0)),
                    outcome='PLANNED' if items else 'NO_MATCHING_MANAGED_INTENTS',
                    terminal_confirmation_count=0, locally_requested_count=0, selected_count=len(items),
                    inventory_unchanged=True, reservation_released=False)

    def _request_record(self, item, intent_id):
        try:
            row = self.store.get(item['account_request_id'])
        except EvidenceError as exc:
            if str(exc) == 'EVIDENCE_MISSING': return None
            raise
        d = row['body'].get('details', {})
        if (row['kind'] != 'COORDINATOR_EVENT' or row['event_id'] != ACCOUNT_KEY
                or d.get('version') != ACCOUNT_VERSION or d.get('policy_sha256') != self.coordinator.policy_sha
                or d.get('request') != dict(action='TRANSITION', intent_id=intent_id, status='CANCEL_REQUESTED',
                                            expected_cancel_identity=item['signature'])):
            raise EvidenceError('PAPER_CANCEL_REQUEST_RECORD_CONFLICT')
        return row

    def advance(self, key, *, plan_id):
        return self._step(key, plan_id=plan_id, deliver=True)

    def observe(self, key, *, plan_id):
        return self._step(key, plan_id=plan_id, deliver=False)

    def _step(self, key, *, plan_id, deliver):
        identity(plan_id, maximum=100)
        request = dict(action='DELIVER_LOCAL_CANCEL_REQUESTS' if deliver else 'OBSERVE_ACCOUNT_RECONCILIATION', plan_id=plan_id)
        prior = self._replay(key, request, plan_id)
        if prior: return prior
        head = self._head(plan_id)
        if head is None: raise EvidenceError('PAPER_CANCEL_PLAN_MISSING')
        state = deepcopy(head['body']['details']['state']); attempts = 0; refs = [plan_id, head['id']]
        account = self.coordinator._state(self.coordinator._head())
        for pid, item in state['items'].items():
            if item['fault'] or item['stage'] == 'TERMINAL': continue
            try:
                proof = self._request_record(item, pid)
                intent = account['intents'].get(pid)
                if intent is None or cancel_identity(intent) != item['signature']:
                    raise EvidenceError('PAPER_CANCEL_MANAGED_IDENTITY_CHANGED')
                if proof is None and intent['status'] in UNRESOLVED and deliver and attempts < self.policy.maximum_requests_per_cycle:
                    attempts += 1
                    # This is the sole effectful account call. It cannot release
                    # a reservation or synthesize a fill/terminal confirmation.
                    proof = self.coordinator.transition(item['account_request_id'], intent_id=pid, status='CANCEL_REQUESTED',
                                                         expected_cancel_identity=item['signature'])
                if proof is not None:
                    item.update(stage='REQUESTED', requested_at=proof['body']['recorded_at'], last_delivery_error=None)
                    if len(refs) < 60: refs.append(proof['id'])
            except EvidenceError as exc:
                if str(exc) in {'PAPER_CANCEL_MANAGED_IDENTITY_CHANGED', 'PAPER_CANCEL_REQUEST_RECORD_CONFLICT'}:
                    item['fault'] = str(exc)
                else:
                    item['last_delivery_error'] = str(exc)
        account_head = self.coordinator._head(); account = self.coordinator._state(account_head)
        now = finite(self.store.clock()); reports = []
        for pid, item in state['items'].items():
            current = account['intents'].get(pid)
            if current is None or cancel_identity(current) != item['signature']:
                item['fault'] = 'PAPER_CANCEL_MANAGED_IDENTITY_CHANGED'
                reports.append(dict(intent_id=pid, status='UNKNOWN_IDENTITY_RECONCILIATION_REQUIRED'))
                continue
            terminal = current['status'] in TERMINAL
            if item['stage'] == 'TERMINAL' and (not terminal or item['terminal_status'] != current['status']):
                item['fault'] = 'PAPER_CANCEL_TERMINAL_RECONCILIATION_REGRESSED'
            if terminal and not item['fault']:
                if item['first_terminal_observed_at'] is None: item['first_terminal_observed_at'] = now
                item.update(stage='TERMINAL', terminal_status=current['status'], last_delivery_error=None)
            latency = None
            if item['requested_at'] is not None and item['first_terminal_observed_at'] is not None:
                delta = item['first_terminal_observed_at']-item['requested_at']
                if delta >= 0: latency = delta
            reports.append(dict(intent_id=pid, stage=item['stage'], current_account_status=current['status'],
                confirmed_paper_cancellation=not item['fault'] and terminal and current['status'] == 'CANCELED',
                terminal_kind=current['status'] if terminal and not item['fault'] else None,
                cumulative_fill_units=current['filled_units'], fault=item['fault'], last_delivery_error=item['last_delivery_error'],
                local_request_at=item['requested_at'], first_terminal_observed_at=item['first_terminal_observed_at'],
                local_request_to_terminal_observed_seconds=latency,
                exchange_cancel_latency_seconds=None, actual_order_cancellation_confirmed=False,
                reservation_retained_by_account=not terminal, inventory_sold_by_cancellation=False))
        if account_head: refs.append(account_head['id'])
        complete = all(i['stage'] == 'TERMINAL' and not i['fault'] for i in state['items'].values())
        faults = any(i['fault'] or i['last_delivery_error'] for i in state['items'].values())
        return self._commit(key, request, head, state, refs=refs,
                    heads=(('COORDINATOR_EVENT', ACCOUNT_KEY, account_head['seq'] if account_head else 0),),
                    outcome='RECONCILIATION_REQUIRED' if faults else 'ALL_TARGETS_TERMINAL' if complete else 'PENDING_TERMINAL_RECONCILIATION',
                    local_requests_attempted_this_cycle=attempts,
                    locally_requested_count=sum(i['requested_at'] is not None for i in state['items'].values()),
                    terminal_confirmation_count=sum(i['stage'] == 'TERMINAL' and not i['fault'] for i in state['items'].values()),
                    reports=reports, current_account_faults=list(account['faults']),
                    risk=self.coordinator._risk(account),
                    telemetry_class='SYNTHETIC_PAPER_ACCOUNT_OBSERVATION_NOT_EXCHANGE_LATENCY',
                    cancelled_inventory_is_not_an_exit=True)
