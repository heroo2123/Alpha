"""Bounded synchronous PAPER ticks joining queue, health, strategies and safety.

No daemon installation, V10 access, network order route, sleeps or inferred fills.
Adapters must archive real receipts; a periodic census cannot re-date old data.
"""
from copy import deepcopy
from contextlib import ExitStack
from dataclasses import asdict, dataclass
import fcntl
import os
import time
import uuid

from .evidence import EvidenceError, canonical, digest, finite, identity
from .event_risk import SafetyReductions
from .paper_cancellation import PaperCancellation, CancellationPolicy, ADMISSION_VERSION, managed_opening
from .paper_coordinator import UNRESOLVED
from .runtime_health import KEY as HEALTH_KEY, admission_heads, host_stamp
from .strategy_pipeline import TemperatureStrategies, EntryRequest
from .runtime_feed import EvidenceFeed, FeedPolicy
from .audit_reports import AuditScheduler, AuditPolicy


VERSION = 'alpha_v11_paper_runtime_v1'
KEY = 'v11-paper-runtime'


@dataclass(frozen=True)
class RuntimePolicy:
    version: str
    maximum_updates: int = 32
    maximum_events: int = 4
    maximum_cancel_plans: int = 4
    maximum_tick_seconds: float = 20.
    census_interval_seconds: float = 300.
    census_retry_seconds: float = 30.

    def __post_init__(self):
        identity(self.version)
        for val, bound in ((self.maximum_updates,64),(self.maximum_events,16),(self.maximum_cancel_plans,8)):
            if type(val) is not int or not 1 <= val <= bound: raise EvidenceError('RUNTIME_POLICY_BOUND')
        if not 0 < finite(self.maximum_tick_seconds) <= 60 or not 1 <= finite(self.census_interval_seconds) <= 3600 or not 1 <= finite(self.census_retry_seconds) <= 300:
            raise EvidenceError('RUNTIME_POLICY_BOUND')


@dataclass(frozen=True)
class Evaluation:
    result_ids: tuple[str, ...]
    proposals: tuple = ()


def request_adapter_config(name, requests_for_event):
    config = getattr(requests_for_event,'config',None)
    if config is None: return None  # Historical callback fixtures are not attested plans.
    from .evidence import sha
    sha(config)
    return digest(dict(adapter=name,requests=config))


def check_request_adapter(adapter):
    if request_adapter_config(type(adapter).__name__,adapter.requests_for_event) != adapter.config:
        raise EvidenceError('RUNTIME_REQUEST_PLAN_CHANGED_REVIEW_REQUIRED')


class TemperatureEventAdapter:
    """Use existing reviewed request assembly and the real temperature pipeline."""
    def __init__(self, store, requests_for_event):
        self.store, self.requests_for_event = store, requests_for_event
        self.config = request_adapter_config(type(self).__name__,requests_for_event)

    def evaluate(self, claim, prefix):
        check_request_adapter(self)
        requests = self.requests_for_event(claim)
        if type(requests) is not tuple or not 1 <= len(requests) <= 6 or any(not isinstance(r, EntryRequest) for r in requests):
            raise EvidenceError('RUNTIME_TEMPERATURE_REQUEST_BOUND')
        engine = TemperatureStrategies(self.store); outputs = []; proposals = []
        for index, request in enumerate(requests):
            key = 'eval:'+digest([prefix, index])
            pin = self.store.get(request.admission_id)['body']['details']['request']
            context, strategy = pin['context'], pin['scope']['strategy']
            if context['event_id'] != claim['event_id']: raise EvidenceError('RUNTIME_EVALUATION_EVENT_MISMATCH')
            try:
                admission_heads(self.store, account_id=context['account_id'], event_id=context['event_id'], strategies=(strategy,))
                row = engine.evaluate(key, request)
            except EvidenceError as exc:
                row = self.store.audit(key, event_id=claim['event_id'], kind='RUNTIME_STATUS', details=dict(
                    version=VERSION, outcome='GATED', strategy=strategy, reason=str(exc), financial_authority=False))
            if row['event_id'] != claim['event_id']: raise EvidenceError('RUNTIME_EVALUATION_EVENT_MISMATCH')
            outputs.append(key)
            if row['body']['details'].get('proposal') is not None: proposals.append(engine.proposal(key))
        return Evaluation(tuple(outputs), tuple(proposals))


class PaperRuntime:
    def __init__(self, coordinator, queue, health, policy, *, evaluator, census=None, maker=None, worker_id=None, generation=None, feed_policy=None, rewards=None, audits=None, reconciliation=None):
        if (not isinstance(policy, RuntimePolicy) or coordinator.store is not queue.store or coordinator.store is not health.store
                or health.account_id != coordinator.policy.account_id or set(queue.routes) != set(health.scopes)):
            raise EvidenceError('RUNTIME_COMPONENT_SCOPE_MISMATCH')
        self.coordinator, self.queue, self.health = coordinator, queue, health
        self.store, self.policy, self.evaluator, self.census, self.maker = coordinator.store, policy, evaluator, census, maker
        if getattr(evaluator,'coordinator',None) is not None and evaluator.coordinator is not coordinator:
            raise EvidenceError('RUNTIME_EVALUATOR_ACCOUNT_MISMATCH')
        if worker_id is not None and (worker_id not in health.policy.workers or generation is None):
            raise EvidenceError('RUNTIME_WORKER_IDENTITY_REQUIRED')
        self.worker_id = worker_id; self.generation = identity(generation) if generation is not None else uuid.uuid4().hex
        if maker is not None and maker.coordinator is not coordinator: raise EvidenceError('RUNTIME_MAKER_ACCOUNT_MISMATCH')
        if rewards is not None and (maker is None or rewards.research is not maker):
            raise EvidenceError('RUNTIME_REWARD_RESEARCH_MISMATCH')
        self.rewards = rewards
        self.reconciliation = reconciliation
        if reconciliation is not None and reconciliation.coordinator is not coordinator:
            raise EvidenceError('RUNTIME_RECONCILIATION_ACCOUNT_MISMATCH')
        if reconciliation is not None and reconciliation.queue is not None and reconciliation.queue is not queue:
            raise EvidenceError('RUNTIME_RECONCILIATION_QUEUE_MISMATCH')
        self.audits = audits or AuditScheduler(self.store,AuditPolicy('bounded-audit-v1'))
        if self.audits.store is not self.store: raise EvidenceError('RUNTIME_AUDIT_NAMESPACE_MISMATCH')
        self.cancellation = PaperCancellation(coordinator, CancellationPolicy('runtime-bounded-v1', 16, 256))
        self.feed = EvidenceFeed(queue, feed_policy or FeedPolicy('bounded-receipt-delivery-v1'))
        config = dict(runtime=asdict(policy), account=coordinator.policy_sha, queue=queue.config, health=health.config, feed=self.feed.config,
                      rewards=rewards.config if rewards is not None else None, audits=self.audits.config,
                      resting_admission_policy=ADMISSION_VERSION)
        if getattr(evaluator,'config',None) is not None: config['evaluator'] = evaluator.config
        if maker is not None:config['maker_research']=maker.policy_sha
        if reconciliation is not None:config['paper_reconciliation']=reconciliation.config
        self.config = digest(config)

    def _head(self):
        row = self.store.latest(kind='RUNTIME_STATUS', event_id=KEY)
        if row and row['body']['details'].get('config_sha256') != self.config:
            raise EvidenceError('RUNTIME_CONFIGURATION_CHANGED_REVIEW_REQUIRED')
        return row

    def _save(self, key, state, **details):
        head = self._head()
        return self.store.safety_audit(key, event_id=KEY, kind='RUNTIME_STATUS', details=dict(
            version=VERSION, config_sha256=self.config, state=state, financial_authority=False,
            real_orders_sent=False, deployment_acceptance=False, **details), expected_previous_seq=head['seq'] if head else 0)

    def tick(self, tick_id, *, updates=()):
        identity(tick_id, maximum=80)
        if type(updates) is not tuple or len(updates) > self.policy.maximum_updates:
            raise EvidenceError('RUNTIME_UPDATE_BOUND_BACKPRESSURE')
        if any(type(u) is not tuple or len(u) != 2 for u in updates): raise EvidenceError('RUNTIME_UPDATE_SCHEMA')
        request = dict(tick_id=tick_id, updates=updates)
        final_id = 'runtime:'+digest(tick_id)
        try:
            done = self.store.get(final_id)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING': raise
        else:
            if done['body']['details'].get('config_sha256') != self.config or canonical(done['body']['details'].get('request')) != canonical(request):
                raise EvidenceError('RUNTIME_REPLAY_CONFIG_CONFLICT')
            return done  # A historical result never renews a heartbeat or admission.
        fd = os.open(self.store.path.with_name(self.store.path.name+'.runtime.lock'), os.O_CREAT|os.O_WRONLY|os.O_NOFOLLOW, 0o600)
        try:
            try: fcntl.flock(fd, fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError: raise EvidenceError('PAPER_RUNTIME_ALREADY_RUNNING') from None
            try:
                return self._tick(request, final_id)
            except (EvidenceError, OSError, TimeoutError) as exc:
                head = self._head()
                if head is None: raise
                state = head['body']['details']['state']
                reason = str(exc) if isinstance(exc, EvidenceError) else type(exc).__name__
                return self._save(final_id, state, request=request, outcome='INTERRUPTED_REQUIRES_RECONCILIATION',
                    reason=reason, updates_consumed=False, forward_or_live_acceptance=False)
        finally:
            os.close(fd)

    def _tick(self, request, final_id):
        started = time.monotonic(); deadline = started+self.policy.maximum_tick_seconds
        head = self._head(); state = deepcopy(head['body']['details']['state']) if head else dict(
            attempt=0, boot_id=None, next_census_monotonic=0., visited=[], census_retry={},
            operator_cursor=0, event_cursor=0, active_plans={}, last_cancel_plan='')
        state.setdefault('rule_cursor', 0)
        state.setdefault('cancel_intake_next', 0)
        state.setdefault('last_maker_quote', '')
        state.setdefault('last_admission_intent', '')
        state['attempt'] += 1
        prefix = 'tick:'+digest([request, state['attempt']])
        step = [0]
        def save(**details):
            step[0] += 1; return self._save(prefix+':s'+str(step[0]), state, **details)
        def budget(): return time.monotonic() < deadline
        stamp = host_stamp(self.store)
        if state['boot_id'] != stamp['boot_id']:
            state.update(boot_id=stamp['boot_id'], next_census_monotonic=0., visited=[], census_retry={})
        save(outcome='IN_PROGRESS', request=request)
        if self.worker_id is not None:
            self.health.heartbeat(prefix+':heartbeat', worker=self.worker_id, generation=self.generation)
        health = self.health.sample(prefix+':health'); hd = health['body']['details']
        if state.get('generation') != self.generation and not hd['clock_reasons']:
            if self.coordinator._head() is not None:
                self.coordinator.recover(prefix+':recover')
            state['generation'] = self.generation
            save(outcome='PAPER_WORKER_RESTART_RECONCILED')
        errors = []; cancellation_reports = []; evaluations = []; coordinated = []; retired = []; reward_reports = []
        admission_checks = []
        receipt_reports = []
        if self.reconciliation is not None and not hd['clock_reasons'] and budget():
            try:
                report = self.reconciliation.step(prefix+':receipts',
                    deadline=min(deadline, time.monotonic()+self.policy.maximum_tick_seconds/4))
                receipt_reports.append(report['id'])
                if report['body']['details']['outcome'] != 'RECONCILED':
                    errors.append(dict(stage='RECONCILIATION', reason='PAPER_RECEIPTS_REQUIRE_RECONCILIATION'))
            except (EvidenceError, OSError, TimeoutError) as exc:
                errors.append(dict(stage='RECONCILIATION', reason=str(exc)))
        # Resume durable plans before consuming new triggers. Requests retain risk.
        plan_ids = sorted(state['active_plans'])
        plan_ids = [p for p in plan_ids if p > state['last_cancel_plan']]+[p for p in plan_ids if p <= state['last_cancel_plan']]
        serviced = set()
        for plan_id in plan_ids[:self.policy.maximum_cancel_plans]:
            if not budget(): break
            info = state['active_plans'][plan_id]
            try:
                # An interrupted registration before PLAN may refer to an old
                # health head. Drop only that never-created local plan; current
                # health is evaluated below. Existing plans always retain risk.
                trigger = self.store.get(info['trigger_id'])
                if (self.cancellation._head(plan_id) is None and trigger['event_id'] == HEALTH_KEY
                        and trigger['id'] != health['id']):
                    state['active_plans'].pop(plan_id)
                    save(outcome='UNDELIVERED_HEALTH_PLAN_SUPERSEDED'); continue
                self.cancellation.plan(plan_id, trigger_id=info['trigger_id'])
                report = self.cancellation.advance('dispatch:'+digest([prefix,plan_id]), plan_id=plan_id)
                cancellation_reports.append(report['id'])
                if report['body']['details']['outcome'] == 'ALL_TARGETS_TERMINAL': state['active_plans'].pop(plan_id)
            except EvidenceError as exc: errors.append(dict(stage='CANCEL', reason=str(exc)))
            state['last_cancel_plan'] = plan_id; serviced.add(plan_id)
            save(outcome='CANCELLATION_PROGRESS')
        # Health has one dedicated check. A healthy no-op cannot consume the
        # entire nonhealth intake budget. Rotate channels to prevent a busy
        # operator stream starving EVENT/rule quarantine at the minimum budget.
        remaining = self.policy.maximum_cancel_plans
        triggers = [health]
        channels = (('OPERATOR_EVENT','operator_cursor'),('COORDINATOR_EVENT','event_cursor'),('RULE_STATE','rule_cursor'))
        first = state['cancel_intake_next']
        for kind, cursor in channels[first:]+channels[:first]:
            rows = self.store.records(kind=kind, after_seq=state[cursor], limit=self.policy.maximum_updates)
            for row in rows:
                d = row['body'].get('details', {})
                requested = (d.get('cancellation_status') == 'REQUESTED_NOT_CONFIRMED' or
                             kind == 'RULE_STATE' and d.get('cancel_managed_new_risk_requested') is True and d.get('quarantined') is True)
                if requested: triggers.append(row)
                else: state[cursor] = row['seq']
                if requested: break
        for index, trigger in enumerate(triggers):
            if (not budget() or remaining <= 0
                    or sum('admission_intent_id' not in p for p in state['active_plans'].values()) >= 32): break
            plan_id = 'runtime-plan:'+digest([trigger['id'], trigger['sha256']])
            if plan_id in serviced: continue
            if trigger['kind'] != 'RUNTIME_STATUS': remaining -= 1
            if plan_id not in state['active_plans']:
                state['active_plans'][plan_id] = dict(trigger_id=trigger['id'])
                save(outcome='CANCELLATION_PLAN_REGISTERED')
            try:
                self.cancellation.plan(plan_id, trigger_id=trigger['id'])
                report = self.cancellation.advance(prefix+':new-cancel:'+str(index), plan_id=plan_id)
                cancellation_reports.append(report['id'])
                if report['body']['details']['outcome'] == 'ALL_TARGETS_TERMINAL': state['active_plans'].pop(plan_id)
                if trigger['kind'] != 'RUNTIME_STATUS':
                    cursor = {'OPERATOR_EVENT':'operator_cursor','COORDINATOR_EVENT':'event_cursor','RULE_STATE':'rule_cursor'}[trigger['kind']]
                    state[cursor] = trigger['seq']
            except EvidenceError as exc: errors.append(dict(stage='CANCEL_PLAN', reason=str(exc)))
            if trigger['kind'] != 'RUNTIME_STATUS':
                state['cancel_intake_next'] = (next(i for i,c in enumerate(channels) if c[0] == trigger['kind'])+1)%len(channels)
            save(outcome='CANCELLATION_PROGRESS')
        # A separate bounded sweep cannot be starved by a busy source/operator
        # stream or a healthy prefix. Each admission plan owns one unresolved
        # intent; at most the account's 512 retained intents can be tracked in
        # addition to the existing 32 general safety plans. Pending terminal
        # reconciliation never prevents cancellation of another invalid pin.
        account = self.coordinator._state(self.coordinator._head())
        planned = {p['admission_intent_id'] for p in state['active_plans'].values() if 'admission_intent_id' in p}
        candidates = sorted(pid for pid, intent in account['intents'].items()
            if intent['status'] in UNRESOLVED and not intent.get('cancel_requested')
            and managed_opening(intent) and pid not in planned)
        candidates = [p for p in candidates if p > state['last_admission_intent']]+[p for p in candidates if p <= state['last_admission_intent']]
        remaining = self.policy.maximum_cancel_plans
        for pid in candidates[:self.policy.maximum_updates]:
            if not budget() or remaining <= 0: break
            try:
                check = self.cancellation.check_admission('admission-check:'+digest([prefix,pid]), intent_id=pid)
                admission_checks.append(check['id'])
                if not check['body']['details']['passed']:
                    remaining -= 1
                    plan_id = 'admission-plan:'+digest([check['id'],check['sha256']])
                    # Persist registration first so an interrupted dispatch is
                    # replayed using the original immutable intent identity.
                    state['active_plans'][plan_id] = dict(trigger_id=check['id'], admission_intent_id=pid)
                    save(outcome='ADMISSION_CANCELLATION_REGISTERED')
                    self.cancellation.plan(plan_id, trigger_id=check['id'])
                    report = self.cancellation.advance('dispatch:'+digest([prefix,plan_id]), plan_id=plan_id)
                    cancellation_reports.append(report['id'])
                    if report['body']['details']['outcome'] == 'ALL_TARGETS_TERMINAL': state['active_plans'].pop(plan_id)
            except EvidenceError as exc: errors.append(dict(stage='RESTING_ADMISSION', intent_id=pid, reason=str(exc)))
            state['last_admission_intent'] = pid
            save(outcome='RESTING_ADMISSION_PROGRESS')
        if self.maker is not None and budget():
            quotes = self.maker._state(self.maker._head())
            observing = sorted(key for key,q in quotes.items() if q['status'] == 'OBSERVING')
            observing = [k for k in observing if k > state['last_maker_quote']]+[k for k in observing if k <= state['last_maker_quote']]
            for quote_id in observing[:self.policy.maximum_updates]:
                if not budget(): break
                quote = quotes[quote_id]
                context = quote['request']['context']
                try:
                    rule = self.store.latest(kind='RULE_STATE',event_id=context['event_id'])
                    if rule and rule['body']['details'].get('quarantined') is True:
                        raise EvidenceError('MAKER_RULE_QUARANTINED')
                    admission_heads(self.store, account_id=context['account_id'], event_id=context['event_id'], strategies=('MAKER_RESEARCH',))
                    if finite(self.store.clock()) >= quote['expires_at']: raise EvidenceError('MAKER_QUOTE_EXPIRED')
                    self.maker.revalidate_admission(quote_id)
                except EvidenceError as exc:
                    try:
                        row = self.maker.retire('retire:'+digest([prefix,quote_id]), quote_id=quote_id, reason=str(exc))
                        retired.append(row['id'])
                    except EvidenceError as failure: errors.append(dict(stage='MAKER_RETIRE', reason=str(failure)))
                state['last_maker_quote'] = quote_id
                save(outcome='MAKER_SAFETY_PROGRESS')
        if hd['global_reasons']:
            errors.append(dict(stage='HEALTH', reason='CLOCK_OR_WORKER_GATED'))
        else:
            if budget():
                feed = self.feed.drain('runtime-feed:'+digest(prefix))
                save(outcome='ARCHIVED_SOURCES_DELIVERED', feed_id=feed['id'])
            for index, (kind, source_id) in enumerate(request['updates']):
                if not budget():
                    errors.append(dict(stage='INGEST', reason='TICK_BUDGET_UPDATES_UNCONSUMED', from_index=index)); break
                try: self.queue.publish(prefix+':update:'+str(index), kind=kind, evidence_id=source_id)
                except EvidenceError as exc: errors.append(dict(stage='INGEST', reason=str(exc), index=index))
            if stamp['monotonic'] >= state['next_census_monotonic'] and budget():
                self.queue.schedule_census(prefix+':periodic')
                state['next_census_monotonic'] = stamp['monotonic']+self.policy.census_interval_seconds
            pending = self.queue.snapshot(); available = set(pending['pending'])|set(pending['needs_census'])
            # A separate fresh-census worker can resolve the dependency while
            # this scheduler continues cancellation. Do not delay its resulting
            # evaluation behind the old adapter-failure retry timer.
            state['census_retry'] = {e:t for e,t in state['census_retry'].items() if e in pending['needs_census']}
            if not available-set(state['visited']): state['visited'] = []
            for index in range(self.policy.maximum_events):
                if not budget(): break
                excluded = (set(state['visited'])|set(self.queue.preparing_model_events())
                            |{e for e,t in state['census_retry'].items() if stamp['monotonic'] < t})
                with ExitStack() as event_work:
                    try:
                        claim = event_work.enter_context(self.queue.work(prefix+':work:'+str(index), exclude_events=tuple(sorted(excluded))))
                    except EvidenceError as exc:
                        if str(exc) != 'TRIGGER_WORKER_ALREADY_RUNNING': raise
                        errors.append(dict(stage='EVENT_SCHEDULE', reason='EVENT_WORK_DEFERRED_BUSY_WORKER'))
                        break
                    if claim is None: break
                    event = claim['event_id']; state['visited'].append(event)
                    output = None
                    try:
                        if claim['requires_full_census']:
                            if self.census is None: raise EvidenceError('RUNTIME_FRESH_CENSUS_ADAPTER_REQUIRED')
                            coverage = self.census(claim, prefix+':census:'+str(index))
                            if not isinstance(coverage, dict): raise EvidenceError('RUNTIME_CENSUS_NOT_READY')
                            self.queue.complete_census(prefix+':coverage:'+str(index), claim_id=claim['claim_id'], **coverage)
                            state['census_retry'].pop(event, None)
                            # Newly received census sources require a new health sample.
                            health = self.health.sample(prefix+':post-census-health:'+str(index))
                        output = self.evaluator.evaluate(claim, prefix+':evaluation:'+str(index))
                        if not isinstance(output, Evaluation) or len(output.proposals) > 6:
                            raise EvidenceError('RUNTIME_EVALUATION_BOUND')
                    except (EvidenceError, TimeoutError) as exc:
                        reason = str(exc) if isinstance(exc, EvidenceError) else 'ADAPTER_TIMEOUT'
                        errors.append(dict(stage='EVALUATE', event_id=event, reason=reason))
                        if claim['requires_full_census']: state['census_retry'][event] = stamp['monotonic']+self.policy.census_retry_seconds
                        row = self.store.audit(prefix+':gated:'+str(index), event_id=event, kind='RUNTIME_STATUS',
                            details=dict(version=VERSION, outcome='GATED', reason=reason, financial_authority=False))
                        output = Evaluation((row['id'],))
                    finished = self.queue.finish(prefix+':finished:'+str(index), claim_id=claim['claim_id'], result_ids=output.result_ids)
                    evaluations.append(finished['id'])
                    if finished['body']['details']['result']['outcome'] == 'RESEARCH_EVALUATED' and output.proposals:
                        batch = self.coordinator.coordinate(prefix+':coordinate:'+str(index), output.proposals)
                        coordinated.append(batch['id'])
                save(outcome='EVALUATION_PROGRESS')
        if self.rewards is not None and not hd['global_reasons'] and budget():
            try:
                row = self.rewards.refresh('rewards:'+digest(prefix)); reward_reports.append(row['id'])
            except EvidenceError as exc: errors.append(dict(stage='REWARDS', reason=str(exc)))
        audit_request_ids=[]
        if not hd['clock_reasons'] and budget():
            try:audit_request_ids=list(self.audits.request_due())
            except EvidenceError as exc:errors.append(dict(stage='AUDIT_SCHEDULE',reason=str(exc)))
        return self._save(final_id, state, request=request, outcome='BUDGET_EXHAUSTED' if not budget() else 'DEGRADED' if errors else 'TICK_COMPLETED',
            health_id=health['id'], errors=errors, evaluation_ids=evaluations, account_batch_ids=coordinated,
            updates_consumed=not hd['global_reasons'] and not any(e['stage']=='INGEST' for e in errors),
            cancellation_report_ids=cancellation_reports, retired_quote_ids=retired,
            resting_admission_check_ids=admission_checks,
            reward_report_ids=reward_reports,
            audit_request_ids=audit_request_ids,
            **({'receipt_reconciliation_ids': receipt_reports} if self.reconciliation is not None else {}),
            duration_monotonic_seconds=time.monotonic()-started, budget_exhausted=not budget(),
            queue_metrics=self.queue.snapshot()['metrics'], forward_or_live_acceptance=False)
