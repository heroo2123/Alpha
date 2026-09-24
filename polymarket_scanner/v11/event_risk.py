"""Durable research event states and monotonic operator reductions.

No order API or financial authority. Upstream feature derivation, protected
operator authentication and downstream cancel reconciliation remain separate.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields

from .evidence import EvidenceError, EvidenceStore, ReleaseBinding, digest, finite, identity


VERSION = "alpha_v11_event_risk_v1"
STATES = {"NORMAL", "CAUTION", "EVENT", "RECOVERY"}
ACTIONS = {"CANCEL_ALL_MANAGED_ORDERS", "CANCEL_AND_HALT", "CANCEL_EVENT",
           "QUARANTINE_STATION", "QUARANTINE_CITY", "NO_NEW_ORDERS", "REDUCE_ONLY",
           "DISABLE_INVENTORY_OPERATIONS", "REQUIRE_MANUAL_REVIEW"}


@dataclass(frozen=True)
class EventContext:
    account_id: str
    city_id: str
    station_id: str
    event_id: str

    def __post_init__(self):
        for value in asdict(self).values():
            identity(value)

    @property
    def scopes(self):
        return (('ACCOUNT', self.account_id), ('CITY', self.city_id),
                ('STATION', self.station_id), ('EVENT', self.event_id))


def _key(family: str, scope: object) -> str:
    return family + ':' + digest(scope)


def _existing(store: EvidenceStore, record_id: str, request: dict) -> dict | None:
    try:
        prior = store.get(record_id)
    except EvidenceError as exc:
        if str(exc) == 'EVIDENCE_MISSING':
            return None
        raise
    if prior['body'].get('details', {}).get('request') != request:
        raise EvidenceError('REPLAY_REQUEST_CONFLICT')
    return prior


class SafetyReductions:
    """Append-only reductions in one nonfinancial namespace; no restore method.

    Actor/reason are audit attribution, never proof of operator authentication.
    Production routing must authenticate separately and bind exact account scope.
    """

    def __init__(self, store: EvidenceStore):
        self.store = store

    def apply(self, command_id: str, *, scope: str, scope_id: str, action: str,
              actor: str, reason: str) -> dict:
        if scope not in {'ACCOUNT', 'CITY', 'STATION', 'EVENT'} or action not in ACTIONS:
            raise EvidenceError('SAFETY_ACTION_INVALID')
        required = {'CANCEL_ALL_MANAGED_ORDERS': 'ACCOUNT', 'CANCEL_EVENT': 'EVENT',
                    'QUARANTINE_STATION': 'STATION', 'QUARANTINE_CITY': 'CITY'}
        if action in required and scope != required[action]:
            raise EvidenceError('SAFETY_SCOPE_MISMATCH')
        request = dict(scope=scope, scope_id=identity(scope_id), action=action,
                       actor=identity(actor), reason=identity(reason))
        replay = _existing(self.store, command_id, request)
        if replay:
            return replay
        key = _key('safety', [scope, scope_id])
        previous = self.store.latest(kind='OPERATOR_EVENT', event_id=key)
        flags = dict(no_new_orders=False, reduce_only=False, inventory_disabled=False,
                     manual_review=False, quarantined=False)
        if previous:
            flags.update(previous['body']['details']['flags'])
        if action in {'NO_NEW_ORDERS', 'CANCEL_AND_HALT'}:
            flags['no_new_orders'] = True
        if action == 'REDUCE_ONLY':
            flags['reduce_only'] = True
        if action == 'DISABLE_INVENTORY_OPERATIONS':
            flags['inventory_disabled'] = True
        if action == 'REQUIRE_MANUAL_REVIEW':
            flags['manual_review'] = True
        if action.startswith('QUARANTINE_'):
            flags.update(quarantined=True, manual_review=True)
        cancel = action.startswith('CANCEL_') or action.startswith('QUARANTINE_')
        details = dict(version=VERSION, request=request, flags=flags,
                       cancellation_status='REQUESTED_NOT_CONFIRMED' if cancel else 'NO_NEW_REQUEST',
                       cancellation_request_id=command_id if cancel else None,
                       existing_inventory_unchanged=True, financial_authority=False)
        return self.store.audit(command_id, event_id=key, kind='OPERATOR_EVENT', details=details,
                                expected_previous_seq=previous['seq'] if previous else 0)

    def view(self, context: EventContext) -> dict:
        flags = dict(no_new_orders=False, reduce_only=False, inventory_disabled=False,
                     manual_review=False, quarantined=False)
        heads = []
        for scope, value in context.scopes:
            row = self.store.latest(kind='OPERATOR_EVENT', event_id=_key('safety', [scope, value]))
            if row:
                heads.append({'id': row['id'], 'sha256': row['sha256']})
                for name, enabled in row['body']['details']['flags'].items():
                    flags[name] = flags[name] or enabled
        return dict(flags=flags, heads=heads, financial_authority=False)

    def atomic_heads(self, context: EventContext) -> tuple[tuple[str, str, int], ...]:
        result = []
        for scope, value in context.scopes:
            key = _key('safety', [scope, value])
            row = self.store.latest(kind='OPERATOR_EVENT', event_id=key)
            result.append(('OPERATOR_EVENT', key, row['seq'] if row else 0))
        return tuple(result)


@dataclass(frozen=True)
class StateGuard:
    size_multiplier: float
    additional_ev_per_share: float
    lifetime_multiplier: float
    liquidity_multiplier: float
    revalidate_seconds: float

    def __post_init__(self):
        for val in asdict(self).values():
            finite(val)
        if (not 0 < self.size_multiplier <= 1 or not 0 < self.lifetime_multiplier <= 1
                or self.liquidity_multiplier < 1 or self.revalidate_seconds <= 0):
            raise EvidenceError('STATE_GUARD_INVALID')


@dataclass(frozen=True)
class EventPolicy:
    policy_version: str
    max_book_age_seconds: float
    max_source_age_seconds: float
    max_metrics_age_seconds: float
    model_disagreement_caution: float
    model_disagreement_event: float
    model_age_caution_seconds: float
    model_age_event_seconds: float
    price_velocity_caution: float
    price_velocity_event: float
    spread_caution: float
    spread_event: float
    depth_loss_caution: float
    depth_loss_event: float
    cross_bucket_motion_caution: float
    cross_bucket_motion_event: float
    loss_utilization_caution: float
    loss_utilization_event: float
    adverse_fills_event: int
    negative_markout_event: float
    settlement_caution_seconds: float
    recovery_samples: int
    recovery_min_span_seconds: float
    recovery_min_spacing_seconds: float
    normal: StateGuard
    caution: StateGuard
    event: StateGuard
    recovery: StateGuard

    def __post_init__(self):
        identity(self.policy_version)
        for field in fields(self):
            if field.name not in {'policy_version', *[s.lower() for s in STATES]}:
                if finite(getattr(self, field.name)) <= 0:
                    raise EvidenceError('EVENT_POLICY_NONPOSITIVE')
        for base in ('model_disagreement', 'price_velocity', 'spread', 'depth_loss',
                     'cross_bucket_motion', 'loss_utilization'):
            if getattr(self, base+'_caution') >= getattr(self, base+'_event'):
                raise EvidenceError('EVENT_THRESHOLDS_UNORDERED')
        if self.model_age_caution_seconds >= self.model_age_event_seconds:
            raise EvidenceError('EVENT_THRESHOLDS_UNORDERED')
        if (type(self.recovery_samples) is not int or not 2 <= self.recovery_samples <= 1000
                or type(self.adverse_fills_event) is not int or not 1 <= self.adverse_fills_event <= 1000):
            raise EvidenceError('EVENT_COUNT_BOUND')
        if (self.normal.size_multiplier != 1 or self.normal.additional_ev_per_share != 0
                or self.normal.lifetime_multiplier != 1 or self.normal.liquidity_multiplier != 1):
            raise EvidenceError('NORMAL_GUARD_BASELINE_REQUIRED')
        for guard in (self.caution, self.event, self.recovery):
            if (not isinstance(guard, StateGuard) or guard.size_multiplier >= 1
                    or guard.additional_ev_per_share <= 0 or guard.lifetime_multiplier >= 1
                    or guard.liquidity_multiplier <= 1
                    or guard.revalidate_seconds >= self.normal.revalidate_seconds):
                raise EvidenceError('STRICTER_DEGRADED_GUARDS_REQUIRED')
        if (self.event.size_multiplier > self.caution.size_multiplier
                or self.event.additional_ev_per_share < self.caution.additional_ev_per_share):
            raise EvidenceError('STRONGER_EVENT_GUARD_REQUIRED')


@dataclass(frozen=True)
class EventMetrics:
    measured_at: float
    model_disagreement: float | None
    model_age_seconds: float | None
    price_velocity: float | None
    spread: float | None
    depth_loss: float | None
    cross_bucket_motion: float | None
    loss_utilization: float | None
    time_to_settlement_seconds: float | None
    adverse_fills: int | None
    recent_markout_per_share: float | None
    sources_healthy: bool
    websocket_synchronized: bool
    clock_healthy: bool
    new_model_run: bool = False
    official_forecast_release: bool = False
    routine_observation: bool = False
    special_observation: bool = False
    source_revision: bool = False

    def __post_init__(self):
        for field in fields(self):
            val = getattr(self, field.name)
            if field.name in {'sources_healthy', 'websocket_synchronized', 'clock_healthy',
                              'new_model_run', 'official_forecast_release', 'routine_observation',
                              'special_observation', 'source_revision'}:
                if type(val) is not bool:
                    raise EvidenceError('EVENT_BOOLEAN_REQUIRED')
            elif val is not None:
                finite(val, nonnegative=field.name not in {'recent_markout_per_share',
                                                         'time_to_settlement_seconds'})
        if self.measured_at is None or (self.adverse_fills is not None and
                                       (type(self.adverse_fills) is not int or self.adverse_fills > 1_000_000)):
            raise EvidenceError('EVENT_METRIC_INVALID')
        for field in ('spread', 'depth_loss', 'cross_bucket_motion'):
            val = getattr(self, field)
            if val is not None and val > 1:
                raise EvidenceError('EVENT_FRACTION_INVALID')


class EventRiskEngine:
    def __init__(self, store: EvidenceStore):
        self.store = store

    def step(self, record_id: str, *, context: EventContext, policy: EventPolicy,
             binding: ReleaseBinding, metrics: EventMetrics, book_ids: tuple[str, ...],
             source_ids: tuple[str, ...]) -> dict:
        if not isinstance(binding, ReleaseBinding):
            raise EvidenceError('RELEASE_BINDING_REQUIRED')
        ids = book_ids + source_ids
        if len(ids) > 48 or len(set(ids)) != len(ids):
            raise EvidenceError('EVENT_EVIDENCE_BOUND')
        request = dict(context=asdict(context), policy=asdict(policy), binding=asdict(binding),
                       metrics=asdict(metrics), book_ids=list(book_ids), source_ids=list(source_ids))
        replay = _existing(self.store, record_id, request)
        if replay:
            return replay
        now = finite(self.store.clock())
        if metrics.measured_at > now:
            raise EvidenceError('EVENT_METRICS_IN_FUTURE')
        key = _key('event-risk', context.event_id)
        previous = self.store.latest(kind='COORDINATOR_EVENT', event_id=key)
        old = previous['body']['details'] if previous else None
        if old and old['request']['context'] != asdict(context):
            raise EvidenceError('EVENT_CONTEXT_CHANGED_REVIEW_REQUIRED')
        event_reasons, caution_reasons = [], []
        watermarks = {}
        expires = [metrics.measured_at+policy.max_metrics_age_seconds]
        for group, capture_ids, allowed, max_age in (
            ('book', book_ids, {'BOOK'}, policy.max_book_age_seconds),
            ('source', source_ids, {'OFFICIAL_OBSERVATION', 'MODEL', 'PWS_OBSERVATION'}, policy.max_source_age_seconds),
        ):
            if not capture_ids:
                event_reasons.append(group.upper()+'_EVIDENCE_MISSING')
            for capture_id in capture_ids:
                row = self.store.get(capture_id)
                body = row['body']
                if row['event_id'] != context.event_id or row['kind'] not in allowed:
                    raise EvidenceError('EVENT_EVIDENCE_IDENTITY')
                at = body['issued_at'] if row['kind'] == 'MODEL' else body['observed_at']
                if row['kind'] == 'PWS_OBSERVATION':
                    # QC summaries are features, not a new sensor observation.
                    # Use the oldest contributing sensor; recomputing a summary
                    # cannot advance stale sensors or bootstrap recovery.
                    qc = body['payload']; ages = qc.get('observation_age_seconds')
                    at = None
                    if (body['provider'] == 'ALPHA_PWS_QC' and qc.get('health') == 'HEALTHY'
                            and qc.get('station') == context.station_id
                            and isinstance(ages,list) and 1 <= len(ages) <= 400):
                        try:
                            from .pws_quality import current_neighborhood_heads
                            current_neighborhood_heads(self.store,row)
                            as_of = finite(qc.get('as_of'))
                            oldest = as_of-max(finite(age) for age in ages)
                            if 0 <= oldest <= as_of <= body['available_at'] <= now: at = oldest
                        except EvidenceError:
                            pass  # Unknown/malformed QC time stays an EVENT gate.
                if at is not None:
                    expires.extend((at+max_age, body['received_at']+max_age))
                if (body['available_at'] > now or body['evidence_class'] == 'HISTORICAL_AVAILABILITY_UNKNOWN'
                        or at is None or not 0 <= now-at <= max_age
                        or not 0 <= now-body['received_at'] <= max_age):
                    event_reasons.append(group.upper()+'_STALE_OR_UNKNOWN')
                if group == 'book' and body['payload'].get('stream_healthy') is not True:
                    event_reasons.append('BOOK_STREAM_NOT_HEALTHY')
                source_key = digest([group, body['provider'], body['source_identity']])
                if source_key in watermarks:
                    raise EvidenceError('DUPLICATE_EVENT_SOURCE_IDENTITY')
                watermarks[source_key] = at
        if now-metrics.measured_at > policy.max_metrics_age_seconds:
            event_reasons.append('METRICS_STALE')
        for field in ('sources_healthy', 'websocket_synchronized', 'clock_healthy'):
            if not getattr(metrics, field):
                event_reasons.append(field.upper())
        for field in ('new_model_run', 'official_forecast_release', 'special_observation', 'source_revision'):
            if getattr(metrics, field):
                event_reasons.append(field.upper())
        if metrics.routine_observation:
            caution_reasons.append('ROUTINE_OBSERVATION_REVALIDATION')
        for base in ('model_disagreement', 'price_velocity', 'spread', 'depth_loss',
                     'cross_bucket_motion', 'loss_utilization', 'model_age'):
            field = base+'_seconds' if base == 'model_age' else base
            suffix = '_seconds' if base == 'model_age' else ''
            value = getattr(metrics, field)
            if value is None:
                event_reasons.append(base.upper()+'_UNKNOWN')
            elif value >= getattr(policy, base+'_event'+suffix):
                event_reasons.append(base.upper())
            elif value >= getattr(policy, base+'_caution'+suffix):
                caution_reasons.append(base.upper())
        if metrics.adverse_fills is None or metrics.recent_markout_per_share is None:
            event_reasons.append('EXECUTION_HEALTH_UNKNOWN')
        elif (metrics.adverse_fills >= policy.adverse_fills_event
              or metrics.recent_markout_per_share <= -policy.negative_markout_event):
            event_reasons.append('ADVERSE_EXECUTION')
        if metrics.time_to_settlement_seconds is None or metrics.time_to_settlement_seconds <= 0:
            event_reasons.append('SETTLEMENT_WINDOW_UNKNOWN_OR_CLOSED')
        elif metrics.time_to_settlement_seconds <= policy.settlement_caution_seconds:
            caution_reasons.append('NEAR_SETTLEMENT')
        policy_sha = digest(asdict(policy))
        changed = (old is None or old['policy_sha256'] != policy_sha
                   or old['request']['binding'] != asdict(binding)
                   or old['source_identities'] != sorted(watermarks))
        stable = dict(count=0, started_at=None, last_counted_at=None, watermarks={})
        if event_reasons:
            state = 'EVENT'
        elif caution_reasons:
            state = 'CAUTION'
        elif old and old['state'] == 'NORMAL' and not changed:
            state, stable = 'NORMAL', old['stability']
        else:
            state = 'RECOVERY'
            if old and old['state'] == 'RECOVERY' and not changed:
                stable = dict(old['stability'])
            last = stable['last_counted_at']
            marks = stable['watermarks']
            distinct = (not marks or (marks.keys() == watermarks.keys() and
                        all(watermarks[k] is not None and watermarks[k] > marks[k] for k in marks)))
            if (distinct and (last is None or now-last >= policy.recovery_min_spacing_seconds)):
                stable.update(count=stable['count']+1, started_at=stable['started_at'] if last is not None else now,
                              last_counted_at=now, watermarks=watermarks)
            if stable['count'] >= policy.recovery_samples and now-stable['started_at'] >= policy.recovery_min_span_seconds:
                # A later clock tick alone cannot complete recovery.
                if stable['last_counted_at'] == now:
                    state = 'NORMAL'
        safety = SafetyReductions(self.store).view(context)
        flags = safety['flags']
        suppressed = flags['no_new_orders'] or flags['reduce_only'] or flags['manual_review'] or flags['quarantined']
        guard = asdict(getattr(policy, state.lower()))
        expires.append(now+guard['revalidate_seconds'])
        if metrics.model_age_seconds is not None:
            next_age = policy.model_age_caution_seconds if state == 'NORMAL' else policy.model_age_event_seconds
            if metrics.model_age_seconds < next_age:
                expires.append(now+next_age-metrics.model_age_seconds)
        if metrics.time_to_settlement_seconds is not None:
            remaining = metrics.time_to_settlement_seconds
            expires.append(now+max(0, remaining-policy.settlement_caution_seconds if
                                   remaining > policy.settlement_caution_seconds else remaining))
        details = dict(version=VERSION, request=request, state=state, policy_sha256=policy_sha,
                       source_identities=sorted(watermarks),
                       state_changed=old is None or old['state'] != state, stability=stable,
                       valid_until=min(expires),
                       reasons=sorted(set(event_reasons+caution_reasons)) or ['STABLE_EVIDENCE'],
                       guard=guard, safety=safety, ordinary_new_risk_research_allowed=state != 'EVENT' and not suppressed,
                       passive_new_risk_research_allowed=state != 'EVENT' and not suppressed,
                       source_shock_status='GATED_BY_OPERATOR' if suppressed else
                         'REQUIRES_EXACT_FRESH_SOURCE_CLOB_CERTIFICATION_AND_STRONGER_EV',
                       cancellation_status='REQUESTED_NOT_CONFIRMED' if state == 'EVENT' else 'NO_NEW_REQUEST',
                       cancellation_scope='MANAGED_PASSIVE_AND_NEW_RISK_RESTING_ORDERS' if state == 'EVENT' else None,
                       existing_inventory_unchanged=True, financial_authority=False)
        return self.store.audit(record_id, event_id=key, kind='COORDINATOR_EVENT', details=details,
                                evidence_ids=ids,
                                expected_previous_seq=previous['seq'] if previous else 0)

    def revalidate(self, record_id: str) -> dict:
        """Reject stale state/operator pins before a coordinator consumes them.

        This read does not atomically reserve risk or authorize submission.
        The eventual account coordinator must recheck within its own boundary.
        """
        row = self.store.get(record_id)
        body = row['body']
        if row['kind'] != 'COORDINATOR_EVENT' or body.get('details', {}).get('version') != VERSION:
            raise EvidenceError('EVENT_STATE_REQUIRED')
        latest = self.store.latest(kind='COORDINATOR_EVENT', event_id=row['event_id'])
        details = body['details']
        context = EventContext(**details['request']['context'])
        if latest['id'] != record_id or SafetyReductions(self.store).view(context) != details['safety']:
            raise EvidenceError('EVENT_OR_OPERATOR_STATE_CHANGED')
        now = finite(self.store.clock())
        if not body['recorded_at'] <= now <= details['valid_until']:
            raise EvidenceError('EVENT_REVALIDATION_EXPIRED')
        return details
