"""Causal official-release reaction pins for shared nonfinancial economics.

Scheduled notices cannot replace a received exact-source report. A directional
EVENT exception is data eligibility only and retains the stronger event guard,
all operator reductions, common economics, reservations and scenario limits.
"""
from dataclasses import asdict

from .certification import CapabilityScope
from .event_risk import EventContext, EventRiskEngine
from .evidence import EvidenceError, EvidenceStore, ReleaseBinding, canonical, digest, finite, identity
from .model_registry import ActiveModelRegistry
from .pws_lead import _lineage, _report
from .rules import RuleFingerprint
from .strategy_admission import StrategyAdmission, VERSION as ADMISSION_VERSION
from .valuation import contract_target


VERSION = 'alpha_v11_received_source_release_pin_v1'
STRATEGIES = {'SOURCE_SHOCK', 'RELEASE_OPPORTUNITY'}


def received_report_pair(store, *, rule, previous_official_id, current_official_id):
    """Exact bounded immediate receipt pair, shared with read-only replay."""
    before, after = store.get(previous_official_id), store.get(current_official_id)
    prior_value, value = _report(before, rule), _report(after, rule)
    b, n = before['body'], after['body']
    if (before['seq'] >= after['seq'] or b['available_at'] > n['available_at']
            or (b['provider'], b['source_identity']) != (n['provider'], n['source_identity'])):
        raise EvidenceError('RELEASE_PREVIOUS_EXACT_SOURCE_IDENTITY_REQUIRED')
    subsequent = store.records(kind='OFFICIAL_OBSERVATION', event_id=rule.payload['event_id'],
                                    after_seq=before['seq'], limit=1000)
    same_source = [r for r in subsequent if (r['body']['provider'], r['body']['source_identity']) ==
                   (n['provider'], n['source_identity'])]
    if len(subsequent) == 1000 or not same_source or same_source[0]['id'] != after['id']:
        raise EvidenceError('RELEASE_PREDECESSOR_NOT_IMMEDIATE_OR_SCAN_BOUND')
    return before, after, prior_value, value


def received_change_type(before, after):
    b, n = before['body'], after['body']
    if n['observed_at'] < b['observed_at']:
        raise EvidenceError('LATE_OLDER_REPORT_REQUIRES_DISTINCT_REVISION_MODEL')
    kind = 'NEW_OFFICIAL_OBSERVATION' if n['observed_at'] > b['observed_at'] else 'OFFICIAL_REVISION'
    if kind == 'OFFICIAL_REVISION' and digest(n['payload']) == digest(b['payload']):
        raise EvidenceError('DUPLICATE_RECEIPT_IS_NOT_SOURCE_REVISION')
    return kind


class SourceRelease:
    def __init__(self, store: EvidenceStore):
        self.store = store

    def _assess(self, *, admission_id, event_state_id, previous_official_id, current_official_id, book_id,
                schedule_id=None):
        row = self.store.get(admission_id); a = row['body'].get('details', {})
        if row['kind'] != 'REGISTRY' or a.get('version') != ADMISSION_VERSION:
            raise EvidenceError('RELEASE_STRATEGY_ADMISSION_REQUIRED')
        request = a['request']; scope = CapabilityScope(**request['scope'])
        if scope.strategy not in STRATEGIES:
            raise EvidenceError('RELEASE_STRATEGY_SCOPE_REQUIRED')
        rule = RuleFingerprint(**request['rule']); binding = ReleaseBinding(**request['binding'])
        context = EventContext(**request['context']); now = finite(self.store.clock())
        admission = StrategyAdmission(self.store).revalidate(admission_id, context=context, rule=rule,
                       binding=asdict(binding), strategies=(scope.strategy,))
        heads = list(admission['heads'])
        book_head = self.store.latest(kind='BOOK', event_id=context.event_id)
        heads.append(('BOOK', context.event_id, book_head['seq'] if book_head else 0))
        before, after, prior_value, value = received_report_pair(self.store, rule=rule,
            previous_official_id=previous_official_id, current_official_id=current_official_id)
        b, n = before['body'], after['body']
        latest = self.store.latest(kind='OFFICIAL_OBSERVATION', event_id=context.event_id)
        leases = {s['evidence_id']:s for s in request['source_leases']}
        if (not latest or latest['id'] != current_official_id or current_official_id not in leases
                or leases[current_official_id]['role'] != 'OFFICIAL'):
            raise EvidenceError('RELEASE_CURRENT_OFFICIAL_LEASE_REQUIRED')
        kind = received_change_type(before, after)
        event = EventRiskEngine(self.store).revalidate(event_state_id)
        event_row = self.store.get(event_state_id); er = event['request']; metrics = er['metrics']; policy = er['policy']
        if (er['context'] != asdict(context) or er['binding'] != asdict(binding)
                or current_official_id not in er['source_ids'] or book_id not in er['book_ids']):
            raise EvidenceError('RELEASE_EVENT_SOURCE_AND_BOOK_BINDING')
        heads.append(('COORDINATOR_EVENT', event_row['event_id'], event_row['seq']))
        if (kind == 'OFFICIAL_REVISION' and metrics['source_revision'] is not True
                or kind == 'NEW_OFFICIAL_OBSERVATION' and not (metrics['routine_observation'] or metrics['special_observation'])):
            raise EvidenceError('RELEASE_EVENT_METRIC_NOT_BOUND_TO_RECEIVED_CHANGE')
        if any(event['safety']['flags'][k] for k in ('no_new_orders', 'reduce_only', 'manual_review', 'quarantined')):
            raise EvidenceError('RELEASE_OPERATOR_SUPPRESSES_NEW_RISK')
        if any(metrics[k] is not True for k in ('sources_healthy', 'websocket_synchronized', 'clock_healthy')):
            raise EvidenceError('RELEASE_HEALTH_CANNOT_BYPASS_EVENT')
        for field in ('model_disagreement', 'model_age', 'price_velocity', 'spread', 'depth_loss',
                      'cross_bucket_motion', 'loss_utilization'):
            suffix = '_seconds' if field == 'model_age' else ''
            measured = metrics[field+suffix]
            if measured is None or measured >= policy[field+'_event'+suffix]:
                raise EvidenceError('RELEASE_HAZARD_CANNOT_BYPASS_EVENT')
        if (metrics['adverse_fills'] is None or metrics['recent_markout_per_share'] is None
                or metrics['adverse_fills'] >= policy['adverse_fills_event']
                or metrics['recent_markout_per_share'] <= -policy['negative_markout_event']
                or metrics['time_to_settlement_seconds'] is None or metrics['time_to_settlement_seconds'] <= 0):
            raise EvidenceError('RELEASE_EXECUTION_OR_SETTLEMENT_HEALTH_REQUIRED')
        book = self.store.get(book_id); book_body = book['body']; p = book_body.get('payload', {})
        target = contract_target(rule, p.get('market_id'), p.get('side'))
        if (book['kind'] != 'BOOK' or book['event_id'] != context.event_id
                or any(p.get(k) != v for k, v in target.items()) or p.get('rule_fingerprint') != rule.sha256
                or p.get('stream_healthy') is not True or book_body['evidence_class'] == 'HISTORICAL_AVAILABILITY_UNKNOWN'
                or book_body['observed_at'] is None or book['seq'] <= after['seq']
                or not n['available_at'] <= book_body['observed_at'] <= book_body['available_at'] <= now):
            raise EvidenceError('RELEASE_REQUIRES_EXACT_BOOK_OBSERVED_AFTER_RECEIPT')
        latest_book = self.store.latest_source(kind='BOOK', event_id=context.event_id,
                         provider=book_body['provider'], source_identity=book_body['source_identity'])
        if not latest_book or latest_book['id'] != book_id:
            raise EvidenceError('RELEASE_CURRENT_EXACT_BOOK_REQUIRED')
        if (event_row['seq'] <= book['seq'] or metrics['measured_at'] < book_body['available_at']):
            raise EvidenceError('RELEASE_EVENT_MEASUREMENT_PRECEDES_REVALIDATION')
        model_ids = tuple(k for k, lease in leases.items() if lease['role'] == 'MODEL')
        graph = _lineage(self.store, model_ids, event_id=context.event_id, cutoff=now)
        if graph['records'].get(current_official_id) != after['sha256']:
            raise EvidenceError('RELEASE_PAYOUT_MODEL_MUST_INCLUDE_RECEIVED_OFFICIAL_EVIDENCE')
        if any(key not in leases or leases[key]['role'] != 'PWS' for key in graph['pws_ids']):
            raise EvidenceError('RELEASE_AUXILIARY_PWS_REQUIRES_FRESH_QC_LEASE')
        for key in model_ids:
            model_row = self.store.get(key)
            # Every model component must incorporate this receipt; one adapted
            # component cannot hide a stale sibling. Raw leaves may predate it.
            own = _lineage(self.store, (key,), event_id=context.event_id, cutoff=now)
            if model_row['seq'] <= after['seq'] or current_official_id not in own['records']:
                raise EvidenceError('RELEASE_EACH_PAYOUT_COMPONENT_REQUIRES_RECOMPUTATION')
        mode = 'V11_PAPER' if request['stage'] == 'PAPER' else 'V11_SHADOW'
        model = ActiveModelRegistry().pin(scope_key=scope.key, mode=mode)
        if (model.state_sha256 != admission['model_state_sha256'] or model.bundle.payload['bundle']['target'] != 'FINAL_CONTRACT_PAYOUT'):
            raise EvidenceError('RELEASE_APPROVED_PAYOUT_MODEL_REQUIRED')
        schedule = None
        if schedule_id is not None:
            s = self.store.get(schedule_id); v = s['body'].get('details', {})
            if (s['kind'] != 'SOURCE_SCHEDULE' or s['event_id'] != context.event_id or s['seq'] >= after['seq']
                    or s['body']['recorded_at'] > n['available_at']
                    or v.get('version') != 'alpha_v11_release_schedule_v1' or v.get('station') != context.station_id):
                raise EvidenceError('RELEASE_SCHEDULE_PROVENANCE_REQUIRED')
            schedule = dict(id=schedule_id, sha256=s['sha256'], expected_release_at=finite(v['expected_release_at']),
                            actual_receipt_at=n['available_at'], proves_actual_release=False)
        recheck = min(policy['max_book_age_seconds'], event['guard']['revalidate_seconds'])
        expiry = min(admission['valid_until'], event['valid_until'], book_body['observed_at']+recheck,
                     book_body['available_at']+recheck, n['observed_at']+policy['max_source_age_seconds'])
        if now >= expiry:
            raise EvidenceError('RELEASE_FRESHNESS_EXPIRED')
        # A final coordinator valuation still verifies both book sides, depth,
        # fees and stronger state-adjusted EV. This pin never sets a price edge.
        return dict(strategy=scope.strategy, context=asdict(context), rule=asdict(rule), binding=asdict(binding),
                    admission_id=admission_id, event_state_id=event_state_id, book_id=book_id, book_sha256=book['sha256'],
                    current_official_id=current_official_id, current_official_sha256=after['sha256'],
                    previous_official_id=previous_official_id, change_type=kind,
                    reported_temperature_change=value-prior_value, temperature_unit=rule.payload['unit'],
                    received_at=n['available_at'], provider_published_at=n['published_at'], schedule=schedule,
                    model_lineage=graph, model_state_sha256=model.state_sha256, heads=heads,
                    model_input_sha256=sorted(self.store.get(k)['sha256'] for k in model_ids),
                    event_state=event['state'], stronger_guard=event['guard'], valid_until=expiry,
                    directional_event_data_eligible=event['state'] == 'EVENT',
                    passive_new_risk_permitted=False, settlement_finality=False, financial_authority=False)

    def pin(self, record_id, *, admission_id, event_state_id, previous_official_id, current_official_id,
            book_id, schedule_id=None):
        request = dict(admission_id=admission_id, event_state_id=event_state_id, previous_official_id=previous_official_id,
                       current_official_id=current_official_id, book_id=book_id, schedule_id=schedule_id)
        for value in request.values():
            if value is not None:
                identity(value)
        try:
            old = self.store.get(record_id)
        except EvidenceError as exc:
            if str(exc) != 'EVIDENCE_MISSING':
                raise
        else:
            if (old['kind'] != 'REGISTRY' or old['body']['details'].get('version') != VERSION
                    or old['body']['details']['request'] != request):
                raise EvidenceError('RELEASE_PIN_ID_COLLISION')
            return old
        result = self._assess(**request)
        return self.store.audit(record_id, event_id='source-release:'+digest(request), kind='REGISTRY',
                details=dict(version=VERSION, request=request, assessment=result),
                evidence_ids=tuple(v for v in request.values() if v is not None),
                expected_heads=tuple(tuple(h) for h in result['heads']))

    def revalidate(self, record_id, *, context, rule, binding, admission_ids, event_state_id, book_id, strategies):
        row = self.store.get(record_id); d = row['body'].get('details', {})
        if row['kind'] != 'REGISTRY' or d.get('version') != VERSION:
            raise EvidenceError('RECEIVED_SOURCE_RELEASE_PIN_REQUIRED')
        before = d['assessment']
        if (before['context'] != asdict(context) or before['rule'] != asdict(rule) or before['binding'] != binding
                or before['admission_id'] not in admission_ids or set(strategies) != {before['strategy']}
                or before['event_state_id'] != event_state_id or before['book_id'] != book_id):
            raise EvidenceError('RELEASE_PROPOSAL_IDENTITY_MISMATCH')
        current = self._assess(**d['request'])
        if canonical(current) != canonical(before):
            raise EvidenceError('RELEASE_SOURCE_OR_AUTHORITY_CHANGED_RECOMPUTE')
        return current
